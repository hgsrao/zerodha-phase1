"""Box 6 (Model Predictive Control) -- simple-pid-based trade plan builder.

Replaces revision2/boxes.py's hand-rolled BoundedPID with the simple-pid
library's PID controller for the entry/exit timing adjustment. Keeps
everything else (ATR stop/target distance, slippage estimate, minimum
reward:risk enforcement) identical to ModelPredictiveControlBox -- that
part is ATR-based order construction, not a control loop, and simple-pid
has nothing to say about it.

Documented, real difference from BoundedPID (not glossed over): BoundedPID
sums only the last `window` errors, then clamps that sum (a rolling-window
anti-windup). simple-pid instead accumulates the integral term without
bound and clamps the OUTPUT via output_limits ("Setting output limits also
avoids integral windup, since the integral term will never be allowed to
grow outside of the limits" -- simple_pid's own docstring). Both prevent
unbounded growth; they are not the same anti-windup strategy, and will
produce different numbers under a sustained one-directional error. dt=1 is
passed explicitly on every update so the controller advances one
simulated bar at a time regardless of wall-clock time -- matching this
project's own established principle of injected, deterministic event time
rather than real time.

PID setpoints (fixed this session, see _confidence_baseline): both PIDs'
targets used to be fixed absolute constants -- entry hardcoded at 0.5
(identical to entry_confidence_threshold's own default, which
IntelligentDiscrimination already uses to filter out every signal the
entry PID would ever see, making error = target - confidence
mathematically <= 0 on every call), exit at decision.timing_quality
(effectively fixed at exit_confidence_threshold). Both drove their
integral term to its clamp and kept it there on real INFY data
(entry_adjustment pinned at -0.0997 / -0.100; after fixing entry alone, a
real 6-month run showed exit still pinned on 14.6% of calls vs entry's
5.4%, mean -0.047 vs -0.017 -- the same fixed-absolute-target design,
just one step removed from a mathematical guarantee). No fixed target
generally avoids this: whichever level is picked, a symbol whose real
confidence sits mostly on one side of it drives a persistently one-signed
error regardless of Kp/Ki/Kd. Fixed by giving both PIDs the SAME rolling
mean of the symbol's own recent confidence as their setpoint (computed
once per call, shared -- they observe the identical input series), which
cannot be permanently on one side of the current reading the way a fixed
constant can.
"""

from __future__ import annotations
from revision2_external.dynamic_parameter_controller import DynamicParameterController

from collections import deque
from math import isfinite
from typing import Any, Deque, Dict, List, Optional, Tuple

from simple_pid import PID
from revision2_external.bb05_bb06_parameters import require

# Fixed protective floors; never calibratable.
_ENTRY_TIMING_FLOOR = 0.3
_EXIT_TIGHTNESS_FLOOR = 0.5
from revision2.transaction_costs import paper_fill_price

from revision2.contracts import EffectiveConfig, IDDecision, ParameterUse, PASignal, TradePlan


def _np_clip(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


_STATE_VERSION = 1
# Construction contract of every MPC PID (see _get_pid / build_plan): any other value is foreign state.
_PID_MODE = dict(dt=1, sample_time=None, auto_mode=True, proportional_on_measurement=False,
                 differential_on_measurement=True, error_map=None)
_PID_STATE_FIELDS = {'tunings', 'setpoint', 'output_limits', 'proportional', 'integral', 'derivative',
                     'last_input', 'last_error', 'last_output'}
_STATE_FIELDS = {'version', 'config_hash', 'simple_pid_version', 'pid_enabled', 'pid_mode',
                 'confidence_history', 'entry_pids', 'exit_pids'}


def _simple_pid_version() -> str:
    from importlib.metadata import version
    return version("simple_pid")


def _state_real(value: Any, what: str) -> float:
    """Finite real number; bool/str/None/NaN/inf are never accepted as numeric PID state."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid MPC PID state: {what} is not a number")
    try:
        number = float(value)  # an oversized JSON int raises OverflowError here
        finite = isfinite(number)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"Invalid MPC PID state: {what} is not finite") from None
    if not finite:
        raise ValueError(f"Invalid MPC PID state: {what} is not finite")
    return number


def _state_dict(value: Any, keys: set, what: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Invalid MPC PID state: {what} schema")
    return value


def _state_pair(value: Any, count: int, what: str) -> List[float]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"Invalid MPC PID state: {what} shape")
    return [_state_real(v, what) for v in value]


class SimplePIDModelPredictiveControlBox:
    def __init__(self, *, pid_enabled: bool = True) -> None:
        """Build ATR/RR plans, optionally with the legacy PID modifiers.

        ``pid_enabled=False`` is an explicit research ablation.  It keeps
        admission, ATR, target/stop multipliers and all downstream safety and
        sizing logic unchanged while making the three PID-derived plan
        transformations identities.  It is deliberately not a configuration
        parameter: a run must declare which architecture it is testing.
        """
        self.pid_enabled = bool(pid_enabled)
        self._entry_pids: Dict[str, PID] = {}
        self._exit_pids: Dict[str, PID] = {}
        # Rolling per-symbol confidence history backing BOTH PIDs' adaptive
        # setpoint -- see _confidence_baseline below for why this exists.
        self._confidence_history: Dict[str, Deque[float]] = {}

    # ---- exact state continuity (mpc_v1) -------------------------------------------------------
    # Only semantic controller state is persisted.  PID._last_time is a monotonic wall-clock stamp that
    # is meaningless across processes and unused here (dt=1 is always explicit), so it is excluded.
    # Config is bound by config_hash and *validated*, never reapplied: tunings are per-call scheduled
    # values and are restored as saved; limits/window must equal what the live config would produce.

    @staticmethod
    def _config_bounds(config: EffectiveConfig) -> Tuple[int, float]:
        window = config.require("pid_integral_window_bars")
        clamp = config.require("pid_integral_max_clamp")
        if isinstance(window, bool) or int(window) != window or int(window) < 1:
            raise ValueError("Invalid MPC PID state: configured confidence window")
        return int(window), abs(_state_real(clamp, "configured clamp"))

    @staticmethod
    def _export_pid(pid: PID) -> Dict[str, Any]:
        live_mode = dict(sample_time=pid.sample_time, auto_mode=pid.auto_mode,
                         proportional_on_measurement=pid.proportional_on_measurement,
                         differential_on_measurement=pid.differential_on_measurement, error_map=pid.error_map)
        if live_mode != {k: v for k, v in _PID_MODE.items() if k != 'dt'} or any(
                type(live_mode[k]) is not type(_PID_MODE[k]) for k in live_mode):
            raise ValueError("Invalid MPC PID state: live PID mode differs from the MPC contract")
        low, high = pid.output_limits
        proportional, integral, derivative = pid.components
        return dict(tunings=[pid.Kp, pid.Ki, pid.Kd], setpoint=pid.setpoint, output_limits=[low, high],
                    proportional=proportional, integral=integral, derivative=derivative,
                    last_input=pid._last_input, last_error=pid._last_error, last_output=pid._last_output)

    def export_state(self, config: EffectiveConfig) -> Dict[str, Any]:
        """JSON-safe ``mpc_v1`` payload; validated here so an invalid state never reaches disk."""
        def encode(pids: Dict[str, PID]) -> Dict[str, Any]:
            out = {}
            for symbol, pid in pids.items():
                state = self._export_pid(pid)
                out[symbol] = {k: ([_state_real(v, k) for v in x] if isinstance(x, (list, tuple))
                                   else _state_real(x, k)) for k, x in state.items()}
            return out

        payload = dict(
            version=_STATE_VERSION, config_hash=config.config_hash, simple_pid_version=_simple_pid_version(),
            pid_enabled=self.pid_enabled, pid_mode=dict(_PID_MODE),
            confidence_history={symbol: dict(maxlen=history.maxlen,
                                             values=[_state_real(v, 'confidence') for v in history])
                                for symbol, history in self._confidence_history.items()},
            entry_pids=encode(self._entry_pids), exit_pids=encode(self._exit_pids))
        self._stage_state(payload, config)
        return payload

    def _stage_state(self, payload: Any, config: EffectiveConfig):
        """Strictly validate and rebuild into NEW objects; touches no live attribute."""
        payload = _state_dict(payload, _STATE_FIELDS, "payload")
        if type(payload['version']) is not int or payload['version'] != _STATE_VERSION:
            raise ValueError("Unsupported MPC PID state version")
        if payload['config_hash'] != config.config_hash or type(payload['config_hash']) is not str:
            raise ValueError("MPC PID state was saved under a different config")
        if payload['simple_pid_version'] != _simple_pid_version() or type(payload['simple_pid_version']) is not str:
            raise ValueError("MPC PID state was saved under a different simple_pid version")
        if type(payload['pid_enabled']) is not bool or payload['pid_enabled'] != self.pid_enabled:
            raise ValueError("MPC PID state pid_enabled binding mismatch")
        mode = payload['pid_mode']
        if (not isinstance(mode, dict) or set(mode) != set(_PID_MODE)
                or any(type(mode[k]) is not type(v) or mode[k] != v for k, v in _PID_MODE.items())):
            raise ValueError("MPC PID state mode differs from the MPC contract")
        window, clamp = self._config_bounds(config)

        histories: Dict[str, Deque[float]] = {}
        raw_histories = payload['confidence_history']
        if not isinstance(raw_histories, dict):
            raise ValueError("Invalid MPC PID state: confidence_history")
        for symbol, entry in raw_histories.items():
            if type(symbol) is not str or not symbol:
                raise ValueError("Invalid MPC PID state: symbol")
            entry = _state_dict(entry, {'maxlen', 'values'}, "confidence history")
            if type(entry['maxlen']) is not int or entry['maxlen'] != window:
                raise ValueError("Invalid MPC PID state: confidence window differs from config")
            if not isinstance(entry['values'], list) or len(entry['values']) > window:
                raise ValueError("Invalid MPC PID state: confidence history length")
            histories[symbol] = deque((_state_real(v, 'confidence') for v in entry['values']), maxlen=window)

        def decode(raw: Any, role: str) -> Dict[str, PID]:
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid MPC PID state: {role} PIDs")
            if not self.pid_enabled and raw:
                raise ValueError("Disabled MPC cannot carry PID state")
            pids: Dict[str, PID] = {}
            for symbol, state in raw.items():
                if symbol not in histories:
                    raise ValueError("Orphaned MPC PID without confidence history")
                state = _state_dict(state, _PID_STATE_FIELDS, f"{role} PID")
                kp, ki, kd = _state_pair(state['tunings'], 3, 'tunings')
                low, high = _state_pair(state['output_limits'], 2, 'output_limits')
                if (low, high) != (-clamp, clamp):
                    raise ValueError("MPC PID output limits differ from the configured clamp")
                values = {name: _state_real(state[name], name) for name in
                          ('setpoint', 'proportional', 'integral', 'derivative',
                           'last_input', 'last_error', 'last_output')}
                if abs(values['integral']) > clamp or abs(values['last_output']) > clamp:
                    raise ValueError("MPC PID integral/output exceeds the configured clamp")
                pid = PID(Kp=kp, Ki=ki, Kd=kd, setpoint=values['setpoint'], sample_time=None,
                          output_limits=(low, high), auto_mode=True, proportional_on_measurement=False,
                          differential_on_measurement=True, error_map=None)
                pid._proportional, pid._integral, pid._derivative = (
                    values['proportional'], values['integral'], values['derivative'])
                pid._last_input, pid._last_error, pid._last_output = (
                    values['last_input'], values['last_error'], values['last_output'])
                pids[symbol] = pid
            return pids

        entry_pids = decode(payload['entry_pids'], 'entry')
        exit_pids = decode(payload['exit_pids'], 'exit')
        if set(entry_pids) != set(exit_pids) or (self.pid_enabled and set(entry_pids) != set(histories)):
            raise ValueError("MPC PID symbol mapping is incomplete")
        return histories, entry_pids, exit_pids

    def restore_state(self, payload: Any, config: EffectiveConfig) -> None:
        """Install a saved ``mpc_v1`` payload into this EMPTY box; all-or-nothing."""
        if self._entry_pids or self._exit_pids or self._confidence_history:
            raise ValueError("MPC PID state can only be restored into an empty controller")
        histories, entry_pids, exit_pids = self._stage_state(payload, config)
        self._confidence_history, self._entry_pids, self._exit_pids = histories, entry_pids, exit_pids

    def _get_pid(self, store: Dict[str, PID], symbol: str, kp: float, ki: float, kd: float,
                 target: float, clamp: float) -> PID:
        if symbol not in store:
            pid = PID(Kp=kp, Ki=ki, Kd=kd, setpoint=target, sample_time=None, output_limits=(-abs(clamp), abs(clamp)))
            store[symbol] = pid
        store[symbol].tunings = (kp, ki, kd)
        store[symbol].output_limits = (-abs(clamp), abs(clamp))
        return store[symbol]

    def _confidence_baseline(self, symbol: str, current_confidence: float, window: int) -> float:
        """Both PIDs' setpoint: a rolling mean of this symbol's own recent
        decision.confidence, computed from bars BEFORE this one (the current
        reading is folded into the history only after the baseline is read).
        Called exactly once per build_plan() call -- both PIDs share the one
        history, since they observe the identical confidence series; a second
        call would double-count the same bar.

        This replaces a real, provable saturation bug found by tracing real
        INFY trades. Entry PID: the old fixed target (0.5) was identical to
        entry_confidence_threshold's own default, and IDDecision.approved
        already guarantees decision.confidence >= entry_confidence_threshold
        for every signal that ever reaches this PID (IntelligentDiscrimination
        filters everything else out first). So error = target - confidence
        was mathematically <= 0 on 100% of calls -- not usually small, not
        occasionally reversing, but *guaranteed* one-signed by construction,
        regardless of Kp/Ki/Kd. That drove simple_pid's internal integral
        term to its clamp and kept it pinned there: real data showed
        entry_adjustment = -0.09971 and -0.100 (both at/within 0.0003 of
        -pid_integral_max_clamp) for confidences 0.5130 and 0.5709 on the
        real 2023-07-13 INFY trade. Exit PID: same underlying problem, one
        step removed -- target=decision.timing_quality (effectively fixed at
        exit_confidence_threshold) has no *mathematical* guarantee of
        one-signed error the way entry's did, but empirically it was worse:
        after the entry fix, a real 6-month INFY run showed the exit PID
        pinned at its clamp on 14.6% of calls (68/467) against only 5.4%
        (25/467) for the already-fixed entry PID (mean -0.047 vs -0.017) --
        confirming the same fixed-absolute-target design leans hard on real
        data regardless of which specific constant is chosen.
        No choice of fixed target fixes this in general: whatever absolute
        level is picked, a symbol whose approved-signal confidence happens to
        sit mostly on one side of it will still drive a persistently
        one-signed error (verified: retargeting entry to green_threshold's
        default, 0.75, would merely flip which rail saturates, since INFY's
        real confidence values cluster near 0.5-0.6). A trailing mean of the
        symbol's own confidence has no such guarantee: deviations from a
        recent average are positive about as often as negative for any real,
        noisy series, so both controllers can actually move off their rail
        instead of riding it permanently. Reuses pid_integral_window_bars,
        which previously had no simple-pid equivalent at all (see module
        docstring) -- this gives it real, load-bearing meaning instead of
        being consumed only for parameter-coverage bookkeeping.
        """
        history = self._confidence_history.setdefault(symbol, deque(maxlen=window))
        if history.maxlen != window:
            history = self._confidence_history[symbol] = deque(history, maxlen=window)
        baseline = (sum(history) / len(history)) if history else current_confidence
        history.append(current_confidence)
        return baseline

    def build_plan(
        self, signal: PASignal, decision: IDDecision, entry_price: float, atr: float, config: EffectiveConfig,
    ) -> Tuple[Optional[TradePlan], Dict[str, float], List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = require(config, name) if name.startswith("mpc_") else config.require(name)
            trace.append(ParameterUse(name, "MPC", value, calculation, output_field))
            return value

        profit_mult = float(req("profit_target_atr_mult", "ATR multiplier for the profit target", "target_price"))
        stop_mult = float(req("stop_loss_atr_mult", "ATR multiplier for the stop", "stop_price"))
        margin_buffer = float(req("profit_target_margin_buffer", "extra buffer added to the target", "target_price"))
        min_rr = float(req("min_risk_reward_ratio", "minimum reward:risk enforced on the target distance", "target_price"))
        min_hold = int(req("min_hold_bars", "minimum bars to hold before exit is allowed", "minimum_hold_bars"))
        max_hold = int(req("max_hold_bars", "maximum bars to hold before forced exit", "maximum_hold_bars"))
        slippage_cost_mult = float(req("slippage_cost_multiplier", "cost multiplier applied to entry price", "entry_price"))
        kp_entry = float(req("pid_kp_entry", "entry PID proportional gain", "timing_quality"))
        ki_entry = float(req("pid_ki_entry", "entry PID integral gain", "timing_quality"))
        kd_entry = float(req("pid_kd_entry", "entry PID derivative gain", "timing_quality"))
        kp_exit = float(req("pid_kp_exit", "exit PID proportional gain", "timing_quality"))
        ki_exit = float(req("pid_ki_exit", "exit PID integral gain", "timing_quality"))
        kd_exit = float(req("pid_kd_exit", "exit PID derivative gain", "timing_quality"))
        # Now genuinely load-bearing: the rolling window backing the entry
        # PID's adaptive setpoint (see _entry_setpoint). Previously consumed
        # only for coverage bookkeeping since it had no simple-pid
        # equivalent -- fixing the entry-PID saturation bug gave it a real job.
        pid_window = int(req("pid_integral_window_bars", "rolling window for the entry PID's adaptive confidence baseline", "timing_quality"))
        integral_clamp = float(req("pid_integral_max_clamp", "clamp applied to the PID integral/output term", "timing_quality"))
        # Retained from bf091fd unchanged: coverage-bookkeeping read only.  The
        # value is not consumed by simple-pid (see audit: pre-existing debt).
        req("pid_derivative_smoothing", "smoothing window for the PID derivative term (BoundedPID only; simple-pid derivative is single-step)", "timing_quality")
        entry_price_gain = req("mpc_entry_price_gain", "market reference adjustment", "entry_price")
        base_slippage = req("mpc_base_slippage_fraction", "shared paper fill base", "entry_price")
        for name in ("mpc_schedule_kp_gain", "mpc_schedule_ki_gain", "mpc_schedule_kd_gain"):
            req(name, "entry PID gain scheduling", "timing_quality")

        if not decision.approved:
            return None, {}, trace

        side = "BUY" if signal.direction > 0 else "SELL"

        stop_distance = atr * stop_mult
        target_distance = atr * profit_mult * (1 + margin_buffer)
        if stop_distance > 0:
            target_distance = max(target_distance, stop_distance * min_rr)

        # Computed ONCE per call and shared by both PIDs -- see
        # _confidence_baseline's docstring for why neither PID keeps a fixed
        # absolute target any more.  The ablation path intentionally retains
        # the same ATR/RR plan but applies no PID transformation at all.
        confidence_baseline = self._confidence_baseline(signal.symbol, decision.confidence, pid_window)
        if self.pid_enabled:
            err_delta = float(decision.confidence - confidence_baseline)
            kp_e, ki_e, kd_e = DynamicParameterController.get_tier3_pid_schedule(kp_entry, ki_entry, kd_entry, err_delta, config=config)
            entry_pid = self._get_pid(self._entry_pids, signal.symbol, kp_e, ki_e, kd_e, target=confidence_baseline, clamp=integral_clamp)
            entry_pid.setpoint = confidence_baseline  # keep in sync on every call, not just at first construction
            entry_adjustment = entry_pid(decision.confidence, dt=1)
            entry_timing_multiplier = _np_clip(1.0 - abs(entry_adjustment), _ENTRY_TIMING_FLOOR, 1.0)

            exit_pid = self._get_pid(self._exit_pids, signal.symbol, kp_exit, ki_exit, kd_exit, target=confidence_baseline, clamp=integral_clamp)
            exit_pid.setpoint = confidence_baseline  # keep in sync on every call, not just at first construction
            exit_adjustment = exit_pid(decision.confidence, dt=1)
            exit_tightness = _np_clip(1.0 - abs(exit_adjustment), _EXIT_TIGHTNESS_FLOOR, 1.0)
            entry_p, entry_i, entry_d = entry_pid.components
            exit_p, exit_i, exit_d = exit_pid.components
        else:
            entry_adjustment = 0.0
            exit_adjustment = 0.0
            entry_timing_multiplier = 1.0
            exit_tightness = 1.0
            entry_p = entry_i = entry_d = 0.0
            exit_p = exit_i = exit_d = 0.0
        # The controller adjusts the submitted paper-market reference, not
        # merely an internal plan field.  The broker subsequently applies
        # its normal adverse fill exactly once.  This keeps planned entry
        # and expected paper fill aligned in both PID modes.
        execution_market_price = float(entry_price) * (1.0 + entry_adjustment * entry_price_gain)
        effective_entry = paper_fill_price(execution_market_price, side, slippage_cost_mult * base_slippage)
        stop_distance *= exit_tightness
        target_distance *= exit_tightness

        if side == "BUY":
            stop_price = effective_entry - stop_distance
            target_price = effective_entry + target_distance
        else:
            stop_price = effective_entry + stop_distance
            target_price = effective_entry - target_distance

        # No profit-floor check here -- moved post-sizing, into
        # SafetyGatesTargetBox.evaluate_post_sizing(), which knows the real
        # quantity and can compare against the real round-trip cost. See
        # that method and minimum_profit_margin_over_cost's registry entry.
        plan = TradePlan(
            side=side, entry_price=float(effective_entry), stop_price=float(stop_price),
            target_price=float(target_price), minimum_hold_bars=min_hold, maximum_hold_bars=max_hold,
        )
        # Observation-only detail for the telemetry ledger.  None of these
        # fields participates in the plan calculations above.
        pid_info = {
            "pid_enabled": self.pid_enabled,
            "execution_market_price": float(execution_market_price),
            "entry_adjustment": entry_adjustment, "exit_adjustment": exit_adjustment,
            "entry_timing_multiplier": entry_timing_multiplier,
            "entry_setpoint": confidence_baseline,
            "entry_measurement": float(decision.confidence),
            "entry_error": confidence_baseline - float(decision.confidence),
            "entry_p": float(entry_p), "entry_i": float(entry_i), "entry_d": float(entry_d),
            "entry_clamped": abs(entry_adjustment) >= integral_clamp - 1e-12,
            "exit_setpoint": confidence_baseline,
            "exit_measurement": float(decision.confidence),
            "exit_error": confidence_baseline - float(decision.confidence),
            "exit_p": float(exit_p), "exit_i": float(exit_i), "exit_d": float(exit_d),
            "exit_clamped": abs(exit_adjustment) >= integral_clamp - 1e-12,
            "exit_tightness": exit_tightness,
        }
        return plan, pid_info, trace
