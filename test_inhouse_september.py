#!/usr/bin/env python3
"""Quick test of in-house engine on September 2023 with fixed configuration."""

import sys
sys.path.insert(0, '.')

import pandas as pd
from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import SingleSymbolReplayFeed
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2.orchestrator import Revision2Orchestrator
from pathlib import Path

# Load data
ROOT = Path(__file__).resolve().parent
manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
verified = verify_manifest(manifest)
if not verified.valid:
    raise RuntimeError(verified.message)

# Load MARUTI September data
print("Loading MARUTI September 2023 data...")
feed = SingleSymbolReplayFeed("MARUTI", data_dir=manifest.data_dir, max_bars=None)
maruti_data = feed.load()

# Filter to September 2023
maruti_data["timestamp"] = pd.to_datetime(maruti_data["timestamp"])
maruti_data = maruti_data[(maruti_data["timestamp"] >= "2023-09-01") & (maruti_data["timestamp"] < "2023-10-01")]
print(f"Loaded {len(maruti_data)} bars")

# Run orchestrator
print("\nRunning in-house orchestrator...")
registry = CanonicalParameterRegistry()
orch = Revision2Orchestrator("MARUTI", registry=registry, starting_equity=100000.0)

result = orch.run(maruti_data, warmup=60)

print("\nRESULT:")
print("="*80)
print(f"Completed Trades: {result.get('completed_trades', 0)}")
print(f"Gross P&L:        ₹{result.get('gross_pnl', 0):.2f}")
print(f"Net P&L:          ₹{result.get('net_pnl', 0):.2f}")
print(f"Max Drawdown:     {result.get('mtm_max_drawdown_fraction', 0)*100:.4f}%")

print("\nCOMPARISON:")
print("="*80)
print(f"External Sep:     +₹295.66")
print(f"In-house Sep:     ₹{result.get('net_pnl', 0):.2f}")

if result.get('net_pnl', 0) > 0:
    print("\n✅ PROFITABLE - Configuration fix worked!")
else:
    print("\n❌ Still losing - need more investigation")
