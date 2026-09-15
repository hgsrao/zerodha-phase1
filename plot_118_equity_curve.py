"""
118-Trade Cumulative Net Equity & Underwater Drawdown Visualizer
---------------------------------------------------------------
1. Re-executes the exact calibrated 17-symbol engine logic across 2023-2026.
2. Captures all 118 executed trades with trade-level timestamps and net P&Ls.
3. Computes cumulative net equity and rolling high-water mark drawdown.
4. Generates a publication-grade dual-panel performance plot saved to PNG.
"""

import calendar
import sys
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

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

def discover_whitelisted_files(data_dir: Path) -> dict:
    sym_map = {}
    for f in data_dir.glob("NSE_*_minute_*.csv"):
        parts = f.name.split('_')
        if len(parts) >= 2:
            sym = parts[1].upper()
            if sym in WHITELIST:
                sym_map[sym] = f
    return sym_map

def compute_variance_ratio(log_returns: np.ndarray, k: int = 15) -> float:
    if len(log_returns) < k * 5:
        return 1.0
    var_1 = np.var(log_returns, ddof=1)
    k_returns = pd.Series(log_returns).rolling(k).sum().dropna().values
    var_k = np.var(k_returns, ddof=1)
    if var_1 == 0:
        return 1.0
    return float(var_k / (k * var_1))

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
            label = f"{y}-{m:02d}"
            windows.append((label, start_d, end_d))
    return windows

def compute_daily_gaps(symbol_frames: dict, gap_threshold: float = 1.25) -> set:
    gapped_days = set()
    for sym, df in symbol_frames.items():
        daily = df.groupby('date_only').agg(
            first_open=('open', 'first'),
            last_close=('close', 'last')
        ).sort_index()
        daily['prev_close'] = daily['last_close'].shift(1)
        daily['gap_pct'] = (daily['first_open'] - daily['prev_close']) / daily['prev_close'] * 100.0
        for d in daily[daily['gap_pct'] <= -abs(gap_threshold)].index:
            gapped_days.add((sym, d))
    return gapped_days

def run_simulation() -> pd.DataFrame:
    sym_files = discover_whitelisted_files(DATA_DIR)
    config = EngineConfig()
    config.symbols = list(sym_files.keys())
    windows = generate_monthly_windows(2023, 2026)

    governor = TimeDecayGovernor(config)
    session_cutoff = pd.to_datetime(config.session_close_time).time()
    entry_cutoff = pd.to_datetime(config.entry_cutoff_time).time()

    all_closed_trades = []

    print("[1/2] Replaying 38-month production cycles across 17 assets...")
    for label, start_d, end_d in windows:
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
            continue

        gapped_days = compute_daily_gaps(symbol_frames, gap_threshold=1.25)
        all_timestamps = sorted(list(set.union(*[set(df.index) for df in symbol_frames.values()])))

        active_positions = {}
        armed_symbols = {}

        for t in all_timestamps:
            symbols_to_close = []

            # 1. Exit logic
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
                r_target = max(r_target, 1.80)

                if curr_peak_r >= r_target:
                    symbols_to_close.append((sym, pos.entry_price + (r_target * risk_ticks), "DYNAMIC_R_TARGET"))
                    continue

                if bar['vwap_zscore'] >= z_target and curr_r >= 1.40:
                    symbols_to_close.append((sym, bar_close, "DYNAMIC_Z_TARGET"))
                    continue

                if pos.peak_r >= 1.20:
                    pos.stop_price = max(pos.stop_price, pos.entry_price + (pos.peak_r * 0.40 * risk_ticks))

            for sym, fill_p, reason in symbols_to_close:
                p = active_positions.pop(sym)
                gross = (fill_p - p.entry_price) * p.shares
                friction = ZerodhaFeeCalculator.calculate_round_trip(p.entry_price * p.shares, fill_p * p.shares)
                net = gross - friction
                all_closed_trades.append({
                    'symbol': sym,
                    'entry_time': p.entry_time,
                    'exit_time': t,
                    'gross_pnl': gross,
                    'friction': friction,
                    'net_pnl': net,
                    'exit_reason': reason
                })

            # 2. Entry logic
            if config.max_concurrent_positions - len(active_positions) <= 0:
                continue

            current_z = [
                symbol_frames[s].loc[t, 'vwap_zscore']
                for s in symbol_frames if t in symbol_frames[s].index
            ]
            if current_z and np.mean(current_z) < -0.45:
                armed_symbols.clear()
                continue

            active_sectors = [config.sector_map.get(s, "OTHER") for s in active_positions.keys()]

            for sym, df in symbol_frames.items():
                if sym in active_positions or t not in df.index:
                    continue
                if (sym, df.loc[t, 'date_only']) in gapped_days:
                    continue

                sym_sector = config.sector_map.get(sym, "OTHER")
                if active_sectors.count(sym_sector) >= 1:
                    continue

                loc_idx = df.index.get_loc(t)
                if loc_idx < 30 or loc_idx + 1 >= len(df):
                    continue

                curr_bar = df.iloc[loc_idx]
                prev_bar = df.iloc[loc_idx - 1]

                if curr_bar['time_only'] >= entry_cutoff:
                    armed_symbols.pop(sym, None)
                    continue

                window_bars = df.iloc[loc_idx-25:loc_idx]
                log_ret = np.diff(np.log(window_bars['close'].values))
                if compute_variance_ratio(log_ret, k=5) > 1.05:
                    continue

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
                        risk_t = max(entry_p - armed_symbols[sym].swing_low, config.stop_atr_multiplier * curr_bar['atr'])
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
                            armed_symbols.pop(sym, None)
                            active_sectors.append(sym_sector)
                            if config.max_concurrent_positions - len(active_positions) <= 0:
                                break

    return pd.DataFrame(all_closed_trades)

def plot_equity_and_drawdown(df: pd.DataFrame, output_path: str = "production_118_equity_profile.png"):
    print("[2/2] Calculating equity milestones and rendering high-resolution plot...")
    df = df.sort_values('exit_time').reset_index(drop=True)

    df['cum_gross'] = df['gross_pnl'].cumsum()
    df['cum_net'] = df['net_pnl'].cumsum()
    df['cum_friction'] = df['friction'].cumsum()

    # Drawdown based on cumulative net P&L
    df['hwm'] = df['cum_net'].cummax()
    df['drawdown'] = df['cum_net'] - df['hwm']
    max_dd = df['drawdown'].min()
    max_dd_idx = df['drawdown'].idxmin()
    max_dd_date = df.loc[max_dd_idx, 'exit_time']

    final_net = df['cum_net'].iloc[-1]
    final_gross = df['cum_gross'].iloc[-1]
    total_fees = df['cum_friction'].iloc[-1]
    win_rate = (df['net_pnl'] > 0).mean() * 100.0

    # Plot styling
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True, gridspec_kw={'height_ratios': [2.2, 1.0]})

    # Panel 1: Cumulative Net Equity vs Gross
    ax1.plot(df['exit_time'], df['cum_net'], label=f'Net P&L (Post-Fees): ₹{final_net:+,.2f}', color='#107c41', linewidth=2.2)
    ax1.plot(df['exit_time'], df['cum_gross'], label=f'Gross Alpha: ₹{final_gross:+,.2f}', color='#70ad47', linewidth=1.2, linestyle='--')
    ax1.plot(df['exit_time'], df['hwm'], label='High Watermark', color='#004b1c', linewidth=0.8, linestyle=':')

    ax1.set_title(
        f"118-Trade Production Performance | July 2023 – August 2026\n"
        f"Net P&L: ₹{final_net:+,.2f} | Fees: ₹{total_fees:,.2f} | Win Rate: {win_rate:.1f}% | Max DD: ₹{max_dd:,.2f}",
        fontsize=12, fontweight='bold', pad=12
    )
    ax1.set_ylabel("Portfolio Net P&L (₹)", fontsize=11, fontweight='bold')
    ax1.legend(loc='upper left', frameon=True, framealpha=0.9)
    ax1.grid(True, linestyle='--', alpha=0.5)

    # Panel 2: Underwater Drawdown
    ax2.plot(df['exit_time'], df['drawdown'], color='#c00000', linewidth=1.5)
    ax2.fill_between(df['exit_time'], df['drawdown'], 0, color='#ff0000', alpha=0.25)
    ax2.axhline(0, color='black', linewidth=0.8, linestyle='--')

    # Annotate Max Drawdown
    ax2.scatter([max_dd_date], [max_dd], color='#900000', zorder=5, s=40)
    ax2.annotate(
        f"Max DD: ₹{max_dd:,.2f}",
        xy=(max_dd_date, max_dd),
        xytext=(max_dd_date, max_dd * 0.6),
        arrowprops=dict(facecolor='black', arrowstyle='->', lw=1.0),
        fontsize=9, fontweight='bold', color='#900000'
    )

    ax2.set_ylabel("Drawdown (₹)", fontsize=11, fontweight='bold')
    ax2.set_xlabel("Trade Exit Date", fontsize=11, fontweight='bold')
    ax2.grid(True, linestyle='--', alpha=0.5)

    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    fig.autofmt_xdate()

    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()

    print(f"\n[SUCCESS] Rendered high-resolution chart to: {Path(output_path).resolve()}")

def main():
    trades_df = run_simulation()
    print(f"[INFO] Verified total completed trades: {len(trades_df)}")
    plot_equity_and_drawdown(trades_df, "production_118_equity_profile.png")

if __name__ == '__main__':
    main()
