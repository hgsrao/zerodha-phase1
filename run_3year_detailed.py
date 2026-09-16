from pathlib import Path
import pandas as pd
import numpy as np

# ==========================================
# 1. CASCADE ENGINE COMPONENTS
# ==========================================
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

class RecedingHorizonMPC:
    def __init__(self, horizon: int = 5, q_weight: float = 1.0, r_weight: float = 0.2):
        self.horizon = horizon
        self.q = q_weight
        self.r = r_weight
        
    def optimize_trajectory(self, price_series: np.ndarray, current_z: float) -> tuple[float, float]:
        if len(price_series) < self.horizon:
            return 0.0, 1.0
        dt_prices = np.diff(price_series) / price_series[:-1]
        predicted_drift = float(np.sum(dt_prices))
        tracking_error = abs(current_z)
        control_effort = abs(predicted_drift)
        cost = (self.q * tracking_error**2) + (self.r * control_effort**2)
        confidence = 1.0 / (1.0 + cost)
        return predicted_drift, confidence

# ==========================================
# 2. FEATURE GENERATION PIPELINE
# ==========================================
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = -delta.where(delta < 0, 0.0).rolling(window=14).mean()
    rs = gain / loss
    df['rsi_14'] = 100 - (100 / (1 + rs))
    df['rsi_14'] = df['rsi_14'].fillna(50)
    
    df['rsi_percentile'] = df['rsi_14'].rolling(100).apply(
        lambda x: (x.argsort().argsort()[-1] + 1.0) / 100.0, raw=True
    ).bfill()

    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = tr.rolling(14).mean().bfill()
    df['atr_baseline'] = df['atr'].rolling(100).mean().bfill()
    df['vol_ratio'] = df['atr'] / df['atr_baseline']
    
    df['vol_median'] = df['volume'].rolling(50).median().bfill()
    df['vol_flow_ratio'] = df['volume'] / df['vol_median']
    
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3
    rolling_vol = df['volume'].rolling(375).sum()
    df['vwap'] = (df['typ_price'] * df['volume']).rolling(375).sum() / rolling_vol
    df['vwap_std'] = df['typ_price'].rolling(375).std().replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    df['vwap_zscore'] = df['vwap_zscore'].bfill().fillna(0)
    
    return df.dropna().reset_index(drop=True)

# ==========================================
# 3. 3-YEAR DETAILED BACKTEST EXECUTION
# ==========================================
def run_3year_detailed():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = list(data_dir.glob('*.csv'))
    
    if not csv_files:
        print(f"No CSV files found in {data_dir}")
        return
        
    print(f"Discovered {len(csv_files)} historical files. Generating detailed trade log...\n")
    all_trades = []
    
    for i, file_path in enumerate(csv_files, 1):
        symbol = file_path.name.split('_')[1]
        print(f"[{i}/{len(csv_files)}] Processing {symbol} (3-Year Timeline)...", end='\r')
        
        try:
            raw_df = pd.read_csv(file_path)
            df = compute_features(raw_df)
            
            # Find the timestamp column safely
            time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']]
            time_col = time_col[0] if time_col else df.columns[0]
            
        except Exception as e:
            print(f"\nError processing {symbol}: {e}")
            continue
            
        lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
        studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
        exit_pid = ExitPIDController(kp=0.4, ki=0.05, target_z_recovery=-0.3)
        volume_pid = VolumeFlowRatioPID(kp=0.5, ki=0.1, setpoint=1.2)
        mpc_engine = RecedingHorizonMPC(horizon=5, q_weight=1.0, r_weight=0.2)
        
        symbol_trades = []
        last_exit_pos = -999
        
        for pos in range(375, len(df) - 40): 
            if pos < last_exit_pos + 15:
                continue
                
            row = df.iloc[pos]
            vol_r = row['vol_ratio'] if not np.isnan(row['vol_ratio']) else 1.0
            vol_flow = row['vol_flow_ratio'] if not np.isnan(row['vol_flow_ratio']) else 1.0
            
            if len(symbol_trades) >= 5:
                rolling_exp = np.mean([t['outcome_r'] for t in symbol_trades[-10:]])
                lifecycle_output, _ = lifecycle_pid.update(rolling_exp, vol_r)
                dynamic_z_bias = np.clip(-2.5 + lifecycle_output, -3.6, -2.2)
            else:
                dynamic_z_bias = -2.5
                
            current_z = row['vwap_zscore']
            studies_output, _ = studies_pid.update(current_z, vol_r)
            
            price_window = df['close'].iloc[pos : pos+5].values
            predicted_drift, mpc_confidence = mpc_engine.optimize_trajectory(price_window, current_z)
            
            size_multiplier, is_vol_valid = volume_pid.compute_size_multiplier(vol_flow)
            
            if current_z < dynamic_z_bias and row['rsi_percentile'] < 0.05 and studies_output > 1.0 and mpc_confidence > 0.0 and predicted_drift >= 0.0 and is_vol_valid:
                entry_price = row['close']
                current_atr = row['atr']
                if current_atr <= 0:
                    continue
                    
                future_window = df.iloc[pos+1 : pos+41]
                risk_ticks = (1.2 + 0.3 * (vol_r - 1.0)) * current_atr
                
                exit_price = 0.0
                exit_time = None
                exit_r = 0.0
                exit_offset = 40
                
                for offset, (_, fut_row) in enumerate(future_window.iterrows(), start=1):
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_price = fut_row['low']
                        exit_time = fut_row[time_col]
                        exit_offset = offset
                        break
                    
                    dynamic_z_target = exit_pid.compute_dynamic_exit_threshold(fut_row['vwap_zscore'], vol_r)
                    if fut_row['vwap_zscore'] >= dynamic_z_target:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_price = fut_row['close']
                        exit_time = fut_row[time_col]
                        exit_offset = offset
                        break
                else:
                    final_row = future_window.iloc[-1]
                    exit_r = np.clip((final_row['close'] - entry_price) / risk_ticks, -1.0, 2.0)
                    exit_price = final_row['close']
                    exit_time = final_row[time_col]
                    
                last_exit_pos = pos + exit_offset
                weighted_r = exit_r * size_multiplier
                net_r = weighted_r - 0.18 # Subtracting friction
                
                trade_record = {
                    'symbol': symbol,
                    'entry_time': row[time_col],
                    'entry_price': round(entry_price, 2),
                    'exit_time': exit_time,
                    'exit_price': round(exit_price, 2),
                    'size_multiplier': round(size_multiplier, 2),
                    'outcome_r': round(weighted_r, 3),
                    'net_r': round(net_r, 3)
                }
                
                symbol_trades.append(trade_record)
                all_trades.append(trade_record)
                
    # Final Output Formatting
    print("\n" + "=" * 70)
    print("3-YEAR PORTFOLIO SYMBOL-WISE SUMMARY")
    print("=" * 70)
    
    if all_trades:
        df_trades = pd.DataFrame(all_trades)
        
        # Save to CSV
        csv_out_path = '3year_detailed_trade_log.csv'
        df_trades.to_csv(csv_out_path, index=False)
        print(f"✅ Detailed trade log saved to: {csv_out_path}\n")
        
        print(f"{'SYMBOL':<15} | {'TRADES':<8} | {'WIN RATE':<10} | {'TOTAL NET P&L':<15}")
        print("-" * 70)
        
        for sym, grp in df_trades.groupby('symbol'):
            trades = len(grp)
            win_rate = (grp['net_r'] > 0).mean() * 100
            total_net_r = grp['net_r'].sum()
            print(f"{sym:<15} | {trades:<8} | {win_rate:>6.2f}%   | {total_net_r:>+9.3f} R")
            
        print("-" * 70)
        total_portfolio_trades = len(df_trades)
        portfolio_win_rate = (df_trades['net_r'] > 0).mean() * 100
        portfolio_total_net = df_trades['net_r'].sum()
        
        print(f"Total Portfolio Trades (3 Years) : {total_portfolio_trades}")
        print(f"Aggregated Portfolio Win Rate    : {portfolio_win_rate:.2f}%")
        print(f"Total Cumulative Net P&L         : {portfolio_total_net:+.3f} R")
    else:
        print("No trades executed across the 3-year timeline.")
    print("=" * 70)

if __name__ == '__main__':
    run_3year_detailed()
