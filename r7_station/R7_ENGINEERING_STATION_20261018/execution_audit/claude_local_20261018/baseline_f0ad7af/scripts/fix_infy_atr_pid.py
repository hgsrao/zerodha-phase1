#!/usr/bin/env python3
"""
FIX: ATR Multiplier + PID Integral Gain Tuning for INFY
========================================================

ROOT CAUSE (from PID analysis):
- ATR mechanical stop (4.0x) fires on entry bar (79% of cases)
- PID controller needs 20+ bars to accumulate meaningful signal
- Result: PID never gets a chance to work

SOLUTION:
1. Increase ATR multiplier from 4.0 to 5.5 (give PID more time)
2. Increase integral gain (Ki) from 0.02 to 0.05 (accumulate faster)
3. This allows PID to take over exit control instead of mechanical stop

EXPECTED RESULT:
- Longer trade holding periods (not 0-bar exits)
- More PID-driven exits instead of ATR-forced exits
- Better win rate (trades that are held longer perform better: 33% win @ 4+ bars)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard
import os
os.environ['OMP_NUM_THREADS'] = '1'

import pandas as pd
import numpy as np

print('\n' + '='*160)
print('INFY FIX: ATR Multiplier + PID Integral Gain Tuning')
print('='*160 + '\n')

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision3.macro_grid_synchronizer import MacroGridSynchronizer
from revision3.master_control_system import MasterControlSystem

symbol = 'INFY'
max_bars = 5000

print(f'[1] Loading {symbol} data...')
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

df = loader._load_symbol_csv(symbol)
if max_bars:
    df = df.iloc[:max_bars]
print(f'  ✓ {symbol}: {len(df):,} bars\n')

print('[2] Creating synthetic Nifty...')
symbol_data = {}
for sym in ['TCS', 'INFY', 'HDFCBANK', 'RELIANCE', 'WIPRO']:
    try:
        sdf = loader._load_symbol_csv(sym)
        if max_bars:
            sdf = sdf.iloc[:max_bars]
        symbol_data[sym] = sdf
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

print(f'  ✓ Nifty: {len(nifty_df):,} bars\n')

# ═══════════════════════════════════════════════════════════════════════════
# BASELINE: Current settings (ATR=4.0, Ki=0.02)
# ═══════════════════════════════════════════════════════════════════════════

print('[3] BASELINE RUN: Current settings (ATR=4.0, Ki=0.02)')
print('-'*160)

registry_baseline = CanonicalParameterRegistry()
registry_baseline.values['trailing_stop_atr_mult'] = 4.0  # Current

orch_baseline = Revision2ExternalEngineOrchestrator(
    [symbol],
    registry_baseline,
    starting_equity=1_000_000.0
)

df_reset = loader._load_symbol_csv(symbol)
if max_bars:
    df_reset = df_reset.iloc[:max_bars]

symbol_bars = {symbol: df_reset}
report_baseline = orch_baseline.run(symbol_bars, warmup=60)

baseline_trades = report_baseline.get('trades', [])
baseline_pnl = report_baseline.get('net_pnl', 0)
baseline_wr = report_baseline.get('win_rate', 0)

print(f'Trades: {len(baseline_trades)}')
print(f'P&L: ₹{baseline_pnl:,.2f}')
print(f'Win rate: {100*baseline_wr:.1f}%')

# Analyze bars held
bars_0 = sum(1 for t in baseline_trades if t.get('bars_held', 0) == 0)
bars_1_3 = sum(1 for t in baseline_trades if 1 <= t.get('bars_held', 0) <= 3)
bars_4_plus = sum(1 for t in baseline_trades if t.get('bars_held', 0) >= 4)

print(f'\nBars held distribution:')
print(f'  0 bars (forced exits): {bars_0} trades ({100*bars_0/len(baseline_trades) if len(baseline_trades) > 0 else 0:.1f}%)')
print(f'  1-3 bars: {bars_1_3} trades ({100*bars_1_3/len(baseline_trades) if len(baseline_trades) > 0 else 0:.1f}%)')
print(f'  4+ bars: {bars_4_plus} trades ({100*bars_4_plus/len(baseline_trades) if len(baseline_trades) > 0 else 0:.1f}%)')
print()

# ═══════════════════════════════════════════════════════════════════════════
# FIXED RUN: New settings (ATR=5.5, Ki=0.05)
# ═══════════════════════════════════════════════════════════════════════════

print('[4] FIXED RUN: New settings (ATR=5.5, Ki=0.05)')
print('-'*160)

registry_fixed = CanonicalParameterRegistry()
registry_fixed.values['trailing_stop_atr_mult'] = 5.5  # INCREASED from 4.0
registry_fixed.values['pid_ki'] = 0.05  # INCREASED from 0.02

orch_fixed = Revision2ExternalEngineOrchestrator(
    [symbol],
    registry_fixed,
    starting_equity=1_000_000.0
)

symbol_bars = {symbol: df_reset}
report_fixed = orch_fixed.run(symbol_bars, warmup=60)

fixed_trades = report_fixed.get('trades', [])
fixed_pnl = report_fixed.get('net_pnl', 0)
fixed_wr = report_fixed.get('win_rate', 0)

print(f'Trades: {len(fixed_trades)}')
print(f'P&L: ₹{fixed_pnl:,.2f}')
print(f'Win rate: {100*fixed_wr:.1f}%')

# Analyze bars held
bars_0_fixed = sum(1 for t in fixed_trades if t.get('bars_held', 0) == 0)
bars_1_3_fixed = sum(1 for t in fixed_trades if 1 <= t.get('bars_held', 0) <= 3)
bars_4_plus_fixed = sum(1 for t in fixed_trades if t.get('bars_held', 0) >= 4)

print(f'\nBars held distribution:')
print(f'  0 bars (forced exits): {bars_0_fixed} trades ({100*bars_0_fixed/len(fixed_trades) if len(fixed_trades) > 0 else 0:.1f}%)')
print(f'  1-3 bars: {bars_1_3_fixed} trades ({100*bars_1_3_fixed/len(fixed_trades) if len(fixed_trades) > 0 else 0:.1f}%)')
print(f'  4+ bars: {bars_4_plus_fixed} trades ({100*bars_4_plus_fixed/len(fixed_trades) if len(fixed_trades) > 0 else 0:.1f}%)')
print()

# ═══════════════════════════════════════════════════════════════════════════
# COMPARISON
# ═══════════════════════════════════════════════════════════════════════════

print('='*160)
print('COMPARISON: Baseline vs Fixed')
print('='*160 + '\n')

print(f'{"Metric":<30} {"Baseline (4.0x ATR)":>35} {"Fixed (5.5x ATR)":>35} {"Change":>20}')
print('-'*160)

pnl_change = fixed_pnl - baseline_pnl
pnl_pct = (pnl_change / abs(baseline_pnl) * 100) if baseline_pnl != 0 else 0
print(f'{"Total Trades":<30} {len(baseline_trades):>35} {len(fixed_trades):>35} {len(fixed_trades) - len(baseline_trades):>20}')

print(f'{"P&L":<30} ₹{baseline_pnl:>33,.2f} ₹{fixed_pnl:>33,.2f} ₹{pnl_change:>18,.2f} ({pnl_pct:+.1f}%)')

wr_change = (fixed_wr - baseline_wr) * 100
print(f'{"Win Rate":<30} {100*baseline_wr:>33.1f}% {100*fixed_wr:>33.1f}% {wr_change:>18.1f}%')

avg_bars_baseline = sum(t.get('bars_held', 0) for t in baseline_trades) / len(baseline_trades) if len(baseline_trades) > 0 else 0
avg_bars_fixed = sum(t.get('bars_held', 0) for t in fixed_trades) / len(fixed_trades) if len(fixed_trades) > 0 else 0
avg_bars_change = avg_bars_fixed - avg_bars_baseline

print(f'{"Avg bars held":<30} {avg_bars_baseline:>35.2f} {avg_bars_fixed:>35.2f} {avg_bars_change:>20.2f}')

zero_bar_change = bars_0_fixed - bars_0
zero_bar_pct = (zero_bar_change / bars_0 * 100) if bars_0 > 0 else 0
print(f'{"0-bar forced exits":<30} {bars_0:>35} {bars_0_fixed:>35} {zero_bar_change:>20} ({zero_bar_pct:+.1f}%)')

# ═══════════════════════════════════════════════════════════════════════════
# KEY FINDINGS
# ═══════════════════════════════════════════════════════════════════════════

print()
print('='*160)
print('KEY FINDINGS')
print('='*160 + '\n')

if fixed_wr > 0.5:
    print(f'✓ TARGET ACHIEVED: Win rate {100*fixed_wr:.1f}% > 50%')
elif fixed_wr > baseline_wr:
    print(f'✓ IMPROVEMENT: Win rate increased from {100*baseline_wr:.1f}% to {100*fixed_wr:.1f}%')
else:
    print(f'⚠️  Win rate {100*fixed_wr:.1f}% (baseline: {100*baseline_wr:.1f}%)')

if fixed_pnl > 0:
    print(f'✓ PROFITABLE: ₹{fixed_pnl:,.2f} gain')
elif fixed_pnl > baseline_pnl:
    print(f'✓ IMPROVEMENT: Loss reduced from ₹{baseline_pnl:,.2f} to ₹{fixed_pnl:,.2f} (₹{abs(pnl_change):,.2f} saved)')
else:
    print(f'⚠️  P&L: ₹{fixed_pnl:,.2f}')

if avg_bars_fixed > avg_bars_baseline:
    print(f'✓ LONGER HOLDS: Trades held {avg_bars_change:.2f} more bars on average')
    print(f'  └─ More time for PID to work instead of mechanical ATR stop')

if bars_0_fixed < bars_0:
    print(f'✓ FEWER FORCED EXITS: Reduced 0-bar exits from {bars_0} to {bars_0_fixed} ({zero_bar_pct:.1f}% reduction)')
    print(f'  └─ PID controller now has time to influence exits')

print()
print('='*160)
print('RECOMMENDATION')
print('='*160 + '\n')

if fixed_wr > 0.5 and fixed_pnl > 0:
    print('✓ DEPLOY FIXED SETTINGS TO PRODUCTION')
    print(f'  ATR multiplier: 5.5 (was 4.0)')
    print(f'  PID Ki: 0.05 (was 0.02)')
    print(f'  Expected result: {100*fixed_wr:.1f}% win rate, ₹{fixed_pnl:,.2f} P&L')
elif fixed_wr > baseline_wr:
    print('✓ SETTINGS IMPROVED')
    print(f'  Deploy with continued tuning')
else:
    print('⚠️  Continue tuning:')
    print(f'  - Try ATR = 6.0 (give even more time)')
    print(f'  - Try Ki = 0.10 (accumulate faster)')

print()
print('='*160 + '\n')
