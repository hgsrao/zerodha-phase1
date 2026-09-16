#!/usr/bin/env python3
"""
Corrected Cost-Aware P&L Audit: Friction-Inclusive Hurdle Rate
===============================================================

Proper calculation:
1. Friction: 8 bps round-trip = 0.20-0.27R of 1.0R stop (typical ATR 30-40 bps)
2. Hurdle rate: p(1.5-c_R) - (1-p)(1.0+c_R) = 0 → p = 48-50.8% (NOT 40%)
3. Model must achieve >48% to be viable

Cohort Analysis:
- Report mean net P&L by decile, top 0.5%, top 1%, top 5%
- Exclude INTRABAR_ORDER_UNKNOWN from win rate calculation
- Show total resolved sample count
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('corrected_cost_audit_triple_barrier_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class CorrectedCostAudit:
    """Friction-aware cost audit with proper hurdle rate."""

    # Friction parameters
    BROKERAGE_BPS_PER_LEG = 2
    EXCHANGE_BPS_PER_LEG = 3
    SLIPPAGE_BPS_PER_LEG = 3
    TOTAL_BPS_PER_LEG = BROKERAGE_BPS_PER_LEG + EXCHANGE_BPS_PER_LEG + SLIPPAGE_BPS_PER_LEG  # 8 bps
    ROUND_TRIP_BPS = 2 * TOTAL_BPS_PER_LEG  # 16 bps

    # Barrier config
    TARGET_MULTIPLE = 1.5
    STOP_MULTIPLE = 1.0

    # Friction-adjusted hurdle (assuming ATR ~30 bps, so c_R ≈ 0.27R)
    FRICTION_R_CONSERVATIVE = 0.27  # 8 bps / 30 bps
    FRICTION_R_OPTIMISTIC = 0.20    # 8 bps / 40 bps

    # Hurdle rates
    HURDLE_CONSERVATIVE = (STOP_MULTIPLE + FRICTION_R_CONSERVATIVE) / (TARGET_MULTIPLE - FRICTION_R_CONSERVATIVE + STOP_MULTIPLE + FRICTION_R_CONSERVATIVE)
    HURDLE_OPTIMISTIC = (STOP_MULTIPLE + FRICTION_R_OPTIMISTIC) / (TARGET_MULTIPLE - FRICTION_R_OPTIMISTIC + STOP_MULTIPLE + FRICTION_R_OPTIMISTIC)

    def __init__(self,
                 feature_dir: str = "revision2/features_mtf_2026",
                 label_dir: str = "revision2/labels_triple_barrier_2026",
                 model_path: str = "revision2/frozen_models_triple_barrier_45min_2026/logistic_regression.pkl",
                 scaler_path: str = "revision2/frozen_models_triple_barrier_45min_2026/feature_scaler.pkl"):
        """Initialize auditor."""
        self.feature_dir = Path(feature_dir)
        self.label_dir = Path(label_dir)

        with open(model_path, 'rb') as f:
            self.model = pickle.load(f)
        with open(scaler_path, 'rb') as f:
            self.scaler = pickle.load(f)

        logger.info("✅ CorrectedCostAudit initialized")
        logger.info(f"   Round-trip friction: {self.ROUND_TRIP_BPS} bps")
        logger.info(f"   Hurdle rate (conservative, c_R=0.27): {self.HURDLE_CONSERVATIVE*100:.2f}%")
        logger.info(f"   Hurdle rate (optimistic, c_R=0.20): {self.HURDLE_OPTIMISTIC*100:.2f}%")

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR."""
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=1).mean()
        return atr

    def load_and_score(self):
        """Load features, labels, OHLCV and generate scores."""
        logger.info("\nLoading data and generating model scores...")

        feature_cols = ['5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
                       '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
                       '15m_rs_percentile', '15m_rs_excess']

        X_list, ohlcv_list = [], []
        for f in sorted(self.feature_dir.glob("*_mtf_2026.parquet")):
            df = pd.read_parquet(f)
            X = df[[c for c in feature_cols if c in df.columns]].copy()
            X_list.append(X)
            ohlcv_list.append(df[['open', 'high', 'low', 'close']].copy())

        X = pd.concat(X_list, axis=0, ignore_index=False).reset_index(drop=True)
        ohlcv = pd.concat(ohlcv_list, axis=0, ignore_index=False).reset_index(drop=True)

        # Score
        valid_mask = X.notna().all(axis=1)
        scores_full = np.full(len(X), np.nan)
        X_scaled = self.scaler.transform(X[valid_mask])
        scores_full[valid_mask] = self.model.predict_proba(X_scaled)[:, 1]

        # Labels
        labels = pd.read_parquet(self.label_dir / "train_labels.parquet")['label'].reset_index(drop=True)

        logger.info(f"  ✅ Features: {X.shape}")
        logger.info(f"  ✅ Scores generated: {(~np.isnan(scores_full)).sum():,}")
        logger.info(f"  ✅ Labels: {labels.shape}")

        return ohlcv, scores_full, labels

    def run_audit(self):
        """Run corrected audit."""
        logger.info("\n" + "="*80)
        logger.info("CORRECTED COST-AWARE AUDIT: FRICTION-INCLUSIVE HURDLE RATE")
        logger.info("="*80 + "\n")

        ohlcv, scores, labels = self.load_and_score()
        atr = self.calculate_atr(ohlcv)

        # Build audit DataFrame
        valid_mask = ~np.isnan(scores) & labels.notna()
        audit_df = pd.DataFrame({
            'score': scores[valid_mask],
            'label': labels[valid_mask].values,
            'open': ohlcv.loc[valid_mask, 'open'].values,
            'high': ohlcv.loc[valid_mask, 'high'].values,
            'low': ohlcv.loc[valid_mask, 'low'].values,
            'atr': atr[valid_mask].values
        })

        logger.info(f"Total audit rows: {len(audit_df):,}")
        logger.info(f"Base win rate: {audit_df['label'].mean()*100:.2f}%")

        # Barriers and intrabar detection
        audit_df['target'] = audit_df['open'] + (self.TARGET_MULTIPLE * audit_df['atr'])
        audit_df['stop'] = audit_df['open'] - (self.STOP_MULTIPLE * audit_df['atr'])

        target_crossed = audit_df['high'] >= audit_df['target']
        stop_crossed = audit_df['low'] <= audit_df['stop']
        intrabar_ambiguous = target_crossed & stop_crossed

        logger.info(f"Intrabar ambiguity: {intrabar_ambiguous.sum():,} ({intrabar_ambiguous.mean()*100:.2f}%)")

        # Cohort analysis: deciles + top percentiles
        cohort_specs = [
            ("Decile 10 (Top 10%)", lambda s: s >= np.percentile(s, 90)),
            ("Decile 9 (80-90%)", lambda s: (s >= np.percentile(s, 80)) & (s < np.percentile(s, 90))),
            ("Top 5%", lambda s: s >= np.percentile(s, 95)),
            ("Top 1%", lambda s: s >= np.percentile(s, 99)),
            ("Top 0.5%", lambda s: s >= np.percentile(s, 99.5)),
        ]

        logger.info("\n" + "="*80)
        logger.info("COHORT ANALYSIS")
        logger.info("="*80 + "\n")

        logger.info(f"{'Cohort':25s} {'N Total':12s} {'N Resolved':12s} {'Intrabar':12s} {'Win Rate':12s} {'Gross P&L':15s} {'Net P&L (bps)':15s} {'vs Hurdle':15s}")
        logger.info("-" * 130)

        results = []

        for cohort_name, mask_fn in cohort_specs:
            cohort_mask = mask_fn(audit_df['score'].values)
            cohort = audit_df[cohort_mask]

            if len(cohort) == 0:
                continue

            # Exclude intrabar ambiguous
            cohort_resolved_mask = ~intrabar_ambiguous[cohort_mask]
            cohort_resolved = cohort[cohort_resolved_mask]

            n_total = len(cohort)
            n_resolved = len(cohort_resolved)
            n_intrabar = len(cohort) - len(cohort_resolved)

            if n_resolved == 0:
                continue

            # Win rate (label=1 = TARGET_FIRST)
            win_rate = cohort_resolved['label'].mean()

            # Gross P&L per trade (in R units)
            gross_pnl = (win_rate * self.TARGET_MULTIPLE) - ((1 - win_rate) * self.STOP_MULTIPLE)

            # Convert friction to R units
            avg_atr = cohort_resolved['atr'].mean()
            friction_r = (self.ROUND_TRIP_BPS / 10000) / (avg_atr / 10000) if avg_atr > 0 else 0.27
            friction_r = max(0.20, min(0.27, friction_r))  # Clamp to 0.20-0.27 range

            # Net P&L
            net_pnl_r = gross_pnl - friction_r

            # Net P&L in bps (approx: net_pnl_r * avg_atr)
            net_pnl_bps = net_pnl_r * avg_atr

            # vs hurdle
            vs_hurdle_conservative = win_rate - self.HURDLE_CONSERVATIVE
            vs_hurdle_label = f"+{vs_hurdle_conservative*100:+.1f}%" if vs_hurdle_conservative > 0 else f"{vs_hurdle_conservative*100:.1f}%"

            logger.info(f"{cohort_name:25s} {n_total:12,} {n_resolved:12,} {n_intrabar:12,} {win_rate*100:11.2f}% {gross_pnl:14.4f}R {net_pnl_bps:14.2f} bps {vs_hurdle_label:>14s}")

            results.append({
                'cohort': cohort_name,
                'n_total': n_total,
                'n_resolved': n_resolved,
                'n_intrabar': n_intrabar,
                'win_rate': float(win_rate),
                'gross_pnl_r': float(gross_pnl),
                'friction_r': float(friction_r),
                'net_pnl_r': float(net_pnl_r),
                'net_pnl_bps': float(net_pnl_bps),
                'vs_hurdle_conservative': float(vs_hurdle_conservative),
                'viable': win_rate > self.HURDLE_CONSERVATIVE
            })

        # Summary
        logger.info("\n" + "="*80)
        logger.info("FINAL ASSESSMENT")
        logger.info("="*80 + "\n")

        logger.info(f"Friction-adjusted hurdle rate: {self.HURDLE_CONSERVATIVE*100:.1f}% (conservative, c_R=0.27R)")
        logger.info(f"Alternative hurdle (optimistic, c_R=0.20R): {self.HURDLE_OPTIMISTIC*100:.1f}%")
        logger.info(f"\nViable cohorts (win_rate > hurdle): {sum(1 for r in results if r['viable'])}/{len(results)}")

        viable_results = [r for r in results if r['viable']]
        if viable_results:
            best = max(viable_results, key=lambda r: r['net_pnl_bps'])
            logger.info(f"✅ Best viable cohort: {best['cohort']}")
            logger.info(f"   Win rate: {best['win_rate']*100:.2f}% (vs {self.HURDLE_CONSERVATIVE*100:.1f}% hurdle)")
            logger.info(f"   Net P&L: {best['net_pnl_bps']:.2f} bps per trade")
        else:
            logger.warning(f"❌ NO VIABLE COHORTS - Model cannot beat friction-adjusted hurdle")

        return {
            "hurdle_rate_conservative": self.HURDLE_CONSERVATIVE * 100,
            "hurdle_rate_optimistic": self.HURDLE_OPTIMISTIC * 100,
            "round_trip_friction_bps": self.ROUND_TRIP_BPS,
            "results": results
        }


if __name__ == "__main__":
    auditor = CorrectedCostAudit()
    results = auditor.run_audit()

    print("\n" + "="*80)
    print("✅ CORRECTED COST AUDIT COMPLETE")
    print("="*80)
    print(f"\nHurdle Rates (friction-inclusive):")
    print(f"  Conservative (c_R=0.27): {results['hurdle_rate_conservative']:.1f}%")
    print(f"  Optimistic (c_R=0.20):   {results['hurdle_rate_optimistic']:.1f}%")
    print(f"\nViable cohorts: {sum(1 for r in results['results'] if r['viable'])}")
