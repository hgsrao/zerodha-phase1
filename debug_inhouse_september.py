#!/usr/bin/env python3
"""Debug in-house engine to find why no trades are being generated."""

import sys
sys.path.insert(0, '.')

import pandas as pd
from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import SingleSymbolReplayFeed
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2.orchestrator import Revision2Orchestrator
from pathlib import Path

ROOT = Path(__file__).resolve().parent
manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
loader_result = verify_manifest(manifest)
if not loader_result.valid:
    raise RuntimeError(loader_result.message)

# Load MARUTI September data
print("Loading MARUTI September 2023 data...")
feed = SingleSymbolReplayFeed("MARUTI", data_dir=manifest.data_dir, max_bars=None)
maruti_data = feed.load()
maruti_data["timestamp"] = pd.to_datetime(maruti_data["timestamp"])
maruti_data = maruti_data[(maruti_data["timestamp"] >= "2023-09-01") & (maruti_data["timestamp"] < "2023-10-01")]
print(f"Loaded {len(maruti_data)} bars\n")

# Run orchestrator with full trace
print("Running orchestrator with tracing...")
registry = CanonicalParameterRegistry()
orch = Revision2Orchestrator("MARUTI", registry=registry, starting_equity=100000.0)

# Collect trace for debugging
trace = []
result = orch.run(maruti_data, warmup=60, trace_sink=trace)

print("\n" + "="*80)
print("ORCHESTRATOR EXECUTION SUMMARY")
print("="*80)
print(f"Bars processed: {len(maruti_data)}")
print(f"Warmup bars: 60")
print(f"Trade bars: {len(maruti_data) - 60}")
print(f"\nCompleted trades: {result.get('completed_trades', 0)}")
print(f"Net P&L: ₹{result.get('net_pnl', 0):.2f}")
print(f"Gross P&L: ₹{result.get('gross_pnl', 0):.2f}")

# Analyze trace for entry signals
print("\n" + "="*80)
print("ANALYZING TRACE FOR ENTRY SIGNALS")
print("="*80)

if trace:
    print(f"Trace records: {len(trace)}")

    # Look for entry signals
    entry_signals = [t for t in trace if t.get('entry_signal_generated')]
    print(f"Entry signals generated: {len(entry_signals)}")

    if entry_signals:
        print("\nFirst 5 entry signals:")
        for i, sig in enumerate(entry_signals[:5], 1):
            print(f"  {i}. Bar {sig.get('bar_idx')}: {sig.get('entry_signal_generated')}")

    # Look for trades that were opened
    trades_opened = [t for t in trace if t.get('trade_opened')]
    print(f"\nTrades opened: {len(trades_opened)}")

    if trades_opened:
        print("First 3 trades opened:")
        for i, trade in enumerate(trades_opened[:3], 1):
            print(f"  {i}. Bar {trade.get('bar_idx')}: {trade.get('trade_opened')}")

    # Look for safety gate rejections
    gate_rejects = [t for t in trace if t.get('safety_gate_rejection')]
    print(f"\nSafety gate rejections: {len(gate_rejects)}")

    if gate_rejects:
        print("First 5 rejections:")
        for i, reject in enumerate(gate_rejects[:5], 1):
            reason = reject.get('safety_gate_rejection', {})
            print(f"  {i}. Bar {reject.get('bar_idx')}: {reason}")
else:
    print("No trace data collected")

# Check PA confidence levels
print("\n" + "="*80)
print("CHECKING PA SIGNAL CONFIDENCE LEVELS")
print("="*80)

if trace:
    pa_scores = [t.get('pa_confidence') for t in trace if t.get('pa_confidence') is not None]
    if pa_scores:
        print(f"PA confidence samples: {len(pa_scores)}")
        print(f"Min: {min(pa_scores):.4f}")
        print(f"Max: {max(pa_scores):.4f}")
        print(f"Mean: {sum(pa_scores)/len(pa_scores):.4f}")

        threshold = registry.get('entry_confidence_threshold').default
        above_threshold = sum(1 for s in pa_scores if s >= threshold)
        print(f"\nEntry threshold: {threshold}")
        print(f"Signals above threshold: {above_threshold}/{len(pa_scores)}")
    else:
        print("No PA confidence data in trace")

print("\n" + "="*80)
print("COMPARISON WITH EXTERNAL ENGINE")
print("="*80)
print(f"External Sep: 3 trades, +₹295.66")
print(f"In-house Sep: {result.get('completed_trades', 0)} trades, ₹{result.get('net_pnl', 0):.2f}")
