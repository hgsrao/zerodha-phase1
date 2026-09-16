"""
Symbol-by-Symbol Attribution & Trade Distribution Analyzer
(Causal 1-Minute Simulation @ 2.0 bps Slippage)
"""

import pandas as pd
import numpy as np
from causality_certified_engine import run_causal_replay

def main():
    print("[1/2] Running single causal pass at 2.0 bps/leg to capture full trade ledger...")
    tdf = run_causal_replay(slippage_bps_per_leg=2.0)
    
    if tdf.empty:
        print("[ERROR] No trades captured.")
        return

    tdf['is_win'] = tdf['net_pnl'] > 0
    tdf['is_gross_win'] = tdf['gross_pnl'] > 0

    print("\n" + "=" * 95)
    print("SYMBOL-BY-SYMBOL ATTRIBUTION TABLE (253 TRADES @ 2.0 BPS SLIPPAGE)")
    print("=" * 95)
    
    summary = []
    for sym, g in tdf.groupby('symbol'):
        n = len(g)
        wins = g['is_win'].sum()
        gross_wins = g['is_gross_win'].sum()
        net_wr = (wins / n) * 100.0
        gross_wr = (gross_wins / n) * 100.0
        gross_pnl = g['gross_pnl'].sum()
        fees = g['friction'].sum()
        net_pnl = g['net_pnl'].sum()
        avg_trade = net_pnl / n
        
        # Breakdown exit reasons
        exits = g['reason'].value_counts().to_dict()
        targets = exits.get('DYNAMIC_R_TARGET_1M_HIT', 0)
        stops = exits.get('STOP_LOSS_1M_HIT', 0)
        z_exits = exits.get('DYNAMIC_Z_15M_CLOSE_EXIT', 0)
        sq_offs = exits.get('MANDATORY_1515_SQUAREOFF', 0)

        summary.append({
            'Symbol': sym,
            'Trades': n,
            'Net WR%': round(net_wr, 1),
            'Gross WR%': round(gross_wr, 1),
            'Gross P&L': round(gross_pnl, 2),
            'Fees Paid': round(fees, 2),
            'Net P&L': round(net_pnl, 2),
            'Net/Trade': round(avg_trade, 2),
            'Targets': targets,
            'Stops': stops,
            'Z-Exits': z_exits,
            '15:15 Closes': sq_offs
        })

    sdf = pd.DataFrame(summary).sort_values('Net P&L', ascending=False)
    
    fmt = "{:<12} | {:>6} | {:>7} | {:>8} | {:>11} | {:>10} | {:>11} | {:>9} | {:>4} | {:>4} | {:>4} | {:>4}"
    header = fmt.format("Symbol", "Trades", "Net WR%", "Gross WR%", "Gross P&L", "Fees Paid", "Net P&L", "Net/Trade", "Tgt", "Stp", "Z-Ex", "1515")
    print(header)
    print("-" * len(header))
    
    for _, r in sdf.iterrows():
        print(fmt.format(
            r['Symbol'], r['Trades'], f"{r['Net WR%']}%", f"{r['Gross WR%']}%",
            f"₹{r['Gross P&L']:+,.2f}", f"₹{r['Fees Paid']:,.2f}",
            f"₹{r['Net P&L']:+,.2f}", f"₹{r['Net/Trade']:+,.2f}",
            r['Targets'], r['Stops'], r['Z-Exits'], r['15:15 Closes']
        ))
    print("=" * len(header))

    # Exit Reason Diagnostics
    print("\n" + "=" * 60)
    print("AGGREGATE EXIT REASON BREAKDOWN")
    print("=" * 60)
    exit_summary = tdf.groupby('reason').agg(
        trades=('net_pnl', 'count'),
        net_pnl=('net_pnl', 'sum'),
        gross_pnl=('gross_pnl', 'sum'),
        fees=('friction', 'sum')
    )
    exit_summary['avg_net'] = exit_summary['net_pnl'] / exit_summary['trades']
    print(exit_summary.to_string())
    print("=" * 60)

if __name__ == '__main__':
    main()
