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


_STATE_VERSION = 1
_STATE_FIELDS = {"version", "config_hash", "symbols"}
_SYMBOL_FIELDS = {"atr_period", "scale", "warmup", "history"}
_SCALE_FIELDS = {"dp_scale", "dv_scale", "baseline_vol"}
_WARMUP_FIELDS = {"timestamp", "open", "high", "low", "close", "volume"}
_HISTORY_FIELDS = {"maxlen", "values"}
_EXCHANGE_TZ = "Asia/Kolkata"
# STRUCTURAL_NOT_PARAMETER: payload-size guard against a corrupt/hostile file, not a market horizon.
_MAX_WARMUP_ROWS = 200_000


def _history_capacity(entry_smoothing, exit_smoothing, pa_persistence_lookback):
    """Shared existing deque reserve; recovery must use the same capacity as evaluation."""
    return max(entry_smoothing, exit_smoothing, pa_persistence_lookback, 20)


def _state_real(value: Any, what: str) -> float:
    """Finite real number; bool/str/None/NaN/inf/oversized ints are never accepted as PA state."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid PA state: {what} is not a number")
    try:
        number = float(value)  # an oversized JSON int raises OverflowError here
        finite = math.isfinite(number)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"Invalid PA state: {what} is not finite") from None
    if not finite:
        raise ValueError(f"Invalid PA state: {what} is not finite")
    return number


def _state_int(value: Any, what: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"Invalid PA state: {what}")
    return value


def _state_dict(value: Any, keys: set, what: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Invalid PA state: {what} schema")
    return value


def _exchange_time(value: Any, what: str, *, localize_naive: bool) -> pd.Timestamp:
    """Nanosecond Timestamp in exchange-local time; an equivalent UTC/offset form is accepted."""
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, OverflowError, TypeError):
        raise ValueError(f"Invalid PA state: {what} is not a timestamp") from None
    if pd.isna(stamp):
        raise ValueError(f"Invalid PA state: {what} is not a timestamp")
    if stamp.tzinfo is None:
        if not localize_naive:
            raise ValueError(f"Invalid PA state: {what} is not timezone-aware")
        stamp = stamp.tz_localize(_EXCHANGE_TZ)
    return stamp.tz_convert(_EXCHANGE_TZ).as_unit("ns")


def _calibration_scale(warmup_bars: pd.DataFrame, atr_period: int) -> Dict[str, float]:
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

    return {"dp_scale": dp_scale, "dv_scale": dv_scale, "baseline_vol": max(baseline_vol, _NUMERICAL_EPSILON)}


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
        self._scale[symbol] = _calibration_scale(warmup_bars, atr_period)

    # ---- exact state continuity (pa_v1) --------------------------------------------------------
    # Persisted: the calibration warmup frame (OHLCV + exchange-local timestamps), the ATR period it
    # was calibrated with, the derived scale, and the raw-signal smoothing deque.  The scale is
    # recomputed once from the restored warmup on staging and must equal the saved value exactly, so
    # a changed library/numeric path fails closed.  This box has no compute_signal; evaluate() is the
    # only signal path and is not touched by restoration.

    @staticmethod
    def _config_bounds(config: EffectiveConfig) -> Tuple[int, int]:
        def positive_int(name: str) -> int:
            value = config.require(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or int(value) < 1:
                raise ValueError(f"Invalid PA state: configured {name}")
            return int(value)

        history_len = _history_capacity(positive_int("entry_signal_smoothing_window"),
                          positive_int("exit_signal_smoothing_window"),
                          positive_int("pa_persistence_lookback"))
        return positive_int("atr_calculation_period"), history_len

    @staticmethod
    def _live_columns(frame: Any) -> Tuple[List[pd.Timestamp], Dict[str, List[float]]]:
        if not isinstance(frame, pd.DataFrame) or not _WARMUP_FIELDS.issubset(frame.columns):
            raise ValueError("Invalid PA state: live warmup is not a timestamped OHLCV frame")
        try:
            stamps = list(pd.to_datetime(frame["timestamp"]))
            columns = {name: frame[name].to_numpy(dtype=float).tolist() for name in _WARMUP_FIELDS - {"timestamp"}}
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Invalid PA state: live warmup is not a certified OHLCV frame") from None
        return stamps, columns

    def export_state(self, config: EffectiveConfig) -> Dict[str, Any]:
        """JSON-safe ``pa_v1`` payload; validated here so an invalid state never reaches disk."""
        if not (set(self._scale) == set(self._warmup) == set(self._atr_period)) \
                or not set(self._history).issubset(self._scale):
            raise ValueError("Invalid PA state: live calibration maps are inconsistent")
        symbols: Dict[str, Any] = {}
        for symbol in self._scale:
            stamps, columns = self._live_columns(self._warmup[symbol])
            history = self._history.get(symbol)
            symbols[symbol] = dict(
                atr_period=self._atr_period[symbol],
                scale={k: _state_real(v, f"scale {k}") for k, v in self._scale[symbol].items()},
                warmup=dict(timestamp=[_exchange_time(s, "warmup timestamp", localize_naive=False).isoformat()
                                       for s in stamps], **columns),
                history=None if history is None else dict(
                    maxlen=history.maxlen, values=[_state_real(v, "raw signal") for v in history]))
        payload = dict(version=_STATE_VERSION, config_hash=config.config_hash, symbols=symbols)
        self._stage_state(payload, config, None)
        return payload

    def _stage_state(self, payload: Any, config: EffectiveConfig, not_after: Any):
        """Strictly validate and rebuild into NEW objects; touches no live attribute."""
        payload = _state_dict(payload, _STATE_FIELDS, "payload")
        if type(payload["version"]) is not int or payload["version"] != _STATE_VERSION:
            raise ValueError("Unsupported PA state version")
        if type(payload["config_hash"]) is not str or payload["config_hash"] != config.config_hash:
            raise ValueError("PA state was saved under a different config")
        atr_period, history_len = self._config_bounds(config)
        cutoff = None if not_after is None else _exchange_time(not_after, "not_after", localize_naive=True)
        if not isinstance(payload["symbols"], dict):
            raise ValueError("Invalid PA state: symbols")

        scale_map: Dict[str, Dict[str, float]] = {}
        warmup_map: Dict[str, pd.DataFrame] = {}
        atr_map: Dict[str, int] = {}
        history_map: Dict[str, deque] = {}
        for symbol, entry in payload["symbols"].items():
            if type(symbol) is not str or not symbol:
                raise ValueError("Invalid PA state: symbol")
            entry = _state_dict(entry, _SYMBOL_FIELDS, "symbol entry")
            if _state_int(entry["atr_period"], "atr period", 1) != atr_period:
                raise ValueError("PA state ATR period differs from config")
            saved_scale = _state_dict(entry["scale"], _SCALE_FIELDS, "scale")
            saved_scale = {k: _state_real(saved_scale[k], f"scale {k}") for k in sorted(_SCALE_FIELDS)}
            if any(v <= 0 for v in saved_scale.values()):
                raise ValueError("Invalid PA state: scale must be positive")

            raw = _state_dict(entry["warmup"], _WARMUP_FIELDS, "warmup")
            rows = raw["timestamp"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_WARMUP_ROWS:
                raise ValueError("Invalid PA state: warmup length")
            if any(type(v) is not str for v in rows):
                raise ValueError("Invalid PA state: warmup timestamp is not text")
            stamps = [_exchange_time(v, "warmup timestamp", localize_naive=False) for v in rows]
            if any(b <= a for a, b in zip(stamps, stamps[1:])):
                raise ValueError("Invalid PA state: warmup timestamps are not strictly increasing")
            if cutoff is not None and stamps[-1] > cutoff:
                raise ValueError("PA warmup contains bars after the durable cursor")
            columns = {}
            for name in ("open", "high", "low", "close", "volume"):
                values = raw[name]
                if not isinstance(values, list) or len(values) != len(stamps):
                    raise ValueError(f"Invalid PA state: warmup {name} shape")
                columns[name] = [_state_real(v, f"warmup {name}") for v in values]
                limit_ok = all(v >= 0 for v in columns[name]) if name == "volume" else all(v > 0 for v in columns[name])
                if not limit_ok:
                    raise ValueError(f"Invalid PA state: warmup {name} range")
            for op, high, low, close in zip(columns["open"], columns["high"], columns["low"], columns["close"]):
                if high < max(op, low, close) or low > min(op, high, close):
                    raise ValueError("Invalid PA state: warmup OHLC geometry")
            frame = pd.DataFrame(dict(timestamp=pd.DatetimeIndex(stamps).as_unit("ns"), **columns))

            recomputed = _calibration_scale(frame, atr_period)
            if recomputed != saved_scale:
                raise ValueError("PA scale differs from recomputation on the saved warmup")

            saved_history = entry["history"]
            if saved_history is not None:
                saved_history = _state_dict(saved_history, _HISTORY_FIELDS, "history")
                if type(saved_history["maxlen"]) is not int or saved_history["maxlen"] != history_len:
                    raise ValueError("Invalid PA state: signal history maxlen differs from config")
                if not isinstance(saved_history["values"], list) or len(saved_history["values"]) > history_len:
                    raise ValueError("Invalid PA state: signal history length")
                history_map[symbol] = deque((_state_real(v, "raw signal") for v in saved_history["values"]),
                                            maxlen=history_len)
            scale_map[symbol], warmup_map[symbol], atr_map[symbol] = recomputed, frame, atr_period
        return scale_map, warmup_map, atr_map, history_map

    def restore_state(self, payload: Any, config: EffectiveConfig, not_after: Any = None) -> None:
        """Install a saved ``pa_v1`` payload into this EMPTY box; all-or-nothing.

        ``not_after`` (exchange-local if naive) bounds the newest warmup bar: a warmup that postdates
        the durable cursor would leak future data into calibration.
        """
        if self._history or self._scale or self._warmup or self._atr_period:
            raise ValueError("PA state can only be restored into an empty box")
        scale, warmup, atr, history = self._stage_state(payload, config, not_after)
        self._scale, self._warmup, self._atr_period, self._history = scale, warmup, atr, history

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
        history = self._history.setdefault(snapshot.symbol, deque(maxlen=_history_capacity(entry_smoothing, exit_smoothing, pa_persistence_lookback)))
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
