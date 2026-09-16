#!/usr/bin/env python3
"""
REVISION 7: DAILY/WEEKLY SWING TRADING ENGINE
==============================================
Solves: Friction drag (0.27R becomes negligible on 3-5R daily moves)

Execution:
  python3 scripts/revision7_daily_swing_complete.py --phase feature_extraction
  python3 scripts/revision7_daily_swing_complete.py --phase label_generation
  python3 scripts/revision7_daily_swing_complete.py --phase model_training
  python3 scripts/revision7_daily_swing_complete.py --phase validation_gate
  python3 scripts/revision7_daily_swing_complete.py --phase test_gate
"""

import glob, pickle, os, sys, argparse
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# CONFIGURATION
FEATURE_COLS = ['atr_14d', 'vwap_dist_atr', 'trend_4d', 'realized_vol']
TARGET_MULT, STOP_MULT = 1.2, 1.0  # Realistic daily swing targets (median 5d move ≈ 0.7R, targeting 1.2R/1.0R)
HORIZON_DAYS = 5  # 5-day holding period
FRICTION_R = 0.27  # Round-trip (8 bps)
HURDLE_RATE = 0.508

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

def extract_daily_features(phase='train'):
    """Extract daily OHLCV and compute technical features"""
    logger.info(f"PHASE 1: Extracting daily features for {phase}")

    Path(f"revision7_daily/{phase}").mkdir(parents=True, exist_ok=True)

    success_count = 0
    for symbol in SYMBOLS:
        try:
            # Load 1-minute features
            files = glob.glob(f'revision2/features_2026/{phase}/*{symbol}*.parquet')
            if not files:
                continue

            df_1m = pd.read_parquet(files[0])

            # Handle timezone-aware index
            if df_1m.index.tz is not None:
                df_1m.index = df_1m.index.tz_localize(None)

            # Aggregate to daily (simple: use close timestamp to group)
            daily = df_1m.resample('D').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()

            if len(daily) < 10:  # Validation/test splits are shorter (1 month)
                continue

            # Compute daily features
            df = daily.copy()

            # ATR (14-day)
            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - df['close'].shift()).abs(),
                (df['low'] - df['close'].shift()).abs()
            ], axis=1).max(axis=1)
            df['atr_14d'] = tr.rolling(14, min_periods=1).mean()

            # VWAP distance (20-day)
            typical = (df['high'] + df['low'] + df['close']) / 3.0
            vwap = (typical * df['volume']).rolling(20, min_periods=5).sum() / df['volume'].rolling(20, min_periods=5).sum()
            df['vwap_dist_atr'] = ((df['close'] - vwap) / df['atr_14d'].replace(0, 1)).clip(-5, 5)

            # Trend (4-day)
            df['trend_4d'] = np.sign(df['close'].diff(4)).fillna(0)

            # Realized volatility (20-day)
            log_ret = np.log(df['close'] / df['close'].shift(1))
            df['realized_vol'] = log_ret.rolling(20, min_periods=5).std()

            # Save
            output_path = f'revision7_daily/{phase}/{symbol}_daily.parquet'
            df[['open', 'high', 'low', 'close'] + FEATURE_COLS].to_parquet(output_path)
            success_count += 1
            logger.info(f"  ✅ {symbol}: {len(daily)} daily bars")
        except Exception as e:
            logger.warning(f"  ⚠️  {symbol}: {str(e)[:50]}")

    logger.info(f"✅ Extracted {success_count}/{len(SYMBOLS)} symbols\n")

def generate_triple_barrier_labels(phase='train'):
    """Generate 5-day triple-barrier labels"""
    logger.info(f"PHASE 2: Generating triple-barrier labels for {phase}")

    all_labels = []
    all_features = []

    for symbol in SYMBOLS:
        try:
            df = pd.read_parquet(f'revision7_daily/{phase}/{symbol}_daily.parquet')
            if len(df) < HORIZON_DAYS + 10:
                continue

            atr = df['atr_14d'].values
            highs = df['high'].values
            lows = df['low'].values
            opens = df['open'].values
            n = len(df)

            labels = np.full(n, np.nan)

            for t in range(n - HORIZON_DAYS - 1):
                entry_bar = t + 1
                if entry_bar >= n or pd.isna(atr[t]) or atr[t] == 0:
                    continue

                entry_px = opens[entry_bar]
                target_px = entry_px + (TARGET_MULT * atr[t])
                stop_px = entry_px - (STOP_MULT * atr[t])

                exit_state = 0
                for idx in range(entry_bar, min(entry_bar + HORIZON_DAYS, n)):
                    if highs[idx] >= target_px:
                        exit_state = 1
                        break
                    if lows[idx] <= stop_px:
                        exit_state = 0
                        break

                labels[entry_bar] = float(exit_state)

            # Collect
            valid = ~np.isnan(labels) & df[FEATURE_COLS].notna().all(axis=1).values
            if valid.sum() > 0:
                all_labels.extend(labels[valid])
                all_features.append(df[valid][FEATURE_COLS].values)
        except Exception as e:
            logger.warning(f"  ⚠️  {symbol}: {str(e)[:50]}")

    if all_features:
        X = np.vstack(all_features)
        y = np.array(all_labels)

        logger.info(f"Generated {len(y):,} labels | Win rate: {y.mean()*100:.2f}%")

        np.save(f'revision7_daily/{phase}_X.npy', X)
        np.save(f'revision7_daily/{phase}_y.npy', y)
        logger.info(f"✅ Saved training data\n")

def train_model():
    """Train frozen model on TRAIN split"""
    logger.info("PHASE 3: Training frozen model (TRAIN)")

    X_train = np.load('revision7_daily/train_X.npy')
    y_train = np.load('revision7_daily/train_y.npy')

    logger.info(f"Training samples: {len(y_train):,}")

    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    # Train
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=5,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train_scaled, y_train)

    Path('revision7_daily').mkdir(parents=True, exist_ok=True)
    with open('revision7_daily/model.pkl', 'wb') as f:
        pickle.dump((model, scaler), f)

    logger.info(f"✅ Model trained and frozen\n")

def validation_gate():
    """Run validation gate on April 2026"""
    logger.info("PHASE 4: VALIDATION GATE (April 2026)")

    with open('revision7_daily/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_val = np.load('revision7_daily/validation_X.npy')
    y_val = np.load('revision7_daily/validation_y.npy')

    X_val_scaled = scaler.transform(X_val)
    scores = model.predict_proba(X_val_scaled)[:, 1]

    threshold = np.percentile(scores, 99.0)
    top1_mask = scores >= threshold

    win_rate = y_val[top1_mask].mean() * 100 if top1_mask.sum() > 0 else 0.0
    gross_R = (win_rate/100 * TARGET_MULT) - ((100-win_rate)/100 * STOP_MULT)
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

    with open('revision7_daily/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_test = np.load('revision7_daily/test_X.npy')
    y_test = np.load('revision7_daily/test_y.npy')

    X_test_scaled = scaler.transform(X_test)
    scores = model.predict_proba(X_test_scaled)[:, 1]

    threshold = np.percentile(scores, 99.0)
    top1_mask = scores >= threshold

    win_rate = y_test[top1_mask].mean() * 100 if top1_mask.sum() > 0 else 0.0
    gross_R = (win_rate/100 * TARGET_MULT) - ((100-win_rate)/100 * STOP_MULT)
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
    parser.add_argument('--phase', choices=['feature_extraction', 'label_generation', 'model_training', 'validation_gate', 'test_gate'], required=True)
    args = parser.parse_args()

    if args.phase == 'feature_extraction':
        extract_daily_features('train')
        extract_daily_features('validation')
        extract_daily_features('test')
    elif args.phase == 'label_generation':
        generate_triple_barrier_labels('train')
        generate_triple_barrier_labels('validation')
        generate_triple_barrier_labels('test')
    elif args.phase == 'model_training':
        train_model()
    elif args.phase == 'validation_gate':
        validation_gate()
    elif args.phase == 'test_gate':
        test_gate()
