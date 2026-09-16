"""Causal, timestamp-aligned Nifty/VIX context for the external engine.

This module deliberately starts as an *observation-only* adapter.  It replaces
the older grid experiments' positional-bar alignment and synthetic Nifty/VIX
fallbacks with an explicit as-of data contract.  A missing, stale, or
misaligned context produces an unavailable observation; it never fabricates a
neutral VIX or silently approves an entry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from revision3.macro_grid_synchronizer import MacroGridSynchronizer, SyncResult


@dataclass(frozen=True)
class GridShadowObservation:
    symbol: str
    decision_timestamp: str
    direction: int
    available: bool
    reason: str
    source_timestamp: Optional[str] = None
    source_age_seconds: Optional[float] = None
    nifty_close: Optional[float] = None
    vix_close: Optional[float] = None
    nifty_ema_50: Optional[float] = None
    macro_nifty_trend: Optional[int] = None
    macro_vix_level: Optional[float] = None
    macro_vix_slope: Optional[float] = None
    synchronized: Optional[bool] = None
    phase_delta_degrees: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


class SealedGridContextProvider:
    """Supply a causally available Nifty/VIX regime observation.

    The Nifty and VIX feeds are expected to be 15-minute bars.  At a stock
    decision timestamp, the provider considers only context bars strictly
    earlier than that timestamp.  This conservative convention is safe even
    when a source does not document whether its timestamp marks bar open or
    bar close.
    """

    def __init__(
        self,
        nifty_bars: pd.DataFrame,
        vix_bars: pd.DataFrame,
        *,
        max_staleness_seconds: float = 20.0 * 60.0,
        minimum_aligned_bars: int = 63,
        synchronizer: Optional[MacroGridSynchronizer] = None,
    ) -> None:
        self.nifty = self._normalize(nifty_bars, "Nifty")
        self.vix = self._normalize(vix_bars, "VIX")
        self.max_staleness_seconds = float(max_staleness_seconds)
        self.minimum_aligned_bars = int(minimum_aligned_bars)
        self.synchronizer = synchronizer or MacroGridSynchronizer()

    @staticmethod
    def _normalize(frame: pd.DataFrame, label: str) -> pd.DataFrame:
        timestamp_column = "timestamp" if "timestamp" in frame.columns else "date" if "date" in frame.columns else None
        if timestamp_column is None or "close" not in frame.columns:
            raise ValueError(f"{label} bars require timestamp/date and close columns")
        result = frame[[timestamp_column, "close"]].copy()
        result.columns = ["timestamp", "close"]
        result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True, errors="raise")
        result["close"] = pd.to_numeric(result["close"], errors="raise")
        if result["timestamp"].duplicated().any() or not result["timestamp"].is_monotonic_increasing:
            raise ValueError(f"{label} timestamps must be unique and chronological")
        if result["close"].isna().any() or (result["close"] <= 0).any():
            raise ValueError(f"{label} close values must be positive")
        return result

    @staticmethod
    def _unavailable(symbol: str, decision: pd.Timestamp, direction: int, reason: str) -> GridShadowObservation:
        return GridShadowObservation(symbol, decision.isoformat(), direction, False, reason)

    def observe(
        self,
        symbol: str,
        symbol_bars: pd.DataFrame,
        decision_timestamp: object,
        direction: int,
    ) -> GridShadowObservation:
        """Produce a passive context observation for one candidate.

        ``symbol_bars`` must contain only bars known at the decision time. The
        caller is responsible for supplying a causal prefix, which the external
        orchestrator does by slicing through its current bar index.
        """
        decision = pd.Timestamp(decision_timestamp)
        if decision.tzinfo is None:
            raise ValueError("decision timestamp must include a timezone")
        decision = decision.tz_convert("UTC")

        nifty_prior = self.nifty[self.nifty["timestamp"] < decision]
        vix_prior = self.vix[self.vix["timestamp"] < decision]
        if nifty_prior.empty or vix_prior.empty:
            return self._unavailable(symbol, decision, direction, "GRID_CONTEXT_NOT_YET_AVAILABLE")

        nifty_last = nifty_prior.iloc[-1]
        vix_last = vix_prior.iloc[-1]
        if nifty_last.timestamp != vix_last.timestamp:
            return self._unavailable(symbol, decision, direction, "GRID_CONTEXT_TIMESTAMP_MISMATCH")
        source_timestamp = nifty_last.timestamp
        age = decision - source_timestamp
        if age.total_seconds() > self.max_staleness_seconds:
            return self._unavailable(symbol, decision, direction, "GRID_CONTEXT_STALE")

        context = nifty_prior.merge(vix_prior, on="timestamp", how="inner", suffixes=("_nifty", "_vix"))
        if len(context) < self.minimum_aligned_bars:
            return self._unavailable(symbol, decision, direction, "GRID_CONTEXT_WARMUP_INSUFFICIENT")

        # These labels are computed only after the source timestamp has been
        # verified as a completed, fresh Nifty/VIX bar.  ``*_prior`` contains
        # strictly pre-decision data, so neither indicator can use a bar that
        # was not available when the candidate was evaluated.
        if len(nifty_prior) < 50 or len(vix_prior) < 5:
            return self._unavailable(symbol, decision, direction, "GRID_CONTEXT_FEATURE_WARMUP_INSUFFICIENT")
        nifty_ema_50 = float(nifty_prior["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        nifty_close = float(nifty_prior["close"].iloc[-1])
        macro_nifty_trend = 1 if nifty_close > nifty_ema_50 else -1
        vix_close = float(vix_prior["close"].iloc[-1])
        vix_prior_close = float(vix_prior["close"].iloc[-5])
        macro_vix_slope = (vix_close - vix_prior_close) / vix_prior_close

        timestamp_column = "timestamp" if "timestamp" in symbol_bars.columns else "date" if "date" in symbol_bars.columns else None
        if timestamp_column is None or "close" not in symbol_bars.columns:
            raise ValueError("symbol bars require timestamp/date and close columns")
        plant = symbol_bars[[timestamp_column, "close"]].copy()
        plant.columns = ["timestamp", "plant_close"]
        plant["timestamp"] = pd.to_datetime(plant["timestamp"], utc=True, errors="raise")
        plant["plant_close"] = pd.to_numeric(plant["plant_close"], errors="raise")
        plant = plant[(plant["timestamp"] <= source_timestamp) & plant["plant_close"].notna()].sort_values("timestamp")
        if plant.empty:
            return self._unavailable(symbol, decision, direction, "SYMBOL_CONTEXT_NOT_AVAILABLE")

        aligned = pd.merge_asof(
            context.sort_values("timestamp"), plant, on="timestamp", direction="backward"
        ).dropna(subset=["plant_close"])
        if len(aligned) < self.minimum_aligned_bars:
            return self._unavailable(symbol, decision, direction, "SYMBOL_GRID_ALIGNMENT_INSUFFICIENT")

        # A real symbol series and the real Nifty series are supplied here;
        # this fixes the old prototype which supplied Nifty for both inputs.
        result: SyncResult = self.synchronizer.check_synchronization(
            plant_close=aligned["plant_close"].to_numpy(dtype=float),
            grid_close=aligned["close_nifty"].to_numpy(dtype=float),
            current_vix=float(aligned["close_vix"].iloc[-1]),
            trade_direction=int(direction),
        )
        return GridShadowObservation(
            symbol=symbol,
            decision_timestamp=decision.isoformat(),
            direction=int(direction),
            available=True,
            reason=result.reason,
            source_timestamp=source_timestamp.isoformat(),
            source_age_seconds=float(age.total_seconds()),
            nifty_close=nifty_close,
            vix_close=vix_close,
            nifty_ema_50=nifty_ema_50,
            macro_nifty_trend=macro_nifty_trend,
            macro_vix_level=vix_close,
            macro_vix_slope=macro_vix_slope,
            synchronized=bool(result.is_synchronized),
            phase_delta_degrees=float(result.delta_phi),
        )
