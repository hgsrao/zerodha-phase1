"""R8 engine adapter over the existing shared R5/R6 native engine framework.

The default class is the engine, not a harness. No live client is constructed.
The native entry/exit, safety, sizing and order/position ledger remain authoritative.
"""
from collections import Counter, deque
from dataclasses import asdict, replace
from copy import deepcopy
import json
from hashlib import sha256
import math

from .r8_topology import SYMBOL_TO_BAY, BAY_IDS
from .r8_turbine_governor import repository_defaults, TurbineGovernorUnit
from .r8_state import Bar, Context, ControlConfig, BayAggregateState, number, clamp
from .r8_coordinated_controller import SymbolController
from .r8_allocator import Utilization, droop_weights, increase_demand_pu, allocate_headroom
from .r8_controller_parameters import R5ControllerParameters
from .r8_dynamic_parameters import R5EnvironmentState
from .r8_adaptation import R8_ADAPTATION_MODES, STRATEGY_TIGHTER, R8Adaptation

R8_DECISION_MEMORY_LIMIT = 1000


def r8_parameter_fingerprint(parameters):
    """SHA-256 of the complete controller-parameter image: every registry value (governor specs,
    schedules, AVR coefficients, protection, dispatcher, HRSG, profile schedules), the profile bases
    and the position-control V3 values.  Works on the host's image and on R8's copy alike."""
    payload = {'values': dict(parameters.values), 'profile_bases': dict(parameters.profile_bases),
               'v3_values': dict(parameters.v3_values)}
    return sha256(json.dumps(payload, sort_keys=True, allow_nan=False, default=str).encode()).hexdigest()


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
        self.owner._r8_observed[symbol] = (index, row.copy(), deepcopy(result))
        return result


class R8EngineMixin:
    def __init__(self, *args, r8_configs, r8_dispatch_scale=0.01,
                 r8_allow_native_shorts=True, r8_decision_log_path=None,
                 r8_decision_memory=5000, r8_adaptation_mode='off', r8_adaptation_log_path=None,
                 **kwargs):
        if r8_adaptation_mode not in R8_ADAPTATION_MODES:
            raise ValueError(f'r8_adaptation_mode must be one of {R8_ADAPTATION_MODES} (there is no live mode)')
        # Configs are explicit: selecting one symbol must not reallocate its entire bay.
        configs = {c.symbol: c for c in r8_configs}
        if not r8_allow_native_shorts:
            # Shorts disabled: a four-way BEAR while flat is HOLD at the source, not only at the gate.
            configs = {s: replace(c, allow_shorts=False) for s, c in configs.items()}
        if len(configs) != len(r8_configs) or not configs or sum(c.budget_fraction for c in configs.values()) > 1 + 1e-12:
            raise ValueError('invalid or duplicated symbol allocations')
        for symbol, c in configs.items():
            if SYMBOL_TO_BAY.get(symbol) != c.bay_id:
                raise ValueError(f'unmapped/mismatched R8 symbol: {symbol}')
        self.r8_dispatch_scale = number(r8_dispatch_scale)
        if self.r8_dispatch_scale <= 0 or type(r8_allow_native_shorts) is not bool:
            raise ValueError('invalid dispatch scale or short policy')
        self.r8_allow_native_shorts = r8_allow_native_shorts
        self.r8_configs = configs
        self.r8_symbol_controllers = {s: SymbolController(c) for s, c in configs.items()}
        # Commissioned sharing weights w_i = B_i / R_i, fixed for the run.
        self.r8_weights = droop_weights(configs.values())
        # Timestamp batch: every symbol at a market timestamp is allocated against one
        # common portfolio state before any governor or FSM state is committed.
        self._r8_batch = {}
        self._r8_batch_ts = None
        self._r8_tick_events = None
        self._r8_deferred_exits = {}
        self._r8_utilization_ts = None
        self._r8_utilization = None
        self._r8_utilization_error = None
        self.r8_bay_state = {b: BayAggregateState() for b in BAY_IDS}
        self.r8_physical_units = {b: TurbineGovernorUnit(c, p_mech_pu=c.power_min_pu)
                                  for b, c in repository_defaults().items()}
        self.r8_gate_counts = Counter()
        # Complete decision telemetry goes to an external append-only JSONL log.
        # Memory keeps only a bounded recent window; checkpoints hold a count and a
        # rolling SHA-256 chain, so their size does not grow with replay length.
        if type(r8_decision_memory) is not int or r8_decision_memory < 1:
            raise ValueError('decision memory must be a positive integer')
        # Keep the in-memory audit window bounded even if a caller requests a larger
        # value. The complete decision stream remains in the append-only log and is
        # represented in checkpoints by count plus rolling digest.
        self.r8_decisions = deque(maxlen=min(r8_decision_memory, R8_DECISION_MEMORY_LIMIT))
        self.r8_decision_log_path = r8_decision_log_path
        self._r8_decision_log = None
        self.r8_decision_count = 0
        self.r8_decision_digest = '0' * 64
        self._r8_observed = {}
        self._r8_current_timestamp = None
        self._r8_exit_scope = False
        self.r8_normal_exit_state = {}
        self.r8_adaptation_mode = r8_adaptation_mode
        self.r8_adaptation_enabled = r8_adaptation_mode in ('shadow', 'paper')   # evaluates and logs
        self.r8_adaptation_applies = r8_adaptation_mode == 'paper'                # installs tighten-only limits
        super().__init__(*args, **kwargs)
        if set(self.symbols) != set(configs):
            raise ValueError('R8 configs must exactly match requested universe')
        # R8 owns adaptation: the host engine's own profile consumption stays off so a profile can
        # never be applied twice or by a path R8 does not log and checkpoint.
        if getattr(self, 'full_adaptation', False) or getattr(self, 'dynamic_profile_mode', 'off') != 'off':
            raise ValueError('R8 owns adaptation: use r8_adaptation_mode, host dynamic_profile_mode must stay off')
        if r8_adaptation_mode != 'off' and not getattr(self, '_plant_wired', False):
            raise ValueError('r8_adaptation_mode shadow/paper requires the PAPER_APPLY plant')
        # R8's own registry image, read from the run's EffectiveConfig exactly as the host reads its own.
        if hasattr(self, 'config') and hasattr(self, 'safety_contract'):
            self.r8_parameters = R5ControllerParameters.from_config(
                self.config, safety_bases=dict(self.safety_contract.values))
        else:                                    # minimal test hosts; off mode never evaluates
            self.r8_parameters = R5ControllerParameters.defaults()
        self.r8_parameter_fingerprint = r8_parameter_fingerprint(self.r8_parameters)
        host_parameters = getattr(self, 'controller_parameters', None)
        if host_parameters is not None and r8_parameter_fingerprint(host_parameters) != self.r8_parameter_fingerprint:
            raise ValueError('R8 parameter image differs from the host parameter image')
        self.r8_adaptation = R8Adaptation(r8_adaptation_mode, self.r8_parameters, r8_adaptation_log_path)
        self.chart_studies = _StudiesTap(self.chart_studies, self)
        self.r8_topology_report = {s: c.bay_id for s, c in configs.items()}

    def _r8_actual(self, symbol, close):
        equity = number(float(self._equity()))
        if equity <= 0:
            raise ValueError('R8 exposure requires positive equity')
        quantity = number(float(self._broker_position(symbol).get('quantity', 0)))
        # Keep excess exposure visible rather than clamping it away.
        return quantity * close / (equity * self.r8_configs[symbol].budget_fraction)

    def _r8_filled_gross_notional(self):
        """Gross notional of FILLED native positions only (native base definition)."""
        from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Core
        if isinstance(self, Core):
            return number(float(Core._gross_exposure_notional(self)))
        return number(float(self._gross_exposure_notional()))

    def _r8_pending_increase_notional(self):
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
                    pending += requested * self._r8_pending_price(record)
        return pending

    def _r8_pending_price(self, record):
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

    def _r8_gross_limit_notional(self):
        run = getattr(self, '_run', None)
        fraction = getattr(run, 'max_gross_fraction', None)
        if fraction is None:
            fraction = float(self.safety_contract.values['max_gross_exposure_fraction'])
        equity = number(float(self._equity()))
        return equity * number(float(fraction))

    def r8_utilization(self, timestamp):
        """Common portfolio measurement, snapshotted once per clock timestamp so every
        symbol at this tick is allocated against the same headroom (order independent)."""
        key = str(timestamp)
        if self._r8_utilization_ts != key:
            try:
                self._r8_utilization = Utilization(self._r8_filled_gross_notional(),
                                                   self._r8_pending_increase_notional(),
                                                   self._r8_gross_limit_notional())
                self._r8_utilization_error = None
            except (ValueError, KeyError, TypeError) as exc:
                # Fail closed: no exposure increase may be authorized without a valid
                # measurement. Reductions remain approved by the allocator.
                self._r8_utilization = None
                self._r8_utilization_error = f'{type(exc).__name__}: {exc}'
                self.r8_gate_counts['UTILIZATION_UNAVAILABLE_FAIL_CLOSED'] += 1
            self._r8_utilization_ts = key
        return self._r8_utilization

    def _r8_execution_blocked(self, symbol):
        return (symbol in getattr(self, '_symbol_inflight', {}) or symbol in getattr(self, '_symbol_working', {})
                or bool(getattr(self, '_execution_halted', False)))

    def _r8_step(self, symbol, index, row, study, study_available):
        """Single observation step (used outside a tick): queue it and allocate now."""
        self._r8_queue(symbol, index, row, study, study_available)
        decisions = self._r8_flush_batch()
        return decisions[symbol]

    def _r8_queue(self, symbol, index, row, study, study_available):
        """Prepare one symbol's completed bar: features, AVR and reference advance once."""
        ts = str(row['timestamp'])
        if self._r8_batch and self._r8_batch_ts != ts:
            self._r8_flush_batch()
        if symbol in self._r8_batch:
            raise RuntimeError(f'R8 symbol observed twice at one timestamp: {symbol}')
        bar = Bar(index, ts, *[float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')])
        ctrl = self.r8_symbol_controllers[symbol]
        if ctrl.last_input is not None and bar.index <= ctrl.last_input[0].index:
            return None
        grid = self._governor_grid_return(row['timestamp'])
        available = grid is not None
        # Signed NIFTY market direction (not capacity), normalized and bounded.
        dispatch = clamp(float(grid) / self.r8_dispatch_scale) if available else 0.0
        context = Context(self._r8_actual(symbol, bar.close), clamp(study), dispatch,
                          study_available=study_available, dispatch_available=available)
        reference, eligible = ctrl.prepare(bar, context)
        self._r8_batch[symbol] = {'bar': bar, 'context': context, 'reference': reference, 'eligible': eligible,
                                  'grid': grid, 'study_available': study_available}
        self._r8_batch_ts = ts
        return reference

    def _r8_symbol_cap_notional(self, symbol, actual, equity):
        """Native per-symbol exposure cap, expressed as remaining same-side notional."""
        values = getattr(getattr(self, 'safety_contract', None), 'values', {}) or {}
        frac = values.get('max_exposure_per_symbol_fraction')
        if frac is None:
            return float('inf')
        held = abs(number(actual)) * equity * self.r8_configs[symbol].budget_fraction
        return max(0.0, number(float(frac)) * equity - held)

    def _r8_flush_batch(self):
        """Allocate headroom across every queued symbol at once, then commit each once."""
        batch, ts = self._r8_batch, self._r8_batch_ts
        self._r8_batch, self._r8_batch_ts = {}, None
        if not batch:
            return {}
        util = self.r8_utilization(ts)
        equity = number(float(self._equity()))
        demands, blocked = {}, {}
        for symbol in sorted(batch):
            item = batch[symbol]
            blocked[symbol] = self._r8_execution_blocked(symbol)
            pu = increase_demand_pu(item['reference'], item['context'].actual_exposure)
            if not item['eligible'] or blocked[symbol] or util is None:
                pu = 0.0
            notional = pu * equity * self.r8_configs[symbol].budget_fraction
            demands[symbol] = min(notional, self._r8_symbol_cap_notional(symbol, item['context'].actual_exposure, equity))
        headroom = 0.0 if util is None else util.headroom_notional
        grants, case = allocate_headroom(demands, self.r8_weights, headroom)
        self.r8_gate_counts[f'ALLOCATION_{case}'] += 1
        if util is None:
            self.r8_gate_counts['ALLOCATION_FAIL_CLOSED'] += 1
        out = {}
        for symbol in sorted(batch):
            item = batch[symbol]
            ctrl = self.r8_symbol_controllers[symbol]
            base = equity * self.r8_configs[symbol].budget_fraction
            capacity = 0.0 if util is None else grants[symbol] / base
            decision = ctrl.commit(capacity, blocked[symbol])
            out[symbol] = decision
            self._r8_record(decision, item, util, case, demands[symbol], grants[symbol], capacity, blocked[symbol])
        for symbol, args in sorted(self._r8_deferred_exits.items()):
            self._r8_arm_normal_exit(symbol, *args)
        self._r8_deferred_exits = {}
        return out

    def _r8_record(self, decision, item, util, case, demand, grant, capacity, blocked):
        context, grid = item['context'], item['grid']
        self.r8_gate_counts['BARS_PROCESSED'] += 1
        fraction = self.r8_configs[decision.symbol].budget_fraction
        # Aggregate portfolio fractions, not incomparable normalized symbol units.
        weighted = type(decision)(**{**vars(decision), 'target': decision.target * fraction,
                                     'actual': decision.actual * fraction})
        self.r8_bay_state[decision.bay_id].update(weighted)
        record = asdict(decision)
        record['allocation'] = {'utilization': None if util is None else util.actual,
                                'projected_utilization': None if util is None else util.projected,
                                'headroom_notional': 0.0 if util is None else util.headroom_notional,
                                'measurement_error': self._r8_utilization_error,
                                'case': case, 'weight': self.r8_weights[decision.symbol],
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
        self._r8_emit_decision(record)
        self._record_controller_event('R8_COMPLETED_BAR', decision.timestamp, decision.symbol, record)

    def _r8_emit_decision(self, record):
        line = json.dumps(record, sort_keys=True, allow_nan=False, default=str)
        self.r8_decision_digest = sha256((self.r8_decision_digest + line).encode()).hexdigest()
        self.r8_decision_count += 1
        self.r8_decisions.append(record)
        if self.r8_decision_log_path is not None:
            if self._r8_decision_log is None:
                self._r8_decision_log = open(self.r8_decision_log_path, 'a', encoding='utf-8')
            self._r8_decision_log.write(line + '\n')
            self._r8_decision_log.flush()

    def r8_close_decision_log(self):
        if self._r8_decision_log is not None:
            self._r8_decision_log.close()
            self._r8_decision_log = None
        self.r8_adaptation.close()

    # ------------------------------------------------------------------
    # Runtime adaptation (R8 copy of the dynamic-parameter formulas)
    # ------------------------------------------------------------------
    def _r8_environment(self, tick_events):
        """Causal supervisory environment for this timestamp: volatility against each symbol's own
        EMA baseline (R8 keeps its own baseline), HMM stress posterior, trend share, fleet drawdown
        and mean governor error.  Same formula as the host plant's environment."""
        alpha = 2.0 / (self.governor_config.z_window_bars + 1.0)
        ratios, stress, trending, measured = [], [], 0, 0
        baselines = self.r8_adaptation.vol_baseline
        for event in tick_events:
            telemetry = self._governor_telemetry.get(event.symbol)
            if telemetry is None or not telemetry.available or not telemetry.atr_fraction:
                continue
            measured += 1
            fraction = float(telemetry.atr_fraction)
            baseline = baselines.get(event.symbol)
            if baseline is not None and baseline > 0.0:
                ratios.append(fraction / baseline)
            baselines[event.symbol] = fraction if baseline is None else baseline + alpha * (fraction - baseline)
            trending += int(abs(float(telemetry.z_score)) >= 1.0)
            observation = self.id_box.latest_regime_observation(event.symbol)
            if observation.get('available') and observation.get('stress_probability') is not None:
                stress.append(float(observation['stress_probability']))
        turbulent = min(1.0, max(0.0, sum(stress) / len(stress))) if stress else 1.0 / 3.0
        trend_share = trending / measured if measured else 0.5
        ratios.sort()
        governors = [bay.governor for bay in self.real_plant_dcs.bays.values()]
        return R5EnvironmentState(
            normalized_vol=ratios[len(ratios) // 2] if ratios else 1.0,
            trend_probability=(1.0 - turbulent) * trend_share,
            chop_probability=(1.0 - turbulent) * (1.0 - trend_share),
            turbulent_probability=turbulent,
            fleet_drawdown_fraction=self._current_drawdown(),
            control_error_delta=sum(g.last_error for g in governors) / len(governors),
        )

    def _plant_bar_step(self, timestamp, tick_events, symbol_bars):
        adaptation = self.r8_adaptation
        if not adaptation.evaluates or not self._plant_wired:
            return super()._plant_bar_step(timestamp, tick_events, symbol_bars)
        # Read the environment before the host steps: the inputs are the same ones the host reads.
        environment = self._r8_environment(tick_events)
        super()._plant_bar_step(timestamp, tick_events, symbol_bars)
        previous_image, previous_applied = adaptation.image, deepcopy(adaptation.applied)
        adaptation.evaluate(environment)
        parity = adaptation.check_parity(self.real_plant_dcs.dynamic_snapshot)
        if parity and adaptation.applies:
            adaptation.record(timestamp, previous_image, previous_applied, parity)
            raise RuntimeError(f'R8 dynamic formulas disagree with the host plant: {parity[:5]}')
        if adaptation.applies:
            profile = self._r8_profile_values(adaptation.snapshot)
            self._r8_install_profile(profile)
            adaptation.applied = profile
        adaptation.record(timestamp, previous_image, previous_applied, parity)
        self.r8_gate_counts['ADAPTATION_BARS'] += 1

    def _r8_profile_values(self, snapshot):
        """Pure: the tighten-only operating values for this snapshot.  Strategy values move only in
        the tighter direction from the run's own base (and stay inside registry bounds); safety and
        execution limits are min(contract, profile); the dispatch ceiling only falls."""
        strategy, safety, execution = snapshot.strategy, snapshot.safety, snapshot.execution
        overlay = {}
        for name, tighter, kind in STRATEGY_TIGHTER:
            spec = self.registry.params[name]
            value = min(max(float(getattr(strategy, name)), spec.minimum), spec.maximum)
            overlay[name] = kind(tighter(value, float(self.config.require(name))))
        c = self.safety_contract.values
        gate = self._base_gate_config
        tightened = {
            'drawdown_halt_threshold': min(float(c['safety_drawdown_halt_threshold']), float(safety.drawdown_halt_threshold)),
            'daily_loss_halt_threshold': float(c['max_daily_loss_rupees']) * min(1.0, float(safety.daily_loss_limit_multiplier)),
            'max_concurrent_positions': min(int(c['max_concurrent_positions']), int(safety.max_concurrent_positions)),
            'max_gross_exposure_fraction': min(float(c['max_gross_exposure_fraction']), float(safety.max_gross_exposure_fraction)),
            'max_exposure_per_symbol_fraction': min(float(c['max_exposure_per_symbol_fraction']),
                                                    float(safety.max_symbol_exposure_fraction)),
            'slippage_tolerance_percent': min(float(gate.slippage_tolerance_percent), float(execution.slippage_tolerance_fraction)),
            'max_market_data_age_seconds': min(int(gate.max_market_data_age_seconds), int(execution.max_market_data_age_seconds)),
            'order_timeout_seconds': min(int(gate.order_timeout_seconds), int(execution.order_timeout_seconds)),
        }
        merit = self.plant_control.dispatch_controller.merit_source
        ceiling = min(float(merit.max_ceiling), float(snapshot.plant.dispatcher_max_ceiling))
        floor = min(float(snapshot.plant.dispatcher_min_floor), ceiling)
        return {'version': snapshot.version, 'strategy': overlay, 'safety_and_execution': tightened,
                'dispatch_ceiling': ceiling, 'dispatch_floor': floor}

    def _r8_prepare_profile(self, profile):
        """Validate a profile and build everything installing it needs, without changing the engine."""
        if not isinstance(profile, dict) or set(profile) != {'version', 'strategy', 'safety_and_execution',
                                                             'dispatch_ceiling', 'dispatch_floor'}:
            raise ValueError('invalid R8 profile schema')
        if set(profile['strategy']) != {name for name, _, _ in STRATEGY_TIGHTER}:
            raise ValueError('invalid R8 strategy profile')
        key = tuple(sorted(profile['strategy'].items()))
        decision_config = None
        if key != self._decision_config_key:
            values = self.config.as_dict()
            values.update(profile['strategy'])
            decision_config = type(self.config).build(values, registry_hash=self.registry.FROZEN_IDENTITY_SHA256)
        gate_config = replace(self._base_gate_config, **profile['safety_and_execution'])
        entry_engine = None
        if gate_config != self.entry_decision_engine.config:
            entry_engine = type(self.entry_decision_engine)(config=gate_config, logger=self.entry_decision_engine.logger)
        return {'profile': deepcopy(profile), 'key': key, 'decision_config': decision_config, 'entry_engine': entry_engine}

    def _r8_commit_profile(self, prepared):
        """Assign a prepared profile to the host consumers (plain assignments; cannot fail midway)."""
        profile = prepared['profile']
        if prepared['decision_config'] is not None:
            self._decision_config, self._decision_config_key = prepared['decision_config'], prepared['key']
        if prepared['entry_engine'] is not None:
            self.entry_decision_engine = prepared['entry_engine']
        if getattr(self, '_run', None) is not None:
            self._run.max_concurrent = profile['safety_and_execution']['max_concurrent_positions']
            self._run.max_gross_fraction = profile['safety_and_execution']['max_gross_exposure_fraction']
        merit = self.plant_control.dispatch_controller.merit_source
        merit.runtime_ceiling_cap = profile['dispatch_ceiling']
        merit.runtime_floor = profile['dispatch_floor']
        self._runtime_profile = deepcopy(profile)

    def _r8_install_profile(self, profile):
        """Hand a tighten-only profile to the host's existing consumers (PA / ID / Box 6 config,
        safety gate, run limits, sector dispatch, drawdown halt and per-symbol sizing)."""
        self._r8_commit_profile(self._r8_prepare_profile(profile))

    def r8_adaptation_report(self):
        a = self.r8_adaptation
        return {'mode': a.mode, 'bars': self.r8_gate_counts.get('ADAPTATION_BARS', 0),
                'version': a.controller._version, 'changes_logged': a.count, 'digest': a.digest,
                'parity_checks': a.parity_checks, 'parity_mismatches': a.parity_mismatches,
                'stress_index': None if a.snapshot is None else a.snapshot.stress_index,
                'applied': deepcopy(a.applied)}

    def _observe_conviction(self, symbol, signal, composite_result):
        super()._observe_conviction(symbol, signal, composite_result)
        observed = self._r8_observed.pop(symbol, None)
        if observed is None:
            raise RuntimeError('R8 studies/bar handoff missing')
        index, row, result = observed
        if result != composite_result:
            raise RuntimeError('R8 studies handoff mismatch')
        # Use the signed studies result, not candidate conviction or candidate side.
        direction, confidence = float(result['direction']), float(result['confidence'])
        if direction not in (-1, 0, 1) or not 0 <= confidence <= 1:
            raise ValueError('invalid signed study observation')
        self._r8_queue(symbol, index, row, direction * confidence, True)

    def r8_flush(self):
        """Allocate and commit any queued observations (e.g. the last warmup bar)."""
        return self._r8_flush_batch()

    def _process_tick(self, timestamp, tick_events):
        events = list(tick_events)
        # Warmup observations arrive symbol by symbol outside the tick clock; settle any
        # leftover batch before this timestamp's observations are gathered.
        self._r8_flush_batch()
        self._r8_tick_events = events
        try:
            result = super()._process_tick(timestamp, events)
        finally:
            self._r8_tick_events = None
        # Normally the batch is allocated at the pre-admission hook; this covers a
        # native core that skips it, and symbols rejected before studies.
        self._r8_complete_tick(events)
        return result

    def _r8_complete_tick(self, events):
        for event in events:
            ctrl = self.r8_symbol_controllers[event.symbol]
            done = ctrl.last_result is not None and ctrl.last_result.bar_index >= event.bar_idx
            if not done and event.symbol not in self._r8_batch:
                # Symbols rejected before studies still receive a degraded observation.
                row = self._run.symbol_bars[event.symbol].iloc[event.bar_idx]
                self._r8_queue(event.symbol, event.bar_idx, row, 0.0, False)
        self._r8_flush_batch()

    def rank_simultaneous_entry_candidates(self, candidates):
        """Native S4 arbitration point: every symbol at this timestamp has been observed
        and exited; allocate R8 capacity once, before any candidate is admitted."""
        if self._r8_tick_events is not None:
            self._r8_complete_tick(self._r8_tick_events)
        return super().rank_simultaneous_entry_candidates(candidates)

    def _plant_entry_gate(self, symbol, timestamp, side, quantity, price, bar, candidate_id, governor_entry):
        self.r8_gate_counts['CANDIDATES_RECEIVED'] += 1
        ctrl = self.r8_symbol_controllers[symbol]
        pulse = ctrl.last_result
        expected = 'BUY' if side == 'BUY' else 'SELL' if side == 'SELL' else None
        if expected is None:
            raise ValueError('invalid order side')
        if side == 'SELL' and not self.r8_allow_native_shorts:
            self.r8_gate_counts['REJECTED_BY_SHORT_POLICY'] += 1
            return 0
        if pulse is None or pulse.bar_index != self._current_bar_idx or pulse.timestamp != str(timestamp) or not pulse.eligible or pulse.pulse != expected:
            self.r8_gate_counts['REJECTED_BY_R8_PULSE'] += 1
            return 0
        self.r8_gate_counts['ADMITTED_BY_R8_PULSE'] += 1
        native = int(super()._plant_entry_gate(symbol, timestamp, side, quantity, price, bar,
                                              candidate_id, governor_entry))
        self.r8_gate_counts['ADMITTED_BY_NATIVE_PLANT_GATE' if native > 0 else 'REJECTED_BY_NATIVE_PLANT_GATE'] += 1
        return max(0, min(int(quantity), native))

    def _admit_entry_candidate(self, pending, timestamp):
        # The native core only invokes _plant_entry_gate when a plant is wired.
        # Enforce the R8 pulse at the candidate seam as well, so a core engine
        # without a plant can never bypass R8 admission.
        self.r8_gate_counts['UPSTREAM_CANDIDATES_RECEIVED'] += 1
        symbol = pending['symbol']
        decision = self.r8_symbol_controllers[symbol].last_result
        side = pending['plan'].side
        if side not in ('BUY', 'SELL'):
            raise ValueError('invalid candidate side')
        allowed = (decision is not None and decision.bar_index == pending['bar_idx']
            and decision.timestamp == str(timestamp) and decision.eligible and decision.pulse == side
            and (side != 'SELL' or self.r8_allow_native_shorts))
        if not allowed:
            self.r8_gate_counts['REJECTED_BEFORE_NATIVE_ADMISSION'] += 1
            self.entry_candidate_observations.dispose(pending['candidate_id'], 'REJECTED', 'r8_pulse_rejected')
            return None
        self.r8_gate_counts['ADMITTED_TO_NATIVE_CANDIDATE_PATH'] += 1
        return super()._admit_entry_candidate(pending, timestamp)

    def r8_physical_governor_step(self, bay_id, *, dt_s, p_ref_pu, p_electrical_pu):
        """Explicit physical inputs only: true R6 droop, turbine lag and rotor swing."""
        return self.r8_physical_units[bay_id].step(dt_s=dt_s, p_ref_pu=p_ref_pu,
                                                 p_electrical_pu=p_electrical_pu)

    # Operator decision (2026-10-17): only native exits individually proven discretionary
    # may be deferred to R8. GOVERNOR_Z_EXIT is the mean-reversion release that the native
    # authority itself documents as discretionary (revision5/governor_authority.py: it ranks
    # below every hard, protective and Mark V limiter and is deferred inside minimum hold).
    # Everything else, including FSRN/FSRT limiter exits, stressed-regime, saturation,
    # governor path-error, controller-path and final-controller exits, and any unknown
    # reason, keeps native execution authority.
    R8_DISCRETIONARY_NATIVE_EXITS = frozenset({'governor_exit:GOVERNOR_Z_EXIT'})

    @classmethod
    def _r8_normal_reason(cls, reason):
        """Exact-match allowlist of discretionary native exits; no prefix matching."""
        return reason in cls.R8_DISCRETIONARY_NATIVE_EXITS

    def _execute_exit(self, symbol, timestamp, trade, exit_price, reason):
        if self._r8_exit_scope and self._r8_normal_reason(str(reason)):
            self.r8_gate_counts['NATIVE_NORMAL_EXIT_ADVISORY'] += 1
            self._record_controller_event('R8_NATIVE_EXIT_ADVISORY', timestamp, symbol,
                                          {'native_reason': reason})
            return None
        result = super()._execute_exit(symbol, timestamp, trade, exit_price, reason)
        if self.open_trades.get(symbol) is not trade:
            self.r8_normal_exit_state.pop(symbol, None)
        return result

    def _r8_remove_normal_pending(self, trade):
        pending = trade.get('controller_exit_pending')
        # Only a pending native exit with an allowlisted discretionary reason may be
        # withdrawn; protective and unknown pending exits keep native authority.
        if pending and self._r8_normal_reason(str(pending.get('exit_reason', ''))):
            trade.pop('controller_exit_pending')

    def _maybe_exit(self, symbol, timestamp, bar, signal, held_bars, session_last_bar,
                    chart_studies_confidence, chart_studies_audit=None):
        trade = self.open_trades.get(symbol)
        if trade is None:
            self.r8_normal_exit_state.pop(symbol, None)
            return None
        self._r8_remove_normal_pending(trade)
        same_bar_pending = trade.get('controller_exit_pending')
        if (same_bar_pending and same_bar_pending.get('exit_reason') == 'r8_coordinated_exit'
                and same_bar_pending.get('armed_timestamp') == str(timestamp)):
            trade.pop('controller_exit_pending')
        else:
            same_bar_pending = None
        # Let the existing engine process all protective/hard/time exits first.
        self._r8_exit_scope = True
        try:
            result = super()._maybe_exit(symbol, timestamp, bar, signal, held_bars,
                session_last_bar, chart_studies_confidence, chart_studies_audit)
        finally:
            self._r8_exit_scope = False
        if self.open_trades.get(symbol) is not trade:
            self.r8_normal_exit_state.pop(symbol, None)
            return result
        self._r8_remove_normal_pending(trade)
        if same_bar_pending and not trade.get('controller_exit_pending'):
            trade['controller_exit_pending'] = same_bar_pending
        # Existing protective pending exits and venue working exits take precedence.
        if trade.get('controller_exit_pending') or symbol in getattr(self, '_symbol_inflight', {}) or symbol in getattr(self, '_working_exits', {}):
            return result
        bar_index = self._current_bar_idx
        decision = self.r8_symbol_controllers[symbol].last_result
        if symbol in self._r8_batch or (decision is not None and decision.timestamp != str(timestamp)
                                        and self._r8_batch_ts == str(timestamp)):
            # The batch for this timestamp is not allocated yet: arm after commit.
            self._r8_deferred_exits[symbol] = (timestamp, held_bars, bar_index)
            return result
        self._r8_arm_normal_exit(symbol, timestamp, held_bars, bar_index)
        return result

    def _r8_arm_normal_exit(self, symbol, timestamp, held_bars, bar_index):
        trade = self.open_trades.get(symbol)
        if trade is None:
            self.r8_normal_exit_state.pop(symbol, None)
            return
        # Existing protective pending exits and venue working exits take precedence.
        if trade.get('controller_exit_pending') or symbol in getattr(self, '_symbol_inflight', {}) or symbol in getattr(self, '_working_exits', {}):
            return
        decision = self.r8_symbol_controllers[symbol].last_result
        c = self.r8_configs[symbol]
        if (decision is None or decision.timestamp != str(timestamp) or
                decision.bar_index != bar_index):
            return
        identity = str(trade.get('trade_id', trade.get('candidate_id', '')))
        state = self.r8_normal_exit_state.get(symbol)
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
        self.r8_normal_exit_state[symbol] = state
        self._record_controller_event('R8_POSITION_DECISION', timestamp, symbol,
            {'position_id': identity, 'pulse': decision.pulse, 'action': 'EXIT_REQUEST' if request_exit else 'HOLD',
             'confirmations': state['count'], 'held_bars': held_bars})
        if state['count'] >= c.normal_exit_confirm_bars and held_bars >= int(trade['minimum_hold_bars']):
            # This signal is known at this close, so the order uses the next open.
            trade['controller_exit_pending'] = {
                'armed_timestamp': str(timestamp), 'armed_bar': decision.bar_index,
                'reason': 'R8_OPPOSITE_PULSE' if decision.pulse == opposite else 'R8_NEUTRAL_RELEASE',
                'exit_reason': 'r8_coordinated_exit', 'event': 'R8_COORDINATED_EXIT',
                'pulse': decision.pulse, 'position_id': identity}
            self.r8_gate_counts['NORMAL_EXITS_ARMED'] += 1
            self._record_controller_event('R8_NORMAL_EXIT_ARMED', timestamp, symbol,
                                          dict(trade['controller_exit_pending']))

    def r8_snapshot(self):
        if self._r8_observed or self._r8_batch or self._r8_deferred_exits:
            raise RuntimeError('snapshot requires a completed control handoff')
        return deepcopy({'schema': 3, 'adaptation': self.r8_adaptation_mode, 'identity': self._r8_identity(),
            'adaptation_state': self.r8_adaptation.snapshot_state(), 'dispatch_scale': self.r8_dispatch_scale,
            'allow_shorts': self.r8_allow_native_shorts, 'topology': self.r8_topology_report,
            'controllers': {s: c.snapshot() for s, c in self.r8_symbol_controllers.items()},
            'physical': {b: x.snapshot() for b, x in self.r8_physical_units.items()},
            'bay_state': {b: x.symbols for b, x in self.r8_bay_state.items()},
            'normal_exit_state': self.r8_normal_exit_state,
            'counts': dict(self.r8_gate_counts),
            'decision_count': self.r8_decision_count, 'decision_digest': self.r8_decision_digest})

    def _r8_identity(self):
        registry = getattr(self, 'registry', None)
        config = getattr(self, 'config', None)
        return {'registry_identity_sha256': getattr(registry, 'FROZEN_IDENTITY_SHA256', None),
                'config_hash': getattr(config, 'config_hash', None),
                'r8_parameter_fingerprint': self.r8_parameter_fingerprint}

    def r8_restore(self, payload):
        """Restore R8 control state only, alongside a separately restored native ledger."""
        json.dumps(payload, allow_nan=False)
        if (payload['schema'], payload['adaptation'], payload['dispatch_scale'], payload['allow_shorts'], payload['topology']) != (3, self.r8_adaptation_mode, self.r8_dispatch_scale, self.r8_allow_native_shorts, self.r8_topology_report):
            raise ValueError('R8 checkpoint identity/configuration mismatch')
        if payload.get('identity') != self._r8_identity():
            raise ValueError('R8 checkpoint registry / configuration / parameter identity mismatch')
        adaptation_state = payload['adaptation_state']
        self.r8_adaptation.validate_state(adaptation_state)
        _, restored_snapshot = self.r8_adaptation.reproduce(adaptation_state)
        prepared_profile = None
        if adaptation_state['applied'] is not None:
            # The saved profile must be exactly what this engine derives from the saved snapshot.
            if restored_snapshot is None or adaptation_state['applied'] != self._r8_profile_values(restored_snapshot):
                raise ValueError('R8 checkpoint applied profile does not follow from its snapshot')
            prepared_profile = self._r8_prepare_profile(adaptation_state['applied'])
        if set(payload['controllers']) != set(self.r8_symbol_controllers) or set(payload['physical']) != set(BAY_IDS) or set(payload['bay_state']) != set(BAY_IDS):
            raise ValueError('R8 checkpoint universe mismatch')
        new = {s: SymbolController(c) for s, c in self.r8_configs.items()}
        for s, controller in new.items():
            controller.restore(payload['controllers'][s])
        physical = {b: TurbineGovernorUnit(c, p_mech_pu=c.power_min_pu) for b, c in repository_defaults().items()}
        for b, unit in physical.items():
            unit.restore(payload['physical'][b])
        bays = {b: BayAggregateState() for b in BAY_IDS}
        for s, ctrl in new.items():
            if ctrl.last_result is not None:
                d = ctrl.last_result
                f = self.r8_configs[s].budget_fraction
                bays[d.bay_id].update(type(d)(**{**vars(d), 'target': d.target * f, 'actual': d.actual * f}))
        if {b: x.symbols for b, x in bays.items()} != payload['bay_state']:
            raise ValueError('R8 aggregate state mismatch')
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
        self.r8_symbol_controllers, self.r8_physical_units, self.r8_bay_state = new, physical, bays
        self.r8_gate_counts = Counter(payload['counts'])
        self.r8_decisions.clear()
        self.r8_decision_count, self.r8_decision_digest = payload['decision_count'], payload['decision_digest']
        self.r8_normal_exit_state = deepcopy(payload['normal_exit_state'])
        self.r8_adaptation.restore_state(adaptation_state)      # already validated: assignments only
        if prepared_profile is not None:
            self._r8_commit_profile(prepared_profile)


def engine_class(base=None):
    """Attach to the existing shared engine; callers may supply a compatible native subclass."""
    if base is None:
        from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
        base = Revision2ExternalEngineOrchestrator
    return type('Revision8Engine', (R8EngineMixin, base), {'__module__': __name__})


def r8_classes():
    """Return R8-enabled Engine B and Engine C classes for the native runners.

    The factory keeps the integration process-local: callers explicitly replace the
    runner's orchestrator class for an offline run, while the native classes and live
    order paths remain unchanged on disk.
    """
    from engine_b.orchestrator import EngineBOrchestrator
    from engine_c.orchestrator import EngineCOrchestrator

    class R8EngineBOrchestrator(R8EngineMixin, EngineBOrchestrator):
        r8_variant = 'B'

    class R8EngineCOrchestrator(R8EngineMixin, EngineCOrchestrator):
        r8_variant = 'C'

    return R8EngineBOrchestrator, R8EngineCOrchestrator


# Lazy construction avoids importing native analytics during isolated component tests.
def Revision8Engine(*args, **kwargs):
    return engine_class()(*args, **kwargs)
