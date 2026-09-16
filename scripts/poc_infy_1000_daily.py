#!/usr/bin/env python3
"""
PROOF OF CONCEPT: INFY ₹1,000/day intraday target

Simple, no-frills backtest:
- Entry: PA confidence > 0.6
- Exit: Time-based (60min hold) or EOD
- Stop: ATR × 1.5
- Target: ATR × 3.0

Goal: Prove ₹500/day average (scale to 48 = ₹24k/day)
"""

import sys
sys.path.insert(0, '.')

import os
os.environ['OMP_NUM_THREADS'] = '1'

import pandas as pd
import numpy as np
from datetime import datetime
from collections import defaultdict

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

print('\n' + '='*160)
print('POC: INFY ₹1,000/day Intraday Target')
print('='*160 + '\n')

# Load data
print('[LOAD] INFY data...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

df = loader._load_symbol_csv('INFY').iloc[:5000]
print(f'  ✓ {len(df):,} bars loaded\n')

# Create synthetic Nifty
print('[SETUP] Creating market context...')
symbol_data = {}
for sym in ['TCS', 'INFY', 'HDFCBANK', 'RELIANCE', 'WIPRO']:
    try:
        sdf = loader._load_symbol_csv(sym)
        symbol_data[sym] = sdf.iloc[:5000]
    except:
        pass

all_ts = set()
for df_sym in symbol_data.values():
    all_ts.update(df_sym['timestamp'].unique())
all_ts = sorted(list(all_ts))

nifty_rows = []
for ts in all_ts:
    prices = []
    for df_sym in symbol_data.values():
        row = df_sym[df_sym['timestamp'] == ts]
        if len(row) > 0:
            prices.append(float(row['close'].iloc[0]))
    if len(prices) > 0:
        nifty_rows.append({'timestamp': ts, 'close': np.mean(prices)})

nifty_df = pd.DataFrame(nifty_rows)
nifty_df['returns'] = nifty_df['close'].pct_change()
nifty_df['volatility'] = nifty_df['returns'].rolling(window=20).std()
vix_prices = (20.0 * nifty_df['volatility'] * 100).fillna(20.0).values
nifty_prices = nifty_df['close'].values

print(f'  ✓ Nifty: {len(nifty_df)} bars\n')

# Run backtest
print('[BACKTEST] Running simple INFY strategy...')
print('-'*160)

registry = CanonicalParameterRegistry()
orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=100_000.0)
orch.nifty_prices = nifty_prices
orch.vix_prices = vix_prices

symbol_bars = {'INFY': df}
report = orch.run(symbol_bars, warmup=60)

trades = report.get('trades', [])
pnl = report.get('net_pnl', 0)
wr = report.get('win_rate', 0)

print(f'\nResults:')
print(f'  Trades: {len(trades)}')
print(f'  P&L: ₹{pnl:,.2f}')
print(f'  Win rate: {wr*100:.1f}%\n')

# Daily breakdown
print('[DAILY BREAKDOWN]')
print('-'*160)

if trades:
    # Group trades by date
    daily_pnl = defaultdict(float)
    for trade in trades:
        try:
            entry_time = trade.get('entry_timestamp', '')
            if entry_time:
                date = str(entry_time).split()[0]
                trade_pnl = trade.get('pnl', 0)
                daily_pnl[date] += trade_pnl
        except:
            pass

    total_days = len(daily_pnl)
    daily_values = sorted(daily_pnl.values(), reverse=True)

    if daily_values:
        print(f'Trading days: {total_days}')
        print(f'Average daily P&L: ₹{np.mean(daily_values):,.2f}')
        print(f'Best day: ₹{max(daily_values):,.2f}')
        print(f'Worst day: ₹{min(daily_values):,.2f}')
        print(f'Profitable days: {len([x for x in daily_values if x > 0])}/{total_days}\n')

        print('Daily P&L (top 10):')
        for i, (date, pnl_val) in enumerate(sorted(daily_pnl.items(), key=lambda x: x[1], reverse=True)[:10], 1):
            print(f'  {i:2d}. {date}: ₹{pnl_val:>10,.2f}')

print('\n' + '='*160)
print('TARGET ANALYSIS')
print('='*160 + '\n')

avg_daily = np.mean(daily_values) if daily_values else 0
target_daily = 1000
scale_factor = 48

print(f'INFY Average Daily P&L: ₹{avg_daily:,.2f}')
print(f'Target per symbol: ₹{target_daily:,}/day')
print(f'Symbols in portfolio: {scale_factor}')
print(f'Scaled portfolio target: ₹{target_daily * scale_factor:,}/day\n')

if avg_daily >= target_daily / 2:
    print(f'✓ SUCCESS: INFY averaging ₹{avg_daily:,.2f}/day')
    print(f'  At 48 symbols: ₹{avg_daily * scale_factor:,.2f}/day')
    print(f'  Status: READY TO SCALE\n')
elif avg_daily > 0:
    print(f'⚠ PARTIAL: INFY averaging ₹{avg_daily:,.2f}/day')
    print(f'  Need optimization to hit ₹{target_daily:,}/day')
    print(f'  At 48 symbols: ₹{avg_daily * scale_factor:,.2f}/day\n')
else:
    print(f'✗ LOSS: INFY averaging ₹{avg_daily:,.2f}/day')
    print(f'  Strategy needs revision\n')

print('='*160)
print('NEXT STEP')
print('='*160 + '\n')

if avg_daily > 0:
    print(f'✓ Scale to 10 symbols (same logic)')
    print(f'  Expected: ₹{avg_daily * 10:,.2f}/day')
    print(f'  Then scale to 48: ₹{avg_daily * 48:,.2f}/day\n')
else:
    print('✗ Revise entry/exit logic')
    print('  Try: longer hold time, wider stops, different confidence threshold\n')

print('='*160 + '\n')
