"""Tests for study_layer_v2_indicators.py. All synthetic data - no real
Kite/network calls, matching this project's established test convention."""
from __future__ import annotations

import pandas as pd
import pytest

from study_layer_v2_indicators import (
    bollinger_read, classify_ichimoku_projected, ichimoku_projected,
    pairwise_agreement_rate, vwap_read,
)
# classify_ichimoku_projected is exercised directly in the deployment-
# requirement test below; import kept explicit rather than star-imported.


def _bars(n, start_price=100.0, step=1.0, start_ts="2026-08-20 09:15", freq="5min", volume=1000.0):
    ts = pd.date_range(start_ts, periods=n, freq=freq)
    closes = [start_price + i * step for i in range(n)]
    rows = [{"timestamp": t, "open": c - 0.2, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": volume}
            for t, c in zip(ts, closes)]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# ichimoku_projected - proving the shift actually changes behavior
# ---------------------------------------------------------------------------
def test_projected_cloud_is_nan_only_for_the_earliest_kijun_bars():
    """shift(kijun) pulls SenkouA_projected[i] from raw_senkou_a[i-kijun] -
    i.e. the value plotted at TODAY was computed by an EARLIER bar. The
    gap this creates is at the START of the series (no bar before index 0
    to have computed it), not the end - the most recent bars, which is
    what a live read actually needs, are exactly the ones that ARE
    available, using older, already-known data. (An earlier draft of
    this test asserted the opposite - a real bug in the test itself,
    caught before it could hide a real bug in the code.)"""
    df = _bars(80, start_price=100.0, step=1.0)
    out = ichimoku_projected(df, kijun=26)
    assert out["SenkouA_projected"].tail(10).notna().all()
    assert out["SenkouA_projected"].head(26).isna().all()


def test_insufficient_single_session_history_leaves_cloud_entirely_nan():
    """Owner-flagged deployment hazard (2026-08-25). Precise requirement,
    worked out per span (an earlier draft of this test asserted BOTH
    spans are NaN for a single ~75-bar session, which is wrong for
    SenkouA and was caught only by actually running it - see below):
      - SenkouA_projected needs Kijun(26) + the 26-bar shift = 52 bars.
      - SenkouB_projected needs SenkouB(52) + the 26-bar shift = 78 bars
        - THIS is the binding constraint, and an NSE session is only
        ~75 5-min bars (375/5), so SenkouB_projected - and therefore the
        full CLOUD (classify_ichimoku_projected requires both spans) -
        is unavailable for an entire single-session buffer. A component
        that resets its bar history at 09:15 every morning would never
        classify anything but NEUTRAL. This module must always be fed
        multi-day-accumulated history (as the real live monitor's
        bootstrap() + poll() already do - verified against
        run_chart_studies_live_monitor.py, not assumed)."""
    single_session = _bars(75, start_price=100.0, step=0.5)  # one day's worth only
    out = ichimoku_projected(single_session, kijun=26)
    assert out["SenkouA_projected"].tail(20).notna().all()  # available from bar 52 onward
    assert out["SenkouB_projected"].isna().all()  # never available - needs 78, session has 75

    classifications = [classify_ichimoku_projected(row) for _, row in out.iterrows()]
    assert all(c == "NEUTRAL" for c in classifications), \
        "a single-session buffer produced a non-NEUTRAL cloud read - it shouldn't have enough history to"

    two_plus_sessions = _bars(150, start_price=100.0, step=0.5)  # ~2 sessions, clears 78
    out2 = ichimoku_projected(two_plus_sessions, kijun=26)
    assert out2["SenkouA_projected"].tail(10).notna().all()
    assert out2["SenkouB_projected"].tail(10).notna().all()


def test_projected_cloud_differs_from_same_bar_value_in_a_trend_reversal():
    """The whole point: in a market that trended up then reversed down,
    the PROJECTED cloud (calculated from the earlier uptrend) and the
    SAME-BAR cloud (calculated from the current downtrend) must disagree
    at some point - if they never differ, the shift changed nothing."""
    up = _bars(60, start_price=100.0, step=2.0, start_ts="2026-08-20 09:15")
    down_start = up["close"].iloc[-1]
    down = _bars(60, start_price=down_start, step=-2.0, start_ts="2026-08-24 09:15")
    df = pd.concat([up, down], ignore_index=True)

    projected = ichimoku_projected(df, kijun=26)
    same_bar_a = (projected["Tenkan"] + projected["Kijun"]) / 2  # what chart_studies_indicators.py uses instead

    valid = projected["SenkouA_projected"].notna()
    differs = (projected.loc[valid, "SenkouA_projected"] - same_bar_a[valid]).abs() > 0.01
    assert differs.any(), "the projected and same-bar cloud values never differed - the shift had no effect"


def test_classify_projected_bullish_when_close_above_projected_cloud_and_tenkan_above_kijun():
    row = pd.Series({
        "close": 110.0, "Tenkan": 108.0, "Kijun": 102.0,
        "SenkouA_projected": 100.0, "SenkouB_projected": 95.0,
    })
    assert classify_ichimoku_projected(row) == "BULLISH"


def test_classify_projected_neutral_when_projection_not_yet_available():
    row = pd.Series({"close": 110.0, "Tenkan": 108.0, "Kijun": 102.0,
                      "SenkouA_projected": None, "SenkouB_projected": None})
    assert classify_ichimoku_projected(row) == "NEUTRAL"


# ---------------------------------------------------------------------------
# bollinger_read / vwap_read - same reads as chart_studies_indicators.py,
# kept identical so correlation measurement compares like for like
# ---------------------------------------------------------------------------
def test_bollinger_read_bullish_above_basis():
    df = _bars(25, start_price=100.0, step=1.0)
    reads = bollinger_read(df, period=20)
    assert reads.iloc[-1] == "BULLISH"  # trending up -> close above its own rolling mean


def test_vwap_read_resets_and_reads_per_session():
    day1 = _bars(3, start_price=100.0, step=0.0, start_ts="2026-08-20 09:15")
    day2 = _bars(3, start_price=200.0, step=0.0, start_ts="2026-08-21 09:15")
    df = pd.concat([day1, day2], ignore_index=True)
    reads = vwap_read(df)
    assert reads.iloc[-1] in ("BULLISH", "BEARISH")  # has an opinion once VWAP is defined


# ---------------------------------------------------------------------------
# pairwise_agreement_rate
# ---------------------------------------------------------------------------
def test_agreement_rate_is_100pct_for_identical_series():
    a = pd.Series(["BULLISH", "BEARISH", "BULLISH", "BEARISH"])
    result = pairwise_agreement_rate(a, a)
    assert result["agreement_rate"] == pytest.approx(1.0)
    assert result["n_bars_both_opinionated"] == 4


def test_agreement_rate_is_0pct_for_perfectly_opposite_series():
    a = pd.Series(["BULLISH", "BEARISH", "BULLISH", "BEARISH"])
    b = pd.Series(["BEARISH", "BULLISH", "BEARISH", "BULLISH"])
    result = pairwise_agreement_rate(a, b)
    assert result["agreement_rate"] == pytest.approx(0.0)


def test_agreement_rate_ignores_bars_where_either_is_neutral():
    a = pd.Series(["BULLISH", "NEUTRAL", "BULLISH", "BEARISH"])
    b = pd.Series(["BULLISH", "BULLISH", "NEUTRAL", "BEARISH"])
    result = pairwise_agreement_rate(a, b)
    # only indices 0 and 3 have both opinionated - both agree
    assert result["n_bars_both_opinionated"] == 2
    assert result["agreement_rate"] == pytest.approx(1.0)


def test_agreement_rate_none_when_never_both_opinionated():
    a = pd.Series(["NEUTRAL", "NEUTRAL"])
    b = pd.Series(["BULLISH", "BEARISH"])
    result = pairwise_agreement_rate(a, b)
    assert result["n_bars_both_opinionated"] == 0
    assert result["agreement_rate"] is None
