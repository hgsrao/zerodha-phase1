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
    """
    Regulates exit timing and profit targets based on momentum deceleration
    (rate-of-change of VWAP z-score recovery).
    """
    def __init__(self, kp: float = 0.4, ki: float = 0.05, target_z_recovery: float = -0.3):
        self.kp = kp
        self.ki = ki
        self.target_z_recovery = target_z_recovery
        self.integral = 0.0
        self.prev_z = 0.0

    def compute_dynamic_exit_threshold(self, current_z: float, vol_ratio: float) -> float:
        # Rate of change of z-score (momentum deceleration)
        z_roc = current_z - self.prev_z
        self.prev_z = current_z
        
        # Error relative to target z-score recovery
        error = self.target_z_recovery - current_z
        self.integral += error
        
        # Gain schedule with volatility ratio
        kp_sched = self.kp * (1.0 + 0.2 * max(0.0, vol_ratio - 1.0))
        output = (kp_sched * error) + (self.ki * self.integral) + (0.5 * z_roc)
        
        # Adjust target z-score dynamically (-0.6 to -0.1 range)
        dynamic_target = self.target_z_recovery + np.clip(output, -0.3, 0.2)
        return float(np.clip(dynamic_target, -0.8, -0.1))

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

class ProductionEngineWithExitPID:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
        self.studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
        self.exit_pid = ExitPIDController(kp=0.4, ki=0.05, target_z_recovery=-0.3)
        self.mpc_engine = RecedingHorizonMPC(horizon=5, q_weight=1.0, r_weight=0.2)
        self.executed_trades = []

    def execute_backtest(self):
        parquet_path = Path(f'/home/shrinivas/ECS_Project_external_engine/mean_reversion/features_2026/train/{self.symbol}_mr_2026.parquet')
        if not parquet_path.exists():
            print(f"Data file not found for {self.symbol}")
            return
            
        df = pd.read_parquet(parquet_path)
        df['atr'] = (df['high'] - df['low']).rolling(14).mean().bfill()
        df['atr_baseline'] = df['atr'].rolling(100).mean().bfill()
        df['vol_ratio'] = df['atr'] / df['atr_baseline']
        df['vol_median'] = df['volume'].rolling(50).median()
        
        last_exit_pos = -999
        
        for pos in range(100, len(df) - 40):
            if pos < last_exit_pos + 15:
                continue
                
            row = df.iloc[pos]
            vol_r = row['vol_ratio'] if not np.isnan(row['vol_ratio']) else 1.0
            
            # Outer Loop: Lifecycle PID
            if len(self.executed_trades) >= 5:
                rolling_exp = np.mean([t['outcome_r'] for t in self.executed_trades[-10:]])
                lifecycle_output, _ = self.lifecycle_pid.update(rolling_exp, vol_r)
                dynamic_z_bias = np.clip(-2.5 + lifecycle_output, -3.6, -2.2)
            else:
                dynamic_z_bias = -2.5
                
            # Inner Loop: Studies PID
            current_z = row['vwap_zscore']
            studies_output, _ = self.studies_pid.update(current_z, vol_r)
            
            # Predictive Layer: MPC Trajectory
            price_window = df['close'].iloc[pos : pos+5].values
            predicted_drift, mpc_confidence = self.mpc_engine.optimize_trajectory(price_window, current_z)
            
            # Cascaded Entry Gates
            is_lifecycle = current_z < dynamic_z_bias
            is_studies = row['rsi_14'] < 28 and studies_output > 1.0
            is_mpc = mpc_confidence > 0.0 and predicted_drift >= 0.0
            is_vol = row['volume'] > row['vol_median'] if not np.isnan(row['vol_median']) else True
            
            if is_lifecycle and is_studies and is_mpc and is_vol:
                entry_price = row['close']
                current_atr = row['atr']
                if current_atr <= 0:
                    continue
                    
                future_window = df.iloc[pos+1 : pos+41]
                risk_ticks = (1.2 + 0.3 * (vol_r - 1.0)) * current_atr
                
                exit_r = 0.0
                exit_offset = 40
                
                for offset, (_, fut_row) in enumerate(future_window.iterrows(), start=1):
                    # Check stop loss
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_offset = offset
                        break
                    
                    # Dynamic Exit PID Target Check
                    dynamic_z_target = self.exit_pid.compute_dynamic_exit_threshold(fut_row['vwap_zscore'], vol_r)
                    if fut_row['vwap_zscore'] >= dynamic_z_target:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_offset = offset
                        break
                else:
                    final_close = future_window.iloc[-1]['close']
                    exit_r = np.clip((final_close - entry_price) / risk_ticks, -1.0, 2.0)
                    
                last_exit_pos = pos + exit_offset
                self.executed_trades.append({'outcome_r': exit_r})

        self.print_performance()

    def print_performance(self):
        print('=' * 60)
        print(f'EXIT PID ENGINE PERFORMANCE REPORT: {self.symbol}')
        print('=' * 60)
        if not self.executed_trades:
            print('No trades executed.')
            return
            
        trades = np.array([t['outcome_r'] for t in self.executed_trades])
        total = len(trades)
        wins = (trades > 0).sum()
        win_rate = wins / total
        gross_exp = trades.mean()
        net_exp = gross_exp - 0.18
        
        print(f'Total Trades: {total}')
        print(f'Win Rate: {win_rate*100:.2f}%')
        print(f'Gross Expectancy: {gross_exp:+.3f}R')
        print(f'Net Expectancy (post-friction): {net_exp:+.3f}R')
        print('=' * 60)

if __name__ == '__main__':
    engine = ProductionEngineWithExitPID('MARUTI')
    engine.execute_backtest()
