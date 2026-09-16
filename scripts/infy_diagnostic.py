#!/usr/bin/env python3
"""
INFY Comprehensive Diagnostic
==============================

Deep investigation into what's actually happening with the 62 losing trades.

Questions:
1. Are entry prices reasonable?
2. Are stops too tight?
3. Are exits happening at the right prices?
4. Is there a P&L calculation error?
5. Is the problem the STRATEGY or the SYSTEM?
"""

import sys
sys.path.insert(0, '.')

import determinism_guard
import os
os.environ['OMP_NUM_THREADS'] = '1'

import pandas as pd
import numpy as np

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

print('\n' + '='*160)
print('INFY COMPREHENSIVE DIAGNOSTIC')
print('='*160 + '\n')

# Reset stop_loss_atr_mult to max
registry = CanonicalParameterRegistry()

# Read current value to understand what it's at
stop_spec = registry.get('stop_loss_atr_mult')
print(f'Current stop_loss_atr_mult: {stop_spec.default}\n')

symbol = 'INFY'
manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

df = loader._load_symbol_csv(symbol)
df = df.iloc[:5000]

print(f'[1] INFY Data Quality')
print('-'*160)
print(f'Bars loaded: {len(df)}')
print(f'Close price range: ₹{df["close"].min():.2f} to ₹{df["close"].max():.2f}')
print(f'Typical spread (High-Low): ₹{(df["high"] - df["low"]).mean():.2f}')
print(f'Typical ATR: ₹{df["close"].rolling(14).std().mean():.2f}')
daily_range_pct = 100*((df["high"] - df["low"])/df["close"]).mean()
print(f'Daily range as % of price: {daily_range_pct:.2f}%\n')

# Run backtest
orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)

symbol_bars = {symbol: df}
report = orch.run(symbol_bars, warmup=60)

trades = report.get('trades', [])

print(f'[2] Trades Generated')
print('-'*160)
print(f'Total: {len(trades)}')
print(f'Avg entry price: ₹{np.mean([t.get("entry_price", 0) for t in trades]):.2f}')
print(f'Avg P&L: ₹{np.mean([t.get("pnl", 0) for t in trades]):.2f}')
print(f'Total P&L: ₹{sum([t.get("pnl", 0) for t in trades]):.2f}\n')

print(f'[3] First 10 Trades - Detailed Analysis')
print('-'*160)
print(f'{"#":<3} {"Entry":>10} {"Stop":>10} {"Target":>10} {"Exit":>10} {"P&L":>10} {"Held":>6} {"Return%":>10}')
print('-'*160)

for i, t in enumerate(trades[:10]):
    entry = t.get('entry_price', 0)
    stop = t.get('stop_price', entry)
    target = t.get('target_price', entry)
    exit_p = t.get('exit_price', entry)
    pnl = t.get('pnl', 0)
    bars = t.get('bars_held', 0)

    if entry > 0:
        ret = 100 * (exit_p - entry) / entry
    else:
        ret = 0

    stop_dist = abs(entry - stop) / entry * 100 if entry > 0 else 0
    target_dist = abs(target - entry) / entry * 100 if entry > 0 else 0

    print(f'{i+1:<3} {entry:>10.2f} {stop:>10.2f} {target:>10.2f} {exit_p:>10.2f} {pnl:>10.2f} {bars:>6d} {ret:>9.2f}%')
    print(f'     Stop{stop_dist:.1f}% away │ Target {target_dist:.1f}% away')

print('\n' + '[4] P&L Distribution Analysis')
print('-'*160)

profits = [t.get('pnl', 0) for t in trades if t.get('pnl', 0) > 0]
losses = [t.get('pnl', 0) for t in trades if t.get('pnl', 0) < 0]

print(f'Winning trades: {len(profits)} ({100*len(profits)/len(trades):.1f}%)')
print(f'Losing trades: {len(losses)} ({100*len(losses)/len(trades):.1f}%)')

if profits:
    print(f'  Avg win: ₹{np.mean(profits):,.2f}')
    print(f'  Max win: ₹{np.max(profits):,.2f}')
    print(f'  Total profit: ₹{np.sum(profits):,.2f}')

if losses:
    print(f'  Avg loss: ₹{np.mean(losses):,.2f}')
    print(f'  Max loss: ₹{np.min(losses):,.2f}')
    print(f'  Total loss: ₹{np.sum(losses):,.2f}')

print(f'\nExpected Value (EV):')
if profits and losses:
    n_total = len(trades)
    win_pct = len(profits) / n_total
    loss_pct = len(losses) / n_total
    avg_win = np.mean(profits)
    avg_loss = abs(np.mean(losses))

    ev_per_trade = (win_pct * avg_win) - (loss_pct * avg_loss)
    print(f'  Win%: {100*win_pct:.1f}%, AvgWin: ₹{avg_win:.2f}')
    print(f'  Loss%: {100*loss_pct:.1f}%, AvgLoss: ₹{avg_loss:.2f}')
    print(f'  EV per trade: ₹{ev_per_trade:.2f}')

    if ev_per_trade > 0:
        print(f'  → Positive expectancy! Problem is VARIANCE, not edge.')
    else:
        print(f'  → Negative expectancy. Strategy has no edge.')

print('\n' + '[5] Exit Quality Analysis')
print('-'*160)

exit_reasons = {}
for t in trades:
    reason = t.get('exit_reason', 'unknown')
    exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

print('Exit reasons:')
for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
    pct = 100 * count / len(trades)
    print(f'  {reason:<40}: {count:3d} ({pct:5.1f}%)')

print('\n' + '='*160)
print('DIAGNOSTIC SUMMARY')
print('='*160 + '\n')

# Hypothesis testing
print('Hypothesis 1: Strategy has no edge on INFY')
if len(profits) < len(losses):
    print('  ✓ LIKELY - More losing than winning trades')
else:
    print('  ✗ Unlikely - Many winning trades generated')

print('\nHypothesis 2: Mechanical stop is too tight')
if 'stop_hit' in exit_reasons and exit_reasons.get('stop_hit', 0) > len(trades) * 0.7:
    print('  ✓ LIKELY - 70%+ of trades exit via stop')
else:
    print('  ? Unclear - Not all exits are stop hits')

print('\nHypothesis 3: Entry signal quality is poor')
print(f'  Signal quality: {100*len(profits)/len(trades):.1f}% win rate')
if len(profits) / len(trades) < 0.4:
    print('  ✓ LIKELY - Win rate < 40%')

print('\n' + '='*160 + '\n')

print('NEXT STEPS:')
print('  1. If strategy has no edge → Switch to different entry logic')
print('  2. If stops too tight → Increase stop distance (already done)')
print('  3. If signal is weak → Raise entry confidence threshold')
print('  4. If P&L calc is wrong → Verify with manual calculation')

print('\n' + '='*160 + '\n')
