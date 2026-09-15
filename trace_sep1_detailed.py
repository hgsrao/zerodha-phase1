#!/usr/bin/env python3
"""Detailed trace of Sep 1 to find WHERE entry was missed."""

import sys
sys.path.insert(0, '.')

import pandas as pd
from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import SingleSymbolReplayFeed
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2.orchestrator import Revision2Orchestrator
from pathlib import Path

ROOT = Path.cwd()
manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
loader_result = verify_manifest(manifest)

feed = SingleSymbolReplayFeed("MARUTI", data_dir=manifest.data_dir, max_bars=None)
maruti_data = feed.load()
maruti_data["timestamp"] = pd.to_datetime(maruti_data["timestamp"])
maruti_data = maruti_data[(maruti_data["timestamp"] >= "2023-09-01") & (maruti_data["timestamp"] < "2023-09-02")]

print("DETAILED TRACE OF SEPTEMBER 1, 2023")
print("="*80)
print(f"Total bars: {len(maruti_data)}")
print(f"Open: ₹{maruti_data.iloc[0]['close']:.2f}")
print(f"Close: ₹{maruti_data.iloc[-1]['close']:.2f}")
print(f"Daily return: +{(maruti_data.iloc[-1]['close'] - maruti_data.iloc[0]['open'])/maruti_data.iloc[0]['open']*100:.2f}%")
print()

# Run orchestrator with full trace
registry = CanonicalParameterRegistry()
orch = Revision2Orchestrator("MARUTI", registry=registry, starting_equity=100000.0)

trace = []
result = orch.run(maruti_data, warmup=60, trace_sink=trace)

print("ORCHESTRATOR RESULT")
print("-"*80)
print(f"Completed trades: {result.get('completed_trades', 0)}")
print(f"Net P&L: ₹{result.get('net_pnl', 0):.2f}")
print()

# Find bars where PA generated strong signals
strong_signals = []
for t in trace:
    pa_conf = t.get('pa', {}).get('confidence', 0)
    pa_dir = t.get('pa', {}).get('direction', 0)
    bar_idx = t.get('bar_idx')
    timestamp = t.get('timestamp')
    stage = t.get('stage')

    if pa_conf > 0.2 and pa_dir != 0:  # Strong signal
        strong_signals.append({
            'bar_idx': bar_idx,
            'timestamp': timestamp,
            'pa_conf': pa_conf,
            'pa_dir': pa_dir,
            'stage': stage,
            'id_reason': t.get('id', {}).get('reason', 'N/A')
        })

print("STRONG PA SIGNALS (confidence > 0.2)")
print("-"*80)
print(f"Found {len(strong_signals)} bars with strong PA signals")
print()

if strong_signals:
    for sig in strong_signals[:10]:  # Show first 10
        direction = "UP" if sig['pa_dir'] > 0 else "DOWN"
        print(f"Bar {sig['bar_idx']:>3} {sig['timestamp']:>26} | Conf:{sig['pa_conf']:.3f} Dir:{direction} | Stage: {sig['stage']}")
        if "rejected_id" in sig['stage']:
            print(f"            └─ Rejected: {sig['id_reason']}")
    print()

# Find the FIRST opportunity that got rejected
print("="*80)
print("ANALYZING FIRST STRONG SIGNAL THAT GOT REJECTED")
print("="*80)

rejected_signals = [s for s in strong_signals if 'rejected' in s['stage']]
if rejected_signals:
    first_rejected = rejected_signals[0]
    bar_idx = first_rejected['bar_idx']

    print(f"\nFirst rejected strong signal at bar {bar_idx}")
    print(f"  Timestamp: {first_rejected['timestamp']}")
    print(f"  PA Confidence: {first_rejected['pa_conf']:.4f}")
    print(f"  PA Direction: {'UP' if first_rejected['pa_dir'] > 0 else 'DOWN'}")
    print(f"  Rejection reason: {first_rejected['id_reason']}")
    print()

    # Show bar data around that point
    print("Bar data around signal:")
    print("-"*80)
    start = max(60, bar_idx - 3)  # warmup is 60, so skip those
    end = min(len(maruti_data), bar_idx + 4)

    for i in range(start, end):
        bar = maruti_data.iloc[i]
        marker = " ← STRONG SIGNAL" if i == bar_idx else ""
        time = bar['timestamp'].strftime('%H:%M')
        print(f"  Bar {i:>3} {time} | O:{bar['open']:>8.2f} H:{bar['high']:>8.2f} L:{bar['low']:>8.2f} C:{bar['close']:>8.2f}{marker}")

# Check if ANY bars made it past ID rejection
print()
print("="*80)
print("REJECTION STATISTICS")
print("="*80)

stages = {}
for t in trace:
    stage = t.get('stage', 'unknown')
    if stage not in stages:
        stages[stage] = 0
    stages[stage] += 1

print("\nRejection breakdown:")
for stage, count in sorted(stages.items(), key=lambda x: -x[1])[:10]:
    pct = 100.0 * count / len(trace)
    print(f"  {stage}: {count} ({pct:.1f}%)")

print()
print("CONCLUSION:")
print("-"*80)
if result.get('completed_trades', 0) == 0:
    print("❌ NO TRADES COMPLETED ON SEP 1")
    if rejected_signals:
        print(f"   But found {len(rejected_signals)} strong signals that were REJECTED at ID stage")
        print(f"   This is the BOTTLENECK preventing trades")
    else:
        print("   And NO strong signals were even generated!")
