import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

MACHINE_DROOP_MAP = {
    # GTG (4%)
    "HDFCBANK": 0.04, "ICICIBANK": 0.04, "SBIN": 0.04, "AXISBANK": 0.04, "KOTAKBANK": 0.04,
    "BAJFINANCE": 0.04, "BAJAJFINSV": 0.04, "TCS": 0.04, "INFY": 0.04, "TECHM": 0.04,
    "WIPRO": 0.04, "HCLTECH": 0.04, "TATASTEEL": 0.04, "JSWSTEEL": 0.04, "HINDALCO": 0.04,

    # CSTG (5.5%)
    "RELIANCE": 0.055, "LT": 0.055, "MARUTI": 0.055, "M&M": 0.055, "TATACONSUM": 0.055,
    "EICHERMOT": 0.055, "BAJAJ-AUTO": 0.055, "ADANIENT": 0.055, "ADANIPORTS": 0.055,
    "GRASIM": 0.055, "ULTRACEMCO": 0.055, "TITAN": 0.055,

    # BPSTG (7.5%)
    "ITC": 0.075, "HINDUNILVR": 0.075, "NESTLEIND": 0.075, "BRITANNIA": 0.075,
    "CIPLA": 0.075, "DRREDDY": 0.075, "SUNPHARMA": 0.075, "APOLLOHOSP": 0.075,
    "NTPC": 0.075, "POWERGRID": 0.075, "COALINDIA": 0.075, "ONGC": 0.075, "BPCL": 0.075
}
DEFAULT_DROOP = 0.055

class BlackBox10_SovereignDroopAuthority:
    """
    Stage 6 / Black Box 10: Sovereign Authority with Governor Droop Modulation.
    Regulates fuel valve demand and executes dynamic governor unloading.
    """
    def __init__(self, droop_r: float):
        self.droop_r = droop_r

    def evaluate_entry(self, raw_pid_u: float, nifty_vel: float) -> tuple[bool, float, str]:
        # Grid frequency droop attenuation
        droop_attenuation = (1.0 / self.droop_r) * max(0.0, -nifty_vel)
        u_eff = float(np.clip(raw_pid_u - droop_attenuation, 0.0, 1.25))
        
        # Permissive valve lift: requires at least 0.65 effective demand
        if u_eff >= 0.65:
            return True, u_eff, "VALVE_DISPATCH_PERMISSIVE"
        return False, u_eff, f"VALVE_LOCKOUT_UEFF_{u_eff:.2f}"

    def evaluate_in_trade(self, current_r: float, peak_r: float, raw_pid_u: float, nifty_vel: float, z_score: float) -> tuple[bool, str]:
        # Dynamic governor unloading:
        # If MFE >= +0.35R and momentum slows or price retraces 50% of peak, de-latch
        if peak_r >= 0.35:
            if current_r <= peak_r * 0.45 or z_score >= -0.60:
                return True, "GOVERNOR_UNLOAD_PROFIT_LATCH"

        # Equilibrium target touch
        if z_score >= -0.30:
            return True, "VWAP_EQUILIBRIUM_TARGET"

        # Emergency trip
        if current_r <= -1.0:
            return True, "EMERGENCY_OVERSPEED_TRIP"

        return False, "HOLD_VALVE_OPEN"

class GainScheduledPID:
    def __init__(self, kp: float = 0.50, ki: float = 0.08, setpoint: float = 0.0):
        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint
        self.integral = 0.0

    def update(self, current_z: float, vol_r: float) -> float:
        error = self.setpoint - current_z
        self.integral = np.clip(self.integral + error, -10.0, 10.0)  # Anti-windup clamp
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
    print("Loading NIFTY Grid features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    trades = []
    blocked_phase = 0
    blocked_bb10_valve = 0
    
    print("Running Stage 1 with Black Box 10 Sovereign Droop Authority...")
    
    for i, fpath in enumerate(csv_files, 1):
        symbol = fpath.name.split('_')[1]
        droop_r = MACHINE_DROOP_MAP.get(symbol, DEFAULT_DROOP)
        bb10 = BlackBox10_SovereignDroopAuthority(droop_r=droop_r)
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
            
            # Oversold trigger
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                
                # Phase alignment check
                price_dir = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_dir = 1.0 if row['dv_dt'] > 0 else -1.0
                if row['dp_dt'] < 0 and (price_dir * volume_dir) < 0:
                    blocked_phase += 1
                    continue
                
                # Raw PID Demand
                raw_u = pid.update(row['vwap_zscore'], row['vol_ratio'])
                
                # Black Box 10 Sovereign Droop Permissive Check
                permissive, u_eff, reason = bb10.evaluate_entry(raw_u, grid_row['f_grid'])
                if not permissive:
                    blocked_bb10_valve += 1
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
                    # Current price state
                    curr_close = fut_row['close']
                    current_r = (curr_close - entry_price) / risk_ticks
                    
                    # Update peak MFE
                    peak_r = max(peak_r, (fut_row['high'] - entry_price) / risk_ticks)
                    
                    # Check emergency low limit
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_reason = 'EMERGENCY_OVERSPEED_TRIP'
                        bars_held = offset
                        break
                        
                    # Fetch live grid velocity for in-trade droop evaluation
                    fut_t = fut_row['dt']
                    n_vel = grid_df.loc[fut_t, 'f_grid'] if fut_t in grid_df.index else 0.0
                    
                    # Black Box 10 Dynamic Governor Unload Evaluation
                    should_exit, reason_label = bb10.evaluate_in_trade(
                        current_r=current_r,
                        peak_r=peak_r,
                        raw_pid_u=raw_u,
                        nifty_vel=n_vel,
                        z_score=fut_row['vwap_zscore']
                    )
                    
                    if should_exit:
                        exit_r = current_r
                        exit_reason = reason_label
                        bars_held = offset
                        break
                else:
                    exit_r = (future.iloc[-1]['close'] - entry_price) / risk_ticks
                    
                last_exit = pos + bars_held
                
                # Modulated MW dispatch sizing
                mw_mult = float(np.clip(u_eff, 0.5, 1.2))
                gross_r = exit_r * mw_mult
                net_r = gross_r - (0.18 * mw_mult)
                
                trades.append({
                    'symbol': symbol,
                    'droop_r': droop_r,
                    'gross_r': gross_r,
                    'net_r': net_r,
                    'exit_reason': exit_reason,
                    'bars_held': bars_held
                })

    print("\n" + "=" * 75)
    print("STAGE 1 WITH BLACK BOX 10 SOVEREIGN DROOP AUTHORITY")
    print("=" * 75)
    print(f"Permitted Trades Executed    : {len(trades)}")
    print(f"Blocked - Phase Divergence   : {blocked_phase}")
    print(f"Throttled - BB10 Valve Lift  : {blocked_bb10_valve}")
    
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
            print(f"  • {r:<30}: {cnt} trades ({cnt/total*100:.1f}%)")
    print("=" * 75)

if __name__ == '__main__':
    run()
