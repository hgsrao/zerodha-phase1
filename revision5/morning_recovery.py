"""Explicit, fail-closed boot preparation; never authorizes new trading risk.

This restores the supported fleet/ownership state and verifies contingent CNC
protection. Stock PID caches and live execution resumption remain separate gates.
"""
from copy import deepcopy
from dataclasses import asdict
from math import isfinite
import hashlib
from pathlib import Path

from revision5.state_recovery import StateRecoveryJournal
from revision5.position_lifecycle import B_OPEN
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
                 exit_states={key: asdict(value) for key, value in engine._exit_controller_states.items()},
                 engine_b=runtime.engine_b.export_state())
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
    engine.broker = broker
    for s in snapshots:
        engine.open_trades[s.record.symbol] = deepcopy(s.trade)
        engine._position_lifecycle[s.record.position_id] = s.record
        runtime._history[s.record.position_id] = deepcopy(s.protection.get('history', []))
    engine._morning_recovery_prepared = True
    return dict(prepared=True, admissions_allowed=False, checkpoint_timestamp=state['timestamp'],
                **({'fleet_loading_restored': False,
                    'fleet_loading_remaining_gate': 'Verified current gross/equity measurement and next execution cursor'}
                   if 'fleet_loading' in state else {}),
                ecs_demand_pu=ecs_demand, native_timestamp=saved_timestamp,
                native_bar_index=state['native_bar_index'],
                restored_positions=len(snapshots), protection=protection,
                remaining_gate='Complete stock PID, ECS, electrical and execution cursor restoration')
