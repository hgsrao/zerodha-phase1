"""Extends the anti-lookahead invariant to every function in
study_layer_v2_state_vector.py, per the owner's own wording (2026-08-25):

    "Alter every bar after timestamp t. Every normalized state at or
    before t must remain bit-identical. That should become a
    system-wide causality invariant."

Same structure as test_no_lookahead_regression.py: recompute with extra
bars appended AFTER a checkpoint t and confirm the value AT t never
moves. All synthetic data, no Kite/network calls.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from study_layer_v2_state_vector import (
    average_true_range, compute_state_vector, location_bb_score, location_vwap_score,
    momentum_delta, momentum_score, regime_score, structure_score, volatility_percentile,
)


def _multi_session_bars(n=700, start_price=100.0, seed=1):
    rng = np.random.RandomState(seed)
    ts = pd.date_range("2026-01-05 09:15", periods=n, freq="5min")
    drift_regimes = np.where((np.arange(n) // 75) % 2 == 0, 0.25, -0.2)  # alternating up/down "sessions"
    closes = start_price + np.cumsum(drift_regimes + rng.normal(0, 0.2, n))
    highs = closes + np.abs(rng.normal(0.4, 0.1, n))
    lows = closes - np.abs(rng.normal(0.4, 0.1, n))
    opens = closes - rng.normal(0, 0.1, n)
    volumes = 1000.0 + rng.uniform(0, 300, n)
    return pd.DataFrame({"timestamp": ts, "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes})


FULL_DF = _multi_session_bars()
CHECK_INDICES = [520, 560, 600, 640, len(FULL_DF) - 1]


def _assert_series_unaffected(fn, t, cutoff_extra, **kwargs):
    truth = fn(FULL_DF.iloc[: t + 1], **kwargs).iloc[t]
    with_future = fn(FULL_DF.iloc[: t + 1 + cutoff_extra], **kwargs).iloc[t]
    if pd.isna(truth):
        assert pd.isna(with_future), f"{fn.__name__} at t={t}: was NaN, became {with_future} with {cutoff_extra} future bars"
    else:
        assert truth == pytest.approx(with_future), \
            f"{fn.__name__} at t={t} changed from {truth} to {with_future} after appending {cutoff_extra} future bars"


@pytest.mark.parametrize("cutoff_extra", [0, 1, 10, 50])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_average_true_range_unaffected_by_future_bars(t, cutoff_extra):
    _assert_series_unaffected(average_true_range, t, cutoff_extra)


@pytest.mark.parametrize("cutoff_extra", [0, 1, 10, 50])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_structure_score_unaffected_by_future_bars(t, cutoff_extra):
    def fn(df):
        return structure_score(df, average_true_range(df))
    truth = fn(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = fn(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    if pd.isna(truth):
        assert pd.isna(with_future)
    else:
        assert truth == pytest.approx(with_future)


@pytest.mark.parametrize("cutoff_extra", [0, 1, 10, 50])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_location_vwap_score_unaffected_by_future_bars(t, cutoff_extra):
    def fn(df):
        atr = average_true_range(df)
        typical = (df["high"] + df["low"] + df["close"]) / 3
        pv = typical * df["volume"]
        grp = df["timestamp"].dt.normalize()
        vwap = pv.groupby(grp).cumsum() / df["volume"].groupby(grp).cumsum().replace(0, np.nan)
        return location_vwap_score(df, vwap, atr)
    truth = fn(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = fn(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    if pd.isna(truth):
        assert pd.isna(with_future)
    else:
        assert truth == pytest.approx(with_future)


@pytest.mark.parametrize("cutoff_extra", [0, 1, 10, 50])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_location_bb_score_unaffected_by_future_bars(t, cutoff_extra):
    def fn(df):
        basis = df["close"].rolling(20).mean()
        std = df["close"].rolling(20).std()
        return location_bb_score(df, basis, std)
    truth = fn(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = fn(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    if pd.isna(truth):
        assert pd.isna(with_future)
    else:
        assert truth == pytest.approx(with_future)


@pytest.mark.parametrize("cutoff_extra", [0, 1, 10, 50])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_momentum_score_and_delta_unaffected_by_future_bars(t, cutoff_extra):
    def smi(df, k_period=10, d_period=3):
        high, low, close = df["high"], df["low"], df["close"]
        hh, ll = high.rolling(k_period).max(), low.rolling(k_period).min()
        mid = (hh + ll) / 2
        diff, rng_ = close - mid, hh - ll
        d1 = diff.ewm(span=d_period, adjust=False).mean()
        d2 = d1.ewm(span=d_period, adjust=False).mean()
        r1 = rng_.ewm(span=d_period, adjust=False).mean()
        r2 = r1.ewm(span=d_period, adjust=False).mean()
        return (100.0 * (d2 / (r2 / 2))).where(r2 != 0)

    def fn_m(df):
        return momentum_score(smi(df))

    def fn_dm(df):
        return momentum_delta(momentum_score(smi(df)))

    for fn in (fn_m, fn_dm):
        truth = fn(FULL_DF.iloc[: t + 1]).iloc[t]
        with_future = fn(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
        if pd.isna(truth):
            assert pd.isna(with_future)
        else:
            assert truth == pytest.approx(with_future)


@pytest.mark.parametrize("cutoff_extra", [0, 1, 10, 50])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_regime_score_unaffected_by_future_bars(t, cutoff_extra):
    def fn(df):
        return regime_score(df, average_true_range(df))
    truth = fn(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = fn(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    if pd.isna(truth):
        assert pd.isna(with_future)
    else:
        assert truth == pytest.approx(with_future)


@pytest.mark.parametrize("cutoff_extra", [0, 1, 10, 50])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_volatility_percentile_unaffected_by_future_bars(t, cutoff_extra):
    """The one dimension with a genuinely bounded TRAILING (not
    expanding) window - checkpoints here are chosen comfortably past
    window=500 so the trailing window itself is fully populated from
    bars <= t, and appending future bars must not perturb it."""
    def fn(df):
        return volatility_percentile(df, average_true_range(df), window=500)
    truth = fn(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = fn(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    if pd.isna(truth):
        assert pd.isna(with_future)
    else:
        assert truth == pytest.approx(with_future)


# Owner round-2: "randomized across many cut points, not one" - CHECK_INDICES
# alone (5 fixed points) isn't enough; sample broadly across the valid
# range with a fixed seed so the run is reproducible but the coverage is
# wide, not hand-picked.
_RNG = np.random.RandomState(2026)
RANDOM_PREFIX_CHECKPOINTS = sorted(set(_RNG.randint(100, len(FULL_DF), size=40).tolist()))


@pytest.mark.parametrize("t", RANDOM_PREFIX_CHECKPOINTS)
def test_prefix_equivalence_full_dataset_vs_exact_prefix(t):
    """Owner-specified stronger invariant, added explicitly rather than
    relying on the parametrized future-mutation tests' transitivity: the
    state at bar t computed from the FULL dataset (all 700 rows) must be
    IDENTICAL to the state computed from the EXACT prefix ending at t.
    This rules out a class of bug the parametrized tests structurally
    cannot (e.g. something tied to the full dataset's own identity/size,
    not just "some future rows exist"). Randomized across ~40 cut points
    per the owner's explicit request, not just the original 5 fixed
    CHECK_INDICES - this is meant to catch centered rolling windows,
    negative shifts, full-frame normalization, backfill, or future-aware
    interpolation, any of which could slip through a small fixed sample."""
    full_state = compute_state_vector(FULL_DF)
    prefix_state = compute_state_vector(FULL_DF.iloc[: t + 1])
    for field in ["structure", "location_vwap", "location_bb", "momentum", "momentum_delta", "volatility_pct"]:
        a, b = full_state[field].iloc[t], prefix_state[field].iloc[t]
        if pd.isna(a):
            assert pd.isna(b), f"{field} at t={t}: full-dataset computation is NaN but prefix computation is {b}"
        else:
            assert a == pytest.approx(b), \
                f"{field} at t={t}: full-dataset value {a} != prefix-only value {b} - a lookahead leak"


def test_compute_state_vector_orchestrator_unaffected_by_future_bars():
    """One integration-level check on the full orchestrator, on top of
    the per-function checks above."""
    t = 600
    truth = compute_state_vector(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = compute_state_vector(FULL_DF.iloc[: t + 31]).iloc[t]
    for field in ["structure", "location_vwap", "location_bb", "momentum", "momentum_delta", "volatility_pct"]:
        a, b = truth[field], with_future[field]
        if pd.isna(a):
            assert pd.isna(b), f"{field} leaked future data at t={t}"
        else:
            assert a == pytest.approx(b), f"{field} leaked future data at t={t}: {a} -> {b}"
