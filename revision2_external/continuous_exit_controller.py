"""A genuine closed-loop exit controller for Box 6 -- built and tested
completely independently of ModelPredictiveControlBox /
SimplePIDModelPredictiveControlBox, which are left untouched.

The existing exit PID (in both revision2/boxes.py and
revision2_external/pid_controller.py) runs exactly once, at entry, and
bakes its output into a static stop/target price that never changes again
-- verified directly by tracing a real trade this session. That is not a
closed loop: a real PID governor (the gas-turbine analogy this design is
built from) keeps measuring the process variable and keeps correcting the
control output for as long as the process runs, converging on or tracking
a setpoint continuously, not once.

This controller re-runs the SAME simple_pid.PID machinery on every bar a
position stays open, using that bar's freshly-evaluated PA confidence as
the process variable, and uses the continuously-updated output for two
real, testable things:

1. A genuine TRAILING stop: each bar's tightness recomputes a candidate
   stop distance from CURRENT price (not the frozen entry price), and the
   stop only ever ratchets in the position's favor -- standard trailing-
   stop discipline. A stop that could loosen isn't protecting anything.
2. A principled early-exit signal: if the controller's output stays
   pinned at its positive extreme (simple_pid computes error = setpoint -
   input, so confidence persistently BELOW target drives a persistently
   POSITIVE output, not negative -- caught by this module's own test suite
   on the first run) for `saturation_exit_bars` consecutive bars, that
   sustained saturation
   IS the controller's real signal that the process has moved away from
   the setpoint and isn't coming back on its own -- exit rather than wait
   for a static price level to be touched. This is a design decision this
   module makes explicit and documents as such, not an obvious universal
   truth; it is the natural reading of "sustained pulse at one rail means
   something needs to change," but a different exit trigger (e.g. based
   on the trailing stop being hit, only) is equally defensible and easy
   to switch to -- see DISABLE_SATURATION_EXIT below.

Deliberately NOT wired into revision2_external/orchestrator.py yet. This
module is built and tested standalone first, the same way the rest of
this branch's real components were (TA-Lib PA, the from-scratch HMM,
PyPortfolioOpt sizing) before any integration decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from simple_pid import PID


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class ExitControllerState:
    """One open position's continuously-updated exit state -- separate
    from TradePlan (which stays the historical record of what was decided
    at entry); this is what the controller actually acts on bar-by-bar."""

    side: str
    entry_price: float
    original_stop_distance: float
    original_target_distance: float
    current_stop_price: float
    current_target_price: float
    bars_held: int = 0
    consecutive_bars_at_low_confidence_extreme: int = 0
    adjustment_history: list = field(default_factory=list)


class ContinuousExitController:
    """Re-runs simple_pid.PID every bar a position is open, using it to
    drive a real trailing stop and a principled saturation-based early
    exit -- the closed-loop design the existing one-shot exit PID isn't.
    """

    def __init__(
        self, kp: float, ki: float, kd: float, target: float, clamp: float,
        saturation_exit_bars: int = 5, disable_saturation_exit: bool = False,
    ) -> None:
        self.pid = PID(Kp=kp, Ki=ki, Kd=kd, setpoint=target, sample_time=None,
                        output_limits=(-abs(clamp), abs(clamp)))
        self.clamp = abs(clamp)
        self.saturation_exit_bars = saturation_exit_bars
        self.disable_saturation_exit = disable_saturation_exit

    def open_position(self, side: str, entry_price: float, stop_price: float, target_price: float) -> ExitControllerState:
        return ExitControllerState(
            side=side, entry_price=entry_price,
            original_stop_distance=abs(entry_price - stop_price),
            original_target_distance=abs(target_price - entry_price),
            current_stop_price=stop_price, current_target_price=target_price,
        )

    def update(self, state: ExitControllerState, current_confidence: float, current_close: float) -> ExitControllerState:
        """Called once per bar the position stays open. Re-runs the PID on
        the CURRENT confidence reading, then trails the stop from CURRENT
        price -- never loosening it -- and tracks sustained saturation for
        the early-exit signal."""
        adjustment = self.pid(current_confidence, dt=1)
        tightness = _clip(1.0 - abs(adjustment), 0.5, 1.0)
        state.bars_held += 1
        state.adjustment_history.append(adjustment)

        # simple_pid computes error = setpoint - input: confidence BELOW
        # the target produces a LARGE POSITIVE error, and with positive
        # gains, a positive output -- not negative. Sustained low
        # confidence (the "exit, momentum has faded" scenario) therefore
        # saturates at +clamp, not -clamp. Got this backwards on the first
        # pass -- caught immediately by this module's own test suite
        # (test_sustained_saturation_triggers_the_early_exit_signal_at_the_
        # right_bar failed with adjustment=+0.05 when -0.05 was expected).
        at_low_confidence_extreme = adjustment >= self.clamp - 1e-9
        state.consecutive_bars_at_low_confidence_extreme = (
            state.consecutive_bars_at_low_confidence_extreme + 1 if at_low_confidence_extreme else 0
        )

        stop_distance_now = state.original_stop_distance * tightness
        if state.side == "BUY":
            candidate_stop = current_close - stop_distance_now
            state.current_stop_price = max(state.current_stop_price, candidate_stop)  # only ratchet up
        else:
            candidate_stop = current_close + stop_distance_now
            state.current_stop_price = min(state.current_stop_price, candidate_stop)  # only ratchet down

        return state

    def should_exit_on_saturation(self, state: ExitControllerState) -> bool:
        if self.disable_saturation_exit:
            return False
        return state.consecutive_bars_at_low_confidence_extreme >= self.saturation_exit_bars
