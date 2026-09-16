from pathlib import Path
import pandas as pd
import numpy as np

from nautilus_sandbox.strategies.synchronized_mr_strategy import GainScheduledPID, RecedingHorizonMPC

def run_backtest_harness(symbol_str: str = 'MARUTI'):
    print('=' * 60)
    print(f'SYNCHRONIZED CONTROLLER BACKTEST HARNESS: {symbol_str}')
    print('=' * 60)
    
    parquet_path = Path('/home/shrinivas/ECS_Project_external_engine/mean_reversion/features_2026/train')
    df = pd.read_parquet(parquet_path / f'{symbol_str}_mr_2026.parquet')
    
    df['atr'] = (df['high'] - df['low']).rolling(14).mean().bfill()
    df['atr_baseline'] = df['atr'].rolling(100).mean().bfill()
    df['vol_ratio'] = df['atr'] / df['atr_baseline']
    df['vol_median'] = df['volume'].rolling(50).median()
    
    lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
    studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
    mpc_engine = RecedingHorizonMPC(horizon=5, q_weight=1.0, r_weight=0.2)
    
    executed_trades = []
    last_exit_pos = -999
    
    for pos in range(100, len(df) - 40):
        if pos < last_exit_pos + 15:
            continue
            
        row = df.iloc[pos]
        vol_r = row['vol_ratio'] if not np.isnan(row['vol_ratio']) else 1.0
        
        if len(executed_trades) >= 5:
            rolling_exp = np.mean([t['outcome_r'] for t in executed_trades[-10:]])
            lifecycle_output, _ = lifecycle_pid.update(rolling_exp, vol_r)
            dynamic_z_bias = -2.5 + lifecycle_output
            dynamic_z_bias = np.clip(dynamic_z_bias, -3.6, -2.2)
        else:
            dynamic_z_bias = -2.5
            
        current_z = row['vwap_zscore']
        studies_output, _ = studies_pid.update(current_z, vol_r)
        
        price_window = df['close'].iloc[pos : pos+5]
        predicted_drift, mpc_confidence = mpc_engine.optimize_trajectory(price_window, current_z)
        
        is_lifecycle_trigger = current_z < dynamic_z_bias
        is_studies_trigger = row['rsi_14'] < 28 and studies_output > 1.0
        is_mpc_trigger = mpc_confidence > 0.0 and predicted_drift >= 0.0
        is_vol_valid = row['volume'] > row['vol_median'] if not np.isnan(row['vol_median']) else True
        
        if is_lifecycle_trigger and is_studies_trigger and is_mpc_trigger and is_vol_valid:
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
                    profit_ticks = fut_row['close'] - entry_price
                    exit_r = profit_ticks / risk_ticks
                    exit_offset = offset
                    break
            else:
                final_close = future_window.iloc[-1]['close']
                exit_r = (final_close - entry_price) / risk_ticks
                exit_r = np.clip(exit_r, -1.0, 2.0)
                
            last_exit_pos = pos + exit_offset
            executed_trades.append({'outcome_r': exit_r})

    if not executed_trades:
        print('No trades executed.')
        return
        
    trades = np.array([t['outcome_r'] for t in executed_trades])
    total = len(trades)
    wins = (trades > 0).sum()
    win_rate = wins / total
    gross_exp = trades.mean()
    net_exp = gross_exp - 0.18
    
    print(f'Total Synchronized Harness Trades: {total}')
    print(f'Win Rate: {win_rate*100:.2f}%')
    print(f'Gross Expectancy: {gross_exp:+.3f}R')
    print(f'Net Expectancy (post-friction): {net_exp:+.3f}R')
    print('=' * 60)

if __name__ == '__main__':
    run_backtest_harness('MARUTI')
