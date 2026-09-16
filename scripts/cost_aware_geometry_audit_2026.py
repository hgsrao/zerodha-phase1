#!/usr/bin/env python3
"""
Cost-Aware Geometry Audit: Do Top Probability Buckets Survive Trading Costs?
=============================================================================

The probability audit shows 36x+ lift in top 0.1% bucket. But does that lift
translate to positive P&L after costs?

This audit:
1. Load actual forward returns from TRAIN split data
2. Match with top probability predictions
3. Compute expected P&L in each bucket (accounting for 8 bps trading costs)
4. Verify that top buckets achieve positive expected payoff
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
        logging.FileHandler('cost_aware_geometry_audit_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class CostAwareGeometryAudit:
    """Verify predicted probabilities translate to positive P&L after costs."""

    FEATURE_COLUMNS = [
        '5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
        '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
        '15m_rs_percentile', '15m_rs_excess'
    ]

    # Trading cost parameters (basis points)
    TRANSACTION_COST_BPS = 5      # Entry + Exit spread/commission
    SLIPPAGE_BPS = 3              # Execution slippage
    TOTAL_COST_BPS = TRANSACTION_COST_BPS + SLIPPAGE_BPS  # 8 bps

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

        logger.info("✅ CostAwareGeometryAudit initialized")
        logger.info(f"   Trading costs: {self.TOTAL_COST_BPS} bps total")
        logger.info(f"   ({self.TRANSACTION_COST_BPS} bps transaction + {self.SLIPPAGE_BPS} bps slippage)")

    def load_train_data(self) -> Tuple[pd.DataFrame, pd.Series]:
        """Load TRAIN split with OHLCV for return calculation."""
        logger.info("\nLoading TRAIN split (with OHLCV)...")

        # Features + OHLCV
        X_list = []
        ohlcv_list = []

        for parquet_file in sorted(self.feature_dir.glob("*_mtf_2026.parquet")):
            df = pd.read_parquet(parquet_file)

            # Get feature columns
            available_cols = [col for col in self.FEATURE_COLUMNS if col in df.columns]
            X = df[available_cols].copy()
            X_list.append(X)

            # Get OHLCV for return calculation
            ohlcv = df[['close']].copy()
            ohlcv_list.append(ohlcv)

        X_combined = pd.concat(X_list, axis=0, ignore_index=False).reset_index(drop=True)
        ohlcv_combined = pd.concat(ohlcv_list, axis=0, ignore_index=False).reset_index(drop=True)

        # Labels
        labels_path = self.label_dir / "train_labels.parquet"
        y = pd.read_parquet(labels_path)['label'].reset_index(drop=True)

        logger.info(f"  ✅ Loaded features: {X_combined.shape}")
        logger.info(f"  ✅ Loaded OHLCV: {ohlcv_combined.shape}")
        logger.info(f"  ✅ Loaded labels: {y.shape}")

        return X_combined, ohlcv_combined, y

    def calculate_forward_returns(self, df_ohlcv: pd.DataFrame, periods: int = 20) -> pd.Series:
        """Calculate forward returns (same as label generation)."""
        logger.info(f"\nCalculating forward returns ({periods} periods ahead)...")

        forward_close = df_ohlcv['close'].shift(-periods)
        forward_return = (forward_close - df_ohlcv['close']) / df_ohlcv['close']
        forward_return_bps = forward_return * 10000  # Convert to basis points

        logger.info(f"  ✅ Forward returns: min={forward_return_bps.min():.2f} bps, "
                   f"max={forward_return_bps.max():.2f} bps, mean={forward_return_bps.mean():.2f} bps")

        return forward_return_bps

    def generate_probabilities(self, X: pd.DataFrame) -> np.ndarray:
        """Generate predicted probabilities."""
        # Remove NaN rows
        valid_mask = X.notna().all(axis=1)
        X_clean = X[valid_mask]

        # Scale
        X_scaled = self.scaler.transform(X_clean)

        # Predict probabilities
        y_proba = self.model.predict_proba(X_scaled)[:, 1]

        return y_proba, valid_mask

    def cost_aware_pnl_analysis(self, y_proba: np.ndarray, forward_returns_bps: pd.Series,
                                valid_mask: np.ndarray) -> Dict:
        """Analyze P&L by probability bucket, accounting for costs."""
        logger.info("\n" + "="*80)
        logger.info("COST-AWARE P&L ANALYSIS BY PROBABILITY BUCKET")
        logger.info("="*80)

        # Align data
        forward_returns_aligned = forward_returns_bps[valid_mask].values

        # Create result dataframe
        results_df = pd.DataFrame({
            'probability': y_proba,
            'forward_return_bps': forward_returns_aligned
        })

        # Define percentile buckets
        percentile_edges = [0, 50, 75, 90, 95, 99, 99.5, 99.9, 99.95, 99.99, 100]

        pnl_analysis = []

        logger.info(f"\n{'Percentile':20s} {'Min Prob':12s} {'N Samples':12s} "
                   f"{'Avg Return':15s} {'Gross P&L':15s} {'Costs':15s} {'Net P&L':15s} {'Survival':15s}")
        logger.info("-" * 130)

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

            if n_samples == 0:
                continue

            min_prob = bucket['probability'].min()
            avg_return = bucket['forward_return_bps'].mean()

            # Gross P&L (before costs)
            gross_pnl = avg_return

            # Costs (both entry and exit)
            total_costs = self.TOTAL_COST_BPS

            # Net P&L
            net_pnl = gross_pnl - total_costs

            # Did this bucket survive costs? (Net P&L > 0)
            survival = "✅" if net_pnl > 0 else "❌"

            percentile_label = f"{p_low}-{p_high}%ile"

            logger.info(f"{percentile_label:20s} {min_prob:12.6f} {n_samples:12d} "
                       f"{avg_return:14.2f} bps {gross_pnl:14.2f} bps {total_costs:14.2f} bps "
                       f"{net_pnl:14.2f} bps {survival:>14s}")

            pnl_analysis.append({
                'percentile_low': p_low,
                'percentile_high': p_high,
                'min_probability': float(min_prob),
                'n_samples': int(n_samples),
                'avg_forward_return_bps': float(avg_return),
                'gross_pnl_bps': float(gross_pnl),
                'costs_bps': float(total_costs),
                'net_pnl_bps': float(net_pnl),
                'survives_costs': net_pnl > 0
            })

        return pnl_analysis

    def top_bucket_pnl_deep_dive(self, y_proba: np.ndarray, forward_returns_bps: pd.Series,
                                 valid_mask: np.ndarray) -> Dict:
        """Deep dive P&L analysis for top probability buckets."""
        logger.info("\n" + "="*80)
        logger.info("TOP BUCKET P&L DEEP DIVE")
        logger.info("="*80)

        forward_returns_aligned = forward_returns_bps[valid_mask].values

        results_df = pd.DataFrame({
            'probability': y_proba,
            'forward_return_bps': forward_returns_aligned
        })

        top_cuts = [0.1, 0.5, 1.0, 5.0, 10.0]

        logger.info(f"\n{'Top %':15s} {'Prob Threshold':20s} {'N Samples':15s} "
                   f"{'Avg Return':20s} {'Net P&L':20s} {'Profitable':20s}")
        logger.info("-" * 110)

        top_pnl_results = []

        for cut_pct in top_cuts:
            threshold_quantile = (100 - cut_pct) / 100
            threshold_prob = results_df['probability'].quantile(threshold_quantile)

            top_bucket = results_df[results_df['probability'] >= threshold_prob]
            n_samples = len(top_bucket)
            avg_return = top_bucket['forward_return_bps'].mean()
            net_pnl = avg_return - self.TOTAL_COST_BPS

            profitable_count = (top_bucket['forward_return_bps'] > self.TOTAL_COST_BPS).sum()
            profitable_pct = profitable_count / n_samples * 100 if n_samples > 0 else 0

            profitable_label = "✅ YES" if net_pnl > 0 else "❌ NO"

            logger.info(f"{cut_pct:14.1f}% {threshold_prob:19.6f} {n_samples:15d} "
                       f"{avg_return:19.2f} bps {net_pnl:19.2f} bps {profitable_label:>19s}")

            top_pnl_results.append({
                'top_pct': cut_pct,
                'probability_threshold': float(threshold_prob),
                'n_samples': int(n_samples),
                'avg_forward_return_bps': float(avg_return),
                'net_pnl_bps': float(net_pnl),
                'profitable_trades_pct': float(profitable_pct),
                'survives_costs': net_pnl > 0
            })

        return top_pnl_results

    def run_audit(self) -> Dict:
        """Execute full cost-aware geometry audit."""
        logger.info("\n" + "="*80)
        logger.info("COST-AWARE GEOMETRY AUDIT - TRAIN SPLIT")
        logger.info("="*80 + "\n")

        # Load data
        X_train, ohlcv_train, y_train = self.load_train_data()

        # Calculate forward returns
        forward_returns_bps = self.calculate_forward_returns(ohlcv_train)

        # Generate probabilities
        y_proba, valid_mask = self.generate_probabilities(X_train)

        # P&L analysis by bucket
        pnl_analysis = self.cost_aware_pnl_analysis(y_proba, forward_returns_bps, valid_mask)

        # Top bucket deep dive
        top_pnl = self.top_bucket_pnl_deep_dive(y_proba, forward_returns_bps, valid_mask)

        # Summary
        logger.info("\n" + "="*80)
        logger.info("CRITICAL FINDING: COST SURVIVABILITY")
        logger.info("="*80)

        for result in top_pnl:
            pct = result['top_pct']
            net_pnl = result['net_pnl_bps']
            survives = "✅ SURVIVES" if result['survives_costs'] else "❌ FAILS"
            logger.info(f"\nTop {pct:.1f}%: Net P&L = {net_pnl:.2f} bps → {survives}")

        results = {
            "trading_costs_bps": self.TOTAL_COST_BPS,
            "percentile_pnl_analysis": pnl_analysis,
            "top_bucket_pnl_analysis": top_pnl
        }

        # Save results
        results_path = Path("cost_aware_geometry_audit_results.json")
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"\n✅ Results saved: {results_path}")

        return results


if __name__ == "__main__":
    auditor = CostAwareGeometryAudit()
    results = auditor.run_audit()

    print("\n" + "="*80)
    print("✅ COST-AWARE GEOMETRY AUDIT COMPLETE")
    print("="*80)
    print(f"\nTrading Costs: {auditor.TOTAL_COST_BPS} bps (5 bps transaction + 3 bps slippage)")
    print(f"\nTop Bucket Results (must achieve >0 net P&L to be viable):")
    for res in results['top_bucket_pnl_analysis'][:3]:
        status = "✅ VIABLE" if res['survives_costs'] else "❌ NOT VIABLE"
        print(f"  Top {res['top_pct']:.1f}%: {status} (Net P&L: {res['net_pnl_bps']:.2f} bps)")
