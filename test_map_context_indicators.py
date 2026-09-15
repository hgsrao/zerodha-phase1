"""Tests for map_context_indicators.py. All synthetic data - no real
Kite/network calls anywhere, matching this project's established test
convention."""
from __future__ import annotations

import pandas as pd
import pytest

from map_context_indicators import (
    build_map, check_fib_confluence, check_ma_confluence, classify_index_regime,
    cluster_levels, compute_recent_swing_fib_levels, compute_stop_and_target,
    consecutive_directional_days, detect_recent_breakout,
    find_local_extrema, sizing_guidance, ReactionState,
    ENGINE_HARD_STOP_PCT, EXTENDED_STREAK_THRESHOLD, REACTION_TOLERANCE_PCT,
)


def _daily(closes, start="2026-01-01"):
    dates = pd.date_range(start, periods=len(closes), freq="D")
    rows = [{"date": d, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000}
            for d, c in zip(dates, closes)]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# find_local_extrema / cluster_levels
# ---------------------------------------------------------------------------
def test_find_local_extrema_identifies_a_clean_swing_high():
    # symmetric peak at index 5
    closes = [100, 101, 102, 103, 104, 110, 104, 103, 102, 101, 100]
    df = _daily(closes)
    highs, lows = find_local_extrema(df, window=3)
    assert (df["date"].iloc[5], 111.0) in highs  # high = close+1 = 111


def test_cluster_levels_groups_nearby_prices_together():
    points = [("d1", 100.0), ("d2", 100.3), ("d3", 100.5), ("d4", 150.0)]
    clusters = cluster_levels(points, tolerance_pct=0.01)
    assert len(clusters) == 2
    assert clusters[0]["touches"] == 3
    assert clusters[1]["touches"] == 1


def test_cluster_levels_classification_thresholds():
    one_touch = cluster_levels([("d1", 100.0)])
    two_touch = cluster_levels([("d1", 100.0), ("d2", 100.1)], tolerance_pct=0.01)
    three_touch = cluster_levels([("d1", 100.0), ("d2", 100.1), ("d3", 99.9)], tolerance_pct=0.01)
    assert one_touch[0]["classification"] == "INTERESTING"
    assert two_touch[0]["classification"] == "REAL"
    assert three_touch[0]["classification"] == "SIGNIFICANT"


def test_build_map_splits_support_and_resistance_around_current_price():
    daily = _daily([100, 105, 95, 108, 92, 110, 90, 112, 88, 115, 85])
    result = build_map(daily, current_price=100.0, window=2)
    assert result["current_price"] == 100.0
    if result["nearest_resistance"]:
        assert result["nearest_resistance"]["level"] > 100.0
    if result["nearest_support"]:
        assert result["nearest_support"]["level"] < 100.0


# ---------------------------------------------------------------------------
# classify_index_regime
# ---------------------------------------------------------------------------
def test_regime_up_when_price_above_both_smas_in_order():
    closes = list(range(100, 160))  # steadily rising -> close > sma20 > sma50
    df = _daily(closes)
    result = classify_index_regime(df, short_window=20, long_window=50)
    assert result["regime"] == "UP"


def test_regime_down_when_price_below_both_smas_in_order():
    closes = list(range(160, 100, -1))  # steadily falling
    df = _daily(closes)
    result = classify_index_regime(df, short_window=20, long_window=50)
    assert result["regime"] == "DOWN"


def test_regime_unknown_with_insufficient_history():
    df = _daily([100, 101, 102])
    result = classify_index_regime(df, short_window=20, long_window=50)
    assert result["regime"] == "UNKNOWN"


def test_regime_choppy_on_a_flat_series():
    closes = [100.0] * 60
    df = _daily(closes)
    result = classify_index_regime(df, short_window=20, long_window=50)
    assert result["regime"] == "CHOPPY"  # last_close == sma_short == sma_long, neither strict inequality holds


# ---------------------------------------------------------------------------
# consecutive_directional_days
# ---------------------------------------------------------------------------
def test_consecutive_up_days_counts_the_streak():
    closes = [100, 101, 99, 100, 101, 102, 103]  # 99->100->101->102->103 = 4 consecutive up-steps
    df = _daily(closes)
    result = consecutive_directional_days(df)
    assert result["direction"] == "UP"
    assert result["streak"] == 4


def test_consecutive_days_breaks_on_direction_change():
    closes = [100, 105, 104]  # last step is DOWN, breaks immediately
    df = _daily(closes)
    result = consecutive_directional_days(df)
    assert result["direction"] == "DOWN"
    assert result["streak"] == 1


# ---------------------------------------------------------------------------
# check_ma_confluence (2026-08-25 addendum)
# ---------------------------------------------------------------------------
def test_confluence_true_when_short_sma_is_near_the_level():
    result = check_ma_confluence(level_price=100.0, sma_short=100.3, sma_long=150.0, tolerance_pct=0.01)
    assert result["sma_short_confluence"] is True
    assert result["sma_long_confluence"] is False
    assert result["has_confluence"] is True


def test_confluence_true_when_long_sma_is_near_the_level():
    result = check_ma_confluence(level_price=100.0, sma_short=150.0, sma_long=99.8, tolerance_pct=0.01)
    assert result["sma_long_confluence"] is True
    assert result["has_confluence"] is True


def test_confluence_false_when_neither_ma_is_close():
    result = check_ma_confluence(level_price=100.0, sma_short=150.0, sma_long=200.0, tolerance_pct=0.01)
    assert result["has_confluence"] is False


def test_confluence_does_not_duplicate_touch_count_classification():
    """The whole point: an INTERESTING (1-touch) level can still have
    confluence, and a SIGNIFICANT level can still have none - the two
    signals are independent, not derived from each other."""
    interesting_with_confluence = check_ma_confluence(100.0, 100.1, 200.0, tolerance_pct=0.01)
    significant_without_confluence = check_ma_confluence(100.0, 150.0, 200.0, tolerance_pct=0.01)
    assert interesting_with_confluence["has_confluence"] is True
    assert significant_without_confluence["has_confluence"] is False


# ---------------------------------------------------------------------------
# compute_recent_swing_fib_levels / check_fib_confluence (2026-08-25 addendum)
# ---------------------------------------------------------------------------
def test_swing_fib_levels_none_without_enough_swings():
    df = _daily([100.0] * 4)  # too short to have any local extrema at window=3
    assert compute_recent_swing_fib_levels(df, window=3) is None


def test_swing_fib_levels_computed_from_the_most_recent_swing():
    # a clean up-swing then down-swing: local low then local high near the end
    closes = [100, 99, 98, 97, 96, 100, 104, 108, 112, 108, 104]
    df = _daily(closes)
    result = compute_recent_swing_fib_levels(df, window=2)
    assert result is not None
    assert result["swing_low"] < result["swing_high"]
    assert result["swing_low"] < result["fib_618"] < result["fib_50"] < result["swing_high"]


def test_fib_confluence_false_when_no_swing_data():
    result = check_fib_confluence(100.0, None)
    assert result["has_confluence"] is False


def test_fib_confluence_true_when_level_matches_50_retracement():
    fib_levels = {"swing_low": 100.0, "swing_high": 200.0, "fib_50": 150.0, "fib_618": 138.2}
    result = check_fib_confluence(150.2, fib_levels, tolerance_pct=0.01)
    assert result["fib_50_confluence"] is True
    assert result["fib_618_confluence"] is False
    assert result["has_confluence"] is True


def test_fib_confluence_true_when_level_matches_618_retracement():
    fib_levels = {"swing_low": 100.0, "swing_high": 200.0, "fib_50": 150.0, "fib_618": 138.2}
    result = check_fib_confluence(138.0, fib_levels, tolerance_pct=0.01)
    assert result["fib_618_confluence"] is True
    assert result["has_confluence"] is True


def test_fib_confluence_false_when_level_matches_neither():
    fib_levels = {"swing_low": 100.0, "swing_high": 200.0, "fib_50": 150.0, "fib_618": 138.2}
    result = check_fib_confluence(190.0, fib_levels, tolerance_pct=0.01)
    assert result["has_confluence"] is False


# ---------------------------------------------------------------------------
# detect_recent_breakout (2026-08-25 addendum)
# ---------------------------------------------------------------------------
def test_breakout_detects_an_upward_cross_of_a_significant_level():
    daily = _daily([95, 96, 97, 98, 99, 101, 102, 103, 104, 105])
    levels = [{"level": 100.0, "touches": 3, "classification": "SIGNIFICANT", "dates": []}]
    result = detect_recent_breakout(daily, levels, lookback_days=10)
    assert len(result) == 1
    assert result[0]["direction"] == "UP"


def test_breakout_detects_a_downward_cross_of_a_significant_level():
    daily = _daily([105, 104, 103, 102, 101, 99, 98, 97, 96, 95])
    levels = [{"level": 100.0, "touches": 3, "classification": "SIGNIFICANT", "dates": []}]
    result = detect_recent_breakout(daily, levels, lookback_days=10)
    assert len(result) == 1
    assert result[0]["direction"] == "DOWN"


def test_breakout_ignores_non_significant_levels():
    daily = _daily([95, 96, 97, 98, 99, 101, 102, 103, 104, 105])
    levels = [{"level": 100.0, "touches": 1, "classification": "INTERESTING", "dates": []}]
    result = detect_recent_breakout(daily, levels, lookback_days=10)
    assert result == []


def test_breakout_empty_when_price_never_crosses():
    daily = _daily([101, 102, 103, 104, 105, 106, 107, 108, 109, 110])
    levels = [{"level": 100.0, "touches": 3, "classification": "SIGNIFICANT", "dates": []}]
    result = detect_recent_breakout(daily, levels, lookback_days=10)
    assert result == []


# ---------------------------------------------------------------------------
# ReactionState (2026-08-25 addendum, the new engine's own entry rule)
# ---------------------------------------------------------------------------
def _support(level=100.0, touches=2):
    return {"level": level, "touches": touches, "classification": "REAL", "dates": []}


def test_reaction_no_entry_when_level_never_tested():
    st = ReactionState()
    assert st.update(bar_low=105.0, bar_close=106.0, support_level=_support()) is None
    assert st.tested is False


def test_reaction_marks_tested_when_low_reaches_the_level():
    st = ReactionState()
    st.update(bar_low=100.0, bar_close=100.5, support_level=_support())
    assert st.tested is True
    assert st.tested_low == 100.0


def test_reaction_fires_entry_on_a_reclaim_close():
    st = ReactionState()
    st.update(bar_low=99.5, bar_close=99.8, support_level=_support())  # tests the level
    assert st.tested is True
    result = st.update(bar_low=100.0, bar_close=100.5, support_level=_support())  # closes back above
    assert result == "ENTRY"
    assert st.tested is False  # reset after firing


def test_reaction_does_not_fire_while_still_below_the_level():
    st = ReactionState()
    st.update(bar_low=99.5, bar_close=99.8, support_level=_support())
    result = st.update(bar_low=99.0, bar_close=99.6, support_level=_support())  # still below 100
    assert result is None
    assert st.tested is True


def test_reaction_tracks_a_fresh_lower_low_while_still_testing():
    st = ReactionState()
    st.update(bar_low=99.5, bar_close=99.8, support_level=_support())
    st.update(bar_low=98.0, bar_close=99.0, support_level=_support())  # deeper low, still no reclaim
    assert st.tested_low == 98.0  # the stop reference tightened to the new low


def test_reaction_resets_when_the_level_disappears():
    st = ReactionState()
    st.update(bar_low=99.5, bar_close=99.8, support_level=_support())
    assert st.tested is True
    st.update(bar_low=105.0, bar_close=106.0, support_level=None)
    assert st.tested is False


def test_reaction_tolerance_boundary():
    level = _support(level=100.0)
    st = ReactionState()
    just_within = 100.0 * (1 + REACTION_TOLERANCE_PCT)
    st.update(bar_low=just_within, bar_close=just_within + 1, support_level=level)
    assert st.tested is True  # at the boundary still counts as tested


# ---------------------------------------------------------------------------
# compute_stop_and_target (2026-08-25 addendum)
# ---------------------------------------------------------------------------
def test_stop_uses_the_reclaimed_level_when_it_is_the_tighter_option():
    result = compute_stop_and_target(entry_price=100.0, tested_level=99.5, resistance_level=None)
    hard_cap = 100.0 * (1 - ENGINE_HARD_STOP_PCT)
    assert result["stop_price"] == pytest.approx(max(99.5, hard_cap))
    assert result["stop_price"] == pytest.approx(99.5)  # 0.5% below entry is tighter than the 1% cap


def test_stop_uses_hard_cap_when_the_reclaimed_level_is_unusually_far():
    result = compute_stop_and_target(entry_price=100.0, tested_level=90.0, resistance_level=None)
    expected = 100.0 * (1 - ENGINE_HARD_STOP_PCT)  # the 1% cap is tighter than a 10%-away level
    assert result["stop_price"] == pytest.approx(expected)


def test_target_is_none_without_a_resistance_level():
    result = compute_stop_and_target(entry_price=100.0, tested_level=99.0, resistance_level=None)
    assert result["target_price"] is None


def test_target_matches_the_resistance_level_price():
    resistance = {"level": 110.0, "touches": 3, "classification": "SIGNIFICANT", "dates": []}
    result = compute_stop_and_target(entry_price=100.0, tested_level=99.0, resistance_level=resistance)
    assert result["target_price"] == pytest.approx(110.0)


# ---------------------------------------------------------------------------
# sizing_guidance
# ---------------------------------------------------------------------------
def test_sizing_full_when_nothing_flags():
    result = sizing_guidance("UP", "LONG", {"streak": 1, "direction": "UP"})
    assert result.tier == "FULL"
    assert result.reasons == []


def test_sizing_half_when_only_regime_fights():
    result = sizing_guidance("DOWN", "LONG", {"streak": 1, "direction": "UP"})
    assert result.tier == "HALF"
    assert len(result.reasons) == 1


def test_sizing_half_when_only_extended():
    result = sizing_guidance("UP", "LONG", {"streak": EXTENDED_STREAK_THRESHOLD, "direction": "UP"})
    assert result.tier == "HALF"
    assert len(result.reasons) == 1


def test_sizing_pass_when_both_regime_fights_and_extended():
    result = sizing_guidance("DOWN", "LONG", {"streak": EXTENDED_STREAK_THRESHOLD, "direction": "UP"})
    assert result.tier == "PASS"
    assert len(result.reasons) == 2


def test_sizing_extension_in_the_helping_direction_does_not_flag():
    # extended DOWNward while going LONG doesn't count as "extended against the trade"
    result = sizing_guidance("UP", "LONG", {"streak": EXTENDED_STREAK_THRESHOLD, "direction": "DOWN"})
    assert result.tier == "FULL"
