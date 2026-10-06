"""Explicit, fail-closed boot preparation; never authorizes new trading risk.

This restores the supported fleet/ownership state and verifies contingent CNC
protection. Stock PID caches and live execution resumption remain separate gates.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
from math import isfinite
import hashlib
from pathlib import Path

from revision5.state_recovery import StateRecoveryJournal
from revision5.position_lifecycle import B_OPEN
from revision5.electrical_network import BreakerPosition
from revision5.governor import _InnerLoopState
from revision2_external.continuous_exit_controller import ExitControllerState
from revision2_external.broker_reconciliation import BrokerReconciliationService
from revision2_external.protection_policy import gtt_sell_limit, DELIVERY_UNAUTHORIZED


def identity(engine, account_id):
    root = Path(__file__).resolve().parents[1]
    source = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for package in ('revision2', 'revision3', 'revision5', 'revision2_external', 'runtime')
              for p in sorted((root/package).rglob('*.py'))}
    return dict(account_id=account_id, source=source, config=engine.config.config_hash,
                safety=engine.safety_contract.contract_hash, symbols=engine.symbols,
                equity=engine.starting_equity, governor_authority=engine.governor_authority,
                sector_map=engine.sector_map, session_schedule=engine.session_schedule,
                closed_loop_mode=engine.closed_loop_mode, pid_mode=engine.pid_mode,
                plant_mode=engine.plant_control.mode.value,
                **({'fleet_loading_policy': engine.plant_control.fleet_loading.policy.to_dict()}
                   if engine.plant_control.fleet_loading is not None else {}),
                runtime=asdict(engine.combined_cycle_runtime.config),
                handoff=asdict(engine.combined_cycle_runtime.handoff.config),
                engine_b=asdict(engine.combined_cycle_runtime.engine_b.policy))


_BREAKER_FIELDS = {'position', 'lockout_86', 'trip_reason', 'trip_source'}
_TRIP_BASE = {'ansi_code', 'reason', 'fleet_drawdown'}
_TRIP_MEASURED = _TRIP_BASE | {'nifty_15m_return', 'nifty_vol_z'}
_TRIP_CODES = {'ANSI 67', 'ANSI 81U', 'ANSI 81O', 'ANSI 21', 'MICOM_INPUT_UNAVAILABLE'}
_NONFINITE = {'+inf': float('inf'), '-inf': float('-inf')}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError('Invalid protection numeric state')
    return value


def _encode_trip(trip):
    if trip is None:
        return None
    if not isinstance(trip, dict):
        raise ValueError('Invalid MiCOM trip state')
    encoded = dict(trip)
    z = encoded.get('nifty_vol_z')
    if isinstance(z, float) and z in (float('inf'), float('-inf')):
        encoded['nifty_vol_z'] = '+inf' if z > 0 else '-inf'
    return encoded


def capture_protection(engine):
    """Versioned electrical/MiCOM/day-gate state; validated here so a bad value never reaches disk."""
    network = engine.real_plant_dcs.electrical_network
    day = engine._active_trading_date
    payload = dict(
        version=1,
        breakers={breaker_id: dict(position=getattr(breaker.position, 'value', breaker.position),
                                   lockout_86=breaker.lockout_86, trip_reason=breaker.trip_reason,
                                   trip_source=breaker.trip_source)
                  for breaker_id, breaker in network.breakers.items()},
        master_breaker_open=engine.real_plant_dcs.grid_relay.master_breaker_open,
        micom_opened_intertie=engine._micom_opened_intertie,
        micom_trip=_encode_trip(engine._micom_trip),
        active_trading_date=None if day is None else (day.isoformat() if isinstance(day, date) else day),
        day_start_equity=engine._day_start_equity,
        symbol_consecutive_losses=dict(engine.symbol_consecutive_losses))
    stage_protection(engine, payload)
    return payload


def stage_protection(engine, payload):
    """Strictly validate a protection_v1 payload against the live objects; mutates nothing."""
    if not isinstance(payload, dict) or set(payload) != {
            'version', 'breakers', 'master_breaker_open', 'micom_opened_intertie', 'micom_trip',
            'active_trading_date', 'day_start_equity', 'symbol_consecutive_losses'}:
        raise ValueError('Invalid protection_v1 schema')
    if type(payload['version']) is not int or payload['version'] != 1:
        raise ValueError('Unsupported protection payload version')
    network = engine.real_plant_dcs.electrical_network
    breakers = payload['breakers']
    if not isinstance(breakers, dict) or set(breakers) != set(network.breakers):
        raise ValueError('Breaker recovery identity mismatch')
    staged_breakers = {}
    for breaker_id, values in breakers.items():
        if not isinstance(values, dict) or set(values) != _BREAKER_FIELDS:
            raise ValueError('Invalid saved breaker schema')
        position, locked = values['position'], values['lockout_86']
        if type(position) is not str or position not in {p.value for p in BreakerPosition}:
            raise ValueError('Invalid saved breaker position')
        position = BreakerPosition(position)
        if type(locked) is not bool:
            raise ValueError('Invalid saved breaker lockout')
        for name in ('trip_reason', 'trip_source'):
            if values[name] is not None and type(values[name]) is not str:
                raise ValueError('Invalid saved breaker trip text')
        if locked and position is not BreakerPosition.OPEN:
            raise ValueError('Locked breaker must be OPEN')
        text_present = values['trip_reason'] is not None and values['trip_source'] is not None
        text_absent = values['trip_reason'] is None and values['trip_source'] is None
        if (position is BreakerPosition.OPEN and not text_present) or (
                position is BreakerPosition.CLOSED and not text_absent):
            raise ValueError('Breaker trip text inconsistent with position')
        staged_breakers[breaker_id] = (position, locked, values['trip_reason'], values['trip_source'])
    master, opened = payload['master_breaker_open'], payload['micom_opened_intertie']
    if type(master) is not bool or type(opened) is not bool:
        raise ValueError('Invalid saved MiCOM flags')
    if opened and staged_breakers[network.GRID_BREAKER][0] is not BreakerPosition.OPEN:
        raise ValueError('MiCOM-opened intertie flag inconsistent with grid breaker')
    trip = payload['micom_trip']
    if trip is not None:
        if (not isinstance(trip, dict) or set(trip) not in (_TRIP_BASE, _TRIP_MEASURED)
                or trip['ansi_code'] not in _TRIP_CODES or type(trip['reason']) is not str):
            raise ValueError('Invalid saved MiCOM trip')
        full = set(trip) == _TRIP_MEASURED
        code = trip['ansi_code']
        if not ((code == 'MICOM_INPUT_UNAVAILABLE' and not full) or code == 'ANSI 67' or full and code != 'MICOM_INPUT_UNAVAILABLE'):
            raise ValueError('MiCOM trip fields inconsistent with trip code')
        if _number(trip['fleet_drawdown']) < 0:
            raise ValueError('Invalid saved MiCOM drawdown')
        trip = dict(trip)
        if full:
            _number(trip['nifty_15m_return'])
            z = trip['nifty_vol_z']
            trip['nifty_vol_z'] = _NONFINITE[z] if isinstance(z, str) and z in _NONFINITE else _number(z)
    day = payload['active_trading_date']
    if day is not None:
        try:
            parsed = date.fromisoformat(day) if type(day) is str else None
        except ValueError:
            parsed = None
        if parsed is None or parsed.isoformat() != day:
            raise ValueError('Invalid saved trading date')
        day = parsed
    equity = _number(payload['day_start_equity'])
    if equity <= 0:
        raise ValueError('Invalid saved day-start equity')
    losses = payload['symbol_consecutive_losses']
    if (not isinstance(losses, dict) or not set(losses).issubset(set(engine.symbols))
            or any(type(v) is not int or v < 0 for v in losses.values())):
        raise ValueError('Invalid saved symbol loss counts')
    return dict(breakers=staged_breakers, master=master, opened=opened, trip=trip, day=day,
                equity=equity, losses=dict(losses))


def _install_protection(engine, staged):
    """Plain attribute restoration: no close()/reset_86()/trip() calls, no admissions change."""
    network = engine.real_plant_dcs.electrical_network
    for breaker_id, (position, locked, reason, source) in staged['breakers'].items():
        breaker = network.breakers[breaker_id]
        breaker.position, breaker.lockout_86 = position, locked
        breaker.trip_reason, breaker.trip_source = reason, source
    engine.real_plant_dcs.grid_relay.master_breaker_open = staged['master']
    engine._micom_opened_intertie = staged['opened']
    engine._micom_trip = staged['trip']
    engine._active_trading_date = staged['day']
    engine._day_start_equity = staged['equity']
    engine.symbol_consecutive_losses = staged['losses']


def capture(engine, account_id, timestamp):
    runtime = engine.combined_cycle_runtime
    if runtime is None or engine.real_plant_dcs is None:
        raise RuntimeError('Boot checkpoint requires native plant and combined-cycle runtime')
    if not isinstance(account_id, str) or not account_id:
        raise ValueError('Boot checkpoint requires explicit account identity')
    governors = {}
    for key, gov in engine._bay_governors.items():
        governors[key] = {name: deepcopy(getattr(gov, name)) for name in
                          ('history_r', 'integral_error', 'last_error', 'last_control_u')}
        governors[key]['inner'] = [(key, asdict(value)) for key, value in gov._inner_states.items()]
    merit = engine.plant_control.dispatch_controller.merit_source
    state = dict(identity=identity(engine, account_id), timestamp=timestamp,
                 mtm_peak=engine._mtm_peak, mtm_drawdown=engine._mtm_max_drawdown_fraction,
                 ecs_demand_pu=engine.plant_control.ecs._previous_demand,
                 **({'fleet_loading': engine.plant_control.export_fleet_loading_state()}
                    if engine.plant_control.fleet_loading is not None else {}),
                 merit_history=deepcopy(merit.trade_history_r), merit_weights=dict(merit.weights),
                 cooldowns=dict(engine.symbol_cooldown_until_bar), trips=dict(engine.symbol_tripped),
                 governors=governors, close_receipts=dict(engine._close_feedback_receipts),
                 bays={key: {field: getattr(bay, field) for field in
                             ('consecutive_stops', 'cooldown_until_bar_exclusive', 'tripped_offline')}
                       for key, bay in engine.real_plant_dcs.bays.items()},
                 native_bar_index=engine._native_bar_index, native_timestamp=engine._native_timestamp,
                 native_trading_date=engine.real_plant_dcs.current_trading_date,
                 trade_sequence=engine._trade_sequence,
                 execution_v1=dict(
                     version=1,
                     execution_halted=bool(engine._execution_halted),
                     processed_bar_indices=dict(engine._resume_processed_bar_indices),
                     data_identity=dict(engine._resume_data_identity),
                     entry_bar_index=dict(engine._resume_entry_bar_index),
                     ticks_since_reweight=int(engine._resume_ticks_since_reweight),
                     portfolio_weights=dict(engine._portfolio_weights),
                     last_close=dict(engine._last_close),
                     equity_curve=list(engine._equity_curve),
                     mtm_equity_curve=list(engine._mtm_equity_curve),
                     conviction_history={
                         symbol: {
                             name: list(values)
                             for name, values in measures.items()
                         }
                         for symbol, measures in engine._conviction_history.items()
                     },
                     conviction_rank=deepcopy(engine._conviction_rank),
                 ),
                 exit_states={key: asdict(value) for key, value in engine._exit_controller_states.items()},
                 engine_b=runtime.engine_b.export_state(), protection_v1=capture_protection(engine),
                 mpc_v1=engine.mpc.export_state(engine.config),
                 studies_v1=engine.chart_studies.export_state(),
                 pa_v1=engine.pa.export_state(engine.config),
                 id_v1=engine.id_box.export_state(engine.config, engine.symbols),
                 hmm_hysteresis_v1={
                     symbol: controller.export_state(engine.config)
                     for symbol, controller in engine._hmm_risk_hysteresis.items()
                 },
                 outcomes_v1=engine.closed_loop.outcomes.export_state(
                     engine.config, engine.symbols))
    snapshots = runtime.store.list_open()
    if {s.record.symbol for s in snapshots} != set(engine.open_trades):
        raise RuntimeError('Active trades differ from lifecycle store at boot checkpoint')
    updates = []
    for s in snapshots:
        trade = deepcopy(engine.open_trades[s.record.symbol])
        if trade.get('trade_id') != s.record.position_id or engine._position_lifecycle.get(s.record.position_id) != s.record:
            raise RuntimeError('Active trade ownership differs at boot checkpoint')
        protection = deepcopy(s.protection)
        protection['engine_b_state'] = runtime.engine_b.export_state()
        updates.append((s.record,trade,protection,s.revision))
    # Post-fill observers can update trade payloads after the initial lifecycle save.
    # Refresh them atomically with the fleet checkpoint, retaining store invariants.
    StateRecoveryJournal(runtime.store.connection).checkpoint_boot(state, store=runtime.store,
                                                                    position_updates=updates)


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError('Invalid numeric fleet recovery state')
    return value


def _check_active_gtts(broker, snapshots):
    """Unknown active triggers may create later unowned exposure."""
    from decimal import Decimal, ROUND_FLOOR
    by_symbol = {s.record.symbol: s for s in snapshots}
    seen = set()
    for row in broker.get_gtts():
        if row.get('status') != 'active':
            continue
        condition = row.get('condition', {})
        symbol = condition.get('tradingsymbol')
        if symbol not in by_symbol or symbol in seen:
            raise RuntimeError('Unknown or duplicate active GTT during startup')
        seen.add(symbol)
        s = by_symbol[symbol]
        tick = Decimal(str(broker.protection_tick_size(symbol)))
        limit = gtt_sell_limit(s.record.current_stop_price, float(tick))
        orders = row.get('orders', [])
        expected = dict(exchange='NSE', tradingsymbol=symbol, product='CNC', transaction_type='SELL',
                        quantity=broker_quantity(s), order_type='LIMIT', price=limit)
        if (row.get('type') != 'single' or condition.get('exchange') != 'NSE'
            or condition.get('trigger_values') != [s.record.current_stop_price]
            or len(orders) != 1 or any(orders[0].get(k) != v for k,v in expected.items())):
            raise RuntimeError('Active GTT differs from durable protective geometry')


def broker_quantity(snapshot):
    from revision5.combined_cycle_store import CombinedCycleStore
    return CombinedCycleStore.trade_quantity(snapshot.trade)


def prepare(engine, account_id, broker, *, next_timestamp=None):
    engine._execution_halted = True
    if engine.open_trades or engine.completed_trades or engine._position_lifecycle:
        raise RuntimeError('Morning recovery requires a fresh engine')
    runtime = engine.combined_cycle_runtime
    if runtime is None or engine.real_plant_dcs is None:
        raise RuntimeError('Morning recovery requires native plant and combined-cycle runtime')
    state = StateRecoveryJournal(runtime.store.connection).load_boot()
    if state['identity'] != identity(engine, account_id):
        raise RuntimeError('Morning recovery identity mismatch')
    # Staged before any broker call or state install; absence is never a healthy fallback.
    if 'protection_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks protection_v1 state')
    staged_protection = stage_protection(engine, state['protection_v1'])
    if 'mpc_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks mpc_v1 state')
    # Fresh instance: the live MPC is replaced only by the final successful install.
    staged_mpc = type(engine.mpc)(pid_enabled=engine.mpc.pid_enabled)
    staged_mpc.restore_state(state['mpc_v1'], engine.config)
    known = set(engine.symbols)
    if not (set(staged_mpc._confidence_history) | set(staged_mpc._entry_pids)
            | set(staged_mpc._exit_pids)).issubset(known):
        raise ValueError('MPC state names a symbol outside the engine universe')
    if 'studies_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks studies_v1 state')
    staged_studies = type(engine.chart_studies)(config=engine.config)
    staged_studies.restore_state(state['studies_v1'])
    if not set(staged_studies._symbols).issubset(known):
        raise ValueError('Studies state names a symbol outside the engine universe')
    if 'pa_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks pa_v1 state')
    # Warmup may not postdate the durable cursor (checkpoint time when no tick has completed).
    staged_pa = type(engine.pa)()
    staged_pa.restore_state(state['pa_v1'], engine.config,
                            not_after=state['native_timestamp'] if state['native_timestamp'] is not None
                            else state['timestamp'])
    if not set(staged_pa._scale).issubset(known):
        raise ValueError('PA state names a symbol outside the engine universe')

    if 'id_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks id_v1 state')
    staged_id = type(engine.id_box)(config=engine.config)
    staged_id.restore_state(state['id_v1'], engine.config, engine.symbols)

    if 'hmm_hysteresis_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks hmm_hysteresis_v1 state')
    raw_hysteresis = state['hmm_hysteresis_v1']
    if type(raw_hysteresis) is not dict or not set(raw_hysteresis).issubset(known):
        raise ValueError('HMM hysteresis state names a symbol outside the engine universe')
    from revision2_external.closed_loop_control import HMMRiskHysteresis
    staged_hysteresis = {}
    for symbol, payload in raw_hysteresis.items():
        controller = HMMRiskHysteresis()
        controller.restore_state(payload, engine.config)
        staged_hysteresis[symbol] = controller

    if 'outcomes_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks outcomes_v1 state')
    staged_outcomes = type(engine.closed_loop.outcomes)(config=engine.config)
    staged_outcomes.restore_state(state['outcomes_v1'], engine.config, engine.symbols)

    # C04-B: stage execution continuation state before any broker call or
    # mutation of the fresh engine. Missing/corrupt state fails closed.
    if 'execution_v1' not in state:
        raise RuntimeError('Boot checkpoint lacks execution_v1 state')
    execution = state['execution_v1']
    if type(execution) is not dict or execution.get('version') != 1:
        raise ValueError('Invalid execution_v1 state')

    saved_execution_halted = execution.get('execution_halted')
    if type(saved_execution_halted) is not bool:
        raise ValueError('Invalid saved execution halt state')

    processed = execution.get('processed_bar_indices')
    data_identity = execution.get('data_identity')
    entry_bars = execution.get('entry_bar_index')
    ticks = execution.get('ticks_since_reweight')
    weights = execution.get('portfolio_weights')
    last_close = execution.get('last_close')
    equity_curve = execution.get('equity_curve')
    mtm_curve = execution.get('mtm_equity_curve')
    conviction_history = execution.get('conviction_history')
    conviction_rank = execution.get('conviction_rank')

    if type(processed) is not dict or not set(processed).issubset(known):
        raise ValueError('Invalid processed-bar continuation state')
    if any(type(value) is not int or value < 0 for value in processed.values()):
        raise ValueError('Invalid processed-bar index')

    if type(data_identity) is not dict or set(data_identity) != set(processed):
        raise ValueError('Invalid certified data identity')
    if any(type(symbol) is not str or type(value) is not str or len(value) != 64
           for symbol, value in data_identity.items()):
        raise ValueError('Invalid certified data digest')

    if type(entry_bars) is not dict or not set(entry_bars).issubset(known):
        raise ValueError('Invalid entry-bar continuation state')
    if any(type(value) is not int or value < 0 for value in entry_bars.values()):
        raise ValueError('Invalid entry-bar index')

    if type(ticks) is not int or ticks < 0:
        raise ValueError('Invalid portfolio-refit continuation state')

    if type(weights) is not dict or set(weights) != known:
        raise ValueError('Invalid portfolio-weight continuation state')
    if any(type(value) not in (int, float) for value in weights.values()):
        raise ValueError('Invalid portfolio weight')

    if type(last_close) is not dict or not set(last_close).issubset(known):
        raise ValueError('Invalid last-close continuation state')
    if any(type(value) not in (int, float) for value in last_close.values()):
        raise ValueError('Invalid last-close value')

    if type(equity_curve) is not list or not equity_curve:
        raise ValueError('Invalid equity continuation state')
    if any(type(value) not in (int, float) for value in equity_curve):
        raise ValueError('Invalid equity curve value')

    if type(mtm_curve) is not list or not mtm_curve:
        raise ValueError('Invalid MTM continuation state')
    for item in mtm_curve:
        if (type(item) not in (list, tuple) or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) not in (int, float)):
            raise ValueError('Invalid MTM curve value')

    if type(conviction_history) is not dict or not set(conviction_history).issubset(known):
        raise ValueError('Invalid conviction-history continuation state')
    if type(conviction_rank) is not dict or not set(conviction_rank).issubset(known):
        raise ValueError('Invalid conviction-rank continuation state')

    staged_execution_halted = bool(saved_execution_halted)
    staged_processed = {symbol: int(value) for symbol, value in processed.items()}
    staged_data_identity = dict(data_identity)
    staged_entry_bars = {symbol: int(value) for symbol, value in entry_bars.items()}
    staged_ticks = int(ticks)
    staged_weights = {symbol: float(value) for symbol, value in weights.items()}
    staged_last_close = {symbol: float(value) for symbol, value in last_close.items()}
    staged_equity_curve = [float(value) for value in equity_curve]
    staged_mtm_curve = [(str(ts), float(value)) for ts, value in mtm_curve]

    from collections import deque
    staged_conviction_history = {}
    for symbol, measures in conviction_history.items():
        if type(measures) is not dict:
            raise ValueError('Invalid conviction-history measures')
        staged_conviction_history[symbol] = {}
        for name, values in measures.items():
            if type(name) is not str or type(values) is not list:
                raise ValueError('Invalid conviction-history series')
            staged_conviction_history[symbol][name] = deque(
                values, maxlen=engine.governor_config.z_window_bars)

    staged_conviction_rank = deepcopy(conviction_rank)

    import pandas as pd
    saved_timestamp = state['native_timestamp']
    if saved_timestamp is not None and pd.isna(pd.Timestamp(saved_timestamp)):
        raise ValueError('Invalid saved native timestamp')
    if next_timestamp is not None:
        if saved_timestamp is None:
            raise RuntimeError('No completed native tick cursor is available')
        def utc(value):
            ts = pd.Timestamp(value)
            if pd.isna(ts):
                raise ValueError('Invalid next tick timestamp')
            return (ts.tz_localize('Asia/Kolkata') if ts.tz is None else ts).tz_convert('UTC')
        if utc(next_timestamp) <= utc(saved_timestamp):
            raise RuntimeError('Next tick does not advance the durable cursor')
    if getattr(broker, 'environment', None) == 'live' and not broker.verify_account_identity(account_id):
        raise RuntimeError('Broker session account differs from boot checkpoint')
    snapshots = runtime.store.list_open()
    if any(s.record.lifecycle_state != B_OPEN or s.record.product != 'CNC'
           or s.record.direction != 'BUY' for s in snapshots):
        raise RuntimeError('Morning recovery supports acknowledged long CNC carry only')
    if len({s.record.symbol for s in snapshots}) != len(snapshots):
        raise RuntimeError('Duplicate lifecycle symbol during recovery')
    expected = [dict(symbol=s.record.symbol, product='CNC', exchange='NSE',
                     protective_order_id=s.protection.get('protective_order_id'),
                     quantity=runtime.store.trade_quantity(s.trade), protection_required=False)
                for s in snapshots]
    # Compare ALL broker inventory and pending orders before any protective write.
    truth = broker.snapshot()
    inventory = BrokerReconciliationService().reconcile(expected, truth)
    if not inventory.passed:
        raise RuntimeError('Morning inventory reconciliation failed: '+ '; '.join(inventory.discrepancies))
    # A still-active DAY exit plus a new GTT could both sell the same inventory.
    if any(o.get('status') in ('OPEN','TRIGGER PENDING') for o in truth.get('orders', [])):
        raise RuntimeError('Existing pending DAY order must be reconciled before GTT rearm')
    _check_active_gtts(broker, snapshots)
    merit = engine.plant_control.dispatch_controller.merit_source
    if set(state['merit_weights']) != set(merit.weights) or set(state['merit_history']) != set(merit.trade_history_r):
        raise ValueError('Fleet bay identity mismatch')
    weights = {k: _finite(v) for k, v in state['merit_weights'].items()}
    if any(v < 0 for v in weights.values()) or abs(sum(weights.values())-1) > 1e-8:
        raise ValueError('Invalid saved merit allocation')
    histories = {k: [_finite(v) for v in values] for k, values in state['merit_history'].items()}
    peak = _finite(state['mtm_peak'])
    ecs_demand = _finite(state['ecs_demand_pu'])
    if not 0 <= ecs_demand <= 1:
        raise ValueError('Invalid saved ECS demand')
    drawdown = _finite(state['mtm_drawdown'])
    if peak <= 0 or not 0 <= drawdown <= 1:
        raise ValueError('Invalid saved MTM state')
    for field in ('cooldowns', 'trips'):
        if not set(state[field]).issubset(set(engine.symbols)):
            raise ValueError('Symbol recovery identity mismatch')
    if any(type(v) is not int or v < 0 for v in state['cooldowns'].values()):
        raise ValueError('Invalid saved symbol cooldown')
    if any(type(v) is not bool for v in state['trips'].values()):
        raise ValueError('Invalid saved symbol trip')
    if set(state['governors']) != set(engine._bay_governors) or set(state['bays']) != set(engine.real_plant_dcs.bays):
        raise ValueError('Governor recovery identity mismatch')
    parsed_governors = {}
    for key, values in state['governors'].items():
        parsed = {n: [_finite(v) for v in values[n]] if n == 'history_r' else _finite(values[n])
                  for n in ('history_r', 'integral_error', 'last_error', 'last_control_u')}
        inner = {}
        for position_id, value in values['inner']:
            if position_id in inner or (position_id is not None and not isinstance(position_id, str)):
                raise ValueError('Invalid governor inner position identity')
            item = _InnerLoopState(**value)
            if type(item.active) is not bool or type(item.trailing_active) is not bool:
                raise ValueError('Invalid governor inner flags')
            for n in ('integral_error','last_error','last_control_u','protected_r_floor'):
                _finite(getattr(item,n))
            inner[position_id] = item
        parsed['_inner_states'] = inner
        parsed_governors[key] = parsed
    for values in state['bays'].values():
        if (set(values) != {'consecutive_stops','cooldown_until_bar_exclusive','tripped_offline'}
            or type(values['tripped_offline']) is not bool
            or any(type(values[n]) is not int or values[n] < 0 for n in ('consecutive_stops','cooldown_until_bar_exclusive'))):
            raise ValueError('Invalid saved bay cooldown/trip')
    exit_states = {key: ExitControllerState(**value) for key, value in state['exit_states'].items()}
    # Validate Engine B state in a temporary controller before changing owners.
    from revision5.engine_b_management import EngineBController
    staged_b = EngineBController(runtime.engine_b.policy)
    staged_b.restore_state(state['engine_b'])
    restored_b=staged_b.export_state()
    live_ids={snapshot.record.position_id for snapshot in snapshots}
    restored_b['positions']={key:value for key,value in restored_b['positions'].items() if key in live_ids}
    if (type(state['native_bar_index']) is not int or state['native_bar_index'] < -1
        or type(state['trade_sequence']) is not int or state['trade_sequence'] < 0):
        raise ValueError('Invalid recovery cursor')
    if any(not isinstance(k, str) or value != 'DONE' for k,value in state['close_receipts'].items()):
        raise ValueError('Unresolved close feedback during recovery')
    for s in snapshots:
        if s.protection.get('stop') != s.record.current_stop_price:
            raise ValueError('Lifecycle stop differs from durable protection')
        if s.trade.get('governor_stop_price', s.record.current_stop_price) != s.record.current_stop_price:
            raise ValueError('Trade governor stop differs from lifecycle')
    # GTT acceptance is insufficient: adapter must read back active exact geometry.
    protection = []
    for s in snapshots:
        receipt = broker.ensure_cnc_gtt(s.record.position_id, s.record.symbol,
                                       s.record.current_stop_price,
                                       runtime.store.trade_quantity(s.trade))
        if receipt.get('trip_code') == DELIVERY_UNAUTHORIZED:
            engine._trip_delivery_unauthorised(s.record.symbol, state['timestamp'], receipt.get('reason',''))
        if not receipt.get('passed') or not receipt.get('verified'):
            raise RuntimeError('CNC GTT protection not verified: '+ str(receipt))
        protection.append(receipt)
    # Inventory can change during protective writes. Recheck before installing state.
    inventory = BrokerReconciliationService().reconcile(expected, broker.snapshot())
    if not inventory.passed:
        raise RuntimeError('Broker inventory changed during protection preparation')
    _check_active_gtts(broker, snapshots)
    # Broker inventory reconciliation supplies quantity/product, not independently
    # verified current mark-to-market gross/equity or a certified execution slice.
    # Retain the fleet state for explicit later validation; never substitute its
    # saved measurement as current broker truth or unblock direct execution.
    if 'fleet_loading' in state:
        engine._pending_fleet_loading_recovery_state = deepcopy(state['fleet_loading'])
    engine._mtm_peak, engine._mtm_max_drawdown_fraction = peak, drawdown
    engine.plant_control.ecs._previous_demand = ecs_demand
    engine.symbol_cooldown_until_bar = deepcopy(state['cooldowns'])
    engine.symbol_tripped = deepcopy(state['trips'])
    merit.weights, merit.trade_history_r = weights, histories
    for key, values in parsed_governors.items():
        for name, value in values.items():
            setattr(engine._bay_governors[key], name, value)
    for key, values in state['bays'].items():
        for name, value in values.items():
            setattr(engine.real_plant_dcs.bays[key], name, value)
    runtime.engine_b.restore_state(restored_b)
    runtime._history={key:value for key,value in runtime._history.items() if key in live_ids}
    engine._native_bar_index = state['native_bar_index']
    engine._native_timestamp = state['native_timestamp']
    engine.real_plant_dcs.current_bar_index = state['native_bar_index']
    engine.real_plant_dcs.current_trading_date = state['native_trading_date']
    engine._trade_sequence = state['trade_sequence']
    engine._exit_controller_states = exit_states
    engine._close_feedback_receipts = deepcopy(state['close_receipts'])
    _install_protection(engine, staged_protection)
    engine.mpc = staged_mpc
    engine.chart_studies = staged_studies
    engine.pa = staged_pa
    engine.id_box = staged_id
    engine._hmm_risk_hysteresis = staged_hysteresis
    engine.closed_loop.outcomes = staged_outcomes

    # C04-B: atomic installation of the staged execution continuation state.
    engine._resume_execution_halted = staged_execution_halted
    engine._resume_processed_bar_indices = staged_processed
    engine._resume_data_identity = staged_data_identity
    engine._resume_entry_bar_index = staged_entry_bars
    engine._resume_ticks_since_reweight = staged_ticks
    engine._portfolio_weights = staged_weights
    engine._last_close = staged_last_close
    engine._equity_curve = staged_equity_curve
    engine._mtm_equity_curve = staged_mtm_curve
    engine._conviction_history = staged_conviction_history
    engine._conviction_rank = staged_conviction_rank

    engine.broker = broker
    for s in snapshots:
        engine.open_trades[s.record.symbol] = deepcopy(s.trade)
        engine._position_lifecycle[s.record.position_id] = s.record
        runtime._history[s.record.position_id] = deepcopy(s.protection.get('history', []))
    engine._morning_recovery_prepared = True
    return dict(prepared=True, admissions_allowed=False, plant_protection_restored=True,
                checkpoint_timestamp=state['timestamp'],
                **({'fleet_loading_restored': False,
                    'fleet_loading_remaining_gate': 'Verified current gross/equity measurement and next execution cursor'}
                   if 'fleet_loading' in state else {}),
                ecs_demand_pu=ecs_demand, native_timestamp=saved_timestamp,
                native_bar_index=state['native_bar_index'],
                restored_positions=len(snapshots), protection=protection,
                remaining_gate='Complete stock PID, ECS, electrical and execution cursor restoration')
