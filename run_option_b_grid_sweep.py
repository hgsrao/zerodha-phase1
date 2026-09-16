import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

def calculate_zerodha_equity_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = min(40.0, 0.0003 * turnover)
    stt = 0.00025 * exit_val
    exchange_txn = 0.0000325 * turnover
    sebi = 0.000001 * turnover
    stamp_duty = 0.00003 * entry_val
    gst = 0.18 * (brokerage + exchange_txn + sebi)
    return brokerage + stt + exchange_txn + sebi + stamp_duty + gst

def resample_to_15min(raw_df: pd.DataFrame) -> pd.DataFrame:
    raw_df.columns = [c.lower() for c in raw_df.columns]
    time_col = [c for c in raw_df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    raw_df['dt'] = pd.to_datetime(raw_df[time_col]).dt.tz_localize(None)
    raw_df = raw_df.sort_values('dt').set_index('dt')
    
    ohlc_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    df_15 = raw_df.resample('15min').agg(ohlc_dict).dropna().reset_index()
    df_15['date_only'] = df_15['dt'].dt.date
    df_15['time_only'] = df_15['dt'].dt.time
    return df_15

def compute_welford_15min_features(df: pd.DataFrame) -> pd.DataFrame:
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    
    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = tr.rolling(14).mean()
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
            delta_p = p - mean
            R = delta_p * w / w_sum
            mean += R
            M2 += w_sum_old * delta_p * R
            vwap_arr[i] = mean
            std_arr[i] = np.sqrt(M2 / w_sum) if w_sum > 0 and M2 > 0 else 1.0
            
    df['vwap'] = vwap_arr
    df['vwap_std'] = np.where(std_arr < 1e-4, 1.0, std_arr)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

def simulate_sweep_instance(symbol_frames, all_timestamps, r_target_thresh: float, z_target_thresh: float):
    MAX_CONCURRENT_POSITIONS = 5
    PORTFOLIO_EQUITY = 500000.0
    ALLOCATION_PER_TRADE = PORTFOLIO_EQUITY / MAX_CONCURRENT_POSITIONS
    
    active_positions = {}
    closed_trades = []
    armed_symbols = {}
    
    for t in all_timestamps:
        symbols_to_close = []
        
        # 1. Evaluate Active Trades
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
            
            # EOD Cutoff
            if bar_time >= pd.to_datetime('15:15:00').time():
                symbols_to_close.append((sym, bar_close, "SESSION_1515"))
                continue
                
            # Worst-Case Stop Execution (Check low first)
            if bar_low <= pos['stop_price']:
                fill = min(bar_open, pos['stop_price']) - (0.05 * risk_ticks)
                symbols_to_close.append((sym, fill, "STOP"))
                continue
                
            # Current Return
            curr_r = (bar_close - pos['entry_price']) / risk_ticks
            curr_peak_r = (bar_high - pos['entry_price']) / risk_ticks
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)
            
            # Target 1: R-target hit (evaluated against bar high)
            if curr_peak_r >= r_target_thresh:
                fill = pos['entry_price'] + (r_target_thresh * risk_ticks)
                symbols_to_close.append((sym, fill, "R_TARGET"))
                continue
                
            # Target 2: VWAP Z-score target hit (evaluated on bar close, must be in profit)
            if bar['vwap_zscore'] >= z_target_thresh and curr_r >= 0.5:
                symbols_to_close.append((sym, bar_close, "Z_TARGET"))
                continue
                
            # Trailing Profit Lock
            if pos['peak_r'] >= 1.2:
                new_stop = pos['entry_price'] + (pos['peak_r'] * 0.50 * risk_ticks)
                pos['stop_price'] = max(pos['stop_price'], new_stop)
                
        for sym, fill_p, reason in symbols_to_close:
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
            
        # 2. Check Candidate Entries via ANSI 25 Synchrocheck
        available_slots = MAX_CONCURRENT_POSITIONS - len(active_positions)
        if available_slots <= 0:
            continue
            
        for sym, df in symbol_frames.items():
            if sym in active_positions or t not in df.index:
                continue
            loc_idx = df.index.get_loc(t)
            if loc_idx < 2 or loc_idx + 1 >= len(df):
                continue
                
            curr_bar = df.iloc[loc_idx]
            prev_bar = df.iloc[loc_idx - 1]
            
            if curr_bar['time_only'] >= pd.to_datetime('14:30:00').time():
                if sym in armed_symbols:
                    del armed_symbols[sym]
                continue
                
            if curr_bar['vwap_zscore'] < -2.2 and curr_bar['rsi_14'] < 30:
                armed_symbols[sym] = {
                    'armed_time': t,
                    'swing_low': min(curr_bar['low'], prev_bar['low'])
                }
                
            if sym in armed_symbols:
                armed_symbols[sym]['swing_low'] = min(armed_symbols[sym]['swing_low'], curr_bar['low'])
                
                if curr_bar['close'] > prev_bar['high'] and curr_bar['close'] > curr_bar['open']:
                    next_bar = df.iloc[loc_idx + 1]
                    entry_p = next_bar['open']
                    swing_low = armed_symbols[sym]['swing_low']
                    risk_t = max(entry_p - swing_low, 0.8 * curr_bar['atr'])
                    shares = int(ALLOCATION_PER_TRADE / entry_p)
                    
                    if shares >= 1:
                        active_positions[sym] = {
                            'entry_price': entry_p,
                            'stop_price': entry_p - risk_t,
                            'risk_ticks': risk_t,
                            'shares': shares,
                            'peak_r': 0.0
                        }
                        del armed_symbols[sym]
                        available_slots -= 1
                        if available_slots <= 0:
                            break
                            
    return pd.DataFrame(closed_trades)

def run():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print("Precomputing 15m Welford features across all instruments...")
    symbol_frames = {}
    for fpath in csv_files:
        sym = fpath.name.split('_')[1]
        try:
            raw = pd.read_csv(fpath)
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(m) > 300:
                df_15 = resample_to_15min(m)
                if len(df_15) > 40:
                    symbol_frames[sym] = compute_welford_15min_features(df_15).set_index('dt')
        except Exception:
            continue
            
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in symbol_frames.values()])))
    print(f"Dataset ready: {len(symbol_frames)} symbols across {len(all_timestamps):,} bars.\n")
    
    r_targets = [1.2, 1.5, 1.8, 2.0]
    z_targets = [0.0, 0.5, 1.0]
    
    sweep_results = []
    
    print(f"{'R-Target':<10} | {'Z-Target':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Gross P&L':<14} | {'Fees':<12} | {'Net P&L':<14} | {'Net Exp (R)':<12}")
    print("-" * 105)
    
    for r_t in r_targets:
        for z_t in z_targets:
            df = simulate_sweep_instance(symbol_frames, all_timestamps, r_t, z_t)
            if len(df) == 0:
                continue
            wr = (df['net_pnl'] > 0).mean() * 100
            gross = df['gross_pnl'].sum()
            fees = df['friction'].sum()
            net = df['net_pnl'].sum()
            net_exp = df['net_r'].mean()
            
            sweep_results.append({
                'r_target': r_t,
                'z_target': z_t,
                'trades': len(df),
                'win_rate': wr,
                'gross_pnl': gross,
                'fees': fees,
                'net_pnl': net,
                'net_exp': net_exp
            })
            
            print(f"{r_t:<10.1f} | {z_t:<10.1f} | {len(df):<8} | {wr:6.2f}%{'':<3} | ₹{gross:+11,.2f} | ₹{fees:9,.2f} | ₹{net:+11,.2f} | {net_exp:+8.3f}R")
            
    res_df = pd.DataFrame(sweep_results)
    best_row = res_df.loc[res_df['net_pnl'].idxmax()]
    print("-" * 105)
    print(f"Optimal Static Configuration: R-Target = {best_row['r_target']:.1f}R, Z-Target = {best_row['z_target']:.1f} (Net P&L: ₹{best_row['net_pnl']:+,.2f})")

if __name__ == '__main__':
    run()
