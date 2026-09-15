"""
Automated Multi-Parameter Calibration Engine
---------------------------------------------
1. Loads & caches resampled 15m feature matrices in RAM once.
2. Splits dataset: In-Sample (July 2023 - Dec 2024) vs Out-of-Sample (Jan 2025 - Aug 2026).
3. Executes parameter grid search across In-Sample folds.
4. Validates top candidate sets against Out-of-Sample data.
"""

import sys
from pathlib import Path
import itertools
import pandas as pd
import numpy as np

from alpha_engine_core import (
    EngineConfig, ZerodhaFeeCalculator, TimeDecayGovernor,
    WelfordFeaturePipeline, Position, ArmedState
)

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

WHITELIST = [
    "AXISBANK", "ULTRACEMCO", "M&M", "JSWSTEEL", "BAJAJ-AUTO",
    "EICHERMOT", "HINDUNILVR", "BAJFINANCE", "ITC", "GRASIM",
    "MARUTI", "COALINDIA", "SBILIFE", "NTPC", "HDFCLIFE", "CIPLA", "ETERNAL"
]

SECTOR_MAP = {
    "AXISBANK": "BANK", "BAJFINANCE": "FIN", "SBILIFE": "FIN", "HDFCLIFE": "FIN",
    "M&M": "AUTO", "BAJAJ-AUTO": "AUTO", "EICHERMOT": "AUTO", "MARUTI": "AUTO",
    "ULTRACEMCO": "CEMENT", "GRASIM": "CEMENT", "JSWSTEEL": "METALS",
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "COALINDIA": "ENERGY", "NTPC": "ENERGY",
    "CIPLA": "PHARMA", "ETERNAL": "OTHER"
}

def preload_feature_cache():
    print("[1/3] Pre-loading and computing 15-minute streaming features...")
    cache = {}
    files = list(DATA_DIR.glob("NSE_*_minute_*.csv"))
    for f in files:
        sym = f.name.split('_')[1].upper()
        if sym not in WHITELIST:
            continue
        try:
            raw = pd.read_csv(f)
            t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
            df_15 = WelfordFeaturePipeline.resample_to_15m(raw)
            if len(df_15) > 100:
                features = WelfordFeaturePipeline.compute_features(df_15).set_index('dt')
                cache[sym] = features
        except Exception:
            continue
    print(f"[INFO] Cached feature matrices for {len(cache)} assets.")
    return cache

def precompute_gap_days(cache: dict, gap_threshold_pct: float) -> set:
    gapped_days = set()
    for sym, df in cache.items():
        daily = df.groupby('date_only').agg(
            first_open=('open', 'first'),
            last_close=('close', 'last')
        ).sort_index()
        daily['prev_close'] = daily['last_close'].shift(1)
        daily['gap_pct'] = (daily['first_open'] - daily['prev_close']) / daily['prev_close'] * 100.0
        bad_dates = daily[daily['gap_pct'] <= -abs(gap_threshold_pct)].index
        for d in bad_dates:
            gapped_days.add((sym, d))
    return gapped_days

def evaluate_parameters(cache, timestamps, gapped_days, breadth_thresh, z_arm, rsi_arm, config):
    governor = TimeDecayGovernor(config)
    session_cutoff = pd.to_datetime(config.session_close_time).time()
    entry_cutoff = pd.to_datetime(config.entry_cutoff_time).time()

    active_positions = {}
    armed_symbols = {}
    net_pnls = []
    gross_pnls = []
    frictions = []

    for t in timestamps:
        # Exit checks
        to_close = []
        for sym, pos in active_positions.items():
            if t not in cache[sym].index:
                continue
            bar = cache[sym].loc[t]
            bar_open, bar_high, bar_low, bar_close = bar['open'], bar['high'], bar['low'], bar['close']
            bar_time = bar['time_only']
            risk_ticks = pos.risk_ticks

            if bar_time >= session_cutoff:
                to_close.append((sym, bar_close))
                continue
            if bar_low <= pos.stop_price:
                to_close.append((sym, min(bar_open, pos.stop_price) - 0.05 * risk_ticks))
                continue

            curr_peak_r = (bar_high - pos.entry_price) / risk_ticks
            curr_r = (bar_close - pos.entry_price) / risk_ticks
            pos.peak_r = max(pos.peak_r, curr_peak_r)
            r_target, z_target = governor.compute_targets(bar['vol_ratio'], bar_time)

            if curr_peak_r >= r_target:
                to_close.append((sym, pos.entry_price + (r_target * risk_ticks)))
                continue
            if bar['vwap_zscore'] >= z_target and curr_r >= config.min_harvest_r:
                to_close.append((sym, bar_close))
                continue
            if pos.peak_r >= config.trailing_profit_lock_r:
                pos.stop_price = max(pos.stop_price, pos.entry_price + (pos.peak_r * config.trailing_profit_lock_pct * risk_ticks))

        for sym, fill_p in to_close:
            p = active_positions.pop(sym)
            gross = (fill_p - p.entry_price) * p.shares
            fees = ZerodhaFeeCalculator.calculate_round_trip(p.entry_price * p.shares, fill_p * p.shares)
            gross_pnls.append(gross)
            frictions.append(fees)
            net_pnls.append(gross - fees)

        # Breadth Interlock
        if config.max_concurrent_positions - len(active_positions) <= 0:
            continue

        z_vals = [cache[s].loc[t, 'vwap_zscore'] for s in cache if t in cache[s].index]
        if z_vals and np.mean(z_vals) < breadth_thresh:
            armed_symbols.clear()
            continue

        active_sectors = [SECTOR_MAP.get(s, "OTHER") for s in active_positions]

        for sym, df in cache.items():
            if sym in active_positions or t not in df.index:
                continue
            if (sym, df.loc[t, 'date_only']) in gapped_days:
                continue
            sym_sector = SECTOR_MAP.get(sym, "OTHER")
            if active_sectors.count(sym_sector) >= 1:
                continue

            loc_idx = df.index.get_loc(t)
            if loc_idx < 2 or loc_idx + 1 >= len(df):
                continue
            curr_bar = df.iloc[loc_idx]
            prev_bar = df.iloc[loc_idx - 1]

            if curr_bar['time_only'] >= entry_cutoff:
                armed_symbols.pop(sym, None)
                continue

            if curr_bar['vwap_zscore'] < z_arm and curr_bar['rsi_14'] < rsi_arm:
                armed_symbols[sym] = ArmedState(armed_time=t, swing_low=min(curr_bar['low'], prev_bar['low']))

            if sym in armed_symbols:
                armed_symbols[sym].swing_low = min(armed_symbols[sym].swing_low, curr_bar['low'])
                if curr_bar['close'] > prev_bar['high'] and curr_bar['close'] > curr_bar['open']:
                    next_bar = df.iloc[loc_idx + 1]
                    entry_p = next_bar['open']
                    risk_t = max(entry_p - armed_symbols[sym].swing_low, config.stop_atr_multiplier * curr_bar['atr'])
                    shares = int(config.slot_capital / entry_p)
                    if shares >= 1:
                        active_positions[sym] = Position(
                            symbol=sym, entry_time=next_bar.name, entry_price=entry_p,
                            stop_price=entry_p - risk_t, risk_ticks=risk_t, shares=shares, peak_r=0.0
                        )
                        armed_symbols.pop(sym, None)
                        active_sectors.append(sym_sector)
                        if config.max_concurrent_positions - len(active_positions) <= 0:
                            break

    total_trades = len(net_pnls)
    if total_trades < 15:
        return {'trades': total_trades, 'win_rate': 0.0, 'net_pnl': -999999, 'score': -999999}

    net_arr = np.array(net_pnls)
    cum = np.cumsum(net_arr)
    peak = np.maximum.accumulate(cum)
    dd = np.max(peak - cum) if len(cum) > 0 else 1.0
    dd = max(dd, 1000.0)

    net_sum = np.sum(net_arr)
    fee_sum = np.sum(frictions)
    wr = np.mean(net_arr > 0) * 100.0

    # Objective: Calmar-like metric penalizing heavy fee drag
    score = (net_sum / dd) * (net_sum / max(1.0, fee_sum))

    return {
        'trades': total_trades,
        'win_rate': wr,
        'gross_pnl': np.sum(gross_pnls),
        'fees': fee_sum,
        'net_pnl': net_sum,
        'max_dd': dd,
        'score': score
    }

def main():
    cache = preload_feature_cache()
    all_ts = sorted(list(set.union(*[set(df.index) for df in cache.values()])))

    # In-Sample Training: 2023-07 to 2024-12
    # Out-of-Sample Testing: 2025-01 to 2026-08
    is_ts = [t for t in all_ts if t <= pd.Timestamp('2024-12-31 23:59:59')]
    oos_ts = [t for t in all_ts if t >= pd.Timestamp('2025-01-01 00:00:00')]

    config = EngineConfig()

    breadth_grid = [-0.60, -0.45, -0.30]
    z_arm_grid = [-2.8, -2.5, -2.2]
    rsi_arm_grid = [24.0, 28.0, 32.0]
    gap_grid = [1.25, 1.75]

    total_combinations = len(breadth_grid) * len(z_arm_grid) * len(rsi_arm_grid) * len(gap_grid)
    print(f"\n[2/3] Running In-Sample Grid Search ({total_combinations} candidate models)...")

    results = []
    combo_idx = 0

    for gap in gap_grid:
        gapped_days = precompute_gap_days(cache, gap)
        for b_th, z_th, rsi_th in itertools.product(breadth_grid, z_arm_grid, rsi_arm_grid):
            combo_idx += 1
            metrics = evaluate_parameters(cache, is_ts, gapped_days, b_th, z_th, rsi_th, config)
            results.append({
                'breadth': b_th, 'z_arm': z_th, 'rsi_arm': rsi_th, 'gap': gap,
                **metrics
            })
            sys.stdout.write(f"\rEvaluating: {combo_idx}/{total_combinations} | Current Best Score: {max([r['score'] for r in results]):.2f}")
            sys.stdout.flush()

    res_df = pd.DataFrame(results).sort_values('score', ascending=False)
    print("\n\n" + "=" * 80)
    print("TOP 5 IN-SAMPLE CALIBRATED PARAMETER SETS (2023-07 to 2024-12)")
    print("=" * 80)
    cols = ['breadth', 'z_arm', 'rsi_arm', 'gap', 'trades', 'win_rate', 'net_pnl', 'max_dd', 'score']
    print(res_df[cols].head(5).to_string(index=False))

    # [3/3] Out-of-Sample Forward Validation
    best_candidate = res_df.iloc[0]
    print("\n" + "=" * 80)
    print("VALIDATING TOP CANDIDATE ON OUT-OF-SAMPLE DATA (2025-01 to 2026-08)")
    print("=" * 80)
    oos_gapped = precompute_gap_days(cache, best_candidate['gap'])
    oos_metrics = evaluate_parameters(
        cache, oos_ts, oos_gapped,
        best_candidate['breadth'], best_candidate['z_arm'], best_candidate['rsi_arm'], config
    )
    print(f"Optimal Parameters : Breadth < {best_candidate['breadth']} | Z < {best_candidate['z_arm']} | RSI < {best_candidate['rsi_arm']} | Gap >= {best_candidate['gap']}%")
    print(f"OOS Trades         : {oos_metrics['trades']}")
    print(f"OOS Win Rate       : {oos_metrics['win_rate']:.1f}%")
    print(f"OOS Gross Alpha    : ₹{oos_metrics['gross_pnl']:+10,.2f}")
    print(f"OOS Statutory Fees : ₹{oos_metrics['fees']:10,.2f}")
    print(f"OOS Net Portfolio  : ₹{oos_metrics['net_pnl']:+10,.2f}")
    print(f"OOS Max Drawdown   : ₹{oos_metrics['max_dd']:10,.2f}")
    print("=" * 80)

if __name__ == '__main__':
    main()
