import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import riskfolio as rp
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\data\raw")
RESULTS_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "MARUTI", "TMPV",
    "HDFCBANK", "ICICIBANK", "SBIN", "BAJFINANCE",
    "TATASTEEL", "BEL", "HINDALCO", "LT"
]

def load_and_resample(timeframe="15min"):
    print(f"[*] Loading Parquet lake ({len(SYMBOLS)} symbols) and resampling to {timeframe}...")
    close_prices = {}
    
    for sym in SYMBOLS:
        p_path = DATA_DIR / f"{sym}_1min.parquet"
        if not p_path.exists():
            print(f"  [!] Warning: Missing {sym}_1min.parquet")
            continue
        df = pd.read_parquet(p_path)
        # Ensure uniform timezone/datetime index
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df['date'])
        resampled = df['close'].resample(timeframe).last().dropna()
        close_prices[sym] = resampled
        
    prices_df = pd.DataFrame(close_prices).dropna()
    print(f"[✓] Fleet consolidated: {len(prices_df):,} bars | Span: {prices_df.index.min().date()} to {prices_df.index.max().date()}")
    return prices_df

def run_hrp_allocation(prices_df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("RISKFOLIO-LIB: HIERARCHICAL RISK PARITY (HRP) FLEET DISPATCH WEIGHTS")
    print("=" * 80)
    
    returns = np.log(prices_df / prices_df.shift(1)).dropna()
    
    port = rp.HCPortfolio(returns=returns)
    weights = port.optimization(
        model='HRP',
        codependence='pearson',
        rm='MV',
        rf=0.065 / (252 * 25),
        linkage='ward'
    )
    
    weights['Weight_%'] = (weights['weights'] * 100).round(2)
    weights = weights.sort_values(by='weights', ascending=False)
    
    print("\nOptimal CCPP Turbine MW Dispatch Weights (HRP):")
    print("-" * 55)
    for sym, row in weights.iterrows():
        bar = "█" * int(row['Weight_%'] / 2)
        print(f"  {sym:<12} : {row['Weight_%']:>6.2f}%  | {bar}")
    print("-" * 55)
    
    weights.to_csv(RESULTS_DIR / "hrp_fleet_allocation.csv")
    print(f"[✓] Saved HRP dispatch weights to: {RESULTS_DIR / 'hrp_fleet_allocation.csv'}")
    return weights, returns

def run_triple_barrier_grid_sweep(prices_df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("TRIPLE BARRIER PARAMETER GRID CALIBRATION (VECTORIZED)")
    print("=" * 80)
    
    # Run barrier surface optimization across the baseload anchor
    px = prices_df['RELIANCE']
    ret = px.pct_change()
    
    # 20-period rolling volatility and SMA z-score
    vol = ret.rolling(20).std()
    sma = px.rolling(20).mean()
    roll_std = px.rolling(20).std()
    z_score = (px - sma) / roll_std
    
    # Forward return over 12 bars (3 hours on 15m)
    fwd_ret = px.shift(-12) / px - 1.0
    
    # Consolidate aligned numpy arrays to guarantee zero shape/indexing mismatches
    aligned_df = pd.DataFrame({
        'px': px,
        'z_score': z_score,
        'vol': vol,
        'fwd_ret': fwd_ret
    }).dropna()
    
    z_arr = aligned_df['z_score'].to_numpy()
    vol_arr = aligned_df['vol'].to_numpy()
    fwd_arr = aligned_df['fwd_ret'].to_numpy()
    
    z_entries = [-2.5, -2.0, -1.5]
    stop_mults = [1.0, 1.5, 2.0]
    target_mults = [1.5, 2.0, 3.0]
    
    results = []
    total_combs = len(z_entries) * len(stop_mults) * len(target_mults)
    print(f"Testing {total_combs} parameter combinations across 5 years...")
    
    for z in z_entries:
        entry_mask = (z_arr <= z)
        if not np.any(entry_mask):
            continue
            
        sub_fwd = fwd_arr[entry_mask]
        sub_vol = vol_arr[entry_mask]
        
        for s_mult in stop_mults:
            stop_thresh = -s_mult * sub_vol
            for t_mult in target_mults:
                tgt_thresh = t_mult * sub_vol
                
                # Element-wise barrier clamp via pure numpy
                clamped_pnl = np.clip(sub_fwd, stop_thresh, tgt_thresh)
                
                if len(clamped_pnl) >= 20:
                    win_rate = (clamped_pnl > 0).mean() * 100
                    expectancy = clamped_pnl.mean() * 10000  # Basis points
                    pnl_std = clamped_pnl.std()
                    sharpe = (clamped_pnl.mean() / pnl_std) * np.sqrt(252 * 25) if pnl_std > 0 else 0.0
                    
                    results.append({
                        "Z_Entry": z,
                        "Stop_ATR": s_mult,
                        "Target_ATR": t_mult,
                        "Trades": len(clamped_pnl),
                        "Win_Rate%": round(win_rate, 2),
                        "Expectancy_bps": round(expectancy, 1),
                        "Sharpe": round(sharpe, 2)
                    })
                    
    res_df = pd.DataFrame(results).sort_values(by="Sharpe", ascending=False)
    print("\nTop Calibrated Barrier Configurations:")
    print(res_df.head(10).to_string(index=False))
    
    res_df.to_csv(RESULTS_DIR / "triple_barrier_grid_results.csv", index=False)
    print(f"\n[✓] Parameter surface saved to: {RESULTS_DIR / 'triple_barrier_grid_results.csv'}")

if __name__ == "__main__":
    prices = load_and_resample("15min")
    weights, returns = run_hrp_allocation(prices)
    run_triple_barrier_grid_sweep(prices)
