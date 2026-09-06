"""A genuine closed-loop exit controller for Box 6 -- built and tested
independently of ModelPredictiveControlBox / SimplePIDModelPredictiveControlBox,
and now wired into revision2_external/orchestrator.py's position lifecycle.

The existing exit PID (in both revision2/boxes.py and
revision2_external/pid_controller.py) runs exactly once, at entry, and
bakes its output into a static stop/target price that never changes again
-- verified directly by tracing a real trade this session. That is not a
closed loop: a real PID governor (the gas-turbine analogy this design is
built from) keeps measuring the process variable and keeps correcting the
control output for as long as the process runs, converging on or tracking
a setpoint continuously, not once.

Three real, separate inputs feed this controller every bar a position is
open -- not one:

1. CONFIDENCE, from the earlier stages (PA/ID) -- re-run through a real
   PID against this symbol's own rolling baseline (see "Setpoint" below),
   producing how tight to trail. Volume is deliberately NOT a separate
   fourth input here: PA already folds volume_confirmation into the
   confidence it hands this controller, so re-introducing raw volume
   would double-count it.
2. PRICE, the real curve itself -- is it actually making a new favorable
   extreme, tracked as a genuine high-water-mark (see "Price" below).
3. TIME held -- a position gets progressively less benefit of the doubt
   purely from having been open longer, continuously, not just as a final
   cutoff at maximum_hold_bars (see "Time" below).

The trail's MAGNITUDE (the droop) is set from a fourth, non-input
quantity: the symbol's own real, CURRENT ATR (see "Droop" below) -- not
one of the three control-loop inputs, but the real physical constant that
sets how far the loop is even allowed to react.

Setpoint (fixed this session, before this controller was ever wired in --
same lesson already learned and fixed on SimplePIDModelPredictiveControlBox's
entry/exit PIDs): the setpoint is NOT a fixed constant. A fixed target
compared against a real, live confidence signal risks the exact bug found
and fixed earlier this session on Box 6's own PIDs -- if the target sits
persistently on one side of the real signal's range, the integral term
gets driven to its rail and stays there, which isn't genuine proportional
control. Each symbol gets its own rolling mean of its own recent
exit_confidence as its setpoint (computed fresh every update() call,
before the current reading is folded into the history), exactly the
_confidence_baseline design already proven on the entry/exit PIDs.

Price (the real bug this fixes, found on the first real backtest after
wiring this in): the first version measured the trail distance from
current_close directly. That means a bar where price merely pulls back
after a real high, OR a bar where confidence alone dips (even with price
flat), could push the candidate stop up regardless of whether price had
actually made any real progress -- ratcheting on noise, not on the curve.
Verified against real INFY data: that version stopped out 197/197 real
6-month trades via stop/stop_gap alone (0.5% win rate, median 0-minute
holds). Fixed by tracking a genuine high-water-mark (favorable_extreme)
and measuring the trail distance from THAT, so an ordinary pullback the
bar after a new high doesn't retroactively tighten anything -- only
genuine new progress on the price curve does.

Time: bars_held used to only gate WHETHER to check exit conditions
(minimum_hold_bars) and force a final exit (maximum_hold_bars) -- it never
fed the control law itself. time_tightness now decays continuously from
1.0 (a fresh position) toward 0.5 as bars_held approaches max_hold_bars,
and is combined with confidence_tightness by taking whichever is TIGHTER
(more conservative) at that bar -- so a trade that's dragged on a long
time gets pulled in even if confidence alone still looks fine, and a
trade with fading confidence early still gets the existing fast reaction.

Droop: real control-loop droop is tuned relative to the plant's own
physical constants (a mechanical governor's moment of inertia, damping),
not picked arbitrarily -- those constants set how big the plant's natural
motion is and how it responds to a given correction. This system's
equivalent of that "how big" constant is real and already exists: ATR
(signal.volatility * price) is a genuine physical measurement of how much
this specific symbol naturally moves per bar, at CURRENT market
conditions -- not a guessed percentage (verified on real INFY data: mean
ATR/price is 0.072%, nowhere near a generic 4-10% guess, because this is
1-minute data, not daily). There is no equivalent "how fast it decays"
constant (a mechanical time-constant analog) anywhere in this codebase
yet -- that would need real, fresh statistical analysis of the price
series' own autocorrelation/reversion behavior, deliberately not
approximated here. The droop distance is current ATR (re-measured every
bar, not the frozen entry-time ATR) times atr_droop_mult, reusing
stop_loss_atr_mult's own registry-calibratable value rather than
introducing a new parameter.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict

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
    original_target_distance: float
    current_stop_price: float
    current_target_price: float
    favorable_extreme: float  # real price-curve high-water-mark (input #2)
    max_hold_bars: int  # needed for the continuous time-decay input (#3)
    bars_held: int = 0
    consecutive_bars_at_low_confidence_extreme: int = 0
    adjustment_history: list = field(default_factory=list)


class ContinuousExitController:
    """Re-runs simple_pid.PID every bar a position is open, using it to
    drive a real trailing stop and a principled saturation-based early
    exit -- the closed-loop design the existing one-shot exit PID isn't.

    One PID and one rolling confidence baseline per SYMBOL (not one
    shared across all symbols, and not one per position) -- mirrors
    SimplePIDModelPredictiveControlBox's own _get_pid per-symbol caching,
    for the same reason: mixing different symbols' errors into one PID's
    integral state would saturate it regardless of either symbol's real
    behavior.
    """

    def __init__(
        self, kp: float, ki: float, kd: float, clamp: float, atr_droop_mult: float, baseline_window: int = 10,
        saturation_exit_bars: int = 5, disable_saturation_exit: bool = False,
    ) -> None:
        self.kp, self.ki, self.kd = kp, ki, kd
        self.clamp = abs(clamp)
        self.atr_droop_mult = abs(atr_droop_mult)
        self.baseline_window = max(1, int(baseline_window))
        self.saturation_exit_bars = saturation_exit_bars
        self.disable_saturation_exit = disable_saturation_exit
        self._pids: Dict[str, PID] = {}
        self._confidence_history: Dict[str, Deque[float]] = {}

    def _get_pid(self, symbol: str) -> PID:
        if symbol not in self._pids:
            # setpoint is overwritten on every real call in update() below
            # -- the value here only matters for the very first construction.
            self._pids[symbol] = PID(Kp=self.kp, Ki=self.ki, Kd=self.kd, setpoint=0.5,
                                      sample_time=None, output_limits=(-self.clamp, self.clamp))
        return self._pids[symbol]

    def _confidence_baseline(self, symbol: str, current_confidence: float) -> float:
        """See module docstring's Setpoint section. Identical pattern to
        SimplePIDModelPredictiveControlBox._confidence_baseline."""
        history = self._confidence_history.setdefault(symbol, deque(maxlen=self.baseline_window))
        baseline = (sum(history) / len(history)) if history else current_confidence
        history.append(current_confidence)
        return baseline

    def open_position(
        self, side: str, entry_price: float, stop_price: float, target_price: float, max_hold_bars: int,
    ) -> ExitControllerState:
        return ExitControllerState(
            side=side, entry_price=entry_price,
            original_target_distance=abs(target_price - entry_price),
            current_stop_price=stop_price, current_target_price=target_price,
            favorable_extreme=entry_price, max_hold_bars=max(1, int(max_hold_bars)),
        )

    def update(
        self, symbol: str, state: ExitControllerState, current_confidence: float, current_close: float, current_atr: float,
    ) -> ExitControllerState:
        """Called once per bar the position stays open. See module
        docstring for the three inputs (confidence, price, time) and the
        droop (current ATR) this combines every call."""
        baseline = self._confidence_baseline(symbol, current_confidence)
        pid = self._get_pid(symbol)
        pid.setpoint = baseline
        adjustment = pid(current_confidence, dt=1)
        confidence_tightness = _clip(1.0 - abs(adjustment), 0.5, 1.0)
        state.bars_held += 1
        state.adjustment_history.append(adjustment)

        # simple_pid computes error = setpoint - input: confidence BELOW
        # the setpoint produces a POSITIVE error, and with positive gains,
        # a POSITIVE output -- not negative. Sustained low confidence
        # (relative to this symbol's own recent baseline -- the "exit,
        # momentum has faded relative to how this symbol usually looks"
        # scenario) therefore saturates at +clamp, not -clamp. Got this
        # backwards on the first pass, before this controller was ever
        # wired in -- caught immediately by this module's own test suite.
        at_low_confidence_extreme = adjustment >= self.clamp - 1e-9
        state.consecutive_bars_at_low_confidence_extreme = (
            state.consecutive_bars_at_low_confidence_extreme + 1 if at_low_confidence_extreme else 0
        )

        # Input #2: the real curve. Only a genuine new favorable extreme
        # moves this -- an ordinary pullback does not un-favor it.
        if state.side == "BUY":
            state.favorable_extreme = max(state.favorable_extreme, current_close)
        else:
            state.favorable_extreme = min(state.favorable_extreme, current_close)

        # Input #3: time held, as continuous decay, not just a final gate.
        time_fraction = _clip(state.bars_held / state.max_hold_bars, 0.0, 1.0)
        time_tightness = 1.0 - 0.5 * time_fraction  # 1.0 (fresh) -> 0.5 (at max hold)

        # Whichever input says "be more careful right now" wins -- a long
        # hold tightens even if confidence still looks fine, and fading
        # confidence still tightens fast even on a fresh position.
        combined_tightness = min(confidence_tightness, time_tightness)

        # Droop: current ATR, not the frozen entry-time distance -- see
        # module docstring's "Droop" section.
        droop_distance = current_atr * self.atr_droop_mult
        stop_distance_now = droop_distance * combined_tightness
        if state.side == "BUY":
            candidate_stop = state.favorable_extreme - stop_distance_now
            state.current_stop_price = max(state.current_stop_price, candidate_stop)  # only ratchet up
        else:
            candidate_stop = state.favorable_extreme + stop_distance_now
            state.current_stop_price = min(state.current_stop_price, candidate_stop)  # only ratchet down

        return state

    def should_exit_on_saturation(self, state: ExitControllerState) -> bool:
        if self.disable_saturation_exit:
            return False
        return state.consecutive_bars_at_low_confidence_extreme >= self.saturation_exit_bars

    def forget(self, symbol: str) -> None:
        """Called when a position closes -- NOT when the PID/baseline
        should reset, since a symbol's own confidence history is still
        meaningful for its NEXT position. This only exists so a caller
        can explicitly drop state for a symbol it will never trade again
        (e.g. delisted); normal position close does not need to call this."""
        self._pids.pop(symbol, None)
        self._confidence_history.pop(symbol, None)
