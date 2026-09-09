#!/usr/bin/env python3
"""
Prepare data for Ray Tune + Optuna calibrator.

Loads 48 NSE symbols from manifest, extracts 1 month of 1-minute bars,
and serializes as pickle dict for parallel calibration.

Usage:
    python prepare_calibration_data.py \
        --manifest revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json \
        --output symbol_bars.pkl \
        --days 30
"""

import os
import sys
import pickle
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd

from inhouse_validation.manifest_loader import ManifestLoader


def prepare_calibration_data(
    manifest_path: str,
    output_path: str = "symbol_bars.pkl",
    num_days: int = 30,
    warmup_days: int = 1,
) -> None:
    """
    Load 48 symbols × num_days, serialize for calibration.

    Args:
        manifest_path: Path to DATASET_MANIFEST_48SYMBOL_1MIN.json
        output_path: Output pickle file
        num_days: Number of trading days (roughly 20-22 business days/month)
        warmup_days: Additional days for warmup bars
    """

    print()
    print("="*120)
    print("RAY TUNE DATA PREPARATION")
    print("="*120)
    print()

    # Load manifest
    print(f"[LOAD] Loading manifest: {manifest_path}")
    loader = ManifestLoader(manifest_path)
    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))

    all_symbols = loader.manifest.get_symbols()
    print(f"  ✓ Found {len(all_symbols)} symbols")
    print()

    # Calculate bars needed
    # 1 minute bars: ~390 bars/trading day (6.5 hours × 60 min)
    bars_per_day = 390
    total_bars_needed = (num_days + warmup_days) * bars_per_day

    print(f"[PLAN] Data extraction:")
    print(f"  Days: {num_days} (calibration) + {warmup_days} (warmup) = {num_days + warmup_days} total")
    print(f"  Bars per day: {bars_per_day}")
    print(f"  Bars needed per symbol: ~{total_bars_needed}")
    print()

    # Load symbol data
    print(f"[LOAD] Loading symbol data...")
    symbol_bars = {}
    loaded_count = 0
    skipped_count = 0

    for i, symbol in enumerate(all_symbols, 1):
        try:
            # Get file from manifest
            entry = loader.manifest.get_file(symbol)
            if entry is None:
                skipped_count += 1
                continue

            # Load CSV
            path = data_dir / entry.filename
            if not path.exists():
                skipped_count += 1
                continue

            # Verify hash
            actual = loader._compute_file_hash(path)
            if actual != entry.sha256:
                print(f"  ⚠ {symbol} hash mismatch, skipping")
                skipped_count += 1
                continue

            # Load bars
            bars = pd.read_csv(path)

            # Extract first N bars (warmup + calibration)
            if len(bars) < total_bars_needed:
                print(f"  ⚠ {symbol} only {len(bars)} bars, need {total_bars_needed}, skipping")
                skipped_count += 1
                continue

            bars_subset = bars.iloc[:total_bars_needed].copy()
            symbol_bars[symbol] = bars_subset
            loaded_count += 1

            if loaded_count % 5 == 0 or loaded_count == len(all_symbols):
                print(f"  [{loaded_count:2d}/{len(all_symbols)}] {symbol:15s} ✓ ({len(bars_subset)} bars)")

        except Exception as e:
            print(f"  ⚠ {symbol} error: {e}")
            skipped_count += 1
            continue

    print()
    print(f"[RESULT]")
    print(f"  Symbols loaded: {loaded_count}")
    print(f"  Symbols skipped: {skipped_count}")
    print(f"  Total bars: {sum(len(bars) for bars in symbol_bars.values()):,}")
    print()

    if not symbol_bars:
        print("❌ No symbols loaded, aborting")
        sys.exit(1)

    # Serialize
    print(f"[SAVE] Serializing to {output_path}...")
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "wb") as f:
        pickle.dump(symbol_bars, f)

    size_mb = output_file.stat().st_size / (1024 * 1024)
    print(f"  ✓ {size_mb:.1f} MB")
    print()

    # Summary
    print(f"[SUMMARY]")
    print(f"  Ready for Ray Tune calibration")
    print(f"  Run:")
    print(f"    python ray_tune_optuna_calibrator.py \\")
    print(f"      --num-samples 200 \\")
    print(f"      --num-workers 16 \\")
    print(f"      --data-path {output_path}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare data for Ray Tune calibrator")
    parser.add_argument("--manifest", type=str, required=True, help="Path to manifest JSON")
    parser.add_argument("--output", type=str, default="symbol_bars.pkl", help="Output pickle file")
    parser.add_argument("--days", type=int, default=30, help="Number of calibration days")
    parser.add_argument("--warmup-days", type=int, default=1, help="Number of warmup days")

    args = parser.parse_args()

    try:
        prepare_calibration_data(
            manifest_path=args.manifest,
            output_path=args.output,
            num_days=args.days,
            warmup_days=args.warmup_days,
        )
    except Exception as e:
        print(f"❌ Data preparation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
