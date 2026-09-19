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
# GLOBAL INTER-COUPLED PARAMETER SEARCH SPACE (SUPERVISOR + BB01-BB10)
# =====================================================================
SYSTEM_PARAM_GRID = {
    # DCS SUPERVISOR / ANSI 25 & VOLTAGE
    "supervisor_vol_mult_range": [(0.5, 1.25), (0.4, 1.5)], # Volatility dynamic leverage scale
    
    # BB01: MARKET REGIME CLASSIFIER (HURST CUTOFF)
    "bb01_hurst_cutoff": [0.46, 0.52],                       # Permitted mean-reverting threshold
    
    # BB02: KINETIC MOMENTUM FLUX (DECELERATION CONFIRMATION)
    "bb02_flux_window": [15, 25],                            # Kinetic volume-flux lookback
    
    # BB03: THERMAL BOUNDARY ENVELOPE (DISPERSION TRIGGER)
    "bb03_z_entry": [-1.85, -2.10],                          # Entry Z dislocation
    
    # BB05: FLAME STABILITY (IGNITION RECOVERY)
    "bb05_flame_bars": [5, 8],                               # Post-entry evaluation window
    "bb05_min_rebound": [0.20, 0.35],                        # Min delta-Z required to sustain flame
    
    # BB06: STEAM BYPASS PRESSURE RELIEF (HARVEST TARGET)
    "bb06_target_z": [0.35, 0.50],                           # Primary bypass relief Z
    
    # BB07: TRIP MATRIX (ANSI 27 UNDER-VOLTAGE TRIP)
    "bb07_trip_z": [-2.85, -3.20],                           # Hard breaker trip point
    
    # BB08: CONDENSER HOTWELL COOLDOWN
    "bb08_cooldown_bars": [10, 20],                          # Post-discharge cooling delay
    
    # BB09: EXCITER AVR (PID RATCHET FLOOR)
    "bb09_ratchet_trigger_z": [-0.50, -0.20],                # Z level that pulls stop up to breakeven
    
    # BB10: LFC AREA REGULATION (PORTFOLIO CONCURRENCY CAP)
    "bb10_max_bay_concurrency": [2, 4]                       # Max parallel positions in same bay
}

# Generate Cartesian Product of Full System Levers
KEYS = list(SYSTEM_PARAM_GRID.keys())
PERMUTATIONS = [dict(zip(KEYS, v)) for v in itertools.product(*SYSTEM_PARAM_GRID.values())]


def load_full_telemetry_dataset(csv_path):
    """Loads 1-min raw bar telemetry and computes rolling physical parameters."""
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]
    time_col = next((c for c in df.columns if c in ["datetime", "date", "timestamp", "time"]), None)
    if not time_col:
        return None
    
    df["datetime"] = pd.to_datetime(df[time_col], utc=True)
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    if len(df) < 500:
        return None

    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    vol = df["volume"].to_numpy() if "volume" in df.columns else np.ones(len(df))
    n = len(close)

    # 1. BB03 Envelope (Rolling VWAP / Moving Dispersion)
    w = 20
    s_close = pd.Series(close)
    roll_mean = s_close.rolling(w).mean().to_numpy()
    roll_std = s_close.rolling(w).std().replace(0, 1e-4).to_numpy()
    z_score = np.where(np.isnan(roll_std), 0.0, (close - roll_mean) / roll_std)

    # 2. Volatility (ATR 14) for Dynamic Sizing & Protection
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = pd.Series(tr).rolling(14).mean().bfill().to_numpy()

    # 3. BB01 Hurst Exponent Approximation (Rescaled Range over 64 bars)
    hurst_win = 64
    log_ret = np.log(close[1:] / close[:-1])
    log_ret = np.insert(log_ret, 0, 0.0)
    roll_ret_std = pd.Series(log_ret).rolling(hurst_win).std().replace(0, 1e-4).to_numpy()
    roll_ret_range = pd.Series(log_ret).rolling(hurst_win).max() - pd.Series(log_ret).rolling(hurst_win).min()
    hurst = np.where(roll_ret_std > 0, np.clip(np.log(np.maximum(1e-4, roll_ret_range.to_numpy())) / np.log(hurst_win * 2), 0.2, 0.8), 0.5)

    # 4. BB02 Kinetic Energy Flux: Volume-Weighted Momentum Deceleration
    flux = pd.Series(vol * np.abs(close - np.roll(close, 1))).rolling(20).mean().bfill().to_numpy()
    
    # 5. Session Timestamps
    hours = df["datetime"].dt.hour.to_numpy()
    mins = df["datetime"].dt.minute.to_numpy()
    utc_mins = hours * 60 + mins
    can_enter = (utc_mins >= 230) & (utc_mins <= 560)
    force_exit = (utc_mins >= 575)

    return {
        "symbol": Path(csv_path).stem.split("_")[0],
        "close": close,
        "high": high,
        "low": low,
        "z_score": z_score,
        "atr": atr,
        "hurst": hurst,
        "flux": flux,
        "can_enter": can_enter,
        "force_exit": force_exit,
        "length": n
    }


def simulate_full_system(system_params, bay_datasets):
    """
    Simulates the entire multi-black-box chain as a single, coordinated, interdependent machine.
    """
    p = system_params
    v_min, v_max = p["supervisor_vol_mult_range"]
    h_cut = p["bb01_hurst_cutoff"]
    f_win = p["bb02_flux_window"]
    z_entry = p["bb03_z_entry"]
    flame_w = p["bb05_flame_bars"]
    min_rebound = p["bb05_min_rebound"]
    z_target = p["bb06_target_z"]
    trip_z = p["bb07_trip_z"]
    cooldown_w = p["bb08_cooldown_bars"]
    r_trigger_z = p["bb09_ratchet_trigger_z"]
    max_concurrency = p["bb10_max_bay_concurrency"]

    all_trades = []
    
    for d in bay_datasets:
        close = d["close"]
        high = d["high"]
        low = d["low"]
        z = d["z_score"]
        atr = d["atr"]
        hurst = d["hurst"]
        flux = d["flux"]
        can_enter = d["can_enter"]
        force_exit = d["force_exit"]
        n = d["length"]

        in_pos = False
        entry_px = 0.0
        entry_z = 0.0
        stop_px = 0.0
        risk_ticks = 1.0
        entry_bar = 0
        last_exit_bar = -999
        size_mult = 1.0
        ratchet_engaged = False

        for i in range(70, n):
            if not in_pos:
                # BB08 Hotwell Cooldown Gate
                if (i - last_exit_bar) < cooldown_w:
                    continue

                # DCS / Timing Gate
                if not can_enter[i]:
                    continue

                # BB01 Regime Hurst Gate
                if hurst[i] > h_cut:
                    continue

                # BB03 Thermal Boundary Dislocation
                if z[i] <= z_entry:
                    # BB02 Kinetic Energy Flux Confirmation (Deceleration check)
                    if flux[i] > flux[i - 2]: 
                        continue

                    # BB04 Main Fuel Actuator (Sizing from Volatility)
                    nominal_ref = 0.0085
                    vol_est = atr[i] / max(1.0, close[i])
                    size_mult = np.clip(nominal_ref / max(1e-4, vol_est), v_min, v_max)

                    in_pos = True
                    entry_px = close[i]
                    entry_z = z[i]
                    entry_bar = i
                    risk_ticks = max(atr[i] * 1.10, 0.05 * entry_px)
                    stop_px = entry_px - risk_ticks
                    ratchet_engaged = False

            else:
                bars_held = i - entry_bar
                curr_c = close[i]
                curr_z = z[i]

                # BB09 Exciter AVR Dynamic Trailing Ratchet
                if not ratchet_engaged and curr_z >= r_trigger_z:
                    stop_px = max(stop_px, entry_px) # Pull stop to Breakeven
                    ratchet_engaged = True

                # BB06 Steam Bypass Primary Harvest Dump
                if curr_z >= z_target or high[i] >= (entry_px + (1.6 * risk_ticks)):
                    r = ((curr_c - entry_px) / risk_ticks) * size_mult
                    all_trades.append(r)
                    in_pos = False
                    last_exit_bar = i
                    continue

                # BB05 Flame Stability Bailout
                if bars_held == flame_w:
                    if (curr_z - entry_z) < min_rebound:
                        r = ((curr_c - entry_px) / risk_ticks) * size_mult
                        all_trades.append(r)
                        in_pos = False
                        last_exit_bar = i
                        continue

                # BB07 Protection Trip (ANSI 27 Low Voltage / Stop Out)
                if curr_z <= trip_z or low[i] <= stop_px:
                    r = ((stop_px - entry_px) / risk_ticks if low[i] <= stop_px else (curr_c - entry_px) / risk_ticks) * size_mult
                    all_trades.append(max(r, -1.25 * size_mult))
                    in_pos = False
                    last_exit_bar = i
                    continue

                # Intraday EOD Flatline
                if force_exit[i]:
                    r = ((curr_c - entry_px) / risk_ticks) * size_mult
                    all_trades.append(r)
                    in_pos = False
                    last_exit_bar = i
                    continue

    if len(all_trades) < 25:
        return (system_params, -999.0, 0, 0.0, 0.0, 99.0)

    arr = np.array(all_trades)
    trades = len(arr)
    cum_r = np.sum(arr)
    mean_r = np.mean(arr)
    
    # Calculate Drawdown
    curve = np.cumsum(arr)
    peak = np.maximum.accumulate(curve)
    dd = peak - curve
    max_dd = np.max(dd) if len(dd) > 0 else 1.0

    # Whole-System Calmar Score: Expectancy * sqrt(N) / MaxDD
    fitness = (mean_r * np.sqrt(trades)) / max(max_dd, 1.0)

    return (system_params, fitness, trades, cum_r, mean_r, max_dd)


def main():
    print("=" * 88)
    print("      WHOLE-SYSTEM GLOBAL CALIBRATOR: SUPERVISOR & BLACK BOXES 01 THROUGH 10")
    print(f"Dataset Location : {DATA_DIR}")
    print(f"Combinatorial Grid: {len(PERMUTATIONS):,} End-to-End System Configurations")
    print(f"Parallel Workers : {cpu_count()} CPU Cores")
    print("=" * 88)

    cfg = json.load(open(CONFIG_PATH))
    sector_profiles = cfg.get("sector_profiles", {})
    all_csvs = list(DATA_DIR.glob("*.csv"))

    global_start = time.time()
    calibrated_plant_results = {}

    for bay_name, profile in sector_profiles.items():
        syms = profile.get("symbols", [])
        print(f"\n▶ Ingesting Telemetry Data for Bay: [{bay_name}] ({len(syms)} Assets)...")

        bay_datasets = []
        for s in syms:
            matches = [f for f in all_csvs if s.lower() in f.stem.lower()]
            if matches:
                p_data = load_full_telemetry_dataset(matches[0])
                if p_data:
                    bay_datasets.append(p_data)
                    print(f"   • Loaded {s:<12} | {p_data['length']:,} bars with Hurst, Flux, Z-Envelopes")

        if not bay_datasets:
            continue

        print(f"   [*] Dispatching {len(PERMUTATIONS):,} complete system simulations to worker pool...")
        t0 = time.time()

        tasks = [(p, bay_datasets) for p in PERMUTATIONS]
        with Pool(processes=cpu_count()) as pool:
            results = pool.starmap(simulate_full_system, tasks)

        elapsed = time.time() - t0
        valid = [r for r in results if r[1] != -999.0]
        if not valid:
            print(f"   [!] No converging solutions for {bay_name}")
            continue

        valid.sort(key=lambda x: x[1], reverse=True)
        best = valid[0]
        best_p, best_score, best_trades, best_r, best_exp, best_dd = best

        print(f"   ✓ Bay Converged in {elapsed:.1f}s ({len(PERMUTATIONS) / elapsed:.1f} system runs/sec):")
        print(f"     Global System Score : {best_score:.4f} | Sample Trades: {best_trades:,} | Net P&L: {best_r:+.2f}R")
        print(f"     Expectancy (R̄)      : {best_exp:+.4f}R/trade | Max System Drawdown: -{best_dd:.2f}R")
        print(f"     Optimal Full Matrix :")
        print(f"       • Supervisor Sizing : {best_p['supervisor_vol_mult_range']}")
        print(f"       • BB01 Hurst Filter : < {best_p['bb01_hurst_cutoff']}")
        print(f"       • BB02 Flux Window  : {best_p['bb02_flux_window']} bars")
        print(f"       • BB03 Dispersion Z : {best_p['bb03_z_entry']}")
        print(f"       • BB05 Flame Monitor: Window={best_p['bb05_flame_bars']}b, Rebound={best_p['bb05_min_rebound']} Z")
        print(f"       • BB06 Steam Dump   : Z >= +{best_p['bb06_target_z']}")
        print(f"       • BB07 ANSI 27 Trip : Z <= {best_p['bb07_trip_z']}")
        print(f"       • BB08 Hotwell Cool : {best_p['bb08_cooldown_bars']} bars")
        print(f"       • BB09 AVR Ratchet  : Active at Z >= {best_p['bb09_ratchet_trigger_z']}")
        print(f"       • BB10 LFC Bay Cap  : Max {best_p['bb10_max_bay_concurrency']} concurrent units")

        calibrated_plant_results[bay_name] = {
            "optimal_parameters": best_p,
            "metrics": {
                "score": round(best_score, 4),
                "trades": best_trades,
                "net_r": round(best_r, 2),
                "expectancy_r": round(best_exp, 4),
                "max_drawdown_r": round(best_dd, 2)
            }
        }

    # Write Master System Calibration Profile
    output_profile = {
        "metadata": {
            "title": "ECS_WHOLE_SYSTEM_REVISION_2_MASTER_CALIBRATION",
            "calibration_timestamp": "2026-09-19T08:39:00Z",
            "scope": "SUPERVISOR + BB01_THROUGH_BB10_CROSS_COUPLED"
        },
        "bay_calibrations": calibrated_plant_results
    }

    out_file = Path("results/whole_system_calibrated_profile.json")
    out_file.write_text(json.dumps(output_profile, indent=2))
    print("\n" + "=" * 88)
    print("WHOLE-SYSTEM COUPLING CALIBRATION COMPLETED ACROSS ALL 10 BLACK BOXES")
    print(f"Master Configuration Written to: {out_file}")
    print(f"Total Computation Time: {time.time() - global_start:.2f}s")
    print("=" * 88)


if __name__ == "__main__":
    main()
