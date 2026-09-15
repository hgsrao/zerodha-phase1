"""
Improved PA Algorithm - V2
Replaces anti-predictive momentum with mean reversion + regime detection
"""

import math
import numpy as np
from collections import deque
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List

@dataclass
class ImprovedPASignal:
    """Improved signal with regime and mean reversion components"""
    symbol: str
    timestamp: str
    confidence: float
    direction: int  # -1, 0, +1
    quality_band: str  # red, amber, green
    momentum: float
    volatility: float
    exit_confidence: float
    
    # New components
    regime: str  # bullish, bearish, sideways
    mean_reversion_score: float
    volume_score: float
    trend_strength: float


class ImprovedPredictiveAnalyticsBox:
    """Redesigned PA box with regime detection and mean reversion"""
    
    def __init__(self):
        self._scale: Dict[str, Dict[str, float]] = {}
        self._history: Dict[str, deque] = {}
        self._trend_history: Dict[str, deque] = {}
    
    def calibrate(self, symbol: str, warmup_data):
        """Calibrate on warmup window"""
        close = warmup_data["close"].values
        volume = warmup_data["volume"].values
        high = warmup_data["high"].values
        low = warmup_data["low"].values
        
        # Calculate calibration scales
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
        self._trend_history[symbol] = deque(maxlen=50)
    
    def _detect_regime(self, close_prices: np.ndarray, atr: float) -> str:
        """Detect market regime: bullish, bearish, or sideways"""
        if len(close_prices) < 20:
            return "sideways"
        
        # Simple trend detection using close vs moving averages
        ma_20 = np.mean(close_prices[-20:])
        ma_5 = np.mean(close_prices[-5:])
        
        current = close_prices[-1]
        
        # Volatility ratio
        vol_ratio = atr / current if current else 1.0
        
        if ma_5 > ma_20 and current > ma_20:
            if vol_ratio < 0.02:  # Low volatility in uptrend = strong bull
                return "bullish"
        elif ma_5 < ma_20 and current < ma_20:
            if vol_ratio < 0.02:  # Low volatility in downtrend = strong bear
                return "bearish"
        
        return "sideways"
    
    def _mean_reversion_score(self, close_prices: np.ndarray) -> float:
        """Score how far price has deviated from mean (mean reversion opportunity)"""
        if len(close_prices) < 20:
            return 0.0
        
        ma_20 = np.mean(close_prices[-20:])
        current = close_prices[-1]
        std_dev = np.std(close_prices[-20:])
        
        if std_dev == 0:
            return 0.0
        
        z_score = (current - ma_20) / std_dev
        # Higher deviation = stronger mean reversion signal
        # Cap at [-2, 2] range
        reversion_score = np.clip(z_score / 2.0, -1.0, 1.0)
        
        return abs(reversion_score)  # Strength of signal
    
    def _volume_score(self, volumes: np.ndarray) -> float:
        """Score volume participation"""
        if len(volumes) < 10:
            return 0.5
        
        recent_vol = np.mean(volumes[-5:])
        historical_vol = np.mean(volumes[-20:])
        
        if historical_vol == 0:
            return 0.5
        
        vol_ratio = recent_vol / historical_vol
        # Volume surge = higher confidence
        return np.clip(vol_ratio / 2.0, 0.1, 1.0)
    
    def evaluate(self, snapshot, config) -> Tuple[ImprovedPASignal, List]:
        """Generate improved signal with regime and mean reversion"""
        
        symbol = snapshot.symbol
        bars = snapshot.bars
        n = len(bars)
        
        if n < 60:
            # Return neutral signal during warmup
            return ImprovedPASignal(
                symbol=symbol, timestamp="", confidence=0.0, direction=0,
                quality_band="red", momentum=0.0, volatility=0.0,
                exit_confidence=0.0, regime="sideways", 
                mean_reversion_score=0.0, volume_score=0.5, trend_strength=0.0
            ), []
        
        close = bars["close"].values
        volume = bars["volume"].values
        high = bars["high"].values
        low = bars["low"].values
        
        scale = self._scale.get(symbol, {'dp_scale': 0.01, 'dv_scale': 0.01, 'baseline_vol': 0.01})
        
        # Calculate components
        atr_period = 20
        tr = np.maximum(high[-atr_period:] - low[-atr_period:],
                       np.maximum(np.abs(high[-atr_period:] - close[-atr_period-1:-1]),
                                 np.abs(low[-atr_period:] - close[-atr_period-1:-1])))
        atr = float(np.mean(tr)) if len(tr) > 0 else 0.001
        
        # Regime detection
        regime = self._detect_regime(close, atr)
        
        # Mean reversion score (replaces momentum for better predictivity)
        mean_rev_score = self._mean_reversion_score(close)
        
        # Volume analysis
        vol_score = self._volume_score(volume)
        
        # Volatility
        volatility = atr / close[-1] if close[-1] else 0.0
        vol_regime_mult = 1.0
        if volatility < scale['baseline_vol'] * 0.7:
            vol_regime_mult = 1.2  # Low vol = cleaner moves = higher confidence
        elif volatility > scale['baseline_vol'] * 1.5:
            vol_regime_mult = 0.8  # High vol = less reliable
        
        # Trend strength (how consistent is the trend)
        recent_close = close[-5:]
        trend_up = sum(1 for i in range(1, len(recent_close)) if recent_close[i] > recent_close[i-1])
        trend_strength = trend_up / 4.0  # 0 to 1
        
        # Combine signals
        # Mean reversion in trending markets
        direction = 0
        confidence = 0.0
        
        if regime == "bullish":
            # In bullish market, take mean reversion DOWN signals (oversold bounces)
            if mean_rev_score > 0.5 and trend_strength > 0.5:
                direction = 1  # Long the bounce
                confidence = mean_rev_score * 0.6 + vol_score * 0.3 + trend_strength * 0.1
        elif regime == "bearish":
            # In bearish market, take mean reversion UP signals (oversold bounces in downtrend)
            if mean_rev_score > 0.5 and trend_strength > 0.5:
                direction = -1  # Short the bounce
                confidence = mean_rev_score * 0.6 + vol_score * 0.3 + trend_strength * 0.1
        else:  # Sideways
            # In sideways, mean reversion is strongest signal
            if mean_rev_score > 0.6:
                if close[-1] < np.mean(close[-20:]):
                    direction = 1  # Below MA, expect bounce up
                else:
                    direction = -1  # Above MA, expect pullback
                confidence = mean_rev_score * 0.7 + vol_score * 0.3
        
        # Adjust for volume participation
        confidence *= vol_score
        
        # Volatility adjustment
        confidence *= vol_regime_mult
        
        # Confidence thresholding
        if confidence < 0.15:
            direction = 0
            quality_band = "red"
        elif confidence < 0.35:
            quality_band = "amber"
        else:
            quality_band = "green"
        
        exit_confidence = confidence * 0.9  # Slightly lower exit bar
        
        return ImprovedPASignal(
            symbol=symbol,
            timestamp=str(bars.iloc[-1].get("timestamp", "")),
            confidence=float(np.clip(confidence, 0.0, 1.0)),
            direction=int(direction),
            quality_band=quality_band,
            momentum=0.0,  # Disabled
            volatility=float(volatility),
            exit_confidence=float(np.clip(exit_confidence, 0.0, 1.0)),
            regime=regime,
            mean_reversion_score=float(mean_rev_score),
            volume_score=float(vol_score),
            trend_strength=float(trend_strength)
        ), []


# Test the improved algorithm
if __name__ == "__main__":
    import pandas as pd
    from market_data_loader import SingleSymbolReplayFeed
    from revision2.dataset_manifest import DatasetManifest, verify_manifest
    from pathlib import Path
    
    ROOT = Path('/home/shrinivas/ECS_Project')
    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
    verify_manifest(manifest)
    
    feed = SingleSymbolReplayFeed("MARUTI", data_dir=manifest.data_dir, max_bars=None)
    maruti_data = feed.load()
    maruti_data["timestamp"] = pd.to_datetime(maruti_data["timestamp"])
    maruti_data = maruti_data[(maruti_data["timestamp"] >= "2023-09-01") & (maruti_data["timestamp"] < "2023-09-02")]
    
    print("IMPROVED PA ALGORITHM TEST")
    print("="*80)
    print("Mean reversion + regime detection + volume analysis")
    print("Momentum component DISABLED (was anti-predictive at 46.7% accuracy)")
    print()
    
    pa = ImprovedPredictiveAnalyticsBox()
    warmup = maruti_data.iloc[:60]
    pa.calibrate("MARUTI", warmup)
    
    # Test a few bars
    regimes = []
    mean_revs = []
    confidences = []
    
    for i in range(65, min(100, len(maruti_data))):
        snapshot_bars = maruti_data.iloc[max(0, i-50):i+1]
        
        class Snapshot:
            def __init__(self, symbol, bars):
                self.symbol = symbol
                self.bars = bars
        
        snapshot = Snapshot("MARUTI", snapshot_bars)
        signal, _ = pa.evaluate(snapshot, None)
        
        regimes.append(signal.regime)
        mean_revs.append(signal.mean_reversion_score)
        confidences.append(signal.confidence)
        
        if i <= 70 or signal.confidence > 0.3:
            print(f"Bar {i}: Regime={signal.regime:10} MeanRev={signal.mean_reversion_score:.3f} Conf={signal.confidence:.3f} Dir={signal.direction}")
    
    print()
    print(f"Regime distribution: Bullish={regimes.count('bullish')}, Bearish={regimes.count('bearish')}, Sideways={regimes.count('sideways')}")

