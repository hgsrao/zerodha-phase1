#!/usr/bin/env python3
"""Test Revision 04 Strategy"""

import sys
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision4_production import Revision04Strategy, Revision04Backtest

print('\n' + '='*160)
print('REVISION 04: Production Intraday Strategy')
print('='*160 + '\n')

# Load INFY data
print('[LOAD] INFY data...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
df = loader._load_symbol_csv('INFY').iloc[:5000]
print(f'  ✓ {len(df):,} bars\n')

# Create strategy with optimized parameters
print('[STRATEGY] Initializing Revision 04...')
strategy = Revision04Strategy(
    min_confidence=0.75,      # HIGH confidence only (was issue in baseline)
    atr_stop_mult=1.0,        # Tight stops
    atr_target_mult=2.5,      # 2.5:1 reward/risk
    hold_minutes=60,          # 1-hour max hold
    trading_start_hour=9,
    trading_start_minute=15,
    trading_end_hour=14,
    trading_end_minute=0,     # Close by 2 PM
)
print(f'  ✓ Strategy ready\n')

# Run backtest
print('[BACKTEST] Running Revision 04 on INFY...')
print('-'*160)

backtest = Revision04Backtest(strategy, starting_equity=100_000.0)
report = backtest.run('INFY', df)

print(f'\nResults:')
print(f'  Total trades: {report["total_trades"]}')
print(f'  Winning trades: {report["winning_trades"]}')
print(f'  Losing trades: {report["losing_trades"]}')
print(f'  Win rate: {report["win_rate"]*100:.1f}%')
print(f'  Total P&L: ₹{report["total_pnl"]:,.2f}')
print(f'  Average P&L/trade: ₹{report["avg_pnl"]:,.2f}')
print(f'  Best trade: ₹{report["best_trade"]:,.2f}')
print(f'  Worst trade: ₹{report["worst_trade"]:,.2f}')
print(f'  Equity: ₹{report["equity"]:,.2f}\n')

# Daily breakdown
print('[DAILY BREAKDOWN]')
print('-'*160)

if report["trades"]:
    daily_pnl = {}
    for trade in report["trades"]:
        date = trade["entry_time"].split()[0]
        if date not in daily_pnl:
            daily_pnl[date] = 0
        daily_pnl[date] += trade["pnl"]

    trading_days = len(daily_pnl)
    daily_values = list(daily_pnl.values())

    print(f'Trading days: {trading_days}')
    print(f'Average daily P&L: ₹{np.mean(daily_values):,.2f}')
    print(f'Best day: ₹{max(daily_values):,.2f}')
    print(f'Worst day: ₹{min(daily_values):,.2f}')
    print(f'Profitable days: {len([x for x in daily_values if x > 0])}/{trading_days}\n')

    print('Daily P&L (all days):')
    for date in sorted(daily_pnl.keys()):
        pnl = daily_pnl[date]
        status = '✓' if pnl > 0 else '✗'
        print(f'  {status} {date}: ₹{pnl:>10,.2f}')

print('\n' + '='*160)
print('TARGET ANALYSIS')
print('='*160 + '\n')

avg_daily = np.mean(daily_values) if report["trades"] else 0
target_daily = 1000
scale_factor = 48

print(f'INFY Average Daily P&L: ₹{avg_daily:,.2f}')
print(f'Target per symbol: ₹{target_daily:,}/day')
print(f'Symbols in portfolio: {scale_factor}')
print(f'Scaled to 48 symbols: ₹{avg_daily * scale_factor:,.2f}/day\n')

if avg_daily >= 500:
    print(f'✓ SUCCESS: INFY averaging ₹{avg_daily:,.2f}/day')
    print(f'  Scale to 48 symbols: ₹{avg_daily * scale_factor:,.2f}/day')
    print(f'  Status: READY TO SCALE TO 10, THEN 48\n')
elif avg_daily > 0:
    print(f'⚠ PARTIAL: INFY averaging ₹{avg_daily:,.2f}/day')
    print(f'  Close to target, minor tuning may help\n')
else:
    print(f'✗ LOSS: INFY averaging ₹{avg_daily:,.2f}/day')
    print(f'  Strategy revision needed\n')

print('='*160 + '\n')
