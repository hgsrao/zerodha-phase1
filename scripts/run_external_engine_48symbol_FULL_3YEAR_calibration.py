#!/usr/bin/env python3
"""Full 3-year calibration (2023-07-03 to 2026-08-24) for the external-library engine.

CRITICAL: The 1-month smoke test produced ZERO PA signals (0 trades across all candidates).
This is a data-scale problem: 60-bar warmup (1 hour) is insufficient for feature normalization.

This script uses the FULL 3-year historical dataset to properly calibrate scale factors:
- 60+ bar warmup is now embedded in 1000s of bars of trading history
- Feature variance is stable and meaningful
- Should produce directional signals and actual trades

This is a VALIDATION run: it re-verifies that both PA box calibration and entire pipeline
can generate trades on sufficient data, before claiming the 1-month smoke test is invalid.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard  # Pin BLAS/LAPACK threading + fix Python hash randomization before numpy

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

import argparse
import json
import time

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.calibration_supervisor import AcceptanceGates, CalibrationRunConfig, CalibrationSupervisor
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUTPUT_DIR / "external_engine_48symbol_FULL_3YEAR_calibration_summary.json"
CHECKPOINT_PATH = OUTPUT_DIR / "external_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json"


def load_symbols_full_dataset():
    """Load ALL available data for each symbol (typically 3 years, 2023-07-03 to 2026-08-24)."""
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    all_symbols = sorted(f.symbol for f in manifest.files)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    symbol_bars = {}
    skipped = []
    for s in all_symbols:
        frame = loader._load_symbol_csv(s)
        # NO cutoff: use all available data for this symbol
        if len(frame) == 0:
            skipped.append((s, "no data at all"))
            continue
        symbol_bars[s] = frame.reset_index(drop=True)
    if skipped:
        print(f"Skipped {len(skipped)} symbol(s) with no data:", flush=True)
        for s, reason in skipped:
            print(f"  {s}: {reason}", flush=True)
    return sorted(symbol_bars.keys()), symbol_bars


def main():
    parser = argparse.ArgumentParser(description="Full 3-year calibration with proper data scale")
    parser.add_argument("--time-only", action="store_true", help="Run one fixed-param evaluation and exit")
    args = parser.parse_args()

    print("=" * 90, flush=True)
    print("LOADING FULL 3-YEAR DATASET (vs. 1-month smoke test)", flush=True)
    print("=" * 90, flush=True)

    symbols, symbol_bars = load_symbols_full_dataset()
    total_bars = sum(len(b) for b in symbol_bars.values())

    # Report date range and scale
    all_timestamps = pd.concat([b["timestamp"] for b in symbol_bars.values()])
    date_start = all_timestamps.min()
    date_end = all_timestamps.max()
    date_duration = (date_end - date_start).days

    print(f"\n{len(symbols)} symbols, {total_bars:,} TOTAL bars (full dataset)", flush=True)
    print(f"Date range: {date_start} to {date_end} ({date_duration} days)", flush=True)
    print(f"Average bars per symbol: {total_bars // len(symbols):,}", flush=True)
    print(f"\nComparison to 1-month smoke test:")
    print(f"  1-month: ~8,600 bars/symbol, 60-bar warmup (1 hour)")
    print(f"  3-year:  ~{total_bars // len(symbols):,} bars/symbol, 60-bar warmup still tiny fraction", flush=True)

    registry = CanonicalParameterRegistry()

    if args.time_only:
        print("\nRunning single fixed-parameter evaluation...", flush=True)
        t0 = time.time()
        orch = Revision2ExternalEngineOrchestrator(symbols, registry, starting_equity=1_000_000.0)
        report = orch.run(symbol_bars, warmup=60)
        elapsed = time.time() - t0
        print(json.dumps({
            "elapsed_seconds": elapsed, "bars_processed": report["bars_processed"],
            "completed_trades": report["completed_trades"], "net_pnl": report["net_pnl"],
        }, indent=2, default=str), flush=True)
        return

    print("\nStarting calibration supervisor (RandomSearch → TPE → CMA-ES → fine-tune)...", flush=True)
    run_config = CalibrationRunConfig.from_registry_defaults(registry, checkpoint_path=str(CHECKPOINT_PATH), seed=203)  # seed=203 for 3-year run
    gates = AcceptanceGates()  # smoke_test_defaults

    supervisor = CalibrationSupervisor(
        registry, symbols, symbol_bars, run_config=run_config, gates=gates,
        warmup=60, starting_equity=1_000_000.0,
        orchestrator_class=Revision2ExternalEngineOrchestrator,
    )

    t0 = time.time()
    result = supervisor.run()
    elapsed = time.time() - t0

    from collections import Counter
    summary = {
        "engine": "Revision2ExternalEngineOrchestrator",
        "dataset": "FULL_3YEAR (2023-07-03 to 2026-08-24)",
        "symbols": symbols,
        "total_bars": total_bars,
        "bars_per_symbol_avg": total_bars // len(symbols),
        "elapsed_seconds": elapsed,
        "stopped_reason": result.stopped_reason,
        "candidates_evaluated": len(result.candidates),
        "candidates_by_phase": dict(Counter(c.phase for c in result.candidates)),
        "candidates_accepted": sum(1 for c in result.candidates if c.accepted),
        "best_score": result.best_score,
        "best_params": result.best_params,
        "best_report": {k: v for k, v in (result.best_report or {}).items() if k != "trades"},
    }
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\n" + "=" * 90, flush=True)
    print("CALIBRATION COMPLETE", flush=True)
    print("=" * 90, flush=True)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("symbols",)}, indent=2, default=str), flush=True)
    print(f"\nSaved to {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    main()
