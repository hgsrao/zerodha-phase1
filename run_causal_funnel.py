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
    
    # 1. RSI (14) - Strictly forward causal
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    
    # 2. Rolling RSI Percentile (100-bar lookback, causal rank)
    df['rsi_percentile'] = df['rsi_14'].rolling(100).apply(
        lambda x: (x.argsort().argsort()[-1] + 1.0) / 100.0, raw=True
    )

    # 3. ATR (14) & Volatility baseline (100-bar)
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
    
    # 4. Volume median (50-bar)
    df['vol_median'] = df['volume'].rolling(50).median()
    df['vol_flow_ratio'] = df['volume'] / df['vol_median']
    
    # 5. Rolling VWAP & Z-score (375-bar window)
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3
    rolling_vol = df['volume'].rolling(375).sum()
    df['vwap'] = (df['typ_price'] * df['volume']).rolling(375).sum() / rolling_vol.replace(0, 1e-5)
    df['vwap_std'] = df['typ_price'].rolling(375).std().replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    # CAUSAL RULE: No .bfill(). Drop warmup bars entirely.
    return df.dropna().reset_index(drop=True)

def run_funnel_scan():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    if not csv_files:
        print(f"No CSV files found in {data_dir}")
        return
        
    print(f"Running causal funnel across {len(csv_files)} symbols (No lookahead, no bfill, Next-Bar fill)...")
    
    all_trade_records = []
    
    for i, file_path in enumerate(csv_files, 1):
        symbol = file_path.name.split('_')[1]
        print(f"[{i:02d}/{len(csv_files)}] Processing {symbol}...", end='\r')
        
        try:
            raw_df = pd.read_csv(file_path)
            df = compute_causal_features(raw_df)
            time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']]
            time_col = time_col[0] if time_col else df.columns[0]
        except Exception as e:
            continue
            
        if len(df) < 200:
            continue
            
        lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
        studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
        exit_pid = ExitPIDController(kp=0.4, ki=0.05, target_z_recovery=-0.3)
        volume_pid = VolumeFlowRatioPID(kp=0.5, ki=0.1, setpoint=1.2)
        
        symbol_outcomes = []
        last_exit_pos = -999
        
        for pos in range(0, len(df) - 45):
            if pos < last_exit_pos + 15:
                continue
                
            row = df.iloc[pos]
            vol_r = row['vol_ratio']
            vol_flow = row['vol_flow_ratio']
            
            # Causal state check
            if len(symbol_outcomes) >= 5:
                rolling_exp = np.mean(symbol_outcomes[-10:])
                lifecycle_output, _ = lifecycle_pid.update(rolling_exp, vol_r)
                dynamic_z_bias = np.clip(-2.5 + lifecycle_output, -3.6, -2.2)
            else:
                dynamic_z_bias = -2.5
                
            current_z = row['vwap_zscore']
            studies_output, _ = studies_pid.update(current_z, vol_r)
            size_multiplier, is_vol_valid = volume_pid.compute_size_multiplier(vol_flow)
            
            # Causal Entry Gate: Evaluated at bar pos Close
            signal_triggered = (
                current_z < dynamic_z_bias and 
                row['rsi_percentile'] < 0.05 and 
                studies_output > 1.0 and 
                is_vol_valid
            )
            
            if signal_triggered:
                # Execution at next bar Open (pos + 1)
                entry_bar = df.iloc[pos + 1]
                entry_price = entry_bar['open']
                current_atr = row['atr']
                
                if current_atr <= 0:
                    continue
                    
                risk_ticks = (1.2 + 0.3 * (vol_r - 1.0)) * current_atr
                future_window = df.iloc[pos + 1 : pos + 42]
                
                peak_high = entry_price
                trough_low = entry_price
                exit_r = 0.0
                exit_reason = 'TIME_HORIZON'
                bars_held = 40
                
                for offset, (_, fut_row) in enumerate(future_window.iterrows(), start=1):
                    peak_high = max(peak_high, fut_row['high'])
                    trough_low = min(trough_low, fut_row['low'])
                    
                    # Stop loss check (Conservative Stop Precedence)
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_reason = 'STOP_LOSS'
                        bars_held = offset
                        break
                    
                    # Target recovery check
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
                    
                mfe_r = (peak_high - entry_price) / risk_ticks
                mae_r = (entry_price - trough_low) / risk_ticks
                
                last_exit_pos = pos + bars_held
                gross_r = exit_r * size_multiplier
                net_r = gross_r - 0.18
                
                symbol_outcomes.append(gross_r)
                
                all_trade_records.append({
                    'symbol': symbol,
                    'entry_time': entry_bar[time_col],
                    'entry_price': entry_price,
                    'exit_reason': exit_reason,
                    'bars_held': bars_held,
                    'gross_r': gross_r,
                    'net_r': net_r,
                    'mfe_r': mfe_r,
                    'mae_r': mae_r,
                    'reached_025r': mfe_r >= 0.25,
                    'reached_050r': mfe_r >= 0.50,
                    'reached_100r': mfe_r >= 1.00
                })

    df_out = pd.DataFrame(all_trade_records)
    df_out.to_csv('causal_funnel_results.csv', index=False)
    print("\nSaved trade records to causal_funnel_results.csv")

if __name__ == '__main__':
    run_funnel_scan()
