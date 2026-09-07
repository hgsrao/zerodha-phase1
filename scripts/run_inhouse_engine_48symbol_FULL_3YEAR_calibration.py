#!/usr/bin/env python3
"""Full 3-year calibration for the in-house engine (vanilla volatility regime detection).

Parallel to external engine: both running on identical FULL 3-year dataset.
In-house engine uses vanilla IntelligentDiscriminationBox (no HMM) to establish baseline.

Expected outcome: if PA box was the bottleneck on 1-month data, BOTH engines should now
produce meaningful trade counts and accepted candidates on 3-year data.
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
from revision2.orchestrator import Revision2Orchestrator  # In-house orchestrator (NOT external)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_inhouse_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUTPUT_DIR / "inhouse_engine_48symbol_FULL_3YEAR_calibration_summary.json"
CHECKPOINT_PATH = OUTPUT_DIR / "inhouse_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json"


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
    parser = argparse.ArgumentParser(description="In-house engine: full 3-year calibration")
    parser.add_argument("--time-only", action="store_true", help="Run one fixed-param evaluation and exit")
    args = parser.parse_args()

    print("=" * 90, flush=True)
    print("IN-HOUSE ENGINE: LOADING FULL 3-YEAR DATASET", flush=True)
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

    registry = CanonicalParameterRegistry()

    if args.time_only:
        print("\nRunning single fixed-parameter evaluation...", flush=True)
        t0 = time.time()
        orch = Revision2Orchestrator(symbols, registry, starting_equity=1_000_000.0)
        report = orch.run(symbol_bars, warmup=60)
        elapsed = time.time() - t0
        print(json.dumps({
            "elapsed_seconds": elapsed, "bars_processed": report["bars_processed"],
            "completed_trades": report["completed_trades"], "net_pnl": report["net_pnl"],
        }, indent=2, default=str), flush=True)
        return

    print("\nStarting calibration supervisor...", flush=True)
    run_config = CalibrationRunConfig.from_registry_defaults(registry, checkpoint_path=str(CHECKPOINT_PATH), seed=204)  # seed=204 for in-house
    gates = AcceptanceGates()  # smoke_test_defaults

    supervisor = CalibrationSupervisor(
        registry, symbols, symbol_bars, run_config=run_config, gates=gates,
        warmup=60, starting_equity=1_000_000.0,
        orchestrator_class=Revision2Orchestrator,
    )

    t0 = time.time()
    result = supervisor.run()
    elapsed = time.time() - t0

    from collections import Counter
    summary = {
        "engine": "Revision2Orchestrator (in-house, vanilla ID box)",
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
    print("IN-HOUSE CALIBRATION COMPLETE", flush=True)
    print("=" * 90, flush=True)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("symbols",)}, indent=2, default=str), flush=True)
    print(f"\nSaved to {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    main()
