#!/usr/bin/env python3
"""Real (not simulated) run of the external-library engine on INFY and
MARUTI TOGETHER, through the full portfolio-level orchestrator (all 10
boxes), across the FULL real dataset each symbol has (2023-07-03 to
2026-08-24, ~3.1 years -- the same "3-year" window this project has used
for its earlier single-symbol INFY/MARUTI runs, which also used the whole
dataset rather than a truncated slice).

Two symbols together, not run independently and added up: portfolio-level
boxes (PyPortfolioOpt position sizing, sector/exposure gates, the safety
contract) only genuinely engage with real cross-symbol behavior when more
than one symbol shares the same clock-tick loop.

Writes the full real trade ledger to disk as it's the honest source of
truth for what happened, not just the funnel summary.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUTPUT_DIR / "infy_maruti_3year_summary.json"

SYMBOLS = ["INFY", "MARUTI"]


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

    frames = {}
    for symbol in SYMBOLS:
        frame = loader._load_symbol_csv(symbol)
        frames[symbol] = frame
        print(f"{symbol}: {len(frame)} real bars, {frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]}", flush=True)

    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(SYMBOLS, registry, starting_equity=1_000_000.0)

    t0 = time.time()
    report = orch.run(frames, warmup=60)
    elapsed = time.time() - t0

    summary = {
        "symbols": SYMBOLS,
        "window": f"{frames['INFY']['timestamp'].iloc[0]} to {frames['INFY']['timestamp'].iloc[-1]}",
        "elapsed_seconds": elapsed,
        "bars_processed": report["bars_processed"], "pa_signals": report["pa_signals"],
        "id_approvals": report["id_approvals"], "id_rejections": report["id_rejections"],
        "mpc_plans": report["mpc_plans"], "safety_approvals": report["safety_approvals"],
        "safety_rejections": report["safety_rejections"], "gates_evaluated": report["gates_evaluated"],
        "gates_passed": report["gates_passed"], "gates_rejected": report["gates_rejected"],
        "fills": report["fills"], "completed_trades": report["completed_trades"],
        "gross_pnl": report["gross_pnl"], "net_pnl": report["net_pnl"],
        "ending_equity": report["ending_equity"], "mtm_max_drawdown_fraction": report["mtm_max_drawdown_fraction"],
        "config_hash": report["config_hash"],
        "trades": report["trades"],
    }
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k != "trades"}, indent=2, default=str), flush=True)
    print(f"\nsaved full trade ledger to {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    main()
