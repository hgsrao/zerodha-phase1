"""A composite multi-study signal (Ichimoku, Bollinger Bands, Stochastic,
session VWAP) whose per-study VOTE WEIGHTS are set by real, closed-loop PID
controllers instead of fixed constants -- built and tested completely
standalone, NOT yet wired into Box 4/Box 6, the same way ContinuousExitController
was built and proven before any integration decision this session.

Real prior art found this session, in a completely different, older part
of this project (/home/shrinivas/ECS_GitHub/CHART_STUDIES_SIGNAL_RULE_20260824.md):
a live monitor voted 5 studies (Ichimoku, Bollinger Bands, Stochastic
Momentum Index, session VWAP, anchored VWAP) with a FIXED threshold
(+3 of 5 to enter, <=0 to exit) -- every study counted equally, forever,
regardless of which ones were actually working. Anchored VWAP was later
excluded entirely because it "stayed BULLISH the entire session... while
the 4 faster studies genuinely flipped bearish, silently capping how
bearish the composite could read" -- a real, documented failure of fixed
weighting. This module replaces "fixed weight forever" with "each study's
weight is continuously adjusted by its own PID, based on how correct that
study's OWN votes have actually been recently" -- the same real-feedback
principle already applied to Box 6's PIDs this session. Anchored VWAP is
deliberately excluded here too, for the identical, already-documented
reason -- it has no natural "hit rate" over a short horizon by design
(it never resets), so it can't be graded the same way as the other four.

Four studies, real formulas, not stubs:
  - Ichimoku (9, 26, 52): Tenkan-sen/Kijun-sen midpoints, Senkou Span A/B
    cloud (looked back 26 bars, matching how the cloud is actually
    plotted -- no forward projection, which would be lookahead).
    BULLISH: close above both spans AND Tenkan > Kijun. BEARISH: close
    below both spans AND Tenkan < Kijun. Else NEUTRAL.
  - Bollinger Bands (20, 2sigma) via TA-Lib's real BBANDS: BULLISH if
    close > basis (20-SMA), BEARISH if below -- same simplified
    "position relative to mean" read the original monitor used.
  - Stochastic (5,3,3) via TA-Lib's real STOCH (substituted for the
    original's Stochastic Momentum Index -- TA-Lib has no SMI function;
    this is a disclosed substitution, not a silent one): BULLISH if
    %K > %D, BEARISH if %K < %D.
  - Session VWAP: real cumulative (price*volume)/volume, resetting at
    each real calendar-day boundary found in the bar timestamps. BULLISH
    if close > VWAP, BEARISH if below.

The PID weighting mechanism (real feedback, not a fixed vote count):
each study's process variable is its own rolling HIT RATE -- was its vote
N bars ago (a fixed grading horizon) actually followed by price moving in
the voted direction? Graded only once those N bars have genuinely
elapsed (each study keeps its own short vote history for this -- no
lookahead: a vote is never graded using data from its own future beyond
what has actually happened by the current bar). The setpoint is NOT a
fixed 50% -- same lesson already learned and fixed on Box 6's own PIDs
this session: a fixed setpoint compared against a real, live-varying
signal risks permanent one-signed error and integral saturation. Instead
each study's setpoint is the CROSS-STUDY rolling average hit rate at that
bar -- "is this study doing better or worse than the other studies are
doing right now" -- which is what should actually drive relative
re-weighting, and which cannot be persistently one-signed the way a
fixed 50% could be if every study is simultaneously doing well or badly
in some regime.

Real sign-convention bug caught by this module's own test suite on the
first run (the same class of bug already caught once this session on
ContinuousExitController): simple_pid computes error = setpoint - input,
so a study doing BETTER than the cross-study baseline (hit_rate >
baseline) produces a NEGATIVE error and, with positive gains, a NEGATIVE
raw output -- not positive. The first version applied that raw output
directly to the weight, so the study with the best real hit rate (1.00)
ended the test run pinned at the weight FLOOR, not the ceiling. Fixed by
negating the PID's output before applying it to weight -- a study
outperforming its peers must gain weight, exactly the opposite of what
the unnegated sign produced.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List

import numpy as np
import pandas as pd
import talib
from simple_pid import PID

STUDY_NAMES = ("ichimoku", "bollinger", "stochastic", "session_vwap")

_GRADING_HORIZON = 5   # bars after a vote before it's graded as hit/miss
_HIT_RATE_WINDOW = 20  # bars of graded history averaged into a study's own hit rate
_MIN_WEIGHT = 0.05     # a study can be down-weighted hard, never to zero (never silently deleted)
_MAX_WEIGHT = 0.60     # and never let one study dominate the composite entirely


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _ichimoku_vote(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> int:
    """Real Ichimoku (9, 26, 52), no forward projection -- the cloud read
    at the CURRENT bar uses spans computed from data 26 bars ago, exactly
    matching how the displaced cloud actually lines up against price;
    projecting forward would mean reading today's cloud from data that
    doesn't exist yet."""
    if len(close) < 52 + 26:
        return 0
    tenkan = (pd.Series(high).rolling(9).max() + pd.Series(low).rolling(9).min()) / 2
    kijun = (pd.Series(high).rolling(26).max() + pd.Series(low).rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((pd.Series(high).rolling(52).max() + pd.Series(low).rolling(52).min()) / 2).shift(26)
    c, t, k, sa, sb = close[-1], tenkan.iloc[-1], kijun.iloc[-1], senkou_a.iloc[-1], senkou_b.iloc[-1]
    if any(pd.isna(x) for x in (t, k, sa, sb)):
        return 0
    if c > max(sa, sb) and t > k:
        return 1
    if c < min(sa, sb) and t < k:
        return -1
    return 0


def _bollinger_vote(close: np.ndarray) -> int:
    if len(close) < 20:
        return 0
    upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
    if pd.isna(middle[-1]):
        return 0
    return 1 if close[-1] > middle[-1] else (-1 if close[-1] < middle[-1] else 0)


def _stochastic_vote(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> int:
    if len(close) < 5 + 3 + 3:
        return 0
    slowk, slowd = talib.STOCH(high, low, close, fastk_period=5, slowk_period=3, slowd_period=3)
    if pd.isna(slowk[-1]) or pd.isna(slowd[-1]):
        return 0
    return 1 if slowk[-1] > slowd[-1] else (-1 if slowk[-1] < slowd[-1] else 0)


def _session_vwap_vote(timestamps: pd.Series, close: np.ndarray, volume: np.ndarray) -> int:
    dates = pd.to_datetime(timestamps).dt.date
    today = dates.iloc[-1]
    mask = (dates == today).to_numpy()
    session_close = close[mask]
    session_volume = np.maximum(volume[mask], 1e-9)  # real volume is never negative; guards a real zero-volume bar
    vwap = float(np.sum(session_close * session_volume) / np.sum(session_volume))
    return 1 if close[-1] > vwap else (-1 if close[-1] < vwap else 0)


@dataclass
class _StudyState:
    weight: float
    pid: PID
    vote_history: Deque[int] = field(default_factory=lambda: deque(maxlen=_GRADING_HORIZON + 1))
    close_history: Deque[float] = field(default_factory=lambda: deque(maxlen=_GRADING_HORIZON + 1))
    hit_history: Deque[int] = field(default_factory=lambda: deque(maxlen=_HIT_RATE_WINDOW))

    @property
    def hit_rate(self) -> float:
        return (sum(self.hit_history) / len(self.hit_history)) if self.hit_history else 0.5


class CompositeStudySignal:
    """One instance per symbol's worth of state, same per-symbol isolation
    principle as every other PID this session (SimplePIDModelPredictiveControlBox,
    ContinuousExitController) -- mixing symbols would mix their hit-rate
    feedback and saturate weights regardless of either symbol's real behavior."""

    def __init__(self, kp: float = 0.15, ki: float = 0.05, kd: float = 0.05, clamp: float = 0.15) -> None:
        self.kp, self.ki, self.kd, self.clamp = kp, ki, kd, abs(clamp)
        self._symbols: Dict[str, Dict[str, _StudyState]] = {}

    def _get_symbol_state(self, symbol: str) -> Dict[str, _StudyState]:
        if symbol not in self._symbols:
            self._symbols[symbol] = {
                name: _StudyState(
                    weight=1.0 / len(STUDY_NAMES),
                    pid=PID(Kp=self.kp, Ki=self.ki, Kd=self.kd, setpoint=0.5, sample_time=None,
                            output_limits=(-self.clamp, self.clamp)),
                )
                for name in STUDY_NAMES
            }
        return self._symbols[symbol]

    def evaluate(self, symbol: str, bars: pd.DataFrame) -> Dict[str, object]:
        """bars: real OHLCV up to and including the current bar (same
        trailing-window convention as MarketSnapshot elsewhere in this
        codebase) -- the last row is "now"."""
        states = self._get_symbol_state(symbol)
        high, low, close = bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float), bars["close"].to_numpy(dtype=float)
        volume = bars["volume"].to_numpy(dtype=float)

        current_votes = {
            "ichimoku": _ichimoku_vote(high, low, close),
            "bollinger": _bollinger_vote(close),
            "stochastic": _stochastic_vote(high, low, close),
            "session_vwap": _session_vwap_vote(bars["timestamp"], close, volume),
        }
        current_close = float(close[-1])

        # Grade each study's vote from _GRADING_HORIZON bars ago, now that
        # horizon has genuinely elapsed -- no lookahead: only ever compares
        # a past vote to price movement that has actually happened by now.
        for name, state in states.items():
            state.vote_history.append(current_votes[name])
            state.close_history.append(current_close)
            if len(state.vote_history) > _GRADING_HORIZON:
                past_vote = state.vote_history[0]
                past_close = state.close_history[0]
                if past_vote != 0:
                    moved_up = current_close > past_close
                    hit = 1 if (past_vote == 1) == moved_up else 0
                    state.hit_history.append(hit)

        # Cross-study rolling average hit rate -- the adaptive setpoint
        # (see module docstring for why this isn't a fixed 50%).
        cross_study_baseline = sum(s.hit_rate for s in states.values()) / len(states)

        weights = {}
        for name, state in states.items():
            state.pid.setpoint = cross_study_baseline
            # simple_pid computes error = setpoint - input: a study doing
            # BETTER than the cross-study baseline (hit_rate > baseline)
            # produces a NEGATIVE error, and with positive gains, a
            # NEGATIVE raw output -- not positive. Caught by this module's
            # own test suite on the first run (the study with a real 1.00
            # hit rate ended up at the weight FLOOR, not the ceiling).
            # Negate before applying: a study outperforming its peers must
            # gain weight, not lose it.
            adjustment = -state.pid(state.hit_rate, dt=1)
            base = 1.0 / len(STUDY_NAMES)
            state.weight = _clip(base + adjustment, _MIN_WEIGHT, _MAX_WEIGHT)
            weights[name] = state.weight

        total_weight = sum(weights.values()) or 1.0
        weighted_score = sum(current_votes[n] * weights[n] for n in STUDY_NAMES) / total_weight  # in [-1, 1]
        confidence = (weighted_score + 1.0) / 2.0  # mapped to [0, 1], same scale as PASignal.confidence
        direction = 1 if weighted_score > 0 else (-1 if weighted_score < 0 else 0)

        return {
            "confidence": confidence, "direction": direction, "weighted_score": weighted_score,
            "votes": dict(current_votes), "weights": dict(weights),
            "hit_rates": {n: states[n].hit_rate for n in STUDY_NAMES},
        }
