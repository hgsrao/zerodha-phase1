import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

LOT_SIZE = 50  # NIFTY Index Futures lot size

def calculate_zerodha_futures_friction(entry_price: float, exit_price: float, qty: int = LOT_SIZE) -> float:
    entry_val = entry_price * qty
    exit_val = exit_price * qty
    turnover = entry_val + exit_val
    
    brokerage = 40.0                            # ₹20 buy + ₹20 sell flat
    stt = 0.000125 * exit_val                   # 0.0125% on sell side
    exchange_txn = 0.000019 * turnover          # NSE index futures turnover charge
    sebi = 0.000001 * turnover                  # SEBI turnover charge
    stamp_duty = 0.00002 * entry_val            # 0.002% on buy side
    gst = 0.18 * (brokerage + exchange_txn + sebi)
    
    return brokerage + stt + exchange_txn + sebi + stamp_duty + gst

def load_and_resample_nifty_15m() -> pd.DataFrame:
    grid_raw = pd.read_parquet('nifty_grid_features.parquet')
    time_col = [c for c in grid_raw.columns if c.lower() in ['dt', 'date', 'datetime', 'timestamp']][0]
    grid_raw['dt'] = pd.to_datetime(grid_raw[time_col]).dt.tz_localize(None)
    
    # Filter July 2023 baseline period
    m = grid_raw[(grid_raw['dt'] >= '2023-07-03') & (grid_raw['dt'] <= '2023-08-04')].copy()
    m = m.sort_values('dt').set_index('dt')
    
    # If standard OHLC not in grid, synthesize or extract from price columns
    price_col = 'close' if 'close' in m.columns else ('price' if 'price' in m.columns else m.columns[0])
    
    if 'open' in m.columns:
        ohlc_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
        if 'volume' in m.columns:
            ohlc_dict['volume'] = 'sum'
        df_15 = m.resample('15min').agg(ohlc_dict).dropna().reset_index()
    else:
        df_15 = m[[price_col]].resample('15min').agg({
            price_col: ['first', 'max', 'min', 'last']
        }).dropna()
        df_15.columns = ['open', 'high', 'low', 'close']
        df_15['volume'] = 50000.0  # nominal proxy volume if absent
        df_15 = df_15.reset_index()

    if 'volume' not in df_15.columns:
        df_15['volume'] = 50000.0

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

class TimeDecayFuturesGovernor:
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
        decayed_z = -0.10 if decay < 0.5 else base_z * decay
        return float(decayed_r), float(decayed_z)

def run():
    print("Loading and resampling NIFTY continuous data to 15m...")
    df_raw = load_and_resample_nifty_15m()
    df = compute_welford_15min_features(df_raw).set_index('dt')
    print(f"Computed features across {len(df):,} 15-minute bars.")
    
    governor = TimeDecayFuturesGovernor()
    active_position = None
    closed_trades = []
    armed_state = None
    
    timestamps = list(df.index)
    
    print("Executing NIFTY Index Futures Replay (July 2023)...")
    
    for i in range(2, len(timestamps) - 1):
        t = timestamps[i]
        bar = df.loc[t]
        bar_open = bar['open']
        bar_high = bar['high']
        bar_low = bar['low']
        bar_close = bar['close']
        bar_time = bar['time_only']
        
        # 1. EVALUATE ACTIVE FUTURES POSITION
        if active_position is not None:
            risk_ticks = active_position['risk_ticks']
            
            # A. Hard Session Cutoff
            if bar_time >= pd.to_datetime('15:15:00').time():
                fill_p = bar_close
                gross_pnl = (fill_p - active_position['entry_price']) * LOT_SIZE
                friction = calculate_zerodha_futures_friction(active_position['entry_price'], fill_p)
                closed_trades.append({
                    'entry_time': active_position['entry_time'],
                    'exit_time': t,
                    'entry_price': active_position['entry_price'],
                    'exit_price': fill_p,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': gross_pnl - friction,
                    'net_r': (gross_pnl - friction) / (risk_ticks * LOT_SIZE),
                    'exit_reason': "SESSION_1515_SQUAREOFF"
                })
                active_position = None
                continue
                
            # B. Worst-Case Stop Execution
            if bar_low <= active_position['stop_price']:
                fill_p = min(bar_open, active_position['stop_price']) - 2.0  # 2 points slippage on NIFTY
                gross_pnl = (fill_p - active_position['entry_price']) * LOT_SIZE
                friction = calculate_zerodha_futures_friction(active_position['entry_price'], fill_p)
                closed_trades.append({
                    'entry_time': active_position['entry_time'],
                    'exit_time': t,
                    'entry_price': active_position['entry_price'],
                    'exit_price': fill_p,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': gross_pnl - friction,
                    'net_r': (gross_pnl - friction) / (risk_ticks * LOT_SIZE),
                    'exit_reason': "STOP_TRIGGERED"
                })
                active_position = None
                continue
                
            # C. Dynamic Target Evaluation
            curr_peak_r = (bar_high - active_position['entry_price']) / risk_ticks
            active_position['peak_r'] = max(active_position['peak_r'], curr_peak_r)
            curr_r = (bar_close - active_position['entry_price']) / risk_ticks
            
            r_target, z_target = governor.compute_scheduled_targets(bar['vol_ratio'], bar_time)
            
            if curr_peak_r >= r_target:
                fill_p = active_position['entry_price'] + (r_target * risk_ticks)
                gross_pnl = (fill_p - active_position['entry_price']) * LOT_SIZE
                friction = calculate_zerodha_futures_friction(active_position['entry_price'], fill_p)
                closed_trades.append({
                    'entry_time': active_position['entry_time'],
                    'exit_time': t,
                    'entry_price': active_position['entry_price'],
                    'exit_price': fill_p,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': gross_pnl - friction,
                    'net_r': (gross_pnl - friction) / (risk_ticks * LOT_SIZE),
                    'exit_reason': "DECAYED_R_TARGET"
                })
                active_position = None
                continue
                
            if bar['vwap_zscore'] >= z_target and curr_r >= 0.20:
                fill_p = bar_close
                gross_pnl = (fill_p - active_position['entry_price']) * LOT_SIZE
                friction = calculate_zerodha_futures_friction(active_position['entry_price'], fill_p)
                closed_trades.append({
                    'entry_time': active_position['entry_time'],
                    'exit_time': t,
                    'entry_price': active_position['entry_price'],
                    'exit_price': fill_p,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': gross_pnl - friction,
                    'net_r': (gross_pnl - friction) / (risk_ticks * LOT_SIZE),
                    'exit_reason': "DECAYED_Z_TARGET"
                })
                active_position = None
                continue
                
            # Trailing Profit Lock
            if active_position['peak_r'] >= 1.0:
                new_stop = active_position['entry_price'] + (active_position['peak_r'] * 0.50 * risk_ticks)
                active_position['stop_price'] = max(active_position['stop_price'], new_stop)

        # 2. EVALUATE ENTRY PERMISSIVE (ANSI 25 SYNCHROCHECK)
        if active_position is None:
            prev_bar = df.iloc[i - 1]
            
            if bar_time >= pd.to_datetime('13:30:00').time():
                armed_state = None
                continue
                
            # Arm on Oversold
            if bar['vwap_zscore'] < -2.0 and bar['rsi_14'] < 32:
                armed_state = {
                    'swing_low': min(bar['low'], prev_bar['low'])
                }
                
            # ANSI 25 Confirmation
            if armed_state is not None:
                armed_state['swing_low'] = min(armed_state['swing_low'], bar['low'])
                
                if bar['close'] > prev_bar['high'] and bar['close'] > bar['open']:
                    next_bar = df.iloc[i + 1]
                    entry_p = next_bar['open']
                    swing_low = armed_state['swing_low']
                    risk_t = max(entry_p - swing_low, 0.8 * bar['atr'])
                    
                    active_position = {
                        'entry_time': timestamps[i + 1],
                        'entry_price': entry_p,
                        'stop_price': entry_p - risk_t,
                        'risk_ticks': risk_t,
                        'peak_r': 0.0
                    }
                    armed_state = None

    print("\n" + "=" * 75)
    print("NIFTY INDEX FUTURES: TIME-DECAY GOVERNOR RESULTS (JULY 2023)")
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
        
        print(f"Win Rate                      : {wr:.2f}%")
        print(f"Total Gross P&L               : ₹{gross_pnl:+,.2f}")
        print(f"Total Statutory Fees          : ₹{friction:,.2f}")
        print(f"Total Net Portfolio P&L       : ₹{net_pnl:+,.2f}")
        print(f"Average Net Expectancy        : {net_exp:+.3f}R / trade")
        print(f"Average Fee per Trade         : ₹{friction/total:.2f}")
        print("-" * 75)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<30}: {cnt:<3} trades ({cnt/total*100:.1f}%)")
        tdf.to_parquet('nifty_futures_july2023_ledger.parquet')
    print("=" * 75)

if __name__ == '__main__':
    run()
