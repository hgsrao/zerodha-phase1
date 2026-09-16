#!/usr/bin/env python3
"""
Label Regeneration for In-House Engine with Strict Causal Features
===================================================================

The original refined labels (629 positives) were generated ON TOP OF external engine
features that had causality/VWAP issues. The in-house engine features are
causally-clean, creating a domain mismatch.

This script regenerates cost-aware labels using ACTUAL in-house feature characteristics.

Strategy:
1. Analyze forward returns stratified by in-house features
2. Identify which FEATURE CONDITIONS correlate with positive returns
3. Generate labels based on in-house feature patterns (not external labels)
4. Preserve cost-awareness (18 bps min profit after 8 bps trading costs)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import json
import logging
from datetime import datetime
from typing import Dict, Tuple, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('regenerate_labels_inhouse_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class InHouseLabelGenerator:
    """Generate labels using in-house feature characteristics."""

    FEATURE_COLUMNS = [
        '5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
        '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
        '15m_rs_percentile', '15m_rs_excess'
    ]

    def __init__(self,
                 feature_dir: str = "revision2/features_mtf_2026",
                 output_dir: str = "/home/shrinivas/ECS_Project_external_engine/revision2/labels_inhouse_2026"):
        """Initialize generator."""
        self.feature_dir = Path(feature_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("✅ InHouseLabelGenerator initialized")
        logger.info(f"   Features: {self.feature_dir}")
        logger.info(f"   Output labels: {self.output_dir}")

    def load_split_with_returns(self, split_name: str = "train") -> Tuple[pd.DataFrame, pd.Series]:
        """
        Load in-house features and calculate forward returns.

        For TRAIN split: use actual extracted features from revision2/features_mtf_2026/
        """
        logger.info(f"\nLoading {split_name} split (in-house features)...")

        # For now, only TRAIN is extracted in-house
        if split_name != "train":
            logger.warning(f"  In-house extraction only available for TRAIN")
            logger.warning(f"  For {split_name}, use external engine features")
            return None, None

        # Load all symbols
        X_list = []
        close_list = []

        for parquet_file in sorted(self.feature_dir.glob("*_mtf_2026.parquet")):
            df = pd.read_parquet(parquet_file)

            # Features
            available_cols = [col for col in self.FEATURE_COLUMNS if col in df.columns]
            X = df[available_cols].copy()
            X_list.append(X)

            # Close prices for return calculation
            close_list.append(df[['close']].copy())

        X_combined = pd.concat(X_list, axis=0, ignore_index=False).reset_index(drop=True)
        close_combined = pd.concat(close_list, axis=0, ignore_index=False).reset_index(drop=True)

        logger.info(f"  ✅ Loaded features: {X_combined.shape}")
        logger.info(f"  ✅ Loaded close prices: {close_combined.shape}")

        # Calculate forward returns (20 periods = ~20 minutes)
        future_periods = 20
        forward_close = close_combined['close'].shift(-future_periods)
        forward_return = (forward_close - close_combined['close']) / close_combined['close']
        forward_return_bps = forward_return * 10000

        logger.info(f"  ✅ Forward returns: min={forward_return_bps.min():.2f} bps, "
                   f"max={forward_return_bps.max():.2f} bps, mean={forward_return_bps.mean():.2f} bps")

        return X_combined, forward_return_bps

    def analyze_feature_return_correlation(self, X: pd.DataFrame, y_returns: pd.Series) -> Dict:
        """
        Analyze which in-house features correlate with positive returns.

        This gives us insight into feature patterns that predict success.
        """
        logger.info("\n" + "="*80)
        logger.info("FEATURE-RETURN CORRELATION ANALYSIS")
        logger.info("="*80)

        # Minimum profit after costs
        min_profit_threshold = 18  # 8 bps cost + 10 bps profit

        # Identify winning samples
        profitable = y_returns >= min_profit_threshold
        logger.info(f"\nProfitable samples (>= {min_profit_threshold} bps): {profitable.sum()}/{len(profitable)} ({profitable.mean()*100:.2f}%)")

        correlation_analysis = {}

        for col in self.FEATURE_COLUMNS:
            valid_mask = X[col].notna() & y_returns.notna()

            if valid_mask.sum() == 0:
                continue

            x_valid = X.loc[valid_mask, col]
            y_valid = y_returns[valid_mask]
            prof_valid = profitable[valid_mask]

            # Correlation with returns
            corr = x_valid.corr(y_valid)

            # Average return for high vs low values of this feature
            median_val = x_valid.median()
            high_group = y_valid[x_valid > median_val]
            low_group = y_valid[x_valid <= median_val]

            avg_return_high = high_group.mean()
            avg_return_low = low_group.mean()

            # Win rate for high vs low
            prof_high = prof_valid[x_valid > median_val].mean() * 100
            prof_low = prof_valid[x_valid <= median_val].mean() * 100

            correlation_analysis[col] = {
                'correlation_with_return': float(corr),
                'avg_return_high_values': float(avg_return_high),
                'avg_return_low_values': float(avg_return_low),
                'profitable_win_rate_high': float(prof_high),
                'profitable_win_rate_low': float(prof_low),
                'difference_high_vs_low': float(avg_return_high - avg_return_low)
            }

            logger.info(f"\n{col}:")
            logger.info(f"  Correlation with return: {corr:.4f}")
            logger.info(f"  High values: avg return={avg_return_high:.2f} bps, win rate={prof_high:.1f}%")
            logger.info(f"  Low values: avg return={avg_return_low:.2f} bps, win rate={prof_low:.1f}%")
            logger.info(f"  Difference: {avg_return_high - avg_return_low:.2f} bps")

        return correlation_analysis

    def generate_inhouse_aware_labels(self, X: pd.DataFrame, y_returns: pd.Series) -> Tuple[pd.Series, Dict]:
        """
        Generate labels based on in-house feature patterns.

        Strategy: Use a more generous threshold to get meaningful positive rate,
        while still filtering for cost-viable trades.
        """
        logger.info("\n" + "="*80)
        logger.info("LABEL GENERATION: IN-HOUSE FEATURE-AWARE")
        logger.info("="*80)

        # Profit threshold after costs
        min_profit_threshold = 18  # 8 bps cost + 10 bps min profit

        # Base rule: simple return threshold with forward-fill handling
        simple_labels = (y_returns >= min_profit_threshold).astype(int)

        logger.info(f"\nSimple approach (return >= {min_profit_threshold} bps):")
        logger.info(f"  Positive labels: {simple_labels.sum()}/{len(simple_labels)} ({simple_labels.mean()*100:.3f}%)")

        # Alternative: Multi-factor approach using feature patterns
        # High efficiency + positive trend + not extreme volatility + good relative strength
        multi_factor_labels = (
            (X['5m_efficiency'] > 0.3) &  # Above-median efficiency
            (X['5m_trend'] > 0) &  # Positive short-term trend
            (X['15m_trend'] > 0) &  # Positive medium-term trend
            (X['15m_realized_vol'] < X['15m_realized_vol'].quantile(0.75)) &  # Not in top 25% vol
            (X['15m_rs_percentile'] > 0.5) &  # Stronger than median in cross-section
            (y_returns >= min_profit_threshold)  # And actually profitable
        ).astype(int)

        logger.info(f"\nMulti-factor approach (efficiency + trend + vol + relative strength):")
        logger.info(f"  Positive labels: {multi_factor_labels.sum()}/{len(multi_factor_labels)} ({multi_factor_labels.mean()*100:.3f}%)")

        # Conservative approach: ONLY forward returns that survive costs
        # No feature conditions - pure return-based
        conservative_labels = simple_labels

        stats = {
            "total_samples": len(y_returns),
            "simple_positive": int(simple_labels.sum()),
            "simple_positive_pct": float(simple_labels.mean() * 100),
            "multi_factor_positive": int(multi_factor_labels.sum()),
            "multi_factor_positive_pct": float(multi_factor_labels.mean() * 100),
            "profit_threshold_bps": min_profit_threshold,
            "avg_forward_return_all": float(y_returns.mean()),
            "avg_forward_return_positive": float(y_returns[simple_labels == 1].mean()) if simple_labels.sum() > 0 else 0,
            "generated_at": datetime.utcnow().isoformat()
        }

        logger.info(f"\nLabel Statistics:")
        logger.info(f"  Simple labels: {stats['simple_positive']:,} positive ({stats['simple_positive_pct']:.3f}%)")
        logger.info(f"  Multi-factor: {stats['multi_factor_positive']:,} positive ({stats['multi_factor_positive_pct']:.3f}%)")
        logger.info(f"  Avg return (all): {stats['avg_forward_return_all']:.2f} bps")
        logger.info(f"  Avg return (positive labels): {stats['avg_forward_return_positive']:.2f} bps")

        # Use simple labels (return-based only, no feature over-fitting)
        return simple_labels, stats

    def run_regeneration(self) -> Dict:
        """Execute full label regeneration."""
        logger.info("\n" + "="*80)
        logger.info("IN-HOUSE ENGINE LABEL REGENERATION (TRAIN SPLIT)")
        logger.info("="*80 + "\n")

        # Load data
        X_train, y_returns = self.load_split_with_returns("train")
        if X_train is None:
            raise ValueError("Failed to load TRAIN split")

        # Analyze feature-return correlations
        corr_analysis = self.analyze_feature_return_correlation(X_train, y_returns)

        # Generate new labels
        labels, stats = self.generate_inhouse_aware_labels(X_train, y_returns)

        # Save labels
        logger.info(f"\nSaving labels to {self.output_dir}/...")
        labels_path = self.output_dir / "train_labels_inhouse.parquet"
        labels.to_frame(name="label").to_parquet(labels_path)
        logger.info(f"  ✅ Saved: {labels_path}")

        # Save metadata
        metadata_path = self.output_dir / "label_generation_metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump({
                "stats": stats,
                "correlation_analysis": corr_analysis,
                "approach": "return-based with cost-awareness",
                "profit_threshold_bps": 18,
                "future_periods": 20,
                "generated_at": datetime.utcnow().isoformat()
            }, f, indent=2)
        logger.info(f"  ✅ Saved metadata: {metadata_path}")

        results = {
            "labels": labels,
            "stats": stats,
            "correlation_analysis": corr_analysis
        }

        return results


if __name__ == "__main__":
    generator = InHouseLabelGenerator()
    results = generator.run_regeneration()

    print("\n" + "="*80)
    print("✅ IN-HOUSE ENGINE LABEL REGENERATION COMPLETE")
    print("="*80)
    print(f"\nNew Labels Generated:")
    print(f"  Total: {results['stats']['total_samples']:,} samples")
    print(f"  Positive: {results['stats']['simple_positive']:,} ({results['stats']['simple_positive_pct']:.3f}%)")
    print(f"  Saved to: {Path('revision2/labels_inhouse_2026/train_labels_inhouse.parquet').resolve()}")
    print(f"\nNext Step: Retrain model with in-house labels on in-house features")
