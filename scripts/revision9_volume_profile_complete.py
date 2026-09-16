#!/usr/bin/env python3
"""
REVISION 9: VOLUME PROFILE MEAN-REVERSION ENGINE
=================================================
Solves: Technical indicator weakness via institutional volume nodes (POC)

Execution:
  python3 scripts/revision9_volume_profile_complete.py --phase poc_computation
  python3 scripts/revision9_volume_profile_complete.py --phase label_generation
  python3 scripts/revision9_volume_profile_complete.py --phase model_training
  python3 scripts/revision9_volume_profile_complete.py --phase validation_gate
  python3 scripts/revision9_volume_profile_complete.py --phase test_gate
"""

import glob, pickle, os, sys, argparse
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# CONFIGURATION
SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
    "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO"
]

FEATURE_COLS = ['dist_to_poc_atr', 'poc_strength', 'volume_imbalance']
POC_ENTRY_DIST = 2.0  # ±2 ATR from POC
FRICTION_R = 0.27
HURDLE_RATE = 0.508
HORIZON_BARS = 45  # 45-bar holding period

def compute_volume_profiles(phase='train'):
    """Compute Point of Control (POC) for each symbol"""
    logger.info(f"PHASE 1: Computing volume profiles and POC for {phase}")

    Path(f"revision9_volume/{phase}").mkdir(parents=True, exist_ok=True)

    success_count = 0
    for symbol in SYMBOLS:
        try:
            files = glob.glob(f'revision2/features_2026/{phase}/*{symbol}*.parquet')
            if not files:
                continue

            df = pd.read_parquet(files[0])

            # Compute volume profile in rolling 100-bar windows
            # POC = price level with highest cumulative volume
            poc_list = []
            poc_strength_list = []

            for t in range(len(df) - 100):
                window = df.iloc[t:t+100]

                # Create price buckets (10 buckets per bar average range)
                price_range = window['high'].max() - window['low'].min()
                if price_range == 0:
                    continue

                n_buckets = max(20, int(price_range / (window['close'].mean() * 0.01)))
                buckets = np.linspace(window['low'].min(), window['high'].max(), n_buckets)

                # Assign volume to buckets (simplified: volume at bar close)
                bucket_volume = np.zeros(n_buckets - 1)
                for idx, row in window.iterrows():
                    bucket_idx = np.searchsorted(buckets, row['close']) - 1
                    if 0 <= bucket_idx < len(bucket_volume):
                        bucket_volume[bucket_idx] += row['volume']

                if bucket_volume.sum() == 0:
                    continue

                # POC = bucket center with highest volume
                poc_bucket_idx = np.argmax(bucket_volume)
                poc_price = (buckets[poc_bucket_idx] + buckets[poc_bucket_idx + 1]) / 2.0
                poc_strength = bucket_volume[poc_bucket_idx] / bucket_volume.sum()

                poc_list.append(poc_price)
                poc_strength_list.append(poc_strength)

            if poc_list:
                df['poc'] = np.nan
                df.iloc[100:100+len(poc_list), df.columns.get_loc('poc')] = poc_list
                df['poc_strength'] = np.nan
                df.iloc[100:100+len(poc_strength_list), df.columns.get_loc('poc_strength')] = poc_strength_list
                df['poc'] = df['poc'].fillna(method='bfill').fillna(method='ffill')
                df['poc_strength'] = df['poc_strength'].fillna(method='bfill').fillna(method='ffill')

                output_path = f'revision9_volume/{phase}/{symbol}_poc.parquet'
                df.to_parquet(output_path)
                success_count += 1
                logger.info(f"  ✅ {symbol}: computed POC")

        except Exception as e:
            logger.warning(f"  ⚠️  {symbol}: {str(e)[:50]}")

    logger.info(f"✅ Computed POC for {success_count}/{len(SYMBOLS)} symbols\n")

def generate_poc_labels(phase='train'):
    """Generate labels from POC mean-reversion"""
    logger.info(f"PHASE 2: Generating POC mean-reversion labels for {phase}")

    all_labels = []
    all_features = []

    for symbol in SYMBOLS:
        try:
            poc_file = Path(f'revision9_volume/{phase}/{symbol}_poc.parquet')
            if not poc_file.exists():
                continue

            df = pd.read_parquet(poc_file)
            if len(df) < HORIZON_BARS + 50:
                continue

            # ATR (14-bar)
            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - df['close'].shift()).abs(),
                (df['low'] - df['close'].shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(14, min_periods=1).mean()

            # Features
            dist_to_poc = (df['close'] - df['poc']) / (atr + 1e-8)
            poc_strength = df['poc_strength'].fillna(0.5)

            # Volume imbalance (simple: up volume vs down volume)
            returns = df['close'].diff()
            vol_imbal = np.where(
                returns > 0,
                df['volume'],
                -df['volume']
            ).rolling(20, min_periods=5).sum() / (df['volume'].rolling(20, min_periods=5).sum() + 1e-8)

            # Generate labels
            labels = np.full(len(df), np.nan)

            for t in range(len(df) - HORIZON_BARS - 1):
                # Entry when price is extreme relative to POC (±2 ATR)
                if abs(dist_to_poc.iloc[t]) < POC_ENTRY_DIST:
                    continue

                entry_bar = t + 1
                entry_px = df['close'].iloc[entry_bar]
                poc_px = df['poc'].iloc[t]

                exit_state = 0
                for idx in range(entry_bar, min(entry_bar + HORIZON_BARS, len(df))):
                    # Snap to POC within 0.5 ATR
                    if abs(df['close'].iloc[idx] - poc_px) < 0.5 * atr.iloc[t]:
                        exit_state = 1
                        break

                labels[entry_bar] = float(exit_state)

            # Collect valid
            valid = ~np.isnan(labels) & dist_to_poc.notna().values & poc_strength.notna().values & pd.Series(vol_imbal).notna().values
            if valid.sum() > 0:
                features = np.column_stack([
                    dist_to_poc[valid].values,
                    poc_strength[valid].values,
                    pd.Series(vol_imbal)[valid].values
                ])
                all_features.append(features)
                all_labels.extend(labels[valid])

        except Exception as e:
            logger.warning(f"  ⚠️  {symbol}: {str(e)[:50]}")

    if all_features:
        X = np.vstack(all_features)
        y = np.array(all_labels)

        # Clean NaN
        mask = ~np.isnan(y)
        X = X[mask]
        y = y[mask]

        logger.info(f"Generated {len(y):,} POC labels | Win rate: {y.mean()*100:.2f}%")

        np.save(f'revision9_volume/{phase}_X.npy', X)
        np.save(f'revision9_volume/{phase}_y.npy', y)
        logger.info(f"✅ Saved POC training data\n")

def train_model():
    """Train frozen POC model"""
    logger.info("PHASE 3: Training frozen POC model (TRAIN)")

    X_train = np.load('revision9_volume/train_X.npy')
    y_train = np.load('revision9_volume/train_y.npy')

    logger.info(f"Training samples: {len(y_train):,}")

    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    # Train
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=4,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train_scaled, y_train)

    Path('revision9_volume').mkdir(parents=True, exist_ok=True)
    with open('revision9_volume/model.pkl', 'wb') as f:
        pickle.dump((model, scaler), f)

    logger.info(f"✅ POC model trained and frozen\n")

def validation_gate():
    """Run validation gate on April 2026"""
    logger.info("PHASE 4: VALIDATION GATE (April 2026)")

    with open('revision9_volume/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_val = np.load('revision9_volume/validation_X.npy')
    y_val = np.load('revision9_volume/validation_y.npy')

    X_val_scaled = scaler.transform(X_val)
    scores = model.predict_proba(X_val_scaled)[:, 1]

    threshold = np.percentile(scores, 99.0)
    top1_mask = scores >= threshold

    win_rate = y_val[top1_mask].mean() * 100 if top1_mask.sum() > 0 else 0.0
    gross_R = (win_rate/100 * 1.5) - ((100-win_rate)/100 * 1.0)  # 1.5R target, 1.0R stop
    net_bps = (gross_R - FRICTION_R) * 30.0

    passes_hurdle = win_rate > HURDLE_RATE * 100
    passes_pnl = net_bps > 0.0

    logger.info(f"Trades: {top1_mask.sum():,} | Win Rate: {win_rate:.2f}% | Net P&L: {net_bps:.2f} bps")
    logger.info(f"Hurdle (≥50.80%): {win_rate:.2f}% {'✅ PASS' if passes_hurdle else '❌ FAIL'}")
    logger.info(f"Min P&L (>0.0 bps): {net_bps:.2f} bps {'✅ PASS' if passes_pnl else '❌ FAIL'}")

    if passes_hurdle and passes_pnl:
        logger.info(f"\n✅ VALIDATION PASSED - Proceed to TEST\n")
    else:
        logger.info(f"\n❌ VALIDATION FAILED - STOP\n")

def test_gate():
    """Run kill-switch test on May 2026"""
    logger.info("PHASE 5: KILL-SWITCH TEST (May 2026)")

    with open('revision9_volume/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_test = np.load('revision9_volume/test_X.npy')
    y_test = np.load('revision9_volume/test_y.npy')

    X_test_scaled = scaler.transform(X_test)
    scores = model.predict_proba(X_test_scaled)[:, 1]

    threshold = np.percentile(scores, 99.0)
    top1_mask = scores >= threshold

    win_rate = y_test[top1_mask].mean() * 100 if top1_mask.sum() > 0 else 0.0
    gross_R = (win_rate/100 * 1.5) - ((100-win_rate)/100 * 1.0)
    net_bps = (gross_R - FRICTION_R) * 30.0

    passes_hurdle = win_rate > HURDLE_RATE * 100
    passes_pnl = net_bps > 0.0

    logger.info(f"Trades: {top1_mask.sum():,} | Win Rate: {win_rate:.2f}% | Net P&L: {net_bps:.2f} bps")
    logger.info(f"Hurdle (≥50.80%): {win_rate:.2f}% {'✅ PASS' if passes_hurdle else '❌ FAIL'}")
    logger.info(f"Min P&L (>0.0 bps): {net_bps:.2f} bps {'✅ PASS' if passes_pnl else '❌ FAIL'}")

    if passes_hurdle and passes_pnl:
        logger.info(f"\n✅✅ KILL-SWITCH PASSED - UNLOCK JUNE SEALED\n")
    else:
        logger.info(f"\n❌ KILL-SWITCH FAILED - STOP (Do not touch June)\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['poc_computation', 'label_generation', 'model_training', 'validation_gate', 'test_gate'], required=True)
    args = parser.parse_args()

    if args.phase == 'poc_computation':
        compute_volume_profiles('train')
        compute_volume_profiles('validation')
        compute_volume_profiles('test')
    elif args.phase == 'label_generation':
        generate_poc_labels('train')
        generate_poc_labels('validation')
        generate_poc_labels('test')
    elif args.phase == 'model_training':
        train_model()
    elif args.phase == 'validation_gate':
        validation_gate()
    elif args.phase == 'test_gate':
        test_gate()
