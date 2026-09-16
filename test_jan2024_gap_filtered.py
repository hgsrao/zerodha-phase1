"""
January 2024 Replay with Gap-Down Repricing Filter
--------------------------------------------------
Inhibits symbols opening with >= 1.75% gap-down from prior day close.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

from alpha_engine_core import (
    EngineConfig, ZerodhaFeeCalculator, TimeDecayGovernor,
    WelfordFeaturePipeline, Position, ArmedState
)

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

def compute_daily_gaps(symbol_frames: dict) -> dict:
    """Returns a set of (symbol, date) tuples that breached the gap-down limit."""
    gapped_days = set()
    for sym, df in symbol_frames.items():
        daily_bars = df.groupby('date_only').agg(
            first_open=('open', 'first'),
            last_close=('close', 'last')
        ).sort_index()
        daily_bars['prev_close'] = daily_bars['last_close'].shift(1)
        daily_bars['gap_pct'] = (daily_bars['first_open'] - daily_bars['prev_close']) / daily_bars['prev_close'] * 100.0
        
        # Identify sessions gapping down >= 1.75%
        bad_dates = daily_bars[daily_bars['gap_pct'] <= -1.75].index
        for d in bad_dates:
            gapped_days.add((sym, d))
    return gapped_days

def run_january_test():
    config = EngineConfig()
    governor = TimeDecayGovernor(config)
    
    # 1. Load Data
    symbol_frames = {}
    for sym in config.symbols:
        matches = list(DATA_DIR.glob(f"*_{sym}_*.csv"))
        if not matches:
            continue
        fpath = matches[0]
        try:
            raw = pd.read_csv(fpath)
            t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= '2024-01-01') & (raw['dt'] <= '2024-01-31')].copy()
            if len(m) > 200:
                df_15 = WelfordFeaturePipeline.resample_to_15m(m)
                if len(df_15) > 30:
                    symbol_frames[sym] = WelfordFeaturePipeline.compute_features(df_15).set_index('dt')
        except Exception:
            continue

    gapped_symbols_per_day = compute_daily_gaps(symbol_frames)
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in symbol_frames.values()])))
    
    session_cutoff = pd.to_datetime(config.session_close_time).time()
    entry_cutoff = pd.to_datetime(config.entry_cutoff_time).time()

    active_positions = {}
    armed_symbols = {}
    closed_trades = []

    for t in all_timestamps:
        symbols_to_close = []

        # Evaluate Active Positions
        for sym, pos in active_positions.items():
            if t not in symbol_frames[sym].index:
                continue
            bar = symbol_frames[sym].loc[t]
            bar_open, bar_high, bar_low, bar_close = bar['open'], bar['high'], bar['low'], bar['close']
            bar_time = bar['time_only']
            risk_ticks = pos.risk_ticks

            if bar_time >= session_cutoff:
                symbols_to_close.append((sym, bar_close, "SESSION_1515_SQUAREOFF"))
                continue

            if bar_low <= pos.stop_price:
                fill = min(bar_open, pos.stop_price) - (0.05 * risk_ticks)
                symbols_to_close.append((sym, fill, "STOP_TRIGGERED"))
                continue

            curr_peak_r = (bar_high - pos.entry_price) / risk_ticks
            curr_r = (bar_close - pos.entry_price) / risk_ticks
            pos.peak_r = max(pos.peak_r, curr_peak_r)

            r_target, z_target = governor.compute_targets(bar['vol_ratio'], bar_time)

            if curr_peak_r >= r_target:
                fill = pos.entry_price + (r_target * risk_ticks)
                symbols_to_close.append((sym, fill, "DYNAMIC_R_TARGET"))
                continue

            if bar['vwap_zscore'] >= z_target and curr_r >= config.min_harvest_r:
                symbols_to_close.append((sym, bar_close, "DYNAMIC_Z_TARGET"))
                continue

            if pos.peak_r >= config.trailing_profit_lock_r:
                new_stop = pos.entry_price + (pos.peak_r * config.trailing_profit_lock_pct * risk_ticks)
                pos.stop_price = max(pos.stop_price, new_stop)

        for sym, fill_p, reason in symbols_to_close:
            p = active_positions.pop(sym)
            gross_pnl = (fill_p - p.entry_price) * p.shares
            friction = ZerodhaFeeCalculator.calculate_round_trip(p.entry_price * p.shares, fill_p * p.shares)
            net_pnl = gross_pnl - friction
            risk_inr = p.risk_ticks * p.shares

            closed_trades.append({
                'symbol': sym,
                'entry_time': p.entry_time,
                'exit_time': t,
                'entry_price': p.entry_price,
                'exit_price': fill_p,
                'shares': p.shares,
                'risk_inr': risk_inr,
                'gross_pnl': gross_pnl,
                'friction': friction,
                'net_pnl': net_pnl,
                'net_r': net_pnl / max(1.0, risk_inr),
                'exit_reason': reason
            })

        # Evaluate Entries
        available_slots = config.max_concurrent_positions - len(active_positions)
        if available_slots <= 0:
            continue

        # Endogenous Breadth Interlock
        current_z_scores = [
            symbol_frames[s].loc[t, 'vwap_zscore']
            for s in symbol_frames
            if t in symbol_frames[s].index
        ]
        if current_z_scores and np.mean(current_z_scores) < -1.1:
            armed_symbols.clear()
            continue

        active_sectors = [config.sector_map.get(s, "OTHER") for s in active_positions.keys()]

        for sym, df in symbol_frames.items():
            if sym in active_positions or t not in df.index:
                continue

            # Gap-Down Interlock: Inhibit symbol on structural flush days
            current_date = df.loc[t, 'date_only']
            if (sym, current_date) in gapped_symbols_per_day:
                continue

            sym_sector = config.sector_map.get(sym, "OTHER")
            if active_sectors.count(sym_sector) >= 1:
                continue

            loc_idx = df.index.get_loc(t)
            if loc_idx < 2 or loc_idx + 1 >= len(df):
                continue

            curr_bar = df.iloc[loc_idx]
            prev_bar = df.iloc[loc_idx - 1]

            if curr_bar['time_only'] >= entry_cutoff:
                if sym in armed_symbols:
                    del armed_symbols[sym]
                continue

            if curr_bar['vwap_zscore'] < config.z_arm_threshold and curr_bar['rsi_14'] < config.rsi_arm_threshold:
                armed_symbols[sym] = ArmedState(
                    armed_time=t,
                    swing_low=min(curr_bar['low'], prev_bar['low'])
                )

            if sym in armed_symbols:
                armed_symbols[sym].swing_low = min(armed_symbols[sym].swing_low, curr_bar['low'])

                if curr_bar['close'] > prev_bar['high'] and curr_bar['close'] > curr_bar['open']:
                    next_bar = df.iloc[loc_idx + 1]
                    entry_p = next_bar['open']
                    swing_low = armed_symbols[sym].swing_low
                    risk_t = max(entry_p - swing_low, config.stop_atr_multiplier * curr_bar['atr'])

                    shares = int(config.slot_capital / entry_p)

                    if shares >= 1:
                        active_positions[sym] = Position(
                            symbol=sym,
                            entry_time=next_bar.name,
                            entry_price=entry_p,
                            stop_price=entry_p - risk_t,
                            risk_ticks=risk_t,
                            shares=shares,
                            peak_r=0.0
                        )
                        del armed_symbols[sym]
                        active_sectors.append(sym_sector)
                        available_slots -= 1
                        if available_slots <= 0:
                            break

    print("=" * 75)
    print("JANUARY 2024: GAP-FILTERED AUDIT REPORT")
    print("=" * 75)
    tdf = pd.DataFrame(closed_trades)
    print(f"Total Completed Trades : {len(tdf)}")
    if not tdf.empty:
        wr = (tdf['net_pnl'] > 0).mean() * 100
        print(f"Win Rate               : {wr:.1f}%")
        print(f"Gross P&L              : ₹{tdf['gross_pnl'].sum():+10,.2f}")
        print(f"Statutory Fees         : ₹{tdf['friction'].sum():10,.2f}")
        print(f"Net Portfolio P&L      : ₹{tdf['net_pnl'].sum():+10,.2f}")
        print("-" * 75)
        print("Trade Ledger:")
        for _, r in tdf.iterrows():
            print(f"  {r['symbol']:<10} | In: {str(r['entry_time'])[:16]} | Out: {str(r['exit_time'])[:16]} | {r['exit_reason']:<20} | Net: ₹{r['net_pnl']:+8.2f}")
    print("=" * 75)

if __name__ == '__main__':
    run_january_test()
