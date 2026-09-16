"""
2023 Batch Walk-Forward Runner (Terminal Edition)
------------------------------------------------
Iterates over available months in 2023, executes ExecutionEngine from
alpha_engine_core, aggregates trades, computes monthly statistics,
and generates terminal-scannable equity curves and audit summaries.
"""

import calendar
import sys
from pathlib import Path
import pandas as pd

from alpha_engine_core import EngineConfig, ExecutionEngine

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

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
    print("2023 MONTHLY NET P&L DISTRIBUTION (IN-TERMINAL BAR CHART)")
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
        print(f"[ERROR] Data directory does not exist: {DATA_DIR}", file=sys.stderr)
        sys.exit(1)

    config = EngineConfig()
    windows = get_2023_month_windows()
    
    monthly_records = []
    all_trade_ledgers = []

    print("=" * 80)
    print("STARTING 2023 MULTI-MONTH WALK-FORWARD SWEEP")
    print("=" * 80)

    for label, start_d, end_d in windows:
        print(f"\n[SWEEP] Processing {label} | Range: {start_d} to {end_d}...")
        engine = ExecutionEngine(config, DATA_DIR)
        
        try:
            engine.load_dataset(start_d, end_d)
            if not engine.symbol_frames:
                print(f"  [INFO] No trading data available for {label}. Skipping.")
                continue
                
            trades_df = engine.run_replay()
            
            if trades_df.empty:
                monthly_records.append({
                    'Month': label,
                    'Trades': 0,
                    'Win_Rate': 0.0,
                    'Gross_PNL': 0.0,
                    'Friction': 0.0,
                    'Net_PNL': 0.0,
                    'Net_R': 0.0
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
                'Month': label,
                'Trades': total_trades,
                'Win_Rate': wr,
                'Gross_PNL': gross,
                'Friction': fees,
                'Net_PNL': net,
                'Net_R': avg_r
            })

            print(f"  Trades: {total_trades} | WR: {wr:.1f}% | Gross: ₹{gross:+,.2f} | Fees: ₹{fees:,.2f} | Net: ₹{net:+,.2f}")

        except Exception as e:
            print(f"  [ERROR] Encountered exception in {label}: {e}", file=sys.stderr)

    if not monthly_records:
        print("[ERROR] No simulation data generated.")
        return

    summary_df = pd.DataFrame(monthly_records)

    print("\n" + "=" * 80)
    print("2023 WALK-FORWARD PERFORMANCE SUMMARY TABLE")
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

    # Render ASCII Distribution Chart
    print_terminal_pnl_chart(summary_df)

    # Save Complete Master Ledger
    if all_trade_ledgers:
        master_df = pd.concat(all_trade_ledgers, ignore_index=True)
        master_df.to_parquet("walkforward_2023_master_ledger.parquet")
        print("[INFO] Master ledger written to 'walkforward_2023_master_ledger.parquet'")

if __name__ == '__main__':
    main()
