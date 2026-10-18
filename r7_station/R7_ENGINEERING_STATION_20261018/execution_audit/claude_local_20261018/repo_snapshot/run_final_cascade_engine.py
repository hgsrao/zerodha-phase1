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
    """
    Continuous PID feedback controller replacing binary volume check,
    scaling position size multiplier proportionally with institutional accumulation.
    """
    def __init__(self, kp: float = 0.5, ki: float = 0.1, setpoint: float = 1.2):
        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint
        self.integral = 0.0

    def compute_size_multiplier(self, vol_flow_ratio: float) -> tuple[float, bool]:
        error = vol_flow_ratio - self.setpoint
        self.integral += error
        output = (self.kp * error) + (self.ki * self.integral)
        
        # Position size multiplier clamped between 0.5x and 2.0x
        size_mult = float(np.clip(1.0 + output, 0.5, 2.0))
        is_valid = vol_flow_ratio >= 1.05  # Institutional accumulation gate
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

class UltimateCascadeTradingEngine:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
        self.studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
        self.exit_pid = ExitPIDController(kp=0.4, ki=0.05, target_z_recovery=-0.3)
        self.volume_pid = VolumeFlowRatioPID(kp=0.5, ki=0.1, setpoint=1.2)
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
        
        # Volume Flow Ratio: Volume relative to 50-bar rolling median
        df['vol_median'] = df['volume'].rolling(50).median().bfill()
        df['vol_flow_ratio'] = df['volume'] / df['vol_median']
        
        # Rolling Percentile RSI (100-bar lookback, bottom 5th percentile)
        rolling_rsi_window = 100
        df['rsi_percentile'] = df['rsi_14'].rolling(rolling_rsi_window).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x) > 0 else 0.5, raw=False
        ).bfill()
        
        last_exit_pos = -999
        
        for pos in range(120, len(df) - 40):
            if pos < last_exit_pos + 15:
                continue
                
            row = df.iloc[pos]
            vol_r = row['vol_ratio'] if not np.isnan(row['vol_ratio']) else 1.0
            vol_flow = row['vol_flow_ratio'] if not np.isnan(row['vol_flow_ratio']) else 1.0
            
            # 1. Outer Loop: Lifecycle PID
            if len(self.executed_trades) >= 5:
                rolling_exp = np.mean([t['outcome_r'] for t in self.executed_trades[-10:]])
                lifecycle_output, _ = self.lifecycle_pid.update(rolling_exp, vol_r)
                dynamic_z_bias = np.clip(-2.5 + lifecycle_output, -3.6, -2.2)
            else:
                dynamic_z_bias = -2.5
                
            # 2. Inner Loop: Studies PID
            current_z = row['vwap_zscore']
            studies_output, _ = self.studies_pid.update(current_z, vol_r)
            
            # 3. Predictive Layer: MPC Trajectory
            price_window = df['close'].iloc[pos : pos+5].values
            predicted_drift, mpc_confidence = self.mpc_engine.optimize_trajectory(price_window, current_z)
            
            # 4. Volume Flow Ratio PID Gate & Sizing
            size_multiplier, is_vol_valid = self.volume_pid.compute_size_multiplier(vol_flow)
            
            # Cascaded Entry Gates
            is_lifecycle = current_z < dynamic_z_bias
            is_studies = row['rsi_percentile'] < 0.05 and studies_output > 1.0
            is_mpc = mpc_confidence > 0.0 and predicted_drift >= 0.0
            
            if is_lifecycle and is_studies and is_mpc and is_vol_valid:
                entry_price = row['close']
                current_atr = row['atr']
                if current_atr <= 0:
                    continue
                    
                future_window = df.iloc[pos+1 : pos+41]
                risk_ticks = (1.2 + 0.3 * (vol_r - 1.0)) * current_atr
                
                exit_r = 0.0
                exit_offset = 40
                
                for offset, (_, fut_row) in enumerate(future_window.iterrows(), start=1):
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_offset = offset
                        break
                    
                    dynamic_z_target = self.exit_pid.compute_dynamic_exit_threshold(fut_row['vwap_zscore'], vol_r)
                    if fut_row['vwap_zscore'] >= dynamic_z_target:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_offset = offset
                        break
                else:
                    final_close = future_window.iloc[-1]['close']
                    exit_r = np.clip((final_close - entry_price) / risk_ticks, -1.0, 2.0)
                    
                last_exit_pos = pos + exit_offset
                # Weighted outcome adjusted by volume flow sizing multiplier
                self.executed_trades.append({'outcome_r': exit_r * size_multiplier})

        self.print_performance()

    def print_performance(self):
        print('=' * 60)
        print(f'ULTIMATE CASCADE ENGINE PERFORMANCE REPORT: {self.symbol}')
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
    engine = UltimateCascadeTradingEngine('MARUTI')
    engine.execute_backtest()
