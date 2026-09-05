#!/usr/bin/env python3
"""Real calibration search for the IN-HOUSE engine: same 48 real symbols,
same first real calendar month (2023-07-03 to 2023-08-03), same real
RandomSearch -> TPE -> CMA-ES -> local-fine-tune supervisor, same run
config and seed as
scripts/run_external_engine_48symbol_1month_calibration.py -- the external-
engine sibling of this script -- so the two searches are a fair,
apples-to-apples comparison, not just two unrelated runs. The only real
difference is orchestrator_class: Revision2PortfolioOrchestrator here
(this module's original, default target) instead of
Revision2ExternalEngineOrchestrator.

Same caveat as the external-engine sibling: this is a SEARCH-PHASE result,
not a validated one -- one month across 48 symbols, no train/validation
sealing wired up yet. Whatever "winner" this finds must be re-checked with
a single full 48-symbol/3-year run before it is trusted.

--time-only runs just ONE fixed-parameter evaluation and exits, for a real
per-candidate timing measurement before committing to the full search.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.calibration_supervisor import AcceptanceGates, CalibrationRunConfig, CalibrationSupervisor
from revision2.dataset_manifest import DatasetManifest
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUTPUT_DIR / "inhouse_engine_48symbol_1month_calibration_summary.json"
CHECKPOINT_PATH = OUTPUT_DIR / "inhouse_engine_48symbol_1month_calibration_checkpoint.json"


def load_symbols_one_month():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    all_symbols = sorted(f.symbol for f in manifest.files)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    symbol_bars = {}
    skipped = []
    for s in all_symbols:
        frame = loader._load_symbol_csv(s)
        cutoff = pd.Timestamp("2023-07-03", tz=frame["timestamp"].dt.tz) + pd.DateOffset(months=1)
        sliced = frame[frame["timestamp"] < cutoff].reset_index(drop=True)
        if len(sliced) == 0:
            # Real data limitation, not a bug -- see the external-engine
            # sibling script's identical comment (JIOFIN, real first bar
            # 2023-08-21, doesn't exist yet in this window).
            skipped.append((s, str(frame["timestamp"].iloc[0]) if len(frame) else "no data at all"))
            continue
        symbol_bars[s] = sliced
    if skipped:
        print(f"Skipped {len(skipped)} symbol(s) with no real data in this window (real first bar shown):")
        for s, first_ts in skipped:
            print(f"  {s}: real data starts {first_ts}")
    return sorted(symbol_bars.keys()), symbol_bars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--time-only", action="store_true", help="Run one fixed-param evaluation and exit")
    args = parser.parse_args()

    symbols, symbol_bars = load_symbols_one_month()
    total_bars = sum(len(b) for b in symbol_bars.values())
    print(f"{len(symbols)} symbols, {total_bars} total real bars, "
          f"window {list(symbol_bars.values())[0]['timestamp'].iloc[0]} to {list(symbol_bars.values())[0]['timestamp'].iloc[-1]}", flush=True)

    registry = CanonicalParameterRegistry()

    if args.time_only:
        t0 = time.time()
        orch = Revision2PortfolioOrchestrator(symbols, registry, starting_equity=1_000_000.0)
        report = orch.run(symbol_bars, warmup=60)
        elapsed = time.time() - t0
        print(json.dumps({
            "elapsed_seconds": elapsed, "bars_processed": report["bars_processed"],
            "completed_trades": report["completed_trades"], "net_pnl": report["net_pnl"],
        }, indent=2, default=str))
        return

    # Identical run_config/gates/seed to the external-engine sibling script
    # -- only orchestrator_class differs -- so the two searches are
    # comparable, not just two independent, differently-configured runs.
    run_config = CalibrationRunConfig.from_registry_defaults(registry, checkpoint_path=str(CHECKPOINT_PATH), seed=202)
    gates = AcceptanceGates()  # smoke_test_defaults -- appropriate for a search phase, not final validation

    supervisor = CalibrationSupervisor(
        registry, symbols, symbol_bars, run_config=run_config, gates=gates,
        warmup=60, starting_equity=1_000_000.0,
        orchestrator_class=Revision2PortfolioOrchestrator,
    )

    t0 = time.time()
    result = supervisor.run()
    elapsed = time.time() - t0

    from collections import Counter
    summary = {
        "engine": "Revision2PortfolioOrchestrator", "symbols": symbols, "window": "2023-07-03 to one month later",
        "elapsed_seconds": elapsed, "stopped_reason": result.stopped_reason,
        "candidates_evaluated": len(result.candidates),
        "candidates_by_phase": dict(Counter(c.phase for c in result.candidates)),
        "candidates_accepted": sum(1 for c in result.candidates if c.accepted),
        "best_score": result.best_score, "best_params": result.best_params,
        "best_report": {k: v for k, v in (result.best_report or {}).items() if k != "trades"},
    }
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("symbols",)}, indent=2, default=str))
    print(f"\nsaved to {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
