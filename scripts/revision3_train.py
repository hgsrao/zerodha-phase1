import glob, pickle, os
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

FEATURE_COLS = ['rs_thrust', 'rs_momentum_raw', '15m_rs_percentile', '5m_trend', '15m_trend', '5m_vwap_dist_atr', '15m_vwap_dist_atr', '5m_realized_vol', '15m_realized_vol']
TARGET_MULT, STOP_MULT, HORIZON = 1.5, 1.0, 45

train_files = sorted(glob.glob('revision3/features_2026/train/*.parquet'))
all_X, all_y = [], []

for f in train_files:
    df = pd.read_parquet(f)
    df = df[df['macro_gate'] == 1].copy()
    if len(df) == 0: continue
    
    tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift()).abs(), (df['low']-df['close'].shift()).abs()], axis=1).max(axis=1)
    atr, highs, lows, opens, n = tr.rolling(14, min_periods=1).mean().values, df['high'].values, df['low'].values, df['open'].values, len(df)
    labels, ambiguous = np.full(n, np.nan), np.zeros(n, dtype=bool)
    
    for i in range(n - HORIZON - 1):
        if pd.isna(atr[i]) or atr[i] == 0: continue
        tp, sp = opens[i+1] + (TARGET_MULT * atr[i]), opens[i+1] - (STOP_MULT * atr[i])
        ht, hs = False, False
        for k in range(i + 1, min(i + 1 + HORIZON, n)):
            th, sh = highs[k] >= tp, lows[k] <= sp
            if th and sh: ambiguous[i] = True; break
            elif th: ht = True; labels[i] = 1; break
            elif sh: hs = True; labels[i] = 0; break
        if not ht and not hs and not ambiguous[i]:
            labels[i] = 1 if df['close'].values[min(i + 1 + HORIZON, n - 1)] >= opens[i+1] else 0

    valid = df[FEATURE_COLS].notna().all(axis=1) & ~np.isnan(labels) & ~ambiguous
    if valid.sum() > 0: all_X.append(df.loc[valid, FEATURE_COLS].values); all_y.append(labels[valid])

X_train, y_train = np.vstack(all_X), np.concatenate(all_y)
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X_train)
model = LogisticRegression(penalty='l2', C=0.1, class_weight='balanced', max_iter=1000)
model.fit(X_scaled, y_train)

print("\nLearned Weights:")
for feat, weight in sorted(zip(FEATURE_COLS, model.coef_[0]), key=lambda x: abs(x[1]), reverse=True): print(f"  {feat:22s}: {weight:+.4f}")

os.makedirs('revision3/frozen_models_2026', exist_ok=True)
with open("revision3/frozen_models_2026/feature_scaler.pkl", "wb") as f: pickle.dump(scaler, f)
with open("revision3/frozen_models_2026/logistic_regression.pkl", "wb") as f: pickle.dump(model, f)
