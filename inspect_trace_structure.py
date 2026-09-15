#!/usr/bin/env python3
"""Inspect what's actually in the orchestrator trace."""

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

feed = SingleSymbolReplayFeed("MARUTI", data_dir=manifest.data_dir, max_bars=None)
maruti_data = feed.load()
maruti_data["timestamp"] = pd.to_datetime(maruti_data["timestamp"])
maruti_data = maruti_data[(maruti_data["timestamp"] >= "2023-09-01") & (maruti_data["timestamp"] < "2023-10-01")]

print("Running orchestrator...")
registry = CanonicalParameterRegistry()
orch = Revision2Orchestrator("MARUTI", registry=registry, starting_equity=100000.0)

trace = []
result = orch.run(maruti_data, warmup=60, trace_sink=trace)

# Inspect trace structure
print("\nTRACE STRUCTURE ANALYSIS")
print("="*80)

if trace:
    # Get keys from first few records
    print(f"Total trace records: {len(trace)}")
    print(f"\nKeys in first record:")
    if trace:
        keys = sorted(trace[0].keys())
        for key in keys:
            val = trace[0][key]
            print(f"  {key}: {type(val).__name__} = {str(val)[:60]}")

    # Sample some records to see what data exists
    print(f"\nSample trace records (every 100 bars):")
    for i in [60, 160, 260, 360]:
        if i < len(trace):
            rec = trace[i]
            print(f"\n  Bar {rec.get('bar_idx')}:")
            for key in ['pa_signal', 'entry_decision', 'mpc_output', 'trade_opened', 'trade_closed']:
                if key in rec:
                    print(f"    {key}: {rec.get(key)}")
else:
    print("No trace data")
