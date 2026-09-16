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

class GainScheduledPID:
    def __init__(self, kp: float = 0.50, ki: float = 0.08, setpoint: float = 0.0):
        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint
        self.integral = 0.0

    def update(self, current_z: float, vol_r: float) -> float:
        error = self.setpoint - current_z
        self.integral = np.clip(self.integral + error, -5.0, 5.0)
        kp = self.kp * (1.0 + 0.3 * max(0.0, vol_r - 1.0))
        return float((kp * error) + (self.ki * self.integral))

def compute_causal_features(df: pd.DataFrame) -> pd.DataFrame:
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
    
    df['vp'] = df['typ_price'] * df['volume']
    df['cum_vp'] = df.groupby('date_only')['vp'].cumsum()
    df['cum_v'] = df.groupby('date_only')['volume'].cumsum()
    df['vwap'] = df['cum_vp'] / df['cum_v'].replace(0, 1e-5)
    
    df['sq_diff_v'] = ((df['typ_price'] - df['vwap'])**2) * df['volume']
    df['cum_sq_diff'] = df.groupby('date_only')['sq_diff_v'].cumsum()
    df['vwap_std'] = np.sqrt(df['cum_sq_diff'] / df['cum_v'].replace(0, 1e-5)).replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

def run():
    print("Loading NIFTY Grid reference features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    trades = []
    
    print("Executing Phase 1: Honest Execution & True Fills (July 2023)...")
    
    for i, fpath in enumerate(csv_files, 1):
        symbol = fpath.name.split('_')[1]
        droop_r = MACHINE_DROOP_MAP.get(symbol, DEFAULT_DROOP)
        pid = GainScheduledPID()
        
        try:
            raw = pd.read_csv(fpath)
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            month = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(month) < 300:
                continue
            df = compute_causal_features(month)
        except Exception:
            continue
            
        last_exit_idx = -999
        
        for pos in range(0, len(df) - 45):
            if pos < last_exit_idx + 5:
                continue
            row = df.iloc[pos]
            t = row['dt']
            
            # Intraday cutoff: Do not enter new trades after 14:45 IST
            if row['time_only'] >= pd.to_datetime('14:45:00').time():
                continue
                
            if t not in grid_df.index:
                continue
            grid_row = grid_df.loc[t]
            
            # Entry Signal Check
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                # Directional check
                price_dir = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_dir = 1.0 if row['dv_dt'] > 0 else -1.0
                if row['dp_dt'] < 0 and (price_dir * volume_dir) < 0:
                    continue
                
                # Droop attenuation gate
                raw_u = pid.update(row['vwap_zscore'], row['vol_ratio'])
                droop_attenuation = (1.0 / droop_r) * max(0.0, -grid_row['f_grid'])
                u_eff = float(np.clip(raw_u - droop_attenuation, 0.0, 1.25))
                if u_eff < 0.70:
                    continue
                    
                # NEXT-BAR OPEN FILL
                entry_bar = df.iloc[pos + 1]
                entry_time = entry_bar['dt']
                entry_price = entry_bar['open']
                risk_ticks = 1.2 * row['atr']
                
                active_stop_price = entry_price - risk_ticks
                peak_favorable_r = 0.0
                exit_fill_price = None
                exit_reason = None
                exit_time = None
                bars_held = 0
                
                future_bars = df.iloc[pos + 1 : pos + 45]
                
                for offset, (_, bar) in enumerate(future_bars.iterrows()):
                    bars_held = offset
                    bar_open = bar['open']
                    bar_high = bar['high']
                    bar_low = bar['low']
                    bar_close = bar['close']
                    bar_time = bar['time_only']
                    
                    # 1. HARD SESSION CUTOFF (15:15 IST)
                    if bar_time >= pd.to_datetime('15:15:00').time():
                        exit_fill_price = bar_close
                        exit_reason = "SESSION_1515_SQUAREOFF"
                        exit_time = bar['dt']
                        break
                        
                    # 2. WORST-CASE STOP CHECK (Evaluate Bar Low against active stop first)
                    if bar_low <= active_stop_price:
                        # Realistic gap execution: min of open and stop minus slippage
                        fill = min(bar_open, active_stop_price) - (0.05 * risk_ticks)
                        exit_fill_price = fill
                        exit_reason = "STOP_TRIGGERED"
                        exit_time = bar['dt']
                        break
                        
                    # 3. PROFIT TARGETS & EQUILIBRIUM CHECK (Evaluated on Bar Close)
                    if bar['vwap_zscore'] >= -0.20:
                        exit_fill_price = bar_close
                        exit_reason = "VWAP_EQUILIBRIUM"
                        exit_time = bar['dt']
                        break
                        
                    # 4. UPDATE PEAK AND ARM NEW TRAILING STOPS (Effective on NEXT bar)
                    current_bar_peak_r = (bar_high - entry_price) / risk_ticks
                    peak_favorable_r = max(peak_favorable_r, current_bar_peak_r)
                    
                    # Arm Breakeven or Runaway trailing stops for subsequent candles
                    if peak_favorable_r >= 1.0:
                        new_stop = entry_price + (peak_favorable_r * 0.60 * risk_ticks)
                        active_stop_price = max(active_stop_price, new_stop)
                    elif peak_favorable_r >= 0.50:
                        new_stop = entry_price + (0.18 * risk_ticks)
                        active_stop_price = max(active_stop_price, new_stop)
                else:
                    # Time horizon expiration
                    last_bar = future_bars.iloc[-1]
                    exit_fill_price = last_bar['close']
                    exit_reason = "TIME_HORIZON_CLOSE"
                    exit_time = last_bar['dt']
                    
                last_exit_idx = pos + bars_held
                
                # Calculate True Gross R
                gross_r = (exit_fill_price - entry_price) / risk_ticks
                net_r = gross_r - 0.18  # Deduct 0.18R friction baseline
                
                trades.append({
                    'symbol': symbol,
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'entry_price': entry_price,
                    'exit_price': exit_fill_price,
                    'risk_ticks': risk_ticks,
                    'gross_r': gross_r,
                    'net_r': net_r,
                    'exit_reason': exit_reason,
                    'bars_held': bars_held
                })

    print("\n" + "=" * 75)
    print("PHASE 1: HONEST EXECUTION & REALISTIC FILLS BASELINE")
    print("=" * 75)
    print(f"Total Completed Trades : {len(trades):,}")
    
    if trades:
        tdf = pd.DataFrame(trades)
        total = len(tdf)
        wr = (tdf['net_r'] > 0).mean() * 100
        gross_exp = tdf['gross_r'].mean()
        net_exp = tdf['net_r'].mean()
        total_pnl = tdf['net_r'].sum()
        
        print("-" * 75)
        print(f"Win Rate         : {wr:.2f}%")
        print(f"Gross Expectancy : {gross_exp:+.3f}R / trade")
        print(f"Net Expectancy   : {net_exp:+.3f}R / trade")
        print(f"Total Net P&L    : {total_pnl:+.2f}R")
        print("-" * 75)
        print("Exit Distribution (Honest Real-Time Order Fills):")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<25}: {cnt:<5} trades ({cnt/total*100:.1f}%)")
            
        tdf.to_parquet('phase1_honest_trade_ledger.parquet')
        print(f"\nSaved trade audit ledger to 'phase1_honest_trade_ledger.parquet'")
    print("=" * 75)

if __name__ == '__main__':
    run()
