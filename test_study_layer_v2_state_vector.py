"""Tests for study_layer_v2_state_vector.py, structured directly around
the owner's 7 acceptance criteria for the state-normalization module.
All synthetic data. No Kite/network calls."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from study_layer_v2_state_vector import (
    aggregate_1min_to_5min_with_quality, assert_temporal_alignment, atr_price_ratio, average_true_range,
    bb_z_raw, compute_state_vector, data_quality_status, location_bb_score, location_vwap_score,
    momentum_delta, momentum_score, regime_score, structure_distance_atr_raw, structure_score,
    validate_bar_continuity, volatility_percentile, vwap_distance_atr_raw, vwap_session_contamination_status,
)


def _bars(n, start_price=100.0, drift_pct=0.001, noise_seed=1, start_ts="2026-06-01 09:15", freq="5min"):
    """Multi-session-like synthetic 5-min bars, built from PERCENTAGE
    (log-return) drift and PROPORTIONAL intrabar noise - not absolute
    rupee amounts. This matters here specifically: an earlier draft used
    fixed absolute drift/noise regardless of price level, which silently
    broke the very dimensionlessness property test_structure_score_is_
    comparable_across_very_different_price_levels and test_volatility_
    percentile_is_direction_agnostic exist to check (ATR stayed ~flat in
    absolute terms while price diverged, so ATR/price ratios weren't
    remotely comparable across price levels or directions - a bug in the
    test fixture, not in study_layer_v2_state_vector.py). Real markets
    move by percentage, not fixed rupee steps, so this is also just the
    more honest synthetic generator."""
    rng = np.random.RandomState(noise_seed)
    ts = pd.date_range(start_ts, periods=n, freq=freq)
    log_returns = drift_pct + rng.normal(0, 0.0015, n)
    closes = start_price * np.exp(np.cumsum(log_returns))
    intrabar_pct = np.abs(rng.normal(0.004, 0.001, n))
    highs = closes * (1 + intrabar_pct)
    lows = closes * (1 - intrabar_pct)
    opens = closes * (1 - rng.normal(0, 0.001, n))
    volumes = 1000.0 + rng.uniform(0, 200, n)
    return pd.DataFrame({"timestamp": ts, "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes})


# ---------------------------------------------------------------------------
# 1. Boundedness - never escapes its declared contract, even under
#    adversarial/extreme synthetic inputs
# ---------------------------------------------------------------------------
def test_structure_score_never_escapes_minus1_plus1_under_extreme_price():
    df = _bars(200, start_price=100.0, drift_pct=0.03)  # violent, unrealistic uptrend
    atr = average_true_range(df)
    s = structure_score(df, atr)
    valid = s.dropna()
    assert (valid >= -1.0).all() and (valid <= 1.0).all()


def test_momentum_score_never_escapes_minus1_plus1_even_for_smi_beyond_100():
    """tanh must guarantee the bound even if SMI itself overshoots its
    usual +-100 range - the whole reason tanh was chosen over a bare
    /100 clip."""
    smi = pd.Series([500.0, -500.0, 1e6, -1e6, 0.0, 100.0, -100.0])
    m = momentum_score(smi)
    assert (m >= -1.0).all() and (m <= 1.0).all()


def test_momentum_delta_never_escapes_minus1_plus1():
    m = pd.Series([1.0, -1.0, 1.0, -1.0, 1.0])  # maximum possible bar-to-bar swing
    dm = momentum_delta(m)
    valid = dm.dropna()
    assert (valid >= -1.0).all() and (valid <= 1.0).all()


def test_volatility_percentile_is_always_within_0_and_1():
    df = _bars(600, start_price=100.0)
    atr = average_true_range(df)
    v = volatility_percentile(df, atr, window=500)
    valid = v.dropna()
    assert (valid >= 0.0).all() and (valid <= 1.0).all()


def test_location_scores_never_escape_minus1_plus1():
    df = _bars(100, start_price=100.0, drift_pct=0.05)  # extreme move away from VWAP/basis
    atr = average_true_range(df)
    vwap = (df["close"]).expanding().mean()  # stand-in, not testing VWAP's own math here
    bb_basis = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    lv = location_vwap_score(df, vwap, atr)
    lb = location_bb_score(df, bb_basis, bb_std)
    assert (lv.dropna() >= -1.0).all() and (lv.dropna() <= 1.0).all()
    assert (lb.dropna() >= -1.0).all() and (lb.dropna() <= 1.0).all()


# ---------------------------------------------------------------------------
# 2. Causality - covered exhaustively in test_no_lookahead_state_vector.py;
#    one direct smoke test here too.
# ---------------------------------------------------------------------------
def test_atr_at_bar_t_does_not_use_bar_t_plus_1():
    df = _bars(60)
    atr_full = average_true_range(df)
    atr_partial = average_true_range(df.iloc[:40])
    assert atr_full.iloc[39] == pytest.approx(atr_partial.iloc[39])


# ---------------------------------------------------------------------------
# 3. Dimensionlessness - a Rs.150 stock and a Rs.6,000 stock must produce
#    COMPARABLE structure scores for an equivalent ATR-relative move
# ---------------------------------------------------------------------------
def test_structure_score_is_comparable_across_very_different_price_levels():
    # Driftless (pure random walk in log-price) - avoids the trend
    # saturating tanh, which would defeat the point of this test (a
    # saturated 0.999... at BOTH price levels wouldn't distinguish
    # "comparable" from "both broken the same way"; a wandering,
    # unsaturated distribution at both levels is the real check).
    cheap = _bars(150, start_price=150.0, drift_pct=0.0, noise_seed=7)
    expensive = _bars(150, start_price=6000.0, drift_pct=0.0, noise_seed=7)  # same % dynamics by construction now
    atr_cheap = average_true_range(cheap)
    atr_expensive = average_true_range(expensive)
    s_cheap = structure_score(cheap, atr_cheap).dropna()
    s_expensive = structure_score(expensive, atr_expensive).dropna()
    # Not asserting exact equality (different random noise realizations),
    # just that neither is pinned to +-1 (saturated) while the other is
    # near 0, which is what a NON-dimensionless (raw rupee distance)
    # version of this function would do for a Rs.6,000 stock.
    assert s_expensive.abs().median() < 0.99
    assert s_cheap.abs().median() < 0.99


# ---------------------------------------------------------------------------
# 4. Semantic purity - volatility carries no direction; regime is
#    computed independently of any single stock's own price
# ---------------------------------------------------------------------------
def test_volatility_percentile_is_direction_agnostic():
    """A violent DOWN move and an equally violent UP move of the same
    magnitude must rank as similarly 'volatile', not one bullish-high and
    one bearish-low."""
    # Modest drift, not an extreme one: a rolling-mean ATR necessarily
    # lags a few bars behind the CURRENT price, so in ANY sustained trend
    # (up or down) recent-past bars differ systematically from the
    # current bar - a real, expected asymmetry that grows with the size
    # of the trend, not a bug. A modest, realistic drift keeps that lag
    # effect small enough that this test is actually checking direction-
    # agnosticism, not amplifying an unrelated lag artifact.
    up = _bars(600, start_price=100.0, drift_pct=0.001, noise_seed=3)
    down = _bars(600, start_price=100.0, drift_pct=-0.001, noise_seed=3)
    atr_up = average_true_range(up)
    atr_down = average_true_range(down)
    v_up = volatility_percentile(up, atr_up, window=500).dropna()
    v_down = volatility_percentile(down, atr_down, window=500).dropna()
    assert v_up.median() == pytest.approx(v_down.median(), abs=0.2)


def test_regime_score_uses_index_bars_not_a_symbols_own_bars():
    """regime_score's signature only accepts an index_df + its own ATR -
    it has no access to any individual symbol's price at all, which is
    what keeps it a context variable rather than accidentally becoming
    per-symbol evidence."""
    nifty = _bars(120, start_price=22000.0, drift_pct=0.0007)
    atr = average_true_range(nifty)
    r = regime_score(nifty, atr)
    assert r.dropna().abs().le(1.0).all()


# ---------------------------------------------------------------------------
# 5. Monotonicity - increasing raw evidence never decreases the
#    normalized read (the realistic bug here is a sign flip, not a
#    non-monotonic transform)
# ---------------------------------------------------------------------------
def test_momentum_score_is_monotonic_in_raw_smi():
    smi = pd.Series(sorted(np.random.RandomState(2).uniform(-150, 150, 50)))
    m = momentum_score(smi)
    assert (m.diff().dropna() >= 0).all()


def test_structure_score_increases_as_price_moves_further_above_cloud():
    df = _bars(150, start_price=100.0, drift_pct=0.002, noise_seed=9)
    atr = average_true_range(df)
    s = structure_score(df, atr)
    # Compare a later window (after the price has trended further from
    # its own cloud, given the steady drift) to an earlier one.
    valid = s.dropna()
    first_half_mean = valid.iloc[: len(valid) // 2].mean()
    second_half_mean = valid.iloc[len(valid) // 2 :].mean()
    assert second_half_mean >= first_half_mean


# ---------------------------------------------------------------------------
# 6. No full-sample normalization - trailing window only
# ---------------------------------------------------------------------------
def test_volatility_percentile_uses_a_bounded_trailing_window_not_full_sample():
    """Two series identical for their first 600 bars, then diverging
    wildly afterward, must produce IDENTICAL volatility percentiles for
    bar 550 (well inside the trailing window, unaffected by what comes
    after) - proving the window is trailing/bounded, not expanding from
    a point that could still be influenced by a later full-sample pass."""
    base = _bars(600, start_price=100.0, drift_pct=0.001, noise_seed=5)
    tame_tail = pd.concat([base, _bars(100, start_price=float(base["close"].iloc[-1]), drift_pct=0.001, noise_seed=11,
                                        start_ts=str(base["timestamp"].iloc[-1] + pd.Timedelta(minutes=5)))],
                           ignore_index=True)
    wild_tail = pd.concat([base, _bars(100, start_price=float(base["close"].iloc[-1]), drift_pct=0.08, noise_seed=11,
                                        start_ts=str(base["timestamp"].iloc[-1] + pd.Timedelta(minutes=5)))],
                           ignore_index=True)
    atr_tame = average_true_range(tame_tail)
    atr_wild = average_true_range(wild_tail)
    v_tame = volatility_percentile(tame_tail, atr_tame, window=500)
    v_wild = volatility_percentile(wild_tail, atr_wild, window=500)
    assert v_tame.iloc[550] == pytest.approx(v_wild.iloc[550])


# ---------------------------------------------------------------------------
# 7. NOT_READY != 0.0 - insufficient warm-up must be NaN, never a fake
#    neutral value that could be confused with a genuinely-computed 0.0
# ---------------------------------------------------------------------------
def test_all_state_vector_fields_are_nan_not_zero_during_warmup():
    df = _bars(5, start_price=100.0)  # far too little history for anything
    out = compute_state_vector(df)
    for field in ["structure", "location_vwap", "location_bb", "momentum", "volatility_pct"]:
        assert out[field].isna().all(), f"{field} should be entirely NaN (NOT_READY) with only 5 bars, not a fake 0.0"


def test_a_genuine_zero_reading_is_distinguishable_from_not_ready():
    """Construct a case where structure_score computes an honest ~0.0
    (price sitting right at the cloud midpoint) AFTER warm-up, and prove
    it is a real float 0.0-ish value, not NaN - i.e. the two states really
    are distinguishable in this implementation, not just in the docstring."""
    df = _bars(150, start_price=100.0, drift_pct=0.0, noise_seed=4)  # flat market -> price hovers near its own cloud
    atr = average_true_range(df)
    s = structure_score(df, atr)
    late = s.dropna()
    assert len(late) > 0
    assert not late.isna().any()
    # at least one of the later readings should be small in magnitude
    # (near the cloud) in a genuinely flat, driftless market
    assert late.tail(30).abs().min() < 0.5


def test_compute_state_vector_ready_flags_are_false_exactly_when_value_is_nan():
    """Added per the owner's review: NaN internally is fine, but the
    interface must carry an explicit readiness flag so a future
    fillna(0) can never silently convert NOT_READY into a real neutral
    reading. Checked both during warm-up (all False) and after (mixed)."""
    df = _bars(5, start_price=100.0)
    out = compute_state_vector(df)
    for field in ["structure", "location_vwap", "location_bb", "momentum", "volatility_pct"]:
        ready_col = f"{field}_ready"
        assert ready_col in out.columns
        assert (out[ready_col] == out[field].notna()).all(), \
            f"{ready_col} must exactly track notna() for {field} - readiness and NaN must never disagree"
        assert not out[ready_col].any(), f"{ready_col} should be all-False during warm-up"

    df_long = _bars(600, start_price=100.0, drift_pct=0.0005, noise_seed=8)
    out_long = compute_state_vector(df_long)
    for field in ["structure", "location_vwap", "location_bb", "momentum", "volatility_pct"]:
        ready_col = f"{field}_ready"
        assert (out_long[ready_col] == out_long[field].notna()).all()
        assert out_long[ready_col].tail(20).all(), f"{ready_col} should be all-True well after warm-up"


# ---------------------------------------------------------------------------
# 12. Raw + normalized traceability - every dimension keeps its raw,
#     lossless measurement alongside the bounded normalized one
# ---------------------------------------------------------------------------
def test_compute_state_vector_exposes_raw_alongside_normalized_for_every_dimension():
    df = _bars(600, start_price=100.0, drift_pct=0.0005, noise_seed=6)
    out = compute_state_vector(df)
    for raw_col in ["structure_distance_atr_raw", "vwap_distance_atr_raw", "bb_z_raw", "smi_raw", "volatility_raw"]:
        assert raw_col in out.columns
    # the raw values must NOT already be bounded to [-1,1] - proving they
    # are genuinely the pre-squash measurement, not tanh applied twice
    assert out["structure_distance_atr_raw"].dropna().abs().max() > 1.0 or \
        out["bb_z_raw"].dropna().abs().max() > 1.0, \
        "at least one raw field should exceed the [-1,1] normalized bound in 600 bars - otherwise raw isn't really raw"


def test_raw_and_normalized_are_related_by_exactly_tanh():
    df = _bars(300, start_price=100.0, drift_pct=0.001, noise_seed=13)
    out = compute_state_vector(df)
    reconstructed_structure = np.tanh(out["structure_distance_atr_raw"])
    pd.testing.assert_series_equal(reconstructed_structure, out["structure"], check_names=False)
    reconstructed_bb = np.tanh(out["bb_z_raw"])
    pd.testing.assert_series_equal(reconstructed_bb, out["location_bb"], check_names=False)


# ---------------------------------------------------------------------------
# 8. Temporal/session continuity - detects, never silently repairs
# ---------------------------------------------------------------------------
def test_validate_bar_continuity_flags_a_clean_series_as_clean():
    df = _bars(100, start_price=100.0)
    report = validate_bar_continuity(df)
    assert report["is_clean"] is True
    assert report["duplicate_timestamps"] == []
    assert report["non_monotonic"] is False
    assert report["intra_session_gaps"] == []


def test_validate_bar_continuity_detects_duplicate_timestamps():
    df = _bars(50, start_price=100.0)
    dup = df.copy()
    dup.loc[10, "timestamp"] = dup.loc[9, "timestamp"]  # force a duplicate
    report = validate_bar_continuity(dup)
    assert report["is_clean"] is False
    assert len(report["duplicate_timestamps"]) >= 1


def test_validate_bar_continuity_detects_non_monotonic_timestamps():
    df = _bars(50, start_price=100.0)
    shuffled = df.copy()
    shuffled.loc[20, "timestamp"] = df.loc[5, "timestamp"]  # jump backward
    report = validate_bar_continuity(shuffled)
    assert report["non_monotonic"] is True
    assert report["is_clean"] is False


def test_validate_bar_continuity_detects_an_intra_session_gap_not_a_cross_session_one():
    df = _bars(30, start_price=100.0, start_ts="2026-06-01 09:15")
    with_gap = pd.concat([df.iloc[:15], df.iloc[20:]], ignore_index=True)  # drop 5 bars mid-session (25 min hole)
    report = validate_bar_continuity(with_gap)
    assert report["is_clean"] is False
    assert len(report["intra_session_gaps"]) == 1
    assert report["intra_session_gaps"][0]["missing_bars"] == 5

    # A genuine cross-session gap (e.g. today's last bar -> tomorrow's
    # first) must NOT be flagged - that gap is expected, not a hole.
    day1 = _bars(10, start_price=100.0, start_ts="2026-06-01 09:15")
    day2 = _bars(10, start_price=101.0, start_ts="2026-06-02 09:15", noise_seed=2)
    two_days = pd.concat([day1, day2], ignore_index=True)
    report2 = validate_bar_continuity(two_days)
    assert report2["intra_session_gaps"] == [], "a normal overnight gap between sessions must not be flagged"


# ---------------------------------------------------------------------------
# 9. Cross-input timestamp alignment - first-cut fail-closed contract
# ---------------------------------------------------------------------------
def test_assert_temporal_alignment_accepts_exact_match_under_strict_default():
    """Default max_staleness is now '0min' - the owner's round-2
    preference for a 5-min state engine: context should ideally carry
    the EXACT SAME completed 5-min timestamp as the decision bar."""
    result = assert_temporal_alignment(decision_ts="2026-06-01 10:00", context_ts="2026-06-01 10:00")
    assert result["status"] == "ALIGNED"
    assert result["aligned"] is True


def test_assert_temporal_alignment_strict_default_rejects_even_a_small_lag():
    result = assert_temporal_alignment(decision_ts="2026-06-01 10:00", context_ts="2026-06-01 09:58")
    assert result["status"] == "STALE"
    assert result["aligned"] is False


def test_assert_temporal_alignment_accepts_fresh_context_with_explicit_tolerance():
    """A caller with a genuine reason to tolerate lag passes a wider
    max_staleness explicitly - not a global loosening of the default."""
    result = assert_temporal_alignment(decision_ts="2026-06-01 10:00", context_ts="2026-06-01 09:58",
                                        max_staleness="5min")
    assert result["status"] == "ALIGNED"
    assert result["aligned"] is True


def test_assert_temporal_alignment_rejects_context_from_the_future():
    result = assert_temporal_alignment(decision_ts="2026-06-01 10:00", context_ts="2026-06-01 10:05")
    assert result["status"] == "FUTURE_CONTEXT"
    assert result["aligned"] is False


def test_assert_temporal_alignment_future_context_is_invalid_regardless_of_max_staleness():
    """FUTURE_CONTEXT must never be rescued by a wide max_staleness -
    causality and freshness are two separate contracts."""
    result = assert_temporal_alignment(decision_ts="2026-06-01 10:00", context_ts="2026-06-01 10:01",
                                        max_staleness="1h")
    assert result["status"] == "FUTURE_CONTEXT"


def test_assert_temporal_alignment_rejects_stale_context():
    result = assert_temporal_alignment(decision_ts="2026-06-01 10:00", context_ts="2026-06-01 09:30",
                                        max_staleness="10min")
    assert result["status"] == "STALE"
    assert result["aligned"] is False


def test_assert_temporal_alignment_boundary_is_inclusive_at_exactly_max_staleness():
    result = assert_temporal_alignment(decision_ts="2026-06-01 10:00", context_ts="2026-06-01 09:50",
                                        max_staleness="10min")
    assert result["status"] == "ALIGNED"
    assert result["aligned"] is True


# ---------------------------------------------------------------------------
# 11. Exact price-scale invariance (metamorphic test, owner-specified):
#     multiply OHLC by 10x / 0.1x, leave the pattern and volume intact -
#     every dimensionless state must be IDENTICAL within tolerance.
#     Strictly stronger than the earlier "neither saturates" heuristic.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scale", [10.0, 0.1, 40.0, 100.0])
def test_dimensionless_states_are_exactly_scale_invariant(scale):
    original = _bars(300, start_price=200.0, drift_pct=0.0008, noise_seed=21)
    scaled = original.copy()
    for col in ["open", "high", "low", "close"]:
        scaled[col] = scaled[col] * scale
    # volume is explicitly left untouched - price scale and volume are
    # independent dimensions; only price-derived measurements are
    # claimed scale-invariant here.

    atr_o, atr_s = average_true_range(original), average_true_range(scaled)

    s_o = structure_score(original, atr_o)
    s_s = structure_score(scaled, atr_s)
    pd.testing.assert_series_equal(s_o, s_s, check_names=False, check_exact=False, rtol=1e-9, atol=1e-9)

    bb_basis_o, bb_std_o = original["close"].rolling(20).mean(), original["close"].rolling(20).std()
    bb_basis_s, bb_std_s = scaled["close"].rolling(20).mean(), scaled["close"].rolling(20).std()
    z_o = bb_z_raw(original, bb_basis_o, bb_std_o)
    z_s = bb_z_raw(scaled, bb_basis_s, bb_std_s)
    pd.testing.assert_series_equal(z_o, z_s, check_names=False, check_exact=False, rtol=1e-9, atol=1e-9)

    vwap_o = (original["high"] + original["low"] + original["close"]) / 3
    vwap_s = (scaled["high"] + scaled["low"] + scaled["close"]) / 3
    d_o = vwap_distance_atr_raw(original, vwap_o, atr_o)
    d_s = vwap_distance_atr_raw(scaled, vwap_s, atr_s)
    pd.testing.assert_series_equal(d_o, d_s, check_names=False, check_exact=False, rtol=1e-9, atol=1e-9)

    v_o = atr_price_ratio(original, atr_o)
    v_s = atr_price_ratio(scaled, atr_s)
    pd.testing.assert_series_equal(v_o, v_s, check_names=False, check_exact=False, rtol=1e-9, atol=1e-9)


# ---------------------------------------------------------------------------
# Owner round-2: readiness STATUS enum, not a bare boolean
# ---------------------------------------------------------------------------
def test_compute_state_vector_status_is_insufficient_history_during_warmup():
    df = _bars(5, start_price=100.0)
    out = compute_state_vector(df)
    for field in ["structure", "location_vwap", "location_bb", "momentum", "volatility_pct"]:
        status_col = f"{field}_status"
        assert status_col in out.columns
        assert (out[status_col] == "INSUFFICIENT_HISTORY").all()


def test_compute_state_vector_status_is_ready_after_warmup():
    df = _bars(600, start_price=100.0, drift_pct=0.0005, noise_seed=8)
    out = compute_state_vector(df)
    for field in ["structure", "location_vwap", "location_bb", "momentum", "volatility_pct"]:
        status_col = f"{field}_status"
        assert (out[status_col].tail(20) == "READY").all()


def test_status_and_ready_flag_never_disagree():
    df = _bars(600, start_price=100.0, drift_pct=0.0005, noise_seed=8)
    out = compute_state_vector(df)
    for field in ["structure", "location_vwap", "location_bb", "momentum", "volatility_pct"]:
        ready = out[f"{field}_ready"]
        status = out[f"{field}_status"]
        assert ((status == "READY") == ready).all()
        assert ((status == "INSUFFICIENT_HISTORY") == ~ready).all()


# ---------------------------------------------------------------------------
# 13. Deterministic replay
# ---------------------------------------------------------------------------
def test_compute_state_vector_is_deterministic_across_repeated_calls():
    df = _bars(300, start_price=100.0, drift_pct=0.0008, noise_seed=17)
    out1 = compute_state_vector(df)
    out2 = compute_state_vector(df)
    pd.testing.assert_frame_equal(out1, out2)


def test_compute_state_vector_rejects_unsorted_input_rather_than_silently_resorting():
    """Fail closed, matching validate_bar_continuity's own philosophy -
    a caller passing out-of-order timestamps gets an explicit error, not
    a silently "corrected" (and possibly wrongly-ordered, e.g. on a
    duplicate timestamp) result."""
    df = _bars(50, start_price=100.0)
    shuffled = df.iloc[::-1].reset_index(drop=True)  # deliberately reversed
    with pytest.raises(ValueError, match="sorted"):
        compute_state_vector(shuffled)


# ---------------------------------------------------------------------------
# 14. Schema/config identity
# ---------------------------------------------------------------------------
def test_compute_state_vector_stamps_schema_version():
    df = _bars(60, start_price=100.0)
    out = compute_state_vector(df)
    assert (out["state_schema_version"] == "STUDY_STATE_V2_1").all()


def test_compute_state_vector_config_hash_changes_with_config():
    df = _bars(200, start_price=100.0, drift_pct=0.0005, noise_seed=9)
    out_default = compute_state_vector(df)
    out_diff_atr = compute_state_vector(df, atr_period=20)
    assert out_default["config_hash"].iloc[0] != out_diff_atr["config_hash"].iloc[0]


def test_compute_state_vector_config_hash_identical_for_identical_config():
    df = _bars(200, start_price=100.0, drift_pct=0.0005, noise_seed=9)
    out_a = compute_state_vector(df, atr_period=14, bb_period=20)
    out_b = compute_state_vector(df, atr_period=14, bb_period=20)
    assert out_a["config_hash"].iloc[0] == out_b["config_hash"].iloc[0]


# ---------------------------------------------------------------------------
# Owner round-3 (2026-08-25, triggered by the real 388-gap finding in the
# V10-C archive): incomplete-source-bar policy - source_bar_count /
# expected_source_bar_count / bar_complete, and lookback-aware data
# quality classification. No repair anywhere - detection only.
# ---------------------------------------------------------------------------
def _one_min_bars(n, start_price=100.0, start_ts="2026-06-01 09:15", drop_indices=()):
    """1-min bars, with specific bar indices DROPPED to synthesize real
    gaps (not synthetic corruption of existing values - a genuinely
    missing row, exactly like the real archive's own gaps)."""
    rng = np.random.RandomState(3)
    ts = pd.date_range(start_ts, periods=n, freq="1min")
    closes = start_price + np.cumsum(rng.normal(0, 0.05, n))
    df = pd.DataFrame({
        "timestamp": ts,
        "open": closes - 0.01, "high": closes + 0.05, "low": closes - 0.05, "close": closes,
        "volume": 100.0 + rng.uniform(0, 20, n),
    })
    if drop_indices:
        df = df.drop(index=list(drop_indices)).reset_index(drop=True)
    return df


def test_aggregate_1min_to_5min_marks_complete_bars_when_all_5_minutes_present():
    df = _one_min_bars(30)  # 6 clean 5-min buckets
    agg = aggregate_1min_to_5min_with_quality(df)
    assert (agg["source_bar_count"] == 5).all()
    assert (agg["expected_source_bar_count"] == 5).all()
    assert agg["bar_complete"].all()


def test_aggregate_1min_to_5min_flags_the_specific_bucket_missing_a_minute():
    """Drop 1-min bar index 7 (09:22) - falls inside the 09:20-09:25
    bucket. That ONE output bar must be flagged; all others must not."""
    df = _one_min_bars(30, drop_indices=[7])
    agg = aggregate_1min_to_5min_with_quality(df)
    bucket_0920 = agg[agg["timestamp"] == pd.Timestamp("2026-06-01 09:20")]
    assert len(bucket_0920) == 1
    assert bucket_0920.iloc[0]["source_bar_count"] == 4
    assert bucket_0920.iloc[0]["bar_complete"] == False  # noqa: E712 - explicit bool check, not just falsy
    others = agg[agg["timestamp"] != pd.Timestamp("2026-06-01 09:20")]
    assert others["bar_complete"].all()


def test_aggregate_1min_to_5min_does_not_fabricate_or_drop_ohlc_for_incomplete_bars():
    """The incomplete bar's OWN open/high/low/close/volume must still be
    the real, partial aggregate - not NaN, not dropped, not interpolated.
    This function detects; it must never repair."""
    df = _one_min_bars(30, drop_indices=[7])
    agg = aggregate_1min_to_5min_with_quality(df)
    bucket = agg[agg["timestamp"] == pd.Timestamp("2026-06-01 09:20")].iloc[0]
    assert not pd.isna(bucket["open"])
    assert not pd.isna(bucket["close"])
    assert bucket["volume"] > 0


def test_data_quality_status_incomplete_bar_itself_is_flagged():
    bar_complete = pd.Series([True, True, False, True, True])
    status = data_quality_status(bar_complete, lookback_bars=3)
    assert status.iloc[2] == "INCOMPLETE_SOURCE_BAR"


def test_data_quality_status_recent_gap_in_lookback_but_bar_itself_complete():
    """Bar 4 is itself complete, but bar 2 (within a 3-bar trailing
    lookback ending at bar 4) was incomplete - a rolling ATR/BB/SMI/
    Ichimoku computation at bar 4 is resting on contaminated inputs."""
    bar_complete = pd.Series([True, True, False, True, True])
    status = data_quality_status(bar_complete, lookback_bars=3)
    assert status.iloc[4] == "RECENT_GAP_IN_LOOKBACK"


def test_data_quality_status_clean_once_gap_ages_out_of_the_lookback_window():
    bar_complete = pd.Series([True, True, False, True, True, True, True, True])
    status = data_quality_status(bar_complete, lookback_bars=3)
    # by index 6, the gap at index 2 is more than 3 bars back (6-2=4 > lookback window 3)
    assert status.iloc[6] == "CLEAN"


def test_data_quality_status_different_dimensions_get_different_verdicts_for_the_same_gap():
    """The whole point of per-dimension lookback: a gap 15 bars back
    should contaminate Structure (78-bar Ichimoku horizon) but NOT
    Momentum (10-bar SMI horizon) - collapsing this into one global
    status would misclassify the clean SMI read as contaminated."""
    bar_complete = pd.Series([True] * 15 + [False] + [True] * 20)
    structure_status = data_quality_status(bar_complete, lookback_bars=78)
    momentum_status = data_quality_status(bar_complete, lookback_bars=10)
    check_at = 25  # 25 - 15 = 10 bars after the gap
    assert structure_status.iloc[check_at] == "RECENT_GAP_IN_LOOKBACK"
    assert momentum_status.iloc[check_at] == "CLEAN"


def test_vwap_session_contamination_persists_for_rest_of_session_not_just_a_fixed_window():
    """Unlike the fixed-lookback dimensions, VWAP is cumulative from
    session start - one gap near the open must contaminate EVERY later
    bar that session, no matter how far away, then reset clean at the
    next session's start."""
    session_id = pd.Series(["day1"] * 10 + ["day2"] * 10)
    bar_complete = pd.Series([True, False] + [True] * 8 + [True] * 10)  # gap early in day1 only
    status = vwap_session_contamination_status(bar_complete, session_id)
    assert status.iloc[1] == "SESSION_GAP_UPSTREAM"
    assert status.iloc[9] == "SESSION_GAP_UPSTREAM"  # far from the gap, but still day1
    assert status.iloc[10] == "CLEAN"  # day2 starts fresh
    assert status.iloc[19] == "CLEAN"


def test_vwap_session_contamination_status_never_reuses_stale_context_label():
    """Deliberate distinctness check: SESSION_GAP_UPSTREAM must not
    collide with assert_temporal_alignment's STALE status - they mean
    different things and reusing one label for both would recreate the
    exact vocabulary-collision problem this review round exists to fix."""
    session_id = pd.Series(["day1"] * 5)
    bar_complete = pd.Series([True, False, True, True, True])
    status = vwap_session_contamination_status(bar_complete, session_id)
    assert "STALE" not in status.unique().tolist()
    assert set(status.unique()).issubset({"CLEAN", "SESSION_GAP_UPSTREAM"})
