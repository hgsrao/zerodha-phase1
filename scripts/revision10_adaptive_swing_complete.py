#!/usr/bin/env python3
"""
REVISION 10: ADAPTIVE SWING TRADING ENGINE
===========================================
Fixes: Revision 7-9 failures by solving three core issues:

1. Realistic Targets (1.5R/1.0R based on actual daily moves, not aggressive)
2. Regime-Aware Entry (trend confirmation: price > 20-day MA)
3. Strong Features (volatility regime + macro gating + momentum + structure)

Execution:
  python3 scripts/revision10_adaptive_swing_complete.py --phase feature_extraction
  python3 scripts/revision10_adaptive_swing_complete.py --phase label_generation
  python3 scripts/revision10_adaptive_swing_complete.py --phase model_training
  python3 scripts/revision10_adaptive_swing_complete.py --phase validation_gate
  python3 scripts/revision10_adaptive_swing_complete.py --phase test_gate
"""

import glob, pickle, os, sys, argparse
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# CONFIGURATION
FEATURE_COLS = ['trend_filter', 'volatility_regime', 'momentum_5d', 'atr_ratio', 'breadth_signal']
TARGET_MULT, STOP_MULT = 1.5, 1.0  # Realistic targets (median 5d move ≈ 0.9R, targeting 1.5R/1.0R)
HORIZON_DAYS = 5
FRICTION_R = 0.27
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

def extract_daily_features_adaptive(phase='train'):
    """Extract daily features with regime-aware engineering"""
    logger.info(f"PHASE 1: Extracting adaptive daily features for {phase}")

    Path(f"revision10_adaptive/{phase}").mkdir(parents=True, exist_ok=True)

    success_count = 0
    for symbol in SYMBOLS:
        try:
            files = glob.glob(f'revision2/features_2026/{phase}/*{symbol}*.parquet')
            if not files:
                continue

            df_1m = pd.read_parquet(files[0])

            # Handle timezone
            if df_1m.index.tz is not None:
                df_1m.index = df_1m.index.tz_localize(None)

            # Aggregate to daily
            daily = df_1m.resample('D').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()

            if len(daily) < 10:
                continue

            df = daily.copy()

            # ATR (14-day)
            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - df['close'].shift()).abs(),
                (df['low'] - df['close'].shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(14, min_periods=1).mean()

            # FEATURE 1: Trend Filter (1 if price > 20d MA, -1 if price < 20d MA, 0 otherwise)
            sma20 = df['close'].rolling(20, min_periods=5).mean()
            trend_filter = pd.Series(np.sign((df['close'] - sma20).values), index=df.index).fillna(0)

            # FEATURE 2: Volatility Regime (normalized 30-day realized vol)
            log_ret = pd.Series(np.log((df['close'] / df['close'].shift(1)).values), index=df.index)
            realized_vol = log_ret.rolling(30, min_periods=5).std()
            vol_percentile = realized_vol.rolling(60, min_periods=10).apply(
                lambda x: (realized_vol.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-8)
            ).fillna(0.5)
            volatility_regime = vol_percentile.clip(0, 1)

            # FEATURE 3: Momentum (5-day returns)
            momentum_5d = (df['close'] / df['close'].shift(5) - 1).fillna(0)

            # FEATURE 4: ATR Ratio (current ATR / 30-day avg ATR, normalized)
            atr_avg = atr.rolling(30, min_periods=5).mean()
            atr_ratio = (atr / (atr_avg + 1e-8)).clip(0.5, 2.0)

            # FEATURE 5: Breadth Signal (from intraday 1-minute data - approximate)
            # Use volume to estimate market stress: high volume on down days = stress
            returns = df['close'].diff()
            down_vol = pd.Series(np.where(returns < 0, df['volume'].values, 0), index=df.index)
            down_vol_ratio = down_vol.rolling(15, min_periods=5).sum() / (df['volume'].rolling(15, min_periods=5).sum() + 1e-8)
            breadth_signal = 1 - down_vol_ratio.clip(0, 1)  # 1 = bullish, 0 = bearish

            # Save
            output_path = f'revision10_adaptive/{phase}/{symbol}_adaptive.parquet'
            df_out = pd.DataFrame({
                'open': df['open'],
                'high': df['high'],
                'low': df['low'],
                'close': df['close'],
                'volume': df['volume'],
                'trend_filter': trend_filter,
                'volatility_regime': volatility_regime,
                'momentum_5d': momentum_5d,
                'atr_ratio': atr_ratio,
                'breadth_signal': breadth_signal
            })
            df_out.to_parquet(output_path)
            success_count += 1
            logger.info(f"  ✅ {symbol}: {len(daily)} daily bars")
        except Exception as e:
            logger.warning(f"  ⚠️  {symbol}: {str(e)[:50]}")

    logger.info(f"✅ Extracted {success_count}/{len(SYMBOLS)} symbols\n")

def generate_adaptive_labels(phase='train'):
    """Generate labels with trend confirmation + realistic targets"""
    logger.info(f"PHASE 2: Generating adaptive labels for {phase}")

    all_labels = []
    all_features = []

    for symbol in SYMBOLS:
        try:
            df = pd.read_parquet(f'revision10_adaptive/{phase}/{symbol}_adaptive.parquet')
            if len(df) < HORIZON_DAYS + 10:
                continue

            n = len(df)
            labels = np.full(n, np.nan)

            # ATR for targets
            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - df['close'].shift()).abs(),
                (df['low'] - df['close'].shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(14, min_periods=1).mean().values

            for t in range(n - HORIZON_DAYS - 1):
                # ENTRY FILTER: Only enter if trend_filter is positive (price > 20d MA)
                if df['trend_filter'].iloc[t] <= 0:
                    continue

                entry_bar = t + 1
                if entry_bar >= n or pd.isna(atr[t]) or atr[t] == 0:
                    continue

                entry_px = df['open'].iloc[entry_bar]
                target_px = entry_px + (TARGET_MULT * atr[t])
                stop_px = entry_px - (STOP_MULT * atr[t])

                exit_state = 0
                for idx in range(entry_bar, min(entry_bar + HORIZON_DAYS, n)):
                    if df['high'].iloc[idx] >= target_px:
                        exit_state = 1
                        break
                    if df['low'].iloc[idx] <= stop_px:
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

        np.save(f'revision10_adaptive/{phase}_X.npy', X)
        np.save(f'revision10_adaptive/{phase}_y.npy', y)
        logger.info(f"✅ Saved training data\n")

def train_model():
    """Train frozen model on TRAIN split"""
    logger.info("PHASE 3: Training frozen model (TRAIN)")

    X_train = np.load('revision10_adaptive/train_X.npy')
    y_train = np.load('revision10_adaptive/train_y.npy')

    logger.info(f"Training samples: {len(y_train):,}")

    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    # Train with Gradient Boosting (better for mixed feature types)
    model = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        random_state=42
    )
    model.fit(X_train_scaled, y_train)

    Path('revision10_adaptive').mkdir(parents=True, exist_ok=True)
    with open('revision10_adaptive/model.pkl', 'wb') as f:
        pickle.dump((model, scaler), f)

    logger.info(f"✅ Model trained and frozen\n")

def validation_gate():
    """Run validation gate on April 2026"""
    logger.info("PHASE 4: VALIDATION GATE (April 2026)")

    with open('revision10_adaptive/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_val = np.load('revision10_adaptive/validation_X.npy')
    y_val = np.load('revision10_adaptive/validation_y.npy')

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

    with open('revision10_adaptive/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_test = np.load('revision10_adaptive/test_X.npy')
    y_test = np.load('revision10_adaptive/test_y.npy')

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
        extract_daily_features_adaptive('train')
        extract_daily_features_adaptive('validation')
        extract_daily_features_adaptive('test')
    elif args.phase == 'label_generation':
        generate_adaptive_labels('train')
        generate_adaptive_labels('validation')
        generate_adaptive_labels('test')
    elif args.phase == 'model_training':
        train_model()
    elif args.phase == 'validation_gate':
        validation_gate()
    elif args.phase == 'test_gate':
        test_gate()
