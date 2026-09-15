#!/usr/bin/env python3
"""
DEBUG: Why no trades are executing?
"""

import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

# Load INFY data
df = pd.read_csv(DATA_DIR / "NSE_INFY_15minute_2023-08-14_2026-08-16.csv")
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
df = df.sort_values('timestamp').reset_index(drop=True)

print("\n" + "="*80)
print("DEBUG: Paper Trading Data Check")
print("="*80 + "\n")

print(f"Total rows in INFY: {len(df)}")
print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

# Check 2026-08-01
test_date = pd.Timestamp('2026-08-01', tz='Asia/Kolkata')
df_day = df[(df['timestamp'].dt.date == test_date.date())].copy()

print(f"\nFiltered to 2026-08-01:")
print(f"  Rows on that date: {len(df_day)}")
print(f"  Time range: {df_day['timestamp'].min()} to {df_day['timestamp'].max()}" if len(df_day) > 0 else "  No data")

# Sample PA calculation
if len(df_day) > 50:
    LEARNED_WEIGHTS = {
        'momentum': 0.1847,
        'rsi': 0.1963,
        'macd': 0.2163,
        'volume_ratio': 0.2152,
        'roc': 0.1847,
    }

    print("\n" + "-"*80)
    print("PA Score Calculation (Sample - Bar 50-60 of the day):")
    print("-"*80)

    for idx in range(50, min(60, len(df_day))):
        row = df_day.iloc[idx]
        history = df_day.iloc[max(0, idx-50):idx]

        close = row['close']

        # Momentum
        momentum = (close - history['close'].iloc[-20]) / history['close'].iloc[-20]
        momentum_score = np.clip(momentum + 0.5, 0, 1)

        # RSI
        if len(history) >= 14:
            gains = losses = 0
            for i in range(1, 15):
                ch = history['close'].iloc[-15+i] - history['close'].iloc[-16+i]
                if ch > 0:
                    gains += ch
                else:
                    losses += abs(ch)
            rs = gains / (losses + 1e-6)
            rsi = 100 - (100 / (1 + rs))
            rsi_score = rsi / 100.0
        else:
            rsi_score = 0.5

        # MACD
        if len(history) >= 26:
            ema12 = history['close'].ewm(span=12).mean().iloc[-1]
            ema26 = history['close'].ewm(span=26).mean().iloc[-1]
            macd = ema12 - ema26
            macd_score = np.clip(macd / 10 + 0.5, 0, 1)
        else:
            macd_score = 0.5

        # Volume
        volume_ratio = row['volume'] / (history['volume'].mean() + 1e-6)
        volume_score = np.clip(volume_ratio / 2, 0, 1)

        # ROC
        roc = (close - history['close'].iloc[-1]) / history['close'].iloc[-1]
        roc_score = np.clip(roc + 0.5, 0, 1)

        # Weighted score
        pa_score = (
            momentum_score * LEARNED_WEIGHTS['momentum'] +
            rsi_score * LEARNED_WEIGHTS['rsi'] +
            macd_score * LEARNED_WEIGHTS['macd'] +
            volume_score * LEARNED_WEIGHTS['volume_ratio'] +
            roc_score * LEARNED_WEIGHTS['roc']
        )

        meets_threshold = pa_score >= 0.50

        print(f"Bar {idx:2d} (09:15+{idx*15:3d}min) | PA={pa_score:.3f} | "
              f"Mom={momentum_score:.2f} RSI={rsi_score:.2f} MACD={macd_score:.2f} "
              f"Vol={volume_score:.2f} ROC={roc_score:.2f} | "
              f"{'✓ TRADE' if meets_threshold else '✗ SKIP'}")

    print(f"\nThreshold: 0.50")
    print(f"How many bars >= 0.50? → Count trades\n")
