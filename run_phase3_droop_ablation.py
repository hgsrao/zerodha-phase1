import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

MACHINE_DROOP_MAP = {
    "HDFCBANK": 0.04, "ICICIBANK": 0.04, "SBIN": 0.04, "AXISBANK": 0.04, "KOTAKBANK": 0.04,
    "BAJFINANCE": 0.04, "BAJAJFINSV": 0.04, "TCS": 0.04, "INFY": 0.04, "TECHM": 0.04,
    "WIPRO": 0.04, "HCLTECH": 0.04, "TATASTEEL": 0.04, "JSWSTEEL": 0.04, "HINDALCO": 0.04,
    "RELIANCE": 0.055, "LT": 0.055, "MARUTI": 0.055, "M&M": 0.055, "TATACONSUM": 0.055,
    "EICHERMOT": 0.055, "BAJAJ-AUTO": 0.055, "ADANIENT": 0.055, "ADANIPORTS": 0.055,
    "GRASIM": 0.055, "ULTRACEMCO": 0.055, "TITAN": 0.055,
    "ITC": 0.075, "HINDUNILVR": 0.075, "NESTLEIND": 0.075, "BRITANNIA": 0.075,
    "CIPLA": 0.075, "DRREDDY": 0.075, "SUNPHARMA": 0.075, "APOLLOHOSP": 0.075,
    "NTPC": 0.075, "POWERGRID": 0.075, "COALINDIA": 0.075, "ONGC": 0.075, "BPCL": 0.075
}
DEFAULT_DROOP = 0.055

def calculate_zerodha_equity_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = min(40.0, 0.0003 * turnover)
    stt = 0.00025 * exit_val
    exchange_txn = 0.0000325 * turnover
    sebi = 0.000001 * turnover
    stamp_duty = 0.00003 * entry_val
    gst = 0.18 * (brokerage + exchange_txn + sebi)
    return brokerage + stt + exchange_txn + sebi + stamp_duty + gst

def compute_causal_features_welford(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    
    df['rsi_percentile'] = df['rsi_14'].rolling(100).apply(
        lambda x: (x.argsort().argsort()[-1] + 1.0) / 100.0, raw=True
    )

    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = tr.rolling(14).mean()
    df['atr_baseline'] = df['atr'].rolling(100).mean()
    df['vol_ratio'] = (df['atr'] / df['atr_baseline']).replace(0, 1.0)
    
    df['vol_median'] = df['volume'].rolling(50).median()
    df['vol_flow_ratio'] = df['volume'] / df['vol_median']
    
    df['dp_dt'] = df['close'].diff()
    df['dv_dt'] = df['volume'].diff()
    
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['dt'] = pd.to_datetime(df[time_col]).dt.tz_localize(None)
    df['date_only'] = df['dt'].dt.date
    df['time_only'] = df['dt'].dt.time
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    vwap_arr = np.zeros(len(df))
    std_arr = np.zeros(len(df))
    
    for _, idxs in df.groupby('date_only').groups.items():
        w_sum = 0.0
        mean = 0.0
        M2 = 0.0
        for i in idxs:
            p = df.at[i, 'typ_price']
            w = max(1.0, df.at[i, 'volume'])
            w_sum_old = w_sum
            w_sum += w
            delta = p - mean
            R = delta * w / w_sum
            mean += R
            M2 += w_sum_old * delta * R
            vwap_arr[i] = mean
            std_arr[i] = np.sqrt(M2 / w_sum) if w_sum > 0 and M2 > 0 else 1.0
            
    df['vwap'] = vwap_arr
    df['vwap_std'] = np.where(std_arr < 1e-4, 1.0, std_arr)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

def simulate_portfolio(symbol_frames, all_timestamps, grid_df, use_droop: bool):
    MAX_CONCURRENT_POSITIONS = 5
    PORTFOLIO_EQUITY = 500000.0
    ALLOCATION_PER_TRADE = PORTFOLIO_EQUITY / MAX_CONCURRENT_POSITIONS
    
    active_positions = {}
    closed_trades = []
    
    for t in all_timestamps:
        if t not in grid_df.index:
            continue
        grid_row = grid_df.loc[t]
        
        symbols_to_close = []
        for sym, pos in active_positions.items():
            if t not in symbol_frames[sym].index:
                continue
            bar = symbol_frames[sym].loc[t]
            bar_open = bar['open']
            bar_high = bar['high']
            bar_low = bar['low']
            bar_close = bar['close']
            bar_time = bar['time_only']
            risk_ticks = pos['risk_ticks']
            
            if bar_time >= pd.to_datetime('15:15:00').time():
                symbols_to_close.append((sym, bar_close, "SESSION_1515_SQUAREOFF", t))
                continue
                
            if bar_low <= pos['stop_price']:
                fill = min(bar_open, pos['stop_price']) - (0.05 * risk_ticks)
                symbols_to_close.append((sym, fill, "STOP_TRIGGERED", t))
                continue
                
            if bar['vwap_zscore'] >= -0.20:
                symbols_to_close.append((sym, bar_close, "VWAP_EQUILIBRIUM", t))
                continue
                
            curr_peak_r = (bar_high - pos['entry_price']) / risk_ticks
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)
            
            if pos['peak_r'] >= 1.0:
                new_stop = pos['entry_price'] + (pos['peak_r'] * 0.60 * risk_ticks)
                pos['stop_price'] = max(pos['stop_price'], new_stop)
            elif pos['peak_r'] >= 0.50:
                new_stop = pos['entry_price'] + (0.18 * risk_ticks)
                pos['stop_price'] = max(pos['stop_price'], new_stop)
                
        for sym, fill_p, reason, exit_t in symbols_to_close:
            p = active_positions.pop(sym)
            gross_pnl = (fill_p - p['entry_price']) * p['shares']
            friction = calculate_zerodha_equity_friction(p['entry_price'] * p['shares'], fill_p * p['shares'])
            net_pnl = gross_pnl - friction
            risk_inr = p['risk_ticks'] * p['shares']
            
            closed_trades.append({
                'symbol': sym,
                'gross_pnl': gross_pnl,
                'friction': friction,
                'net_pnl': net_pnl,
                'net_r': net_pnl / max(1.0, risk_inr),
                'exit_reason': reason
            })
            
        available_slots = MAX_CONCURRENT_POSITIONS - len(active_positions)
        if available_slots <= 0:
            continue
            
        for sym, df in symbol_frames.items():
            if sym in active_positions or t not in df.index:
                continue
            row = df.loc[t]
            
            if row['time_only'] >= pd.to_datetime('14:45:00').time():
                continue
                
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                price_dir = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_dir = 1.0 if row['dv_dt'] > 0 else -1.0
                if row['dp_dt'] < 0 and (price_dir * volume_dir) < 0:
                    continue
                
                # Droop Condition
                if use_droop:
                    droop_r = MACHINE_DROOP_MAP.get(sym, DEFAULT_DROOP)
                    droop_attenuation = (1.0 / droop_r) * max(0.0, -grid_row['f_grid'])
                    if droop_attenuation > 0.40:
                        continue
                        
                loc_idx = df.index.get_loc(t)
                if loc_idx + 1 >= len(df):
                    continue
                next_bar = df.iloc[loc_idx + 1]
                entry_p = next_bar['open']
                risk_t = 1.2 * row['atr']
                shares = int(ALLOCATION_PER_TRADE / entry_p)
                if shares < 1:
                    continue
                    
                active_positions[sym] = {
                    'entry_price': entry_p,
                    'stop_price': entry_p - risk_t,
                    'risk_ticks': risk_t,
                    'shares': shares,
                    'peak_r': 0.0,
                    'entry_time': next_bar.name
                }
                available_slots -= 1
                if available_slots <= 0:
                    break
                    
    return pd.DataFrame(closed_trades)

def run():
    print("Loading NIFTY Grid features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print("Precomputing features for all symbols (July 2023)...")
    symbol_frames = {}
    for fpath in csv_files:
        sym = fpath.name.split('_')[1]
        try:
            raw = pd.read_csv(fpath)
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(m) > 300:
                symbol_frames[sym] = compute_causal_features_welford(m).set_index('dt')
        except Exception:
            continue
            
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in symbol_frames.values()])))
    print(f"Loaded {len(symbol_frames)} symbols across {len(all_timestamps):,} minutes.")
    
    print("\nRunning Arm A: Baseline Engine (Without Droop Governor)...")
    df_no_droop = simulate_portfolio(symbol_frames, all_timestamps, grid_df, use_droop=False)
    
    print("Running Arm B: Engine with Speed-Droop Governor...")
    df_with_droop = simulate_portfolio(symbol_frames, all_timestamps, grid_df, use_droop=True)
    
    print("\n" + "=" * 75)
    print("PHASE 3: SPEED-DROOP GOVERNOR ABLATION RESULTS")
    print("=" * 75)
    print(f"{'Metric':<25} | {'Arm A (No Droop)':<20} | {'Arm B (With Droop)':<20}")
    print("-" * 75)
    print(f"{'Total Trades':<25} | {len(df_no_droop):<20} | {len(df_with_droop):<20}")
    print(f"{'Win Rate':<25} | {(df_no_droop['net_pnl'] > 0).mean()*100:6.2f}%{'':<13} | {(df_with_droop['net_pnl'] > 0).mean()*100:6.2f}%")
    print(f"{'Gross P&L':<25} | ₹{df_no_droop['gross_pnl'].sum():+10,.2f}{'':<8} | ₹{df_with_droop['gross_pnl'].sum():+10,.2f}")
    print(f"{'Statutory Fees':<25} | ₹{df_no_droop['friction'].sum():10,.2f}{'':<8} | ₹{df_with_droop['friction'].sum():10,.2f}")
    print(f"{'Net Portfolio P&L':<25} | ₹{df_no_droop['net_pnl'].sum():+10,.2f}{'':<8} | ₹{df_with_droop['net_pnl'].sum():+10,.2f}")
    print(f"{'Net Expectancy (R)':<25} | {df_no_droop['net_r'].mean():+7.3f}R / trade{'':<6} | {df_with_droop['net_r'].mean():+7.3f}R / trade")
    print("=" * 75)

if __name__ == '__main__':
    run()
