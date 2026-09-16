#!/usr/bin/env python3
"""
Cost-Aware P&L Audit: Triple-Barrier 45-Minute Model with Realistic Friction
==============================================================================

Requirements:
1. Intrabar Ambiguity: Both barriers crossed in same bar → EXCLUDED
2. Friction: 8 bps round-trip (statutory slippage + brokerage + exchange fees)
3. Score Sorting: Verify if top probability percentiles achieve win rate >40%
4. Net Expectancy: Confirm positive net P&L after costs

Mathematical Requirement:
- Baseline breakeven: 40.0% win rate (1.5:1 reward/risk)
- Base population: 38.39% win rate → NEGATIVE raw expectancy (-0.03765R)
- Model must achieve >40% win rate in top percentiles to be viable
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import pickle
import json
import logging
from datetime import datetime
from typing import Dict, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('cost_aware_pnl_audit_triple_barrier_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TripleBarrierCostAudit:
    """Audit cost-aware P&L for triple-barrier model with realistic friction."""

    # Realistic Friction (basis points)
    TRANSACTION_BPS = 3      # Exchange fee
    BROKERAGE_BPS = 2        # Broker commission
    SLIPPAGE_BPS = 3         # Execution slippage
    TOTAL_FRICTION_BPS = TRANSACTION_BPS + BROKERAGE_BPS + SLIPPAGE_BPS  # 8 bps

    # Barrier Configuration
    TARGET_ATR_MULTIPLE = 1.5
    STOP_ATR_MULTIPLE = 1.0
    BREAKEVEN_WIN_RATE = (TARGET_ATR_MULTIPLE + STOP_ATR_MULTIPLE) / STOP_ATR_MULTIPLE  # 40%

    def __init__(self,
                 feature_dir: str = "revision2/features_mtf_2026",
                 label_dir: str = "revision2/labels_triple_barrier_2026",
                 model_path: str = "revision2/frozen_models_triple_barrier_45min_2026/logistic_regression.pkl",
                 scaler_path: str = "revision2/frozen_models_triple_barrier_45min_2026/feature_scaler.pkl"):
        """Initialize auditor."""
        self.feature_dir = Path(feature_dir)
        self.label_dir = Path(label_dir)

        # Load model and scaler
        with open(model_path, 'rb') as f:
            self.model = pickle.load(f)
        with open(scaler_path, 'rb') as f:
            self.scaler = pickle.load(f)

        logger.info("✅ TripleBarrierCostAudit initialized")
        logger.info(f"   Friction: {self.TOTAL_FRICTION_BPS} bps ({self.TRANSACTION_BPS} tx + {self.BROKERAGE_BPS} brokerage + {self.SLIPPAGE_BPS} slippage)")
        logger.info(f"   Breakeven Win Rate: {self.BREAKEVEN_WIN_RATE*100:.1f}%")

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range."""
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=1).mean()

        return atr

    def load_data_with_predictions(self) -> Tuple[pd.DataFrame, np.ndarray, pd.Series]:
        """
        Load features, generate predictions, and load labels.

        Returns:
            (combined_ohlcv, predicted_probabilities, labels)
        """
        logger.info("\nLoading features and generating predictions...")

        feature_cols = ['5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
                       '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
                       '15m_rs_percentile', '15m_rs_excess']

        X_list = []
        ohlcv_list = []

        for f in sorted(self.feature_dir.glob("*_mtf_2026.parquet")):
            df = pd.read_parquet(f)
            X = df[[c for c in feature_cols if c in df.columns]].copy()
            X_list.append(X)
            ohlcv_list.append(df[['open', 'high', 'low', 'close', 'volume']].copy())

        X_combined = pd.concat(X_list, axis=0, ignore_index=False).reset_index(drop=True)
        ohlcv_combined = pd.concat(ohlcv_list, axis=0, ignore_index=False).reset_index(drop=True)

        logger.info(f"  ✅ Loaded features: {X_combined.shape}")

        # Generate predictions
        valid_mask = X_combined.notna().all(axis=1)
        X_valid = X_combined[valid_mask]
        X_scaled = self.scaler.transform(X_valid)
        y_proba_valid = self.model.predict_proba(X_scaled)[:, 1]

        # Create full probability array (NaN for invalid rows)
        y_proba_full = np.full(len(X_combined), np.nan)
        y_proba_full[valid_mask] = y_proba_valid

        logger.info(f"  ✅ Generated predictions: {y_proba_valid.shape}")

        # Load labels
        labels = pd.read_parquet(self.label_dir / "train_labels.parquet")['label'].reset_index(drop=True)
        logger.info(f"  ✅ Loaded labels: {labels.shape}")

        return ohlcv_combined, y_proba_full, labels

    def audit_pnl_by_percentile(self, ohlcv: pd.DataFrame, y_proba: np.ndarray, labels: pd.Series) -> Dict:
        """
        Audit P&L by probability percentile, handling intrabar ambiguity.

        For each bar, if both target and stop are breached in the same bar (intrabar),
        exclude that outcome.
        """
        logger.info("\n" + "="*80)
        logger.info("COST-AWARE P&L AUDIT BY PROBABILITY PERCENTILE")
        logger.info("="*80 + "\n")

        # Calculate ATR
        atr = self.calculate_atr(ohlcv)

        # Create audit dataframe
        valid_mask = ~np.isnan(y_proba) & labels.notna()
        audit_df = pd.DataFrame({
            'probability': y_proba[valid_mask],
            'label': labels[valid_mask].values,
            'high': ohlcv.loc[valid_mask, 'high'].values,
            'low': ohlcv.loc[valid_mask, 'low'].values,
            'open': ohlcv.loc[valid_mask, 'open'].values,
            'atr': atr[valid_mask].values
        })

        logger.info(f"Valid audit rows: {len(audit_df):,}")
        logger.info(f"Base win rate in population: {audit_df['label'].mean()*100:.2f}%")

        # Calculate barriers
        audit_df['target'] = audit_df['open'] + (self.TARGET_ATR_MULTIPLE * audit_df['atr'])
        audit_df['stop'] = audit_df['open'] - (self.STOP_ATR_MULTIPLE * audit_df['atr'])

        # Detect intrabar ambiguity
        target_crossed = audit_df['high'] >= audit_df['target']
        stop_crossed = audit_df['low'] <= audit_df['stop']
        intrabar_ambiguous = target_crossed & stop_crossed

        logger.info(f"Intrabar ambiguity (both barriers hit): {intrabar_ambiguous.sum():,} ({intrabar_ambiguous.mean()*100:.2f}%)")

        # Define percentile buckets
        percentiles = [10, 25, 50, 75, 90, 95, 99, 99.5, 99.9]

        logger.info(f"\n{'Percentile':15s} {'N Samples':12s} {'Win Rate':12s} {'Gross P&L':15s} {'Friction':15s} {'Net P&L':15s} {'Status':12s}")
        logger.info("-" * 110)

        results = []

        for pct in percentiles:
            # Get top-X% by probability
            threshold = np.nanpercentile(audit_df['probability'], 100 - pct)
            cohort_mask = audit_df['probability'] >= threshold

            cohort = audit_df[cohort_mask]
            cohort_unambiguous = cohort[~intrabar_ambiguous[cohort_mask]]

            if len(cohort_unambiguous) == 0:
                continue

            # Win rate (label=1 means TARGET_FIRST, which is a win)
            win_rate = cohort_unambiguous['label'].mean()

            # Gross P&L (wins get +1.5R, losses get -1.0R)
            gross_pnl = (win_rate * self.TARGET_ATR_MULTIPLE) - ((1 - win_rate) * self.STOP_ATR_MULTIPLE)

            # Friction (in ATR units: 8 bps / 10000 * ATR)
            # Simplified: 8 bps = 0.0008 * price, as ratio of ATR
            friction_bps_per_atr = self.TOTAL_FRICTION_BPS / 10000  # Convert bps to decimal
            avg_atr = cohort_unambiguous['atr'].mean()
            friction_atr = (friction_bps_per_atr * cohort_unambiguous['open'].mean()) / avg_atr
            # Simpler: friction is 8 bps per trade, which is 0.08% of notional
            # In terms of ATR: friction_atr = 8 bps / avg_atr in bps
            # Let's use: friction = 8 bps converted to R units
            # If we think in bps: target = 150 bps (1.5 ATR), stop = 100 bps (1.0 ATR)
            # Friction: 8 bps on entry + 8 bps on exit (assuming exit at target/stop)
            # Approximate: friction per R = 16 bps / 100 bps = 0.16R
            friction_per_r = (2 * self.TOTAL_FRICTION_BPS) / 10000  # Both entry and exit legs

            # Net P&L
            net_pnl = gross_pnl - friction_per_r

            # Status
            viable = "✅ VIABLE" if net_pnl > 0 else "❌ NOT VIABLE"
            if win_rate >= self.BREAKEVEN_WIN_RATE * 100:
                viable_threshold = "✅ >40%" if net_pnl > 0 else "⚠️  >40% but -cost"
            else:
                viable_threshold = "❌ <40%"

            percentile_label = f"Top {pct}%"
            logger.info(f"{percentile_label:15s} {len(cohort_unambiguous):12,} {win_rate*100:11.2f}% {gross_pnl:14.4f}R {-friction_per_r:14.4f}R {net_pnl:14.4f}R {viable:>11s}")

            results.append({
                'percentile': pct,
                'n_samples': len(cohort_unambiguous),
                'win_rate': float(win_rate),
                'gross_pnl_r': float(gross_pnl),
                'friction_r': float(friction_per_r),
                'net_pnl_r': float(net_pnl),
                'viable': net_pnl > 0
            })

        return results

    def run_audit(self) -> Dict:
        """Execute full cost-aware P&L audit."""
        logger.info("\n" + "="*80)
        logger.info("TRIPLE-BARRIER COST-AWARE P&L AUDIT - TRAIN SPLIT")
        logger.info("="*80 + "\n")

        # Load data
        ohlcv, y_proba, labels = self.load_data_with_predictions()

        # Audit by percentile
        results = self.audit_pnl_by_percentile(ohlcv, y_proba, labels)

        # Summary
        logger.info("\n" + "="*80)
        logger.info("VIABILITY ASSESSMENT")
        logger.info("="*80 + "\n")

        logger.info(f"Theoretical Breakeven: {self.BREAKEVEN_WIN_RATE*100:.1f}% win rate")
        logger.info(f"Base Population: 38.39% win rate")
        logger.info(f"Friction: {self.TOTAL_FRICTION_BPS} bps ({2*self.TOTAL_FRICTION_BPS} bps round-trip)")

        viable_count = sum(1 for r in results if r['viable'])
        logger.info(f"\nViable Percentiles: {viable_count}/{len(results)}")

        if viable_count > 0:
            top_viable = [r for r in results if r['viable']][0]
            logger.info(f"✅ Best viable cohort: Top {top_viable['percentile']}% with {top_viable['net_pnl_r']:+.4f}R net P&L")
        else:
            logger.warning(f"❌ NO VIABLE COHORTS - Model cannot overcome base negative expectancy + friction")

        return {
            "theoretical_breakeven_pct": self.BREAKEVEN_WIN_RATE * 100,
            "base_population_win_rate": 38.39,
            "friction_bps_round_trip": 2 * self.TOTAL_FRICTION_BPS,
            "percentile_results": results,
            "viable_count": viable_count
        }


if __name__ == "__main__":
    auditor = TripleBarrierCostAudit()
    results = auditor.run_audit()

    print("\n" + "="*80)
    print("✅ COST-AWARE P&L AUDIT COMPLETE")
    print("="*80)
    print(f"\nTheoretical Framework:")
    print(f"  Breakeven Win Rate: {results['theoretical_breakeven_pct']:.1f}%")
    print(f"  Base Population: {results['base_population_win_rate']:.2f}%")
    print(f"  Friction: {results['friction_bps_round_trip']} bps (round-trip)")
    print(f"\nResult: {results['viable_count']} viable percentiles out of {len(results['percentile_results'])}")
    if results['viable_count'] > 0:
        print(f"✅ MODEL IS VIABLE - Top cohorts achieve positive net P&L after costs")
    else:
        print(f"❌ MODEL IS NOT VIABLE - Unable to overcome negative base expectancy + friction")
