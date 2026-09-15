"""
Chart-studies indicator math + composite entry/exit state machine.

Pure computation, no Kite/network calls anywhere in this file — every
function takes a pandas DataFrame (or plain values) and returns one. The
exact rule these implement is documented (and was written down BEFORE any
live output was looked at) in CHART_STUDIES_SIGNAL_RULE_20260824.md; keep
this file and that document in sync if either changes.

Input DataFrames are expected sorted ascending by timestamp with columns
timestamp/open/high/low/close/volume — the same shape
r1c_live_kite_client.get_minute_bars() already returns per row, just
resampled to 5-minute bars by the caller before these functions see it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Individual indicators
# ---------------------------------------------------------------------------
def ichimoku(df: pd.DataFrame, tenkan=9, kijun=26, senkou_b=52) -> pd.DataFrame:
    df = df.copy()
    high, low = df["high"], df["low"]
    df["Tenkan"] = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    df["Kijun"] = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
    # Senkou spans are conventionally plotted `kijun` periods ahead, but
    # for a live bullish/bearish READ against the CURRENT bar we want the
    # cloud boundary that would have been projected onto today by an
    # earlier bar - i.e. no forward shift here. This is a live monitor
    # decision, not a charting decision: shifting here would place NaN at
    # the most recent bars (nothing to shift into it from beyond the data
    # we have), which is exactly where a live read needs a value most.
    df["SenkouA"] = (df["Tenkan"] + df["Kijun"]) / 2
    df["SenkouB"] = (high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2
    return df


def bollinger_bands(df: pd.DataFrame, period=20, num_std=2.0) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]
    df["BB_Basis"] = close.rolling(period).mean()
    std = close.rolling(period).std()
    df["BB_Upper"] = df["BB_Basis"] + num_std * std
    df["BB_Lower"] = df["BB_Basis"] - num_std * std
    return df


def stochastic_momentum_index(df: pd.DataFrame, k_period=10, d_period=3, ema_period=3) -> pd.DataFrame:
    """Standard SMI: distance of close from the midpoint of the recent
    high/low range, double-EMA-smoothed, then a signal-line EMA on top."""
    df = df.copy()
    high, low, close = df["high"], df["low"], df["close"]
    hh = high.rolling(k_period).max()
    ll = low.rolling(k_period).min()
    midpoint = (hh + ll) / 2
    diff = close - midpoint
    rng = (hh - ll)

    diff_ema1 = diff.ewm(span=d_period, adjust=False).mean()
    diff_ema2 = diff_ema1.ewm(span=d_period, adjust=False).mean()
    rng_ema1 = rng.ewm(span=d_period, adjust=False).mean()
    rng_ema2 = rng_ema1.ewm(span=d_period, adjust=False).mean()

    # Guard divide-by-zero (rng_ema2 == 0 happens on a dead-flat opening
    # print, e.g. an illiquid symbol's first bar) - SMI undefined there,
    # not "extremely large"; NaN is the honest value, not 0 or +-inf.
    smi = (100.0 * (diff_ema2 / (rng_ema2 / 2))).where(rng_ema2 != 0)
    df["SMI"] = smi
    df["SMI_Signal"] = smi.ewm(span=ema_period, adjust=False).mean()
    return df


def session_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Cumulative typical-price VWAP, resetting every trading day. `df`
    must already carry a `TradingDay` column (date, no time component)."""
    df = df.copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    grp = df["TradingDay"]
    df["VWAP"] = pv.groupby(grp).cumsum() / df["volume"].groupby(grp).cumsum().replace(0, pd.NA)
    return df


def anchored_vwap(df: pd.DataFrame, anchor_ts, running_pv: float = 0.0, running_vol: float = 0.0) -> pd.DataFrame:
    """Cumulative typical-price VWAP from `anchor_ts` onward, never
    resetting. `running_pv`/`running_vol` let the caller carry forward a
    base accumulated from bars/days before `df` (e.g. the historical
    bootstrap fetched once at monitor startup) instead of requiring every
    single bar back to the anchor date to be re-passed in on every call."""
    df = df.copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    in_scope = df["timestamp"] >= pd.Timestamp(anchor_ts)
    cum_pv = pv.where(in_scope, 0.0).cumsum() + running_pv
    cum_vol = df["volume"].where(in_scope, 0.0).cumsum() + running_vol
    df["AnchoredVWAP"] = (cum_pv / cum_vol.replace(0, pd.NA))
    return df


# ---------------------------------------------------------------------------
# Per-bar classification and the 5-way composite
# ---------------------------------------------------------------------------
BULLISH, BEARISH, NEUTRAL = "BULLISH", "BEARISH", "NEUTRAL"


def classify_row(row) -> Dict[str, str]:
    """Reads ONE fully-computed row (must already have every indicator
    column from the functions above) and returns each study's
    BULLISH/BEARISH/NEUTRAL read plus the net composite score."""
    reads: Dict[str, str] = {}

    close = row["close"]
    if pd.isna(row.get("SenkouA")) or pd.isna(row.get("SenkouB")) or pd.isna(row.get("Tenkan")) or pd.isna(row.get("Kijun")):
        reads["ichimoku"] = NEUTRAL
    else:
        cloud_top = max(row["SenkouA"], row["SenkouB"])
        cloud_bottom = min(row["SenkouA"], row["SenkouB"])
        if close > cloud_top and row["Tenkan"] > row["Kijun"]:
            reads["ichimoku"] = BULLISH
        elif close < cloud_bottom and row["Tenkan"] < row["Kijun"]:
            reads["ichimoku"] = BEARISH
        else:
            reads["ichimoku"] = NEUTRAL

    if pd.isna(row.get("BB_Basis")):
        reads["bollinger"] = NEUTRAL
    else:
        reads["bollinger"] = BULLISH if close > row["BB_Basis"] else BEARISH

    if pd.isna(row.get("SMI")) or pd.isna(row.get("SMI_Signal")):
        reads["smi"] = NEUTRAL
    else:
        reads["smi"] = BULLISH if row["SMI"] > row["SMI_Signal"] else BEARISH

    if pd.isna(row.get("VWAP")):
        reads["vwap"] = NEUTRAL
    else:
        reads["vwap"] = BULLISH if close > row["VWAP"] else BEARISH

    if pd.isna(row.get("AnchoredVWAP")):
        reads["anchored_vwap"] = NEUTRAL
    else:
        reads["anchored_vwap"] = BULLISH if close > row["AnchoredVWAP"] else BEARISH

    # 2026-08-24 addendum (see CHART_STUDIES_SIGNAL_RULE_20260824.md, "what
    # can we make better" section): anchored_vwap is still computed and
    # returned in `reads` (displayed/logged same as before) but is NOT
    # counted in the composite vote below. Owner observed it stayed
    # BULLISH the entire 2026-08-24 session (anchored to 2026-03-02, ~6
    # months back) while the 4 faster studies genuinely flipped bearish -
    # an equal-weighted static/slow vote was quietly capping how bearish
    # the composite could read. VOTING_STUDIES excludes it; all 4 in
    # VOTING_STUDIES are still weighted equally to each other.
    score = sum(1 for k in VOTING_STUDIES if reads[k] == BULLISH) - sum(1 for k in VOTING_STUDIES if reads[k] == BEARISH)
    reads["score"] = score
    return reads


VOTING_STUDIES = ("ichimoku", "bollinger", "smi", "vwap")  # anchored_vwap excluded - see above
ENTRY_THRESHOLD = 3   # score must reach >= this, from below, to fire ENTRY (of 4 voting studies now, was /5 before 2026-08-24)
EXIT_THRESHOLD = 0    # score must fall to <= this, from above, to fire EXIT

# 2026-08-24 addendum: a single closed 5-min bar crossing the threshold used
# to fire immediately (see the old edge-triggered version of update() this
# replaced). The owner's first live day showed 2 of 3 SUNPHARMA round-trips
# were single-bar-driven chop that reversed within the hour. Requiring the
# condition to hold for CONFIRMATION_BARS consecutive closed bars is a
# standard whipsaw filter - signals arrive slightly later, with fewer false
# starts. Disclosed, not swept/optimized - same convention as
# ENTRY_THRESHOLD/EXIT_THRESHOLD above.
CONFIRMATION_BARS = 2

# 2026-08-24 addendum: a backstop ONLY - the composite's own EXIT_THRESHOLD
# crossing (now debounced above) is still the primary, intended exit. This
# fires independently of the composite score when price has already moved
# further against the entry than that. Disclosed, not swept/optimized.
HARD_STOP_PCT = 0.005


def hard_stop_price(entry_price: float) -> float:
    return entry_price * (1.0 - HARD_STOP_PCT)


def is_hard_stop_breached(entry_price: float, latest_close: float) -> bool:
    return latest_close <= hard_stop_price(entry_price)


# ---------------------------------------------------------------------------
# GREEN / AMBER / RED projection (2026-08-24 addendum)
# ---------------------------------------------------------------------------
# The owner's report described this qualitatively ("momentum weakening,
# lower high, volatility compression, movement toward support") - not
# implementable as written, no swing-detection or volatility-compression
# algorithm exists anywhere in this file. Reduced here to the two numbers
# already being computed every bar, no new inputs: GREEN/RED are exactly
# the existing ENTRY_THRESHOLD/EXIT_THRESHOLD boundaries re-read as a
# projection rather than a transition event; AMBER is the gap between
# them. Fully derived, testable, disclosed - not swept/optimized.
GREEN, AMBER, RED = "GREEN", "AMBER", "RED"


def project_state(score: int) -> str:
    if score >= ENTRY_THRESHOLD:
        return GREEN   # setup aligned - new entries would be permitted
    if score <= EXIT_THRESHOLD:
        return RED     # exit zone - a held position's exit is confirmed or confirming
    return AMBER        # ambiguous - no new entry; tighten the stop if already IN


# ---------------------------------------------------------------------------
# Next-bar-open execution + ABSTAIN slippage guard (2026-08-24 addendum)
# ---------------------------------------------------------------------------
# Report rule: "Use a marketable-limit/slippage boundary; ABSTAIN if the
# price runs away." No number was given - this is the first-cut, disclosed
# value, same convention as HARD_STOP_PCT above.
ABSTAIN_SLIPPAGE_PCT = 0.003


def evaluate_fill(decision_price: float, fill_open_price: float):
    """Compares the DECISION bar's close (what the composite saw when it
    fired) against the FILL bar's own open (the next bar's real open -
    resampled 5-min bars already carry a genuine 'open' field, so a PAPER
    system can honor "enter/exit at the next bar's open" exactly, without
    needing to act at the literal bar-boundary tick the way a real-money
    system would). Returns ("FILL", slippage_pct) or ("ABSTAIN",
    slippage_pct) - never silently fills through excessive slippage."""
    slippage_pct = abs(fill_open_price - decision_price) / decision_price
    if slippage_pct > ABSTAIN_SLIPPAGE_PCT:
        return "ABSTAIN", slippage_pct
    return "FILL", slippage_pct


@dataclass
class SymbolSignalState:
    state: str = "FLAT"  # FLAT | IN
    last_score: Optional[int] = None
    events: List[dict] = field(default_factory=list)
    streak: int = 0  # consecutive closed bars meeting the pending transition's condition

    def update(self, timestamp, score: int, reads: Dict[str, str]) -> Optional[dict]:
        """Feed one new bar's composite score. Returns the event dict if
        this bar triggered an ENTRY/EXIT transition, else None.

        2026-08-24: now requires CONFIRMATION_BARS consecutive closed bars
        meeting the threshold before firing (previously fired on the first
        bar that crossed it) - see the addendum above `CONFIRMATION_BARS`.
        The streak resets to 0 the moment a bar fails to meet the
        condition, so it must be CONSECUTIVE, not "N times total"."""
        event = None
        if self.state == "FLAT":
            if score >= ENTRY_THRESHOLD:
                self.streak += 1
            else:
                self.streak = 0
            if self.streak >= CONFIRMATION_BARS:
                self.state = "IN"
                self.streak = 0
                event = {"timestamp": str(timestamp), "direction": "ENTRY", "score": score, "reads": dict(reads)}
        elif self.state == "IN":
            if score <= EXIT_THRESHOLD:
                self.streak += 1
            else:
                self.streak = 0
            if self.streak >= CONFIRMATION_BARS:
                self.state = "FLAT"
                self.streak = 0
                event = {"timestamp": str(timestamp), "direction": "EXIT", "score": score, "reads": dict(reads)}
        self.last_score = score
        if event is not None:
            self.events.append(event)
        return event


def compute_all_indicators(df: pd.DataFrame, anchor_ts, running_pv: float = 0.0, running_vol: float = 0.0) -> pd.DataFrame:
    """Convenience: run every indicator function in sequence. `df` must
    have timestamp/open/high/low/close/volume, sorted ascending, plus a
    TradingDay column (date component of timestamp)."""
    df = ichimoku(df)
    df = bollinger_bands(df)
    df = stochastic_momentum_index(df)
    df = session_vwap(df)
    df = anchored_vwap(df, anchor_ts, running_pv=running_pv, running_vol=running_vol)
    return df
