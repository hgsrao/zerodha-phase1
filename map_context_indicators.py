"""Pure computation for the Map + Context observer (2026-08-25 addendum).

Read-only, no Kite/network calls anywhere in this file - matches this
project's established "pure computation, testable without Kite" split
(see chart_studies_indicators.py's own module docstring for the same
convention). Built from a price-action video review: "the map" (support/
resistance from multi-touch daily levels) and "context" (index regime +
how extended a move already is, used to gate/size, not to enter).

Explicitly informational only - nothing here fires an entry or exit.
Not wired into any live engine. Catalyst/event-calendar awareness (the
third context check in the source video) is NOT implemented - no
earnings/macro calendar exists anywhere in this project yet; disclosed
as unavailable rather than silently omitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# Disclosed, first-cut parameters - not swept/optimized, same convention
# as every other threshold in this project (ENTRY_THRESHOLD, HARD_STOP_PCT, etc.)
SWING_WINDOW = 3          # bars on each side to qualify as a local high/low
CLUSTER_TOLERANCE_PCT = 0.006   # 0.6% - levels within this of each other are "the same" level
SIGNIFICANT_TOUCHES = 3   # touches >= this -> SIGNIFICANT; == 2 -> REAL; == 1 -> INTERESTING
REGIME_SHORT_SMA = 20
REGIME_LONG_SMA = 50


def find_local_extrema(daily: pd.DataFrame, window: int = SWING_WINDOW) -> tuple[list, list]:
    """daily must have columns date/high/low, sorted ascending. Returns
    (local_highs, local_lows), each a list of (date, price)."""
    highs = daily["high"].reset_index(drop=True)
    lows = daily["low"].reset_index(drop=True)
    dates = daily["date"].reset_index(drop=True)
    local_highs, local_lows = [], []
    for i in range(window, len(daily) - window):
        window_high = highs.iloc[i - window:i + window + 1]
        window_low = lows.iloc[i - window:i + window + 1]
        if highs.iloc[i] == window_high.max():
            local_highs.append((dates.iloc[i], float(highs.iloc[i])))
        if lows.iloc[i] == window_low.min():
            local_lows.append((dates.iloc[i], float(lows.iloc[i])))
    return local_highs, local_lows


def cluster_levels(points: list, tolerance_pct: float = CLUSTER_TOLERANCE_PCT) -> list[dict]:
    """Groups nearby swing points into levels. `points` is a list of
    (date, price). Greedy clustering on sorted price - simple, disclosed,
    not a sophisticated model."""
    if not points:
        return []
    ordered = sorted(points, key=lambda p: p[1])
    clusters: list[list] = [[ordered[0]]]
    for point in ordered[1:]:
        anchor = clusters[-1][-1][1]
        if abs(point[1] - anchor) / anchor <= tolerance_pct:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    results = []
    for cluster in clusters:
        level = sum(p[1] for p in cluster) / len(cluster)
        touches = len(cluster)
        classification = (
            "SIGNIFICANT" if touches >= SIGNIFICANT_TOUCHES
            else "REAL" if touches == 2
            else "INTERESTING"
        )
        results.append({
            "level": level,
            "touches": touches,
            "classification": classification,
            "dates": [str(p[0]) for p in cluster],
        })
    return sorted(results, key=lambda r: r["level"])


def build_map(daily: pd.DataFrame, current_price: float, window: int = SWING_WINDOW) -> dict:
    """The full 'Map' for one symbol: clustered levels split into support
    (below current price) and resistance (above), nearest first."""
    local_highs, local_lows = find_local_extrema(daily, window)
    resistance_levels = [lvl for lvl in cluster_levels(local_highs) if lvl["level"] > current_price]
    support_levels = [lvl for lvl in cluster_levels(local_lows) if lvl["level"] < current_price]
    resistance_levels.sort(key=lambda r: r["level"])  # nearest first (ascending, just above price)
    support_levels.sort(key=lambda r: -r["level"])    # nearest first (descending, just below price)
    return {
        "current_price": current_price,
        "nearest_support": support_levels[0] if support_levels else None,
        "nearest_resistance": resistance_levels[0] if resistance_levels else None,
        "all_support": support_levels,
        "all_resistance": resistance_levels,
    }


def classify_index_regime(index_daily: pd.DataFrame,
                           short_window: int = REGIME_SHORT_SMA,
                           long_window: int = REGIME_LONG_SMA) -> dict:
    """UP / DOWN / CHOPPY off a simple SMA-stack rule - disclosed, not
    optimized. Requires at least `long_window` rows."""
    closes = index_daily["close"]
    if len(closes) < long_window:
        return {"regime": "UNKNOWN", "reason": "insufficient history"}
    last_close = float(closes.iloc[-1])
    sma_short = float(closes.tail(short_window).mean())
    sma_long = float(closes.tail(long_window).mean())
    if last_close > sma_short > sma_long:
        regime = "UP"
    elif last_close < sma_short < sma_long:
        regime = "DOWN"
    else:
        regime = "CHOPPY"
    return {
        "regime": regime,
        "last_close": last_close,
        "sma_short": sma_short,
        "sma_long": sma_long,
        "as_of_date": str(index_daily["date"].iloc[-1]),
    }


def consecutive_directional_days(daily: pd.DataFrame) -> dict:
    """How many most-recent consecutive daily closes moved the same
    direction - a simple, disclosed proxy for 'how extended is this move',
    not a volatility-normalized model."""
    closes = daily["close"].reset_index(drop=True)
    if len(closes) < 2:
        return {"streak": 0, "direction": None}
    direction = None
    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        delta = closes.iloc[i] - closes.iloc[i - 1]
        step_direction = "UP" if delta > 0 else ("DOWN" if delta < 0 else None)
        if direction is None:
            if step_direction is None:
                break
            direction = step_direction
            streak = 1
        elif step_direction == direction:
            streak += 1
        else:
            break
    return {"streak": streak, "direction": direction}


def check_ma_confluence(level_price: float, sma_short: float, sma_long: float,
                         tolerance_pct: float = CLUSTER_TOLERANCE_PCT) -> dict:
    """2026-08-25 addendum. Independent confluence check: does a moving
    average sit near this level? Complementary to touch-count, not
    derived from it - a level can be SIGNIFICANT by touches alone with
    no MA confluence, or only INTERESTING by touches but corroborated by
    MA confluence. Verified against real data before building: 2 of 10
    real nearest-level checks on 2026-08-25 hit on SMA_LONG, 0 on
    SMA_SHORT, and neither duplicated the touch-count classification -
    see MAP_CONTEXT_OBSERVER_NOTE_20260825.md if present."""
    near_short = abs(sma_short - level_price) / level_price <= tolerance_pct
    near_long = abs(sma_long - level_price) / level_price <= tolerance_pct
    return {
        "sma_short_confluence": near_short,
        "sma_long_confluence": near_long,
        "has_confluence": near_short or near_long,
    }


def compute_recent_swing_fib_levels(daily: pd.DataFrame, window: int = SWING_WINDOW) -> Optional[dict]:
    """2026-08-25 addendum. Fibonacci used ONLY as a location overlay for
    confluence-checking an already-found Map level - NOT as a directional
    score or an entry trigger in its own right (three separate video
    reviews this session used three different, mutually-inconsistent Fib
    zones for entry timing - 38.2-61.8%, 70.5-88.6%, 50-61.8% - none of
    which is treated as more correct here; this only checks whether a
    level ALSO happens to sit near the 50%/61.8% retracement of the most
    recent significant swing, as independent corroborating evidence, the
    same way check_ma_confluence() already works). Reuses
    find_local_extrema() unchanged. Returns None if there aren't enough
    swings to measure from."""
    local_highs, local_lows = find_local_extrema(daily, window)
    if not local_highs or not local_lows:
        return None
    recent_high = max(local_highs, key=lambda p: p[0])
    recent_low = max(local_lows, key=lambda p: p[0])
    swing_low, swing_high = sorted([recent_low[1], recent_high[1]])
    span = swing_high - swing_low
    return {
        "swing_low": swing_low,
        "swing_high": swing_high,
        "fib_50": swing_high - 0.5 * span,
        "fib_618": swing_high - 0.618 * span,
    }


def check_fib_confluence(level_price: float, fib_levels: Optional[dict],
                          tolerance_pct: float = CLUSTER_TOLERANCE_PCT) -> dict:
    """Mirrors check_ma_confluence()'s shape exactly - independent
    corroboration, not derived from touch count or MA confluence."""
    if fib_levels is None:
        return {"fib_50_confluence": False, "fib_618_confluence": False, "has_confluence": False}
    near_50 = abs(fib_levels["fib_50"] - level_price) / level_price <= tolerance_pct
    near_618 = abs(fib_levels["fib_618"] - level_price) / level_price <= tolerance_pct
    return {
        "fib_50_confluence": near_50,
        "fib_618_confluence": near_618,
        "has_confluence": near_50 or near_618,
    }


def detect_recent_breakout(daily: pd.DataFrame, levels: list[dict],
                            lookback_days: int = 10) -> list[dict]:
    """2026-08-25 addendum. Only SIGNIFICANT levels qualify - breaking a
    level nobody had tested isn't a breakout in any meaningful sense.
    Returns the subset of `levels` price has crossed (either direction)
    within the last `lookback_days` closes, each tagged with direction."""
    recent = daily.tail(lookback_days)
    if len(recent) < 2:
        return []
    first_close = float(recent["close"].iloc[0])
    last_close = float(recent["close"].iloc[-1])
    breakouts = []
    for lvl in levels:
        if lvl["classification"] != "SIGNIFICANT":
            continue
        price = lvl["level"]
        if first_close < price < last_close:
            breakouts.append({**lvl, "direction": "UP"})
        elif first_close > price > last_close:
            breakouts.append({**lvl, "direction": "DOWN"})
    return breakouts


# ---------------------------------------------------------------------------
# Reaction-at-a-level entry rule (2026-08-25, new standalone engine only -
# NOT used by chart_studies_indicators.py / run_chart_studies_live_monitor.py,
# which are explicitly untouched by this addendum)
# ---------------------------------------------------------------------------
# Disclosed, first-cut parameters - not swept/optimized, same convention as
# every other threshold in this project.
REACTION_TOLERANCE_PCT = 0.005   # 0.5% - how close a bar's low must come to the level to count as "tested"
MIN_LEVEL_TOUCHES = 2            # only REAL or SIGNIFICANT levels qualify - excludes 1-touch INTERESTING
ENGINE_HARD_STOP_PCT = 0.01      # 1% - caps risk even if the tested low is unusually far from entry


@dataclass
class ReactionState:
    """Per-symbol state for the reaction-at-a-level entry rule: waits for
    a qualifying support level to be TESTED (a bar's low reaches it),
    then for a later bar's CLOSE to reclaim back above it - the same
    'reclaim' language reviewed from the source material, reduced to two
    numbers (REACTION_TOLERANCE_PCT, close > level), not a discretionary
    read."""
    tested: bool = False
    tested_level: Optional[float] = None
    tested_low: Optional[float] = None  # becomes the structural stop reference on entry

    def update(self, bar_low: float, bar_close: float, support_level: Optional[dict]) -> Optional[str]:
        """Feed one new closed bar. `support_level` must already be
        filtered to REAL/SIGNIFICANT only by the caller (see
        MIN_LEVEL_TOUCHES) - this class does not re-check touch count.
        Returns 'ENTRY' the bar a reclaim confirms, else None."""
        if support_level is None:
            self.tested = False
            self.tested_level = None
            self.tested_low = None
            return None
        level_price = support_level["level"]
        if not self.tested:
            if bar_low <= level_price * (1 + REACTION_TOLERANCE_PCT):
                self.tested = True
                self.tested_level = level_price
                self.tested_low = bar_low
            return None
        # already tested - watching for the reclaim, or a fresh lower low while still testing
        if bar_close > self.tested_level:
            self.tested = False
            self.tested_level = None
            self.tested_low = None
            return "ENTRY"
        if bar_low < self.tested_low:
            self.tested_low = bar_low
        return None


def compute_stop_and_target(entry_price: float, tested_level: float,
                             resistance_level: Optional[dict]) -> dict:
    """Stop = whichever is TIGHTER (closer to entry) of the reclaimed
    LEVEL ITSELF and the ENGINE_HARD_STOP_PCT hard cap. Deliberately uses
    the level, not the deeper intrabar low reached while testing it - the
    source material is explicit that invalidation is "closes back below
    the level I just reclaimed", evaluated by CLOSE not by wick ("a wick
    below it is the market testing that level... a close below it is the
    market making a statement"). The live engine's own exit check
    (map_context_live_engine.py's poll()) compares the bar's CLOSE
    against this stop, matching that same close-vs-wick distinction.
    Target = the nearest qualifying resistance, or None (no target, exit
    relies on the stop / a compulsory close only)."""
    structural_stop = tested_level
    hard_cap_stop = entry_price * (1 - ENGINE_HARD_STOP_PCT)
    stop_price = max(structural_stop, hard_cap_stop)
    target_price = resistance_level["level"] if resistance_level else None
    return {"stop_price": stop_price, "target_price": target_price}


@dataclass
class SizingGuidance:
    tier: str          # FULL | HALF | PASS
    reasons: list


# Disclosed, first-cut sizing rule - not swept/optimized. A fighting index
# regime or a heavily extended move each independently downgrade sizing;
# both together is a PASS, not a stack of two HALFs.
EXTENDED_STREAK_THRESHOLD = 5  # matches the source video's own "5 days in a row" example


def sizing_guidance(index_regime: str, trade_direction: str, extension: dict) -> SizingGuidance:
    reasons = []
    fighting_regime = (
        (trade_direction == "LONG" and index_regime == "DOWN")
        or (trade_direction == "SHORT" and index_regime == "UP")
    )
    if fighting_regime:
        reasons.append(f"index regime ({index_regime}) opposes a {trade_direction.lower()} trade")
    extended = (
        extension["streak"] >= EXTENDED_STREAK_THRESHOLD
        and (
            (trade_direction == "LONG" and extension["direction"] == "UP")
            or (trade_direction == "SHORT" and extension["direction"] == "DOWN")
        )
    )
    if extended:
        reasons.append(f"{extension['streak']} consecutive {extension['direction']} days - chasing an extended move")
    if fighting_regime and extended:
        return SizingGuidance("PASS", reasons)
    if fighting_regime or extended:
        return SizingGuidance("HALF", reasons)
    return SizingGuidance("FULL", reasons)
