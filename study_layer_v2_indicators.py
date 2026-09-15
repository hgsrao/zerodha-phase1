"""Study Layer V2 (2026-08-25) — restoring signal character, not just adding weights.

Root cause diagnosed 2026-08-25 (see the owner's own review, confirmed
against chart_studies_indicators.py's real code): the composite vote
treats every study as an interchangeable +-1, regardless of whether it
is a LEADING/projective signal or a REACTIVE/coincident one. Confirmed
concretely: chart_studies_indicators.ichimoku() explicitly disables the
classic forward (kijun-period) shift of the Senkou spans - the exact
property that makes Ichimoku a projection rather than a same-bar
moving-average check. That function and this one are NOT the same code;
chart_studies_indicators.py is untouched by this file, per owner
instruction not to touch the live Chart Studies Monitor.

This module is Step 1 only: restore genuine Ichimoku projection, and
provide the tooling to measure how correlated (redundant) the existing
votes actually are against real history, before any weighting scheme is
built. Read-only, pure computation, no Kite/network calls - same
convention as chart_studies_indicators.py's own module docstring.

DEPLOYMENT REQUIREMENT (2026-08-25, owner-flagged): ichimoku_projected()
needs, per span: SenkouA_projected = Kijun(26) + the 26-bar shift = 52
bars; SenkouB_projected = SenkouB(52) + the 26-bar shift = 78 bars -
SenkouB is the binding constraint, and classify_ichimoku_projected()
requires BOTH spans, so the full cloud read needs ~78 5-min bars. An NSE
session is only ~75 5-min bars (375 min / 5), so a component that resets
its bar history at 09:15 every session would never classify anything but
NEUTRAL - it would run out the entire day one span short. This is a real
risk in principle but NOT present in the actual live monitor: verified
against run_chart_studies_live_monitor.py, whose bootstrap() pulls
BOOTSTRAP_MINUTE_HISTORY_DAYS=3 days of history (~225 5-min bars after
resample) and poll() appends without truncation - see
chart_studies_bars_<SYMBOL>.csv on disk, which carries multiple trading
days, not one. Any caller of this module MUST feed that same kind of
multi-day-accumulated bar series, never a single-session buffer; see
test_insufficient_single_session_history_leaves_cloud_entirely_nan in
the test file, which turns this requirement into an executable check
rather than a comment someone can miss.

ANTI-LOOKAHEAD (2026-08-25, owner-requested general regression):
every study function here must satisfy - for any bar t - that recomputing
with additional bars appended AFTER t never changes the value already
produced for t. Enforced generically (not just for Ichimoku) in
test_no_lookahead_regression.py. No Chikou span exists yet in this
module; when one is added (today's close plotted `kijun` bars BACKWARD),
it must be built the same direction as the projected Senkou spans - only
ever reading OLDER data into a later row, never a later close into an
earlier one - and must pass the same regression suite before use.
"""
from __future__ import annotations

import pandas as pd


def ichimoku_projected(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26,
                        senkou_b: int = 52) -> pd.DataFrame:
    """The classic, genuinely projective Ichimoku: on a real chart, Senkou
    Span A/B computed from data ending at bar t are PLOTTED `kijun`
    periods ahead, at position t+kijun. Equivalently, the cloud value
    aligned to TODAY (position i) is whatever an EARLIER bar (i - kijun)
    computed - not today's own just-formed midpoint. That earlier data
    already exists in history, so this is fully available for a live
    read against the most recent bar; the honest cost lands at the
    OTHER end - the first `kijun` bars of the whole series have no
    earlier bar to pull a projection from, and are NaN. Compare to
    chart_studies_indicators.ichimoku(), which uses each bar's own
    same-day midpoint instead - a same-bar reactive read wearing
    Ichimoku's name, not a projection of older, price-independent
    information onto today."""
    df = df.copy()
    high, low = df["high"], df["low"]
    df["Tenkan"] = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    df["Kijun"] = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
    raw_senkou_a = (df["Tenkan"] + df["Kijun"]) / 2
    raw_senkou_b = (high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2
    # The forward shift: bar i's PROJECTED cloud (what an earlier bar
    # calculated about today) is bar (i - kijun)'s raw computed value.
    df["SenkouA_projected"] = raw_senkou_a.shift(kijun)
    df["SenkouB_projected"] = raw_senkou_b.shift(kijun)
    return df


def classify_ichimoku_projected(row) -> str:
    """Same BULLISH/BEARISH/NEUTRAL vocabulary as
    chart_studies_indicators.classify_row(), applied to the genuinely
    projected cloud instead of the same-bar one."""
    close = row["close"]
    if pd.isna(row.get("SenkouA_projected")) or pd.isna(row.get("SenkouB_projected")) \
            or pd.isna(row.get("Tenkan")) or pd.isna(row.get("Kijun")):
        return "NEUTRAL"
    cloud_top = max(row["SenkouA_projected"], row["SenkouB_projected"])
    cloud_bottom = min(row["SenkouA_projected"], row["SenkouB_projected"])
    if close > cloud_top and row["Tenkan"] > row["Kijun"]:
        return "BULLISH"
    if close < cloud_bottom and row["Tenkan"] < row["Kijun"]:
        return "BEARISH"
    return "NEUTRAL"


def bollinger_read(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Same read chart_studies_indicators.py uses for Bollinger - close
    vs. basis - kept identical here so correlation measurement below
    compares like for like, not a redesigned version of Bollinger too."""
    basis = df["close"].rolling(period).mean()
    return (df["close"] > basis).map({True: "BULLISH", False: "BEARISH"}).where(basis.notna(), "NEUTRAL")


def vwap_read(df: pd.DataFrame) -> pd.Series:
    """Same read as chart_studies_indicators.py's session VWAP - close vs.
    cumulative-typical-price VWAP within each trading day."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    grp = df["timestamp"].dt.normalize()
    vwap = pv.groupby(grp).cumsum() / df["volume"].groupby(grp).cumsum().replace(0, pd.NA)
    return (df["close"] > vwap).map({True: "BULLISH", False: "BEARISH"}).where(vwap.notna(), "NEUTRAL")


def pairwise_agreement_rate(read_a: pd.Series, read_b: pd.Series) -> dict:
    """How often two studies' BULLISH/BEARISH reads agree, restricted to
    bars where BOTH have a real (non-NEUTRAL) opinion - the honest
    question 'when both are willing to vote, do they vote the same way',
    not diluted by bars where one or both are silent. A rate near 50%
    means the two are close to independent; a rate near 100% means one is
    largely redundant with the other."""
    both_opinionated = (read_a != "NEUTRAL") & (read_b != "NEUTRAL")
    n = int(both_opinionated.sum())
    if n == 0:
        return {"n_bars_both_opinionated": 0, "agreement_rate": None}
    agree = (read_a[both_opinionated] == read_b[both_opinionated]).sum()
    return {"n_bars_both_opinionated": n, "agreement_rate": float(agree) / n}
