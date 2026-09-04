#!/usr/bin/env python3
"""Run corrected backtest on all 48 symbols"""
from backtest_corrected import BacktestCorrected
import json

symbols_48 = [
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK',
    'BAJAJ-AUTO', 'BAJAJFINSV', 'BAJFINANCE', 'BEL', 'BHARTIARTL',
    'CIPLA', 'COALINDIA', 'DRREDDY', 'EICHERMOT', 'ETERNAL',
    'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE', 'HINDALCO',
    'HINDUNILVR', 'ICICIBANK', 'INDIGO', 'INFY', 'ITC',
    'JIOFIN', 'JSWSTEEL', 'KOTAKBANK', 'LT', 'M&M',
    'MARUTI', 'MAXHEALTH', 'NTPC', 'ONGC', 'POWERGRID',
    'RELIANCE', 'SBILIFE', 'SBIN', 'SHRIRAMFIN', 'SUNPHARMA',
    'TATACONSUM', 'TATASTEEL', 'TCS', 'TECHM', 'TITAN',
    'TRENT', 'ULTRACEMCO', 'WIPRO'
]

print("\n" + "="*90)
print("PHASE 2 CORRECTED - 48 SYMBOL FULL BACKTEST")
print("="*90)

engine = BacktestCorrected(initial_capital=1000000)
results = engine.run(symbols_48)

# Calculate max drawdown
equity_curve = results['equity_curve']
max_dd = 0
peak = equity_curve[0]
for val in equity_curve:
    if val > peak:
        peak = val
    dd = (peak - val) / peak * 100 if peak > 0 else 0
    max_dd = max(max_dd, dd)

# Save results
with open("PHASE2_CORRECTED_48SYMBOL_RESULTS.json", "w") as f:
    json.dump({
        'test': '48-symbol corrected backtest',
        'data_source': 'frozen NSE 15-minute data (2023-08-14 to 2026-08-14)',
        'symbols': symbols_48,
        'initial_capital': results['initial_capital'],
        'final_equity': results['final_equity'],
        'total_pnl': results['total_pnl'],
        'return_percent': (results['total_pnl'] / results['initial_capital']) * 100,
        'total_trades': results['total_trades'],
        'winning_trades': sum(1 for t in results['trades'] if t['realized_pnl'] > 0),
        'losing_trades': sum(1 for t in results['trades'] if t['realized_pnl'] <= 0),
        'win_rate': results['win_rate'] * 100,
        'profit_factor': (
            sum(t['realized_pnl'] for t in results['trades'] if t['realized_pnl'] > 0) /
            abs(sum(t['realized_pnl'] for t in results['trades'] if t['realized_pnl'] < 0))
            if sum(t['realized_pnl'] for t in results['trades'] if t['realized_pnl'] < 0) != 0
            else 0
        ),
        'max_drawdown': max_dd,
        'execution_type': 'CAUSAL (next-bar entry)',
        'costs_included': True,
        'gate_evaluation': 'YES - 18 gates active',
        'status': 'CORRECTED - REAL DATA'
    }, f, indent=2)

print("\n✅ 48-symbol backtest complete")
print("   Results saved to PHASE2_CORRECTED_48SYMBOL_RESULTS.json\n")
