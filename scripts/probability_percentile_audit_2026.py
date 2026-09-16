#!/usr/bin/env python3
"""
Probability Percentile Audit for Extreme-Imbalance Financial Data
===================================================================

For extreme label imbalance (0.058% positive rate), the 0.5 classification threshold
is mathematically useless. This audit breaks down predicted probabilities by percentile,
revealing where the actual discriminatory power lives.

Strategy:
1. Load trained model and TRAIN split data
2. Generate predicted probabilities (not class labels)
3. Rank-order and compute percentiles
4. For each bucket, measure the actual positive rate (target-first rate)
5. Identify the probability threshold where calibration makes sense
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import json
import logging
import pickle
from typing import Dict, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('probability_percentile_audit_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ProbabilityPercentileAudit:
    """Audit predicted probabilities for extreme-imbalance classification."""

    FEATURE_COLUMNS = [
        '5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
        '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
        '15m_rs_percentile', '15m_rs_excess'
    ]

    def __init__(self,
                 feature_dir: str = "revision2/features_mtf_2026",
                 model_path: str = "revision2/frozen_models_inhouse_2026/logistic_regression_inhouse_refined.pkl",
                 scaler_path: str = "revision2/frozen_models_inhouse_2026/feature_scaler.pkl",
                 label_dir: str = "/home/shrinivas/ECS_ModelDevelopment_2026_MTF/labels_refined_2026"):
        """Initialize auditor."""
        self.feature_dir = Path(feature_dir)
        self.model_path = Path(model_path)
        self.scaler_path = Path(scaler_path)
        self.label_dir = Path(label_dir)

        # Load model and scaler
        with open(self.model_path, 'rb') as f:
            self.model = pickle.load(f)
        with open(self.scaler_path, 'rb') as f:
            self.scaler = pickle.load(f)

        logger.info("✅ ProbabilityPercentileAudit initialized")
        logger.info(f"   Model: {self.model_path}")
        logger.info(f"   Scaler: {self.scaler_path}")

    def load_train_data(self) -> Tuple[pd.DataFrame, pd.Series]:
        """Load TRAIN split features and labels."""
        logger.info("\nLoading TRAIN split...")

        # Features
        X_list = []
        for parquet_file in sorted(self.feature_dir.glob("*_mtf_2026.parquet")):
            df = pd.read_parquet(parquet_file)
            available_cols = [col for col in self.FEATURE_COLUMNS if col in df.columns]
            X = df[available_cols].copy()
            X_list.append(X)

        X_combined = pd.concat(X_list, axis=0, ignore_index=False).reset_index(drop=True)
        logger.info(f"  ✅ Loaded features: {X_combined.shape}")

        # Labels
        labels_path = self.label_dir / "train_labels.parquet"
        y = pd.read_parquet(labels_path)['label'].reset_index(drop=True)

        logger.info(f"  ✅ Loaded labels: {y.shape}")
        logger.info(f"     Positive: {y.sum()}/{len(y)} ({y.mean()*100:.3f}%)")

        return X_combined, y

    def generate_probabilities(self, X: pd.DataFrame) -> np.ndarray:
        """Generate predicted probabilities."""
        logger.info("\nGenerating predicted probabilities...")

        # Remove NaN rows
        valid_mask = X.notna().all(axis=1)
        X_clean = X[valid_mask]

        logger.info(f"  Data: {X_clean.shape} ({(~valid_mask).sum()} NaN rows removed)")

        # Scale
        X_scaled = self.scaler.transform(X_clean)

        # Predict probabilities (probability of positive class)
        y_proba = self.model.predict_proba(X_scaled)[:, 1]

        logger.info(f"  ✅ Probabilities: min={y_proba.min():.6f}, max={y_proba.max():.6f}, mean={y_proba.mean():.6f}")

        return y_proba, valid_mask

    def percentile_breakdown(self, y_proba: np.ndarray, y_true: pd.Series, valid_mask: np.ndarray) -> Dict:
        """
        Break down predicted probabilities by percentile.

        Compute target-first rate (actual positive rate) in each bucket.
        """
        logger.info("\n" + "="*80)
        logger.info("PERCENTILE BREAKDOWN (Target-First Rates by Probability Bucket)")
        logger.info("="*80)

        # Align true labels with probabilities
        y_true_aligned = y_true[valid_mask].values

        # Create result dataframe
        results_df = pd.DataFrame({
            'probability': y_proba,
            'label': y_true_aligned
        })

        # Define percentile buckets (deciles + additional granular cuts)
        percentile_edges = [
            0, 10, 20, 30, 40, 50, 60, 70, 80, 90,  # Deciles
            95, 99, 99.5, 99.9, 99.95, 99.99, 100   # Granular top buckets
        ]

        breakdown = []

        logger.info(f"\n{'Percentile':20s} {'Min Prob':12s} {'Max Prob':12s} {'N Samples':12s} "
                   f"{'Positives':12s} {'Target Rate':15s} {'Lift vs Base':15s}")
        logger.info("-" * 110)

        base_rate = y_true_aligned.mean()

        for i in range(len(percentile_edges) - 1):
            p_low = percentile_edges[i]
            p_high = percentile_edges[i + 1]

            # Find data in this percentile range
            if p_low == 0:
                mask = results_df['probability'] <= results_df['probability'].quantile(p_high / 100)
            elif p_high == 100:
                mask = results_df['probability'] > results_df['probability'].quantile(p_low / 100)
            else:
                q_low = results_df['probability'].quantile(p_low / 100)
                q_high = results_df['probability'].quantile(p_high / 100)
                mask = (results_df['probability'] > q_low) & (results_df['probability'] <= q_high)

            bucket = results_df[mask]
            n_samples = len(bucket)
            n_positives = bucket['label'].sum()
            target_rate = bucket['label'].mean() if n_samples > 0 else 0
            lift = target_rate / base_rate if base_rate > 0 else 0

            percentile_label = f"{p_low}-{p_high}%ile"
            min_prob = bucket['probability'].min() if n_samples > 0 else 0
            max_prob = bucket['probability'].max() if n_samples > 0 else 0

            logger.info(f"{percentile_label:20s} {min_prob:12.6f} {max_prob:12.6f} {n_samples:12d} "
                       f"{n_positives:12d} {target_rate*100:14.2f}% {lift:14.2f}x")

            breakdown.append({
                'percentile_low': p_low,
                'percentile_high': p_high,
                'min_probability': float(min_prob),
                'max_probability': float(max_prob),
                'n_samples': int(n_samples),
                'n_positives': int(n_positives),
                'target_rate': float(target_rate),
                'lift_vs_base': float(lift),
                'percentile_label': percentile_label
            })

        return breakdown, base_rate

    def top_bucket_deep_dive(self, y_proba: np.ndarray, y_true: pd.Series, valid_mask: np.ndarray):
        """Deep dive into top probability buckets."""
        logger.info("\n" + "="*80)
        logger.info("TOP BUCKET DEEP DIVE (Top 0.1%, 0.5%, 1% by Probability)")
        logger.info("="*80)

        y_true_aligned = y_true[valid_mask].values
        base_rate = y_true_aligned.mean()

        results_df = pd.DataFrame({
            'probability': y_proba,
            'label': y_true_aligned
        })

        top_cuts = [0.1, 0.5, 1.0, 5.0, 10.0]

        logger.info(f"\n{'Top %':15s} {'Prob Threshold':20s} {'N Samples':15s} {'Positives':15s} "
                   f"{'Target Rate':20s} {'Lift':20s}")
        logger.info("-" * 110)

        top_results = []

        for cut_pct in top_cuts:
            threshold_quantile = (100 - cut_pct) / 100
            threshold_prob = results_df['probability'].quantile(threshold_quantile)

            top_bucket = results_df[results_df['probability'] >= threshold_prob]
            n_samples = len(top_bucket)
            n_positives = top_bucket['label'].sum()
            target_rate = top_bucket['label'].mean()
            lift = target_rate / base_rate if base_rate > 0 else 0

            logger.info(f"{cut_pct:14.1f}% {threshold_prob:19.6f} {n_samples:15d} {n_positives:15d} "
                       f"{target_rate*100:19.2f}% {lift:19.2f}x")

            top_results.append({
                'top_pct': cut_pct,
                'probability_threshold': float(threshold_prob),
                'n_samples': int(n_samples),
                'n_positives': int(n_positives),
                'target_rate': float(target_rate),
                'lift': float(lift)
            })

        return top_results, base_rate

    def cumulative_gain_chart(self, y_proba: np.ndarray, y_true: pd.Series, valid_mask: np.ndarray):
        """Compute cumulative gain for lift chart."""
        logger.info("\n" + "="*80)
        logger.info("CUMULATIVE GAIN ANALYSIS")
        logger.info("="*80)

        y_true_aligned = y_true[valid_mask].values
        base_rate = y_true_aligned.mean()

        # Sort by probability descending
        sorted_idx = np.argsort(-y_proba)
        sorted_labels = y_true_aligned[sorted_idx]

        # Compute cumulative positive rate
        n_total = len(sorted_labels)
        n_positives_total = sorted_labels.sum()

        cumulative_positives = np.cumsum(sorted_labels)
        sample_pcts = np.arange(1, n_total + 1) / n_total * 100
        positive_pcts = cumulative_positives / n_positives_total * 100

        # Find key points
        sample_pct_targets = [1, 5, 10, 25, 50, 100]
        logger.info(f"\n{'% of Population':20s} {'% of Positives Captured':25s} {'Lift':15s}")
        logger.info("-" * 60)

        for target_pct in sample_pct_targets:
            idx = min(int(target_pct / 100 * n_total) - 1, n_total - 1)
            pos_pct = positive_pcts[idx]
            lift = pos_pct / target_pct if target_pct > 0 else 0

            logger.info(f"{target_pct:19.1f}% {pos_pct:24.1f}% {lift:14.2f}x")

        return sample_pcts, positive_pcts

    def run_audit(self) -> Dict:
        """Execute full probability audit."""
        logger.info("\n" + "="*80)
        logger.info("PROBABILITY PERCENTILE AUDIT - TRAIN SPLIT")
        logger.info("="*80 + "\n")

        # Load data
        X_train, y_train = self.load_train_data()

        # Generate probabilities
        y_proba, valid_mask = self.generate_probabilities(X_train)

        # Percentile breakdown
        breakdown, base_rate = self.percentile_breakdown(y_proba, y_train, valid_mask)

        # Top bucket deep dive
        top_results, _ = self.top_bucket_deep_dive(y_proba, y_train, valid_mask)

        # Cumulative gain
        sample_pcts, positive_pcts = self.cumulative_gain_chart(y_proba, y_train, valid_mask)

        # Summary
        logger.info("\n" + "="*80)
        logger.info("AUDIT SUMMARY")
        logger.info("="*80)
        logger.info(f"\nBase Rate (TRAIN): {base_rate*100:.3f}%")
        logger.info(f"Model AUC: 0.8854 (from training)")
        logger.info(f"\nKey Finding: Top 1% of predictions have {top_results[2]['target_rate']*100:.2f}% positive rate")
        logger.info(f"             Lift vs base: {top_results[2]['lift']:.2f}x")
        logger.info(f"\nTop 0.1% lift:   {top_results[0]['lift']:.2f}x")
        logger.info(f"Top 0.5% lift:   {top_results[1]['lift']:.2f}x")
        logger.info(f"Top 1.0% lift:   {top_results[2]['lift']:.2f}x")

        results = {
            "base_rate": float(base_rate),
            "percentile_breakdown": breakdown,
            "top_bucket_analysis": top_results,
            "cumulative_gain": {
                "sample_percentiles": sample_pcts.tolist(),
                "positive_percentiles": positive_pcts.tolist()
            }
        }

        # Save results
        results_path = Path("probability_percentile_audit_results.json")
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"\n✅ Results saved: {results_path}")

        return results


if __name__ == "__main__":
    auditor = ProbabilityPercentileAudit()
    results = auditor.run_audit()

    print("\n" + "="*80)
    print("✅ PROBABILITY PERCENTILE AUDIT COMPLETE")
    print("="*80)
    print(f"\nKey Insight:")
    print(f"  If top 1% bucket achieves {results['top_bucket_analysis'][2]['target_rate']*100:.2f}% positive rate,")
    print(f"  then model has {results['top_bucket_analysis'][2]['lift']:.2f}x lift over base rate.")
    print(f"\nCalibration Strategy:")
    print(f"  Set decision threshold to probability where lift justifies trading costs (e.g., 10x lift minimum)")
