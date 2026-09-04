"""Strict, deterministic OHLCV certification shared by every backend."""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd


REQUIRED = ("timestamp", "open", "high", "low", "close", "volume")


def certify_bars(frame: pd.DataFrame, timezone: str = "Asia/Kolkata") -> Tuple[pd.DataFrame, Dict[str, int]]:
    if frame is None or frame.empty:
        raise ValueError("market data is empty")
    columns = {str(c).lower(): c for c in frame.columns}
    missing = [name for name in REQUIRED if name not in columns]
    if missing:
        raise ValueError(f"missing required market-data columns: {', '.join(missing)}")

    bars = frame.rename(columns={columns[name]: name for name in REQUIRED}).copy()
    parsed = pd.to_datetime(bars["timestamp"], errors="coerce")
    if parsed.isna().any():
        raise ValueError("unparseable market-data timestamp")
    if parsed.dt.tz is None:
        parsed = parsed.dt.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
    else:
        parsed = parsed.dt.tz_convert(timezone)
    bars["timestamp"] = parsed

    numeric = list(REQUIRED[1:])
    for name in numeric:
        bars[name] = pd.to_numeric(bars[name], errors="coerce")
    if not np.isfinite(bars[numeric].to_numpy(dtype=float)).all():
        raise ValueError("non-finite OHLCV value")
    if (bars[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (bars["volume"] < 0).any():
        raise ValueError("volume cannot be negative")
    if (bars["high"] < bars[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("bar high is below open/low/close")
    if (bars["low"] > bars[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("bar low is above open/high/close")

    value_cols = [c for c in bars.columns if c != "timestamp"]
    duplicate_rows_removed = 0
    keep_indices = []
    for timestamp, group in bars.groupby("timestamp", sort=False, dropna=False):
        if len(group) > 1:
            first = group.iloc[0][value_cols]
            if not group[value_cols].eq(first).all(axis=None):
                raise ValueError(f"conflicting duplicate timestamp: {timestamp}")
            duplicate_rows_removed += len(group) - 1
        keep_indices.append(group.index[0])
    bars = bars.loc[keep_indices].sort_values("timestamp", kind="stable").reset_index(drop=True)
    if not bars["timestamp"].is_monotonic_increasing or bars["timestamp"].duplicated().any():
        raise ValueError("timestamps must be strictly increasing after certification")
    return bars, {"input_rows": len(frame), "output_rows": len(bars), "exact_duplicates_removed": duplicate_rows_removed}
