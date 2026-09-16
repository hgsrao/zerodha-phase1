from pathlib import Path
import pandas as pd
import numpy as np

class ANSI25_SynchrocheckRelay:
    def __init__(self, delta_v_max: float = 0.10, delta_f_max: float = 0.10, delta_angle_deg_max: float = 10.0):
        self.delta_v_max = delta_v_max
        self.delta_f_max = delta_f_max
        self.delta_angle_max_deg = delta_angle_deg_max

    def check(self, v_gen: float, v_grid: float, 
              f_gen: float, f_grid: float, 
              angle_gen_rad: float, angle_grid_rad: float, 
              pid_output: float) -> tuple[bool, str]:
        
        # 1. Voltage mismatch (|Delta V| / V_grid)
        v_ref = max(v_grid, 0.1)
        delta_v_pct = abs(v_gen - v_grid) / v_ref
        if delta_v_pct > self.delta_v_max:
            return False, f"VOLTAGE_MISMATCH_{delta_v_pct*100:.1f}%"

        # 2. Slip frequency mismatch (|Delta f| / f_ref)
        f_ref = max(abs(f_grid), 0.0005)
        delta_f_pct = abs(f_gen - f_grid) / f_ref
        if delta_f_pct > self.delta_f_max:
            return False, f"SLIP_FREQ_MISMATCH_{delta_f_pct*100:.1f}%"

        # 3. Phase angle difference
        diff_rad = (angle_gen_rad - angle_grid_rad + np.pi) % (2 * np.pi) - np.pi
        diff_deg = abs(np.degrees(diff_rad))
        if diff_deg > self.delta_angle_max_deg:
            return False, f"PHASE_OUT_OF_STEP_{diff_deg:.1f}DEG"

        # 4. Studies PID check
        if pid_output <= 1.0:
            return False, "PID_THRESHOLD_NOT_MET"

        return True, "SYNCHRONIZED_PERMISSIVE"

class GainScheduledPID:
    def __init__(self, kp: float = 0.50, ki: float = 0.08, setpoint: float = 0.0):
        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint
        self.integral = 0.0
    def update(self, current_z: float, vol_r: float) -> float:
        error = self.setpoint - current_z
        self.integral += error
        kp = self.kp * (1.0 + 0.3 * max(0.0, vol_r - 1.0))
        return (kp * error) + (self.ki * self.integral)

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
    df['vol_ratio'] = df['atr'] / df['atr_baseline']
    
    df['vol_median'] = df['volume'].rolling(50).median()
    df['vol_flow_ratio'] = df['volume'] / df['vol_median']
    df['f_gen'] = df['close'].pct_change().fillna(0)
    df['delta_gen_rad'] = np.arctan2(df['f_gen'], np.maximum(0.01, df['vol_flow_ratio'] - 1.0))
    
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['dt'] = pd.to_datetime(df[time_col]).dt.tz_localize(None)
    df['date_only'] = df['dt'].dt.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    df['cum_vp'] = df.groupby('date_only').apply(lambda g: (g['typ_price'] * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['cum_v'] = df.groupby('date_only')['volume'].cumsum()
    df['vwap'] = df['cum_vp'] / df['cum_v'].replace(0, 1e-5)
    
    df['cum_sq_diff'] = df.groupby('date_only').apply(lambda g: ((g['typ_price'] - g['vwap'])**2 * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['vwap_std'] = np.sqrt(df['cum_sq_diff'] / df['cum_v'].replace(0, 1e-5)).replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

def run():
    print("Loading NIFTY Grid Features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    relay = ANSI25_SynchrocheckRelay(delta_v_max=0.10, delta_f_max=0.10, delta_angle_deg_max=10.0)
    
    trades = []
    rejections = {}
    
    print("Running Stage 1 (July 2023) with ANSI 25 Synchrocheck Relay...")
    
    for i, fpath in enumerate(csv_files, 1):
        symbol = fpath.name.split('_')[1]
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
            
        pid = GainScheduledPID()
        last_exit = -999
        
        for pos in range(0, len(df) - 45):
            if pos < last_exit + 15:
                continue
            row = df.iloc[pos]
            t = row['dt']
            
            if t not in grid_df.index:
                continue
            grid_row = grid_df.loc[t]
            
            # Base Oversold Signal
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                pid_out = pid.update(row['vwap_zscore'], row['vol_ratio'])
                
                # ANSI 25 Relay Permissive Evaluation
                permissive, reason = relay.check(
                    v_gen=row['vol_ratio'],
                    v_grid=grid_row['v_grid'],
                    f_gen=row['f_gen'],
                    f_grid=grid_row['f_grid'],
                    angle_gen_rad=row['delta_gen_rad'],
                    angle_grid_rad=grid_row['delta_grid_rad'],
                    pid_output=pid_out
                )
                
                if not permissive:
                    rejections[reason] = rejections.get(reason, 0) + 1
                    continue
                    
                entry_bar = df.iloc[pos + 1]
                entry_price = entry_bar['open']
                risk_ticks = 1.2 * row['atr']
                future = df.iloc[pos + 1 : pos + 42]
                
                exit_r = 0.0
                bars_held = 40
                exit_reason = 'TIME_HORIZON'
                
                for offset, (_, fut_row) in enumerate(future.iterrows(), start=1):
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_reason = 'STOP_LOSS'
                        bars_held = offset
                        break
                    if fut_row['vwap_zscore'] >= -0.3:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_reason = 'VWAP_Z_TARGET'
                        bars_held = offset
                        break
                else:
                    exit_r = (future.iloc[-1]['close'] - entry_price) / risk_ticks
                    
                last_exit = pos + bars_held
                gross_r = exit_r
                net_r = gross_r - 0.18
                trades.append({'symbol': symbol, 'gross_r': gross_r, 'net_r': net_r, 'exit_reason': exit_reason})

    print("\n" + "=" * 75)
    print("STAGE 1 WITH ANSI 25 SYNCHROCHECK RESULTS")
    print("=" * 75)
    print(f"Permitted Trades Closed: {len(trades)}")
    print("\nSynchrocheck Lockout / Rejection Breakdown:")
    for r, count in sorted(rejections.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {r:<30}: {count} candidate entries blocked")
        
    if trades:
        tdf = pd.DataFrame(trades)
        print("-" * 75)
        print(f"Win Rate         : {(tdf['net_r'] > 0).mean() * 100:.2f}%")
        print(f"Gross Expectancy : {tdf['gross_r'].mean():+.3f}R")
        print(f"Net Expectancy   : {tdf['net_r'].mean():+.3f}R")
        print(f"Total Net P&L    : {tdf['net_r'].sum():+.2f}R")
    print("=" * 75)

if __name__ == '__main__':
    run()
