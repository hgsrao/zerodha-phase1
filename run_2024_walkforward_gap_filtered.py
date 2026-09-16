"""
2024 Walk-Forward Sweep (Breadth + Session Gap-Down Filter)
----------------------------------------------------------
Runs across all 2024 months with:
1. Endogenous Breadth Gate (inhibit on market flush)
2. Daily Gap-Down Circuit Breaker (inhibit symbol on >= 1.75% opening gap down)
"""

import calendar
import sys
from pathlib import Path
import pandas as pd
import numpy as np

from alpha_engine_core import (
    EngineConfig, ZerodhaFeeCalculator, TimeDecayGovernor,
    WelfordFeaturePipeline, Position, ArmedState
)

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

def compute_daily_gaps(symbol_frames: dict) -> set:
    gapped_days = set()
    for sym, df in symbol_frames.items():
        daily = df.groupby('date_only').agg(
            first_open=('open', 'first'),
            last_close=('close', 'last')
        ).sort_index()
        daily['prev_close'] = daily['last_close'].shift(1)
        daily['gap_pct'] = (daily['first_open'] - daily['prev_close']) / daily['prev_close'] * 100.0
        bad_dates = daily[daily['gap_pct'] <= -1.75].index
        for d in bad_dates:
            gapped_days.add((sym, d))
    return gapped_days

def run_month_replay(config: EngineConfig, start_d: str, end_d: str) -> pd.DataFrame:
    governor = TimeDecayGovernor(config)
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
            m = raw[(raw['dt'] >= start_d) & (raw['dt'] <= end_d)].copy()
            if len(m) > 200:
                df_15 = WelfordFeaturePipeline.resample_to_15m(m)
                if len(df_15) > 30:
                    symbol_frames[sym] = WelfordFeaturePipeline.compute_features(df_15).set_index('dt')
        except Exception:
            continue

    if not symbol_frames:
        return pd.DataFrame()

    gapped_days = compute_daily_gaps(symbol_frames)
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in symbol_frames.values()])))
    session_cutoff = pd.to_datetime(config.session_close_time).time()
    entry_cutoff = pd.to_datetime(config.entry_cutoff_time).time()

    active_positions = {}
    armed_symbols = {}
    closed_trades = []

    for t in all_timestamps:
        symbols_to_close = []

        # 1. Evaluate Active Positions
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

        # 2. Evaluate Entries
        available_slots = config.max_concurrent_positions - len(active_positions)
        if available_slots <= 0:
            continue

        # Endogenous Breadth Interlock
        current_z = [
            symbol_frames[s].loc[t, 'vwap_zscore']
            for s in symbol_frames if t in symbol_frames[s].index
        ]
        if current_z and np.mean(current_z) < -1.1:
            armed_symbols.clear()
            continue

        active_sectors = [config.sector_map.get(s, "OTHER") for s in active_positions.keys()]

        for sym, df in symbol_frames.items():
            if sym in active_positions or t not in df.index:
                continue

            # Gap-Down Repricing Filter
            current_date = df.loc[t, 'date_only']
            if (sym, current_date) in gapped_days:
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

    return pd.DataFrame(closed_trades)

def get_2024_windows():
    windows = []
    for month in range(1, 9):
        if month == 8:
            windows.append(("2024-08 (Aug)", "2024-08-01", "2024-08-24"))
        else:
            last_day = calendar.monthrange(2024, month)[1]
            windows.append((f"2024-{month:02d} ({calendar.month_abbr[month]})", f"2024-{month:02d}-01", f"2024-{month:02d}-{last_day:02d}"))
    return windows

def print_terminal_pnl_chart(summary_df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("2024 MONTHLY NET P&L DISTRIBUTION (GAP-FILTERED)")
    print("=" * 80)
    max_val = max(summary_df['Net_PNL'].abs().max(), 1.0)
    bar_width = 30
    for _, r in summary_df.iterrows():
        val = r['Net_PNL']
        units = int(abs(val) / max_val * bar_width)
        if val >= 0:
            bar = f"{' ' * bar_width}|{'█' * units:<{bar_width}}"
        else:
            bar = f"{'█' * units:>{bar_width}}|{' ' * bar_width}"
        print(f"{r['Month']:<18} {bar}  ₹{val:+10,.2f}")
    print("=" * 80)

def main():
    config = EngineConfig()
    windows = get_2024_windows()
    records = []
    all_ledgers = []

    print("=" * 80)
    print("STARTING 2024 WALK-FORWARD SWEEP (GAP-FILTERED + BREADTH)")
    print("=" * 80)

    for label, start_d, end_d in windows:
        print(f"\n[SWEEP] Processing {label} | Range: {start_d} to {end_d}...")
        trades_df = run_month_replay(config, start_d, end_d)
        
        if trades_df.empty:
            records.append({
                'Month': label, 'Trades': 0, 'Win_Rate': 0.0,
                'Gross_PNL': 0.0, 'Friction': 0.0, 'Net_PNL': 0.0, 'Net_R': 0.0
            })
            continue

        trades_df['month_period'] = label
        all_ledgers.append(trades_df)

        tot = len(trades_df)
        wr = (trades_df['net_pnl'] > 0).mean() * 100
        gross = trades_df['gross_pnl'].sum()
        fees = trades_df['friction'].sum()
        net = trades_df['net_pnl'].sum()
        avg_r = trades_df['net_r'].mean()

        records.append({
            'Month': label, 'Trades': tot, 'Win_Rate': wr,
            'Gross_PNL': gross, 'Friction': fees, 'Net_PNL': net, 'Net_R': avg_r
        })
        print(f"  Trades: {tot} | WR: {wr:.1f}% | Gross: ₹{gross:+,.2f} | Fees: ₹{fees:,.2f} | Net: ₹{net:+,.2f}")

    summary_df = pd.DataFrame(records)

    print("\n" + "=" * 80)
    print("2024 GAP-FILTERED PERFORMANCE SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Month':<18} | {'Trades':<6} | {'Win %':<6} | {'Gross P&L':<12} | {'Fees':<10} | {'Net P&L':<12}")
    print("-" * 80)
    for _, r in summary_df.iterrows():
        print(f"{r['Month']:<18} | {int(r['Trades']):<6} | {r['Win_Rate']:5.1f}% | ₹{r['Gross_PNL']:+10,.2f} | ₹{r['Friction']:8,.2f} | ₹{r['Net_PNL']:+10,.2f}")
    print("-" * 80)

    tot_trades = summary_df['Trades'].sum()
    tot_gross = summary_df['Gross_PNL'].sum()
    tot_fees = summary_df['Friction'].sum()
    tot_net = summary_df['Net_PNL'].sum()

    print(f"{'TOTAL':<18} | {int(tot_trades):<6} | {'---':<6} | ₹{tot_gross:+10,.2f} | ₹{tot_fees:8,.2f} | ₹{tot_net:+10,.2f}")
    print("=" * 80)

    print_terminal_pnl_chart(summary_df)

    if all_ledgers:
        pd.concat(all_ledgers, ignore_index=True).to_parquet("walkforward_2024_gapfiltered_ledger.parquet")
        print("[INFO] Saved to 'walkforward_2024_gapfiltered_ledger.parquet'")

if __name__ == '__main__':
    main()
