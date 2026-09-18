import json
import itertools
from pathlib import Path
import pandas as pd
import numpy as np
from run_stage1_full_ecs_dcs import compute_causal_features, ECSTradingSupervisor

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
GRID_DF = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')

# Define discrete search grid per sector type
PARAM_GRID = {
    'z_entry_threshold': [-2.6, -2.4, -2.2, -2.0],
    'sl_atr_mult': [1.8, 2.0, 2.2, 2.5],
    'target_zscore': [0.4, 0.6, 0.8],
    'max_bars_held': [30, 40, 50]
}

def simulate_symbol(df, params, supervisor):
    trades = []
    last_exit = -999
    z_in = params['z_entry_threshold']
    sl_m = params['sl_atr_mult']
    z_tgt = params['target_zscore']
    max_b = params['max_bars_held']

    for pos in range(0, len(df) - max_b - 5):
        if pos < last_exit + 15:
            continue
        row = df.iloc[pos]
        t = row['dt']
        if t not in GRID_DF.index:
            continue
            
        grid_row = GRID_DF.loc[t]

        # Supervisory Gate
        if row['vwap_zscore'] < z_in and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
            permissive, _, _ = supervisor.evaluate_signals(
                dp_dt=row['dp_dt'], dv_dt=row['dv_dt'],
                vol_ratio=row['vol_ratio'], nifty_vel=grid_row['f_grid']
            )
            if not permissive:
                continue

            entry_price = df.iloc[pos + 1]['open']
            risk = sl_m * row['atr']
            if risk <= 0:
                continue

            future = df.iloc[pos + 1 : pos + 1 + max_b]
            exit_r = 0.0
            
            for bar_idx, (_, fut_row) in enumerate(future.iterrows(), 1):
                # Stop loss
                if fut_row['low'] <= entry_price - risk:
                    exit_r = -1.0 - 0.038
                    last_exit = pos + bar_idx
                    break
                # Target
                if fut_row['vwap_zscore'] >= z_tgt:
                    exit_r = ((fut_row['close'] - entry_price) / risk) - 0.038
                    last_exit = pos + bar_idx
                    break
            else:
                # Time horizon exit
                final_close = future.iloc[-1]['close']
                exit_r = ((final_close - entry_price) / risk) - 0.038
                last_exit = pos + max_b

            trades.append(exit_r)

    return trades

def optimize():
    cfg_file = Path('results/fleet_config.json')
    cfg = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
    profiles = cfg.get('sector_governor_profiles', {})
    
    supervisor = ECSTradingSupervisor()
    optimal_fleet = {}

    print("=" * 75)
    print("STARTING AUTONOMOUS CONVERGENCE LOOP (4 SECTORS)")
    print("=" * 75)

    for sector_id, sec_data in profiles.items():
        symbols = sec_data.get('symbols', [])
        print(f"\n[AUTO-TUNE] Converging {sector_id} on symbols: {symbols}...")
        
        # Pre-cache sector data
        symbol_dfs = []
        for s in symbols:
            matched = list(DATA_DIR.glob(f'*_{s}_*.csv'))
            if not matched:
                continue
            raw = pd.read_csv(matched[0])
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(m) >= 300:
                symbol_dfs.append(compute_causal_features(m))

        if not symbol_dfs:
            print(f"  No data found for {sector_id}, retaining defaults.")
            optimal_fleet[sector_id] = sec_data
            continue

        best_score = -9999.0
        best_params = None
        best_stats = {}

        # Coordinate descent over parameter grid
        keys = list(PARAM_GRID.keys())
        combos = [dict(zip(keys, v)) for v in itertools.product(*PARAM_GRID.values())]

        for p in combos:
            all_r = []
            for df in symbol_dfs:
                all_r.extend(simulate_symbol(df, p, supervisor))
            
            n_trades = len(all_r)
            if n_trades < 15:  # Sample size filter
                continue
            
            total_r = sum(all_r)
            win_rate = np.mean([r > 0 for r in all_r])
            
            # Penalize low win rates or negative expectancy
            score = total_r - (15.0 * max(0.0, 0.40 - win_rate))

            if score > best_score:
                best_score = score
                best_params = p
                best_stats = {
                    'trades': n_trades,
                    'win_rate': f"{win_rate*100:.1f}%",
                    'total_r': round(total_r, 2),
                    'exp_r': round(total_r / n_trades, 3)
                }

        if best_params:
            print(f"  ✓ Converged {sector_id}:")
            print(f"    Parameters: {best_params}")
            print(f"    Performance: {best_stats}")
            sec_data.update(best_params)
            optimal_fleet[sector_id] = sec_data
        else:
            print(f"  ⚠️ No configuration met stability criteria for {sector_id}.")
            optimal_fleet[sector_id] = sec_data

    cfg['sector_governor_profiles'] = optimal_fleet
    cfg_file.write_text(json.dumps(cfg, indent=2))
    print("\n" + "=" * 75)
    print("CONVERGENCE COMPLETE: Updated results/fleet_config.json with optimal parameters.")
    print("=" * 75)

if __name__ == '__main__':
    optimize()
