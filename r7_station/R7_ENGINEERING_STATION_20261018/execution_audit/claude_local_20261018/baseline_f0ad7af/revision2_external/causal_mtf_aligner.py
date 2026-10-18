"""Strict prior-only alignment of completed higher-timeframe bars.

One-minute timestamps in this dataset denote the start of a minute bar.  A
15-minute interval ``[09:45, 10:00)`` therefore becomes available at 10:00,
but is deliberately unavailable to a decision timestamped 10:00 itself.
Consumers receive it from 10:01 onwards through a strict ``available_at < t``
as-of join.
"""

from __future__ import annotations

import pandas as pd


class CausalMTFAligner:
    """Build and broadcast completed 5m/15m information without look-ahead."""

    _REQUIRED = ("open", "high", "low", "close", "volume")

    def __init__(self, timeframe: str = "15min") -> None:
        if timeframe not in {"5min", "15min"}:
            raise ValueError("timeframe must be '5min' or '15min'")
        self.timeframe = timeframe
        self.prefix = f"htf_{timeframe}_"

    def aggregate_completed_bars(self, minute_bars: pd.DataFrame) -> pd.DataFrame:
        """Aggregate left-closed HTF intervals and index them by completion time."""
        if not isinstance(minute_bars.index, pd.DatetimeIndex):
            raise ValueError("minute bars require a DatetimeIndex")
        missing = set(self._REQUIRED) - set(minute_bars.columns)
        if missing:
            raise ValueError(f"minute bars missing columns: {sorted(missing)}")
        if not minute_bars.index.is_monotonic_increasing or not minute_bars.index.is_unique:
            raise ValueError("minute bar index must be unique and sorted")
        return (
            minute_bars.loc[:, self._REQUIRED]
            .resample(self.timeframe, closed="left", label="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
        )

    def align_features(self, decision_bars: pd.DataFrame, completed_features: pd.DataFrame) -> pd.DataFrame:
        """Broadcast completed features using an explicit strict completion boundary."""
        if not isinstance(decision_bars.index, pd.DatetimeIndex):
            raise ValueError("decision bars require a DatetimeIndex")
        if not isinstance(completed_features.index, pd.DatetimeIndex):
            raise ValueError("completed features require a DatetimeIndex")
        if not decision_bars.index.is_monotonic_increasing or not completed_features.index.is_monotonic_increasing:
            raise ValueError("decision and completed indices must be sorted")

        left = pd.DataFrame({"_decision_at": decision_bars.index}, index=decision_bars.index)
        right = completed_features.copy()
        right[f"{self.prefix}available_at"] = right.index
        right = right.reset_index(drop=True).sort_values(f"{self.prefix}available_at")
        merged = pd.merge_asof(
            left.reset_index(drop=True).sort_values("_decision_at"), right,
            left_on="_decision_at", right_on=f"{self.prefix}available_at",
            direction="backward", allow_exact_matches=False,
        )
        merged.index = decision_bars.index
        return merged.drop(columns=["_decision_at"])

    def process(self, minute_bars: pd.DataFrame) -> pd.DataFrame:
        """Append causally aligned raw HTF OHLCV values for diagnostics."""
        aggregate = self.aggregate_completed_bars(minute_bars).add_prefix(self.prefix)
        return minute_bars.join(self.align_features(minute_bars, aggregate))
