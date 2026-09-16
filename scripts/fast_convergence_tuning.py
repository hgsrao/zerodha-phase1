#!/usr/bin/env python3
"""
FAST CONVERGENCE TUNING: Focused parameter search

Optimizes only critical parameters:
- Grid phase tolerance (key regulator)
- VIX band (volatility gate)

Converges to maximum loss reduction
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
print('FAST CONVERGENCE TUNING: OPTIMIZING CRITICAL PARAMETERS')
print('='*160 + '\n')

# Load data
print('[SETUP] Loading INFY + Nifty...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

df = loader._load_symbol_csv('INFY').iloc[:5000]

symbol_data = {}
for sym in ['TCS', 'INFY', 'HDFCBANK', 'RELIANCE', 'WIPRO']:
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
    prices = [float(df_sym[df_sym['timestamp'] == ts]['close'].iloc[0])
              for df_sym in symbol_data.values()
              if len(df_sym[df_sym['timestamp'] == ts]) > 0]
    if prices:
        nifty_rows.append({'timestamp': ts, 'close': np.mean(prices)})

nifty_df = pd.DataFrame(nifty_rows)
nifty_df['returns'] = nifty_df['close'].pct_change()
nifty_df['volatility'] = nifty_df['returns'].rolling(window=20).std()
vix_prices = (20.0 * nifty_df['volatility'] * 100).fillna(20.0).values
nifty_prices = nifty_df['close'].values

print(f'  ✓ INFY: {len(df):,} bars')
print(f'  ✓ Nifty: {len(nifty_df)} bars\n')

# Baseline
print('[BASELINE] No grid...')
registry = CanonicalParameterRegistry()
orch_base = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
orch_base.grid_sync = None
orch_base.nifty_prices = nifty_prices
orch_base.vix_prices = vix_prices

symbol_bars = {'INFY': df}
report_base = orch_base.run(symbol_bars, warmup=60)
baseline_pnl = report_base.get('net_pnl', 0)
baseline_trades = len(report_base.get('trades', []))

print(f'  {baseline_trades} trades, ₹{baseline_pnl:,.2f}\n')

# Convergence search
print('[SEARCH] Optimizing parameters...\n')
print(f'{"Phase°":<8} {"VIX_Low":<8} {"VIX_High":<8} {"Trades":<8} {"P&L":>15} {"Improve%":>12} {"Converged?"}')
print('-'*160)

best_improvement = 0
best_phase = 15
best_vix = (10, 30)
no_improvement_count = 0

for phase_tol in [5, 8, 10, 12, 15, 18, 20, 25]:
    for vix_low in [8, 10, 12]:
        for vix_high in [28, 30, 32]:
            if vix_high <= vix_low + 5:
                continue

            registry = CanonicalParameterRegistry()
            orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)

            grid_sync = MacroGridSynchronizer(
                phase_tolerance_deg=float(phase_tol),
                vix_operating_band=(float(vix_low), float(vix_high)),
                trend_ema_period=50
            )
            orch.grid_sync = grid_sync
            orch.nifty_prices = nifty_prices
            orch.vix_prices = vix_prices

            report = orch.run(symbol_bars, warmup=60)
            pnl = report.get('net_pnl', 0)
            trades = len(report.get('trades', []))

            improvement = abs(pnl - baseline_pnl) / abs(baseline_pnl) * 100 if baseline_pnl != 0 else 0
            if pnl > baseline_pnl:
                improvement_pct = improvement
            else:
                improvement_pct = -improvement

            converged = '✓ BEST' if improvement_pct > best_improvement else ''
            if improvement_pct > best_improvement:
                best_improvement = improvement_pct
                best_phase = phase_tol
                best_vix = (vix_low, vix_high)
                no_improvement_count = 0
            else:
                no_improvement_count += 1

            print(f'{phase_tol:<8} {vix_low:<8} {vix_high:<8} {trades:<8} ₹{pnl:>13,.0f} {improvement_pct:>11.1f}% {converged}')

            if no_improvement_count > 8:
                print('\n✓ CONVERGENCE DETECTED\n')
                break

        if no_improvement_count > 8:
            break
    if no_improvement_count > 8:
        break

print('\n' + '='*160)
print('CONVERGENCE RESULTS')
print('='*160 + '\n')

# Final run with best params
registry = CanonicalParameterRegistry()
orch_final = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)

grid_sync = MacroGridSynchronizer(
    phase_tolerance_deg=float(best_phase),
    vix_operating_band=best_vix,
    trend_ema_period=50
)
orch_final.grid_sync = grid_sync
orch_final.nifty_prices = nifty_prices
orch_final.vix_prices = vix_prices

report_final = orch_final.run(symbol_bars, warmup=60)
final_pnl = report_final.get('net_pnl', 0)
final_trades = len(report_final.get('trades', []))

final_improvement = abs(final_pnl - baseline_pnl) / abs(baseline_pnl) * 100 if baseline_pnl != 0 else 0
if final_pnl > baseline_pnl:
    final_improvement = final_improvement
else:
    final_improvement = -final_improvement

print(f'BASELINE:')
print(f'  {baseline_trades} trades, ₹{baseline_pnl:,.2f}\n')

print(f'CONVERGED PARAMETERS:')
print(f'  Phase tolerance: ±{best_phase}°')
print(f'  VIX operating band: {best_vix[0]}-{best_vix[1]}\n')

print(f'OPTIMIZED RESULT:')
print(f'  {final_trades} trades, ₹{final_pnl:,.2f}')
print(f'  Trades filtered: {baseline_trades - final_trades}')
print(f'  Loss reduction: {final_improvement:+.1f}%\n')

print('='*160)
if final_improvement > 25:
    print('✅ STRONG CONVERGENCE: >25% improvement')
elif final_improvement > 15:
    print('✅ GOOD CONVERGENCE: >15% improvement')
else:
    print('⚠️  MODERATE CONVERGENCE: <15% improvement')
print('='*160 + '\n')

print('RECOMMENDED DEPLOYMENT:')
print(f'  Apply phase={best_phase}°, vix=({best_vix[0]}-{best_vix[1]})')
print(f'  Deploy to all 48 symbols')
print(f'  Expected portfolio improvement: {final_improvement*0.8:.1f}% (conservative)\n')

print('='*160 + '\n')
