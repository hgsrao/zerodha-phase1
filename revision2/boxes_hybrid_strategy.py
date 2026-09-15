"""
Hybrid Trend+Mean Reversion PA Algorithm
========================================
Adapts strategy based on market regime:
- Trending markets: Use trend-following (trade WITH momentum)
- Sideways markets: Use mean reversion (trade AGAINST extremes)

This combines the best of both worlds and eliminates the directional mismatch.
"""

import math
import numpy as np
from collections import deque
from typing import Dict, Tuple, List, Any

from revision2.contracts import PASignal

def _np_clip(val, lo, hi):
    """Clip value to range"""
    return max(lo, min(hi, val))


class HybridPredictiveAnalyticsBox:
    """Hybrid strategy that adapts to market regime"""

    def __init__(self):
        self._scale: Dict[str, Dict[str, float]] = {}
        self._history: Dict[str, deque] = {}

    def calibrate(self, symbol: str, warmup_data):
        """Calibrate on warmup window"""
        close = warmup_data["close"].values
        volume = warmup_data["volume"].values
        high = warmup_data["high"].values
        low = warmup_data["low"].values

        # Calculate scales (same as original)
        returns = np.diff(close) / close[:-1]
        dp_scale = np.std(returns) if len(returns) > 1 else 0.01

        atr_period = 20
        tr = np.maximum(high[1:] - low[1:],
                       np.maximum(np.abs(high[1:] - close[:-1]),
                                 np.abs(low[1:] - close[:-1])))
        baseline_vol = np.mean(tr[-atr_period:]) / close[-1] if close[-1] else 0.01

        dv_scale = np.std(np.diff(volume) / (volume[:-1] + 1)) if len(volume) > 1 else 0.01

        self._scale[symbol] = {
            'dp_scale': max(dp_scale, 0.001),
            'dv_scale': max(dv_scale, 0.001),
            'baseline_vol': max(baseline_vol, 0.001),
        }

        self._history[symbol] = deque(maxlen=100)

    def evaluate(self, snapshot, config):
        """
        Hybrid signal generation - adapts to market regime

        Regime Detection:
        - BULLISH: MA5 > MA20 AND price > MA20 AND low volatility
        - BEARISH: MA5 < MA20 AND price < MA20 AND low volatility
        - SIDEWAYS: Everything else

        Strategy:
        - BULLISH: Generate LONG signals on dips (trend-following)
        - BEARISH: Generate SHORT signals on rallies (trend-following)
        - SIDEWAYS: Generate mean reversion signals (buy oversold, sell overbought)
        """
        symbol = snapshot.symbol
        bars = snapshot.bars
        n = len(bars)

        close = bars["close"].values
        volume = bars["volume"].values
        high = bars["high"].values
        low = bars["low"].values

        scale = self._scale.get(symbol, {'dp_scale': 0.01, 'dv_scale': 0.01, 'baseline_vol': 0.01})

        # ====================================================================
        # VOLATILITY & TREND COMPONENTS
        # ====================================================================

        atr_period = 20
        if len(high) > atr_period:
            high_low = high[-atr_period:] - low[-atr_period:]
            high_close = np.abs(high[-atr_period:] - close[-atr_period-1:-1])
            low_close = np.abs(low[-atr_period:] - close[-atr_period-1:-1])
            tr = np.maximum(high_low, np.maximum(high_close, low_close))
            atr = float(np.mean(tr))
        else:
            atr = 0.001

        volatility = atr / close[-1] if close[-1] else 0.0

        # ====================================================================
        # REGIME DETECTION (Key Decision Point)
        # ====================================================================

        if len(close) >= 20:
            ma_5 = np.mean(close[-5:])
            ma_20 = np.mean(close[-20:])
            current = close[-1]
            vol_ratio = atr / close[-1] if close[-1] else 1.0

            # Trending detection: Clear MA separation AND clean volatility
            ma_separation = abs(ma_5 - ma_20) / ma_20
            is_clean_trend = vol_ratio < 0.015  # Very clean trends have low volatility

            if ma_5 > ma_20 and current > ma_20 and ma_separation > 0.003:
                regime = "bullish"
                is_trending = True
            elif ma_5 < ma_20 and current < ma_20 and ma_separation > 0.003:
                regime = "bearish"
                is_trending = True
            else:
                regime = "sideways"
                is_trending = False
        else:
            ma_5 = close[-1]
            ma_20 = close[-1]
            current = close[-1]
            regime = "sideways"
            is_trending = False
            std_20 = 0.0

        # ====================================================================
        # MEAN REVERSION SCORE (for all regimes - used differently)
        # ====================================================================

        if len(close) >= 20:
            std_20 = np.std(close[-20:])

            if std_20 > 0:
                z_score = (current - ma_20) / std_20
                mean_rev_score = abs(_np_clip(z_score / 2.0, -1.0, 1.0))
                z_normalized = z_score  # For directional decisions
            else:
                mean_rev_score = 0.0
                z_normalized = 0.0
        else:
            ma_20 = close[-1]
            std_20 = 0.0
            mean_rev_score = 0.0
            z_normalized = 0.0

        # ====================================================================
        # SIGNAL GENERATION - STRATEGY ADAPTED TO REGIME
        # ====================================================================

        direction = 0
        confidence = 0.0
        signal_type = ""

        if regime == "bullish" and is_trending:
            # ================================================================
            # BULLISH REGIME: Trend-Following Strategy
            # ================================================================
            # Buy dips in uptrend (when price pulls back toward MA20)
            # Expect continuation higher

            if mean_rev_score > 0.3:  # Meaningful pullback
                if z_normalized < 0:  # Price below MA20 (oversold relative to trend)
                    direction = 1  # BUY the dip in uptrend
                    confidence = mean_rev_score * 0.85
                    signal_type = "trend_follow_long"
                elif z_normalized > 0.5 and mean_rev_score > 0.7:
                    # Strong continuation signal
                    direction = 1  # Follow the trend higher
                    confidence = mean_rev_score * 0.7
                    signal_type = "trend_continuation_long"

            # Avoid shorting in bullish - that's fighting the trend
            direction = max(direction, 0)  # Force to LONG or NONE, never SHORT

        elif regime == "bearish" and is_trending:
            # ================================================================
            # BEARISH REGIME: Trend-Following Strategy
            # ================================================================
            # Short rallies in downtrend (when price pulls back toward MA20)
            # Expect continuation lower

            if mean_rev_score > 0.3:  # Meaningful rally
                if z_normalized > 0:  # Price above MA20 (overbought relative to trend)
                    direction = -1  # SHORT the rally in downtrend
                    confidence = mean_rev_score * 0.85
                    signal_type = "trend_follow_short"
                elif z_normalized < -0.5 and mean_rev_score > 0.7:
                    # Strong continuation signal
                    direction = -1  # Follow the trend lower
                    confidence = mean_rev_score * 0.7
                    signal_type = "trend_continuation_short"

            # Avoid longing in bearish - that's fighting the trend
            direction = min(direction, 0)  # Force to SHORT or NONE, never LONG

        else:
            # ================================================================
            # SIDEWAYS REGIME: Mean Reversion Strategy
            # ================================================================
            # Buy oversold (z < -1σ), sell overbought (z > +1σ)

            if mean_rev_score > 0.5:  # Strong mean reversion setup
                if z_normalized < -1.0:  # OVERSOLD
                    direction = 1  # BUY the bounce
                    confidence = mean_rev_score * 0.80
                    signal_type = "mean_reversion_long"
                elif z_normalized > 1.0:  # OVERBOUGHT
                    direction = -1  # SELL the pullback
                    confidence = mean_rev_score * 0.80
                    signal_type = "mean_reversion_short"

        # ====================================================================
        # VOLUME ADJUSTMENT (applies to all regimes)
        # ====================================================================

        if len(volume) >= 20 and confidence > 0:
            recent_vol = np.mean(volume[-5:])
            historical_vol = np.mean(volume[-20:])
            if historical_vol > 0:
                vol_score = min(recent_vol / historical_vol, 2.0)
                confidence *= (0.5 + 0.5 * vol_score)

        # ====================================================================
        # VOLATILITY REGIME ADJUSTMENT (applies to all regimes)
        # ====================================================================

        vol_regime_mult = 1.0
        if volatility < scale['baseline_vol'] * 0.7:
            vol_regime_mult = 1.2  # Low vol = cleaner signals
        elif volatility > scale['baseline_vol'] * 1.5:
            vol_regime_mult = 0.7  # High vol = noisier signals

        confidence *= vol_regime_mult

        # ====================================================================
        # QUALITY BANDS
        # ====================================================================

        confidence = _np_clip(confidence, 0.0, 1.0)

        if confidence < 0.15:
            quality_band = "red"
            direction = 0
        elif confidence < 0.35:
            quality_band = "amber"
        else:
            quality_band = "green"

        exit_confidence = confidence * 0.9

        # ====================================================================
        # BUILD RETURN SIGNAL - Return PASignal object (orchestrator format)
        # ====================================================================

        signal = PASignal(
            symbol=symbol,
            timestamp=str(bars.iloc[-1].get("timestamp", "")),
            direction=int(direction),
            confidence=float(confidence),
            momentum=0.0,  # Disabled in hybrid - using mean reversion score instead
            volatility=float(volatility),
            vwap_deviation=0.0,  # Disabled in hybrid
            volume_confirmation=0.0,  # Handled in confidence adjustment
            exit_confidence=float(exit_confidence),
            quality_band=quality_band,
        )

        return signal, []
