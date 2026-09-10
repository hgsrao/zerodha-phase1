#!/usr/bin/env python3
"""Run one real symbol/session through active bounded closed-loop paper logic."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--date", default="2023-09-01", help="Exchange-local calendar date")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    output = Path(args.output or PROJECT_ROOT / "diagnostic_output" / f"closed_loop_{args.symbol}_{args.date.replace('-', '')}.json")

    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(f"manifest verification failed: {verification.message}")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = loader._load_symbol_csv(args.symbol)
    timezone = bars["timestamp"].dt.tz
    start = pd.Timestamp(args.date).tz_localize(timezone) if timezone is not None else pd.Timestamp(args.date)
    end = start + pd.DateOffset(days=1)
    day = bars[(bars["timestamp"] >= start) & (bars["timestamp"] < end)].reset_index(drop=True)
    if len(day) <= 60:
        raise RuntimeError(f"{args.symbol} {args.date} has only {len(day)} bars; need >60 for warmup")

    orchestrator = Revision2ExternalEngineOrchestrator(
        [args.symbol], CanonicalParameterRegistry(), starting_equity=100_000.0,
        closed_loop_mode="active_paper",
    )
    report = orchestrator.run({args.symbol: day}, warmup=60)
    events = report["controller_telemetry"]
    artifact = {
        "run_type": "single_symbol_intraday_closed_loop_paper_replay",
        "status": "EXECUTION_OBSERVED" if report["completed_trades"] else "NO_EXECUTION",
        "closed_loop_mode": report["closed_loop_mode"],
        "symbol": args.symbol,
        "date": args.date,
        "dataset_manifest_hash": manifest.manifest_hash,
        "manifest_checked_files": verification.checked_files,
        "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
        "metrics": {key: report[key] for key in ("orders_submitted", "fills", "completed_trades", "net_pnl", "gross_pnl", "ending_equity", "mtm_max_drawdown_fraction")},
        "controller_event_counts": dict(Counter(row["event_type"] for row in events)),
        "trades": report["trades"],
        "control_trace_events": [
            row for row in events if row["event_type"] in {
                "ENTRY_CONFIDENCE_THROTTLE", "EXIT_PROTECTION_UPDATE", "CONTROLLER_OUTCOME",
                "ENTRY_QUALITY_COMPARATOR", "PORTFOLIO_RISK_COMPARATOR", "DYNAMIC_SIZE_ACTUATION",
                "CLOSED_LOOP_ENTRY_SNAPSHOT", "TRADE_PATH_COMPARATOR", "TRADE_PATH_STOP_ACTUATION",
                "OUTCOME_LEDGER_UPDATE",
            }
        ],
        "note": "Paper replay only. Dynamic controls are bounded: entry/portfolio only reduce size; path loop only tightens stops.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "status": artifact["status"], "metrics": artifact["metrics"], "events": artifact["controller_event_counts"]}, indent=2))


if __name__ == "__main__":
    main()
