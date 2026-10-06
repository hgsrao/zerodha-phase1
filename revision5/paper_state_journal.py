"""Crash-safe journal for the synchronous, offline R5 paper replay.

Recovery reconstructs controllers by replaying the sealed inputs from the beginning.
Every durable prefix checkpoint must match before new checkpoints may be appended.
No Python objects are deserialized and no external broker requests are replayed.
This is deliberately not a live-broker recovery implementation.
"""
from __future__ import annotations

from dataclasses import asdict
import fcntl
import importlib.metadata
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

from revision5.protection_snapshot import build_plant_protection_snapshot
from revision5.state_recovery import StateRecoveryJournal


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def plant_state(engine):
    plant = engine.real_plant_dcs
    return {
        'protection': asdict(build_plant_protection_snapshot(plant)),
        'electrical': plant.electrical_network.snapshot(),
        'trading_date': plant.current_trading_date,
        'bays': {key: {
            'consecutive_stops': bay.consecutive_stops,
            'cooldown_until_bar_exclusive': bay.cooldown_until_bar_exclusive,
            'loss_cooldown_bars': bay.loss_cooldown_bars,
            'target_cooldown_bars': bay.target_cooldown_bars,
            'governor': bay.governor.snapshot(),
        } for key, bay in plant.bays.items()},
    }


def checkpoint_state(engine):
    # Broker UUIDs and wall-clock filled_at fields are transport metadata, not replay state.
    fills = [{k: v for k, v in fill.items() if k not in {'order_id', 'filled_at'}}
             for fill in engine.broker.fills]
    return {
        'plant': plant_state(engine), 'active_fills': engine.open_trades,
        'positions': engine.broker.positions, 'fills': fills,
        'completed_trades': engine.completed_trades,
        'realized_pnl': engine.broker.realized_pnl,
        'booked_costs': engine.broker.booked_costs,
        'close_receipts': engine._close_feedback_receipts,
        'merit_history': engine.plant_control.dispatch_controller.merit_source.trade_history_r,
        'merit_weights': engine.plant_control.dispatch_controller.merit_source.weights,
        'mtm_peak_equity': engine._mtm_peak,
        'ecs_demand_pu': engine.plant_control.ecs._previous_demand,
        **({'fleet_loading': engine.plant_control.export_fleet_loading_state()}
           if engine.plant_control.fleet_loading is not None else {}),
        'native_cursor': [engine._native_bar_index, engine._native_timestamp],
        'symbol_cooldowns': engine.symbol_cooldown_until_bar,
        'symbol_trips': engine.symbol_tripped,
        'execution_halted': engine._execution_halted,
    }


class PaperStateJournal:
    """Single-writer SQLite journal, committed atomically at portfolio-tick boundaries.

    ``commands`` is a sealed sequence of explicit test/operator breaker commands:
    {tick: zero-based integer, action: 'trip'|'reset', bay_id: ..., reason: ...}.
    Direct, unrecorded plant mutations are unsupported during durable replay.
    ``after_commit`` is a harness hook for killing a worker after a real commit.
    """
    def __init__(self, path, *, commands=(), after_commit=None, require_existing=False, feedback_compaction=False, feedback_account_id="OFFLINE_PAPER"):
        if type(feedback_compaction) is not bool or not isinstance(feedback_account_id, str) or not feedback_account_id:
            raise ValueError('Explicit paper feedback compaction/account identity required')
        self.feedback_compaction=feedback_compaction
        self.feedback_account_id=feedback_account_id
        self.feedback_epoch=None
        self._feedback_contexts={}
        self._identity_payload=None
        self._bound_engine=None
        self.path = Path(path)
        if require_existing and not self.path.is_file():
            raise RuntimeError('Verified paper resume requires an existing journal')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lease = self.path.with_suffix(self.path.suffix + '.lock').open('a')
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lease.close()
            raise RuntimeError('Paper journal already has an active writer') from None
        self.con = sqlite3.connect(self.path, isolation_level=None)
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute('PRAGMA synchronous=FULL')
        self.recovery = StateRecoveryJournal(self.con)
        self.con.execute('CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        self.con.execute('''CREATE TABLE IF NOT EXISTS checkpoints (
            seq INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, payload TEXT NOT NULL,
            previous_hash TEXT NOT NULL, hash TEXT NOT NULL)''')
        self.commands = list(commands)
        for command in self.commands:
            if command['action'] not in {'trip', 'reset'} or type(command['tick']) is not int or command['tick'] < 0:
                raise ValueError('Invalid durable plant command')
        self.after_commit = after_commit
        self.cursor = 0
        self.previous_hash = ''
        self.bound = False
        self.reconciled_checkpoints = 0
        self.prefix_length = self.con.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0]
        self.prefix_verified = self.prefix_length == 0
        self.resume_boundary = None
        if require_existing:
            last = self.con.execute('SELECT timestamp FROM checkpoints ORDER BY seq DESC LIMIT 1').fetchone()
            if self.prefix_length == 0 or last[0] == 'FINAL':
                self.close()
                raise RuntimeError('Verified paper resume requires an interrupted prefix, not a completed run')

    def bind(self, engine, frames, warmup):
        if self.bound:
            raise RuntimeError('Journal/engine can run only once; restart with fresh objects')
        engine._assert_paper_plant_broker()
        if engine.broker.fills or engine.open_trades or engine.completed_trades or (self.feedback_compaction and engine._close_feedback_receipts):
            raise RuntimeError('Recovery must start with a fresh offline engine')
        root = Path(__file__).resolve().parents[1]
        # Include tracked source plus new source files in engine packages during development.
        paths = set(subprocess.check_output(['git', 'ls-files', '*.py'], cwd=root, text=True).splitlines())
        for package in ('revision5', 'revision2_external'):
            paths.update(str(p.relative_to(root)) for p in (root/package).rglob('*.py'))
        source = {p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in sorted(paths) if (root/p).is_file()}
        def frame_hash(frame):
            return hashlib.sha256(frame.to_json(date_format='iso', double_precision=15).encode()).hexdigest()
        provider = engine.plant_control.synchronizer.provider
        identity = canonical({
            'schema': 1, 'python': sys.version, 'source': source,
            'feedback_consumption': None if not self.feedback_compaction else {
                'schema': 1, 'mode': 'FRESH_RECONSTRUCTION_EPOCH', 'compaction': 'AFTER_VERIFIED_CHECKPOINT',
                'paper_account': self.feedback_account_id},
            'packages': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()),
            'modes': [engine.closed_loop_mode, engine.pid_mode, engine.risk_profile,
                      engine.dynamic_target_setpoint_mode, engine.governor_authority,
                      engine.plant_control.mode.value],
            **({'fleet_loading_policy': engine.plant_control.fleet_loading.policy.to_dict()}
               if engine.plant_control.fleet_loading is not None else {}),
            'combined_cycle': None if engine.combined_cycle_runtime is None else {
                'runtime': asdict(engine.combined_cycle_runtime.config),
                'handoff': asdict(engine.combined_cycle_runtime.handoff.config),
                'engine_b': asdict(engine.combined_cycle_runtime.engine_b.policy)},
            'sectors': engine.sector_map, 'sessions': engine.session_schedule,
            'config': engine.config.config_hash, 'safety': engine.safety_contract.contract_hash,
            'warmup': warmup, 'symbols': engine.symbols, 'equity': engine.starting_equity,
            'slippage': engine.broker.slippage_fraction,
            'frames': {s: frame_hash(f) for s, f in frames.items()},
            'grid': [frame_hash(provider.nifty), frame_hash(provider.vix),
                     provider.max_staleness_seconds, provider.minimum_aligned_bars],
            'initial_plant': plant_state(engine), 'commands': self.commands,
        })
        self.con.execute('BEGIN IMMEDIATE')
        try:
            row = self.con.execute('SELECT payload FROM identity WHERE id=1').fetchone()
            if row is None:
                self.con.execute('INSERT INTO identity VALUES (1, ?)', (identity,))
            elif row[0] != identity:
                raise RuntimeError('Recovery identity mismatch: code, configuration, inputs or initial plant changed')
            if self.feedback_compaction:
                self.feedback_epoch=uuid.uuid4().hex
                self.recovery.begin_feedback_epoch(self.feedback_epoch, hashlib.sha256(identity.encode()).hexdigest())
            self.con.execute('COMMIT')
        except BaseException:
            self.con.execute('ROLLBACK')
            self.feedback_epoch=None
            raise
        self._identity_payload=identity
        self._bound_engine=engine
        self.bound = True

    def before_tick(self, engine, timestamp):
        for command in self.commands:
            if command['tick'] != self.cursor:
                continue
            if command['action'] == 'trip':
                engine.real_plant_dcs.trip_unit_breaker(bay_id=command['bay_id'], lockout=True,
                    reason=command.get('reason', 'DURABLE_OPERATOR_TRIP'), source='PAPER_COMMAND_JOURNAL')
            else:
                engine.real_plant_dcs.reset_unit_breaker(bay_id=command['bay_id'])
            engine._record_controller_event('NATIVE_PLANT_COMMAND', timestamp, 'PLANT', command)

    def _feedback_binding(self, engine):
        if (not self.bound or not self.feedback_compaction or not self.feedback_epoch
            or self._bound_engine is not engine or getattr(engine, 'paper_journal', None) is not self):
            raise RuntimeError('Feedback consumption requires the attached bound paper journal')
        engine._assert_paper_plant_broker()
        row=self.con.execute('SELECT payload FROM identity WHERE id=1').fetchone()
        if row != (self._identity_payload,):
            raise RuntimeError('Feedback sealed identity mismatch')
        return hashlib.sha256(self._identity_payload.encode()).hexdigest()

    def feedback_applied(self, engine, event_id, context):
        binding=self._feedback_binding(engine)
        fingerprint=digest(context)
        applied=self.recovery.feedback_applied(self.feedback_epoch,binding,event_id,fingerprint)
        if not applied:
            self._feedback_contexts[event_id]=fingerprint
        return applied

    def is_durably_closed(self, engine, event_id):
        """Require an actual CLOSED lifecycle and matching completed checkpoint/ledger."""
        binding=self._feedback_binding(engine)
        if not event_id.startswith('trade_id:') or self.cursor < 1 or engine.combined_cycle_runtime is None:
            return False
        event=self.con.execute('SELECT fingerprint FROM applied_feedback WHERE epoch=? AND event_id=?', (self.feedback_epoch,event_id)).fetchone()
        if event is None:
            return False
        self.recovery.feedback_applied(self.feedback_epoch,binding,event_id,event[0])
        row=self.con.execute('SELECT timestamp,payload,previous_hash,hash FROM checkpoints WHERE seq=?', (self.cursor-1,)).fetchone()
        if row is None or row[3] != digest([self.cursor-1,row[0],row[2],row[1]]) or row[3] != self.previous_hash:
            raise RuntimeError('Corrupt completed feedback checkpoint')
        trade_id=event_id[len('trade_id:'):]
        try:
            snapshot=engine.combined_cycle_runtime.store.load(trade_id)
        except KeyError:
            return False
        if snapshot.record.lifecycle_state != 'CLOSED':
            return False
        completed=snapshot.protection.get('completed_trade')
        checkpoint=json.loads(row[1])
        ledger=[trade for trade in engine.completed_trades if trade.get('trade_id') == trade_id]
        persisted=[trade for trade in checkpoint['completed_trades'] if trade.get('trade_id') == trade_id]
        if (not isinstance(completed,dict) or completed.get('trade_id') != trade_id
            or len(ledger) != 1 or len(persisted) != 1
            or canonical(completed) != canonical(ledger[0]) or canonical(completed) != canonical(persisted[0])
            or checkpoint['close_receipts'].get(event_id) != 'DONE'
            or snapshot.protection.get('close_feedback_receipts',{}).get(event_id) != 'DONE'):
            return False
        position=[p for p in snapshot.protection.get('broker_snapshot',{}).get('positions',[]) if p.get('tradingsymbol') == snapshot.record.symbol]
        if len(position) != 1 or float(position[0].get('quantity',float('nan'))) != 0:
            return False
        return True

    def commit_feedback(self, engine, event_id):
        if not self.bound:
            raise RuntimeError('Feedback journal has no sealed replay identity')
        kwargs={}
        if self.feedback_compaction:
            binding=self._feedback_binding(engine)
            fingerprint=self._feedback_contexts.get(event_id)
            if fingerprint is None:
                raise RuntimeError('Feedback must validate current-epoch consumption before mutation')
            kwargs=dict(epoch=self.feedback_epoch,identity_checksum=binding,fingerprint=fingerprint)
        self.recovery.commit_feedback(event_id, checkpoint_state(engine), **kwargs)
        self._feedback_contexts.pop(event_id,None)

    def checkpoint(self, engine, timestamp):
        if not self.bound:
            raise RuntimeError('Journal has no sealed replay identity')
        # Reconcile active paper positions before anything is made durable.
        for symbol, trade in engine.open_trades.items():
            engine._verify_broker_position_reconciles(symbol, trade)
        for symbol, position in engine.broker.positions.items():
            if position['quantity'] != 0 and symbol not in engine.open_trades:
                raise RuntimeError('Unowned broker position at checkpoint')
        payload = canonical(checkpoint_state(engine))
        timestamp = str(timestamp)
        checksum = digest([self.cursor, timestamp, self.previous_hash, payload])
        self.con.execute('BEGIN IMMEDIATE')
        try:
            row = self.con.execute('SELECT timestamp,payload,previous_hash,hash FROM checkpoints WHERE seq=?', (self.cursor,)).fetchone()
            if row is not None:
                if row != (timestamp, payload, self.previous_hash, checksum):
                    raise RuntimeError(f'Restart reconciliation mismatch at checkpoint {self.cursor}')
                self.reconciled_checkpoints += 1
            else:
                count = self.con.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0]
                if count != self.cursor:
                    raise RuntimeError('Journal sequence has a gap')
                self.con.execute('INSERT INTO checkpoints VALUES (?,?,?,?,?)',
                    (self.cursor, timestamp, payload, self.previous_hash, checksum))
            self.con.execute('COMMIT')
        except BaseException:
            self.con.execute('ROLLBACK')
            raise
        self.previous_hash = checksum
        self.cursor += 1
        if not self.prefix_verified and self.cursor == self.prefix_length:
            self.prefix_verified = True
            self.resume_boundary = timestamp
        if self.feedback_compaction:
            engine._compact_close_feedback_receipts(list(engine._close_feedback_receipts))
        if self.after_commit is not None:
            self.after_commit(self, engine)

    def close(self):
        self.con.close()
        self._lease.close()
