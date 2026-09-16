#!/usr/bin/env python3
"""Causal MTF feature primitives; not a data-extraction or calibration run.

Each feature is computed on fully completed 5m/15m bars, then joined onto
one-minute decision rows by a strict ``completed_at < decision_at`` boundary.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from revision2_external.causal_mtf_aligner import CausalMTFAligner


class MTFFeatureExtractor:
    """Build completed-bar MTF features before strictly causal broadcasting."""

    def __init__(self, registry_path: str = "revision2_external/research_dataset_registry.json") -> None:
        self.registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        if self.registry.get("research_family") != "2026_mtf_causal_feature_discovery":
            raise ValueError("wrong MTF research registry")
        # Preserve a future state-machine transition while keeping the
        # registry as the authority over when a full extraction can run.
        status = self.registry.get("status")
        if status != "RESERVED_NOT_YET_RUN":
            raise ValueError(
                "MTF registry is not extractable: "
                f"status={status!r}. Only a newly reserved, unread research block "
                "may enter the extractor."
            )
        if self.registry.get("higher_timeframe_contract", {}).get("partial_bar_features") != "forbidden":
            raise ValueError("registry does not forbid partial higher-timeframe bars")

    @staticmethod
    def _by_session(frame: pd.DataFrame, function) -> pd.Series:
        """Avoid DataFrameGroupBy.apply shape changes across pandas versions."""
        pieces = [function(part) for _, part in frame.groupby(frame.index.normalize(), sort=True)]
        return pd.concat(pieces).reindex(frame.index) if pieces else pd.Series(index=frame.index, dtype=float)

    @staticmethod
    def _session_vwap(frame: pd.DataFrame) -> pd.Series:
        """Cumulative typical-price VWAP, reset at each cash-session start."""
        typical = (frame.high + frame.low + frame.close) / 3.0
        sessions = frame.index.normalize()
        return (typical * frame.volume).groupby(sessions).cumsum() / frame.volume.groupby(sessions).cumsum().replace(0.0, np.nan)

    @classmethod
    def _efficiency_ratio(cls, frame: pd.DataFrame, lookback: int = 14) -> pd.Series:
        def calculate(close: pd.Series) -> pd.Series:
            return (close.diff(lookback).abs() / close.diff().abs().rolling(lookback, min_periods=lookback).sum().replace(0.0, np.nan)).clip(0.0, 1.0)
        return cls._by_session(frame, lambda part: calculate(part.close))

    @classmethod
    def _session_atr(cls, frame: pd.DataFrame, lookback: int = 14) -> pd.Series:
        def calculate(part: pd.DataFrame) -> pd.Series:
            tr = pd.concat([
                part.high - part.low,
                (part.high - part.close.shift()).abs(),
                (part.low - part.close.shift()).abs(),
            ], axis=1).max(axis=1)
            return tr.rolling(lookback, min_periods=lookback).mean()
        return cls._by_session(frame, calculate)

    @classmethod
    def _session_realized_vol(cls, frame: pd.DataFrame, lookback: int = 20) -> pd.Series:
        def calculate(close: pd.Series) -> pd.Series:
            return np.log(close / close.shift()).rolling(lookback, min_periods=lookback).std()
        return cls._by_session(frame, lambda part: calculate(part.close))

    @staticmethod
    def _opening_range_bounds(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Expose a fixed 09:15--10:15 range only after its final bar closes."""
        sessions = frame.index.normalize()
        opening_end = sessions + pd.to_timedelta(10 * 60 + 15, unit="min")
        in_range = frame.index <= opening_end
        high = frame.high.where(in_range).groupby(sessions).cummax()
        low = frame.low.where(in_range).groupby(sessions).cummin()
        complete = frame.index >= opening_end
        return high.where(complete), low.where(complete)

    @staticmethod
    def _orb_position_atr(frame: pd.DataFrame, orb_high: pd.Series, orb_low: pd.Series, atr: pd.Series) -> pd.Series:
        above = (frame.close - orb_high) / atr.replace(0.0, np.nan)
        below = (frame.close - orb_low) / atr.replace(0.0, np.nan)
        values = np.where(frame.close > orb_high, above, np.where(frame.close < orb_low, below, 0.0))
        return pd.Series(values, index=frame.index).where(orb_high.notna() & atr.notna())

    def _completed_features(self, minute_bars: pd.DataFrame, timeframe: str) -> tuple[CausalMTFAligner, pd.DataFrame]:
        aligner = CausalMTFAligner(timeframe)
        completed = aligner.aggregate_completed_bars(minute_bars)
        label = "5m" if timeframe == "5min" else "15m"
        vwap = self._session_vwap(completed)
        atr = self._session_atr(completed)
        atr_pct = atr / completed.close.replace(0.0, np.nan)
        realized_vol = self._session_realized_vol(completed)
        orb_high, orb_low = self._opening_range_bounds(completed)
        result = pd.DataFrame(index=completed.index)
        result[f"{label}_trend"] = np.sign(completed.close.diff(4))
        result[f"{label}_efficiency"] = self._efficiency_ratio(completed)
        result[f"{label}_session_vwap"] = vwap
        result[f"{label}_vwap_distance_atr"] = (completed.close - vwap) / atr.replace(0.0, np.nan)
        result[f"{label}_atr_pct"] = atr_pct
        result[f"{label}_realized_vol"] = realized_vol
        result[f"{label}_volatility_to_atr_ratio"] = realized_vol / atr_pct.replace(0.0, np.nan)
        result[f"{label}_opening_range_high"] = orb_high
        result[f"{label}_opening_range_low"] = orb_low
        result[f"{label}_opening_range_position_atr"] = self._orb_position_atr(completed, orb_high, orb_low, atr)
        return aligner, result

    def extract_features(self, minute_bars: pd.DataFrame) -> pd.DataFrame:
        result = minute_bars.copy()
        for timeframe in ("5min", "15min"):
            aligner, completed_features = self._completed_features(minute_bars, timeframe)
            result = result.join(aligner.align_features(minute_bars, completed_features))
        return result
