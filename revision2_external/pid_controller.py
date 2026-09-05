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

from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from simple_pid import PID

from revision2.contracts import EffectiveConfig, IDDecision, ParameterUse, PASignal, TradePlan


def _np_clip(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


class SimplePIDModelPredictiveControlBox:
    def __init__(self) -> None:
        self._entry_pids: Dict[str, PID] = {}
        self._exit_pids: Dict[str, PID] = {}
        # Rolling per-symbol confidence history backing BOTH PIDs' adaptive
        # setpoint -- see _confidence_baseline below for why this exists.
        self._confidence_history: Dict[str, Deque[float]] = {}

    def _get_pid(self, store: Dict[str, PID], symbol: str, kp: float, ki: float, kd: float,
                 target: float, clamp: float) -> PID:
        if symbol not in store:
            pid = PID(Kp=kp, Ki=ki, Kd=kd, setpoint=target, sample_time=None, output_limits=(-abs(clamp), abs(clamp)))
            store[symbol] = pid
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
        baseline = (sum(history) / len(history)) if history else current_confidence
        history.append(current_confidence)
        return baseline

    def build_plan(
        self, signal: PASignal, decision: IDDecision, entry_price: float, atr: float, config: EffectiveConfig,
    ) -> Tuple[Optional[TradePlan], Dict[str, float], List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
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
        req("pid_derivative_smoothing", "smoothing window for the PID derivative term (BoundedPID only; simple-pid derivative is single-step)", "timing_quality")

        if not decision.approved:
            return None, {}, trace

        side = "BUY" if signal.direction > 0 else "SELL"
        effective_entry = entry_price * (1 + slippage_cost_mult * 0.0005 * (1 if side == "BUY" else -1))

        stop_distance = atr * stop_mult
        target_distance = atr * profit_mult * (1 + margin_buffer)
        if stop_distance > 0:
            target_distance = max(target_distance, stop_distance * min_rr)

        # Computed ONCE per call and shared by both PIDs -- see
        # _confidence_baseline's docstring for why neither PID keeps a fixed
        # absolute target any more.
        confidence_baseline = self._confidence_baseline(signal.symbol, decision.confidence, pid_window)

        entry_pid = self._get_pid(self._entry_pids, signal.symbol, kp_entry, ki_entry, kd_entry, target=confidence_baseline, clamp=integral_clamp)
        entry_pid.setpoint = confidence_baseline  # keep in sync on every call, not just at first construction
        entry_adjustment = entry_pid(decision.confidence, dt=1)
        entry_timing_multiplier = _np_clip(1.0 - abs(entry_adjustment), 0.3, 1.0)
        effective_entry *= (1 + entry_adjustment * 0.001)

        exit_pid = self._get_pid(self._exit_pids, signal.symbol, kp_exit, ki_exit, kd_exit, target=confidence_baseline, clamp=integral_clamp)
        exit_pid.setpoint = confidence_baseline  # keep in sync on every call, not just at first construction
        exit_adjustment = exit_pid(decision.confidence, dt=1)
        exit_tightness = _np_clip(1.0 - abs(exit_adjustment), 0.5, 1.0)
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
        pid_info = {
            "entry_adjustment": entry_adjustment, "exit_adjustment": exit_adjustment,
            "entry_timing_multiplier": entry_timing_multiplier,
        }
        return plan, pid_info, trace
