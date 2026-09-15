"""
Rolling Hurst Exponent & Variance Ratio Diagnostic
--------------------------------------------------
Measures mean-reversion strength (H < 0.5) vs trend-persistence (H > 0.5).
"""

from pathlib import Path
import numpy as np
import pandas as pd

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

def compute_hurst(series: np.ndarray, max_lag: int = 20) -> float:
    if len(series) < 100:
        return 0.5
    lags = range(2, max_lag)
    tau = [np.sqrt(np.std(np.subtract(series[lag:], series[:-lag]))) for lag in lags]
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return float(poly[0] * 2.0)

def compute_variance_ratio(log_returns: np.ndarray, k: int = 15) -> float:
    if len(log_returns) < k * 10:
        return 1.0
    var_1 = np.var(log_returns, ddof=1)
    k_returns = pd.Series(log_returns).rolling(k).sum().dropna().values
    var_k = np.var(k_returns, ddof=1)
    if var_1 == 0:
        return 1.0
    return float(var_k / (k * var_1))

def main():
    target_symbols = [
        'BAJFINANCE', 'JSWSTEEL', 'ULTRACEMCO', 'M&M', 'HINDUNILVR',
        'ADANIENT', 'RELIANCE', 'INFY', 'TECHM', 'SBIN'
    ]
    
    records = []
    for sym in target_symbols:
        f = list(DATA_DIR.glob(f"NSE_{sym}_minute_*.csv"))
        if not f:
            continue
        df = pd.read_csv(f[0])
        t_col = [c for c in df.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
        df['dt'] = pd.to_datetime(df[t_col]).dt.tz_localize(None)
        df = df.set_index('dt').sort_index()
        
        df_15 = df.resample('15min').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
        log_ret = np.diff(np.log(df_15['close'].values))
        
        h = compute_hurst(df_15['close'].values)
        vr = compute_variance_ratio(log_ret, k=15)
        atr_pct = ((df_15['high'] - df_15['low']) / df_15['close']).mean() * 100.0
        
        records.append({
            'Symbol': sym,
            'Hurst (H)': round(h, 3),
            'Variance Ratio': round(vr, 3),
            'ATR%': round(atr_pct, 2),
            'Regime Bias': 'Mean-Reverting' if h < 0.48 and vr < 0.95 else 'Trending / Random Walk'
        })
        
    res = pd.DataFrame(records).sort_values('Hurst (H)')
    print("=" * 80)
    print("EMPIRICAL REGIME CLUSTERING DIAGNOSTIC (TRAINING DATA)")
    print("=" * 80)
    print(res.to_string(index=False))
    print("=" * 80)

if __name__ == '__main__':
    main()
