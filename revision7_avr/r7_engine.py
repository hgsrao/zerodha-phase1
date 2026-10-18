"""R7 engine adapter over the existing shared R5/R6 native engine framework.

The default class is the engine, not a harness. No live client is constructed.
The native entry/exit, safety, sizing and order/position ledger remain authoritative.
"""
from collections import Counter, deque
from dataclasses import asdict
from copy import deepcopy
import json
from hashlib import sha256
import math

from revision5.topology import SYMBOL_TO_BAY, BAY_IDS
from revision6_avr.r6_turbine_governor import repository_defaults, TurbineGovernorUnit
from .r7_state import Bar, Context, ControlConfig, BayAggregateState, number, clamp
from .r7_coordinated_controller import SymbolController
from .r7_allocator import Utilization, droop_weights, increase_demand_pu, allocate_headroom

R7_DECISION_MEMORY_LIMIT = 1000


class _StudiesTap:
    """Forward studies calls unchanged and retain only their current causal bar."""
    def __init__(self, target, owner):
        self.target, self.owner = target, owner
        self.evaluations = Counter()

    def __getattr__(self, name):
        return getattr(self.target, name)

    def evaluate(self, symbol, bars):
        result = self.target.evaluate(symbol, bars)
        self.evaluations[symbol] += 1
        row = bars.iloc[-1]
        index = int(bars.index[-1])
        self.owner._r7_observed[symbol] = (index, row.copy(), deepcopy(result))
        return result


class R7EngineMixin:
    def __init__(self, *args, r7_configs, r7_dispatch_scale=0.01,
                 r7_allow_native_shorts=True, r7_decision_log_path=None,
                 r7_decision_memory=5000, **kwargs):
        # Configs are explicit: selecting one symbol must not reallocate its entire bay.
        configs = {c.symbol: c for c in r7_configs}
        if len(configs) != len(r7_configs) or not configs or sum(c.budget_fraction for c in configs.values()) > 1 + 1e-12:
            raise ValueError('invalid or duplicated symbol allocations')
        for symbol, c in configs.items():
            if SYMBOL_TO_BAY.get(symbol) != c.bay_id:
                raise ValueError(f'unmapped/mismatched R7 symbol: {symbol}')
        self.r7_dispatch_scale = number(r7_dispatch_scale)
        if self.r7_dispatch_scale <= 0 or type(r7_allow_native_shorts) is not bool:
            raise ValueError('invalid dispatch scale or short policy')
        self.r7_allow_native_shorts = r7_allow_native_shorts
        self.r7_configs = configs
        self.r7_symbol_controllers = {s: SymbolController(c) for s, c in configs.items()}
        # Commissioned sharing weights w_i = B_i / R_i, fixed for the run.
        self.r7_weights = droop_weights(configs.values())
        # Timestamp batch: every symbol at a market timestamp is allocated against one
        # common portfolio state before any governor or FSM state is committed.
        self._r7_batch = {}
        self._r7_batch_ts = None
        self._r7_tick_events = None
        self._r7_deferred_exits = {}
        self._r7_utilization_ts = None
        self._r7_utilization = None
        self._r7_utilization_error = None
        self.r7_bay_state = {b: BayAggregateState() for b in BAY_IDS}
        self.r7_physical_units = {b: TurbineGovernorUnit(c, p_mech_pu=c.power_min_pu)
                                  for b, c in repository_defaults().items()}
        self.r7_gate_counts = Counter()
        # Complete decision telemetry goes to an external append-only JSONL log.
        # Memory keeps only a bounded recent window; checkpoints hold a count and a
        # rolling SHA-256 chain, so their size does not grow with replay length.
        if type(r7_decision_memory) is not int or r7_decision_memory < 1:
            raise ValueError('decision memory must be a positive integer')
        # Keep the in-memory audit window bounded even if a caller requests a larger
        # value. The complete decision stream remains in the append-only log and is
        # represented in checkpoints by count plus rolling digest.
        self.r7_decisions = deque(maxlen=min(r7_decision_memory, R7_DECISION_MEMORY_LIMIT))
        self.r7_decision_log_path = r7_decision_log_path
        self._r7_decision_log = None
        self.r7_decision_count = 0
        self.r7_decision_digest = '0' * 64
        self._r7_observed = {}
        self._r7_current_timestamp = None
        self._r7_exit_scope = False
        self.r7_normal_exit_state = {}
        self.r7_adaptation_enabled = False
        self.r7_adaptation_mode = 'off'
        super().__init__(*args, **kwargs)
        if set(self.symbols) != set(configs):
            raise ValueError('R7 configs must exactly match requested universe')
        if getattr(self, 'full_adaptation', False) or getattr(self, 'dynamic_profile_mode', 'off') != 'off':
            raise ValueError('R7 requires runtime parameter adaptation/profile consumption off')
        self.chart_studies = _StudiesTap(self.chart_studies, self)
        self.r7_topology_report = {s: c.bay_id for s, c in configs.items()}

    def _r7_actual(self, symbol, close):
        equity = number(float(self._equity()))
        if equity <= 0:
            raise ValueError('R7 exposure requires positive equity')
        quantity = number(float(self._broker_position(symbol).get('quantity', 0)))
        # Keep excess exposure visible rather than clamping it away.
        return quantity * close / (equity * self.r7_configs[symbol].budget_fraction)

    def _r7_filled_gross_notional(self):
        """Gross notional of FILLED native positions only (native base definition)."""
        from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Core
        if isinstance(self, Core):
            return number(float(Core._gross_exposure_notional(self)))
        return number(float(self._gross_exposure_notional()))

    def _r7_pending_increase_notional(self):
        """Conservative notional of working exposure-increasing (entry) orders."""
        if hasattr(self, '_pending_entries'):                       # Engine C (Backtrader)
            return sum(number(float(r['reserved_notional'])) for r in self._pending_entries())
        pending = 0.0
        for record in getattr(self, '_inflight', {}).values():       # Engine B (Nautilus)
            if record.get('kind') == 'entry':
                # Engine B books a position into open_trades only when the entry order
                # completes, so the filled part of a working partial is in neither the
                # filled ledger nor the remainder. Reserve the full requested quantity
                # until completion: conservative and never double counted.
                requested = max(0.0, float(record['requested']))
                if requested > 0:
                    pending += requested * self._r7_pending_price(record)
        return pending

    def _r7_pending_price(self, record):
        """Conservative valuation price for a working entry: the larger of the last close
        and the planned entry price. A missing or non-positive price is never valued as
        zero; it raises, and the caller fails closed."""
        candidates = []
        last = self._last_close.get(record['symbol'])
        if last is not None:
            candidates.append(float(last))
        plan = (record.get('intent') or {}).get('plan')
        if plan is not None and getattr(plan, 'entry_price', None) is not None:
            candidates.append(float(plan.entry_price))
        valid = [p for p in candidates if math.isfinite(p) and p > 0]
        if not valid:
            raise ValueError(f"no valid price for working entry on {record['symbol']}")
        return max(valid)

    def _r7_gross_limit_notional(self):
        run = getattr(self, '_run', None)
        fraction = getattr(run, 'max_gross_fraction', None)
        if fraction is None:
            fraction = float(self.safety_contract.values['max_gross_exposure_fraction'])
        equity = number(float(self._equity()))
        return equity * number(float(fraction))

    def r7_utilization(self, timestamp):
        """Common portfolio measurement, snapshotted once per clock timestamp so every
        symbol at this tick is allocated against the same headroom (order independent)."""
        key = str(timestamp)
        if self._r7_utilization_ts != key:
            try:
                self._r7_utilization = Utilization(self._r7_filled_gross_notional(),
                                                   self._r7_pending_increase_notional(),
                                                   self._r7_gross_limit_notional())
                self._r7_utilization_error = None
            except (ValueError, KeyError, TypeError) as exc:
                # Fail closed: no exposure increase may be authorized without a valid
                # measurement. Reductions remain approved by the allocator.
                self._r7_utilization = None
                self._r7_utilization_error = f'{type(exc).__name__}: {exc}'
                self.r7_gate_counts['UTILIZATION_UNAVAILABLE_FAIL_CLOSED'] += 1
            self._r7_utilization_ts = key
        return self._r7_utilization

    def _r7_execution_blocked(self, symbol):
        return (symbol in getattr(self, '_symbol_inflight', {}) or symbol in getattr(self, '_symbol_working', {})
                or bool(getattr(self, '_execution_halted', False)))

    def _r7_step(self, symbol, index, row, study, study_available):
        """Single observation step (used outside a tick): queue it and allocate now."""
        self._r7_queue(symbol, index, row, study, study_available)
        decisions = self._r7_flush_batch()
        return decisions[symbol]

    def _r7_queue(self, symbol, index, row, study, study_available):
        """Prepare one symbol's completed bar: features, AVR and reference advance once."""
        ts = str(row['timestamp'])
        if self._r7_batch and self._r7_batch_ts != ts:
            self._r7_flush_batch()
        if symbol in self._r7_batch:
            raise RuntimeError(f'R7 symbol observed twice at one timestamp: {symbol}')
        bar = Bar(index, ts, *[float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')])
        ctrl = self.r7_symbol_controllers[symbol]
        if ctrl.last_input is not None and bar.index <= ctrl.last_input[0].index:
            return None
        grid = self._governor_grid_return(row['timestamp'])
        available = grid is not None
        # Signed NIFTY market direction (not capacity), normalized and bounded.
        dispatch = clamp(float(grid) / self.r7_dispatch_scale) if available else 0.0
        context = Context(self._r7_actual(symbol, bar.close), clamp(study), dispatch,
                          study_available=study_available, dispatch_available=available)
        reference, eligible = ctrl.prepare(bar, context)
        self._r7_batch[symbol] = {'bar': bar, 'context': context, 'reference': reference, 'eligible': eligible,
                                  'grid': grid, 'study_available': study_available}
        self._r7_batch_ts = ts
        return reference

    def _r7_symbol_cap_notional(self, symbol, actual, equity):
        """Native per-symbol exposure cap, expressed as remaining same-side notional."""
        values = getattr(getattr(self, 'safety_contract', None), 'values', {}) or {}
        frac = values.get('max_exposure_per_symbol_fraction')
        if frac is None:
            return float('inf')
        held = abs(number(actual)) * equity * self.r7_configs[symbol].budget_fraction
        return max(0.0, number(float(frac)) * equity - held)

    def _r7_flush_batch(self):
        """Allocate headroom across every queued symbol at once, then commit each once."""
        batch, ts = self._r7_batch, self._r7_batch_ts
        self._r7_batch, self._r7_batch_ts = {}, None
        if not batch:
            return {}
        util = self.r7_utilization(ts)
        equity = number(float(self._equity()))
        demands, blocked = {}, {}
        for symbol in sorted(batch):
            item = batch[symbol]
            blocked[symbol] = self._r7_execution_blocked(symbol)
            pu = increase_demand_pu(item['reference'], item['context'].actual_exposure)
            if not item['eligible'] or blocked[symbol] or util is None:
                pu = 0.0
            notional = pu * equity * self.r7_configs[symbol].budget_fraction
            demands[symbol] = min(notional, self._r7_symbol_cap_notional(symbol, item['context'].actual_exposure, equity))
        headroom = 0.0 if util is None else util.headroom_notional
        grants, case = allocate_headroom(demands, self.r7_weights, headroom)
        self.r7_gate_counts[f'ALLOCATION_{case}'] += 1
        if util is None:
            self.r7_gate_counts['ALLOCATION_FAIL_CLOSED'] += 1
        out = {}
        for symbol in sorted(batch):
            item = batch[symbol]
            ctrl = self.r7_symbol_controllers[symbol]
            base = equity * self.r7_configs[symbol].budget_fraction
            capacity = 0.0 if util is None else grants[symbol] / base
            decision = ctrl.commit(capacity, blocked[symbol])
            out[symbol] = decision
            self._r7_record(decision, item, util, case, demands[symbol], grants[symbol], capacity, blocked[symbol])
        for symbol, args in sorted(self._r7_deferred_exits.items()):
            self._r7_arm_normal_exit(symbol, *args)
        self._r7_deferred_exits = {}
        return out

    def _r7_record(self, decision, item, util, case, demand, grant, capacity, blocked):
        context, grid = item['context'], item['grid']
        self.r7_gate_counts['BARS_PROCESSED'] += 1
        fraction = self.r7_configs[decision.symbol].budget_fraction
        # Aggregate portfolio fractions, not incomparable normalized symbol units.
        weighted = type(decision)(**{**vars(decision), 'target': decision.target * fraction,
                                     'actual': decision.actual * fraction})
        self.r7_bay_state[decision.bay_id].update(weighted)
        record = asdict(decision)
        record['allocation'] = {'utilization': None if util is None else util.actual,
                                'projected_utilization': None if util is None else util.projected,
                                'headroom_notional': 0.0 if util is None else util.headroom_notional,
                                'measurement_error': self._r7_utilization_error,
                                'case': case, 'weight': self.r7_weights[decision.symbol],
                                'demand_notional': demand, 'granted_notional': grant,
                                'rejected_demand_notional': max(0.0, demand - grant),
                                'increase_capacity_pu': capacity, 'execution_blocked': blocked}
        # Causal inputs as consumed this bar, for alignment audits. 'dispatch' is kept for
        # serialization compatibility; it is the signed grid direction signal.
        record['inputs'] = {'study': context.study, 'dispatch': context.dispatch,
                            'grid_direction_signal': context.dispatch,
                            'grid_deviation': None if grid is None else float(grid),
                            'study_available': item['study_available'],
                            'dispatch_available': context.dispatch_available}
        self._r7_emit_decision(record)
        self._record_controller_event('R7_COMPLETED_BAR', decision.timestamp, decision.symbol, record)

    def _r7_emit_decision(self, record):
        line = json.dumps(record, sort_keys=True, allow_nan=False, default=str)
        self.r7_decision_digest = sha256((self.r7_decision_digest + line).encode()).hexdigest()
        self.r7_decision_count += 1
        self.r7_decisions.append(record)
        if self.r7_decision_log_path is not None:
            if self._r7_decision_log is None:
                self._r7_decision_log = open(self.r7_decision_log_path, 'a', encoding='utf-8')
            self._r7_decision_log.write(line + '\n')
            self._r7_decision_log.flush()

    def r7_close_decision_log(self):
        if self._r7_decision_log is not None:
            self._r7_decision_log.close()
            self._r7_decision_log = None

    def _observe_conviction(self, symbol, signal, composite_result):
        super()._observe_conviction(symbol, signal, composite_result)
        observed = self._r7_observed.pop(symbol, None)
        if observed is None:
            raise RuntimeError('R7 studies/bar handoff missing')
        index, row, result = observed
        if result != composite_result:
            raise RuntimeError('R7 studies handoff mismatch')
        # Use the signed studies result, not candidate conviction or candidate side.
        direction, confidence = float(result['direction']), float(result['confidence'])
        if direction not in (-1, 0, 1) or not 0 <= confidence <= 1:
            raise ValueError('invalid signed study observation')
        self._r7_queue(symbol, index, row, direction * confidence, True)

    def r7_flush(self):
        """Allocate and commit any queued observations (e.g. the last warmup bar)."""
        return self._r7_flush_batch()

    def _process_tick(self, timestamp, tick_events):
        events = list(tick_events)
        # Warmup observations arrive symbol by symbol outside the tick clock; settle any
        # leftover batch before this timestamp's observations are gathered.
        self._r7_flush_batch()
        self._r7_tick_events = events
        try:
            result = super()._process_tick(timestamp, events)
        finally:
            self._r7_tick_events = None
        # Normally the batch is allocated at the pre-admission hook; this covers a
        # native core that skips it, and symbols rejected before studies.
        self._r7_complete_tick(events)
        return result

    def _r7_complete_tick(self, events):
        for event in events:
            ctrl = self.r7_symbol_controllers[event.symbol]
            done = ctrl.last_result is not None and ctrl.last_result.bar_index >= event.bar_idx
            if not done and event.symbol not in self._r7_batch:
                # Symbols rejected before studies still receive a degraded observation.
                row = self._run.symbol_bars[event.symbol].iloc[event.bar_idx]
                self._r7_queue(event.symbol, event.bar_idx, row, 0.0, False)
        self._r7_flush_batch()

    def rank_simultaneous_entry_candidates(self, candidates):
        """Native S4 arbitration point: every symbol at this timestamp has been observed
        and exited; allocate R7 capacity once, before any candidate is admitted."""
        if self._r7_tick_events is not None:
            self._r7_complete_tick(self._r7_tick_events)
        return super().rank_simultaneous_entry_candidates(candidates)

    def _plant_entry_gate(self, symbol, timestamp, side, quantity, price, bar, candidate_id, governor_entry):
        self.r7_gate_counts['CANDIDATES_RECEIVED'] += 1
        ctrl = self.r7_symbol_controllers[symbol]
        pulse = ctrl.last_result
        expected = 'BUY' if side == 'BUY' else 'SELL' if side == 'SELL' else None
        if expected is None:
            raise ValueError('invalid order side')
        if side == 'SELL' and not self.r7_allow_native_shorts:
            self.r7_gate_counts['REJECTED_BY_SHORT_POLICY'] += 1
            return 0
        if pulse is None or pulse.bar_index != self._current_bar_idx or pulse.timestamp != str(timestamp) or not pulse.eligible or pulse.pulse != expected:
            self.r7_gate_counts['REJECTED_BY_R7_PULSE'] += 1
            return 0
        self.r7_gate_counts['ADMITTED_BY_R7_PULSE'] += 1
        native = int(super()._plant_entry_gate(symbol, timestamp, side, quantity, price, bar,
                                              candidate_id, governor_entry))
        self.r7_gate_counts['ADMITTED_BY_NATIVE_PLANT_GATE' if native > 0 else 'REJECTED_BY_NATIVE_PLANT_GATE'] += 1
        return max(0, min(int(quantity), native))

    def _admit_entry_candidate(self, pending, timestamp):
        # The native core only invokes _plant_entry_gate when a plant is wired.
        # Enforce the R7 pulse at the candidate seam as well, so a core engine
        # without a plant can never bypass R7 admission.
        self.r7_gate_counts['UPSTREAM_CANDIDATES_RECEIVED'] += 1
        symbol = pending['symbol']
        decision = self.r7_symbol_controllers[symbol].last_result
        side = pending['plan'].side
        if side not in ('BUY', 'SELL'):
            raise ValueError('invalid candidate side')
        allowed = (decision is not None and decision.bar_index == pending['bar_idx']
            and decision.timestamp == str(timestamp) and decision.eligible and decision.pulse == side
            and (side != 'SELL' or self.r7_allow_native_shorts))
        if not allowed:
            self.r7_gate_counts['REJECTED_BEFORE_NATIVE_ADMISSION'] += 1
            self.entry_candidate_observations.dispose(pending['candidate_id'], 'REJECTED', 'r7_pulse_rejected')
            return None
        self.r7_gate_counts['ADMITTED_TO_NATIVE_CANDIDATE_PATH'] += 1
        return super()._admit_entry_candidate(pending, timestamp)

    def r7_physical_governor_step(self, bay_id, *, dt_s, p_ref_pu, p_electrical_pu):
        """Explicit physical inputs only: true R6 droop, turbine lag and rotor swing."""
        return self.r7_physical_units[bay_id].step(dt_s=dt_s, p_ref_pu=p_ref_pu,
                                                 p_electrical_pu=p_electrical_pu)

    # Operator decision (2026-10-17): only native exits individually proven discretionary
    # may be deferred to R7. GOVERNOR_Z_EXIT is the mean-reversion release that the native
    # authority itself documents as discretionary (revision5/governor_authority.py: it ranks
    # below every hard, protective and Mark V limiter and is deferred inside minimum hold).
    # Everything else, including FSRN/FSRT limiter exits, stressed-regime, saturation,
    # governor path-error, controller-path and final-controller exits, and any unknown
    # reason, keeps native execution authority.
    R7_DISCRETIONARY_NATIVE_EXITS = frozenset({'governor_exit:GOVERNOR_Z_EXIT'})

    @classmethod
    def _r7_normal_reason(cls, reason):
        """Exact-match allowlist of discretionary native exits; no prefix matching."""
        return reason in cls.R7_DISCRETIONARY_NATIVE_EXITS

    def _execute_exit(self, symbol, timestamp, trade, exit_price, reason):
        if self._r7_exit_scope and self._r7_normal_reason(str(reason)):
            self.r7_gate_counts['NATIVE_NORMAL_EXIT_ADVISORY'] += 1
            self._record_controller_event('R7_NATIVE_EXIT_ADVISORY', timestamp, symbol,
                                          {'native_reason': reason})
            return None
        result = super()._execute_exit(symbol, timestamp, trade, exit_price, reason)
        if self.open_trades.get(symbol) is not trade:
            self.r7_normal_exit_state.pop(symbol, None)
        return result

    def _r7_remove_normal_pending(self, trade):
        pending = trade.get('controller_exit_pending')
        # Only a pending native exit with an allowlisted discretionary reason may be
        # withdrawn; protective and unknown pending exits keep native authority.
        if pending and self._r7_normal_reason(str(pending.get('exit_reason', ''))):
            trade.pop('controller_exit_pending')

    def _maybe_exit(self, symbol, timestamp, bar, signal, held_bars, session_last_bar,
                    chart_studies_confidence, chart_studies_audit=None):
        trade = self.open_trades.get(symbol)
        if trade is None:
            self.r7_normal_exit_state.pop(symbol, None)
            return None
        self._r7_remove_normal_pending(trade)
        same_bar_pending = trade.get('controller_exit_pending')
        if (same_bar_pending and same_bar_pending.get('exit_reason') == 'r7_coordinated_exit'
                and same_bar_pending.get('armed_timestamp') == str(timestamp)):
            trade.pop('controller_exit_pending')
        else:
            same_bar_pending = None
        # Let the existing engine process all protective/hard/time exits first.
        self._r7_exit_scope = True
        try:
            result = super()._maybe_exit(symbol, timestamp, bar, signal, held_bars,
                session_last_bar, chart_studies_confidence, chart_studies_audit)
        finally:
            self._r7_exit_scope = False
        if self.open_trades.get(symbol) is not trade:
            self.r7_normal_exit_state.pop(symbol, None)
            return result
        self._r7_remove_normal_pending(trade)
        if same_bar_pending and not trade.get('controller_exit_pending'):
            trade['controller_exit_pending'] = same_bar_pending
        # Existing protective pending exits and venue working exits take precedence.
        if trade.get('controller_exit_pending') or symbol in getattr(self, '_symbol_inflight', {}) or symbol in getattr(self, '_working_exits', {}):
            return result
        bar_index = self._current_bar_idx
        decision = self.r7_symbol_controllers[symbol].last_result
        if symbol in self._r7_batch or (decision is not None and decision.timestamp != str(timestamp)
                                        and self._r7_batch_ts == str(timestamp)):
            # The batch for this timestamp is not allocated yet: arm after commit.
            self._r7_deferred_exits[symbol] = (timestamp, held_bars, bar_index)
            return result
        self._r7_arm_normal_exit(symbol, timestamp, held_bars, bar_index)
        return result

    def _r7_arm_normal_exit(self, symbol, timestamp, held_bars, bar_index):
        trade = self.open_trades.get(symbol)
        if trade is None:
            self.r7_normal_exit_state.pop(symbol, None)
            return
        # Existing protective pending exits and venue working exits take precedence.
        if trade.get('controller_exit_pending') or symbol in getattr(self, '_symbol_inflight', {}) or symbol in getattr(self, '_working_exits', {}):
            return
        decision = self.r7_symbol_controllers[symbol].last_result
        c = self.r7_configs[symbol]
        if (decision is None or decision.timestamp != str(timestamp) or
                decision.bar_index != bar_index):
            return
        identity = str(trade.get('trade_id', trade.get('candidate_id', '')))
        state = self.r7_normal_exit_state.get(symbol)
        if state is None or state['position_id'] != identity:
            state = {'position_id': identity, 'last_bar': -1, 'count': 0}
        if state['last_bar'] == decision.bar_index:
            return
        same_direction = 'BUY' if trade['side'] == 'BUY' else 'SELL'
        opposite = 'SELL' if same_direction == 'BUY' else 'BUY'
        neutral = (decision.pulse == 'HOLD' and abs(decision.signal) <= c.buy_release
                   and abs(decision.target) <= c.buy_release)
        request_exit = decision.eligible and (decision.pulse == opposite or neutral)
        consecutive = decision.bar_index == state['last_bar'] + 1
        state['count'] = (state['count'] + 1 if consecutive else 1) if request_exit else 0
        state['last_bar'] = decision.bar_index
        self.r7_normal_exit_state[symbol] = state
        self._record_controller_event('R7_POSITION_DECISION', timestamp, symbol,
            {'position_id': identity, 'pulse': decision.pulse, 'action': 'EXIT_REQUEST' if request_exit else 'HOLD',
             'confirmations': state['count'], 'held_bars': held_bars})
        if state['count'] >= c.normal_exit_confirm_bars and held_bars >= int(trade['minimum_hold_bars']):
            # This signal is known at this close, so the order uses the next open.
            trade['controller_exit_pending'] = {
                'armed_timestamp': str(timestamp), 'armed_bar': decision.bar_index,
                'reason': 'R7_OPPOSITE_PULSE' if decision.pulse == opposite else 'R7_NEUTRAL_RELEASE',
                'exit_reason': 'r7_coordinated_exit', 'event': 'R7_COORDINATED_EXIT',
                'pulse': decision.pulse, 'position_id': identity}
            self.r7_gate_counts['NORMAL_EXITS_ARMED'] += 1
            self._record_controller_event('R7_NORMAL_EXIT_ARMED', timestamp, symbol,
                                          dict(trade['controller_exit_pending']))

    def r7_snapshot(self):
        if self._r7_observed or self._r7_batch or self._r7_deferred_exits:
            raise RuntimeError('snapshot requires a completed control handoff')
        return deepcopy({'schema': 2, 'adaptation': 'off', 'dispatch_scale': self.r7_dispatch_scale,
            'allow_shorts': self.r7_allow_native_shorts, 'topology': self.r7_topology_report,
            'controllers': {s: c.snapshot() for s, c in self.r7_symbol_controllers.items()},
            'physical': {b: x.snapshot() for b, x in self.r7_physical_units.items()},
            'bay_state': {b: x.symbols for b, x in self.r7_bay_state.items()},
            'normal_exit_state': self.r7_normal_exit_state,
            'counts': dict(self.r7_gate_counts),
            'decision_count': self.r7_decision_count, 'decision_digest': self.r7_decision_digest})

    def r7_restore(self, payload):
        """Restore R7 control state only, alongside a separately restored native ledger."""
        json.dumps(payload, allow_nan=False)
        if (payload['schema'], payload['adaptation'], payload['dispatch_scale'], payload['allow_shorts'], payload['topology']) != (2, 'off', self.r7_dispatch_scale, self.r7_allow_native_shorts, self.r7_topology_report):
            raise ValueError('R7 checkpoint identity/configuration mismatch')
        if set(payload['controllers']) != set(self.r7_symbol_controllers) or set(payload['physical']) != set(BAY_IDS) or set(payload['bay_state']) != set(BAY_IDS):
            raise ValueError('R7 checkpoint universe mismatch')
        new = {s: SymbolController(c) for s, c in self.r7_configs.items()}
        for s, controller in new.items():
            controller.restore(payload['controllers'][s])
        physical = {b: TurbineGovernorUnit(c, p_mech_pu=c.power_min_pu) for b, c in repository_defaults().items()}
        for b, unit in physical.items():
            unit.restore(payload['physical'][b])
        bays = {b: BayAggregateState() for b in BAY_IDS}
        for s, ctrl in new.items():
            if ctrl.last_result is not None:
                d = ctrl.last_result
                f = self.r7_configs[s].budget_fraction
                bays[d.bay_id].update(type(d)(**{**vars(d), 'target': d.target * f, 'actual': d.actual * f}))
        if {b: x.symbols for b, x in bays.items()} != payload['bay_state']:
            raise ValueError('R7 aggregate state mismatch')
        if any(type(v) is not int or v < 0 for v in payload['counts'].values()):
            raise ValueError('invalid checkpoint counters')
        for s, state in payload['normal_exit_state'].items():
            if s not in new or set(state) != {'position_id', 'last_bar', 'count'} or type(state['count']) is not int or state['count'] < 0 or type(state['last_bar']) is not int or state['last_bar'] < 0:
                raise ValueError('invalid normal-exit checkpoint')
        # Validate every remaining field before the first assignment so a rejected payload
        # leaves the engine untouched.
        if type(payload['decision_count']) is not int or payload['decision_count'] < 0 or not (
                isinstance(payload['decision_digest'], str) and len(payload['decision_digest']) == 64):
            raise ValueError('invalid decision telemetry checkpoint')
        self.r7_symbol_controllers, self.r7_physical_units, self.r7_bay_state = new, physical, bays
        self.r7_gate_counts = Counter(payload['counts'])
        self.r7_decisions.clear()
        self.r7_decision_count, self.r7_decision_digest = payload['decision_count'], payload['decision_digest']
        self.r7_normal_exit_state = deepcopy(payload['normal_exit_state'])


def engine_class(base=None):
    """Attach to the existing shared engine; callers may supply a compatible native subclass."""
    if base is None:
        from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
        base = Revision2ExternalEngineOrchestrator
    return type('Revision7Engine', (R7EngineMixin, base), {'__module__': __name__})


def r7_classes():
    """Return R7-enabled Engine B and Engine C classes for the native runners.

    The factory keeps the integration process-local: callers explicitly replace the
    runner's orchestrator class for an offline run, while the native classes and live
    order paths remain unchanged on disk.
    """
    from engine_b.orchestrator import EngineBOrchestrator
    from engine_c.orchestrator import EngineCOrchestrator

    class R7EngineBOrchestrator(R7EngineMixin, EngineBOrchestrator):
        r7_variant = 'B'

    class R7EngineCOrchestrator(R7EngineMixin, EngineCOrchestrator):
        r7_variant = 'C'

    return R7EngineBOrchestrator, R7EngineCOrchestrator


# Lazy construction avoids importing native analytics during isolated component tests.
def Revision7Engine(*args, **kwargs):
    return engine_class()(*args, **kwargs)
