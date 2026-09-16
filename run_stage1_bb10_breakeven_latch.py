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

class BlackBox10_BreakevenGovernor:
    def __init__(self, droop_r: float):
        self.droop_r = droop_r

    def evaluate_entry(self, raw_pid_u: float, nifty_vel: float) -> tuple[bool, float]:
        droop_attenuation = (1.0 / self.droop_r) * max(0.0, -nifty_vel)
        u_eff = float(np.clip(raw_pid_u - droop_attenuation, 0.0, 1.25))
        return (u_eff >= 0.70), u_eff

    def evaluate_in_trade(self, current_r: float, peak_r: float, z_score: float) -> tuple[bool, float, str]:
        # Tier 3: Full equilibrium reached
        if z_score >= -0.20:
            return True, current_r, "VWAP_EQUILIBRIUM_TARGET"

        # Tier 2: Major Gain Lock (Peak >= +1.0R)
        # Harvest if price retraces to 60% of peak
        if peak_r >= 1.0 and current_r <= peak_r * 0.60:
            return True, current_r, "GOVERNOR_RUNAWAY_PROFIT_HARVEST"

        # Tier 1: True Cost Breakeven Latch (Peak reached >= +0.50R)
        # Never let a +0.50R move become a loss; exit floor locked at +0.22R
        if peak_r >= 0.50 and current_r <= 0.22:
            return True, 0.22, "GOVERNOR_TRUE_BREAKEVEN_LATCH"

        # Emergency Overspeed Trip (only active if peak never reached +0.50R)
        if current_r <= -1.0:
            return True, -1.0, "EMERGENCY_OVERSPEED_TRIP"

        return False, current_r, "HOLD_VALVE_OPEN"

class GainScheduledPID:
    def __init__(self, kp: float = 0.50, ki: float = 0.08, setpoint: float = 0.0):
        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint
        self.integral = 0.0

    def update(self, current_z: float, vol_r: float) -> float:
        error = self.setpoint - current_z
        self.integral = np.clip(self.integral + error, -5.0, 5.0)  # Tighter anti-windup
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
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    trades = []
    
    print("Running Breakeven Floor Governor on Stage 1 (July 2023)...")
    
    for i, fpath in enumerate(csv_files, 1):
        symbol = fpath.name.split('_')[1]
        droop_r = MACHINE_DROOP_MAP.get(symbol, DEFAULT_DROOP)
        bb10 = BlackBox10_BreakevenGovernor(droop_r=droop_r)
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
            
        last_exit = -999
        
        for pos in range(0, len(df) - 45):
            if pos < last_exit + 15:
                continue
            row = df.iloc[pos]
            t = row['dt']
            
            if t not in grid_df.index:
                continue
            grid_row = grid_df.loc[t]
            
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                price_dir = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_dir = 1.0 if row['dv_dt'] > 0 else -1.0
                if row['dp_dt'] < 0 and (price_dir * volume_dir) < 0:
                    continue
                
                raw_u = pid.update(row['vwap_zscore'], row['vol_ratio'])
                permissive, u_eff = bb10.evaluate_entry(raw_u, grid_row['f_grid'])
                if not permissive:
                    continue
                    
                entry_bar = df.iloc[pos + 1]
                entry_price = entry_bar['open']
                risk_ticks = 1.2 * row['atr']
                future = df.iloc[pos + 1 : pos + 42]
                
                peak_r = 0.0
                exit_r = 0.0
                bars_held = 40
                exit_reason = 'TIME_HORIZON'
                
                for offset, (_, fut_row) in enumerate(future.iterrows(), start=1):
                    curr_close = fut_row['close']
                    current_r = (curr_close - entry_price) / risk_ticks
                    peak_r = max(peak_r, (fut_row['high'] - entry_price) / risk_ticks)
                    
                    should_exit, resolved_r, reason_label = bb10.evaluate_in_trade(
                        current_r=current_r,
                        peak_r=peak_r,
                        z_score=fut_row['vwap_zscore']
                    )
                    
                    if should_exit:
                        exit_r = resolved_r
                        exit_reason = reason_label
                        bars_held = offset
                        break
                else:
                    exit_r = (future.iloc[-1]['close'] - entry_price) / risk_ticks
                    
                last_exit = pos + bars_held
                gross_r = exit_r
                net_r = gross_r - 0.18
                
                trades.append({
                    'symbol': symbol,
                    'droop_r': droop_r,
                    'gross_r': gross_r,
                    'net_r': net_r,
                    'exit_reason': exit_reason
                })

    print("\n" + "=" * 75)
    print("STAGE 1 WITH BREAKEVEN FLOOR GOVERNOR RESULTS")
    print("=" * 75)
    print(f"Permitted Trades Executed: {len(trades)}")
    
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
        print("Machine P&L Breakdown by Inertia Class:")
        for r_val, grp in tdf.groupby('droop_r'):
            m_type = "GTG (4%)" if r_val == 0.04 else ("CSTG (5.5%)" if r_val == 0.055 else "BPSTG (7.5%)")
            print(f"  • {m_type:<14}: {len(grp):<4} trades | WR: {(grp['net_r'] > 0).mean()*100:5.1f}% | Net P&L: {grp['net_r'].sum():+.2f}R")
        print("-" * 75)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<33}: {cnt} trades ({cnt/total*100:.1f}%)")
    print("=" * 75)

if __name__ == '__main__':
    run()
