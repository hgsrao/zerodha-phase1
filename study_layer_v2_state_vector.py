"""Study Layer V2 - continuous state vector (2026-08-25, owner-specified,
revised after owner's 18-point + 5-new-criteria architecture review).

Layer 2 of the "repair sensors -> normalize states -> establish feedback
variable -> PI controller" order the owner laid out. Deliberately NOT a
composite score - a state VECTOR, kept distinct per dimension - SEVEN
dimensions, not six (the owner's own correction to an earlier miscount):

    x_t = [S_t, L_vwap_t, L_bb_t, M_t, dM_t, R_t, V_pct_t]

Fusing these into one scalar here would quietly recreate the weighted-
vote architecture this whole effort exists to move past. Whether/how
these combine is a LATER, empirically-earned decision (Layer 3+), not
made in this file. The owner's own warning applies recursively: the
original disease (equal-weight voting) can recur INSIDE a single
dimension too (e.g. Structure = cloud position + cloud color + Tenkan/
Kijun + Chikou, arbitrarily weighted, would hide the same problem one
layer deeper) - which is why Structure here is deliberately ONE
defensible measurement, not four combined.

Acceptance criteria (1-7 original, 8-12 added after the owner's review):
  1. Boundedness       - a DECLARED-bounded output can never escape its
                          contract, enforced by construction (tanh /
                          linear rescale of an already-bounded input).
  2. Causality          - value at bar t depends only on bars <= t.
  3. Dimensionlessness  - price distances in ATR (or std-dev) units.
  4. Semantic purity    - ATR never becomes "bullish"; Regime is a
                          separate context dimension, never fused in.
  5. Monotonicity       - Layer 2 transforms are monotonic in the
                          PHYSICAL quantity they encode (SMI up -> M up;
                          cloud distance up -> S up). Layer 3 is free to
                          later learn a NON-monotonic relationship on
                          top (e.g. "slightly above VWAP good, extremely
                          above VWAP bad") - that is a Layer-3 belief
                          about trade quality, and injecting it into
                          Layer 2's normalization would be exactly the
                          "Location is not Direction" mistake the owner
                          flagged in criterion 3's own review.
  6. No full-sample normalization - percentile/rolling stats use a
                          bounded TRAILING window, never the whole
                          series or an expanding-from-inception one.
  7. NOT_READY != 0.0   - insufficient warm-up is NaN internally, AND
                          carries an explicit `<field>_ready` boolean at
                          the DataFrame interface (added per the owner's
                          review: NaN alone risks a future `fillna(0)`
                          silently converting "unknown" into "genuinely
                          neutral" - the readiness column makes that
                          class of bug impossible to introduce silently).
  8. Temporal/session continuity - validate_bar_continuity() detects
                          (never silently repairs) duplicate timestamps,
                          non-monotonic timestamps, and intra-session
                          gaps versus the expected bar cadence. Missing
                          data must never be read as "market didn't
                          move."
  9. Cross-input timestamp alignment - assert_temporal_alignment() is a
                          first-cut, fail-closed contract for when a
                          CONTEXT input (e.g. Regime, computed from the
                          index's own bar timeline) gets joined onto a
                          symbol's own decision timestamp. Not wired in
                          yet (the join itself doesn't exist - Regime is
                          still computed standalone from index bars, per
                          criterion 4/#6 of the owner's review), but the
                          contract exists now so the join can't be built
                          without it later.
 10. Prefix-equivalence causality - test_no_lookahead_state_vector.py's
                          existing "append future bars, past values must
                          not move" tests are extended with an explicit,
                          non-inductive "full dataset vs. an exact
                          prefix" comparison - stronger than trusting
                          transitivity across the parametrized cases.
 11. Exact price-scale invariance - test_study_layer_v2_state_vector.py
                          adds a deterministic metamorphic test: multiply
                          OHLC by 10x and 0.1x (volume, pattern
                          unchanged) and require every dimensionless
                          state to be IDENTICAL within numerical
                          tolerance. Strictly stronger than the original
                          "neither saturates" heuristic test (kept
                          alongside, not replaced, since it exercises a
                          genuinely different randomized scenario).
 12. Raw + normalized traceability - EVERY dimension keeps its raw,
                          lossless measurement (structure_distance_atr,
                          vwap_distance_atr, bb_z, smi_raw, atr_price_
                          ratio) alongside its bounded, normalized
                          counterpart, so 3 months from now the question
                          "was the underlying market measurement unusual,
                          or did normalization create the unusual state?"
                          is answerable, not conflated. NOT applied
                          mechanically to every field regardless of
                          need (owner's round-2 correction): momentum_
                          delta has no separate raw/normalized pair,
                          since it isn't a lossy transform of some other
                          raw quantity - manufacturing one would just
                          bloat the schema without adding information.
 13. Deterministic replay - identical (bars, timestamps, config) must
                          produce numerically identical output. No
                          dependence on wall clock, random state, or
                          row order beyond an explicit timestamp sort.
                          compute_state_vector() raises ValueError on
                          unsorted input rather than silently
                          re-sorting (fail closed, not fail quiet).
 14. Schema/config identity - every compute_state_vector() output
                          carries `state_schema_version`
                          (STATE_SCHEMA_VERSION) and `config_hash` (a
                          hash of the actual atr/bb/smi/vol_window
                          parameters used), so state produced under
                          different code or config versions can never
                          be silently compared as if equivalent.

Pure computation. No Kite/network calls. Not wired into any live engine.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

import study_layer_v2_indicators as v2

# Durable schema identity (acceptance criterion 14, owner-added
# 2026-08-25 round 2): stamped onto every compute_state_vector() output
# so a later comparison (e.g. live shadow output vs. offline replay)
# can never silently compare state produced by different code/config
# versions without it being visible in the data itself.
STATE_SCHEMA_VERSION = "STUDY_STATE_V2_1"


# ---------------------------------------------------------------------------
# ATR - the shared dimensionless yardstick every other dimension leans on
# ---------------------------------------------------------------------------
def average_true_range(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Classic ATR: rolling mean of True Range. Simple rolling mean (not
    Wilder's smoothing) - a documented, deliberate first cut, easy to
    swap later; both are equally causal, this one is more transparent to
    read while validating everything else in this file."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ---------------------------------------------------------------------------
# Structure - Ichimoku, distance to the projected cloud in ATR units.
# Raw measurement and normalized read are two separate functions, per
# acceptance criterion 12 - normalization must never destroy the raw one.
# ---------------------------------------------------------------------------
def structure_distance_atr_raw(df: pd.DataFrame, atr: pd.Series, kijun: int = 26, senkou_b: int = 52) -> pd.Series:
    """d_cloud = (Price - CloudMid) / ATR - unbounded, lossless. CloudMid
    is the midpoint of the genuinely PROJECTED cloud (study_layer_v2_
    indicators.ichimoku_projected - not chart_studies_indicators' same-
    bar version). Deliberately the smallest semantically-correct
    structural measurement: Tenkan/Kijun relationship, cloud orientation
    and cloud thickness are documented candidate extensions, not added
    here - per the owner's "resist combining all four initially,
    establish whether the extra components add anything" instruction."""
    computed = v2.ichimoku_projected(df, kijun=kijun, senkou_b=senkou_b)
    cloud_mid = (computed["SenkouA_projected"] + computed["SenkouB_projected"]) / 2
    return (df["close"] - cloud_mid) / atr.replace(0, np.nan)


def structure_score(df: pd.DataFrame, atr: pd.Series, kijun: int = 26, senkou_b: int = 52) -> pd.Series:
    """S_t = tanh(structure_distance_atr_raw). NaN (NOT_READY) whenever
    the projected cloud or ATR isn't defined yet - never a fake 0.0."""
    return np.tanh(structure_distance_atr_raw(df, atr, kijun=kijun, senkou_b=senkou_b))


# ---------------------------------------------------------------------------
# Location - VWAP and Bollinger, kept SEPARATE (not fused) so their
# redundancy can be measured empirically rather than assumed. "Location",
# never "Direction": magnitude-aware, and Layer 2 does not judge whether
# a given location is GOOD for a trade - that is explicitly Layer 3's job.
# ---------------------------------------------------------------------------
def vwap_distance_atr_raw(df: pd.DataFrame, vwap: pd.Series, atr: pd.Series) -> pd.Series:
    """d_vwap = (Price - VWAP) / ATR - unbounded, lossless."""
    return (df["close"] - vwap) / atr.replace(0, np.nan)


def location_vwap_score(df: pd.DataFrame, vwap: pd.Series, atr: pd.Series) -> pd.Series:
    """L_vwap_t = tanh(vwap_distance_atr_raw). Deliberately NOT "close >
    VWAP -> bullish" (a direction read) - a MAGNITUDE-aware LOCATION
    read: barely above VWAP and 4-ATR above VWAP are very different
    states. Whether either is a GOOD state for a trade is not decided
    here - "price severely extended above VWAP" and "price constructively
    above VWAP" are both just "positive L_vwap" at this layer; Layer 3
    is where that distinction, if it exists, gets learned."""
    return np.tanh(vwap_distance_atr_raw(df, vwap, atr))


def bb_z_raw(df: pd.DataFrame, bb_basis: pd.Series, bb_std: pd.Series) -> pd.Series:
    """z_BB = (Price - Basis) / rolling_std - unbounded, lossless. Kept
    separately from its tanh-squashed counterpart specifically because,
    per the owner's review, beyond ~2 sigma tanh compresses heavily
    (tanh(2)=0.964, tanh(3)=0.995) and whether a 2 sigma vs. 3 sigma
    extension carries different information is exactly the kind of
    question that compression would quietly foreclose before it's ever
    asked."""
    return (df["close"] - bb_basis) / bb_std.replace(0, np.nan)


def location_bb_score(df: pd.DataFrame, bb_basis: pd.Series, bb_std: pd.Series) -> pd.Series:
    """L_bb_t = tanh(bb_z_raw), for the same declared-bounded contract as
    every other normalized dimension here. See bb_z_raw for why the raw
    z-score is ALSO retained, not discarded by this squashing."""
    return np.tanh(bb_z_raw(df, bb_basis, bb_std))


# ---------------------------------------------------------------------------
# Momentum - SMI, already roughly bounded; guaranteed bounded here by
# construction rather than trusted to stay within its usual range. The
# raw SMI value itself is the "raw" counterpart (already an input the
# caller has and compute_state_vector preserves it directly).
# ---------------------------------------------------------------------------
def momentum_score(smi: pd.Series) -> pd.Series:
    """M_t = tanh(SMI / 100). Classic SMI is APPROXIMATELY [-100, +100]
    but the double-EMA smoothing can technically overshoot slightly in
    edge cases - tanh (not a bare /100 clip) guarantees the [-1, +1]
    contract can never be escaped, satisfying acceptance criterion #1
    literally rather than "usually.\""""
    return np.tanh(smi / 100.0)


def momentum_delta(momentum: pd.Series) -> pd.Series:
    """dM_t = (M_t - M_t-1) / 2. Since M_t in [-1,+1] by construction,
    the raw difference is bounded in [-2,+2] by construction too - the
    /2 is a simple LINEAR rescale (not tanh) to land it in [-1,+1]
    without compressing/distorting the shape near the extremes, since
    the bound here is already exact, not just probable. This is where
    the "bullishness accelerating vs decelerating even while every vote
    stays nominally bullish" signal lives - deliberately preserved
    UNSMOOTHED (no EMA/slope applied) through normalization, per the
    owner's explicit "don't smooth dM yet, that's an empirical Layer-3
    question" instruction."""
    return (momentum - momentum.shift(1)) / 2.0


# ---------------------------------------------------------------------------
# Regime - a CONTEXT dimension, computed continuously but not fused with
# the stock-level dimensions above; how it's used is a Layer-3 decision.
# Explicit contract (per the owner's "audit the source of R" point):
# R_t represents ONLY the normalized spread between the index's own
# SMA(short) and SMA(long), expressed in the index's own ATR units - nothing
# else is folded in here (no RSI, no ADX, no breadth), so this dimension
# cannot itself become a hidden sub-vote system, and its meaning stays
# falsifiable ("a spread-based directional regime read"), not the
# unfalsifiable "general market confidence" the owner warned against.
# ---------------------------------------------------------------------------
def regime_score(index_df: pd.DataFrame, atr: pd.Series, short: int = 20, long: int = 50) -> pd.Series:
    """R_t = tanh((SMA_short - SMA_long) / ATR) on the INDEX (e.g.
    NIFTY) - a continuous version of map_context_indicators.
    classify_index_regime()'s discrete UP/DOWN/CHOPPY stack. This
    function does not decide how R_t modifies anything downstream (gain
    scaling vs. evidence blend) - that separation is the owner's
    explicit correction and is deliberately left open here."""
    sma_short = index_df["close"].rolling(short).mean()
    sma_long = index_df["close"].rolling(long).mean()
    spread = (sma_short - sma_long) / atr.replace(0, np.nan)
    return np.tanh(spread)


# ---------------------------------------------------------------------------
# Volatility - never directional. TWO measurements retained, answering
# two DIFFERENT questions (owner's correction, round 2 - the original
# docstring here called raw ATR/price "not cross-symbol comparable",
# which was imprecise and has been fixed):
#   - atr_price_ratio (V_raw): ATR/Price IS dimensionless and IS
#     comparable across symbols IN PERCENTAGE TERMS - 1.5% ATR means the
#     same relative move whether the stock is Rs.200 or Rs.5,000. What it
#     does NOT tell you is whether 1.5% is UNUSUAL for THIS PARTICULAR
#     symbol - stocks have structurally different volatility
#     distributions, so a persistent 1.5% may be ordinary for one and
#     exceptional for another.
#   - volatility_percentile (V_pct): answers that second, symbol-
#     relative-abnormality question - but can quietly decay toward 0.5
#     during a sustained high-vol regime as the trailing window fills
#     with more high-vol observations, understating persistent elevated
#     risk. atr_price_ratio is what catches that decay when it matters.
# Both valuable, answering different questions - not a redundant pair.
# ---------------------------------------------------------------------------
def atr_price_ratio(df: pd.DataFrame, atr: pd.Series) -> pd.Series:
    """V_raw_t = ATR / Price. Non-negative, dimensionless, and already
    comparable across symbols in PERCENTAGE terms (see the section
    comment above this function for the precise distinction from
    volatility_percentile - this field answers "how big is the move in
    percentage terms", not "is that percentage unusual for this
    symbol")."""
    return atr / df["close"]


# Frozen percentile convention (owner's review: "freeze the convention,
# otherwise a seemingly harmless implementation change later will alter
# historical states"):
#   - trailing window length: `window` bars (default 500), NOT expanding
#     from inception.
#   - the CURRENT observation participates in its own rank (the window
#     passed to the rank function includes bar t itself).
#   - minimum observations before READY: exactly `window` bars
#     (min_periods=window) - fewer than that is NOT_READY (NaN), not a
#     partial-window estimate.
#   - cross-session behavior: the trailing window spans session/day
#     boundaries freely; there is no session-reset for this field.
#   - tie treatment: inclusive - the rank counts values <= the current
#     one, so tied values share the SAME (highest-among-the-tie)
#     percentile, not an arbitrary order-dependent tie-break.
def volatility_percentile(df: pd.DataFrame, atr: pd.Series, window: int = 500) -> pd.Series:
    """V_pct_t = the trailing-window percentile rank of ATR/Price at bar
    t, among the `window` most recent (Price, ATR) observations up to
    and including t. Bounded [0,1] by construction (a rank fraction).
    See the frozen convention documented immediately above this
    function - deliberately a TRAILING window, not an expanding/full-
    sample one, so "high volatility" means the same kind of thing at any
    point in a multi-year series, not a moving, ever-diluting target."""
    atr_pct = atr_price_ratio(df, atr)

    def _rank_of_last(window_vals: np.ndarray) -> float:
        last = window_vals[-1]
        return float((window_vals <= last).sum()) / len(window_vals)

    return atr_pct.rolling(window, min_periods=window).apply(_rank_of_last, raw=True)


# ---------------------------------------------------------------------------
# Temporal/session continuity (acceptance criterion 8) - detects, never
# silently repairs. Missing data must never be read as "no movement."
# ---------------------------------------------------------------------------
def validate_bar_continuity(df: pd.DataFrame, expected_freq: str = "5min") -> dict:
    """Reports (does not fix) three failure classes in a bar series:
      - duplicate timestamps
      - non-monotonic (out-of-order) timestamps
      - INTRA-session gaps larger than `expected_freq` (a gap ACROSS a
        session/day boundary - e.g. today's last bar to tomorrow's first
        - is expected and NOT flagged; a gap WITHIN one trading day is a
        real hole, e.g. a missing 10:35 candle, and must never be
        silently treated as "price didn't move for 10 minutes.\")

    CURRENT SESSION DEFINITION (owner's round-2 caution, documented
    explicitly rather than left implicit): a "session" here is inferred
    purely as one calendar date (timestamp normalized to midnight) -
    there is NO authoritative NSE trading-calendar input (holidays,
    special/truncated Muhurat-style sessions). This is deliberately
    SAFE for that gap, not silently wrong: the gap check only compares
    CONSECUTIVE bars already present within the same calendar date - it
    never assumes a fixed bar COUNT per session, so a genuinely shorter
    special session produces fewer bars without any false gap being
    flagged. What it cannot yet do is validate that a given date was a
    real trading day at all, or use an authoritative calendar instead of
    calendar-date inference - a real limitation, not yet closed, and
    worth remembering specifically for Ichimoku's own multi-session
    warm-up requirement.

    Returns a dict: {"is_clean": bool, "duplicate_timestamps": [...],
    "non_monotonic": bool, "intra_session_gaps": [{"after": ts, "before":
    ts, "missing_bars": n}, ...]}. Callers decide policy (fail closed on
    a dirty report) - this function only detects."""
    ts = pd.to_datetime(df["timestamp"])
    freq = pd.Timedelta(expected_freq)

    dup_mask = ts.duplicated(keep=False)
    duplicate_timestamps = sorted(ts[dup_mask].unique().astype(str).tolist())

    non_monotonic = bool((ts.diff().dropna() < pd.Timedelta(0)).any())

    gaps = []
    session = ts.dt.normalize()
    same_session = session == session.shift(1)
    deltas = ts.diff()
    for i in range(1, len(ts)):
        if same_session.iloc[i] and deltas.iloc[i] > freq:
            missing = int(round(deltas.iloc[i] / freq)) - 1
            if missing > 0:
                gaps.append({"after": str(ts.iloc[i - 1]), "before": str(ts.iloc[i]), "missing_bars": missing})

    return {
        "is_clean": not duplicate_timestamps and not non_monotonic and not gaps,
        "duplicate_timestamps": duplicate_timestamps,
        "non_monotonic": non_monotonic,
        "intra_session_gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Incomplete-source-bar policy (owner's round-3 requirement, 2026-08-25,
# triggered by a REAL finding: validate_bar_continuity() found 0/48
# symbols fully clean in the real V10-C 1-min archive - 388 intra-session
# gaps, 96 of them exactly 90 minutes long, 93% of all missing candles).
# A 5-min bar aggregated from incomplete 1-min source data can look
# perfectly ordinary (valid open/high/low/close/volume) while silently
# resting on fewer than the expected 5 source observations - nothing
# downstream would otherwise know. This is NOT a repair mechanism: no
# OHLC is forward-filled, interpolated, or invented anywhere here.
# ---------------------------------------------------------------------------
def aggregate_1min_to_5min_with_quality(df_1min: pd.DataFrame, bar_freq: str = "5min") -> pd.DataFrame:
    """Resamples 1-min OHLCV to `bar_freq` bars (same label="left",
    closed="left" convention used everywhere else in this project) and
    additionally reports, per output bar:
        source_bar_count           - how many 1-min rows actually fed it
        expected_source_bar_count  - how many SHOULD have (bar_freq / 1min)
        bar_complete                - source_bar_count == expected
    A bar with bar_complete=False is not fabricated or dropped - it is
    passed through with its real (partial) OHLCV and an explicit,
    checkable flag, so a caller can decide policy (typically: exclude
    from a research/eligibility set) rather than this function silently
    deciding for them."""
    df = df_1min.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    agg = df.resample(bar_freq, label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
        source_bar_count=("close", "count"),
    ).dropna(subset=["open"])
    agg = agg.reset_index()

    expected = int(pd.Timedelta(bar_freq) / pd.Timedelta("1min"))
    agg["expected_source_bar_count"] = expected
    agg["bar_complete"] = agg["source_bar_count"] == expected
    return agg


# Each dimension's own lookback horizon, in 5-min BARS, at this module's
# default periods - reused here (not re-guessed) so contamination
# classification asks the same question compute_state_vector() would
# actually need answered for each dimension. Ichimoku's horizon is
# kijun + senkou_b's own effective span + the projection shift (see
# study_layer_v2_indicators.py's own documented ~78-bar requirement).
DEFAULT_LOOKBACK_BARS = {
    "structure": 52 + 26,   # SenkouB window (52) + the projection shift (kijun=26) - the binding
                            # ~78-bar requirement documented in study_layer_v2_indicators.py
    "location_bb": 20,      # bb_period
    "momentum": 10,         # smi_period
    "volatility_pct": 14,   # atr_period (ATR itself feeds volatility_percentile's numerator)
}


def data_quality_status(bar_complete: pd.Series, lookback_bars: int) -> pd.Series:
    """Per-bar quality classification against ONE dimension's own
    lookback horizon:
        INCOMPLETE_SOURCE_BAR  - THIS bar itself rests on < expected
                                  1-min source observations.
        RECENT_GAP_IN_LOOKBACK - this bar is itself complete, but some
                                  bar within the trailing `lookback_bars`
                                  window (inclusive of this bar) was not
                                  - so a rolling computation reading that
                                  window (ATR, Bollinger, SMI, the
                                  Ichimoku cloud) is resting on
                                  contaminated inputs even though this
                                  bar's own OHLCV looks fine.
        CLEAN                  - neither of the above.
    Deliberately per-DIMENSION (called once per dimension with that
    dimension's own DEFAULT_LOOKBACK_BARS entry) rather than one global
    status - a gap 80 bars back matters to Structure (Ichimoku, ~78-bar
    horizon) but not to Momentum (SMI, 10-bar horizon), and collapsing
    that distinction would misclassify clean SMI reads as contaminated
    just because Ichimoku's much longer horizon was hit."""
    incomplete = ~bar_complete
    any_incomplete_in_window = incomplete.rolling(lookback_bars, min_periods=1).max().astype(bool)
    status = pd.Series("CLEAN", index=bar_complete.index)
    status[any_incomplete_in_window] = "RECENT_GAP_IN_LOOKBACK"
    status[incomplete] = "INCOMPLETE_SOURCE_BAR"
    return status


def vwap_session_contamination_status(bar_complete: pd.Series, session_id: pd.Series) -> pd.Series:
    """Session VWAP is a CUMULATIVE sum from session start - a single
    incomplete bar early in the session contaminates every later VWAP
    reading for the REST of that session, not just a fixed lookback
    window (unlike ATR/Bollinger/SMI/Ichimoku above). Deliberately a
    DIFFERENT status value (SESSION_GAP_UPSTREAM), not a reuse of
    STALE_CONTEXT - that value already means something different
    (a cross-input timestamp-freshness failure in assert_temporal_
    alignment) and conflating the two would be exactly the kind of
    imprecise vocabulary this whole review has been correcting."""
    incomplete = ~bar_complete
    contaminated_so_far = incomplete.groupby(session_id).cummax()
    return contaminated_so_far.map({True: "SESSION_GAP_UPSTREAM", False: "CLEAN"})


# ---------------------------------------------------------------------------
# Cross-input timestamp alignment (acceptance criterion 9) - a first-cut,
# fail-closed contract for joining a CONTEXT input (computed on its own
# timeline, e.g. Regime on the index's bars) onto a symbol's own decision
# timestamp. Not wired into compute_state_vector yet - the join itself
# doesn't exist (Regime is still standalone, per this file's own design)
# - but the contract exists now so that join can't be built without it.
# ---------------------------------------------------------------------------
def assert_temporal_alignment(decision_ts, context_ts, max_staleness: str = "0min") -> dict:
    """Checks a context input's own source timestamp against the
    decision timestamp the state vector is being computed for, per two
    SEPARATE, explicitly frozen contracts (owner's round-2 precision
    request):

        Causality:  T_context <= T_decision   (never permit context
                    newer than the information set - a straightforward
                    lookahead leak if violated)
        Freshness:  T_decision - T_context <= max_staleness  (context
                    may be causally valid but too old to describe "now")

    Returns one of exactly three status values - not a bare boolean,
    so a caller can tell "wait, this will resolve" (STALE) apart from
    "something is operationally wrong" (FUTURE_CONTEXT) rather than
    collapsing both into False:
        ALIGNED        - both contracts hold.
        STALE          - causally valid, but older than max_staleness.
        FUTURE_CONTEXT - context is from AFTER the decision timestamp;
                         always invalid, regardless of max_staleness.

    Default max_staleness is "0min" - DELIBERATELY strict: for a 5-min
    state engine, the owner's stated preference is that context should
    ideally carry the EXACT SAME completed 5-min timestamp as the
    decision bar, not merely "recent". Callers with a genuine reason to
    tolerate some lag (e.g. a slower-cadence context source) pass a
    wider max_staleness explicitly - this is not a global loosening.

    FAIL-CLOSED semantics unchanged: a caller must treat any status
    other than ALIGNED as NOT_READY for that dimension, never substitute
    a stale or future value."""
    decision_ts = pd.Timestamp(decision_ts)
    context_ts = pd.Timestamp(context_ts)
    if context_ts > decision_ts:
        return {"status": "FUTURE_CONTEXT", "aligned": False, "staleness": None}
    staleness = decision_ts - context_ts
    if staleness > pd.Timedelta(max_staleness):
        return {"status": "STALE", "aligned": False, "staleness": str(staleness)}
    return {"status": "ALIGNED", "aligned": True, "staleness": str(staleness)}


# ---------------------------------------------------------------------------
# Orchestrator - raw + normalized + explicit readiness for every
# STOCK-LEVEL dimension (Regime stays standalone; see module docstring).
# ---------------------------------------------------------------------------
STATE_VECTOR_FIELDS = ["structure", "location_vwap", "location_bb", "momentum", "momentum_delta", "volatility_pct"]
RAW_FIELDS = {
    "structure": "structure_distance_atr_raw",
    "location_vwap": "vwap_distance_atr_raw",
    "location_bb": "bb_z_raw",
    "momentum": "smi_raw",
    "volatility_pct": "volatility_raw",
}


# Reserved readiness-status values (owner's round-2 request: a status
# enum, not a bare boolean, so Layer 3 can distinguish "wait, this will
# resolve on its own" from "something is operationally wrong"). Only
# READY and INSUFFICIENT_HISTORY are actually reachable from THIS
# module today - the others are reserved for when the callers that
# would produce them are actually wired in (validate_bar_continuity's
# gap/duplicate detection is not yet fed into per-field status, and the
# Regime join via assert_temporal_alignment doesn't exist yet). Listed
# here, not fabricated in the output, so the full intended vocabulary is
# visible before those integrations land.
READY = "READY"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
MISSING_BAR = "MISSING_BAR"                # reserved - not yet produced by this module
STALE_CONTEXT = "STALE_CONTEXT"            # reserved - not yet produced by this module
TIMESTAMP_MISMATCH = "TIMESTAMP_MISMATCH"  # reserved - not yet produced by this module
INVALID_INPUT = "INVALID_INPUT"            # reserved - not yet produced by this module


def _config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def compute_state_vector(df: pd.DataFrame, atr_period: int = 14, bb_period: int = 20, smi_period: int = 10,
                          vol_window: int = 500) -> pd.DataFrame:
    """Computes every STOCK-LEVEL state dimension (Structure, both
    Location reads, Momentum + its delta, Volatility percentile) for one
    symbol's own 5-min bars, WITH the raw/lossless counterpart of every
    dimension, an explicit `<field>_ready` boolean, AND an explicit
    `<field>_status` string (READY or INSUFFICIENT_HISTORY today - see
    the reserved-status constants above) alongside each normalized value
    (acceptance criteria 7 and 12). Regime is deliberately NOT included
    here - it is computed separately from the INDEX's own bars via
    regime_score() and joined downstream through
    assert_temporal_alignment(); conflating a per-symbol frame with the
    shared index frame here would blur exactly the "stock-level evidence
    vs. market-level context" distinction the owner drew a hard line
    around.

    DETERMINISM (acceptance criterion 13): this function raises
    ValueError if `df["timestamp"]` is not already strictly sorted
    ascending, rather than silently re-sorting - fail-closed, same
    philosophy as validate_bar_continuity (detect, don't silently
    repair). Every computation here is a pure function of `df` and the
    keyword arguments - no wall-clock, no random state, no dependence on
    anything but the input - so calling this twice on identical input
    is required to (and, per test_compute_state_vector_is_deterministic_
    across_repeated_calls, does) produce bit-identical output.

    SCHEMA IDENTITY (acceptance criterion 14): every output DataFrame
    carries a constant `state_schema_version` column (this module's
    STATE_SCHEMA_VERSION) and a `config_hash` column - a short hash of
    the actual (atr_period, bb_period, smi_period, vol_window) used - so
    state produced under one configuration can never be silently
    compared against state produced under a different one months later.

    Callers are expected to run validate_bar_continuity(df) themselves
    before calling this - that check is deliberately kept separate
    (detection, not silent repair, is a policy decision for the caller,
    not this function).

    Any bar without enough warm-up history for a given dimension carries
    NaN in that column, False in its `_ready` column, and
    INSUFFICIENT_HISTORY in its `_status` column - NOT_READY, never a
    fake 0.0."""
    if not df["timestamp"].is_monotonic_increasing:
        raise ValueError(
            "compute_state_vector requires df sorted strictly ascending by timestamp - "
            "sort explicitly before calling; this function fails closed rather than silently re-sorting."
        )

    out = df[["timestamp", "close"]].copy()
    atr = average_true_range(df, period=atr_period)

    bb_basis = df["close"].rolling(bb_period).mean()
    bb_std = df["close"].rolling(bb_period).std()
    vwap = _session_vwap(df)
    smi = _smi(df, k_period=smi_period)

    out["structure_distance_atr_raw"] = structure_distance_atr_raw(df, atr)
    out["structure"] = np.tanh(out["structure_distance_atr_raw"])

    out["vwap_distance_atr_raw"] = vwap_distance_atr_raw(df, vwap, atr)
    out["location_vwap"] = np.tanh(out["vwap_distance_atr_raw"])

    out["bb_z_raw"] = bb_z_raw(df, bb_basis, bb_std)
    out["location_bb"] = np.tanh(out["bb_z_raw"])

    out["smi_raw"] = smi
    out["momentum"] = momentum_score(smi)
    out["momentum_delta"] = momentum_delta(out["momentum"])

    out["volatility_raw"] = atr_price_ratio(df, atr)
    out["volatility_pct"] = volatility_percentile(df, atr, window=vol_window)

    for field in STATE_VECTOR_FIELDS:
        ready = out[field].notna()
        out[f"{field}_ready"] = ready
        out[f"{field}_status"] = np.where(ready, READY, INSUFFICIENT_HISTORY)

    config = {"atr_period": atr_period, "bb_period": bb_period, "smi_period": smi_period, "vol_window": vol_window}
    out["state_schema_version"] = STATE_SCHEMA_VERSION
    out["config_hash"] = _config_hash(config)

    return out


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    """Same session-VWAP read used throughout this session's modules -
    cumulative typical-price VWAP, resetting every trading day."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    grp = df["timestamp"].dt.normalize()
    return pv.groupby(grp).cumsum() / df["volume"].groupby(grp).cumsum().replace(0, np.nan)


def _smi(df: pd.DataFrame, k_period: int = 10, d_period: int = 3, ema_period: int = 3) -> pd.Series:
    """Same SMI computation as chart_studies_indicators.stochastic_momentum_index -
    reimplemented here (not imported) only to keep this file's import
    surface to study_layer_v2_indicators alone; the math is identical."""
    high, low, close = df["high"], df["low"], df["close"]
    hh = high.rolling(k_period).max()
    ll = low.rolling(k_period).min()
    midpoint = (hh + ll) / 2
    diff = close - midpoint
    rng = hh - ll
    diff_ema1 = diff.ewm(span=d_period, adjust=False).mean()
    diff_ema2 = diff_ema1.ewm(span=d_period, adjust=False).mean()
    rng_ema1 = rng.ewm(span=d_period, adjust=False).mean()
    rng_ema2 = rng_ema1.ewm(span=d_period, adjust=False).mean()
    return (100.0 * (diff_ema2 / (rng_ema2 / 2))).where(rng_ema2 != 0)
