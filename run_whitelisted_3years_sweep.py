"""
Whitelisted 17-Symbol 3-Year Replay (July 2023 - August 2026)
------------------------------------------------------------
Runs exclusively on validated mean-reverting assets:
AXISBANK, ULTRACEMCO, M&M, JSWSTEEL, BAJAJ-AUTO, EICHERMOT,
HINDUNILVR, BAJFINANCE, ITC, GRASIM, MARUTI, COALINDIA,
SBILIFE, NTPC, HDFCLIFE, CIPLA, ETERNAL.
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

DATA_DIR = Path('/home/dishan/data_48')

WHITELIST = [
    "AXISBANK", "ULTRACEMCO", "M&M", "JSWSTEEL", "BAJAJ-AUTO",
    "EICHERMOT", "HINDUNILVR", "BAJFINANCE", "ITC", "GRASIM",
    "MARUTI", "COALINDIA", "SBILIFE", "NTPC", "HDFCLIFE", "CIPLA", "ETERNAL"
]

SECTOR_MAP = {
    "AXISBANK": "BANK", "BAJFINANCE": "FIN", "SBILIFE": "FIN", "HDFCLIFE": "FIN",
    "M&M": "AUTO", "BAJAJ-AUTO": "AUTO", "EICHERMOT": "AUTO", "MARUTI": "AUTO",
    "ULTRACEMCO": "CEMENT", "GRASIM": "CEMENT", "JSWSTEEL": "METALS",
    "HINDUNILVR": "FMCG", "ITC": "FMCG",
    "COALINDIA": "ENERGY", "NTPC": "ENERGY",
    "CIPLA": "PHARMA", "ETERNAL": "OTHER"
}

def discover_whitelisted_files(data_dir: Path) -> dict:
    sym_map = {}
    for f in data_dir.glob("NSE_*_minute_*.csv"):
        parts = f.name.split('_')
        if len(parts) >= 2:
            sym = parts[1].upper()
            if sym in WHITELIST:
                sym_map[sym] = f
    return sym_map

def generate_monthly_windows(start_year=2023, end_year=2026):
    windows = []
    for y in range(start_year, end_year + 1):
        m_start = 7 if y == 2023 else 1
        m_end = 8 if y == 2026 else 12
        for m in range(m_start, m_end + 1):
            if y == 2026 and m == 8:
                start_d = "2026-08-01"
                end_d = "2026-08-24"
            else:
                last_day = calendar.monthrange(y, m)[1]
                start_d = f"{y}-{m:02d}-01"
                end_d = f"{y}-{m:02d}-{last_day:02d}"
            label = f"{y}-{m:02d} ({calendar.month_abbr[m]})"
            windows.append((label, start_d, end_d))
    return windows

def compute_daily_gaps(symbol_frames: dict) -> set:
    gapped_days = set()
    for sym, df in symbol_frames.items():
        daily = df.groupby('date_only').agg(
            first_open=('open', 'first'),
            last_close=('close', 'last')
        ).sort_index()
        daily['prev_close'] = daily['last_close'].shift(1)
        daily['gap_pct'] = (daily['first_open'] - daily['prev_close']) / daily['prev_close'] * 100.0
        for d in daily[daily['gap_pct'] <= -1.75].index:
            gapped_days.add((sym, d))
    return gapped_days

def run_month_slice(config: EngineConfig, sym_files: dict, start_d: str, end_d: str) -> pd.DataFrame:
    governor = TimeDecayGovernor(config)
    symbol_frames = {}

    for sym, fpath in sym_files.items():
        try:
            raw = pd.read_csv(fpath)
            t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= start_d) & (raw['dt'] <= end_d)].copy()
            if len(m) > 150:
                df_15 = WelfordFeaturePipeline.resample_to_15m(m)
                if len(df_15) > 20:
                    symbol_frames[sym] = WelfordFeaturePipeline.compute_features(df_15).set_index('dt')
        except Exception:
            continue

    if len(symbol_frames) < 3:
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

        # 1. Active position lifecycle
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

        # 2. Entries (Calibrated Breadth for 17-stock basket: Z_bar < -0.45)
        available_slots = config.max_concurrent_positions - len(active_positions)
        if available_slots <= 0:
            continue

        current_z = [
            symbol_frames[s].loc[t, 'vwap_zscore']
            for s in symbol_frames if t in symbol_frames[s].index
        ]
        if current_z and np.mean(current_z) < -0.45:
            armed_symbols.clear()
            continue

        active_sectors = [SECTOR_MAP.get(s, "OTHER") for s in active_positions.keys()]

        for sym, df in symbol_frames.items():
            if sym in active_positions or t not in df.index:
                continue

            current_date = df.loc[t, 'date_only']
            if (sym, current_date) in gapped_days:
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
                if sym in armed_symbols:
                    del armed_symbols[sym]
                continue

            # High-conviction entry trigger
            if curr_bar['vwap_zscore'] < -2.5 and curr_bar['rsi_14'] < 28.0:
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

def main():
    sym_files = discover_whitelisted_files(DATA_DIR)
    print(f"[INFO] Discovered {len(sym_files)} Whitelisted Symbols in {DATA_DIR}")
    print(f"[INFO] Active Universe: {', '.join(sorted(list(sym_files.keys())))}")

    config = EngineConfig()
    config.symbols = list(sym_files.keys())
    config.sector_map = SECTOR_MAP

    windows = generate_monthly_windows(2023, 2026)
    print("=" * 80)
    print("38-MONTH WHITELISTED WALK-FORWARD REPLAY (JULY 2023 - AUGUST 2026)")
    print("=" * 80)

    records = []
    all_ledgers = []

    for label, start_d, end_d in windows:
        print(f"[REPLAY] {label}... ", end="", flush=True)
        tdf = run_month_slice(config, sym_files, start_d, end_d)

        if tdf.empty:
            print("Trades: 0 | Net: ₹0.00")
            records.append({
                'Month': label, 'Trades': 0, 'Win_Rate': 0.0,
                'Gross_PNL': 0.0, 'Friction': 0.0, 'Net_PNL': 0.0
            })
            continue

        tdf['month_period'] = label
        all_ledgers.append(tdf)

        tot = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100
        gross = tdf['gross_pnl'].sum()
        fees = tdf['friction'].sum()
        net = tdf['net_pnl'].sum()

        records.append({
            'Month': label, 'Trades': tot, 'Win_Rate': wr,
            'Gross_PNL': gross, 'Friction': fees, 'Net_PNL': net
        })
        print(f"Trades: {tot:<2} | WR: {wr:5.1f}% | Gross: ₹{gross:+9,.2f} | Fees: ₹{fees:7,.2f} | Net: ₹{net:+9,.2f}")

    sdf = pd.DataFrame(records)
    tot_trades = sdf['Trades'].sum()
    tot_gross = sdf['Gross_PNL'].sum()
    tot_fees = sdf['Friction'].sum()
    tot_net = sdf['Net_PNL'].sum()
    agg_wr = (pd.concat(all_ledgers)['net_pnl'] > 0).mean() * 100 if all_ledgers else 0.0

    print("\n" + "=" * 80)
    print("CONSOLIDATED 38-MONTH WHITELIST AUDIT")
    print("=" * 80)
    print(f"Total Completed Trades : {int(tot_trades)}")
    print(f"Aggregate Win Rate     : {agg_wr:.1f}%")
    print(f"Total Gross Alpha      : ₹{tot_gross:+12,.2f}")
    print(f"Total Statutory Fees   : ₹{tot_fees:12,.2f}")
    print(f"Total Net Portfolio P&L: ₹{tot_net:+12,.2f}")
    print("=" * 80)

if __name__ == '__main__':
    main()
