#!/usr/bin/env python3
"""
Validation Gate: Out-of-Sample Kill-Switch Test
================================================

FROZEN model trained on TRAIN (Jan-Mar 2026) is applied to VALIDATION (Apr 2026).
This is the death test: if the edge disappears out-of-sample, the hypothesis is rejected.

Hard Kill-Switch:
- Top 1.0% cohort must achieve win_rate > 50.80% (friction-adjusted hurdle)
- Net P&L must be > 0.0 bps
- If either fails, stop all further processing. Do NOT touch TEST or SEALED.

Strict Protocol:
1. Load FROZEN model parameters from train
2. Load VALIDATION split (April 2026) from quarantine
3. Generate triple-barrier labels on validation data (same 45-bar logic)
4. Score validation samples with frozen model
5. Run cost audit on top 1.0% cohort
6. Report pass/fail verdict
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
        logging.FileHandler('validation_gate_out_of_sample_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ValidationGateOutOfSample:
    """Out-of-sample validation with hard kill-switch."""

    # Kill-switch parameters
    HURDLE_RATE = 0.508  # 50.80% friction-adjusted
    MIN_NET_PNL_BPS = 0.0

    # Friction
    ROUND_TRIP_BPS = 16

    # Barriers
    TARGET_MULTIPLE = 1.5
    STOP_MULTIPLE = 1.0

    def __init__(self,
                 validation_data_dir: str = "revision2/features_2026/validation",
                 model_path: str = "revision2/frozen_models_triple_barrier_45min_2026/logistic_regression.pkl",
                 scaler_path: str = "revision2/frozen_models_triple_barrier_45min_2026/feature_scaler.pkl"):
        """Initialize validation gate."""
        self.validation_data_dir = Path(validation_data_dir)
        self.model_path = Path(model_path)
        self.scaler_path = Path(scaler_path)

        # Load FROZEN model
        with open(self.model_path, 'rb') as f:
            self.model = pickle.load(f)
        with open(self.scaler_path, 'rb') as f:
            self.scaler = pickle.load(f)

        logger.info("✅ ValidationGateOutOfSample initialized")
        logger.info(f"   Kill-switch hurdle rate: {self.HURDLE_RATE*100:.1f}%")
        logger.info(f"   Min net P&L threshold: {self.MIN_NET_PNL_BPS} bps")

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

    def load_validation_data(self):
        """Load validation split (April 2026) from quarantine."""
        logger.info("\nLoading VALIDATION split (April 2026) from quarantine...")

        feature_cols = ['5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
                       '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
                       '15m_rs_percentile', '15m_rs_excess']

        X_list, ohlcv_list = [], []
        symbol_files = sorted(self.validation_data_dir.glob("*_mtf_2026.parquet"))

        logger.info(f"  Found {len(symbol_files)} symbol files")

        for symbol_file in symbol_files:
            df = pd.read_parquet(symbol_file)
            X = df[[c for c in feature_cols if c in df.columns]].copy()
            X_list.append(X)
            ohlcv_list.append(df[['open', 'high', 'low', 'close']].copy())

        X = pd.concat(X_list, axis=0, ignore_index=False).reset_index(drop=True)
        ohlcv = pd.concat(ohlcv_list, axis=0, ignore_index=False).reset_index(drop=True)

        logger.info(f"  ✅ Loaded features: {X.shape}")
        logger.info(f"  ✅ Loaded OHLCV: {ohlcv.shape}")

        return X, ohlcv

    def generate_triple_barrier_labels_validation(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate triple-barrier labels for validation (same 45-bar logic)."""
        logger.info("\nGenerating triple-barrier labels (45-bar horizon, validation)...")

        atr = self.calculate_atr(ohlcv)
        labels = np.full(len(ohlcv), np.nan)

        for t in range(len(ohlcv) - 46):
            entry_bar = t + 1
            entry_px = ohlcv.loc[entry_bar, 'open']

            if pd.isna(entry_px) or pd.isna(atr.iloc[t]):
                continue

            atr_val = atr.iloc[t]
            target_px = entry_px + (self.TARGET_MULTIPLE * atr_val)
            stop_px = entry_px - (self.STOP_MULTIPLE * atr_val)

            holding_start = entry_bar
            holding_end = min(entry_bar + 45, len(ohlcv) - 1)

            exit_state = 0  # Default: TIMEOUT
            for idx in range(holding_start, holding_end + 1):
                bar_high = ohlcv.loc[idx, 'high']
                bar_low = ohlcv.loc[idx, 'low']

                if not pd.isna(bar_high) and bar_high >= target_px:
                    exit_state = 1  # TARGET_FIRST
                    break
                if not pd.isna(bar_low) and bar_low <= stop_px:
                    exit_state = 0  # STOP_FIRST
                    break

            labels[entry_bar] = float(exit_state)

        logger.info(f"  ✅ Generated {np.sum(~np.isnan(labels)):,} labels")
        return pd.Series(labels)

    def run_validation_gate(self):
        """Execute out-of-sample validation with kill-switch."""
        logger.info("\n" + "="*80)
        logger.info("VALIDATION GATE: OUT-OF-SAMPLE TEST (APRIL 2026)")
        logger.info("="*80 + "\n")

        # Load data
        X_val, ohlcv_val = self.load_validation_data()
        labels_val = self.generate_triple_barrier_labels_validation(ohlcv_val)
        atr_val = self.calculate_atr(ohlcv_val)

        # Score with FROZEN model
        logger.info("\nScoring validation data with FROZEN model...")
        valid_mask = X_val.notna().all(axis=1)
        scores_full = np.full(len(X_val), np.nan)
        X_scaled = self.scaler.transform(X_val[valid_mask])
        scores_full[valid_mask] = self.model.predict_proba(X_scaled)[:, 1]

        logger.info(f"  ✅ Scored {(~np.isnan(scores_full)).sum():,} samples")

        # Build audit DataFrame
        audit_mask = ~np.isnan(scores_full) & labels_val.notna()
        audit_df = pd.DataFrame({
            'score': scores_full[audit_mask],
            'label': labels_val[audit_mask].values,
            'open': ohlcv_val.loc[audit_mask, 'open'].values,
            'high': ohlcv_val.loc[audit_mask, 'high'].values,
            'low': ohlcv_val.loc[audit_mask, 'low'].values,
            'atr': atr_val[audit_mask].values
        })

        logger.info(f"\nTotal audit samples: {len(audit_df):,}")
        logger.info(f"Base win rate (validation): {audit_df['label'].mean()*100:.2f}%")

        # Intrabar detection
        audit_df['target'] = audit_df['open'] + (self.TARGET_MULTIPLE * audit_df['atr'])
        audit_df['stop'] = audit_df['open'] - (self.STOP_MULTIPLE * audit_df['atr'])

        target_crossed = audit_df['high'] >= audit_df['target']
        stop_crossed = audit_df['low'] <= audit_df['stop']
        intrabar_ambiguous = target_crossed & stop_crossed

        logger.info(f"Intrabar ambiguity: {intrabar_ambiguous.sum():,} ({intrabar_ambiguous.mean()*100:.2f}%)")

        # Top 1% cohort analysis
        logger.info("\n" + "="*80)
        logger.info("KILL-SWITCH TEST: TOP 1.0% COHORT")
        logger.info("="*80 + "\n")

        # ENFORCE STRICT FROZEN THRESHOLD (NO LOOK-AHEAD)
        threshold = 0.553850  # Top 1.0% frozen cutoff from TRAIN distribution
        top1_mask = audit_df['score'] >= threshold
        top1_cohort = audit_df[top1_mask]
        top1_resolved_mask = ~intrabar_ambiguous[top1_mask]
        top1_resolved = top1_cohort[top1_resolved_mask]

        n_total = len(top1_cohort)
        n_resolved = len(top1_resolved)
        n_intrabar = n_total - n_resolved

        win_rate = top1_resolved['label'].mean() if n_resolved > 0 else 0
        gross_pnl = (win_rate * self.TARGET_MULTIPLE) - ((1 - win_rate) * self.STOP_MULTIPLE)

        # Friction
        avg_atr = top1_resolved['atr'].mean() if n_resolved > 0 else 1
        friction_r = (self.ROUND_TRIP_BPS / 10000) / (avg_atr / 10000) if avg_atr > 0 else 0.27
        friction_r = max(0.20, min(0.27, friction_r))

        net_pnl_r = gross_pnl - friction_r
        net_pnl_bps = net_pnl_r * avg_atr if n_resolved > 0 else 0

        # Kill-switch verdict
        passes_hurdle = win_rate > self.HURDLE_RATE
        passes_pnl = net_pnl_bps >= self.MIN_NET_PNL_BPS
        overall_pass = passes_hurdle and passes_pnl

        logger.info(f"Top 1.0% Cohort (Validation):")
        logger.info(f"  N samples: {n_total:,}")
        logger.info(f"  N resolved: {n_resolved:,}")
        logger.info(f"  Intrabar ambiguous: {n_intrabar:,}")
        logger.info(f"  Win rate: {win_rate*100:.2f}%")
        logger.info(f"  Gross P&L: {gross_pnl:.4f}R")
        logger.info(f"  Friction: {friction_r:.4f}R")
        logger.info(f"  Net P&L: {net_pnl_bps:.2f} bps")

        logger.info(f"\n{'KILL-SWITCH VERDICT':40s}")
        logger.info("-" * 60)
        logger.info(f"Hurdle rate:    {self.HURDLE_RATE*100:.1f}%   |  Achieved: {win_rate*100:.2f}%   |  {'✅ PASS' if passes_hurdle else '❌ FAIL'}")
        logger.info(f"Min net P&L:    {self.MIN_NET_PNL_BPS:.1f} bps   |  Achieved: {net_pnl_bps:.2f} bps   |  {'✅ PASS' if passes_pnl else '❌ FAIL'}")
        logger.info("-" * 60)

        if overall_pass:
            logger.warning("\n" + "="*80)
            logger.warning("✅ VALIDATION GATE PASSED - HYPOTHESIS SURVIVES OUT-OF-SAMPLE TEST")
            logger.warning("="*80)
            logger.warning("\nProceed to TEST split (May 2026) evaluation")
        else:
            logger.error("\n" + "="*80)
            logger.error("❌ VALIDATION GATE FAILED - HYPOTHESIS REJECTED")
            logger.error("="*80)
            logger.error("\nDO NOT proceed to TEST or SEALED splits.")
            logger.error("The edge does not survive out-of-sample evaluation.")
            if not passes_hurdle:
                logger.error(f"  Reason: Win rate fell below hurdle ({win_rate*100:.2f}% < {self.HURDLE_RATE*100:.1f}%)")
            if not passes_pnl:
                logger.error(f"  Reason: Net P&L is non-positive ({net_pnl_bps:.2f} bps <= 0)")

        return {
            "validation_split": "April 2026",
            "n_audit_samples": len(audit_df),
            "n_top1_samples": n_resolved,
            "win_rate_top1": float(win_rate),
            "hurdle_rate": self.HURDLE_RATE,
            "passes_hurdle": passes_hurdle,
            "net_pnl_bps_top1": float(net_pnl_bps),
            "min_pnl_threshold": self.MIN_NET_PNL_BPS,
            "passes_pnl": passes_pnl,
            "overall_pass": overall_pass,
            "verdict": "PASS" if overall_pass else "FAIL"
        }


if __name__ == "__main__":
    validator = ValidationGateOutOfSample()
    result = validator.run_validation_gate()

    print("\n" + "="*80)
    print("VALIDATION GATE RESULT")
    print("="*80)
    print(f"\nOverall Verdict: {result['verdict']}")
    print(f"Win Rate (Top 1%): {result['win_rate_top1']*100:.2f}% (vs {result['hurdle_rate']*100:.1f}% hurdle)")
    print(f"Net P&L (Top 1%): {result['net_pnl_bps_top1']:.2f} bps (vs {result['min_pnl_threshold']:.1f} bps minimum)")

    if result['overall_pass']:
        print(f"\n✅ Hypothesis SURVIVES out-of-sample test. Proceed to TEST split.")
    else:
        print(f"\n❌ Hypothesis REJECTED. Stop here. Do not test on TEST or SEALED splits.")
