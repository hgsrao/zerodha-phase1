from pathlib import Path
import pandas as pd
import numpy as np

class GainScheduledPID:
    def __init__(self, kp_base: float, ki_base: float, setpoint: float):
        self.kp_base = kp_base
        self.ki_base = ki_base
        self.setpoint = setpoint
        self.integral = 0.0
        
    def update(self, pv: float, vol_ratio: float) -> tuple[float, float]:
        error = self.setpoint - pv
        self.integral += error
        kp = self.kp_base * (1.0 + 0.3 * max(0.0, vol_ratio - 1.0))
        ki = self.ki_base / (1.0 + 0.1 * max(0.0, vol_ratio - 1.0))
        output = (kp * error) + (ki * self.integral)
        return output, error

class ExitPIDController:
    def __init__(self, kp: float = 0.4, ki: float = 0.05, target_z_recovery: float = -0.3):
        self.kp = kp
        self.ki = ki
        self.target_z_recovery = target_z_recovery
        self.integral = 0.0
        self.prev_z = 0.0

    def compute_dynamic_exit_threshold(self, current_z: float, vol_ratio: float) -> float:
        z_roc = current_z - self.prev_z
        self.prev_z = current_z
        error = self.target_z_recovery - current_z
        self.integral += error
        kp_sched = self.kp * (1.0 + 0.2 * max(0.0, vol_ratio - 1.0))
        output = (kp_sched * error) + (self.ki * self.integral) + (0.5 * z_roc)
        dynamic_target = self.target_z_recovery + np.clip(output, -0.3, 0.2)
        return float(np.clip(dynamic_target, -0.8, -0.1))

class VolumeFlowRatioPID:
    def __init__(self, kp: float = 0.5, ki: float = 0.1, setpoint: float = 1.2):
        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint
        self.integral = 0.0

    def compute_size_multiplier(self, vol_flow_ratio: float) -> tuple[float, bool]:
        error = vol_flow_ratio - self.setpoint
        self.integral += error
        output = (self.kp * error) + (self.ki * self.integral)
        size_mult = float(np.clip(1.0 + output, 0.5, 2.0))
        is_valid = vol_flow_ratio >= 1.05
        return size_mult, is_valid

def compute_causal_features(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
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
    
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['date_only'] = pd.to_datetime(df[time_col]).dt.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    df['cum_vp'] = df.groupby('date_only').apply(lambda g: (g['typ_price'] * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['cum_v'] = df.groupby('date_only')['volume'].cumsum()
    df['vwap'] = df['cum_vp'] / df['cum_v'].replace(0, 1e-5)
    
    df['cum_sq_diff'] = df.groupby('date_only').apply(lambda g: ((g['typ_price'] - g['vwap'])**2 * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['vwap_std'] = np.sqrt(df['cum_sq_diff'] / df['cum_v'].replace(0, 1e-5)).replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

def run_year_dryrun():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print(f"=== STAGE 2: 1-YEAR CAUSAL DRY RUN (July 2023 - June 2024) ===")
    all_trades = []
    
    for i, file_path in enumerate(csv_files, 1):
        symbol = file_path.name.split('_')[1]
        print(f"[{i:02d}/48] Loading {symbol} (1-Year)...", end='\r')
        
        try:
            raw_df = pd.read_csv(file_path)
            time_col = [c for c in raw_df.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw_df['dt'] = pd.to_datetime(raw_df[time_col])
            
            # Slice 1 full year
            year_df = raw_df[(raw_df['dt'] >= '2023-07-03') & (raw_df['dt'] <= '2024-06-28')].copy()
            if len(year_df) < 1000:
                continue
                
            df = compute_causal_features(year_df)
        except Exception:
            continue
            
        lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
        studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
        exit_pid = ExitPIDController(kp=0.4, ki=0.05, target_z_recovery=-0.3)
        volume_pid = VolumeFlowRatioPID(kp=0.5, ki=0.1, setpoint=1.2)
        
        symbol_trades = []
        last_exit_pos = -999
        
        for pos in range(0, len(df) - 45):
            if pos < last_exit_pos + 15:
                continue
                
            row = df.iloc[pos]
            vol_r = row['vol_ratio']
            vol_flow = row['vol_flow_ratio']
            
            if len(symbol_trades) >= 5:
                rolling_exp = np.mean([t['gross_r'] for t in symbol_trades[-10:]])
                lifecycle_output, _ = lifecycle_pid.update(rolling_exp, vol_r)
                dynamic_z_bias = np.clip(-2.5 + lifecycle_output, -3.6, -2.2)
            else:
                dynamic_z_bias = -2.5
                
            current_z = row['vwap_zscore']
            studies_output, _ = studies_pid.update(current_z, vol_r)
            size_multiplier, is_vol_valid = volume_pid.compute_size_multiplier(vol_flow)
            
            if current_z < dynamic_z_bias and row['rsi_percentile'] < 0.05 and studies_output > 1.0 and is_vol_valid:
                entry_bar = df.iloc[pos + 1]
                entry_price = entry_bar['open']
                current_atr = row['atr']
                if current_atr <= 0:
                    continue
                    
                risk_ticks = (1.2 + 0.3 * (vol_r - 1.0)) * current_atr
                future_window = df.iloc[pos + 1 : pos + 42]
                
                exit_r = 0.0
                bars_held = 40
                exit_reason = 'TIME_HORIZON'
                
                for offset, (_, fut_row) in enumerate(future_window.iterrows(), start=1):
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_reason = 'STOP_LOSS'
                        bars_held = offset
                        break
                    
                    dynamic_z_target = exit_pid.compute_dynamic_exit_threshold(fut_row['vwap_zscore'], vol_r)
                    if fut_row['vwap_zscore'] >= dynamic_z_target:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_reason = 'VWAP_Z_TARGET'
                        bars_held = offset
                        break
                else:
                    final_close = future_window.iloc[-1]['close']
                    exit_r = (final_close - entry_price) / risk_ticks
                    exit_reason = 'TIME_HORIZON'
                    bars_held = 40
                    
                last_exit_pos = pos + bars_held
                gross_r = exit_r * size_multiplier
                net_r = gross_r - 0.18
                
                trade = {
                    'symbol': symbol,
                    'gross_r': gross_r,
                    'net_r': net_r,
                    'exit_reason': exit_reason,
                    'bars_held': bars_held
                }
                symbol_trades.append(trade)
                all_trades.append(trade)

    print("\n" + "=" * 70)
    print("STAGE 2 (1-YEAR) DRY RUN PERFORMANCE SUMMARY")
    print("=" * 70)
    if all_trades:
        tdf = pd.DataFrame(all_trades)
        total_t = len(tdf)
        wr = (tdf['net_r'] > 0).mean() * 100
        gross_exp = tdf['gross_r'].mean()
        net_exp = tdf['net_r'].mean()
        total_pnl = tdf['net_r'].sum()
        
        print(f"Total Trades (1 Year): {total_t}")
        print(f"Win Rate: {wr:.2f}%")
        print(f"Gross Expectancy: {gross_exp:+.3f}R")
        print(f"Net Expectancy: {net_exp:+.3f}R")
        print(f"Total Net P&L: {total_pnl:+.2f}R")
        print("-" * 70)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<15}: {cnt} trades ({cnt/total_t*100:.1f}%)")
    else:
        print("No trades triggered during this 1-year test window.")
    print("=" * 70)

if __name__ == '__main__':
    run_year_dryrun()
