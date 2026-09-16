import glob, pickle, os
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

print("=" * 80)
print("REVISION 5: GRADIENT BOOSTING NON-LINEAR INTERACTION REDESIGN")
print("=" * 80)

FEATURE_COLS = [
    'rs_thrust', 'rs_momentum_raw', '15m_rs_percentile', 
    '5m_trend', '15m_trend', '5m_vwap_dist_atr', '15m_vwap_dist_atr', 
    '5m_realized_vol', '15m_realized_vol'
]
TARGET_MULT, STOP_MULT, HORIZON, FRICTION_R = 1.5, 1.0, 45, 0.27

def apply_iron_gate(df_synthetic_index, df_universe_closes):
    idx_close = df_synthetic_index['close']
    sma_30 = idx_close.rolling(30, min_periods=5).mean()
    std_30 = idx_close.rolling(30, min_periods=5).std().replace(0, 1e-6)
    trend_z = (idx_close - sma_30) / std_30
    trend_pass = trend_z > 0.25

    ret_15m = df_universe_closes.pct_change(15, fill_method=None).fillna(0)
    breadth_15m = (ret_15m > 0).mean(axis=1)
    breadth_pass = breadth_15m >= 0.55

    idx_1m_ret = idx_close.pct_change(fill_method=None).fillna(0)
    vol_60m_daily = idx_1m_ret.rolling(60, min_periods=10).std() * np.sqrt(375)
    vol_pass = vol_60m_daily >= 0.0090

    return (trend_pass & breadth_pass & vol_pass).astype(int)

def extract_dataset(split_name):
    files = sorted(glob.glob(f'revision3/features_2026/{split_name}/*.parquet'))
    closes = {f.split('/')[-1].split('_')[0]: pd.read_parquet(f)['close'] for f in files}
    df_closes = pd.DataFrame(closes)
    idx_close = (1 + df_closes.pct_change(fill_method=None).fillna(0).mean(axis=1)).cumprod() * 100.0
    df_market = pd.DataFrame({'close': idx_close}, index=idx_close.index)
    gate = apply_iron_gate(df_market, df_closes)

    all_X, all_y = [], []
    for f in files:
        df = pd.read_parquet(f)
        df['iron_gate'] = gate
        df = df[df['iron_gate'] == 1].copy()
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
        if valid.sum() > 0:
            all_X.append(df.loc[valid, FEATURE_COLS].values)
            all_y.append(labels[valid])

    return np.vstack(all_X), np.concatenate(all_y)

print("Preparing Training (Jan-Mar) datasets...")
X_train, y_train = extract_dataset('train')
print(f"Train Samples: {len(y_train)} (Win Rate: {y_train.mean()*100:.2f}%)")

print("\nTraining GradientBoostingClassifier (Max Depth=4, Trees=200)...")
clf = GradientBoostingClassifier(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.03,
    subsample=0.8,
    random_state=42
)
clf.fit(X_train, y_train)

os.makedirs('revision5/frozen_models_2026', exist_ok=True)
with open('revision5/frozen_models_2026/gbc_model.pkl', 'wb') as f:
    pickle.dump(clf, f)

def evaluate_split(split_name, model, threshold_percentile=99.0):
    files = sorted(glob.glob(f'revision3/features_2026/{split_name}/*.parquet'))
    closes = {f.split('/')[-1].split('_')[0]: pd.read_parquet(f)['close'] for f in files}
    df_closes = pd.DataFrame(closes)
    idx_close = (1 + df_closes.pct_change(fill_method=None).fillna(0).mean(axis=1)).cumprod() * 100.0
    df_market = pd.DataFrame({'close': idx_close}, index=idx_close.index)
    gate = apply_iron_gate(df_market, df_closes)
    
    all_scores, all_labels = [], []
    for f in files:
        df = pd.read_parquet(f)
        df['iron_gate'] = gate
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
        valid_df = df[valid]
        g_open = valid_df['iron_gate'].values == 1
        scores = np.zeros(len(valid_df))
        if g_open.sum() > 0:
            scores[g_open] = model.predict_proba(valid_df[FEATURE_COLS][g_open])[:, 1]
        all_scores.extend(scores); all_labels.extend(labels[valid])
        
    scores, labels = np.array(all_scores), np.array(all_labels)
    gate_open_mask = scores > 0
    cutoff = np.percentile(scores[gate_open_mask], threshold_percentile) if len(scores[gate_open_mask]) > 0 else 1.0
    top_mask = scores >= cutoff
    win_rate = labels[top_mask].mean() * 100 if top_mask.sum() > 0 else 0.0
    net_bps = ((win_rate/100 * TARGET_MULT) - ((100-win_rate)/100 * STOP_MULT) - FRICTION_R) * 30.0
    print(f"\n--- {split_name.upper()} (GBC + IRON GATE) ---")
    print(f"Trades Triggered: {top_mask.sum()} | Win Rate: {win_rate:.2f}% | Net Expectancy: {net_bps:.2f} bps")
    return cutoff

print("\nEvaluating Splits Out-of-Sample:")
evaluate_split('validation', clf)
evaluate_split('test', clf, threshold_percentile=99.0)
print("=" * 80)
