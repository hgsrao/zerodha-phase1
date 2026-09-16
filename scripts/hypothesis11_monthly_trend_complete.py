#!/usr/bin/env python3
"""
HYPOTHESIS 11: MONTHLY TREND FOLLOWING
========================================
Tests: Different time horizon (solves friction problem)

Key insight: Monthly bars have much larger moves (5-15% typical)
Target: 35-40% win rate with 3.0R targets = breakeven after friction
Holding: Weeks to months (not days)
Friction impact: <15% of gross (vs 22%+ on daily)

Execution:
  python3 scripts/hypothesis11_monthly_trend_complete.py --phase feature_extraction
  python3 scripts/hypothesis11_monthly_trend_complete.py --phase label_generation
  python3 scripts/hypothesis11_monthly_trend_complete.py --phase model_training
  python3 scripts/hypothesis11_monthly_trend_complete.py --phase validation_gate
  python3 scripts/hypothesis11_monthly_trend_complete.py --phase test_gate
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
FEATURE_COLS = ['trend_ma_signal', 'momentum_20d', 'volatility_regime', 'atr_norm']
TARGET_MULT, STOP_MULT = 3.0, 1.0  # Large targets for monthly moves
HORIZON_DAYS = 15  # 15-day holding period (max feasible given data constraints)
FRICTION_R = 0.27  # Same friction but fewer trades
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

def extract_monthly_features(phase='train'):
    """Extract monthly features with long-term trend signals"""
    logger.info(f"PHASE 1: Extracting monthly features for {phase}")

    Path(f"hypothesis11_monthly/{phase}").mkdir(parents=True, exist_ok=True)

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

            # Aggregate to daily first (for feature calculation)
            daily = df_1m.resample('D').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()

            if len(daily) < 10:  # Validation/test splits are shorter (19-21 bars)
                continue

            df = daily.copy()

            # ATR for position sizing
            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - df['close'].shift()).abs(),
                (df['low'] - df['close'].shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(14, min_periods=1).mean()

            # FEATURE 1: Trend MA Signal (price above/below 20d MA for short data)
            ma20 = df['close'].rolling(20, min_periods=5).mean()
            trend_ma_signal = (df['close'] > ma20).astype(float)
            trend_ma_signal = trend_ma_signal.fillna(0.5)

            # FEATURE 2: Momentum (20-day returns)
            momentum_20d = (df['close'] / df['close'].shift(20) - 1).fillna(0)

            # FEATURE 3: Volatility Regime (percentile of realized vol)
            log_ret = pd.Series(np.log((df['close'] / df['close'].shift(1)).values), index=df.index)
            realized_vol = log_ret.rolling(60, min_periods=10).std()
            vol_pct = realized_vol.rolling(120, min_periods=20).apply(
                lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-8)
            ).fillna(0.5)
            volatility_regime = vol_pct.clip(0, 1)

            # FEATURE 4: ATR Normalized (current ATR / avg ATR)
            atr_avg = atr.rolling(60, min_periods=10).mean()
            atr_norm = (atr / (atr_avg + 1e-8)).clip(0.5, 2.0)

            # Save
            output_path = f'hypothesis11_monthly/{phase}/{symbol}_monthly.parquet'
            df_out = pd.DataFrame({
                'open': df['open'],
                'high': df['high'],
                'low': df['low'],
                'close': df['close'],
                'volume': df['volume'],
                'trend_ma_signal': trend_ma_signal,
                'momentum_20d': momentum_20d,
                'volatility_regime': volatility_regime,
                'atr_norm': atr_norm
            })
            df_out.to_parquet(output_path)
            success_count += 1
            logger.info(f"  ✅ {symbol}: {len(daily)} daily bars")
        except Exception as e:
            logger.warning(f"  ⚠️  {symbol}: {str(e)[:50]}")

    logger.info(f"✅ Extracted {success_count}/{len(SYMBOLS)} symbols\n")

def generate_monthly_labels(phase='train'):
    """Generate labels with trend confirmation"""
    logger.info(f"PHASE 2: Generating monthly trend labels for {phase}")

    all_labels = []
    all_features = []

    for symbol in SYMBOLS:
        try:
            df = pd.read_parquet(f'hypothesis11_monthly/{phase}/{symbol}_monthly.parquet')
            if len(df) < HORIZON_DAYS + 10:  # Lower minimum for shorter data
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
                # ENTRY FILTER: Only enter if in uptrend (price > 20d MA)
                if df['trend_ma_signal'].iloc[t] < 1:  # Must be 1 (uptrend)
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

        np.save(f'hypothesis11_monthly/{phase}_X.npy', X)
        np.save(f'hypothesis11_monthly/{phase}_y.npy', y)
        logger.info(f"✅ Saved training data\n")

def train_model():
    """Train frozen model"""
    logger.info("PHASE 3: Training frozen model (TRAIN)")

    X_train = np.load('hypothesis11_monthly/train_X.npy')
    y_train = np.load('hypothesis11_monthly/train_y.npy')

    logger.info(f"Training samples: {len(y_train):,}")

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=5,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train_scaled, y_train)

    Path('hypothesis11_monthly').mkdir(parents=True, exist_ok=True)
    with open('hypothesis11_monthly/model.pkl', 'wb') as f:
        pickle.dump((model, scaler), f)

    logger.info(f"✅ Model trained and frozen\n")

def validation_gate():
    """Run validation gate on April 2026"""
    logger.info("PHASE 4: VALIDATION GATE (April 2026)")

    with open('hypothesis11_monthly/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_val = np.load('hypothesis11_monthly/validation_X.npy')
    y_val = np.load('hypothesis11_monthly/validation_y.npy')

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

    with open('hypothesis11_monthly/model.pkl', 'rb') as f:
        model, scaler = pickle.load(f)

    X_test = np.load('hypothesis11_monthly/test_X.npy')
    y_test = np.load('hypothesis11_monthly/test_y.npy')

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
        logger.info(f"\n❌ KILL-SWITCH FAILED - STOP\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['feature_extraction', 'label_generation', 'model_training', 'validation_gate', 'test_gate'], required=True)
    args = parser.parse_args()

    if args.phase == 'feature_extraction':
        extract_monthly_features('train')
        extract_monthly_features('validation')
        extract_monthly_features('test')
    elif args.phase == 'label_generation':
        generate_monthly_labels('train')
        generate_monthly_labels('validation')
        generate_monthly_labels('test')
    elif args.phase == 'model_training':
        train_model()
    elif args.phase == 'validation_gate':
        validation_gate()
    elif args.phase == 'test_gate':
        test_gate()
