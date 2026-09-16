"""
2023 Batch Walk-Forward Runner with Macro Trend Gate
---------------------------------------------------
Loads NIFTY index data as a top-down macro interlock.
Inhibits long entries across the Alpha Universe when the broader
index is in a sustained breakdown regime.
"""

import calendar
import sys
from pathlib import Path
import pandas as pd
import numpy as np

from alpha_engine_core import EngineConfig, ZerodhaFeeCalculator, TimeDecayGovernor, WelfordFeaturePipeline, Position, ArmedState

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

def load_nifty_macro(start_d: str, end_d: str) -> Optional[pd.DataFrame]:
    """Finds and prepares NIFTY index 15-minute trend data."""
    matches = list(DATA_DIR.glob("*NIFTY*.csv")) + list(DATA_DIR.glob("*_NIFTY_*.csv"))
    # Exclude BANKNIFTY or other indices
    matches = [m for m in matches if "BANK" not in m.name.upper()]
    if not matches:
        return None
    fpath = matches[0]
    try:
        raw = pd.read_csv(fpath)
        t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
        raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
        m = raw[(raw['dt'] >= start_d) & (raw['dt'] <= end_d)].copy()
        if len(m) < 200:
            return None
        df_15 = WelfordFeaturePipeline.resample_to_15m(m)
        df_15 = WelfordFeaturePipeline.compute_features(df_15)
        df_15['ema_50'] = df_15['close'].ewm(span=50, adjust=False).mean()
        return df_15.set_index('dt')
    except Exception:
        return None

class MacroGatedEngine:
    def __init__(self, config: EngineConfig, data_dir: Path):
        self.cfg = config
        self.data_dir = data_dir
        self.governor = TimeDecayGovernor(config)
        self.active_positions = {}
        self.armed_symbols = {}
        self.closed_trades = []
        self.symbol_frames = {}
        self.nifty_df = None

    def load_dataset(self, start_date: str, end_date: str):
        self.nifty_df = load_nifty_macro(start_date, end_date)
        
        for sym in self.cfg.symbols:
            matches = list(self.data_dir.glob(f"*_{sym}_*.csv"))
            if not matches:
                continue
            fpath = matches[0]
            try:
                raw = pd.read_csv(fpath)
                t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
                raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
                m = raw[(raw['dt'] >= start_date) & (raw['dt'] <= end_date)].copy()
                if len(m) > 200:
                    df_15 = WelfordFeaturePipeline.resample_to_15m(m)
                    if len(df_15) > 30:
                        self.symbol_frames[sym] = WelfordFeaturePipeline.compute_features(df_15).set_index('dt')
            except Exception:
                continue

    def run_replay(self) -> pd.DataFrame:
        if not self.symbol_frames:
            return pd.DataFrame()
            
        all_timestamps = sorted(list(set.union(*[set(df.index) for df in self.symbol_frames.values()])))
        session_cutoff = pd.to_datetime(self.cfg.session_close_time).time()
        entry_cutoff = pd.to_datetime(self.cfg.entry_cutoff_time).time()

        for t in all_timestamps:
            symbols_to_close = []

            # 1. EVALUATE ACTIVE POSITIONS
            for sym, pos in self.active_positions.items():
                if t not in self.symbol_frames[sym].index:
                    continue
                bar = self.symbol_frames[sym].loc[t]
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

                r_target, z_target = self.governor.compute_targets(bar['vol_ratio'], bar_time)

                if curr_peak_r >= r_target:
                    fill = pos.entry_price + (r_target * risk_ticks)
                    symbols_to_close.append((sym, fill, "DYNAMIC_R_TARGET"))
                    continue

                if bar['vwap_zscore'] >= z_target and curr_r >= self.cfg.min_harvest_r:
                    symbols_to_close.append((sym, bar_close, "DYNAMIC_Z_TARGET"))
                    continue

                if pos.peak_r >= self.cfg.trailing_profit_lock_r:
                    new_stop = pos.entry_price + (pos.peak_r * self.cfg.trailing_profit_lock_pct * risk_ticks)
                    pos.stop_price = max(pos.stop_price, new_stop)

            for sym, fill_p, reason in symbols_to_close:
                p = self.active_positions.pop(sym)
                gross_pnl = (fill_p - p.entry_price) * p.shares
                friction = ZerodhaFeeCalculator.calculate_round_trip(
                    p.entry_price * p.shares, fill_p * p.shares
                )
                net_pnl = gross_pnl - friction
                risk_inr = p.risk_ticks * p.shares

                self.closed_trades.append({
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

            # 2. EVALUATE ENTRIES (WITH MACRO TREND GATE)
            available_slots = self.cfg.max_concurrent_positions - len(self.active_positions)
            if available_slots <= 0:
                continue

            # MACRO INTERLOCK CHECK: Inhibit if NIFTY is below EMA50 & deeply negative Z
            macro_inhibit = False
            if self.nifty_df is not None and t in self.nifty_df.index:
                n_bar = self.nifty_df.loc[t]
                if n_bar['close'] < n_bar['ema_50'] and n_bar['vwap_zscore'] < -1.0:
                    macro_inhibit = True

            if macro_inhibit:
                # Clear pending armings during macro breakdown
                self.armed_symbols.clear()
                continue

            active_sectors = [self.cfg.sector_map.get(s, "OTHER") for s in self.active_positions.keys()]

            for sym, df in self.symbol_frames.items():
                if sym in self.active_positions or t not in df.index:
                    continue

                sym_sector = self.cfg.sector_map.get(sym, "OTHER")
                if active_sectors.count(sym_sector) >= 1:
                    continue

                loc_idx = df.index.get_loc(t)
                if loc_idx < 2 or loc_idx + 1 >= len(df):
                    continue

                curr_bar = df.iloc[loc_idx]
                prev_bar = df.iloc[loc_idx - 1]

                if curr_bar['time_only'] >= entry_cutoff:
                    if sym in self.armed_symbols:
                        del self.armed_symbols[sym]
                    continue

                if curr_bar['vwap_zscore'] < self.cfg.z_arm_threshold and curr_bar['rsi_14'] < self.cfg.rsi_arm_threshold:
                    self.armed_symbols[sym] = ArmedState(
                        armed_time=t,
                        swing_low=min(curr_bar['low'], prev_bar['low'])
                    )

                if sym in self.armed_symbols:
                    self.armed_symbols[sym].swing_low = min(self.armed_symbols[sym].swing_low, curr_bar['low'])

                    if curr_bar['close'] > prev_bar['high'] and curr_bar['close'] > curr_bar['open']:
                        next_bar = df.iloc[loc_idx + 1]
                        entry_p = next_bar['open']
                        swing_low = self.armed_symbols[sym].swing_low
                        risk_t = max(entry_p - swing_low, self.cfg.stop_atr_multiplier * curr_bar['atr'])

                        shares = int(self.cfg.slot_capital / entry_p)

                        if shares >= 1:
                            self.active_positions[sym] = Position(
                                symbol=sym,
                                entry_time=next_bar.name,
                                entry_price=entry_p,
                                stop_price=entry_p - risk_t,
                                risk_ticks=risk_t,
                                shares=shares,
                                peak_r=0.0
                            )
                            del self.armed_symbols[sym]
                            active_sectors.append(sym_sector)
                            available_slots -= 1
                            if available_slots <= 0:
                                break

        return pd.DataFrame(self.closed_trades)

def get_2023_month_windows():
    windows = []
    for month in range(7, 13):
        last_day = calendar.monthrange(2023, month)[1]
        start_date = f"2023-{month:02d}-01"
        end_date = f"2023-{month:02d}-{last_day:02d}"
        month_label = f"2023-{month:02d} ({calendar.month_abbr[month]})"
        windows.append((month_label, start_date, end_date))
    return windows

def print_terminal_pnl_chart(summary_df: pd.DataFrame):
    print("\n" + "=" * 80)
    print("MACRO-GATED 2023 MONTHLY NET P&L DISTRIBUTION")
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
    windows = get_2023_month_windows()
    monthly_records = []
    all_trade_ledgers = []

    print("=" * 80)
    print("STARTING 2023 WALK-FORWARD WITH MACRO TREND GATE")
    print("=" * 80)

    for label, start_d, end_d in windows:
        print(f"\n[SWEEP] Processing {label} | Range: {start_d} to {end_d}...")
        engine = MacroGatedEngine(config, DATA_DIR)
        engine.load_dataset(start_d, end_d)
        
        trades_df = engine.run_replay()
        if trades_df.empty:
            monthly_records.append({
                'Month': label, 'Trades': 0, 'Win_Rate': 0.0,
                'Gross_PNL': 0.0, 'Friction': 0.0, 'Net_PNL': 0.0, 'Net_R': 0.0
            })
            continue

        trades_df['month_period'] = label
        all_trade_ledgers.append(trades_df)

        total_trades = len(trades_df)
        wr = (trades_df['net_pnl'] > 0).mean() * 100
        gross = trades_df['gross_pnl'].sum()
        fees = trades_df['friction'].sum()
        net = trades_df['net_pnl'].sum()
        avg_r = trades_df['net_r'].mean()

        monthly_records.append({
            'Month': label, 'Trades': total_trades, 'Win_Rate': wr,
            'Gross_PNL': gross, 'Friction': fees, 'Net_PNL': net, 'Net_R': avg_r
        })
        print(f"  Trades: {total_trades} | WR: {wr:.1f}% | Gross: ₹{gross:+,.2f} | Fees: ₹{fees:,.2f} | Net: ₹{net:+,.2f}")

    summary_df = pd.DataFrame(monthly_records)

    print("\n" + "=" * 80)
    print("MACRO-GATED 2023 PERFORMANCE SUMMARY TABLE")
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

    if all_trade_ledgers:
        master_df = pd.concat(all_trade_ledgers, ignore_index=True)
        master_df.to_parquet("walkforward_2023_macrogated_ledger.parquet")
        print("[INFO] Saved to 'walkforward_2023_macrogated_ledger.parquet'")

if __name__ == '__main__':
    main()
