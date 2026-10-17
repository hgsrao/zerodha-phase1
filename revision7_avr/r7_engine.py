"""R7 engine adapter over the existing shared R5/R6 native engine framework.

The default class is the engine, not a harness. No live client is constructed.
The native entry/exit, safety, sizing and order/position ledger remain authoritative.
"""
from collections import Counter
from dataclasses import asdict
from copy import deepcopy
import json

from revision5.topology import SYMBOL_TO_BAY, BAY_IDS
from revision6_avr.r6_turbine_governor import repository_defaults, TurbineGovernorUnit
from .r7_state import Bar, Context, ControlConfig, BayAggregateState, number, clamp
from .r7_coordinated_controller import SymbolController
from .r7_allocator import Utilization, droop_shares, increase_capacity_pu


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
                 r7_allow_native_shorts=True, **kwargs):
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
        # Fixed per run: shares of portfolio headroom proportional to budget / R.
        self.r7_headroom_shares = droop_shares(configs.values())
        self._r7_utilization_ts = None
        self._r7_utilization = None
        self.r7_bay_state = {b: BayAggregateState() for b in BAY_IDS}
        self.r7_physical_units = {b: TurbineGovernorUnit(c, p_mech_pu=c.power_min_pu)
                                  for b, c in repository_defaults().items()}
        self.r7_gate_counts = Counter()
        self.r7_decisions = []
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
                remaining = max(0.0, float(record['requested']) - float(record.get('filled', 0)))
                pending += remaining * number(float(self._last_close.get(record['symbol'], 0.0)))
        return pending

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
            self._r7_utilization = Utilization(self._r7_filled_gross_notional(),
                                               self._r7_pending_increase_notional(),
                                               self._r7_gross_limit_notional())
            self._r7_utilization_ts = key
        return self._r7_utilization

    def _r7_execution_blocked(self, symbol):
        return (symbol in getattr(self, '_symbol_inflight', {}) or symbol in getattr(self, '_symbol_working', {})
                or bool(getattr(self, '_execution_halted', False)))

    def _r7_step(self, symbol, index, row, study, study_available):
        bar = Bar(index, str(row['timestamp']), *[float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')])
        grid = self._governor_grid_return(row['timestamp'])
        available = grid is not None
        dispatch = clamp(float(grid) / self.r7_dispatch_scale) if available else 0.0
        util = self.r7_utilization(row['timestamp'])
        capacity = increase_capacity_pu(self.r7_headroom_shares[symbol], util, self._equity(),
                                        self.r7_configs[symbol].budget_fraction)
        context = Context(self._r7_actual(symbol, bar.close), clamp(study), dispatch,
                          study_available=study_available, dispatch_available=available,
                          increase_capacity=capacity, execution_blocked=self._r7_execution_blocked(symbol))
        ctrl = self.r7_symbol_controllers[symbol]
        before = ctrl.steps
        decision = ctrl.step(bar, context)
        if ctrl.steps != before:
            self.r7_gate_counts['BARS_PROCESSED'] += 1
            fraction = self.r7_configs[symbol].budget_fraction
            # Aggregate portfolio fractions, not incomparable normalized symbol units.
            weighted = type(decision)(**{**vars(decision), 'target': decision.target * fraction,
                                         'actual': decision.actual * fraction})
            self.r7_bay_state[decision.bay_id].update(weighted)
            record = asdict(decision)
            record['allocation'] = {'utilization': util.actual, 'projected_utilization': util.projected,
                                    'headroom_notional': util.headroom_notional,
                                    'share': self.r7_headroom_shares[symbol], 'increase_capacity_pu': capacity,
                                    'execution_blocked': context.execution_blocked}
            self.r7_decisions.append(record)
            self._record_controller_event('R7_COMPLETED_BAR', bar.timestamp, symbol, record)
        return decision

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
        self._r7_step(symbol, index, row, direction * confidence, True)

    def _process_tick(self, timestamp, tick_events):
        events = list(tick_events)
        result = super()._process_tick(timestamp, events)
        # Symbols rejected before studies still receive a degraded observation.
        for event in events:
            ctrl = self.r7_symbol_controllers[event.symbol]
            if ctrl.last_result is None or ctrl.last_result.bar_index < event.bar_idx:
                row = self._run.symbol_bars[event.symbol].iloc[event.bar_idx]
                self._r7_step(event.symbol, event.bar_idx, row, 0.0, False)
        return result

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
        decision = self.r7_symbol_controllers[symbol].last_result
        c = self.r7_configs[symbol]
        if (decision is None or decision.timestamp != str(timestamp) or
                decision.bar_index != self._current_bar_idx):
            return result
        identity = str(trade.get('trade_id', trade.get('candidate_id', '')))
        state = self.r7_normal_exit_state.get(symbol)
        if state is None or state['position_id'] != identity:
            state = {'position_id': identity, 'last_bar': -1, 'count': 0}
        if state['last_bar'] == decision.bar_index:
            return result
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
        return result

    def r7_snapshot(self):
        if self._r7_observed:
            raise RuntimeError('snapshot requires a completed control handoff')
        return deepcopy({'schema': 2, 'adaptation': 'off', 'dispatch_scale': self.r7_dispatch_scale,
            'allow_shorts': self.r7_allow_native_shorts, 'topology': self.r7_topology_report,
            'controllers': {s: c.snapshot() for s, c in self.r7_symbol_controllers.items()},
            'physical': {b: x.snapshot() for b, x in self.r7_physical_units.items()},
            'bay_state': {b: x.symbols for b, x in self.r7_bay_state.items()},
            'normal_exit_state': self.r7_normal_exit_state,
            'counts': dict(self.r7_gate_counts), 'decisions': self.r7_decisions})

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
        self.r7_symbol_controllers, self.r7_physical_units, self.r7_bay_state = new, physical, bays
        self.r7_gate_counts, self.r7_decisions = Counter(payload['counts']), deepcopy(payload['decisions'])
        self.r7_normal_exit_state = deepcopy(payload['normal_exit_state'])


def engine_class(base=None):
    """Attach to the existing shared engine; callers may supply a compatible native subclass."""
    if base is None:
        from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
        base = Revision2ExternalEngineOrchestrator
    return type('Revision7Engine', (R7EngineMixin, base), {'__module__': __name__})


# Lazy construction avoids importing native analytics during isolated component tests.
def Revision7Engine(*args, **kwargs):
    return engine_class()(*args, **kwargs)
