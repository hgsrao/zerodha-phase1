#!/usr/bin/env python3
"""Real (not simulated) run of the external-library engine on INFY's first
six real calendar months (2023-07-03 to 2024-01-02), with the fixed
post-sizing cost-margin check. Reports the full, real trade ledger --
every completed trade's actual entry, exit, reason, and P&L, exactly as
the orchestrator itself produced them.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard  # noqa: F401 -- must import before pandas/numpy; see its module docstring
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
SUMMARY_PATH = OUTPUT_DIR / "infy_6month_summary.json"


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv("INFY")
    start = frame["timestamp"].iloc[0]
    cutoff = start + pd.DateOffset(months=6)
    frame = frame[frame["timestamp"] < cutoff].reset_index(drop=True)

    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)

    t0 = time.time()
    report = orch.run({"INFY": frame}, warmup=60)
    elapsed = time.time() - t0

    summary = {
        "symbol": "INFY", "window": f"{start} to {frame['timestamp'].iloc[-1]}",
        "bars_in_window": len(frame), "elapsed_seconds": elapsed,
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
    print(json.dumps({k: v for k, v in summary.items() if k != "trades"}, indent=2, default=str))
    print(f"\nsaved full trade ledger to {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
