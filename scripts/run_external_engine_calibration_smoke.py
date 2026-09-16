#!/usr/bin/env python3
"""Smoke-scale proof that CalibrationSupervisor's real search machinery
(RandomSearch -> TPE -> CMA-ES -> local fine-tune, revision2/optimizer.py)
now drives revision2_external.orchestrator.Revision2ExternalEngineOrchestrator
correctly, not just the in-house engine it was originally built for.

Deliberately small: a handful of symbols, a short real trailing window, and
few trials -- this is a MECHANISM CHECK, not a real calibration result. A
result from this scale is not evidence of a good parameter set; it only
proves the wiring (supervisor -> RandomSearch/TPE/CMA-ES -> real external-
engine backtest -> AcceptanceGates -> scoring -> winner selection) runs
end-to-end without errors, on real data, same as the in-house engine's own
"smoke" profile in run_revision2_calibration.py already does for itself.

No train/validation sealing here either -- see calibration_supervisor.py's
own module docstring on why an unsealed full-scale run must never be
presented as a real result. This script's whole job is smaller than that:
prove the plumbing works before spending real compute on real search.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.calibration_supervisor import AcceptanceGates, CalibrationRunConfig, CalibrationSupervisor
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUTPUT_DIR / "external_engine_calibration_smoke_summary.json"

SYMBOLS = ["INFY", "MARUTI", "TCS"]
TAIL_BARS = 1500


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    symbol_bars = {s: loader._load_symbol_csv(s).tail(TAIL_BARS).reset_index(drop=True) for s in SYMBOLS}
    for s, bars in symbol_bars.items():
        print(f"{s}: {len(bars)} real bars, {bars['timestamp'].iloc[0]} to {bars['timestamp'].iloc[-1]}", flush=True)

    registry = CanonicalParameterRegistry()
    run_config = CalibrationRunConfig(phase1_trials=4, phase2_generations=1, phase3_iterations=2, seed=101)
    # Loose gates on purpose -- this is a mechanism check on 1500 bars, not
    # a real calibration; a real run uses AcceptanceGates()'s real defaults.
    gates = AcceptanceGates(min_trades=1, min_symbols_traded=1, min_profit_factor=0.0, max_drawdown_fraction=1.0)

    supervisor = CalibrationSupervisor(
        registry, SYMBOLS, symbol_bars, run_config=run_config, gates=gates,
        warmup=60, starting_equity=1_000_000.0,
        orchestrator_class=Revision2ExternalEngineOrchestrator,
    )

    t0 = time.time()
    result = supervisor.run()
    elapsed = time.time() - t0

    summary = {
        "engine": "Revision2ExternalEngineOrchestrator", "symbols": SYMBOLS, "tail_bars": TAIL_BARS,
        "elapsed_seconds": elapsed, "stopped_reason": result.stopped_reason,
        "candidates_evaluated": len(result.candidates),
        "candidates_by_phase": {},
        "best_score": result.best_score,
        "best_params": result.best_params,
        "best_report": {k: v for k, v in (result.best_report or {}).items() if k != "trades"},
    }
    from collections import Counter
    summary["candidates_by_phase"] = dict(Counter(c.phase for c in result.candidates))
    accepted = [c for c in result.candidates if c.accepted]
    summary["candidates_accepted"] = len(accepted)

    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nsaved to {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
