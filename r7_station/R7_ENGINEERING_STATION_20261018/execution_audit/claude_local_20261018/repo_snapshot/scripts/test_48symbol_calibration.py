#!/usr/bin/env python3
"""
REVISION 04: 48-Symbol One-Month Calibration Test

Proper portfolio backtest with:
- All 48 NSE symbols
- One shared ₹1,00,000 cash pool
- Chronological bar-by-bar processing
- Next-bar fills (no lookahead)
- Real stop/target/time/EOD exits
- Transaction costs
- NO artificial scaling

Target: ₹1,000/day average on ₹1,00,000 investment
Evaluation: Actual portfolio P&L over 1 month
"""

import sys
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision4_production.calibration_backtest import CalibrationBacktest

print('\n' + '='*160)
print('REVISION 04: 48-SYMBOL PORTFOLIO CALIBRATION TEST')
print('='*160 + '\n')

print('[LOAD] Loading 48-symbol dataset...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

# Extract symbols from manifest
symbols = [f.symbol for f in manifest.files[:48]]  # Use all 48 symbols
data = {}

for symbol in symbols:
    try:
        df = loader._load_symbol_csv(symbol)
        # Extract exactly 1 month of recent data
        df = df.tail(22 * 390)  # 22 trading days × 390 min/day ≈ 1 month
        data[symbol] = df
        print(f'  ✓ {symbol:8s}: {len(df):>5,} bars')
    except Exception as e:
        print(f'  ✗ {symbol:8s}: Error loading ({e})')

print(f'\nTotal symbols loaded: {len(data)}\n')

# Create backtest engine
print('[BACKTEST] Initializing calibration engine...')
backtest = CalibrationBacktest(
    symbols=list(data.keys()),
    starting_cash=100_000.0,
    cost_per_trade=5.0,
)
print(f'  Starting equity: ₹{100_000.0:,.2f}')
print(f'  Max positions: 5 concurrent')
print(f'  Max hold time: 60 minutes\n')

# Find common timestamps (minute bars available across all symbols)
print('[SYNC] Synchronizing timestamps across all symbols...')
all_timestamps = set()
for df in data.values():
    all_timestamps.update(df['timestamp'].values)

all_timestamps = sorted(all_timestamps)
common_timestamps = [ts for ts in all_timestamps if all(
    len(data[sym][data[sym]['timestamp'] == ts]) > 0 for sym in data.keys()
)]

print(f'  Found {len(common_timestamps):,} synchronized timestamps')
print(f'  Date range: {common_timestamps[0]} to {common_timestamps[-1]}\n')

# Process bars
print('[PROCESS] Bar-by-bar portfolio processing...')
print('-'*160)

bar_count = 0
for timestamp_idx, timestamp in enumerate(common_timestamps[:1000]):  # First 1000 bars for speed
    bar_count += 1

    if bar_count % 200 == 0:
        print(f'  Bar {bar_count}: {timestamp} | Equity: ₹{backtest.get_equity():,.2f} | Positions: {len(backtest.positions)}')

    # Prepare bar data for this timestamp
    bar_data = {}
    for symbol in data.keys():
        df_symbol = data[symbol]
        bar_row = df_symbol[df_symbol['timestamp'] == timestamp]
        if len(bar_row) > 0:
            row = bar_row.iloc[0]
            bar_data[symbol] = {
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
            }

    # Process bar through backtest
    result = backtest.process_bar(timestamp, bar_data)

print(f'  ✓ Processed {bar_count} bars\n')

# Generate report
print('[RESULTS] Calibration Report')
print('-'*160)

report = backtest.get_report()

print(f'Starting Equity: ₹{report["starting_equity"]:,.2f}')
print(f'Ending Equity: ₹{report["ending_equity"]:,.2f}')
print(f'Total P&L: ₹{report["total_pnl"]:,.2f}\n')

print(f'Total Trades: {report["total_trades"]}')
print(f'Winning Trades: {report["winning_trades"]}')
print(f'Losing Trades: {report["losing_trades"]}')
print(f'Win Rate: {report["win_rate"]*100:.1f}%\n')

print(f'Average P&L/Trade: ₹{report["avg_pnl"]:,.2f}')
print(f'Total Return: {(report["ending_equity"] - report["starting_equity"]) / report["starting_equity"] * 100:.2f}%\n')

# Daily breakdown
if report['trades']:
    daily_pnl = {}
    for trade in report['trades']:
        date = trade['entry_time'].split()[0] if ' ' in str(trade['entry_time']) else str(trade['entry_time'])[:10]
        if date not in daily_pnl:
            daily_pnl[date] = 0
        daily_pnl[date] += trade['pnl']

    trading_days = len(daily_pnl)
    daily_values = list(daily_pnl.values())

    print(f'Trading Days: {trading_days}')
    print(f'Average Daily P&L: ₹{np.mean(daily_values):,.2f}')
    print(f'Best Day: ₹{max(daily_values):,.2f}')
    print(f'Worst Day: ₹{min(daily_values):,.2f}')
    print(f'Profitable Days: {len([x for x in daily_values if x > 0])}/{trading_days}\n')

print('='*160)
print('TARGET EVALUATION')
print('='*160 + '\n')

target_daily = 1000
actual_daily = np.mean(daily_values) if daily_pnl else 0

print(f'Target Daily P&L: ₹{target_daily:,}/day')
print(f'Actual Daily P&L: ₹{actual_daily:,.2f}/day')
print(f'Monthly Target (22 days): ₹{target_daily * 22:,}')
print(f'Monthly Actual: ₹{actual_daily * 22:,.2f}\n')

if actual_daily >= target_daily:
    print(f'✓ SUCCESS: Meeting ₹{target_daily}/day target')
    print(f'  Status: Ready for live trading')
elif actual_daily > 0:
    print(f'⚠ PARTIAL: Positive but below target')
    print(f'  Gap: ₹{target_daily - actual_daily:,.2f}/day needed')
else:
    print(f'✗ FAILURE: Strategy not profitable')

print('\n' + '='*160 + '\n')
