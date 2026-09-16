import pandas as pd

from revision2_external.cross_sectional_relative_strength import calculate_cross_sectional_features


def _frame(values, times):
    return pd.DataFrame({"close": values}, index=pd.DatetimeIndex(times))


def test_ranks_only_exact_clock_returns_and_never_forward_fills_missing_symbol():
    times = ["2026-01-02 09:30", "2026-01-02 09:45", "2026-01-02 10:00", "2026-01-02 10:15", "2026-01-02 10:30"]
    universe = {
        "A": _frame([100, 101, 102, 103, 104], times),
        "B": _frame([100, 102, 104, 106, 108], times),
        "C": _frame([100, 99, 98, 97, 96], times),
        # D has no 10:00 completion; its 10:30 return must not silently
        # bridge that gap and must not join a stale cross-sectional rank.
        "D": _frame([100, 100, 100, 100], [times[0], times[1], times[3], times[4]]),
    }
    out = calculate_cross_sectional_features(universe, min_coverage=3, lookback_bars=4)
    t = pd.Timestamp("2026-01-02 10:30")
    assert out["B"].loc[t, "15m_rs_percentile"] == 1.0
    assert pd.isna(out["D"].loc[t, "15m_rs_percentile"])
    assert out["A"].loc[t, "15m_cross_section_count"] == 3


def test_insufficient_coverage_excludes_entire_cross_sectional_row():
    times = ["2026-01-02 09:30", "2026-01-02 09:45", "2026-01-02 10:00", "2026-01-02 10:15", "2026-01-02 10:30"]
    universe = {
        "A": _frame([100, 101, 102, 103, 104], times),
        "B": _frame([100, 101, 102, 103, 104], times),
        "C": _frame([100, 101, 102, 103], times[:-1]),
    }
    out = calculate_cross_sectional_features(universe, min_coverage=3, lookback_bars=4)
    t = pd.Timestamp("2026-01-02 10:30")
    assert out["A"].loc[t, "15m_cross_section_available"] == False
    assert pd.isna(out["A"].loc[t, "15m_rs_percentile"])
