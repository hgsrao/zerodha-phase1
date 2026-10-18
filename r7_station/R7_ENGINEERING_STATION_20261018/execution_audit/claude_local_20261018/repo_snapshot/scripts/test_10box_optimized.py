#!/usr/bin/env python3
"""
REVISION 04: Optimized 10-Box System Test

Uses subset of data for fast validation
"""

import sys
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision4_production import Revision04Orchestrator

print('\n' + '='*160)
print('REVISION 04: 10-BOX SYSTEM (OPTIMIZED TEST)')
print('='*160 + '\n')

# Load data
print('[LOAD] INFY data...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
df = loader._load_symbol_csv('INFY').iloc[:5000]  # Use only 5000 bars
print(f'  ✓ {len(df):,} bars loaded\n')

# Create orchestrator
print('[INIT] Orchestrator initialized')
orchestrator = Revision04Orchestrator(equity=100_000.0)

# Process bars
print('[PROCESS] Running bars through system...')
closes_array = df['close'].values
volumes_array = df['volume'].values
nifty_trend = 0.5
vix = 15.0

for idx in range(60, len(df)):
    row = df.iloc[idx]

    orchestrator.process_bar(
        symbol='INFY',
        timestamp=row['timestamp'],
        open_price=row['open'],
        high=row['high'],
        low=row['low'],
        close=row['close'],
        volume=row['volume'],
        closes_history=closes_array[:idx+1],
        volumes_history=volumes_array[:idx+1],
        nifty_trend=nifty_trend,
        vix=vix,
    )

print(f'  ✓ Processed {len(df)-60:,} bars\n')

# Summary
summary = orchestrator.get_summary()

print('[RESULTS]')
print('-'*160)
print(f'Starting Equity: ₹{summary["starting_equity"]:,.2f}')
print(f'Ending Equity: ₹{summary["current_equity"]:,.2f}')
print(f'Total P&L: ₹{summary["total_pnl"]:,.2f}')
print(f'Total Trades: {summary["total_trades"]}')
print(f'Win Rate: {summary["win_rate"]*100:.1f}%\n')

if summary['daily_breakdown']:
    daily_values = list(summary['daily_breakdown'].values())
    avg_daily = np.mean(daily_values)
    print(f'Average Daily P&L: ₹{avg_daily:,.2f}')
    print(f'Best Day: ₹{max(daily_values):,.2f}')
    print(f'Worst Day: ₹{min(daily_values):,.2f}\n')

    # Projection to 48 symbols
    print(f'Projection to 48 symbols: ₹{avg_daily * 48:,.2f}/day')
else:
    print('No trades generated\n')

print('='*160 + '\n')
