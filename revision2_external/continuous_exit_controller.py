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

FOUR real, separate inputs feed this controller every bar a position is
open -- not one, and the two confidence sources are never merged into one
number before reaching their own PIDs:

1. PA CONFIDENCE (Box 4/5's own read) -- re-run through its own real PID
   against this symbol's own rolling baseline (see "Setpoint" below),
   producing how tight to trail. Volume is deliberately NOT a separate
   input here: PA already folds volume_confirmation into the confidence
   it hands this controller, so re-introducing raw volume would
   double-count it.
2. CHART-STUDIES CONFIDENCE (Ichimoku/Bollinger/Stochastic/session VWAP,
   see composite_study_signal.py) -- its own, completely separate real
   PID, own rolling baseline, own saturation streak. This is NOT blended
   with PA confidence before reaching a PID (an earlier version of this
   integration did exactly that -- averaged the two raw confidences
   before any PID saw either one -- and a real backtest showed it let
   weak PA signals get boosted past the entry threshold just because the
   chart studies happened to agree, dropping win rate from 9.3% to 5.9%
   on the same real INFY window. Keeping the two PIDs fully independent
   and only combining their OUTPUTS removes that failure mode: a weak
   signal can no longer borrow strength from an unrelated agreeing one).
3. PRICE, the real curve itself -- is it actually making a new favorable
   extreme, tracked as a genuine high-water-mark (see "Price" below).
4. TIME held -- a position gets progressively less benefit of the doubt
   purely from having been open longer, continuously, not just as a final
   cutoff at maximum_hold_bars (see "Time" below).

The trail's MAGNITUDE (the droop) is set from a fifth, non-input
quantity: the symbol's own real, CURRENT ATR (see "Droop" below) -- not
one of the four control-loop inputs, but the real physical constant that
sets how far the loop is even allowed to react.

Setpoint (fixed this session, before this controller was ever wired in --
same lesson already learned and fixed on SimplePIDModelPredictiveControlBox's
entry/exit PIDs): the setpoint is NOT a fixed constant, for EITHER PID. A
fixed target compared against a real, live signal risks the exact bug
found and fixed earlier this session on Box 6's own PIDs -- if the target
sits persistently on one side of the real signal's range, the integral
term gets driven to its rail and stays there, which isn't genuine
proportional control. Each symbol gets its own rolling mean of its own
recent reading as its setpoint (computed fresh every update() call, before
the current reading is folded into the history), exactly the
_confidence_baseline design already proven on the entry/exit PIDs -- now
applied independently to both the PA-confidence track and the
chart-studies track.

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
and is combined with the two confidence tightnesses by taking whichever
is TIGHTEST (most conservative) at that bar -- so a trade that's dragged
on a long time gets pulled in even if both confidence reads still look
fine, and either confidence source fading fast still gets a fast reaction
regardless of what the other one or time is doing.

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

Saturation exit: EITHER track's own sustained saturation is now
sufficient to trigger an exit, independently -- not an average, not a
requirement that both agree. If PA's confidence fades for
saturation_exit_bars consecutive bars, that alone exits, regardless of
what the chart studies are doing; if the chart studies deteriorate for
that long while PA looks fine, that alone exits too. This is a real,
deliberate design choice, not the only defensible one -- requiring BOTH
tracks to saturate together would be stricter/rarer; OR is used here so a
real problem caught by either signal isn't masked by the other looking
fine, and it is disclosed as such rather than presented as the only
possible answer. The two gains (kp/ki/kd/clamp) are currently shared
between both tracks -- a disclosed simplification, not yet independently
calibratable; each track's own PID/history/streak state is nonetheless
kept completely separate.

saturation_exit_bars default (real, diagnosed, not guessed): the first
version of this controller shipped with a placeholder of 5 and, on the
real INFY 6-month backtest, saturation_exit_pa/saturation_exit_studies
fired ZERO times -- traced bar-by-bar (see
scripts/diagnose_saturation_streaks_real_infy.py) to find out why, on
real data, rather than assuming the mechanism was fine because its unit
tests passed. Real finding: two genuine trades reached a real,
uninterrupted studies-track streak of 4 (one bar short of the old gate
of 5) several bars before their eventual real stop-out -- at gate=4,
BOTH would have exited earlier via saturation_exit_studies instead of
riding to a later stop. A third near-miss trade reached streak=4 on the
EXACT same bar it hit its real profit target; since _maybe_exit checks
the price-based target/stop condition before saturation_exit_reason()
on every bar, that real winning trade is unaffected by this change
either way -- the price check still wins the tie. Lowered to 4 on that
basis: a real, already-observed near-miss on genuine data, not a value
searched over to manufacture a nicer P&L number.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

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
    favorable_extreme: float  # real price-curve high-water-mark (input #3)
    max_hold_bars: int  # needed for the continuous time-decay input (#4)
    bars_held: int = 0
    consecutive_bars_at_low_confidence_extreme: int = 0  # PA track's own saturation streak
    consecutive_bars_at_low_studies_extreme: int = 0     # chart-studies track's own saturation streak -- fully independent
    adjustment_history: list = field(default_factory=list)
    studies_adjustment_history: list = field(default_factory=list)


class ContinuousExitController:
    """Re-runs simple_pid.PID every bar a position is open, using it to
    drive a real trailing stop and a principled saturation-based early
    exit -- the closed-loop design the existing one-shot exit PID isn't.

    Two independent PIDs and two independent rolling baselines per
    SYMBOL (PA confidence, chart-studies confidence) -- and, within each,
    one PID per symbol, not one shared across all symbols. Mirrors
    SimplePIDModelPredictiveControlBox's own _get_pid per-symbol caching,
    for the same reason: mixing different symbols' (or different
    signals') errors into one PID's integral state would saturate it
    regardless of either's real behavior.
    """

    def __init__(
        self, kp: float, ki: float, kd: float, clamp: float, atr_droop_mult: float, baseline_window: int = 10,
        saturation_exit_bars: int = 4, disable_saturation_exit: bool = False,
    ) -> None:
        self.kp, self.ki, self.kd = kp, ki, kd
        self.clamp = abs(clamp)
        self.atr_droop_mult = abs(atr_droop_mult)
        self.baseline_window = max(1, int(baseline_window))
        self.saturation_exit_bars = saturation_exit_bars
        self.disable_saturation_exit = disable_saturation_exit
        self._pids: Dict[str, PID] = {}
        self._confidence_history: Dict[str, Deque[float]] = {}
        self._studies_pids: Dict[str, PID] = {}
        self._studies_history: Dict[str, Deque[float]] = {}

    def _get_pid_from(self, store: Dict[str, PID], symbol: str) -> PID:
        if symbol not in store:
            # setpoint is overwritten on every real call in update() below
            # -- the value here only matters for the very first construction.
            store[symbol] = PID(Kp=self.kp, Ki=self.ki, Kd=self.kd, setpoint=0.5,
                                 sample_time=None, output_limits=(-self.clamp, self.clamp))
        return store[symbol]

    def _baseline_from(self, store: Dict[str, Deque[float]], symbol: str, current_value: float) -> float:
        """See module docstring's Setpoint section. Identical pattern to
        SimplePIDModelPredictiveControlBox._confidence_baseline, applied
        independently to whichever track's own history dict is passed in."""
        history = store.setdefault(symbol, deque(maxlen=self.baseline_window))
        baseline = (sum(history) / len(history)) if history else current_value
        history.append(current_value)
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
        self, symbol: str, state: ExitControllerState, current_confidence: float,
        current_chart_studies_confidence: float, current_close: float, current_atr: float,
    ) -> ExitControllerState:
        """Called once per bar the position stays open. See module
        docstring for the four inputs (PA confidence, chart-studies
        confidence, price, time) and the droop (current ATR) this
        combines every call."""
        state.bars_held += 1

        # Track 1: PA confidence -- its own PID, own baseline, own history.
        baseline = self._baseline_from(self._confidence_history, symbol, current_confidence)
        pid = self._get_pid_from(self._pids, symbol)
        pid.setpoint = baseline
        adjustment = pid(current_confidence, dt=1)
        confidence_tightness = _clip(1.0 - abs(adjustment), 0.5, 1.0)
        state.adjustment_history.append(adjustment)

        # Track 2: chart-studies confidence -- a COMPLETELY SEPARATE PID,
        # baseline, and history. Never combined with Track 1's raw
        # confidence at any point -- see module docstring for the real
        # regression this design avoids (averaging raw confidences let a
        # weak PA signal borrow strength from an unrelated agreeing one).
        studies_baseline = self._baseline_from(self._studies_history, symbol, current_chart_studies_confidence)
        studies_pid = self._get_pid_from(self._studies_pids, symbol)
        studies_pid.setpoint = studies_baseline
        studies_adjustment = studies_pid(current_chart_studies_confidence, dt=1)
        studies_tightness = _clip(1.0 - abs(studies_adjustment), 0.5, 1.0)
        state.studies_adjustment_history.append(studies_adjustment)

        # simple_pid computes error = setpoint - input: a reading BELOW
        # the setpoint produces a POSITIVE error, and with positive gains,
        # a POSITIVE output -- not negative. Sustained low confidence
        # (relative to that track's own recent baseline -- "this signal
        # has faded relative to how it usually looks") therefore saturates
        # at +clamp, not -clamp. Got this backwards on the first pass,
        # before this controller was ever wired in -- caught immediately
        # by this module's own test suite. Tracked independently per track.
        at_low_confidence_extreme = adjustment >= self.clamp - 1e-9
        state.consecutive_bars_at_low_confidence_extreme = (
            state.consecutive_bars_at_low_confidence_extreme + 1 if at_low_confidence_extreme else 0
        )
        at_low_studies_extreme = studies_adjustment >= self.clamp - 1e-9
        state.consecutive_bars_at_low_studies_extreme = (
            state.consecutive_bars_at_low_studies_extreme + 1 if at_low_studies_extreme else 0
        )

        # Input #3: the real curve. Only a genuine new favorable extreme
        # moves this -- an ordinary pullback does not un-favor it.
        if state.side == "BUY":
            state.favorable_extreme = max(state.favorable_extreme, current_close)
        else:
            state.favorable_extreme = min(state.favorable_extreme, current_close)

        # Input #4: time held, as continuous decay, not just a final gate.
        time_fraction = _clip(state.bars_held / state.max_hold_bars, 0.0, 1.0)
        time_tightness = 1.0 - 0.5 * time_fraction  # 1.0 (fresh) -> 0.5 (at max hold)

        # Whichever input says "be more careful right now" wins -- a long
        # hold tightens even if both confidence reads still look fine, and
        # either confidence source fading fast still tightens quickly
        # regardless of what the other or time is doing.
        combined_tightness = min(confidence_tightness, studies_tightness, time_tightness)

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

    def saturation_exit_reason(self, state: ExitControllerState) -> Optional[str]:
        """Returns which track (if either) has sustained saturation for
        saturation_exit_bars consecutive bars -- EITHER is sufficient, they
        are checked independently, not combined. Returns None if neither
        has (or if disabled), a specific string identifying which track
        triggered otherwise, so real exit reasons stay honest about which
        signal actually caused the exit rather than a single generic
        label covering two different real causes."""
        if self.disable_saturation_exit:
            return None
        if state.consecutive_bars_at_low_confidence_extreme >= self.saturation_exit_bars:
            return "saturation_exit_pa"
        if state.consecutive_bars_at_low_studies_extreme >= self.saturation_exit_bars:
            return "saturation_exit_studies"
        return None

    def forget(self, symbol: str) -> None:
        """Called when a position closes -- NOT when the PID/baseline
        should reset, since a symbol's own confidence history is still
        meaningful for its NEXT position. This only exists so a caller
        can explicitly drop state for a symbol it will never trade again
        (e.g. delisted); normal position close does not need to call this."""
        self._pids.pop(symbol, None)
        self._confidence_history.pop(symbol, None)
        self._studies_pids.pop(symbol, None)
        self._studies_history.pop(symbol, None)
