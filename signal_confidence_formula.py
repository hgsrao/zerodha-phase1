#!/usr/bin/env python3
"""Real signal confidence formula - transparent multi-factor model
Confidence = 0.35*momentum + 0.35*trend + 0.20*volume + 0.10*volatility
All bounded [0,1], calculated each bar, entry only if confidence >= 0.55"""

import pandas as pd
import numpy as np
from typing import Dict, Optional


class RealSignalConfidence:
    """Calculate confidence for entry signal using frozen formula"""

    def __init__(self, momentum_weight=0.35, trend_weight=0.35,
                 volume_weight=0.20, volatility_weight=0.10,
                 admission_threshold=0.55):
        """
        Initialize with frozen weights.

        Weights must sum to 1.0 and represent:
        - momentum_weight: Recent return relative to volatility (0-1)
        - trend_weight: MA trend strength and direction (0-1)
        - volume_weight: Current volume vs recent average (0-1)
        - volatility_weight: Tradability of current regime (0-1)
        - admission_threshold: Minimum confidence to enter (0-1)
        """
        total_weight = momentum_weight + trend_weight + volume_weight + volatility_weight
        assert abs(total_weight - 1.0) < 0.001, f"Weights must sum to 1.0, got {total_weight}"

        self.momentum_weight = momentum_weight
        self.trend_weight = trend_weight
        self.volume_weight = volume_weight
        self.volatility_weight = volatility_weight
        self.admission_threshold = admission_threshold

    def momentum_score(self, df: pd.DataFrame, lookback=14) -> float:
        """
        Momentum: recent return relative to ATR
        Measures how much price has moved normalized by volatility.
        Score: (current_close - sma(lookback)) / ATR(lookback), bounded [0,1]

        Returns 0.0 if below SMA, up to 1.0 if far above
        """
        if len(df) < lookback:
            return 0.0

        close = df.iloc[-1]['close']
        sma = df['close'].tail(lookback).mean()
        atr = self._atr(df, lookback)

        if atr == 0:
            return 0.0

        # Return relative to ATR: a 1-ATR move is moderate, 2+ is strong
        raw_score = (close - sma) / atr
        # Bound to [0, 1]: 0 at or below SMA, 1 at +2*ATR above
        return max(0.0, min(1.0, raw_score / 2.0))

    def trend_score(self, df: pd.DataFrame, fast=9, slow=21) -> float:
        """
        Trend: fast/slow moving average relationship
        Strong uptrend: fast MA well above slow MA
        Score: (fast_ma - slow_ma) / slow_ma, bounded [0,1]

        Returns 0.0 if fast < slow, up to 1.0 if fast significantly above slow
        """
        if len(df) < slow:
            return 0.0

        fast_ma = df['close'].tail(fast).mean()
        slow_ma = df['close'].tail(slow).mean()

        if slow_ma == 0:
            return 0.0

        # Spread relative to slow MA: 5% spread is strong
        raw_score = (fast_ma - slow_ma) / slow_ma
        # Bound to [0, 1]: 0 at or below parity, 1 at +5% spread
        return max(0.0, min(1.0, raw_score / 0.05))

    def volume_score(self, df: pd.DataFrame, lookback=14) -> float:
        """
        Volume confirmation: current volume vs recent average
        High volume supports momentum; low volume is skeptical.
        Score: current_vol / avg_vol, bounded [0,1]

        Returns 0.5 if at average, up to 1.0 if 2x average, down to 0 if <50% average
        """
        if len(df) < lookback:
            return 0.5

        current_vol = df.iloc[-1]['volume']
        avg_vol = df['volume'].tail(lookback).mean()

        if avg_vol == 0:
            return 0.5

        # Volume ratio: 1.0x = 0.5 score, 2.0x = 1.0 score, 0.5x = 0.0 score
        ratio = current_vol / avg_vol
        return max(0.0, min(1.0, (ratio - 0.5) / 1.5))

    def volatility_score(self, df: pd.DataFrame, lookback=14) -> float:
        """
        Volatility suitability: normal volatility preferred, extremes penalized
        Too low volatility = hard to move; too high = hard to control
        Score: centered at 1.5-2.0% ATR/close, falling away from optimum

        Returns 1.0 at optimal vol, down to 0.0 at extremes
        """
        if len(df) < lookback:
            return 0.5

        atr = self._atr(df, lookback)
        close = df.iloc[-1]['close']

        if close == 0:
            return 0.5

        # ATR as % of close: optimal is 1.0-2.0%
        atr_pct = (atr / close) * 100

        # Gaussian-like penalty away from 1.5% optimum
        # 1.0% = 0.8, 1.5% = 1.0, 2.5% = 0.7, 5% = 0.1
        diff = abs(atr_pct - 1.5)
        return max(0.0, 1.0 - (diff / 4.0))

    def _atr(self, df: pd.DataFrame, lookback: int) -> float:
        """Calculate Average True Range"""
        if len(df) < lookback:
            return 0.0

        high = df['high'].values
        low = df['low'].values
        close = df['close'].values

        tr = np.maximum(
            high[-lookback:] - low[-lookback:],
            np.maximum(
                abs(high[-lookback:] - close[-lookback-1:-1]),
                abs(low[-lookback:] - close[-lookback-1:-1])
            )
        )
        return tr.mean()

    def calculate(self, df: pd.DataFrame) -> float:
        """
        Calculate confidence for current bar (last row of df)

        Returns: float [0.0, 1.0] - confidence score
        """
        if len(df) < 21:  # Need enough history
            return 0.0

        momentum = self.momentum_score(df)
        trend = self.trend_score(df)
        volume = self.volume_score(df)
        volatility = self.volatility_score(df)

        # Weighted sum
        confidence = (
            self.momentum_weight * momentum +
            self.trend_weight * trend +
            self.volume_weight * volume +
            self.volatility_weight * volatility
        )

        return float(np.clip(confidence, 0.0, 1.0))

    def should_enter(self, df: pd.DataFrame) -> bool:
        """
        Entry decision: confidence >= admission_threshold

        Returns: True if signal qualifies for gate evaluation
        """
        confidence = self.calculate(df)
        return confidence >= self.admission_threshold

    def get_components(self, df: pd.DataFrame) -> Dict[str, float]:
        """Return all components for debugging"""
        if len(df) < 21:
            return {
                'momentum': 0.0, 'trend': 0.0, 'volume': 0.0, 'volatility': 0.0,
                'confidence': 0.0, 'qualifies': False
            }

        momentum = self.momentum_score(df)
        trend = self.trend_score(df)
        volume = self.volume_score(df)
        volatility = self.volatility_score(df)

        confidence = (
            self.momentum_weight * momentum +
            self.trend_weight * trend +
            self.volume_weight * volume +
            self.volatility_weight * volatility
        )
        confidence = float(np.clip(confidence, 0.0, 1.0))

        return {
            'momentum': float(momentum),
            'trend': float(trend),
            'volume': float(volume),
            'volatility': float(volatility),
            'confidence': confidence,
            'qualifies': confidence >= self.admission_threshold,
            'threshold': self.admission_threshold
        }


if __name__ == "__main__":
    # Example usage
    print("[OK] Real signal confidence formula loaded")
    print("[OK] Formula: 0.35*momentum + 0.35*trend + 0.20*volume + 0.10*volatility")
    print("[OK] Entry threshold: confidence >= 0.55")
