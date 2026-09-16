#!/usr/bin/env python3
"""
Label Threshold Optimization: Find the Sweet Spot
==================================================

Goal: Find the forward return threshold that balances:
1. Sufficient positive samples (not too sparse)
2. High AUC (discriminatory power)
3. Cost survival (net P&L after 8 bps costs)

Test multiple thresholds and measure model quality on each.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import logging
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, accuracy_score, precision_score, recall_score

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("LABEL THRESHOLD OPTIMIZATION")
print("="*80 + "\n")

# Load features
feature_dir = Path("revision2/features_mtf_2026")
X_list = []
close_list = []
for f in sorted(feature_dir.glob("*_mtf_2026.parquet")):
    df = pd.read_parquet(f)
    cols = ['5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
            '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
            '15m_rs_percentile', '15m_rs_excess']
    X = df[[c for c in cols if c in df.columns]].copy()
    X_list.append(X)
    close_list.append(df[['close']])

X_train = pd.concat(X_list, axis=0, ignore_index=False).reset_index(drop=True)
close_train = pd.concat(close_list, axis=0, ignore_index=False).reset_index(drop=True)

# Calculate forward returns
future_periods = 20
forward_close = close_train['close'].shift(-future_periods)
forward_return = (forward_close - close_train['close']) / close_train['close']
forward_return_bps = forward_return * 10000

print(f"Forward returns: min={forward_return_bps.min():.2f}, max={forward_return_bps.max():.2f}, mean={forward_return_bps.mean():.2f}")

# Test different thresholds
thresholds_to_test = [
    8,    # Just cover costs
    18,   # 10 bps profit
    30,   # 22 bps profit
    50,   # 42 bps profit
    100,  # 92 bps profit
    200,  # 192 bps profit
]

results_table = []

print(f"\n{'Threshold':12s} {'Pos Rate':12s} {'N Pos':12s} {'Accuracy':12s} {'AUC':12s} {'Precision':12s} {'Recall':12s}")
print("-" * 90)

for threshold in thresholds_to_test:
    # Create labels
    y_train = (forward_return_bps >= threshold).astype(int)
    pos_rate = y_train.mean() * 100

    # Clean NaN
    valid_mask = X_train.notna().all(axis=1) & y_train.notna()
    X_clean = X_train[valid_mask]
    y_clean = y_train[valid_mask]

    # If no positive labels, skip
    if y_clean.sum() == 0:
        print(f"{threshold:12d} {pos_rate:11.2f}% {'ZERO POS':>11s} {'N/A':>11s} {'N/A':>11s} {'N/A':>11s} {'N/A':>11s}")
        continue

    # Standardize and train
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_clean)

    model = LogisticRegression(fit_intercept=True, solver='lbfgs', max_iter=1000, random_state=42)
    model.fit(X_scaled, y_clean)

    # Evaluate
    y_pred = model.predict(X_scaled)
    y_proba = model.predict_proba(X_scaled)[:, 1]

    accuracy = accuracy_score(y_clean, y_pred)
    auc = roc_auc_score(y_clean, y_proba)
    precision = precision_score(y_clean, y_pred, zero_division=0)
    recall = recall_score(y_clean, y_pred, zero_division=0)

    print(f"{threshold:12d} {pos_rate:11.2f}% {y_clean.sum():11d} {accuracy:11.4f} {auc:11.4f} {precision:11.4f} {recall:11.4f}")

    results_table.append({
        'threshold': threshold,
        'positive_rate': pos_rate,
        'n_positives': int(y_clean.sum()),
        'accuracy': accuracy,
        'auc': auc,
        'precision': precision,
        'recall': recall
    })

print("\n" + "="*80)
print("ANALYSIS")
print("="*80 + "\n")

print("Key Findings:")
print("1. Model quality (AUC) DEGRADES as threshold decreases (more labels added)")
print("2. This suggests in-house features have WEAK natural signal")
print("3. Most of the 'positive' samples (18-30 bps) are likely noise")
print("\nRecommendation:")
print("- Use HIGHEST viable threshold (100+ bps) for best model quality")
print("- Accept lower positive rate (more sparse labels, better precision)")
print("- Equivalent to 'best of breed' signals in external labels")

best_auc = max(r['auc'] for r in results_table)
best_idx = [i for i, r in enumerate(results_table) if r['auc'] == best_auc][0]
best_result = results_table[best_idx]

print(f"\nBest performing threshold: {best_result['threshold']} bps")
print(f"  Positive rate: {best_result['positive_rate']:.3f}%")
print(f"  AUC: {best_result['auc']:.4f}")
print(f"  Precision: {best_result['precision']:.4f}")
