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

class DynamicExitGovernor:
    def __init__(self, friction_floor_inr: float = 85.0):
        self.friction_floor_inr = friction_floor_inr

    def compute_scheduled_targets(self, vol_ratio: float, nifty_vel: float, risk_inr: float) -> tuple[float, float]:
        # Friction penalty to ensure trade clears statutory hurdle
        friction_r_penalty = (2.5 * self.friction_floor_inr) / max(100.0, risk_inr)
        
        # 1. Dynamic R-target: Scales with volatility expansion
        r_target = max(1.10, 0.75 + friction_r_penalty) * np.clip(vol_ratio, 0.85, 1.45)
        
        # 2. Dynamic Z-target: Scales with NIFTY grid velocity
        z_target = 0.20 + (150.0 * max(0.0, nifty_vel)) + (0.30 * max(0.0, vol_ratio - 1.0))
        z_target = float(np.clip(z_target, 0.10, 1.20))
        
        return float(r_target), float(z_target)

def run():
    print("Loading 15m NIFTY Grid reference features...")
    grid_raw = pd.read_parquet('nifty_grid_features.parquet')
    grid_raw['dt'] = pd.to_datetime(grid_raw['dt']).dt.tz_localize(None)
    # Resample NIFTY features to 15m
    grid_15m = grid_raw.set_index('dt').resample('15min').agg({'f_grid': 'mean'}).dropna()
    
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print("Precomputing 15m Welford features...")
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
    print(f"Loaded {len(symbol_frames)} symbols across {len(all_timestamps):,} bars.")
    
    MAX_CONCURRENT_POSITIONS = 5
    PORTFOLIO_EQUITY = 500000.0
    ALLOCATION_PER_TRADE = PORTFOLIO_EQUITY / MAX_CONCURRENT_POSITIONS
    
    governor = DynamicExitGovernor(friction_floor_inr=85.0)
    active_positions = {}
    closed_trades = []
    armed_symbols = {}
    
    print("\nExecuting Option B with Dynamic Parameter Scheduling...")
    
    for t in all_timestamps:
        nifty_vel = grid_15m.loc[t, 'f_grid'] if t in grid_15m.index else 0.0
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
            risk_inr = risk_ticks * pos['shares']
            
            # Session Cutoff
            if bar_time >= pd.to_datetime('15:15:00').time():
                symbols_to_close.append((sym, bar_close, "SESSION_1515_SQUAREOFF"))
                continue
                
            # Worst-Case Stop Execution
            if bar_low <= pos['stop_price']:
                fill = min(bar_open, pos['stop_price']) - (0.05 * risk_ticks)
                symbols_to_close.append((sym, fill, "STOP_TRIGGERED"))
                continue
                
            # Update peak MFE
            curr_peak_r = (bar_high - pos['entry_price']) / risk_ticks
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)
            curr_r = (bar_close - pos['entry_price']) / risk_ticks
            
            # Dynamic Target Scheduling
            r_target, z_target = governor.compute_scheduled_targets(bar['vol_ratio'], nifty_vel, risk_inr)
            
            # Condition A: Peak achieved dynamic R target
            if curr_peak_r >= r_target:
                fill = pos['entry_price'] + (r_target * risk_ticks)
                symbols_to_close.append((sym, fill, f"DYNAMIC_R_TARGET_{r_target:.2f}R"))
                continue
                
            # Condition B: Price reached dynamic Z target (must have minimum positive return)
            if bar['vwap_zscore'] >= z_target and curr_r >= 0.40:
                symbols_to_close.append((sym, bar_close, f"DYNAMIC_Z_TARGET_{z_target:.2f}"))
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
            
        # 2. EVALUATE ENTRIES (ANSI 25 SYNCHROCHECK)
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

    print("\n" + "=" * 75)
    print("OPTION B: DYNAMIC PARAMETER SCHEDULING RESULTS")
    print("=" * 75)
    print(f"Total Closed Trades           : {len(closed_trades):,}")
    
    if closed_trades:
        tdf = pd.DataFrame(closed_trades)
        total = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100
        gross_pnl = tdf['gross_pnl'].sum()
        friction = tdf['friction'].sum()
        net_pnl = tdf['net_pnl'].sum()
        net_exp = tdf['net_r'].mean()
        
        print(f"Win Rate                      : {wr:.2f}%")
        print(f"Total Gross P&L               : ₹{gross_pnl:+,.2f}")
        print(f"Total Statutory Fees          : ₹{friction:,.2f}")
        print(f"Total Net Portfolio P&L       : ₹{net_pnl:+,.2f}")
        print(f"Average Net Expectancy        : {net_exp:+.3f}R / trade")
        print("-" * 75)
        print("Top Exit Reason Types:")
        print(tdf['exit_reason'].apply(lambda x: x.split('_')[0] + '_' + x.split('_')[1]).value_counts())
    print("=" * 75)

if __name__ == '__main__':
    run()
