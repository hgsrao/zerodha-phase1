#!/usr/bin/env python3
"""Quick validation of 3-symbol calibration backtest"""

import sys
sys.path.insert(0, '.')

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision4_production.calibration_backtest import CalibrationBacktest

print('\n[LOAD] Loading 3-symbol dataset...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

symbols = [f.symbol for f in manifest.files[:3]]
data = {}

for symbol in symbols:
    df = loader._load_symbol_csv(symbol).tail(500)  # Last 500 bars per symbol
    data[symbol] = df
    print(f'  {symbol}: {len(df)} bars')

print(f'\nLoaded {len(data)} symbols')

# Get common timestamps
print('\n[SYNC] Finding synchronized timestamps...')
all_ts = set()
for df in data.values():
    all_ts.update(df['timestamp'].values)

all_ts = sorted(all_ts)
print(f'  Found {len(all_ts)} timestamps')

# Create backtest
print('\n[BACKTEST] Running calibration...')
bt = CalibrationBacktest(list(data.keys()), starting_cash=100_000.0)

for bar_num, ts in enumerate(all_ts[:100]):  # Process 100 bars
    bar_data = {}
    for sym in data.keys():
        bar_rows = data[sym][data[sym]['timestamp'] == ts]
        if len(bar_rows) > 0:
            row = bar_rows.iloc[0]
            bar_data[sym] = {'open': row['open'], 'high': row['high'], 'low': row['low'], 'close': row['close'], 'volume': row['volume']}

    if bar_num % 20 == 0:
        print(f'  Bar {bar_num}: {ts}')

    bt.process_bar(ts, bar_data)

print(f'\n[RESULTS]')
report = bt.get_report()
print(f'  Trades: {report["total_trades"]}')
print(f'  P&L: ₹{report["total_pnl"]:,.2f}')
print(f'  Equity: ₹{report["ending_equity"]:,.2f}')

print('\n✓ Calibration backtest operational\n')
