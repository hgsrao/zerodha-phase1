#!/usr/bin/env python3
"""
REVISION 8: PAIRS COINTEGRATION ENGINE
========================================
Solves: Directional regime exposure via market-neutral pair trading

Execution:
  python3 scripts/revision8_pairs_cointegration_complete.py --phase cointegration_compute
  python3 scripts/revision8_pairs_cointegration_complete.py --phase label_generation
  python3 scripts/revision8_pairs_cointegration_complete.py --phase model_training
  python3 scripts/revision8_pairs_cointegration_complete.py --phase validation_gate
  python3 scripts/revision8_pairs_cointegration_complete.py --phase test_gate
"""

import glob, pickle, os, sys, argparse
import pandas as pd
import numpy as np
from pathlib import Path
from itertools import combinations
from scipy.stats import linregress
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import GradientBoostingClassifier
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

FEATURE_COLS = ['spread_zscore', 'spread_vol', 'correlation_60d']
SPREAD_TARGET = 2.0  # ±2σ entry
MEAN_REVERSION_TARGET = 0.5  # Snap to mean
FRICTION_R = 0.27
HURDLE_RATE = 0.508
HORIZON_BARS = 30  # 30-bar holding period

def compute_cointegration_pairs(phase='train'):
    """Identify and compute cointegration relationships for all pairs"""
    logger.info(f"PHASE 1: Computing cointegration pairs for {phase}")

    Path(f"revision8_pairs/{phase}").mkdir(parents=True, exist_ok=True)

    # Load closes for all symbols
    closes_dict = {}
    for symbol in SYMBOLS:
        try:
            files = glob.glob(f'revision2/features_2026/{phase}/*{symbol}*.parquet')
            if files:
                df = pd.read_parquet(files[0])
                closes_dict[symbol] = df['close'].values
        except:
            pass

    if not closes_dict or len(closes_dict) < 2:
        logger.error("Not enough symbols loaded")
        return

    logger.info(f"  Loaded {len(closes_dict)} symbols")

    # Identify cointegrated pairs (simplified: high correlation + low relative vol)
    all_closes = pd.DataFrame(closes_dict)
    correlation_matrix = all_closes.corr()

    cointegrated_pairs = []
    symbols_list = list(closes_dict.keys())

    for i, sym1 in enumerate(symbols_list):
        for sym2 in symbols_list[i+1:]:
            corr = correlation_matrix.loc[sym1, sym2]

            # High correlation + not identical
            if 0.6 < corr < 0.99:
                # Compute cointegration score (simplified: correlation * relative vol smoothness)
                ret1 = np.diff(closes_dict[sym1]) / closes_dict[sym1][:-1]
                ret2 = np.diff(closes_dict[sym2]) / closes_dict[sym2][:-1]

                spread = closes_dict[sym1][1:] / closes_dict[sym2][1:] if closes_dict[sym2][0] != 0 else None
                if spread is not None and len(spread) > 30:
                    cointegrated_pairs.append({
                        'leader': sym1,
                        'laggard': sym2,
                        'correlation': corr,
                        'spread_mean': np.mean(spread),
                        'spread_std': np.std(spread)
                    })

    logger.info(f"  Found {len(cointegrated_pairs):,} cointegrated pairs")
    logger.info(f"✅ Cointegration matrix computed\n")

    # Save pairs
    with open(f'revision8_pairs/{phase}_pairs.pkl', 'wb') as f:
        pickle.dump(cointegrated_pairs, f)

def generate_pair_spread_labels(phase='train'):
    """Generate labels from pair spread mean-reversion"""
    logger.info(f"PHASE 2: Generating pair spread labels for {phase}")

    # Load cointegrated pairs (frozen from TRAIN)
    with open('revision8_pairs/train_pairs.pkl', 'rb') as f:
        pairs = pickle.load(f)

    all_labels = []
    all_features = []

    for pair_idx, pair in enumerate(pairs[:500]):  # Use top 500 pairs
        try:
            sym_leader = pair['leader']
            sym_laggard = pair['laggard']

            # Load closes
            files_leader = glob.glob(f'revision2/features_2026/{phase}/*{sym_leader}*.parquet')
            files_laggard = glob.glob(f'revision2/features_2026/{phase}/*{sym_laggard}*.parquet')

            if not (files_leader and files_laggard):
                continue

            close_leader = pd.read_parquet(files_leader[0])['close'].values
            close_laggard = pd.read_parquet(files_laggard[0])['close'].values

            if len(close_leader) < HORIZON_BARS + 50 or len(close_laggard) < HORIZON_BARS + 50:
                continue

            # Compute spread
            n = min(len(close_leader), len(close_laggard))
            spread = close_leader[:n] / np.where(close_laggard[:n] != 0, close_laggard[:n], 1)

            # Features
            spread_mean = np.mean(spread[-100:])
            spread_std = np.std(spread[-100:])
            spread_zscore = (spread - spread_mean) / (spread_std + 1e-8)

            spread_vol = np.std(np.diff(spread[-60:]))

            corr_60d = np.corrcoef(close_leader[-60:], close_laggard[-60:])[0, 1]

            # Generate labels
            labels = np.full(n, np.nan)

            for t in range(n - HORIZON_BARS - 1):
                if abs(spread_zscore[t]) < SPREAD_TARGET:
                    continue

                entry_bar = t + 1
                entry_spread = spread[entry_bar]

                exit_state = 0
                for idx in range(entry_bar, min(entry_bar + HORIZON_BARS, n)):
                    if abs(spread[idx] - spread_mean) < MEAN_REVERSION_TARGET * spread_std:
                        exit_state = 1
                        break

                labels[entry_bar] = float(exit_state)

            # Collect valid
            valid = ~np.isnan(labels)
            if valid.sum() > 0:
                pair_features = np.column_stack([
                    spread_zscore[valid],
                    np.full(valid.sum(), spread_vol),
                    np.full(valid.sum(), corr_60d)
                ])
                all_features.append(pair_features)
                all_labels.extend(labels[valid])

        except Exception as e:
            logger.warning(f"  ⚠️  Pair {pair_idx}: {str(e)[:50]}")

    if all_features:
        X = np.vstack(all_features)
        y = np.array(all_labels)

        # Clean NaN
        mask = ~np.isnan(y)
        X = X[mask]
        y = y[mask]

        logger.info(f"Generated {len(y):,} pair labels | Win rate: {y.mean()*100:.2f}%")

        np.save(f'revision8_pairs/{phase}_X.npy', X)
        np.save(f'revision8_pairs/{phase}_y.npy', y)
        logger.info(f"✅ Saved pair training data\n")

def train_model():
    """Train frozen pair model"""
    logger.info("PHASE 3: Training frozen pair model (TRAIN)")

    X_train = np.load('revision8_pairs/train_X.npy')
    y_train = np.load('revision8_pairs/train_y.npy')

    logger.info(f"Training samples: {len(y_train):,}")

    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    # Train
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.1,
        random_state=42
    )
    model.fit(X_train_scaled, y_train)

    Path('revision8_pairs').mkdir(parents=True, exist_ok=True)
    with open('revision8_pairs/model.pkl', 'wb') as f:
        pickle.dump((model, scaler), f)

    logger.info(f"✅ Pair model trained and frozen\n")

def validation_gate():
    """Run validation gate on April 2026"""
    logger.info("PHASE 4: VALIDATION GATE (April 2026)")

    with open('revision8_pairs/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_val = np.load('revision8_pairs/validation_X.npy')
    y_val = np.load('revision8_pairs/validation_y.npy')

    X_val_scaled = scaler.transform(X_val)
    scores = model.predict_proba(X_val_scaled)[:, 1]

    threshold = np.percentile(scores, 99.0)
    top1_mask = scores >= threshold

    win_rate = y_val[top1_mask].mean() * 100 if top1_mask.sum() > 0 else 0.0
    gross_R = (win_rate/100 * 1.0) - ((100-win_rate)/100 * 1.0)  # Market-neutral: symmetric
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

    with open('revision8_pairs/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_test = np.load('revision8_pairs/test_X.npy')
    y_test = np.load('revision8_pairs/test_y.npy')

    X_test_scaled = scaler.transform(X_test)
    scores = model.predict_proba(X_test_scaled)[:, 1]

    threshold = np.percentile(scores, 99.0)
    top1_mask = scores >= threshold

    win_rate = y_test[top1_mask].mean() * 100 if top1_mask.sum() > 0 else 0.0
    gross_R = (win_rate/100 * 1.0) - ((100-win_rate)/100 * 1.0)
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
    parser.add_argument('--phase', choices=['cointegration_compute', 'label_generation', 'model_training', 'validation_gate', 'test_gate'], required=True)
    args = parser.parse_args()

    if args.phase == 'cointegration_compute':
        # Only compute pairs from TRAIN (frozen)
        compute_cointegration_pairs('train')
        logger.info("✅ Pairs frozen from TRAIN split. VALIDATION/TEST will reuse these pairs.\n")
    elif args.phase == 'label_generation':
        generate_pair_spread_labels('train')
        generate_pair_spread_labels('validation')
        generate_pair_spread_labels('test')
    elif args.phase == 'model_training':
        train_model()
    elif args.phase == 'validation_gate':
        validation_gate()
    elif args.phase == 'test_gate':
        test_gate()
