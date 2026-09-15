"""
Hybrid Trend+Mean Reversion PA Algorithm - Version 2
====================================================
IMPROVED: More aggressive trend detection and better regime-based signal generation

Strategy:
- Detect trend strength from MA relationship and recent price movement
- On STRONG trends (like Sep 1): Generate aggressive LONG signals in uptrends
- On SIDEWAYS: Generate contrarian mean reversion signals
- KEY CHANGE: Force trend-following direction in trending regimes, not NONE
"""

import math
import numpy as np
from collections import deque
from typing import Dict, Tuple, List, Any

from revision2.contracts import PASignal

def _np_clip(val, lo, hi):
    """Clip value to range"""
    return max(lo, min(hi, val))


class HybridPredictiveAnalyticsBoxV2:
    """Hybrid strategy with improved trend detection and aggressive trend-following"""

    def __init__(self):
        self._scale: Dict[str, Dict[str, float]] = {}
        self._history: Dict[str, deque] = {}

    def calibrate(self, symbol: str, warmup_data):
        """Calibrate on warmup window"""
        close = warmup_data["close"].values
        volume = warmup_data["volume"].values
        high = warmup_data["high"].values
        low = warmup_data["low"].values

        # Calculate scales
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
        Hybrid signal generation V2 - More aggressive trend-following
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
        # VOLATILITY & MOMENTUM
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
        # TREND DETECTION (Key Improvement: More Aggressive)
        # ====================================================================

        if len(close) >= 20:
            ma_5 = np.mean(close[-5:])
            ma_20 = np.mean(close[-20:])
            current = close[-1]

            # Recent momentum (last 10 bars)
            recent_momentum = (close[-1] - close[-10]) / close[-10] if close[-10] else 0.0

            # Trend strength metrics (relaxed thresholds)
            ma_spread = abs(ma_5 - ma_20) / ma_20  # How far apart are the MAs
            price_above_ma = (current - ma_20) / ma_20  # How far above/below MA20

            # RELAXED DETECTION: Only need gentle MA separation, not 0.3%
            # Strong bullish: MA5 > MA20 + recent upward momentum
            is_bullish = (ma_5 > ma_20 * 0.995 and  # MA5 barely above MA20 (0.5% threshold)
                         current > ma_20 * 0.97 and  # Price above MA20
                         recent_momentum > 0.001)     # Recent upward momentum

            # Strong bearish: MA5 < MA20 + recent downward momentum
            is_bearish = (ma_5 < ma_20 * 1.005 and  # MA5 barely below MA20
                         current < ma_20 * 1.03 and  # Price below MA20
                         recent_momentum < -0.001)   # Recent downward momentum

            # Sideways: MA5 ≈ MA20
            is_sideways = not (is_bullish or is_bearish)

            if is_bullish:
                regime = "bullish"
            elif is_bearish:
                regime = "bearish"
            else:
                regime = "sideways"
        else:
            ma_5 = close[-1]
            ma_20 = close[-1]
            current = close[-1]
            regime = "sideways"
            is_bullish = False
            is_bearish = False

        # ====================================================================
        # MEAN REVERSION SCORE
        # ====================================================================

        if len(close) >= 20:
            std_20 = np.std(close[-20:])

            if std_20 > 0:
                z_score = (current - ma_20) / std_20
                mean_rev_score = abs(_np_clip(z_score / 2.0, -1.0, 1.0))
                z_normalized = z_score
            else:
                mean_rev_score = 0.0
                z_normalized = 0.0
        else:
            std_20 = 0.0
            mean_rev_score = 0.0
            z_normalized = 0.0

        # ====================================================================
        # SIGNAL GENERATION - ADAPTIVE TO REGIME
        # ====================================================================

        direction = 0
        confidence = 0.0

        if is_bullish:
            # ================================================================
            # BULLISH TREND: Generate LONG signals
            # ================================================================
            # On uptrends, aggressively take LONG entries
            # - On dips (negative z-score): Strong entry signal
            # - On momentum (positive z-score): Continuation signal

            confidence_base = 0.65  # High base confidence in uptrends

            if mean_rev_score > 0.2:  # Some volatility/opportunity
                if z_normalized < 0:  # Price dipped toward MA (BUY THE DIP)
                    direction = 1  # LONG
                    confidence = min(0.95, confidence_base + (0.3 * abs(z_normalized)))
                else:  # Price above MA (MOMENTUM CONTINUATION)
                    direction = 1  # LONG
                    confidence = min(0.85, confidence_base + (0.2 * z_normalized))
            else:
                # Low volatility in uptrend - still go LONG
                direction = 1
                confidence = 0.60

        elif is_bearish:
            # ================================================================
            # BEARISH TREND: Generate SHORT signals
            # ================================================================
            # On downtrends, aggressively take SHORT entries

            confidence_base = 0.65

            if mean_rev_score > 0.2:
                if z_normalized > 0:  # Price rallied toward MA (SHORT THE RALLY)
                    direction = -1  # SHORT
                    confidence = min(0.95, confidence_base + (0.3 * abs(z_normalized)))
                else:  # Price below MA (MOMENTUM CONTINUATION)
                    direction = -1  # SHORT
                    confidence = min(0.85, confidence_base + (0.2 * abs(z_normalized)))
            else:
                # Low volatility in downtrend - still go SHORT
                direction = -1
                confidence = 0.60

        else:
            # ================================================================
            # SIDEWAYS: Use Mean Reversion
            # ================================================================

            if mean_rev_score > 0.5:  # Strong mean reversion setup
                if z_normalized < -1.0:  # OVERSOLD
                    direction = 1  # BUY
                    confidence = min(0.90, 0.70 + (0.2 * mean_rev_score))
                elif z_normalized > 1.0:  # OVERBOUGHT
                    direction = -1  # SELL
                    confidence = min(0.90, 0.70 + (0.2 * mean_rev_score))
                else:
                    # Moderate mean reversion
                    if z_normalized < -0.5:
                        direction = 1
                        confidence = 0.50
                    elif z_normalized > 0.5:
                        direction = -1
                        confidence = 0.50

        # ====================================================================
        # VOLUME ADJUSTMENT
        # ====================================================================

        if len(volume) >= 20 and confidence > 0:
            recent_vol = np.mean(volume[-5:])
            historical_vol = np.mean(volume[-20:])
            if historical_vol > 0:
                vol_score = min(recent_vol / historical_vol, 2.0)
                confidence *= (0.6 + 0.4 * vol_score)  # Volume confirmation boost

        # ====================================================================
        # VOLATILITY REGIME ADJUSTMENT
        # ====================================================================

        vol_regime_mult = 1.0
        if volatility < scale['baseline_vol'] * 0.7:
            vol_regime_mult = 1.15  # Low vol = cleaner signals
        elif volatility > scale['baseline_vol'] * 1.5:
            vol_regime_mult = 0.75  # High vol = noisier signals

        confidence *= vol_regime_mult

        # ====================================================================
        # QUALITY BANDS - Refined thresholds
        # ====================================================================

        confidence = _np_clip(confidence, 0.0, 1.0)

        if confidence >= 0.75:
            quality_band = "green"
        elif confidence >= 0.45:
            quality_band = "amber"
        elif confidence >= 0.15:
            quality_band = "neutral"
        else:
            quality_band = "red"
            direction = 0  # Only suppress NONE if very low confidence

        exit_confidence = confidence * 0.9

        # ====================================================================
        # BUILD RETURN SIGNAL
        # ====================================================================

        signal = PASignal(
            symbol=symbol,
            timestamp=str(bars.iloc[-1].get("timestamp", "")),
            direction=int(direction),
            confidence=float(confidence),
            momentum=0.0,
            volatility=float(volatility),
            vwap_deviation=0.0,
            volume_confirmation=0.0,
            exit_confidence=float(exit_confidence),
            quality_band=quality_band,
        )

        return signal, []
