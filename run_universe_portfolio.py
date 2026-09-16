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

def run_universe_backtest():
    train_dir = Path('/home/shrinivas/ECS_Project_external_engine/mean_reversion/features_2026/train')
    parquet_files = list(train_dir.glob('*_mr_2026.parquet'))
    
    all_trades = []
    symbol_summaries = []
    
    print(f"Scanning {len(parquet_files)} asset parquet files across the universe...")
    
    for p_file in parquet_files:
        symbol = p_file.name.replace('_mr_2026.parquet', '')
        df = pd.read_parquet(p_file)
        if len(df) < 150:
            continue
            
        df['atr'] = (df['high'] - df['low']).rolling(14).mean().bfill()
        df['atr_baseline'] = df['atr'].rolling(100).mean().bfill()
        df['vol_ratio'] = df['atr'] / df['atr_baseline']
        df['vol_median'] = df['volume'].rolling(50).median()
        
        lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
        studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
        mpc_engine = RecedingHorizonMPC(horizon=5, q_weight=1.0, r_weight=0.2)
        
        symbol_trades = []
        last_exit_pos = -999
        
        for pos in range(100, len(df) - 40):
            if pos < last_exit_pos + 15:
                continue
                
            row = df.iloc[pos]
            vol_r = row['vol_ratio'] if not np.isnan(row['vol_ratio']) else 1.0
            
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
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_offset = offset
                        break
                    if fut_row['vwap_zscore'] >= -0.3:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_offset = offset
                        break
                else:
                    final_close = future_window.iloc[-1]['close']
                    exit_r = np.clip((final_close - entry_price) / risk_ticks, -1.0, 2.0)
                    
                last_exit_pos = pos + exit_offset
                trade_dict = {'symbol': symbol, 'outcome_r': exit_r}
                symbol_trades.append(trade_dict)
                all_trades.append(trade_dict)
        
        if symbol_trades:
            s_arr = np.array([t['outcome_r'] for t in symbol_trades])
            symbol_summaries.append({
                'Symbol': symbol,
                'Trades': len(s_arr),
                'WinRate_%': (s_arr > 0).mean() * 100,
                'Gross_Exp_R': s_arr.mean(),
                'Net_Exp_R': s_arr.mean() - 0.18
            })

    print("\n" + "=" * 70)
    print("AGGREGATED PORTFOLIO PERFORMANCE SUMMARY")
    print("=" * 70)
    if all_trades:
        all_arr = np.array([t['outcome_r'] for t in all_trades])
        total_trades = len(all_arr)
        portfolio_win_rate = (all_arr > 0).mean() * 100
        portfolio_gross_exp = all_arr.mean()
        portfolio_net_exp = portfolio_gross_exp - 0.18
        
        print(f"Total Universe Assets Processed: {len(symbol_summaries)}")
        print(f"Total Portfolio Trades Executed: {total_trades}")
        print(f"Aggregated Win Rate: {portfolio_win_rate:.2f}%")
        print(f"Aggregated Gross Expectancy: {portfolio_gross_exp:+.3f}R")
        print(f"Aggregated Net Expectancy (post-friction): {portfolio_net_exp:+.3f}R")
    else:
        print("No trades executed across the universe.")
    print("=" * 70)

if __name__ == '__main__':
    run_universe_backtest()
