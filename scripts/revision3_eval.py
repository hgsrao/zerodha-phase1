import glob, pickle
import pandas as pd
import numpy as np

with open('revision3/frozen_models_2026/logistic_regression.pkl', 'rb') as f: model = pickle.load(f)
with open('revision3/frozen_models_2026/feature_scaler.pkl', 'rb') as f: scaler = pickle.load(f)

FEATURE_COLS = ['rs_thrust', 'rs_momentum_raw', '15m_rs_percentile', '5m_trend', '15m_trend', '5m_vwap_dist_atr', '15m_vwap_dist_atr', '5m_realized_vol', '15m_realized_vol']
TARGET_MULT, STOP_MULT, HORIZON, FRICTION_R = 1.5, 1.0, 45, 0.27

def evaluate_split(split_name, cutoff=None):
    all_scores, all_labels = [], []
    for f in sorted(glob.glob(f'revision3/features_2026/{split_name}/*.parquet')):
        df = pd.read_parquet(f)
        tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift()).abs(), (df['low']-df['close'].shift()).abs()], axis=1).max(axis=1)
        atr, highs, lows, opens, n = tr.rolling(14, min_periods=1).mean().values, df['high'].values, df['low'].values, df['open'].values, len(df)
        labels, ambiguous = np.full(n, np.nan), np.zeros(n, dtype=bool)
        for i in range(n - HORIZON - 1):
            if pd.isna(atr[i]) or atr[i] == 0: continue
            tp, sp = opens[i+1] + (TARGET_MULT * atr[i]), opens[i+1] - (STOP_MULT * atr[i])
            ht, hs = False, False
            for k in range(i + 1, min(i + 1 + HORIZON, n)):
                if highs[k] >= tp and lows[k] <= sp: ambiguous[i] = True; break
                elif highs[k] >= tp: ht = True; labels[i] = 1; break
                elif lows[k] <= sp: hs = True; labels[i] = 0; break
            if not ht and not hs and not ambiguous[i]: labels[i] = 1 if df['close'].values[min(i + 1 + HORIZON, n - 1)] >= opens[i+1] else 0

        valid = df[FEATURE_COLS].notna().all(axis=1) & ~np.isnan(labels) & ~ambiguous
        if valid.sum() == 0: continue
        valid_df = df[valid_mask := valid]
        gate = valid_df['macro_gate'].values
        scores = np.zeros(len(valid_df))
        if (gate_open := gate == 1).sum() > 0:
            scores[gate_open] = model.predict_proba(scaler.transform(valid_df[FEATURE_COLS][gate_open]))[:, 1]
        all_scores.extend(scores); all_labels.extend(labels[valid_mask])
        
    scores, labels = np.array(all_scores), np.array(all_labels)
    gate_open_mask = scores > 0
    if cutoff is None: cutoff = np.percentile(scores[gate_open_mask], 99) if len(scores[gate_open_mask]) > 0 else 1.0
    top_mask = scores >= cutoff
    win_rate = labels[top_mask].mean() * 100 if top_mask.sum() > 0 else 0.0
    net_bps = ((win_rate/100 * TARGET_MULT) - ((100-win_rate)/100 * STOP_MULT) - FRICTION_R) * 30.0
    print(f"\n--- {split_name.upper()} ---")
    print(f"Computed Cutoff: {cutoff:.6f}" if split_name=='validation' else f"Using Cutoff: {cutoff:.6f}")
    print(f"Trades: {top_mask.sum()} | Win Rate: {win_rate:.2f}% | Net Expectancy: {net_bps:.2f} bps")
    return cutoff

april_cutoff = evaluate_split('validation')
evaluate_split('test', cutoff=april_cutoff)
