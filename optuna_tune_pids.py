import os
import json
from pathlib import Path
import numpy as np
import pandas as pd
import optuna
from joblib import Parallel, delayed
from run_stage1_full_ecs_dcs import compute_causal_features, ECSTradingSupervisor, SectorTurbinePID

optuna.logging.set_verbosity(optuna.logging.WARNING)

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
GRID_DF = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')

def simulate_symbol_pid(df: pd.DataFrame, sec_prof: dict, pid_params: dict, supervisor: ECSTradingSupervisor) -> list:
    trades = []
    z_in = sec_prof['z_entry_threshold']
    sl_m = sec_prof['sl_atr_mult']
    z_tgt = sec_prof['target_zscore']
    max_b = sec_prof['max_bars_held']

    last_exit = -999
    n_rows = len(df)

    for pos in range(0, n_rows - max_b - 5):
        if pos < last_exit + 15:
            continue
        row = df.iloc[pos]
        t = row['dt']
        if t not in GRID_DF.index:
            continue

        grid_row = GRID_DF.loc[t]

        # Supervisory Permissive Gate
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

            controller = SectorTurbinePID(
                target_z=z_tgt,
                kp=pid_params['kp'],
                ki=pid_params['ki'],
                kd=pid_params['kd'],
                droop=sec_prof.get('pid', {}).get('droop', 0.05),
                integral_clamp=pid_params['integral_clamp'],
                trail_activation_u=pid_params['trail_activation_u']
            )

            current_stop = entry_price - risk
            future = df.iloc[pos + 1 : pos + 1 + max_b]

            for bar_idx, (_, fut_row) in enumerate(future.iterrows(), 1):
                fut_t = fut_row['dt']
                fut_grid_vel = GRID_DF.loc[fut_t]['f_grid'] if fut_t in GRID_DF.index else 0.0

                u_control = controller.update(fut_row['vwap_zscore'], grid_velocity=fut_grid_vel)

                if u_control > -0.30:
                    ratchet_pct = min(1.0, max(0.0, (u_control + 0.30) / 0.60))
                    dynamic_stop = entry_price - (risk * (1.0 - (0.80 * ratchet_pct)))
                    current_stop = max(current_stop, dynamic_stop)

                if fut_row['low'] <= current_stop:
                    trades.append(((current_stop - entry_price) / risk) - 0.038)
                    last_exit = pos + bar_idx
                    break

                if fut_row['vwap_zscore'] >= z_tgt:
                    trades.append(((fut_row['close'] - entry_price) / risk) - 0.038)
                    last_exit = pos + bar_idx
                    break
            else:
                final_close = future.iloc[-1]['close']
                trades.append(((final_close - entry_price) / risk) - 0.038)
                last_exit = pos + max_b

    return trades

def create_pid_objective(dfs: list, sec_prof: dict, supervisor: ECSTradingSupervisor):
    def objective(trial):
        pid_params = {
            'kp': trial.suggest_float('kp', 0.15, 0.60, step=0.05),
            'ki': trial.suggest_float('ki', 0.005, 0.05, step=0.005),
            'kd': trial.suggest_float('kd', 0.05, 0.30, step=0.05),
            'integral_clamp': trial.suggest_float('integral_clamp', 0.8, 2.5, step=0.1),
            'trail_activation_u': trial.suggest_float('trail_activation_u', 0.05, 0.35, step=0.05)
        }

        results = Parallel(n_jobs=-1, prefer="threads")(
            delayed(simulate_symbol_pid)(df, sec_prof, pid_params, supervisor) for df in dfs
        )
        trades = [t for sub in results for t in sub]
        n_trades = len(trades)

        if n_trades < 15:
            return -100.0 + n_trades

        total_r = float(np.sum(trades))
        win_rate = float(np.mean([r > 0 for r in trades]))

        penalty = 25.0 * max(0.0, 0.42 - win_rate)
        return total_r - penalty

    return objective

def tune_all_pids(n_trials: int = 50):
    cfg_file = Path('results/fleet_config.json')
    cfg = json.loads(cfg_file.read_text())
    profiles = cfg.get('sector_governor_profiles', {})
    supervisor = ECSTradingSupervisor()

    print("=" * 80)
    print(f"STARTING BAYESIAN PID TUNER ({n_trials} TRIALS / TURBINE BAY)")
    print("=" * 80)

    for bay, sec_data in profiles.items():
        symbols = sec_data.get('symbols', [])
        print(f"\n[TUNING PID] {bay} | {sec_data.get('engine')} | Symbols: {symbols}")

        dfs = []
        for s in symbols:
            matched = list(DATA_DIR.glob(f'*_{s}_*.csv'))
            if not matched:
                continue
            raw = pd.read_csv(matched[0])
            tcol = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[tcol]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(m) >= 300:
                dfs.append(compute_causal_features(m))

        if not dfs:
            print(f"  [-] No data found for {bay}, skipping.")
            continue

        sampler = optuna.samplers.TPESampler(seed=42)
        study = optuna.create_study(direction='maximize', sampler=sampler)
        study.optimize(create_pid_objective(dfs, sec_data, supervisor), n_trials=n_trials, show_progress_bar=True)

        best_pids = study.best_params
        best_pids['droop'] = sec_data.get('pid', {}).get('droop', 0.05)

        results = Parallel(n_jobs=-1, prefer="threads")(
            delayed(simulate_symbol_pid)(df, sec_data, best_pids, supervisor) for df in dfs
        )
        final_trades = [t for sub in results for t in sub]
        win_rate = np.mean([r > 0 for r in final_trades]) if final_trades else 0.0

        print(f"  ✓ {bay} PID Optimal Gains:")
        print(f"    Gains   : Kp={best_pids['kp']:.2f}, Ki={best_pids['ki']:.3f}, Kd={best_pids['kd']:.2f}, Clamp={best_pids['integral_clamp']:.1f}")
        print(f"    Results : {len(final_trades)} trades | Win Rate: {win_rate*100:.1f}% | Net R: {sum(final_trades):+.2f}R")

        sec_data['pid'] = best_pids

    cfg['sector_governor_profiles'] = profiles
    cfg_file.write_text(json.dumps(cfg, indent=2))
    print("\n" + "=" * 80)
    print("ALL 5 TURBINE PID CONTROLLERS OPTIMIZED & SAVED TO results/fleet_config.json")
    print("=" * 80)

if __name__ == '__main__':
    tune_all_pids(n_trials=50)
