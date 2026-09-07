#!/usr/bin/env python3
"""Quick diagnostic: Check what's slow"""

import sys
sys.path.insert(0, '.')

import os
os.environ['OMP_NUM_THREADS'] = '1'

import time
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest

print('DIAGNOSTIC: What is slow?\n')

print('[1] Loading manifest...')
t0 = time.time()
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
print(f'    ✓ {time.time() - t0:.1f}s\n')

print('[2] Creating loader...')
t0 = time.time()
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
print(f'    ✓ {time.time() - t0:.1f}s\n')

print('[3] Loading INFY CSV...')
t0 = time.time()
df = loader._load_symbol_csv('INFY')
print(f'    ✓ {time.time() - t0:.1f}s ({len(df)} rows)\n')

print('[4] Slicing to 5000 bars...')
t0 = time.time()
df_sliced = df.iloc[:5000]
print(f'    ✓ {time.time() - t0:.1f}s\n')

print('[5] Creating orchestrator...')
t0 = time.time()
from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

registry = CanonicalParameterRegistry()
orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
print(f'    ✓ {time.time() - t0:.1f}s\n')

print('[6] Running backtest on 100 bars only...')
t0 = time.time()
symbol_bars = {'INFY': df_sliced.iloc[:100]}
report = orch.run(symbol_bars, warmup=20)
elapsed = time.time() - t0
print(f'    ✓ {elapsed:.1f}s\n')

trades = len(report.get('trades', []))
pnl = report.get('net_pnl', 0)
print(f'    Trades: {trades}')
print(f'    P&L: ₹{pnl:,.2f}')
print(f'    Speed: {100/elapsed:.0f} bars/sec\n')

print('ESTIMATE for 5000 bars:')
estimated_time = (5000 / 100) * elapsed
print(f'    ~{estimated_time:.1f} seconds ({estimated_time/60:.1f} minutes)\n')
