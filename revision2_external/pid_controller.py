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
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from simple_pid import PID

from revision2.contracts import EffectiveConfig, IDDecision, ParameterUse, PASignal, TradePlan


def _np_clip(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


class SimplePIDModelPredictiveControlBox:
    def __init__(self) -> None:
        self._entry_pids: Dict[str, PID] = {}
        self._exit_pids: Dict[str, PID] = {}

    def _get_pid(self, store: Dict[str, PID], symbol: str, kp: float, ki: float, kd: float,
                 target: float, clamp: float) -> PID:
        if symbol not in store:
            pid = PID(Kp=kp, Ki=ki, Kd=kd, setpoint=target, sample_time=None, output_limits=(-abs(clamp), abs(clamp)))
            store[symbol] = pid
        return store[symbol]

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
        # pid_integral_window_bars has no simple-pid equivalent (see module
        # docstring) -- still consumed/traced so parameter coverage stays
        # honest about what's read, but it doesn't change simple-pid's math.
        req("pid_integral_window_bars", "bounded window for the PID integral term (BoundedPID only; not applicable to simple-pid's unbounded-then-clamped design)", "timing_quality")
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

        entry_pid = self._get_pid(self._entry_pids, signal.symbol, kp_entry, ki_entry, kd_entry, target=0.5, clamp=integral_clamp)
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
