#!/usr/bin/env python3
"""Debug PA box calibration to see what scales are being used."""

import sys
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from market_data_loader import SingleSymbolReplayFeed
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2.boxes import PredictiveAnalyticsBox
from pathlib import Path

ROOT = Path(__file__).resolve().parent
manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
loader_result = verify_manifest(manifest)

feed = SingleSymbolReplayFeed("MARUTI", data_dir=manifest.data_dir, max_bars=None)
maruti_data = feed.load()
maruti_data["timestamp"] = pd.to_datetime(maruti_data["timestamp"])
maruti_data = maruti_data[(maruti_data["timestamp"] >= "2023-09-01") & (maruti_data["timestamp"] < "2023-10-01")]

# Get warmup bars (first 60)
warmup_bars = maruti_data.iloc[:60].copy()

print("WARMUP DATA ANALYSIS")
print("="*80)
print(f"Warmup bars: {len(warmup_bars)}")
print(f"Close range: {warmup_bars['close'].min():.2f} - {warmup_bars['close'].max():.2f}")
print(f"Close mean: {warmup_bars['close'].mean():.2f}")
print(f"Close std: {warmup_bars['close'].std():.2f}")

# Initialize and calibrate PA box
pa = PredictiveAnalyticsBox()
pa.calibrate("MARUTI", warmup_bars)

# Check the calibrated scales
print("\nCALIBRATED SCALES")
print("="*80)
scales = pa._scale.get("MARUTI")
if scales:
    print(f"dp_scale: {scales['dp_scale']}")
    print(f"dv_scale: {scales['dv_scale']}")
    print(f"baseline_vol: {scales['baseline_vol']}")
else:
    print("No scales found!")

# Calculate what volatility actually is in the test data
print("\nTEST DATA VOLATILITY ANALYSIS")
print("="*80)
atr_period = 20
close = maruti_data["close"].values
high = maruti_data["high"].values
low = maruti_data["low"].values

tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
atr = float(tr[-atr_period:].mean()) if len(tr) else 0.0
realized_vol = atr / close[-1] if close[-1] else 0.0

print(f"ATR (recent): {atr:.4f}")
print(f"Close (recent): {close[-1]:.2f}")
print(f"Realized volatility: {realized_vol:.6f}")
print(f"Baseline volatility: {scales.get('baseline_vol', 0):.6f}" if scales else "N/A")

vol_ratio = realized_vol / scales.get('baseline_vol', 1) if scales else 0
print(f"Vol ratio: {vol_ratio:.2f}")
if vol_ratio < 0.7:
    print("  → Low vol regime")
elif vol_ratio < 1.5:
    print("  → Medium vol regime")
else:
    print("  → High vol regime")
