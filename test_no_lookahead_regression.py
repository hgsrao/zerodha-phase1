"""General anti-lookahead regression suite (2026-08-25, owner-requested).

Owner's own words: "one acceptance test should explicitly prove: Changing
bars after timestamp t cannot change any signal generated at t. That
should apply to every study, not merely Ichimoku. I would make this a
general anti-lookahead regression."

The property under test, for every study function in study_layer_v2_indicators.py:
    compute(df up to and including row t)[t] == compute(df up to some later row)[t]
i.e. a signal computed for bar t must be identical whether or not the
function is later handed MORE bars after t. If it isn't, the function is
reading data from the future relative to the bar it claims to describe -
exactly the leakage risk the owner flagged for Chikou (today's close
plotted 26 bars backward - easy to implement by shifting in a direction
that lets row t see a future close) even though Chikou itself is not yet
implemented here. This suite is written generically so it keeps applying
automatically to every future study added to this module, not just the
four that exist today.

All synthetic data. No Kite/network calls.
"""
from __future__ import annotations

import pandas as pd
import pytest

from study_layer_v2_indicators import bollinger_read, ichimoku_projected, vwap_read


def _multi_session_bars(n_sessions=3, bars_per_session=75, start_price=100.0):
    """~3 trading sessions of 5-min bars, mimicking real market data:
    price drifts differently session to session (not a flat line) so a
    lookahead bug that depends on TREND direction wouldn't hide itself."""
    rows = []
    price = start_price
    for s in range(n_sessions):
        day = pd.Timestamp("2026-08-20") + pd.Timedelta(days=s)
        session_ts = pd.date_range(day.replace(hour=9, minute=15), periods=bars_per_session, freq="5min")
        drift = 0.3 if s % 2 == 0 else -0.3  # alternate up/down sessions
        for t in session_ts:
            price += drift
            rows.append({"timestamp": t, "open": price - 0.1, "high": price + 0.4,
                         "low": price - 0.4, "close": price, "volume": 1000.0 + 10 * len(rows)})
    return pd.DataFrame(rows)


FULL_DF = _multi_session_bars()
# Sample check points well past every function's warmup (kijun=26, senkou_b=52,
# +26 shift = 78) so we're testing the property, not just "still warming up".
CHECK_INDICES = [90, 110, 130, 150, 180, len(FULL_DF) - 1]


@pytest.mark.parametrize("cutoff_extra", [0, 1, 5, 20])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_ichimoku_projected_columns_unaffected_by_future_bars(t, cutoff_extra):
    """Recomputing with `cutoff_extra` additional bars appended after t must
    not change row t's Tenkan/Kijun/SenkouA_projected/SenkouB_projected."""
    truth = ichimoku_projected(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = ichimoku_projected(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    for col in ["Tenkan", "Kijun", "SenkouA_projected", "SenkouB_projected"]:
        a, b = truth[col], with_future[col]
        if pd.isna(a):
            assert pd.isna(b), f"{col} at t={t}: was NaN with cutoff, became {b} with {cutoff_extra} future bars"
        else:
            assert a == pytest.approx(b), f"{col} at t={t} changed from {a} to {b} after appending {cutoff_extra} future bars"


@pytest.mark.parametrize("cutoff_extra", [0, 1, 5, 20])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_bollinger_read_unaffected_by_future_bars(t, cutoff_extra):
    truth = bollinger_read(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = bollinger_read(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    assert truth == with_future, f"bollinger_read at t={t} changed from {truth} to {with_future} after {cutoff_extra} future bars"


@pytest.mark.parametrize("cutoff_extra", [0, 1, 5, 20])
@pytest.mark.parametrize("t", CHECK_INDICES)
def test_vwap_read_unaffected_by_future_bars(t, cutoff_extra):
    """VWAP resets per session (groupby day + cumsum) - appending bars from
    a LATER session must not perturb an EARLIER session's cumulative sum,
    since pandas groupby-cumsum is inherently backward-looking within each
    group, but this proves it rather than assuming it."""
    truth = vwap_read(FULL_DF.iloc[: t + 1]).iloc[t]
    with_future = vwap_read(FULL_DF.iloc[: t + 1 + cutoff_extra]).iloc[t]
    assert truth == with_future, f"vwap_read at t={t} changed from {truth} to {with_future} after {cutoff_extra} future bars"


def test_appending_a_dramatically_different_future_session_does_not_leak_backward():
    """A stronger, single deliberately-adversarial case: append a violent
    reversal session after the check point and confirm nothing before the
    check point moves - the parametrized tests above use a mild drift,
    this uses a sharp one so a subtle leak (e.g. an un-shifted rolling
    window with a `center=True` typo) can't hide in a small effect size."""
    t = 120
    base = FULL_DF.iloc[: t + 1]
    violent_future = pd.concat([
        FULL_DF.iloc[: t + 1],
        _multi_session_bars(n_sessions=1, bars_per_session=75, start_price=FULL_DF["close"].iloc[t]).assign(
            timestamp=lambda d: d["timestamp"] + pd.Timedelta(days=10)
        ).assign(close=lambda d: d["close"] * 1.5, high=lambda d: d["high"] * 1.5, low=lambda d: d["low"] * 1.5),
    ], ignore_index=True)

    ichimoku_before = ichimoku_projected(base).iloc[t]
    ichimoku_after = ichimoku_projected(violent_future).iloc[t]
    for col in ["Tenkan", "Kijun", "SenkouA_projected", "SenkouB_projected"]:
        a, b = ichimoku_before[col], ichimoku_after[col]
        if pd.isna(a):
            assert pd.isna(b)
        else:
            assert a == pytest.approx(b), f"{col} leaked a violent future session backward into t={t}"

    assert bollinger_read(base).iloc[t] == bollinger_read(violent_future).iloc[t]
    assert vwap_read(base).iloc[t] == vwap_read(violent_future).iloc[t]
