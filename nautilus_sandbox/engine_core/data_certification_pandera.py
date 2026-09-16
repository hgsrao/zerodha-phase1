"""Box 3 (L2DataCertifier) -- Pandera-based OHLCV bar certification.

The box's real job (verified against revision2/boxes.py's
L2DataCertifierBox and the ten-box audit) is validating plain 1-minute
OHLCV bars, not a Level-2 order book -- there is no L2 feed anywhere in
this pipeline. This module formalizes that same certification (columns
present, no NaNs, positive prices, high >= max(open,low,close), low <=
min(open,high,close), strictly increasing/unique timestamps after exact-
duplicate removal, conflicting duplicates fail closed) as a real Pandera
schema instead of hand-written pandas boolean checks, matching
revision2/data_certification.py's checks one-for-one but through a
library built specifically for this job.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors

REQUIRED = ("timestamp", "open", "high", "low", "close", "volume")


def _ohlc_high_is_max(df: pd.DataFrame) -> pd.Series:
    return df["high"] >= df[["open", "low", "close"]].max(axis=1)


def _ohlc_low_is_min(df: pd.DataFrame) -> pd.Series:
    return df["low"] <= df[["open", "high", "close"]].min(axis=1)


BAR_SCHEMA = pa.DataFrameSchema(
    columns={
        "open": pa.Column(float, checks=pa.Check.gt(0), nullable=False),
        "high": pa.Column(float, checks=pa.Check.gt(0), nullable=False),
        "low": pa.Column(float, checks=pa.Check.gt(0), nullable=False),
        "close": pa.Column(float, checks=pa.Check.gt(0), nullable=False),
        "volume": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
    },
    checks=[
        pa.Check(_ohlc_high_is_max, element_wise=False, error="bar high is below open/low/close"),
        pa.Check(_ohlc_low_is_min, element_wise=False, error="bar low is above open/high/close"),
    ],
    coerce=True,
    strict=False,
)


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
    parsed = parsed.dt.tz_localize(timezone) if parsed.dt.tz is None else parsed.dt.tz_convert(timezone)
    bars["timestamp"] = parsed

    # Duplicate handling first (same policy as revision2/data_certification.py):
    # exact duplicates collapse silently, conflicting ones fail closed. Doing
    # this before the Pandera pass keeps the schema itself about VALUE shape,
    # not row-identity bookkeeping Pandera isn't built to express well.
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

    try:
        validated = BAR_SCHEMA.validate(bars[list(REQUIRED[1:])], lazy=True)
    except (SchemaError, SchemaErrors) as exc:
        raise ValueError(f"OHLCV schema violation: {exc}") from exc
    if not np.isfinite(validated.to_numpy(dtype=float)).all():
        raise ValueError("non-finite OHLCV value")

    bars[list(REQUIRED[1:])] = validated
    if not bars["timestamp"].is_monotonic_increasing or bars["timestamp"].duplicated().any():
        raise ValueError("timestamps must be strictly increasing after certification")

    return bars, {"input_rows": len(frame), "output_rows": len(bars), "exact_duplicates_removed": duplicate_rows_removed}
