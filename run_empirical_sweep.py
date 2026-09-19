import json
import os
import sys
import time
import itertools
from pathlib import Path
from multiprocessing import Pool, cpu_count

import numpy as np
import pandas as pd

DATA_DIR = Path("/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824")
CONFIG_PATH = Path("results/fleet_config.json")

# =====================================================================
# 1. PARAMETER SPACE DEFINITION (ALL PERMUTATIONS)
# =====================================================================
# BB03 / Entry Z Thresholds
Z_ENTRIES = [-1.75, -1.90, -2.05, -2.20]
# BB06 / Target Z Envelopes
Z_TARGETS = [0.35, 0.50, 0.65, 0.80]
# BB09 / Initial Stop Loss Multipliers (ATR Units)
STOP_ATRS = [1.10, 1.30, 1.50, 1.80]
# BB05 / Flame Stability Window (Bars to confirm positive Z drift)
FLAMEOUT_BARS = [4, 6, 8]
# BB07 / Emergency Under-Voltage Trip Z
TRIP_ZS = [-2.60, -2.85, -3.10]

PARAM_GRID = list(itertools.product(Z_ENTRIES, Z_TARGETS, STOP_ATRS, FLAMEOUT_BARS, TRIP_ZS))
TOTAL_PERMS = len(PARAM_GRID)


def load_and_preprocess_symbol(csv_path):
    """Loads 1-min raw CSV, standardizes columns, and pre-computes Rolling Indicators."""
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]
    
    time_col = next((c for c in df.columns if c in ["datetime", "date", "timestamp", "time"]), None)
    if not time_col:
        return None
        
    df["datetime"] = pd.to_datetime(df[time_col], utc=True)
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    
    if len(df) < 500:
        return None

    # Pre-calculate 15-period rolling mean, std (VWAP/Bollinger surrogate), and ATR
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(close)
    
    # Fast rolling mean & std (window=20)
    w = 20
    roll_mean = np.full(n, np.nan)
    roll_std = np.full(n, np.nan)
    
    # Vectorized moving window
    c_series = pd.Series(close)
    roll_mean = c_series.rolling(w).mean().to_numpy()
    roll_std = c_series.rolling(w).std().replace(0, 1e-4).to_numpy()
    
    # ATR(14)
    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]
    atr = pd.Series(tr).rolling(14).mean().bfill().to_numpy()
    
    z_score = np.where(np.isnan(roll_std), 0.0, (close - roll_mean) / roll_std)
    
    # Time mask: Strict Intraday only (09:15 to 15:15 IST -> 03:45 to 09:45 UTC)
    hours = df["datetime"].dt.hour.values
    mins = df["datetime"].dt.minute.values
    utc_mins = hours * 60 + mins
    # Trading window: 03:45 UTC (225 mins) to 09:30 UTC (570 mins)
    can_enter = (utc_mins >= 230) & (utc_mins <= 560)
    force_exit = (utc_mins >= 575)

    return {
        "symbol": Path(csv_path).stem.split("_")[0],
        "close": close,
        "high": high,
        "low": low,
        "z_score": z_score,
        "atr": atr,
        "can_enter": can_enter,
        "force_exit": force_exit,
        "length": n
    }


def simulate_single_permutation(params, precomputed_data_list):
    """Vectorized trade execution simulator across all precomputed assets for a single parameter set."""
    z_entry, z_target, stop_atr, flame_w, trip_z = params
    
    all_trade_r = []
    
    for d in precomputed_data_list:
        close = d["close"]
        high = d["high"]
        low = d["low"]
        z = d["z_score"]
        atr = d["atr"]
        can_enter = d["can_enter"]
        force_exit = d["force_exit"]
        n = d["length"]

        in_pos = False
        entry_px = 0.0
        stop_px = 0.0
        risk_ticks = 1.0
        entry_bar = 0
        entry_z = 0.0
        
        for i in range(25, n):
            if not in_pos:
                if can_enter[i] and z[i] <= z_entry:
                    in_pos = True
                    entry_px = close[i]
                    entry_z = z[i]
                    entry_bar = i
                    risk_ticks = max(atr[i] * stop_atr, 0.05 * entry_px)
                    stop_px = entry_px - risk_ticks
            else:
                bars_held = i - entry_bar
                curr_c = close[i]
                curr_z = z[i]

                # 1. Target Hit (BB06 Steam Bypass)
                if curr_z >= z_target or high[i] >= (entry_px + (1.5 * risk_ticks)):
                    r = (curr_c - entry_px) / risk_ticks
                    all_trade_r.append(r)
                    in_pos = False
                    continue

                # 2. Flameout Check (BB05 Flame Stability)
                if bars_held == flame_w:
                    if (curr_z - entry_z) < 0.20:
                        r = (curr_c - entry_px) / risk_ticks
                        all_trade_r.append(r)
                        in_pos = False
                        continue

                # 3. Emergency Trip (BB07 Protection Trip)
                if curr_z <= trip_z or low[i] <= stop_px:
                    r = (stop_px - entry_px) / risk_ticks if low[i] <= stop_px else (curr_c - entry_px) / risk_ticks
                    all_trade_r.append(max(r, -1.25))
                    in_pos = False
                    continue

                # 4. Intraday Expiry (EOD Flush)
                if force_exit[i]:
                    r = (curr_c - entry_px) / risk_ticks
                    all_trade_r.append(r)
                    in_pos = False
                    continue

    if len(all_trade_r) < 30:
        return (params, -999.0, 0, 0.0, 0.0, 99.0)

    arr = np.array(all_trade_r)
    trades = len(arr)
    cum_pnl = np.sum(arr)
    mean_r = np.mean(arr)
    wr = np.mean(arr > 0) * 100.0
    
    # Compute Drawdown
    equity_curve = np.cumsum(arr)
    highwater = np.maximum.accumulate(equity_curve)
    dd = highwater - equity_curve
    max_dd = np.max(dd) if len(dd) > 0 else 1.0

    # Calmar-like Institutional Objective Score: Expectancy * sqrt(trades) / MaxDD
    score = (mean_r * np.sqrt(trades)) / max(max_dd, 1.0)
    
    return (params, score, trades, cum_pnl, mean_r, max_dd)


def main():
    if not CONFIG_PATH.exists():
        print(f"[-] Config {CONFIG_PATH} not found.")
        sys.exit(1)
        
    cfg = json.load(open(CONFIG_PATH))
    sector_profiles = cfg.get("sector_profiles", {})
    all_symbols = list(cfg.get("symbol_to_sector_map", {}).keys())

    print("=" * 85)
    print("      MASTER EMPIRICAL CALIBRATION ENGINE (2023 - 2026 COMPLETE)")
    print(f"Dataset Location : {DATA_DIR}")
    print(f"Parameter Space  : {TOTAL_PERMS:,} Permutations per Sector Bay")
    print(f"Target Fleet     : {len(all_symbols)} Assets across {len(sector_profiles)} Generator Bays")
    print(f"Compute Cores    : {cpu_count()} Workers Available")
    print("=" * 85)

    all_csvs = list(DATA_DIR.glob("*.csv"))
    master_calibrated_cfg = cfg.copy()

    global_start = time.time()
    grand_total_evaluations = 0

    for bay_name, profile in sector_profiles.items():
        bay_syms = profile.get("symbols", [])
        print(f"\n▶ Ingesting Market Data for Bay: [{bay_name}] ({len(bay_syms)} Assets)...")
        
        precomputed_bay_data = []
        for s in bay_syms:
            matches = [f for f in all_csvs if s.lower() in f.stem.lower()]
            if matches:
                p_data = load_and_preprocess_symbol(matches[0])
                if p_data:
                    precomputed_bay_data.append(p_data)
                    print(f"   • Loaded {s:<12} | {p_data['length']:,} 1-min bars")

        if not precomputed_bay_data:
            print(f"   [!] Warning: No data loaded for bay {bay_name}, skipping.")
            continue

        print(f"   [*] Dispatching {TOTAL_PERMS:,} permutations to process pool...")
        t0 = time.time()

        # Parallel evaluation across permutations
        tasks = [(p, precomputed_bay_data) for p in PARAM_GRID]
        with Pool(processes=cpu_count()) as pool:
            results = pool.starmap(simulate_single_permutation, tasks)

        bay_elapsed = time.time() - t0
        grand_total_evaluations += TOTAL_PERMS

        # Sort by institutional fitness score
        valid_results = [r for r in results if r[1] != -999.0]
        if not valid_results:
            print(f"   [!] No valid results converged for {bay_name}")
            continue

        valid_results.sort(key=lambda x: x[1], reverse=True)
        best = valid_results[0]
        best_params, best_score, best_trades, best_cum_pnl, best_mean_r, best_dd = best
        
        opt_entry_z, opt_target_z, opt_stop_atr, opt_flame, opt_trip_z = best_params

        print(f"   ✓ Bay Calibration Converged in {bay_elapsed:.2f}s ({TOTAL_PERMS / bay_elapsed:.1f} perms/sec):")
        print(f"     Optimal Objective Score : {best_score:.4f}")
        print(f"     Fitted Sample Trades    : {best_trades:,}")
        print(f"     Cumulative Net R        : {best_cum_pnl:+.2f}R")
        print(f"     Trade Expectancy (R̄)    : {best_mean_r:+.3f}R per trade")
        print(f"     Maximum Drawdown        : -{best_dd:.2f}R")
        print(f"     CALIBRATED PARAMETERS   : Entry Z={opt_entry_z} | Target Z=+{opt_target_z} | Stop={opt_stop_atr} ATR | Flame={opt_flame}b | Trip Z={opt_trip_z}")

        # Update configuration profile with empirically derived parameters
        profile["z_entry_threshold"] = float(opt_entry_z)
        profile["target_zscore"] = float(opt_target_z)
        profile["stop_atr_mult"] = float(opt_stop_atr)
        profile["flameout_window_bars"] = int(opt_flame)
        profile["emergency_trip_z"] = float(opt_trip_z)
        profile["empirical_expectancy_r"] = float(round(best_mean_r, 3))
        profile["empirical_max_dd_r"] = float(round(best_dd, 2))

    total_time = time.time() - global_start
    
    # Save back to fleet_config.json
    with open("results/fleet_config.json", "w") as f:
        json.dump(master_calibrated_cfg, f, indent=2)

    print("\n" + "=" * 85)
    print("EMPIRICAL RE-CALIBRATION RUN COMPLETED ACROSS ALL 2023-2026 DATA")
    print(f"Total Parameter Permutations Evaluated : {grand_total_evaluations:,}")
    print(f"Total Computation Elapsed              : {total_time:.2f}s")
    print(f"Master Configuration Written           : results/fleet_config.json")
    print("=" * 85)


if __name__ == "__main__":
    main()
