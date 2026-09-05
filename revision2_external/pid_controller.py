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

Entry-PID setpoint (fixed this session, see _entry_setpoint): the entry
PID's target used to be hardcoded at 0.5 -- identical to
entry_confidence_threshold's own default, which IntelligentDiscrimination
already uses to filter out every signal the entry PID would ever see. That
made error = target - confidence mathematically <= 0 on every call, a
guaranteed one-signed error no choice of Kp/Ki/Kd can fix, which drove the
integral to its clamp and kept it there -- confirmed on real INFY data
(entry_adjustment pinned at -0.0997 / -0.100). Fixed by making the setpoint
a rolling mean of the symbol's own recent confidence instead of a fixed
absolute level, which cannot be permanently on one side of the current
reading. The exit PID's target (decision.timing_quality) was NOT changed:
it compares against a genuinely different, config-driven threshold
(exit_confidence_threshold, not the entry admission floor), so it has no
equivalent mathematical guarantee of one-signed error -- real traced values
(-0.064, -0.075) show the same directional lean but not full saturation.
Left alone rather than "fixed" on unproven suspicion; worth re-checking
against a longer real run if it turns out to saturate too.
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
        # Rolling per-symbol confidence history backing the entry PID's
        # adaptive setpoint -- see _entry_setpoint below for why this exists.
        self._entry_confidence_history: Dict[str, Deque[float]] = {}

    def _get_pid(self, store: Dict[str, PID], symbol: str, kp: float, ki: float, kd: float,
                 target: float, clamp: float) -> PID:
        if symbol not in store:
            pid = PID(Kp=kp, Ki=ki, Kd=kd, setpoint=target, sample_time=None, output_limits=(-abs(clamp), abs(clamp)))
            store[symbol] = pid
        return store[symbol]

    def _entry_setpoint(self, symbol: str, current_confidence: float, window: int) -> float:
        """The entry PID's setpoint: a rolling mean of this symbol's own
        recent decision.confidence, computed from bars BEFORE this one (the
        current reading is folded in only after the baseline is read).

        This replaces a real, provable saturation bug found by tracing real
        INFY trades: the old fixed target (0.5) was identical to
        entry_confidence_threshold's own default, and IDDecision.approved
        already guarantees decision.confidence >= entry_confidence_threshold
        for every signal that ever reaches this PID (IntelligentDiscrimination
        filters everything else out first). So error = target - confidence
        was mathematically <= 0 on 100% of calls -- not usually small, not
        occasionally reversing, but *guaranteed* one-signed by construction,
        regardless of Kp/Ki/Kd. That drives simple_pid's internal integral
        term to its clamp and keeps it pinned there, which is exactly what
        real data showed: entry_adjustment = -0.09971 and -0.100 (both
        at/within 0.0003 of -pid_integral_max_clamp) for confidences 0.5130
        and 0.5709 on the real 2023-07-13 INFY trade. No choice of fixed
        target fixes this in general: whatever absolute level is picked,
        a symbol whose approved-signal confidence happens to sit mostly on
        one side of it will still drive the same guaranteed-one-signed
        error (verified: retargeting to green_threshold's default, 0.75,
        would merely flip which rail saturates, since INFY's real confidence
        values cluster near 0.5-0.6). A trailing mean of the symbol's own
        confidence has no such guarantee: deviations from a recent average
        are positive about as often as negative for any real, noisy series,
        so the controller can actually move off its rail instead of riding
        it permanently. Reuses pid_integral_window_bars, which previously had
        no simple-pid equivalent at all (see module docstring) -- this gives
        it real, load-bearing meaning instead of being consumed only for
        parameter-coverage bookkeeping.
        """
        history = self._entry_confidence_history.setdefault(symbol, deque(maxlen=window))
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

        entry_setpoint = self._entry_setpoint(signal.symbol, decision.confidence, pid_window)
        entry_pid = self._get_pid(self._entry_pids, signal.symbol, kp_entry, ki_entry, kd_entry, target=entry_setpoint, clamp=integral_clamp)
        entry_pid.setpoint = entry_setpoint  # keep in sync on every call, not just at first construction
        entry_adjustment = entry_pid(decision.confidence, dt=1)
        entry_timing_multiplier = _np_clip(1.0 - abs(entry_adjustment), 0.3, 1.0)
        effective_entry *= (1 + entry_adjustment * 0.001)

        exit_pid = self._get_pid(self._exit_pids, signal.symbol, kp_exit, ki_exit, kd_exit, target=decision.timing_quality, clamp=integral_clamp)
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
