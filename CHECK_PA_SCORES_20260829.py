#!/usr/bin/env python3
"""Check PA scores for 2026-08-13"""

import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

LEARNED_WEIGHTS = {
    'momentum': 0.1847,
    'rsi': 0.1963,
    'macd': 0.2163,
    'volume_ratio': 0.2152,
    'roc': 0.1847,
}

def calc_pa_score(row, history):
    close = row['close']

    momentum = (close - history['close'].iloc[-20]) / history['close'].iloc[-20]
    momentum_score = np.clip(momentum + 0.5, 0, 1)

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

    if len(history) >= 26:
        ema12 = history['close'].ewm(span=12).mean().iloc[-1]
        ema26 = history['close'].ewm(span=26).mean().iloc[-1]
        macd = ema12 - ema26
        macd_score = np.clip(macd / 10 + 0.5, 0, 1)
    else:
        macd_score = 0.5

    volume_ratio = row['volume'] / (history['volume'].mean() + 1e-6)
    volume_score = np.clip(volume_ratio / 2, 0, 1)

    roc = (close - history['close'].iloc[-1]) / history['close'].iloc[-1]
    roc_score = np.clip(roc + 0.5, 0, 1)

    pa_score = (
        momentum_score * LEARNED_WEIGHTS['momentum'] +
        rsi_score * LEARNED_WEIGHTS['rsi'] +
        macd_score * LEARNED_WEIGHTS['macd'] +
        volume_score * LEARNED_WEIGHTS['volume_ratio'] +
        roc_score * LEARNED_WEIGHTS['roc']
    )

    return np.clip(pa_score, 0, 1)

print("="*80)
print("PA SCORE CHECK: 2026-08-13")
print("="*80 + "\n")

df = pd.read_csv(DATA_DIR / "NSE_INFY_15minute_2023-08-14_2026-08-16.csv")
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
df = df.sort_values('timestamp').reset_index(drop=True)

test_date = pd.Timestamp('2026-08-13', tz='Asia/Kolkata')
df_day = df[(df['timestamp'].dt.date == test_date.date())].copy()

print(f"INFY on 2026-08-13: {len(df_day)} bars\n")

if len(df_day) > 50:
    print(f"{'Time':25} {'PA Score':12} {'Meets 0.5?':12}")
    print("-" * 50)

    pa_scores = []
    for idx in range(50, len(df_day)):
        row = df_day.iloc[idx]
        history = df_day.iloc[max(0, idx-50):idx]

        pa_score = calc_pa_score(row, history)
        pa_scores.append(pa_score)

        meets_threshold = pa_score >= 0.50
        print(f"{str(row['timestamp'])[-8:]:25} {pa_score:12.3f} {'✓' if meets_threshold else '✗':>12}")

    print(f"\nPA Score Stats:")
    print(f"  Min:  {min(pa_scores):.3f}")
    print(f"  Max:  {max(pa_scores):.3f}")
    print(f"  Mean: {np.mean(pa_scores):.3f}")
    print(f"  ≥0.50: {sum(1 for s in pa_scores if s >= 0.50)} bars")
    print(f"  ≥0.45: {sum(1 for s in pa_scores if s >= 0.45)} bars")
    print(f"  ≥0.40: {sum(1 for s in pa_scores if s >= 0.40)} bars")
else:
    print("Not enough data for calculation")
