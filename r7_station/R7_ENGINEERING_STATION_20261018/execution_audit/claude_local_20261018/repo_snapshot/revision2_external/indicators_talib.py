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

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, MarketSnapshot, ParameterUse, PASignal


def _np_clip(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


# STRUCTURAL_NOT_PARAMETER: denominator/non-finite guard, not a market floor.
_NUMERICAL_EPSILON = 1e-6


def _valid_period(period: int, size: int) -> int:
    # TA-Lib period domain and available observations; no operating horizon here.
    return max(1, min(period, size - 1))


class TALibPredictiveAnalyticsBox:
    def __init__(self) -> None:
        self._history: Dict[str, deque] = {}
        self._scale: Dict[str, Dict[str, float]] = {}
        self._warmup: Dict[str, pd.DataFrame] = {}
        self._atr_period: Dict[str, int] = {}

    def calibrate(self, symbol: str, warmup_bars: pd.DataFrame,
                  config: EffectiveConfig | None = None) -> None:
        # Existing two-argument callers use the canonical default, never legacy 14.
        atr_period = int(config.require("atr_calculation_period") if config is not None
                         else CanonicalParameterRegistry().get("atr_calculation_period").default)
        if warmup_bars.empty:
            raise ValueError("BB04 calibration requires at least one bar")
        self._warmup[symbol] = warmup_bars.copy()
        self._atr_period[symbol] = atr_period
        close = warmup_bars["close"].to_numpy(dtype=float)
        high = warmup_bars["high"].to_numpy(dtype=float)
        low = warmup_bars["low"].to_numpy(dtype=float)
        volume = warmup_bars["volume"].to_numpy(dtype=float)

        returns = pd.Series(close).pct_change().dropna()
        dp_scale = float(returns.std())
        dp_scale = dp_scale if math.isfinite(dp_scale) and dp_scale > 0 else _NUMERICAL_EPSILON
        vol_pct_change = pd.Series(volume).pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        dv_scale = float(vol_pct_change.std())
        dv_scale = dv_scale if math.isfinite(dv_scale) and dv_scale > 0 else _NUMERICAL_EPSILON

        atr_period = _valid_period(atr_period, len(close))
        atr_series = talib.ATR(high, low, close, timeperiod=atr_period)
        baseline_atr = float(np.nanmean(atr_series)) if np.isfinite(atr_series).any() else _NUMERICAL_EPSILON
        baseline_vol = baseline_atr / close[-1] if close[-1] else _NUMERICAL_EPSILON

        self._scale[symbol] = {"dp_scale": dp_scale, "dv_scale": dv_scale, "baseline_vol": max(baseline_vol, _NUMERICAL_EPSILON)}

    def _scale_for(self, symbol: str) -> Dict[str, float]:
        # evaluate() always calibrates first; no unreachable anonymous operating defaults.
        return self._scale[symbol]

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

        momentum_normalization_divisor = float(req("momentum_normalization_divisor", "Momentum amplitude", "momentum"))
        pa_atr_absolute_floor = float(req("pa_atr_absolute_floor", "Absolute ATR fallback trigger and floor", "volatility"))
        pa_atr_fallback_price_fraction = float(req("pa_atr_fallback_price_fraction", "Price-relative ATR fallback", "volatility"))
        pa_persistence_threshold_divisor = float(req("pa_persistence_threshold_divisor", "Persistence qualification normalization", "confidence"))
        pa_persistence_bonus_gain = float(req("pa_persistence_bonus_gain", "Persistence strength gain", "confidence"))
        pa_persistence_bonus_cap = float(req("pa_persistence_bonus_cap", "Persistence bonus input ceiling", "confidence"))
        pa_direction_activation_fraction = float(req("pa_direction_activation_fraction", "Directional activation fraction", "direction"))
        pa_vwap_normalization_divisor = float(req("pa_vwap_normalization_divisor", "VWAP amplitude", "vwap_deviation"))
        pa_volume_normalization_divisor = float(req("pa_volume_normalization_divisor", "Volume confirmation amplitude", "volume_confirmation"))
        pa_low_vol_ratio_boundary = float(req("pa_low_vol_ratio_boundary", "Low volatility regime boundary", "confidence"))
        pa_high_vol_ratio_boundary = float(req("pa_high_vol_ratio_boundary", "High volatility regime boundary", "confidence"))
        pa_persistence_lookback = int(req("pa_persistence_lookback", "Persistence observation window", "confidence"))
        pa_green_confidence_multiplier = float(req("pa_green_confidence_multiplier", "Green band confidence gain", "confidence"))
        pa_amber_confidence_multiplier = float(req("pa_amber_confidence_multiplier", "Amber band confidence gain", "confidence"))
        pa_red_confidence_multiplier = float(req("pa_red_confidence_multiplier", "Red band confidence gain", "confidence"))
        pa_auto_warmup_bars = int(req("pa_auto_warmup_bars", "Automatic calibration warmup length", "confidence"))

        bars = snapshot.bars
        n = len(bars)
        close = bars["close"].to_numpy(dtype=float)
        volume = bars["volume"].to_numpy(dtype=float)
        high = bars["high"].to_numpy(dtype=float)
        low = bars["low"].to_numpy(dtype=float)

        if snapshot.symbol not in self._scale:
            self.calibrate(snapshot.symbol, bars.iloc[:pa_auto_warmup_bars], config)
        elif self._atr_period[snapshot.symbol] != atr_period:
            # Reuse original warmup only: changing the horizon must not introduce future bars.
            self.calibrate(snapshot.symbol, self._warmup[snapshot.symbol], config)
        scale = self._scale_for(snapshot.symbol)

        # Momentum via talib.ROC: ((close[-1]/close[-period])-1)*100, the
        # same formula as the hand-rolled version, scaled by 100.
        roc_period = min(momentum_period, n - 1) if n > 1 else 1
        roc_period = max(roc_period, 1)
        roc_series = talib.ROC(close, timeperiod=roc_period)
        raw_momentum = float(roc_series[-1]) / 100.0 if np.isfinite(roc_series[-1]) else 0.0
        momentum_z = raw_momentum / (scale["dp_scale"] * math.sqrt(max(momentum_period, 1)))
        momentum = _np_clip(momentum_z / momentum_normalization_divisor, -1, 1) * dp_mult

        # VWAP deviation: no TA-Lib equivalent, direct calculation.
        vwap_window_close = close[max(0, n - vwap_period):]
        vwap_window_vol = volume[max(0, n - vwap_period):]
        vwap = float(np.average(vwap_window_close, weights=vwap_window_vol)) if vwap_window_vol.sum() > 0 else float(vwap_window_close.mean())
        raw_vwap_dev = (close[-1] - vwap) / vwap if vwap else 0.0
        vwap_deviation = _np_clip((raw_vwap_dev / scale["dp_scale"]) / pa_vwap_normalization_divisor, -1, 1)

        # Volatility via talib.ATR (Wilder-smoothed), a real, documented
        # difference from the original's simple-mean True Range.
        atr_talib_period = min(atr_period, n - 1) if n > 1 else 1
        atr_talib_period = max(atr_talib_period, 1)
        atr_series = talib.ATR(high, low, close, timeperiod=atr_talib_period)
        atr = float(atr_series[-1]) if np.isfinite(atr_series[-1]) else 0.0
        # BUGFIX: Ensure ATR has minimum value to prevent zero volatility
        if atr < pa_atr_absolute_floor and close[-1] > 0:
            atr = max(pa_atr_absolute_floor, close[-1] * pa_atr_fallback_price_fraction)
        volatility = atr / close[-1] if close[-1] else 0.0
        volatility_score = _np_clip((scale["baseline_vol"] - volatility) / scale["baseline_vol"], -1, 1)

        avg_vol = volume[:-1].mean() if len(volume) > 1 else (volume[-1] if len(volume) else 1.0)
        raw_vol_confirm = (volume[-1] - avg_vol) / avg_vol if avg_vol else 0.0
        volume_confirmation = _np_clip((raw_vol_confirm / scale["dv_scale"]) / pa_volume_normalization_divisor, -1, 1) * dv_mult

        vol_ratio = volatility / scale["baseline_vol"] if scale["baseline_vol"] else 1.0
        if vol_ratio < pa_low_vol_ratio_boundary:
            regime_mult = low_vol_mult
        elif vol_ratio < pa_high_vol_ratio_boundary:
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

        # 20 is a storage reserve, above all configured smoothing/persistence maxima.
        history = self._history.setdefault(snapshot.symbol, deque(maxlen=max(entry_smoothing, exit_smoothing, pa_persistence_lookback, 20)))
        history.append(raw_signal)
        smoothed = sum(list(history)[-entry_smoothing:]) / min(len(history), entry_smoothing)
        exit_smoothed = sum(list(history)[-exit_smoothing:]) / min(len(history), exit_smoothing)

        recent = list(history)[-pa_persistence_lookback:]
        same_direction = sum(1 for x in recent if (x > 0) == (smoothed > 0)) if recent else 0
        if len(recent) and same_direction / len(recent) >= (persistence_requirement / pa_persistence_threshold_divisor):
            smoothed *= 1.0 + pa_persistence_bonus_gain * min(persistence_requirement, pa_persistence_bonus_cap)

        confidence = _np_clip(abs(smoothed), 0.0, 1.0)
        if confidence >= green_threshold:
            quality_band = "green"
            confidence = _np_clip(confidence * pa_green_confidence_multiplier, 0.0, 1.0)
        elif confidence >= amber_lower:
            quality_band = "amber"
            confidence *= pa_amber_confidence_multiplier
        elif confidence <= red_threshold:
            quality_band = "red"
            confidence *= pa_red_confidence_multiplier
        else:
            quality_band = "neutral"

        direction = 0 if abs(smoothed) < entry_threshold * pa_direction_activation_fraction else (1 if smoothed > 0 else -1)

        signal = PASignal(
            symbol=snapshot.symbol, timestamp=snapshot.timestamp, direction=direction,
            confidence=confidence, momentum=momentum, volatility=volatility,
            vwap_deviation=vwap_deviation, volume_confirmation=volume_confirmation,
            exit_confidence=_np_clip(abs(exit_smoothed), 0.0, 1.0), quality_band=quality_band,
        )
        return signal, trace
