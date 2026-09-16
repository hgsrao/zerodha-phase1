"""Deterministic admission checks for pre-run PA calibration windows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class WarmupAdmission:
    """Audit-friendly outcome for one symbol's frozen PA warmup window."""

    symbol: str
    admitted: bool
    bar_count: int
    return_std: float | None
    atr_mean: float | None
    volume_change_std: float | None
    reasons: tuple[str, ...]

    def report(self) -> Mapping[str, object]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


def assess_warmup(symbol: str, bars: pd.DataFrame, *, minimum_bars: int = 60,
                  epsilon: float = 1e-8) -> WarmupAdmission:
    """Reject a symbol-session whose calibration data cannot define PA scales.

    The validator still verifies the source file and records the rejection,
    but it must not hand a degenerate warmup to PA and silently substitute
    fallback scales.  This is a data-admission decision, not a tunable
    strategy or safety parameter.
    """
    required = ("open", "high", "low", "close", "volume")
    missing = tuple(name for name in required if name not in bars.columns)
    if missing:
        return WarmupAdmission(symbol, False, len(bars), None, None, None,
                               ("missing_columns:" + ",".join(missing),))
    if len(bars) < minimum_bars:
        return WarmupAdmission(symbol, False, len(bars), None, None, None,
                               ("insufficient_warmup_bars",))

    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    volume = pd.to_numeric(bars["volume"], errors="coerce")
    if any(series.isna().any() or not np.isfinite(series.to_numpy(dtype=float)).all()
           for series in (close, high, low, volume)):
        return WarmupAdmission(symbol, False, len(bars), None, None, None,
                               ("non_finite_ohlcv",))

    returns = close.pct_change().dropna()
    return_std = float(returns.std()) if len(returns) else 0.0
    previous_close = close.shift(1)
    true_range = pd.concat((
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ), axis=1).max(axis=1).iloc[1:]
    atr_mean = float(true_range.mean()) if len(true_range) else 0.0
    volume_change = volume.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    volume_change_std = float(volume_change.std()) if len(volume_change) else 0.0

    reasons = []
    if not np.isfinite(return_std) or return_std <= epsilon:
        reasons.append("degenerate_return_scale")
    if not np.isfinite(atr_mean) or atr_mean <= epsilon:
        reasons.append("degenerate_atr_scale")
    if not np.isfinite(volume_change_std) or volume_change_std <= epsilon:
        reasons.append("degenerate_volume_scale")
    return WarmupAdmission(symbol, not reasons, len(bars), return_std, atr_mean,
                           volume_change_std, tuple(reasons))
