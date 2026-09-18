import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\data\raw")
RESULTS_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\results")
HRP_PATH = RESULTS_DIR / "hrp_fleet_allocation.csv"

# Load HRP weights cleanly
weights_raw = pd.read_csv(HRP_PATH)
first_col = weights_raw.columns[0]
weights = dict(zip(weights_raw[first_col], weights_raw['weights']))

# Operational parameters calibrated from Phase 2
Z_ENTRY = -2.0
STOP_MULT = 1.0
TARGET_MULT = 3.0
HOLDING_HORIZON = 12  # 12 x 15m = 3-hour intraday cycle

print("=" * 80)
print("PHASE 3: INSTITUTIONAL CCPP MULTI-ASSET PORTFOLIO BACKTEST")
print(f"Calibration: Z_Entry={Z_ENTRY} | Stop={STOP_MULT}x ATR | Target={TARGET_MULT}x ATR | Horizon={HOLDING_HORIZON} bars")
print("=" * 80)

# Load aligned 15m dataset
dfs = {}
for sym in weights.keys():
    p_path = DATA_DIR / f"{sym}_1min.parquet"
    if not p_path.exists():
        continue
    df = pd.read_parquet(p_path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df['date'])
    df_15 = df.resample('15min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    dfs[sym] = df_15

common_index = None
for sym, df in dfs.items():
    if common_index is None:
        common_index = df.index
    else:
        common_index = common_index.intersection(df.index)

print(f"[✓] Synchronized fleet bars: {len(common_index):,} periods ({common_index.min().date()} to {common_index.max().date()})")

# Simulate individual turbine PnL series
fleet_pnls = pd.DataFrame(index=common_index)

for sym, df in dfs.items():
    sub_df = df.loc[common_index]
    px = sub_df['close']
    ret = px.pct_change()
    
    vol = ret.rolling(20).std()
    sma = px.rolling(20).mean()
    roll_std = px.rolling(20).std()
    z_score = (px - sma) / roll_std
    
    fwd_ret = px.shift(-HOLDING_HORIZON) / px - 1.0
    
    entry_mask = (z_score <= Z_ENTRY).to_numpy()
    fwd_arr = fwd_ret.to_numpy()
    vol_arr = vol.to_numpy()
    
    clamped = np.zeros(len(sub_df))
    for i in range(len(sub_df)):
        if entry_mask[i] and not np.isnan(fwd_arr[i]) and not np.isnan(vol_arr[i]):
            stop_lvl = -STOP_MULT * vol_arr[i]
            tgt_lvl = TARGET_MULT * vol_arr[i]
            clamped[i] = np.clip(fwd_arr[i], stop_lvl, tgt_lvl)
            
    fleet_pnls[sym] = clamped

# Portfolio return weighted by HRP allocation
weight_series = pd.Series(weights)
weight_series = weight_series / weight_series.sum()
portfolio_returns = (fleet_pnls * weight_series).sum(axis=1)

# Institutional Performance Metrics
cum_return = (1 + portfolio_returns).cumprod()
peak = cum_return.cummax()
drawdown = (cum_return - peak) / peak
max_dd = drawdown.min() * 100

total_trades = int((fleet_pnls != 0).sum().sum())
total_periods = len(portfolio_returns)
annualized_ret = (cum_return.iloc[-1] ** ((252 * 25) / total_periods) - 1) * 100
vol_ann = portfolio_returns.std() * np.sqrt(252 * 25) * 100
sharpe = (annualized_ret / vol_ann) if vol_ann > 0 else 0.0
downside_returns = portfolio_returns[portfolio_returns < 0]
sortino_denom = downside_returns.std() * np.sqrt(252 * 25) * 100
sortino = (annualized_ret / sortino_denom) if sortino_denom > 0 else 0.0
calmar = abs(annualized_ret / max_dd) if max_dd != 0 else 0.0

print("\n" + "=" * 80)
print("PORTFOLIO PERFORMANCE AUDIT (5-YEAR OOS TEAR-SHEET)")
print("=" * 80)
print(f"  Total Trades Executed    : {total_trades:,}")
print(f"  Annualized Return (CAGR) : {annualized_ret:.2f}%")
print(f"  Annualized Volatility    : {vol_ann:.2f}%")
print(f"  Sharpe Ratio             : {sharpe:.2f}")
print(f"  Sortino Ratio            : {sortino:.2f}")
print(f"  Max Drawdown             : {max_dd:.2f}%")
print(f"  Calmar Ratio             : {calmar:.2f}")
print("=" * 80)

summary_df = pd.DataFrame([{
    'Total_Trades': total_trades,
    'CAGR_%': round(annualized_ret, 2),
    'Annualized_Vol_%': round(vol_ann, 2),
    'Sharpe_Ratio': round(sharpe, 2),
    'Sortino_Ratio': round(sortino, 2),
    'Max_Drawdown_%': round(max_dd, 2),
    'Calmar_Ratio': round(calmar, 2)
}])
summary_df.to_csv(RESULTS_DIR / "portfolio_backtest_summary.csv", index=False)
print(f"[✓] Summary saved to: {RESULTS_DIR / 'portfolio_backtest_summary.csv'}")
