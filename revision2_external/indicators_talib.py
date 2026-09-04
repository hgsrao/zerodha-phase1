"""Box 4 (Predictive Analytics) -- TA-Lib-based indicator computation.

Keeps revision2.boxes.PredictiveAnalyticsBox's own signal-combination logic
(component weights, regime multipliers, persistence bonus, quality bands,
entry/exit smoothing) exactly as designed -- that logic IS the proprietary
signal, not something TA-Lib provides or should replace. What TA-Lib
replaces is the raw indicator math underneath it:
  - momentum: talib.ROC (Rate of Change, ((close/prevClose)-1)*100) in place
    of the hand-rolled (close[-1]-close[0])/close[0] -- the same formula,
    scaled by 100, computed by a C-optimized, widely-used implementation.
  - volatility: talib.ATR (Average True Range) in place of a hand-rolled
    True Range mean. TA-Lib's ATR uses Wilder's smoothing (an exponential
    moving average of True Range), NOT a simple rolling mean -- this is a
    real, documented difference in the resulting number, not a bug. Wilder
    smoothing is the standard, textbook ATR definition; the original box's
    simple-mean version was itself the approximation.

VWAP deviation and volume confirmation have no TA-Lib equivalent (VWAP is
not part of classic TA-Lib) and stay as direct calculations -- there's no
library function to defer to here, and pretending otherwise would be
exactly the kind of dishonest box-ticking this project has repeatedly
rejected.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import talib

from revision2.contracts import EffectiveConfig, MarketSnapshot, ParameterUse, PASignal


def _np_clip(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


class TALibPredictiveAnalyticsBox:
    def __init__(self) -> None:
        self._history: Dict[str, deque] = {}
        self._scale: Dict[str, Dict[str, float]] = {}

    def calibrate(self, symbol: str, warmup_bars: pd.DataFrame) -> None:
        close = warmup_bars["close"].to_numpy(dtype=float)
        high = warmup_bars["high"].to_numpy(dtype=float)
        low = warmup_bars["low"].to_numpy(dtype=float)
        volume = warmup_bars["volume"].to_numpy(dtype=float)

        returns = pd.Series(close).pct_change().dropna()
        dp_scale = float(returns.std()) or 1e-6
        vol_pct_change = pd.Series(volume).pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        dv_scale = float(vol_pct_change.std()) or 1e-6

        atr_period = min(14, max(2, len(close) - 1))
        atr_series = talib.ATR(high, low, close, timeperiod=atr_period)
        baseline_atr = float(np.nanmean(atr_series)) if np.isfinite(atr_series).any() else 1e-6
        baseline_vol = baseline_atr / close[-1] if close[-1] else 1e-6

        self._scale[symbol] = {"dp_scale": dp_scale, "dv_scale": dv_scale, "baseline_vol": max(baseline_vol, 1e-6)}

    def _scale_for(self, symbol: str) -> Dict[str, float]:
        return self._scale.get(symbol, {"dp_scale": 1e-3, "dv_scale": 1.0, "baseline_vol": 1e-3})

    def evaluate(self, snapshot: MarketSnapshot, config: EffectiveConfig) -> Tuple[PASignal, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "PA", value, calculation, output_field))
            return value

        momentum_period = int(req("momentum_calculation_period", "momentum lookback window", "momentum"))
        vwap_period = int(req("vwap_calculation_period", "VWAP lookback window", "vwap_deviation"))
        atr_period = int(req("atr_calculation_period", "ATR/volatility lookback window", "volatility"))
        dp_mult = float(req("base_dp_dt_multiplier", "scales price-momentum magnitude", "momentum"))
        dv_mult = float(req("base_dv_dt_multiplier", "scales volume-confirmation magnitude", "volume_confirmation"))
        momentum_weight = float(req("momentum_weight", "component weight", "confidence"))
        vwap_weight = float(req("vwap_weight", "component weight", "confidence"))
        volatility_weight = float(req("volatility_weight", "component weight", "confidence"))
        confirmation_weight = float(req("confirmation_2bar_weight", "component weight", "confidence"))
        green_threshold = float(req("green_threshold", "high-confidence band boundary", "confidence"))
        amber_lower = float(req("amber_threshold_lower", "medium-confidence band boundary", "confidence"))
        red_threshold = float(req("red_threshold", "low-confidence band boundary", "confidence"))
        vol_regime_mult = float(req("volatility_regime_multiplier", "overall regime scaling", "confidence"))
        low_vol_mult = float(req("low_vol_regime_multiplier", "low-vol regime scaling", "confidence"))
        med_vol_mult = float(req("medium_vol_regime_multiplier", "medium-vol regime scaling", "confidence"))
        high_vol_mult = float(req("high_vol_regime_multiplier", "high-vol regime scaling", "confidence"))
        entry_smoothing = int(req("entry_signal_smoothing_window", "entry signal smoothing window", "confidence"))
        exit_smoothing = int(req("exit_signal_smoothing_window", "exit signal smoothing window", "exit_confidence"))
        persistence_requirement = float(req("signal_persistence_requirement", "consecutive-direction persistence bonus", "confidence"))
        entry_threshold = float(req("entry_confidence_threshold", "directional bias floor", "direction"))

        bars = snapshot.bars
        n = len(bars)
        close = bars["close"].to_numpy(dtype=float)
        volume = bars["volume"].to_numpy(dtype=float)
        high = bars["high"].to_numpy(dtype=float)
        low = bars["low"].to_numpy(dtype=float)

        if snapshot.symbol not in self._scale:
            self.calibrate(snapshot.symbol, bars.iloc[: max(30, min(n, 60))])
        scale = self._scale_for(snapshot.symbol)

        # Momentum via talib.ROC: ((close[-1]/close[-period])-1)*100, the
        # same formula as the hand-rolled version, scaled by 100.
        roc_period = min(momentum_period, n - 1) if n > 1 else 1
        roc_period = max(roc_period, 1)
        roc_series = talib.ROC(close, timeperiod=roc_period)
        raw_momentum = float(roc_series[-1]) / 100.0 if np.isfinite(roc_series[-1]) else 0.0
        momentum_z = raw_momentum / (scale["dp_scale"] * math.sqrt(max(momentum_period, 1)))
        momentum = _np_clip(momentum_z / 3.0, -1, 1) * dp_mult

        # VWAP deviation: no TA-Lib equivalent, direct calculation.
        vwap_window_close = close[max(0, n - vwap_period):]
        vwap_window_vol = volume[max(0, n - vwap_period):]
        vwap = float(np.average(vwap_window_close, weights=vwap_window_vol)) if vwap_window_vol.sum() > 0 else float(vwap_window_close.mean())
        raw_vwap_dev = (close[-1] - vwap) / vwap if vwap else 0.0
        vwap_deviation = _np_clip((raw_vwap_dev / scale["dp_scale"]) / 3.0, -1, 1)

        # Volatility via talib.ATR (Wilder-smoothed), a real, documented
        # difference from the original's simple-mean True Range.
        atr_talib_period = min(atr_period, n - 1) if n > 1 else 1
        atr_talib_period = max(atr_talib_period, 1)
        atr_series = talib.ATR(high, low, close, timeperiod=atr_talib_period)
        atr = float(atr_series[-1]) if np.isfinite(atr_series[-1]) else 0.0
        volatility = atr / close[-1] if close[-1] else 0.0
        volatility_score = _np_clip((scale["baseline_vol"] - volatility) / scale["baseline_vol"], -1, 1)

        avg_vol = volume[:-1].mean() if len(volume) > 1 else (volume[-1] if len(volume) else 1.0)
        raw_vol_confirm = (volume[-1] - avg_vol) / avg_vol if avg_vol else 0.0
        volume_confirmation = _np_clip((raw_vol_confirm / scale["dv_scale"]) / 3.0, -1, 1) * dv_mult

        vol_ratio = volatility / scale["baseline_vol"] if scale["baseline_vol"] else 1.0
        if vol_ratio < 0.7:
            regime_mult = low_vol_mult
        elif vol_ratio < 1.5:
            regime_mult = med_vol_mult
        else:
            regime_mult = high_vol_mult

        total_weight = momentum_weight + vwap_weight + volatility_weight + confirmation_weight
        total_weight = total_weight if total_weight > 0 else 1.0
        raw_signal = (
            _np_clip(momentum, -1, 1) * momentum_weight
            + vwap_deviation * vwap_weight
            + volatility_score * volatility_weight
            + _np_clip(volume_confirmation, -1, 1) * confirmation_weight
        ) / total_weight
        raw_signal *= regime_mult * vol_regime_mult

        history = self._history.setdefault(snapshot.symbol, deque(maxlen=max(entry_smoothing, exit_smoothing, 20)))
        history.append(raw_signal)
        smoothed = sum(list(history)[-entry_smoothing:]) / min(len(history), entry_smoothing)
        exit_smoothed = sum(list(history)[-exit_smoothing:]) / min(len(history), exit_smoothing)

        recent = list(history)[-5:]
        same_direction = sum(1 for x in recent if (x > 0) == (smoothed > 0)) if recent else 0
        if len(recent) and same_direction / len(recent) >= (persistence_requirement / 2.0):
            smoothed *= 1.0 + 0.1 * min(persistence_requirement, 2.0)

        confidence = _np_clip(abs(smoothed), 0.0, 1.0)
        if confidence >= green_threshold:
            quality_band = "green"
            confidence = _np_clip(confidence * 1.10, 0.0, 1.0)
        elif confidence >= amber_lower:
            quality_band = "amber"
            confidence *= 0.85
        elif confidence <= red_threshold:
            quality_band = "red"
            confidence *= 0.5
        else:
            quality_band = "neutral"

        direction = 0 if abs(smoothed) < entry_threshold * 0.2 else (1 if smoothed > 0 else -1)

        signal = PASignal(
            symbol=snapshot.symbol, timestamp=snapshot.timestamp, direction=direction,
            confidence=confidence, momentum=momentum, volatility=volatility,
            vwap_deviation=vwap_deviation, volume_confirmation=volume_confirmation,
            exit_confidence=_np_clip(abs(exit_smoothed), 0.0, 1.0), quality_band=quality_band,
        )
        return signal, trace
