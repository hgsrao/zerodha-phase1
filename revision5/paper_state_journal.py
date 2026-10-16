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

from revision5.protection_snapshot import build_plant_protection_snapshot


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
    def __init__(self, path, *, commands=(), after_commit=None):
        self.path = Path(path)
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

    def bind(self, engine, frames, warmup):
        if self.bound:
            raise RuntimeError('Journal/engine can run only once; restart with fresh objects')
        engine._assert_paper_plant_broker()
        if engine.broker.fills or engine.open_trades or engine.completed_trades:
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
            'packages': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()),
            'modes': [engine.closed_loop_mode, engine.pid_mode, engine.risk_profile,
                      engine.dynamic_target_setpoint_mode],
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
            self.con.execute('COMMIT')
        except BaseException:
            self.con.execute('ROLLBACK')
            raise
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
        if self.after_commit is not None:
            self.after_commit(self, engine)

    def close(self):
        self.con.close()
        self._lease.close()
