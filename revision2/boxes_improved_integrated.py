"""
Improved PA Algorithm - INTEGRATED VERSION
Replaces momentum-based signals with mean reversion + regime detection
To use: Replace PredictiveAnalyticsBox imports with ImprovedPredictiveAnalyticsBox
"""

import math
import numpy as np
from collections import deque
from typing import Dict, Tuple, List, Any

def _np_clip(val, lo, hi):
    """Clip value to range"""
    return max(lo, min(hi, val))


class ImprovedPredictiveAnalyticsBox:
    """Mean Reversion + Regime Detection PA (replacement for momentum-based PA)"""
    
    def __init__(self):
        self._scale: Dict[str, Dict[str, float]] = {}
        self._history: Dict[str, deque] = {}
    
    def calibrate(self, symbol: str, warmup_data):
        """Calibrate on warmup window - IDENTICAL TO ORIGINAL"""
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
        Generate improved signal with mean reversion + regime detection
        Returns: (signal dict matching original format, trace list)
        """
        symbol = snapshot.symbol
        bars = snapshot.bars
        n = len(bars)
        
        close = bars["close"].values
        volume = bars["volume"].values
        high = bars["high"].values
        low = bars["low"].values
        
        scale = self._scale.get(symbol, {'dp_scale': 0.01, 'dv_scale': 0.01, 'baseline_vol': 0.01})
        
        # Calculate components
        atr_period = 20
        if len(high) > atr_period:
            # True range: max(high-low, |high-prev_close|, |low-prev_close|)
            high_low = high[-atr_period:] - low[-atr_period:]
            high_close = np.abs(high[-atr_period:] - close[-atr_period-1:-1])
            low_close = np.abs(low[-atr_period:] - close[-atr_period-1:-1])
            tr = np.maximum(high_low, np.maximum(high_close, low_close))
            atr = float(np.mean(tr))
        else:
            atr = 0.001
        
        volatility = atr / close[-1] if close[-1] else 0.0
        
        # MEAN REVERSION SCORE (replaces momentum)
        if len(close) >= 20:
            ma_20 = np.mean(close[-20:])
            std_20 = np.std(close[-20:])
            current = close[-1]
            
            if std_20 > 0:
                z_score = (current - ma_20) / std_20
                mean_rev_score = abs(_np_clip(z_score / 2.0, -1.0, 1.0))
            else:
                mean_rev_score = 0.0
        else:
            ma_20 = close[-1]
            mean_rev_score = 0.0
        
        # REGIME DETECTION
        if len(close) >= 20:
            ma_5 = np.mean(close[-5:])
            vol_ratio = atr / close[-1] if close[-1] else 1.0
            
            if ma_5 > ma_20 and current > ma_20 and vol_ratio < 0.02:
                regime = "bullish"
            elif ma_5 < ma_20 and current < ma_20 and vol_ratio < 0.02:
                regime = "bearish"
            else:
                regime = "sideways"
        else:
            regime = "sideways"
        
        # SIGNAL GENERATION
        direction = 0
        confidence = 0.0
        
        if mean_rev_score > 0.5:  # Strong mean reversion setup
            if current < ma_20 - std_20:  # OVERSOLD
                direction = 1  # BUY the bounce
                confidence = mean_rev_score * 0.8
            elif current > ma_20 + std_20:  # OVERBOUGHT
                direction = -1  # SELL the pullback
                confidence = mean_rev_score * 0.8
        
        # VOLUME ADJUSTMENT
        if len(volume) >= 20:
            recent_vol = np.mean(volume[-5:])
            historical_vol = np.mean(volume[-20:])
            if historical_vol > 0:
                vol_score = min(recent_vol / historical_vol, 2.0)
                confidence *= (0.5 + 0.5 * vol_score)  # Boost for volume confirmation
        
        # REGIME FILTER - skip counter-trend mean reversion in strong trends
        if regime == "bullish" and direction == -1:  # Shorting in uptrend
            confidence *= 0.5  # Reduce confidence
        elif regime == "bearish" and direction == 1:  # Longing in downtrend
            confidence *= 0.5  # Reduce confidence
        
        # VOLATILITY ADJUSTMENT
        vol_regime_mult = 1.0
        if volatility < scale['baseline_vol'] * 0.7:
            vol_regime_mult = 1.2  # Low vol = cleaner signals
        elif volatility > scale['baseline_vol'] * 1.5:
            vol_regime_mult = 0.7  # High vol = noisier signals
        
        confidence *= vol_regime_mult
        
        # QUALITY BANDS (same as original)
        confidence = _np_clip(confidence, 0.0, 1.0)
        
        if confidence < 0.15:
            quality_band = "red"
            direction = 0
        elif confidence < 0.35:
            quality_band = "amber"
        else:
            quality_band = "green"
        
        exit_confidence = confidence * 0.9
        
        # Build return dict matching original PA signal format
        signal = {
            'symbol': symbol,
            'timestamp': str(bars.iloc[-1].get("timestamp", "")),
            'confidence': float(confidence),
            'direction': int(direction),
            'quality_band': quality_band,
            'momentum': 0.0,  # Disabled
            'volatility': float(volatility),
            'exit_confidence': float(exit_confidence),
            'regime': regime,
            'mean_reversion_score': float(mean_rev_score),
        }
        
        return signal, []

