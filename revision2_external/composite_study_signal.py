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

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Tuple

import numpy as np
import pandas as pd
import talib
from simple_pid import PID

from revision2_external.bb05_bb06_parameters import default_config, require

STUDY_NAMES = ("ichimoku", "bollinger", "stochastic", "session_vwap")

# Grading horizon, hit-rate window and the PID gains/clamp are registry-owned
# (studies_* in canonical_parameter_registry.py).  The weight bounds below are
# FIXED_SAFETY_ENVELOPE values, deliberately not parameters.
MIN_WEIGHT = 0.05  # a study can be down-weighted hard, never to zero (never silently deleted)
MAX_WEIGHT = 0.60  # and never let one study dominate the composite entirely
# Backward-compatible alias DERIVED from the registry default (not an owner);
# existing tests use it as the default grading horizon.
_GRADING_HORIZON = require(default_config(), "studies_grading_horizon_bars")


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


_STATE_VERSION = 1
# Construction contract of every study PID (see _get_symbol_state / evaluate): dt=1 is always explicit.
_PID_MODE = dict(dt=1, sample_time=None, auto_mode=True, proportional_on_measurement=False,
                 differential_on_measurement=True, error_map=None)
_PID_STATE_FIELDS = {"tunings", "setpoint", "output_limits", "proportional", "integral", "derivative",
                     "last_input", "last_error", "last_output"}
_STUDY_FIELDS = {"weight", "pid", "vote_history", "close_history", "hit_history"}
_STATE_FIELDS = {"version", "config_hash", "simple_pid_version", "pid_mode", "grading_horizon",
                 "hit_rate_window", "symbols"}


def _simple_pid_version() -> str:
    from importlib.metadata import version
    return version("simple_pid")


def _state_real(value: object, what: str) -> float:
    """Finite real number; bool/str/None/NaN/inf are never accepted as numeric studies state."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid studies state: {what} is not a number")
    try:
        number = float(value)  # an oversized JSON int raises OverflowError here
        finite = math.isfinite(number)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"Invalid studies state: {what} is not finite") from None
    if not finite:
        raise ValueError(f"Invalid studies state: {what} is not finite")
    return number


def _state_dict(value: object, keys: set, what: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Invalid studies state: {what} schema")
    return value


def _state_pair(value: object, count: int, what: str) -> List[float]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"Invalid studies state: {what} shape")
    return [_state_real(v, what) for v in value]


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
    vwap = _session_vwap_value(timestamps, close, volume)
    return 1 if close[-1] > vwap else (-1 if close[-1] < vwap else 0)


def _session_vwap_value(timestamps: pd.Series, close: np.ndarray, volume: np.ndarray) -> float:
    """Return the causal, current-session VWAP used by the VWAP vote."""
    dates = pd.to_datetime(timestamps).dt.date
    today = dates.iloc[-1]
    mask = (dates == today).to_numpy()
    session_close = close[mask]
    session_volume = np.maximum(volume[mask], 1e-9)  # real volume is never negative; guards a real zero-volume bar
    return float(np.sum(session_close * session_volume) / np.sum(session_volume))


def _indicator_inputs(timestamps: pd.Series, high: np.ndarray, low: np.ndarray,
                      close: np.ndarray, volume: np.ndarray) -> Dict[str, Dict[str, float | bool | None]]:
    """Expose the exact causal inputs behind each study vote for audit only.

    This deliberately duplicates no decision logic: the votes themselves still
    come from the established helpers above.  The values below make a replay
    trace explainable without allowing telemetry to alter a trade.
    """
    current_close = float(close[-1])
    ichimoku: Dict[str, float | bool | None] = {"close": current_close, "ready": False}
    if len(close) >= 78:
        tenkan = (pd.Series(high).rolling(9).max() + pd.Series(low).rolling(9).min()) / 2
        kijun = (pd.Series(high).rolling(26).max() + pd.Series(low).rolling(26).min()) / 2
        span_a = ((tenkan + kijun) / 2).shift(26)
        span_b = ((pd.Series(high).rolling(52).max() + pd.Series(low).rolling(52).min()) / 2).shift(26)
        values = {"tenkan": tenkan.iloc[-1], "kijun": kijun.iloc[-1],
                  "span_a": span_a.iloc[-1], "span_b": span_b.iloc[-1]}
        if not any(pd.isna(value) for value in values.values()):
            ichimoku.update({key: float(value) for key, value in values.items()})
            ichimoku["ready"] = True

    bollinger: Dict[str, float | bool | None] = {"close": current_close, "ready": False}
    if len(close) >= 20:
        upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        if not any(pd.isna(value) for value in (upper[-1], middle[-1], lower[-1])):
            bollinger.update({"upper": float(upper[-1]), "middle": float(middle[-1]), "lower": float(lower[-1]), "ready": True})

    stochastic: Dict[str, float | bool | None] = {"close": current_close, "ready": False}
    if len(close) >= 11:
        slowk, slowd = talib.STOCH(high, low, close, fastk_period=5, slowk_period=3, slowd_period=3)
        if not any(pd.isna(value) for value in (slowk[-1], slowd[-1])):
            stochastic.update({"slowk": float(slowk[-1]), "slowd": float(slowd[-1]), "ready": True})

    return {
        "ichimoku": ichimoku,
        "bollinger": bollinger,
        "stochastic": stochastic,
        "session_vwap": {"close": current_close, "vwap": _session_vwap_value(timestamps, close, volume), "ready": True},
    }


@dataclass
class _StudyState:
    weight: float
    pid: PID
    vote_history: Deque[int]
    close_history: Deque[float]
    hit_history: Deque[int]

    @property
    def hit_rate(self) -> float:
        return (sum(self.hit_history) / len(self.hit_history)) if self.hit_history else 0.5


class CompositeStudySignal:
    """One instance per symbol's worth of state, same per-symbol isolation
    principle as every other PID this session (SimplePIDModelPredictiveControlBox,
    ContinuousExitController) -- mixing symbols would mix their hit-rate
    feedback and saturate weights regardless of either symbol's real behavior."""

    _NAMES = ("studies_pid_kp", "studies_pid_ki", "studies_pid_kd", "studies_pid_output_clamp",
              "studies_grading_horizon_bars", "studies_hit_rate_window_bars")

    def __init__(self, kp: float | None = None, ki: float | None = None, kd: float | None = None,
                 clamp: float | None = None, config=None) -> None:
        """Explicit kp/ki/kd/clamp override the config (legacy call style);
        otherwise every control comes from the canonical registry."""
        self._symbols: Dict[str, Dict[str, _StudyState]] = {}
        self.configure(config if config is not None else default_config())
        if kp is not None:
            self.kp = kp
        if ki is not None:
            self.ki = ki
        if kd is not None:
            self.kd = kd
        if clamp is not None:
            self.clamp = abs(clamp)

    def configure(self, config) -> None:
        """Refresh controls from ``config`` WITHOUT rebuilding any state.

        Existing PID objects keep their integral/derivative memory; only their
        tunings and output limits change.  Vote/close/hit histories are resized
        in place keeping the newest entries (already-elapsed data only, so no
        future information can enter).  All values validate before any mutation.
        """
        values = {name: require(config, name) for name in self._NAMES}
        self.config = config
        self.kp, self.ki, self.kd = (values[n] for n in self._NAMES[:3])
        self.clamp = abs(values["studies_pid_output_clamp"])
        self.grading_horizon = values["studies_grading_horizon_bars"]
        self.hit_rate_window = values["studies_hit_rate_window_bars"]
        for states in self._symbols.values():
            for state in states.values():
                state.pid.tunings = (self.kp, self.ki, self.kd)
                state.pid.output_limits = (-self.clamp, self.clamp)
                state.vote_history = deque(state.vote_history, maxlen=self.grading_horizon + 1)
                state.close_history = deque(state.close_history, maxlen=self.grading_horizon + 1)
                state.hit_history = deque(state.hit_history, maxlen=self.hit_rate_window)

    # ---- exact state continuity (studies_v1) ---------------------------------------------------
    # Only semantic controller state is persisted (PID._last_time is a process-local monotonic stamp,
    # unused because dt=1 is always explicit).  Config is bound by config_hash and *validated*, never
    # reapplied: a legacy kp/ki/kd/clamp override that differs from the config is foreign state.

    def _bound_controls(self) -> Tuple[float, float, float, float]:
        values = {name: require(self.config, name) for name in self._NAMES}
        controls = (values["studies_pid_kp"], values["studies_pid_ki"], values["studies_pid_kd"],
                    abs(values["studies_pid_output_clamp"]))
        if ((self.kp, self.ki, self.kd, self.clamp) != controls
                or self.grading_horizon != values["studies_grading_horizon_bars"]
                or self.hit_rate_window != values["studies_hit_rate_window_bars"]):
            raise ValueError("Studies state requires controls equal to the bound config (legacy override active)")
        for horizon in (self.grading_horizon, self.hit_rate_window):
            if type(horizon) is not int or horizon < 1:
                raise ValueError("Invalid studies state: configured horizon/window")
        return controls

    def export_state(self) -> Dict[str, object]:
        """JSON-safe ``studies_v1`` payload; validated here so an invalid state never reaches disk."""
        symbols: Dict[str, object] = {}
        for symbol, states in self._symbols.items():
            studies: Dict[str, object] = {}
            for name, state in states.items():
                pid = state.pid
                live_mode = dict(sample_time=pid.sample_time, auto_mode=pid.auto_mode,
                                 proportional_on_measurement=pid.proportional_on_measurement,
                                 differential_on_measurement=pid.differential_on_measurement,
                                 error_map=pid.error_map)
                if live_mode != {k: v for k, v in _PID_MODE.items() if k != "dt"} or any(
                        type(live_mode[k]) is not type(_PID_MODE[k]) for k in live_mode):
                    raise ValueError("Invalid studies state: live PID mode differs from the studies contract")
                proportional, integral, derivative = pid.components
                studies[name] = dict(
                    weight=state.weight,
                    pid=dict(tunings=list(pid.tunings), setpoint=pid.setpoint, output_limits=list(pid.output_limits),
                             proportional=proportional, integral=integral, derivative=derivative,
                             last_input=pid._last_input, last_error=pid._last_error, last_output=pid._last_output),
                    vote_history=list(state.vote_history), close_history=list(state.close_history),
                    hit_history=list(state.hit_history))
            symbols[symbol] = studies
        payload = dict(version=_STATE_VERSION, config_hash=self.config.config_hash,
                       simple_pid_version=_simple_pid_version(), pid_mode=dict(_PID_MODE),
                       grading_horizon=self.grading_horizon, hit_rate_window=self.hit_rate_window,
                       symbols=symbols)
        self._stage_state(payload)
        return payload

    def _stage_state(self, payload: object) -> Dict[str, Dict[str, _StudyState]]:
        """Strictly validate and rebuild into NEW objects; touches no live attribute."""
        kp, ki, kd, clamp = self._bound_controls()
        payload = _state_dict(payload, _STATE_FIELDS, "payload")
        if type(payload["version"]) is not int or payload["version"] != _STATE_VERSION:
            raise ValueError("Unsupported studies state version")
        if type(payload["config_hash"]) is not str or payload["config_hash"] != self.config.config_hash:
            raise ValueError("Studies state was saved under a different config")
        if type(payload["simple_pid_version"]) is not str or payload["simple_pid_version"] != _simple_pid_version():
            raise ValueError("Studies state was saved under a different simple_pid version")
        mode = payload["pid_mode"]
        if (not isinstance(mode, dict) or set(mode) != set(_PID_MODE)
                or any(type(mode[k]) is not type(v) or mode[k] != v for k, v in _PID_MODE.items())):
            raise ValueError("Studies state mode differs from the studies contract")
        for key, live in (("grading_horizon", self.grading_horizon), ("hit_rate_window", self.hit_rate_window)):
            if type(payload[key]) is not int or payload[key] != live:
                raise ValueError(f"Invalid studies state: {key} differs from config")
        raw_symbols = payload["symbols"]
        if not isinstance(raw_symbols, dict):
            raise ValueError("Invalid studies state: symbols")

        horizon_len, window = self.grading_horizon + 1, self.hit_rate_window
        staged: Dict[str, Dict[str, _StudyState]] = {}
        for symbol, raw_studies in raw_symbols.items():
            if type(symbol) is not str or not symbol:
                raise ValueError("Invalid studies state: symbol")
            raw_studies = _state_dict(raw_studies, set(STUDY_NAMES), "symbol studies")
            states: Dict[str, _StudyState] = {}
            for name in STUDY_NAMES:
                entry = _state_dict(raw_studies[name], _STUDY_FIELDS, "study")
                weight = _state_real(entry["weight"], "weight")
                if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
                    raise ValueError("Invalid studies state: weight outside configured bounds")
                votes, closes, hits = entry["vote_history"], entry["close_history"], entry["hit_history"]
                if not all(isinstance(h, list) for h in (votes, closes, hits)):
                    raise ValueError("Invalid studies state: history shape")
                if len(votes) != len(closes) or len(votes) > horizon_len or len(hits) > window:
                    raise ValueError("Invalid studies state: history lengths")
                if any(type(v) is not int or v not in (-1, 0, 1) for v in votes):
                    raise ValueError("Invalid studies state: vote")
                if any(type(h) is not int or h not in (0, 1) for h in hits):
                    raise ValueError("Invalid studies state: hit")
                closes = [_state_real(c, "close") for c in closes]
                if any(c <= 0 for c in closes):
                    raise ValueError("Invalid studies state: close must be positive")
                if hits and len(votes) != horizon_len:
                    raise ValueError("Invalid studies state: graded hits before the horizon elapsed")

                p = _state_dict(entry["pid"], _PID_STATE_FIELDS, "study PID")
                if (_state_pair(p["tunings"], 3, "tunings") != [kp, ki, kd]
                        or _state_pair(p["output_limits"], 2, "output_limits") != [-clamp, clamp]):
                    raise ValueError("Studies PID gains/limits differ from the configured controls")
                values = {n: _state_real(p[n], n) for n in ("setpoint", "proportional", "integral", "derivative")}
                last = {n: (None if p[n] is None else _state_real(p[n], n))
                        for n in ("last_input", "last_error", "last_output")}
                hit_rate = (sum(hits) / len(hits)) if hits else 0.5
                if all(v is None for v in last.values()):
                    # Created but never stepped: nothing may have been learned yet.
                    if (votes or hits or weight != 1.0 / len(STUDY_NAMES) or values["setpoint"] != 0.5
                            or any(values[n] != 0 for n in ("proportional", "integral", "derivative"))):
                        raise ValueError("Invalid studies state: unstepped PID carries state")
                else:
                    if any(v is None for v in last.values()) or not votes:
                        raise ValueError("Invalid studies state: incomplete PID memory")
                    if last["last_input"] != hit_rate or last["last_error"] != values["setpoint"] - hit_rate:
                        raise ValueError("Invalid studies state: PID memory incoherent with hit history")
                    if abs(last["last_output"]) > clamp:
                        raise ValueError("Invalid studies state: PID output exceeds the configured clamp")
                if abs(values["integral"]) > clamp:
                    raise ValueError("Invalid studies state: PID integral exceeds the configured clamp")
                pid = PID(Kp=kp, Ki=ki, Kd=kd, setpoint=values["setpoint"], sample_time=None,
                          output_limits=(-clamp, clamp))
                pid._proportional, pid._integral, pid._derivative = (
                    values["proportional"], values["integral"], values["derivative"])
                pid._last_input, pid._last_error, pid._last_output = (
                    last["last_input"], last["last_error"], last["last_output"])
                states[name] = _StudyState(
                    weight=weight, pid=pid, vote_history=deque(votes, maxlen=horizon_len),
                    close_history=deque(closes, maxlen=horizon_len), hit_history=deque(hits, maxlen=window))
            # All four studies are stepped together on the same bars with the same close.
            first = states[STUDY_NAMES[0]]
            if any(list(s.close_history) != list(first.close_history)
                   or len(s.vote_history) != len(first.vote_history)
                   or s.pid.setpoint != first.pid.setpoint for s in states.values()):
                raise ValueError("Invalid studies state: studies disagree on shared bars/baseline")
            if first.vote_history and first.pid.setpoint != sum(s.hit_rate for s in states.values()) / len(states):
                raise ValueError("Invalid studies state: setpoint is not the cross-study baseline")
            staged[symbol] = states
        return staged

    def restore_state(self, payload: object) -> None:
        """Install a saved ``studies_v1`` payload into this EMPTY signal; all-or-nothing."""
        if self._symbols:
            raise ValueError("Studies state can only be restored into an empty signal")
        self._symbols = self._stage_state(payload)

    def _get_symbol_state(self, symbol: str) -> Dict[str, _StudyState]:
        if symbol not in self._symbols:
            self._symbols[symbol] = {
                name: _StudyState(
                    weight=1.0 / len(STUDY_NAMES),
                    pid=PID(Kp=self.kp, Ki=self.ki, Kd=self.kd, setpoint=0.5, sample_time=None,
                            output_limits=(-self.clamp, self.clamp)),
                    vote_history=deque(maxlen=self.grading_horizon + 1),
                    close_history=deque(maxlen=self.grading_horizon + 1),
                    hit_history=deque(maxlen=self.hit_rate_window),
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
        indicator_inputs = _indicator_inputs(bars["timestamp"], high, low, close, volume)

        # Grade each study's vote from ``grading_horizon`` bars ago, now that
        # horizon has genuinely elapsed -- no lookahead: only ever compares
        # a past vote to price movement that has actually happened by now.
        for name, state in states.items():
            state.vote_history.append(current_votes[name])
            state.close_history.append(current_close)
            if len(state.vote_history) > self.grading_horizon:
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
        weight_pid_audit: Dict[str, Dict[str, float | int]] = {}
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
            weight_before = state.weight
            raw_output = float(state.pid(state.hit_rate, dt=1))
            adjustment = -raw_output
            base = 1.0 / len(STUDY_NAMES)
            state.weight = _clip(base + adjustment, MIN_WEIGHT, MAX_WEIGHT)
            weights[name] = state.weight
            weight_pid_audit[name] = {
                "setpoint": float(cross_study_baseline),
                "measurement_hit_rate": float(state.hit_rate),
                "error": float(cross_study_baseline - state.hit_rate),
                "p": float(state.pid.components[0]),
                "i": float(state.pid.components[1]),
                "d": float(state.pid.components[2]),
                "raw_output": raw_output,
                "applied_adjustment": float(adjustment),
                "weight_before": float(weight_before),
                "weight_after": float(state.weight),
                "graded_vote_count": len(state.hit_history),
            }

        total_weight = sum(weights.values()) or 1.0
        weighted_score = sum(current_votes[n] * weights[n] for n in STUDY_NAMES) / total_weight  # in [-1, 1]
        confidence = abs(weighted_score)  # conviction magnitude; direction is independent
        direction = 1 if weighted_score > 0 else (-1 if weighted_score < 0 else 0)

        return {
            "confidence": confidence, "direction": direction, "weighted_score": weighted_score,
            "votes": dict(current_votes), "weights": dict(weights),
            "hit_rates": {n: states[n].hit_rate for n in STUDY_NAMES},
            "indicator_inputs": indicator_inputs,
            "weight_pid_audit": weight_pid_audit,
        }
