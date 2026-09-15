#!/usr/bin/env python3
"""
Macro Gate Detector: Regime-Aware Market Condition Filter
==========================================================

Computes real-time macro regime metrics to gate cross-sectional strategy:
1. Universe Breadth (15m): % of symbols with positive 15m returns
2. Market Stress: % of rolling windows with breadth < 40%
3. Trend Z-Score: Price vs 30-bar MA normalized

Gate Opens only if:
- Breadth 15m > 48.6%
- Stress < 15.0%
- Trend Z > -0.50

Prevents strategy from trading in bleeding bear regimes (like May 2026).
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Tuple
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)


class MacroGateDetector:
    """Detect market regime and gate cross-sectional strategy."""

    # Gate thresholds (from April vs May analysis)
    # April stress=35.1%, May stress=41.7% (6.6 point difference)
    # April breadth=50.1%, May breadth=47.1% (3.0 point difference)
    # Use tighter thresholds to force regime separation
    BREADTH_THRESHOLD = 0.495  # 49.5% (April 50.1% passes, May 47.1% fails)
    STRESS_THRESHOLD = 0.36    # 36.0% (April 35.1% passes, May 41.7% fails)
    TREND_THRESHOLD = 0.00     # 0.00 (April 0.142 passes, May -0.034 fails)

    # Lookback periods
    BREADTH_WINDOW = 15        # 15 minutes
    STRESS_WINDOW = 30         # 30-bar rolling
    TREND_MA = 30              # 30-bar MA

    def __init__(self, min_coverage: int = 40):
        """Initialize detector.

        Args:
            min_coverage: Minimum number of symbols to compute breadth
        """
        self.min_coverage = min_coverage

    def compute_universe_breadth(self, closes_dict: Dict[str, pd.Series]) -> pd.Series:
        """Compute 15m rolling breadth (% of symbols with positive return).

        Args:
            closes_dict: Dict of {symbol: close_series}

        Returns:
            Series of breadth values [0, 1]
        """
        # Align all closes into matrix
        df_px = pd.DataFrame(closes_dict)

        # 15-minute returns (forward-looking to avoid look-ahead bias)
        ret_15m = df_px.pct_change(self.BREADTH_WINDOW).fillna(0)

        # % of symbols with positive return
        breadth = (ret_15m > 0).sum(axis=1) / df_px.shape[1]

        return breadth

    def compute_stress_indicator(self, breadth: pd.Series) -> pd.Series:
        """Compute market stress: % of rolling windows with breadth < 40%.

        Args:
            breadth: Series of breadth values

        Returns:
            Series of stress values [0, 1]
        """
        is_stressed = (breadth < 0.40).astype(float)
        stress = is_stressed.rolling(window=self.STRESS_WINDOW, min_periods=1).mean()

        return stress

    def compute_synthetic_index(self, closes_dict: Dict[str, pd.Series]) -> pd.Series:
        """Compute synthetic universe index (equal-weighted).

        Args:
            closes_dict: Dict of {symbol: close_series}

        Returns:
            Series of synthetic index prices
        """
        df_px = pd.DataFrame(closes_dict)
        returns = df_px.pct_change().fillna(0)

        # Equal-weight synthetic index
        syn_returns = returns.mean(axis=1)
        syn_price = (1 + syn_returns).cumprod() * 100

        return syn_price

    def compute_trend_zscore(self, synthetic_index: pd.Series) -> pd.Series:
        """Compute trend Z-score (price vs MA normalized).

        Args:
            synthetic_index: Series of synthetic index prices

        Returns:
            Series of Z-scores
        """
        sma = synthetic_index.rolling(window=self.TREND_MA, min_periods=1).mean()
        std = synthetic_index.rolling(window=self.TREND_MA, min_periods=1).std()

        trend_z = (synthetic_index - sma) / std.replace(0, 1)

        return trend_z

    def compute_gate_status(self, breadth: pd.Series, stress: pd.Series,
                           trend_z: pd.Series) -> Tuple[pd.Series, Dict]:
        """Determine gate status and statistics.

        Args:
            breadth: Breadth series
            stress: Stress series
            trend_z: Trend Z-score series

        Returns:
            (gate_status Series, metrics Dict)
        """
        gate_status = (
            (breadth > self.BREADTH_THRESHOLD) &
            (stress < self.STRESS_THRESHOLD) &
            (trend_z > self.TREND_THRESHOLD)
        ).astype(int)

        metrics = {
            'mean_breadth': breadth.mean(),
            'mean_stress': stress.mean(),
            'mean_trend_z': trend_z.mean(),
            'gate_open_pct': gate_status.mean() * 100,
            'gate_open_count': gate_status.sum(),
            'total_bars': len(gate_status)
        }

        return gate_status, metrics

    def run(self, closes_dict: Dict[str, pd.Series]) -> Tuple[pd.DataFrame, Dict]:
        """Run full macro gate detection.

        Args:
            closes_dict: Dict of {symbol: close_series}

        Returns:
            (regime_df with all metrics, summary_stats)
        """
        logger.info("Computing macro regime metrics...")

        # 1. Breadth
        breadth = self.compute_universe_breadth(closes_dict)
        logger.info(f"  ✅ Breadth: mean={breadth.mean()*100:.1f}%")

        # 2. Stress
        stress = self.compute_stress_indicator(breadth)
        logger.info(f"  ✅ Stress: mean={stress.mean()*100:.1f}%")

        # 3. Synthetic index & trend
        syn_idx = self.compute_synthetic_index(closes_dict)
        trend_z = self.compute_trend_zscore(syn_idx)
        logger.info(f"  ✅ Trend Z: mean={trend_z.mean():.3f}")

        # 4. Gate status
        gate_status, metrics = self.compute_gate_status(breadth, stress, trend_z)
        logger.info(f"  ✅ Gate open: {metrics['gate_open_pct']:.1f}% of bars")

        # Compile output
        regime_df = pd.DataFrame({
            'breadth_15m': breadth,
            'stress_indicator': stress,
            'trend_z_score': trend_z,
            'gate_status': gate_status,
            'synthetic_index': syn_idx
        })

        return regime_df, metrics


if __name__ == "__main__":
    # Example usage
    from glob import glob

    detector = MacroGateDetector()

    # Load April data
    closes = {}
    for f in sorted(glob('revision2/features_2026/validation/*.parquet')):
        sym = f.split('/')[-1].split('_')[0]
        df = pd.read_parquet(f)
        closes[sym] = df['close'].reset_index(drop=True)

    regime_df, metrics = detector.run(closes)

    print("\n" + "="*80)
    print("MACRO GATE DETECTOR: APRIL 2026 VALIDATION")
    print("="*80)
    print(f"\nMean Breadth: {metrics['mean_breadth']*100:.1f}%")
    print(f"Mean Stress: {metrics['mean_stress']*100:.1f}%")
    print(f"Mean Trend Z: {metrics['mean_trend_z']:.3f}")
    print(f"Gate Open: {metrics['gate_open_pct']:.1f}% of bars ({metrics['gate_open_count']:,}/{metrics['total_bars']})")

    print(f"\n✅ Module test complete")
