"""Causal 15-minute cross-sectional relative-strength shadow features."""

from __future__ import annotations

from datetime import timedelta
from typing import Dict

import numpy as np
import pandas as pd


def _exact_bar_return(close: pd.Series, lookback_bars: int, interval: timedelta) -> pd.Series:
    """Return only when the prior observation is exactly N completed bars back."""
    close = close.sort_index()
    prior_close = close.shift(lookback_bars)
    prior_timestamp = pd.Series(close.index, index=close.index).shift(lookback_bars)
    expected_timestamp = pd.Series(close.index, index=close.index) - interval * int(lookback_bars)
    valid = prior_timestamp == expected_timestamp
    return np.log(close / prior_close).where(valid)


def calculate_cross_sectional_features(
    completed_by_symbol: Dict[str, pd.DataFrame], *, min_coverage: int = 45,
    lookback_bars: int = 4, interval: str = "15min",
) -> Dict[str, pd.DataFrame]:
    """Rank exact-clock completed-bar returns without stale-value substitution.

    A symbol missing an exact completed bar does not receive a return or rank.
    A timestamp with fewer than ``min_coverage`` valid symbols is excluded for
    the entire universe rather than being completed by a forward fill.
    """
    if min_coverage < 2 or min_coverage > len(completed_by_symbol):
        raise ValueError("min_coverage must be between 2 and universe size")
    interval_deltas = {"15min": timedelta(minutes=15)}
    if interval not in interval_deltas:
        raise ValueError("only the causal 15min completed-bar grid is supported")
    delta = interval_deltas[interval]
    returns = {}
    for symbol, frame in completed_by_symbol.items():
        if "close" not in frame or not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError(f"{symbol} must have completed-bar DatetimeIndex and close")
        returns[symbol] = _exact_bar_return(frame["close"], lookback_bars, delta)
    matrix = pd.DataFrame(returns).sort_index()
    coverage = matrix.notna().sum(axis=1)
    valid_row = coverage >= min_coverage
    usable = matrix.where(valid_row, np.nan)
    percentile = usable.rank(axis=1, pct=True, method="average")
    excess = usable.sub(usable.median(axis=1), axis=0)
    missing = len(completed_by_symbol) - coverage

    result: Dict[str, pd.DataFrame] = {}
    for symbol, frame in completed_by_symbol.items():
        output = pd.DataFrame(index=frame.index)
        output["15m_rs_percentile"] = percentile[symbol].reindex(frame.index)
        output["15m_rs_excess"] = excess[symbol].reindex(frame.index)
        output["15m_cross_section_count"] = coverage.reindex(frame.index)
        output["15m_missing_symbol_count"] = missing.reindex(frame.index)
        output["15m_cross_section_available"] = valid_row.reindex(frame.index).fillna(False)
        result[symbol] = output
    return result
