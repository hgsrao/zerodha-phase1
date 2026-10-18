#!/usr/bin/env python3
"""
Revision 3 In-House Engine: Vanilla Regime + ANSI Protection Relay, full 3-year calibration.

Parallel to baseline in-house engine (without relay).
Tests whether ANSI protection constraints improve or degrade parameter optimization.

Hypothesis: Relay may reject marginal candidates, tightening selection → better robustness.
Or: Relay may overly constrain → reduced acceptance, worse scores.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard

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
from collections import Counter

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.calibration_supervisor import AcceptanceGates, CalibrationRunConfig, CalibrationSupervisor
from revision2.dataset_manifest import DatasetManifest
from revision3.portfolio_orchestrator import Revision3PortfolioOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_revision3_inhouse"
OUTPUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUTPUT_DIR / "revision3_inhouse_48symbol_FULL_3YEAR_calibration_summary.json"
CHECKPOINT_PATH = OUTPUT_DIR / "revision3_inhouse_48symbol_FULL_3YEAR_calibration_checkpoint.json"


def load_symbols_full_dataset():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    all_symbols = sorted(f.symbol for f in manifest.files)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    symbol_bars = {}
    skipped = []
    for s in all_symbols:
        frame = loader._load_symbol_csv(s)
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
    parser = argparse.ArgumentParser(description="Revision 3 In-House: Vanilla + Protection Relay, full 3-year")
    parser.add_argument("--time-only", action="store_true", help="Run one fixed-param evaluation and exit")
    args = parser.parse_args()

    print("=" * 100, flush=True)
    print("REVISION 3 IN-HOUSE ENGINE (VANILLA + ANSI PROTECTION RELAY): FULL 3-YEAR CALIBRATION", flush=True)
    print("=" * 100, flush=True)

    symbols, symbol_bars = load_symbols_full_dataset()
    total_bars = sum(len(b) for b in symbol_bars.values())
    all_timestamps = pd.concat([b["timestamp"] for b in symbol_bars.values()])
    date_start = all_timestamps.min()
    date_end = all_timestamps.max()
    date_duration = (date_end - date_start).days

    print(f"\n{len(symbols)} symbols, {total_bars:,} TOTAL bars (full 3-year dataset)", flush=True)
    print(f"Date range: {date_start} to {date_end} ({date_duration} days)", flush=True)
    print(f"Protection Relays: ANSI 81 (frequency), 63 (thermal), 27/59 (voltage), 87 (differential), 50/51 (overcurrent)", flush=True)

    registry = CanonicalParameterRegistry()

    if args.time_only:
        print("\nRunning single fixed-parameter evaluation...", flush=True)
        t0 = time.time()
        orch = Revision3PortfolioOrchestrator(symbols, registry, starting_equity=1_000_000.0)
        report = orch.run(symbol_bars, warmup=60)
        elapsed = time.time() - t0
        print(json.dumps({
            "elapsed_seconds": elapsed, "bars_processed": report.get("bars_processed", 0),
            "completed_trades": report.get("completed_trades", 0), "net_pnl": report.get("net_pnl", 0),
            "protection_status": orch.get_protection_status(),
        }, indent=2, default=str), flush=True)
        return

    print("\nStarting calibration supervisor (Revision3 + Protection Relay)...", flush=True)
    run_config = CalibrationRunConfig.from_registry_defaults(registry, checkpoint_path=str(CHECKPOINT_PATH), seed=306)
    gates = AcceptanceGates()

    supervisor = CalibrationSupervisor(
        registry, symbols, symbol_bars, run_config=run_config, gates=gates,
        warmup=60, starting_equity=1_000_000.0,
        orchestrator_class=Revision3PortfolioOrchestrator,
    )

    t0 = time.time()
    result = supervisor.run()
    elapsed = time.time() - t0

    summary = {
        "engine": "Revision3PortfolioOrchestrator (Vanilla + ANSI Relay)",
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

    print("\n" + "=" * 100, flush=True)
    print("REVISION 3 IN-HOUSE CALIBRATION COMPLETE", flush=True)
    print("=" * 100, flush=True)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("symbols",)}, indent=2, default=str), flush=True)
    print(f"\nSaved to {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    main()
