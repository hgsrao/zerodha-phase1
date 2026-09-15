"""
48-Symbol 3-Year Walk-Forward Execution Engine (2023 - 2026)
------------------------------------------------------------
- Discovers all 48 symbols using exact 'NSE_{SYMBOL}_minute' naming convention
- Pre-computes 15-minute bars and streaming Welford features
- Evaluates monthly slices (July 2023 - August 2026, 38 months)
- Applies Endogenous Breadth Gate (Z_bar < -1.1) + Session Gap-Down Filter (>= 1.75%)
- Enforces Sector De-Clustering, Dynamic Decay Governor & Zerodha statutory friction
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

BASE_SECTOR_MAP = {
    "HDFCBANK": "BANK", "ICICIBANK": "BANK", "SBIN": "BANK", "KOTAKBANK": "BANK", "AXISBANK": "BANK", "INDUSINDBK": "BANK",
    "INFY": "IT", "TCS": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT", "LTIM": "IT",
    "LT": "INFRA", "ADANIENT": "INFRA", "ADANIPORTS": "INFRA",
    "BAJFINANCE": "FIN", "BAJAJFINSV": "FIN", "CHOLAFIN": "FIN", "SBILIFE": "FIN", "HDFCLIFE": "FIN", "SHRIRAMFIN": "FIN",
    "TATAMOTORS": "AUTO", "MARUTI": "AUTO", "M&M": "AUTO", "HEROMOTOCO": "AUTO", "BAJAJ-AUTO": "AUTO", "EICHERMOT": "AUTO",
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "NTPC": "ENERGY", "POWERGRID": "ENERGY", "BPCL": "ENERGY", "COALINDIA": "ENERGY",
    "SUNPHARMA": "PHARMA", "CIPLA": "PHARMA", "DRREDDY": "PHARMA", "DIVISLAB": "PHARMA", "APOLLOHOSP": "PHARMA",
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS", "VEDL": "METALS",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG", "TATACONSUM": "FMCG",
    "TITAN": "CONSUMER", "BHARTIARTL": "TELECOM", "ASIANPAINT": "PAINTS", "GRASIM": "CEMENT", "ULTRACEMCO": "CEMENT"
}

def discover_universe(data_dir: Path) -> dict:
    """Discovers symbols directly via NSE_{SYMBOL}_minute_* format."""
    files = sorted(list(data_dir.glob("NSE_*_minute_*.csv")))
    sym_map = {}
    for f in files:
        parts = f.name.split('_')
        if len(parts) >= 2:
            sym = parts[1].upper()
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
        bad_dates = daily[daily['gap_pct'] <= -1.75].index
        for d in bad_dates:
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

        # 2. Entries with 48-symbol endogenous breadth gate
        available_slots = config.max_concurrent_positions - len(active_positions)
        if available_slots <= 0:
            continue

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

def print_terminal_pnl_chart(summary_df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("48-SYMBOL 3-YEAR MONTHLY NET P&L DISTRIBUTION")
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
    if not DATA_DIR.exists():
        print(f"[ERROR] Directory not found: {DATA_DIR}", file=sys.stderr)
        sys.exit(1)

    sym_files = discover_universe(DATA_DIR)
    print(f"[INFO] Discovered {len(sym_files)} unique symbols across the dataset.")
    print(f"[INFO] Symbol Universe: {', '.join(sorted(list(sym_files.keys()))[:12])}... (+{len(sym_files)-12} more)")

    config = EngineConfig()
    config.symbols = list(sym_files.keys())
    for s in config.symbols:
        config.sector_map[s] = BASE_SECTOR_MAP.get(s, "OTHER")

    windows = generate_monthly_windows(2023, 2026)
    print(f"[INFO] Running {len(windows)} monthly walk-forward slices (July 2023 - August 2026)...")
    print("=" * 80)

    monthly_summary = []
    all_trade_frames = []

    for label, start_d, end_d in windows:
        print(f"[REPLAY] {label} ({start_d} -> {end_d})... ", end="", flush=True)
        tdf = run_month_slice(config, sym_files, start_d, end_d)

        if tdf.empty:
            print("Trades: 0 | Net: ₹0.00")
            monthly_summary.append({
                'Month': label, 'Trades': 0, 'Win_Rate': 0.0,
                'Gross_PNL': 0.0, 'Friction': 0.0, 'Net_PNL': 0.0, 'Net_R': 0.0
            })
            continue

        tdf['month_period'] = label
        all_trade_frames.append(tdf)

        tot = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100
        gross = tdf['gross_pnl'].sum()
        friction = tdf['friction'].sum()
        net = tdf['net_pnl'].sum()
        avg_r = tdf['net_r'].mean()

        monthly_summary.append({
            'Month': label, 'Trades': tot, 'Win_Rate': wr,
            'Gross_PNL': gross, 'Friction': friction, 'Net_PNL': net, 'Net_R': avg_r
        })
        print(f"Trades: {tot:<3} | WR: {wr:5.1f}% | Gross: ₹{gross:+10,.2f} | Fees: ₹{friction:8,.2f} | Net: ₹{net:+10,.2f}")

    summary_df = pd.DataFrame(monthly_summary)

    print("\n" + "=" * 80)
    print("3-YEAR MULTI-ASSET (48 SYMBOLS) CONSOLIDATED AUDIT TABLE")
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
    agg_wr = (pd.concat(all_trade_frames)['net_pnl'] > 0).mean() * 100 if all_trade_frames else 0.0

    print(f"{'TOTAL (38 MONTHS)':<18} | {int(tot_trades):<6} | {agg_wr:5.1f}% | ₹{tot_gross:+10,.2f} | ₹{tot_fees:8,.2f} | ₹{tot_net:+10,.2f}")
    print("=" * 80)

    print_terminal_pnl_chart(summary_df)

    if all_trade_frames:
        master_3yr = pd.concat(all_trade_frames, ignore_index=True)
        out_file = "alpha_48symbols_3years_master_ledger.parquet"
        master_3yr.to_parquet(out_file)
        print(f"\n[INFO] Successfully written full 3-year audit ledger to '{out_file}'.")

if __name__ == '__main__':
    main()
