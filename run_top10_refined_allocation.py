import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

# Excluded chronic draggers (SBIN, RELIANCE)
TARGET_SYMBOLS = [
    "HDFCBANK", "ICICIBANK", "INFY", "TCS", 
    "AXISBANK", "LT", "BAJFINANCE", "TATAMOTORS"
]

SECTOR_MAP = {
    "HDFCBANK": "BANK", "ICICIBANK": "BANK", "AXISBANK": "BANK",
    "INFY": "IT", "TCS": "IT",
    "LT": "INFRA", "BAJFINANCE": "FIN", "TATAMOTORS": "AUTO"
}

PORTFOLIO_CAPITAL = 1000000.0
MAX_CONCURRENT_POSITIONS = 4
SLOT_CAPITAL = 250000.0  # ₹2,50,000 per slot allocation

def calculate_zerodha_equity_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = min(40.0, 0.0003 * turnover)    # ₹20 flat per order leg cap
    stt = 0.00025 * exit_val                     # 0.025% on sell side
    exchange_txn = 0.0000325 * turnover          # NSE turnover charge
    sebi = 0.000001 * turnover                   # SEBI turnover charge
    stamp_duty = 0.00003 * entry_val             # 0.003% on buy side
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
    df['atr_baseline'] = df['atr'].rolling(40).mean().replace(0, 1e-5)
    df['vol_ratio'] = (df['atr'] / df['atr_baseline']).replace(0, 1.0)
    
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

class TimeDecayGovernor:
    def get_time_decay_factor(self, current_time) -> float:
        curr_mins = current_time.hour * 60 + current_time.minute
        cutoff_mins = 15 * 60 + 15
        start_decay = 12 * 60 + 30
        
        if curr_mins < start_decay:
            return 1.0
        remaining = max(0, cutoff_mins - curr_mins)
        total_window = cutoff_mins - start_decay
        return float(np.clip(remaining / total_window, 0.25, 1.0))

    def compute_scheduled_targets(self, vol_ratio: float, current_time) -> tuple[float, float]:
        decay = self.get_time_decay_factor(current_time)
        base_r = 1.30 * np.clip(vol_ratio, 0.9, 1.4)
        base_z = 0.50
        
        decayed_r = max(0.40, base_r * decay)
        decayed_z = -0.10 if decay < 0.50 else base_z * decay
        return float(decayed_r), float(decayed_z)

def run():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    
    print(f"Precomputing features for refined universe: {TARGET_SYMBOLS}...")
    symbol_frames = {}
    for sym in TARGET_SYMBOLS:
        matches = list(data_dir.glob(f"*_{sym}_*.csv"))
        if not matches:
            continue
        fpath = matches[0]
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
    print(f"Loaded {len(symbol_frames)} symbols across {len(all_timestamps):,} bars.")
    
    governor = TimeDecayGovernor()
    active_positions = {}
    closed_trades = []
    armed_symbols = {}
    
    print("\nExecuting Refined Allocation Replay (Slot Capital: ₹2,50,000)...")
    
    for t in all_timestamps:
        symbols_to_close = []
        
        # 1. EVALUATE ACTIVE POSITIONS
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
            
            # Session Cutoff
            if bar_time >= pd.to_datetime('15:15:00').time():
                symbols_to_close.append((sym, bar_close, "SESSION_1515_SQUAREOFF"))
                continue
                
            # Stop Execution
            if bar_low <= pos['stop_price']:
                fill = min(bar_open, pos['stop_price']) - (0.05 * risk_ticks)
                symbols_to_close.append((sym, fill, "STOP_TRIGGERED"))
                continue
                
            curr_peak_r = (bar_high - pos['entry_price']) / risk_ticks
            curr_r = (bar_close - pos['entry_price']) / risk_ticks
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)
            
            r_target, z_target = governor.compute_scheduled_targets(bar['vol_ratio'], bar_time)
            
            if curr_peak_r >= r_target:
                fill = pos['entry_price'] + (r_target * risk_ticks)
                symbols_to_close.append((sym, fill, "DYNAMIC_R_TARGET"))
                continue
                
            if bar['vwap_zscore'] >= z_target and curr_r >= 0.20:
                symbols_to_close.append((sym, bar_close, "DYNAMIC_Z_TARGET"))
                continue
                
            if pos['peak_r'] >= 1.0:
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
                'entry_time': p['entry_time'],
                'exit_time': t,
                'entry_price': p['entry_price'],
                'exit_price': fill_p,
                'shares': p['shares'],
                'risk_inr': risk_inr,
                'gross_pnl': gross_pnl,
                'friction': friction,
                'net_pnl': net_pnl,
                'net_r': net_pnl / max(1.0, risk_inr),
                'exit_reason': reason
            })
            
        # 2. ENTRY EVALUATION WITH SECTOR CONCENTRATION INTERLOCK
        available_slots = MAX_CONCURRENT_POSITIONS - len(active_positions)
        if available_slots <= 0:
            continue
            
        active_sectors = [SECTOR_MAP.get(s, "OTHER") for s in active_positions.keys()]
        
        for sym, df in symbol_frames.items():
            if sym in active_positions or t not in df.index:
                continue
                
            sym_sector = SECTOR_MAP.get(sym, "OTHER")
            if active_sectors.count(sym_sector) >= 1:
                continue
                
            loc_idx = df.index.get_loc(t)
            if loc_idx < 2 or loc_idx + 1 >= len(df):
                continue
                
            curr_bar = df.iloc[loc_idx]
            prev_bar = df.iloc[loc_idx - 1]
            
            if curr_bar['time_only'] >= pd.to_datetime('13:30:00').time():
                if sym in armed_symbols:
                    del armed_symbols[sym]
                continue
                
            if curr_bar['vwap_zscore'] < -2.2 and curr_bar['rsi_14'] < 32:
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
                    
                    # ₹2,50,000 slot allocation
                    shares = int(SLOT_CAPITAL / entry_p)
                    
                    if shares >= 1:
                        active_positions[sym] = {
                            'entry_time': next_bar.name,
                            'entry_price': entry_p,
                            'stop_price': entry_p - risk_t,
                            'risk_ticks': risk_t,
                            'shares': shares,
                            'peak_r': 0.0
                        }
                        del armed_symbols[sym]
                        active_sectors.append(sym_sector)
                        available_slots -= 1
                        if available_slots <= 0:
                            break

    print("\n" + "=" * 75)
    print("REFINED UNIVERSE (NO SBIN/RELIANCE) + ₹2.5L ALLOCATION RESULTS")
    print("=" * 75)
    print(f"Total Completed Trades        : {len(closed_trades)}")
    
    if closed_trades:
        tdf = pd.DataFrame(closed_trades)
        total = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100
        gross_pnl = tdf['gross_pnl'].sum()
        friction = tdf['friction'].sum()
        net_pnl = tdf['net_pnl'].sum()
        net_exp = tdf['net_r'].mean()
        avg_fee = tdf['friction'].mean()
        
        print(f"Win Rate                      : {wr:.2f}%")
        print(f"Total Gross P&L               : ₹{gross_pnl:+,.2f}")
        print(f"Total Statutory Fees          : ₹{friction:,.2f}")
        print(f"Total Net Portfolio P&L       : ₹{net_pnl:+,.2f}")
        print(f"Average Net Expectancy        : {net_exp:+.3f}R / trade")
        print(f"Average Fee per Trade         : ₹{avg_fee:.2f}")
        print("-" * 75)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<30}: {cnt:<3} trades ({cnt/total*100:.1f}%)")
        print("-" * 75)
        print("Performance by Symbol:")
        for sym, g in tdf.groupby('symbol'):
            sym_wr = (g['net_pnl'] > 0).mean() * 100
            print(f"  • {sym:<10}: {len(g):<2} trades | WR: {sym_wr:5.1f}% | Avg Risk: ₹{g['risk_inr'].mean():6.1f} | Net: ₹{g['net_pnl'].sum():+10,.2f}")
            
        tdf.to_parquet('top10_refined_july2023.parquet')
        print("\nSaved detailed ledger to 'top10_refined_july2023.parquet'")
    print("=" * 75)

if __name__ == '__main__':
    run()
