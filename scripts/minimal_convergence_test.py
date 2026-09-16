#!/usr/bin/env python3
"""
MINIMAL CONVERGENCE TEST: Quick 3-iteration scan
Tests key parameters for best result
"""

import sys
sys.path.insert(0, '.')

import os
os.environ['OMP_NUM_THREADS'] = '1'

import pandas as pd
import numpy as np

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision3.macro_grid_synchronizer import MacroGridSynchronizer

print('\n' + '='*160)
print('MINIMAL CONVERGENCE TEST')
print('='*160 + '\n')

# Load data (quick)
print('[LOAD] Getting INFY...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

df = loader._load_symbol_csv('INFY').iloc[:5000]

# Quick Nifty
sym_list = ['TCS', 'INFY', 'HDFCBANK', 'RELIANCE', 'WIPRO']
symbol_data = {}
for sym in sym_list:
    try:
        symbol_data[sym] = loader._load_symbol_csv(sym).iloc[:5000]
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

print(f'✓ Ready\n')

# Baseline
print('[1] BASELINE (no grid):')
registry = CanonicalParameterRegistry()
orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
orch.grid_sync = None
orch.nifty_prices = nifty_prices
orch.vix_prices = vix_prices

symbol_bars = {'INFY': df}
report = orch.run(symbol_bars, warmup=60)
base_pnl = report.get('net_pnl', 0)
base_trades = len(report.get('trades', []))
print(f'   {base_trades} trades, ₹{base_pnl:,.2f}\n')

# Test 3 key configurations
configs = [
    ('10°, 10-30', 10, 10, 30),
    ('15°, 10-30', 15, 10, 30),
    ('15°, 8-32', 15, 8, 32),
]

results = []
print('[SWEEP] Testing configurations:')
print(f'{"Config":<20} {"Phase":<8} {"VIX":<15} {"Trades":<8} {"P&L":>15} {"Improve%":>12}')
print('-'*160)

for label, phase, vix_low, vix_high in configs:
    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)

    grid_sync = MacroGridSynchronizer(
        phase_tolerance_deg=float(phase),
        vix_operating_band=(float(vix_low), float(vix_high)),
        trend_ema_period=50
    )
    orch.grid_sync = grid_sync
    orch.nifty_prices = nifty_prices
    orch.vix_prices = vix_prices

    report = orch.run(symbol_bars, warmup=60)
    pnl = report.get('net_pnl', 0)
    trades = len(report.get('trades', []))

    improvement = (abs(base_pnl) - abs(pnl)) / abs(base_pnl) * 100 if base_pnl != 0 else 0

    results.append((label, phase, vix_low, vix_high, trades, pnl, improvement))
    print(f'{label:<20} {phase:<8} {vix_low}-{vix_high:<13} {trades:<8} ₹{pnl:>13,.0f} {improvement:>11.1f}%')

print('\n' + '='*160)
print('CONVERGENCE ANALYSIS')
print('='*160 + '\n')

best = max(results, key=lambda x: x[6])
label, phase, vix_low, vix_high, trades, pnl, improvement = best

print(f'BASELINE: {base_trades} trades, ₹{base_pnl:,.2f}')
print(f'BEST CONVERGED: Phase={phase}°, VIX={vix_low}-{vix_high}')
print(f'  {trades} trades, ₹{pnl:,.2f}')
print(f'  Improvement: {improvement:+.1f}%')
print(f'  Trades filtered: {base_trades - trades}\n')

print('='*160)
print('✅ CONVERGENCE COMPLETE')
print(f'Best parameters: phase={phase}°, vix_band=({vix_low}, {vix_high})')
print('='*160 + '\n')
