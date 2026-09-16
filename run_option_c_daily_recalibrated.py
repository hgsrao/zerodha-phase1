import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

def calculate_zerodha_delivery_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = 0.0                            # ₹0 brokerage on delivery equity
    stt = 0.001 * turnover                     # 0.1% on buy AND sell side
    exchange_txn = 0.0000325 * turnover        # NSE fee
    sebi = 0.000001 * turnover                 # SEBI charges
    stamp_duty = 0.00015 * entry_val           # 0.015% stamp duty on buy side
    depository_dp = 15.93                      # ₹13.50 + 18% GST DP charge on sell
    gst = 0.18 * (exchange_txn + sebi)
    return brokerage + stt + exchange_txn + sebi + stamp_duty + depository_dp + gst

def resample_ohlc(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['dt'] = pd.to_datetime(df[time_col]).dt.tz_localize(None)
    df = df.sort_values('dt').set_index('dt')
    
    ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    res = df.resample(freq).agg(ohlc).dropna().reset_index()
    res['date_only'] = res['dt'].dt.date
    return res

def precompute_instrument_features(raw_df: pd.DataFrame):
    # 1. Compute Daily Metrics directly on available trading days (min 3 days to initialize)
    df_daily = resample_ohlc(raw_df.copy(), '1D')
    if len(df_daily) < 3:
        return None
        
    df_daily['daily_ema_20'] = df_daily['close'].ewm(span=20, min_periods=3, adjust=False).mean()
    
    tr_d = np.maximum(
        df_daily['high'] - df_daily['low'],
        np.maximum(
            abs(df_daily['high'] - df_daily['close'].shift(1)),
            abs(df_daily['low'] - df_daily['close'].shift(1))
        )
    )
    df_daily['daily_atr'] = tr_d.rolling(14, min_periods=3).mean()
    df_daily['daily_atr'] = df_daily['daily_atr'].fillna(tr_d.expanding().mean())
    df_daily['prior_day_low'] = df_daily['low'].shift(1)
    df_daily['prior_day_close'] = df_daily['close'].shift(1)
    
    daily_lookup = df_daily.set_index('date_only')[['daily_ema_20', 'daily_atr', 'prior_day_low', 'prior_day_close']].to_dict('index')
    
    # 2. Compute 15-Min Execution Features
    df_15 = resample_ohlc(raw_df.copy(), '15min')
    df_15['time_only'] = df_15['dt'].dt.time
    
    delta = df_15['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df_15['rsi_14'] = 100 - (100 / (1 + rs))
    
    # Welford Intraday VWAP
    df_15['typ_price'] = (df_15['high'] + df_15['low'] + df_15['close']) / 3.0
    vwap_arr = np.zeros(len(df_15))
    std_arr = np.zeros(len(df_15))
    
    for _, idxs in df_15.groupby('date_only').groups.items():
        w_sum = 0.0
        mean = 0.0
        M2 = 0.0
        for i in idxs:
            p = df_15.at[i, 'typ_price']
            w = max(1.0, df_15.at[i, 'volume'])
            w_sum_old = w_sum
            w_sum += w
            delta_p = p - mean
            R = delta_p * w / w_sum
            mean += R
            M2 += w_sum_old * delta_p * R
            vwap_arr[i] = mean
            std_arr[i] = np.sqrt(M2 / w_sum) if w_sum > 0 and M2 > 0 else 1.0
            
    df_15['vwap'] = vwap_arr
    df_15['vwap_std'] = np.where(std_arr < 1e-4, 1.0, std_arr)
    df_15['vwap_zscore'] = (df_15['close'] - df_15['vwap']) / df_15['vwap_std']
    
    # Map Daily context into 15m execution bars
    df_15['daily_ema_20'] = df_15['date_only'].map(lambda d: daily_lookup.get(d, {}).get('daily_ema_20', np.nan))
    df_15['daily_atr'] = df_15['date_only'].map(lambda d: daily_lookup.get(d, {}).get('daily_atr', np.nan))
    df_15['prior_day_low'] = df_15['date_only'].map(lambda d: daily_lookup.get(d, {}).get('prior_day_low', np.nan))
    
    df_15['daily_ema_20'] = df_15['daily_ema_20'].bfill().ffill()
    df_15['daily_atr'] = df_15['daily_atr'].bfill().ffill()
    
    return df_15.dropna().set_index('dt')

def run():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print(f"Discovered {len(csv_files)} CSV files in data repository...")
    symbol_frames = {}
    for fpath in csv_files:
        sym = fpath.name.split('_')[1]
        try:
            raw = pd.read_csv(fpath)
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(m) > 300:
                frame = precompute_instrument_features(m)
                if frame is not None and len(frame) > 40:
                    symbol_frames[sym] = frame
        except Exception:
            continue
            
    if not symbol_frames:
        print("CRITICAL: No symbol frames could be processed. Verify directory paths and dates.")
        return
        
    all_timestamps = sorted(list(set().union(*[set(df.index) for df in symbol_frames.values()])))
    print(f"Successfully loaded {len(symbol_frames)} assets across {len(all_timestamps):,} chronological 15m bars.")
    
    MAX_CONCURRENT_POSITIONS = 5
    PORTFOLIO_EQUITY = 500000.0
    ALLOCATION_PER_TRADE = PORTFOLIO_EQUITY / MAX_CONCURRENT_POSITIONS
    
    active_positions = {}
    closed_trades = []
    armed_symbols = {}
    
    print("Executing Recalibrated Option C: Daily Structural Stops + Daily 20-EMA Reversion Targets...")
    
    for t in all_timestamps:
        symbols_to_close = []
        
        # 1. EVALUATE ACTIVE MULTI-DAY SWINGS
        for sym, pos in active_positions.items():
            if t not in symbol_frames[sym].index:
                continue
            bar = symbol_frames[sym].loc[t]
            bar_open = bar['open']
            bar_high = bar['high']
            bar_low = bar['low']
            bar_close = bar['close']
            risk_ticks = pos['risk_ticks']
            pos['bars_held'] += 1
            
            # A. Structural Daily Stop Check
            if bar_low <= pos['stop_price']:
                fill = min(bar_open, pos['stop_price']) - (0.05 * risk_ticks)
                symbols_to_close.append((sym, fill, "DAILY_STOP_TRIGGERED"))
                continue
                
            # Current Return
            curr_peak_r = (bar_high - pos['entry_price']) / risk_ticks
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)
            
            # B. Target Harvest: Daily 20-EMA Reversion (Must deliver >= 1.5R gross profit)
            daily_ema = bar['daily_ema_20']
            if bar_close >= daily_ema and (bar_close - pos['entry_price']) >= (1.50 * risk_ticks):
                symbols_to_close.append((sym, bar_close, "DAILY_20EMA_REVERSION_TARGET"))
                continue
                
            # C. Asymmetric Expansion Harvest (2.5R)
            if curr_peak_r >= 2.50:
                fill = pos['entry_price'] + (2.50 * risk_ticks)
                symbols_to_close.append((sym, fill, "TARGET_HARVEST_2.5R"))
                continue
                
            # D. Dynamic Swing Ratchet: Lock +0.5R once +1.5R is reached; trail 50% above 2.0R
            if pos['peak_r'] >= 2.00:
                new_stop = pos['entry_price'] + (pos['peak_r'] * 0.50 * risk_ticks)
                pos['stop_price'] = max(pos['stop_price'], new_stop)
            elif pos['peak_r'] >= 1.50:
                new_stop = pos['entry_price'] + (0.50 * risk_ticks)
                pos['stop_price'] = max(pos['stop_price'], new_stop)
                
            # E. Max Holding Horizon: 10 Trading Days (250 bars of 15m)
            if pos['bars_held'] >= 250:
                symbols_to_close.append((sym, bar_close, "MAX_HOLDING_HORIZON_CLOSE"))
                continue
                
        for sym, fill_p, reason in symbols_to_close:
            p = active_positions.pop(sym)
            gross_pnl = (fill_p - p['entry_price']) * p['shares']
            friction = calculate_zerodha_delivery_friction(p['entry_price'] * p['shares'], fill_p * p['shares'])
            net_pnl = gross_pnl - friction
            risk_inr = p['risk_ticks'] * p['shares']
            
            closed_trades.append({
                'symbol': sym,
                'gross_pnl': gross_pnl,
                'friction': friction,
                'net_pnl': net_pnl,
                'net_r': net_pnl / max(1.0, risk_inr),
                'bars_held': p['bars_held'],
                'exit_reason': reason
            })
            
        # 2. EVALUATE SWING ENTRIES (ANSI 25 Synchrocheck on Oversold Setups)
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
            
            # Arming state: Oversold on 15m relative to daily context
            if curr_bar['vwap_zscore'] < -2.2 and curr_bar['rsi_14'] < 30.0:
                armed_symbols[sym] = {
                    'armed_time': t,
                    'swing_low': min(curr_bar['low'], prev_bar['low'])
                }
                
            # ANSI 25 Synchrocheck confirmation (C_t > H_{t-1})
            if sym in armed_symbols:
                armed_symbols[sym]['swing_low'] = min(armed_symbols[sym]['swing_low'], curr_bar['low'])
                
                if curr_bar['close'] > prev_bar['high'] and curr_bar['close'] > curr_bar['open']:
                    next_bar = df.iloc[loc_idx + 1]
                    entry_p = next_bar['open']
                    
                    # DAILY STRUCTURAL RISK GEOMETRY
                    prior_low = curr_bar['prior_day_low']
                    daily_atr = curr_bar['daily_atr']
                    atr_stop_distance = 1.50 * daily_atr if not np.isnan(daily_atr) else (0.025 * entry_p)
                    
                    if not np.isnan(prior_low) and prior_low < entry_p:
                        structural_stop = min(prior_low, entry_p - atr_stop_distance)
                    else:
                        structural_stop = entry_p - atr_stop_distance
                        
                    risk_ticks = max(entry_p - structural_stop, 0.015 * entry_p) # Minimum 1.5% stop buffer
                    shares = int(ALLOCATION_PER_TRADE / entry_p)
                    
                    if shares >= 1:
                        active_positions[sym] = {
                            'entry_price': entry_p,
                            'stop_price': entry_p - risk_ticks,
                            'risk_ticks': risk_ticks,
                            'shares': shares,
                            'peak_r': 0.0,
                            'bars_held': 0
                        }
                        del armed_symbols[sym]
                        available_slots -= 1
                        if available_slots <= 0:
                            break

    print("\n" + "=" * 80)
    print("OPTION C (RECALIBRATED): DAILY STRUCTURAL STOPS + DAILY 20-EMA TARGET")
    print("=" * 80)
    print(f"Total Completed Swings        : {len(closed_trades):,}")
    
    if closed_trades:
        tdf = pd.DataFrame(closed_trades)
        total = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100
        gross_pnl = tdf['gross_pnl'].sum()
        friction = tdf['friction'].sum()
        net_pnl = tdf['net_pnl'].sum()
        net_exp = tdf['net_r'].mean()
        avg_bars = tdf['bars_held'].mean()
        avg_market_hours = (avg_bars * 15) / 60.0
        
        print(f"Win Rate                      : {wr:.2f}%")
        print(f"Total Gross Swing Alpha       : ₹{gross_pnl:+,.2f}")
        print(f"Total Statutory Fees          : ₹{friction:,.2f}  (Friction Drag: {friction/max(1.0, abs(gross_pnl))*100:.1f}%)")
        print(f"Total Net Portfolio P&L       : ₹{net_pnl:+,.2f}")
        print(f"Average Net Expectancy        : {net_exp:+.3f}R / trade")
        print(f"Average Holding Duration      : {avg_market_hours:.1f} market hours (~{avg_market_hours/6.25:.1f} trading days)")
        print("-" * 80)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<32}: {cnt:<5} trades ({cnt/total*100:.1f}%)")
        tdf.to_parquet('option_c_daily_recalibrated_ledger.parquet')
        print("\nSaved trade ledger to 'option_c_daily_recalibrated_ledger.parquet'")
    print("=" * 80)

if __name__ == '__main__':
    run()
