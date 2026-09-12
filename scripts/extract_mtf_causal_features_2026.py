#!/usr/bin/env python3
"""Causal MTF feature primitives; extraction is not yet a discovery run.

The module reads the 2026 reservation ledger but performs no manifest load,
feature-table write, label generation, or calibration. It ensures features are
calculated on completed higher-timeframe bars before their broadcast to
one-minute decision rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from revision2_external.causal_mtf_aligner import CausalMTFAligner


class MTFFeatureExtractor:
    """Calculate completed 5m/15m context, then join it strictly backwards."""

    def __init__(self, registry_path: str = "revision2_external/research_dataset_registry.json") -> None:
        self.registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        if self.registry.get("research_family") != "2026_mtf_causal_feature_discovery":
            raise ValueError("wrong MTF research registry")
        if self.registry.get("status") != "RESERVED_NOT_YET_RUN":
            raise ValueError("MTF registry is not available for a new extraction")
        if self.registry.get("higher_timeframe_contract", {}).get("partial_bar_features") != "forbidden":
            raise ValueError("registry does not forbid partial higher-timeframe bars")

    @staticmethod
    def _efficiency_ratio(frame: pd.DataFrame, lookback: int = 14) -> pd.Series:
        net = frame.close.diff(lookback).abs()
        path = frame.close.diff().abs().rolling(lookback, min_periods=lookback).sum()
        return (net / path.replace(0.0, np.nan)).clip(0.0, 1.0)

    @staticmethod
    def _vwap_distance_atr(frame: pd.DataFrame, lookback: int = 20) -> pd.Series:
        typical = (frame.high + frame.low + frame.close) / 3.0
        vwap = (typical * frame.volume).rolling(lookback, min_periods=lookback).sum() / frame.volume.rolling(lookback, min_periods=lookback).sum()
        tr = pd.concat([
            frame.high - frame.low,
            (frame.high - frame.close.shift()).abs(),
            (frame.low - frame.close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        return (frame.close - vwap) / atr.replace(0.0, np.nan)

    def _completed_features(self, minute_bars: pd.DataFrame, timeframe: str) -> tuple[CausalMTFAligner, pd.DataFrame]:
        aligner = CausalMTFAligner(timeframe)
        completed = aligner.aggregate_completed_bars(minute_bars)
        label = "5m" if timeframe == "5min" else "15m"
        result = pd.DataFrame(index=completed.index)
        result[f"{label}_trend"] = np.sign(completed.close.diff(4))
        result[f"{label}_efficiency"] = self._efficiency_ratio(completed)
        result[f"{label}_vwap_distance_atr"] = self._vwap_distance_atr(completed)
        result[f"{label}_realized_vol"] = np.log(completed.close / completed.close.shift()).rolling(20, min_periods=20).std()
        return aligner, result

    def extract_features(self, minute_bars: pd.DataFrame) -> pd.DataFrame:
        result = minute_bars.copy()
        for timeframe in ("5min", "15min"):
            aligner, completed_features = self._completed_features(minute_bars, timeframe)
            result = result.join(aligner.align_features(minute_bars, completed_features))
        return result
