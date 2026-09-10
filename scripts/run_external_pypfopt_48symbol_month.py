#!/usr/bin/env python3
"""Fixed-parameter 48-symbol report for the external PyPortfolioOpt engine.

This is deliberately *not* a calibration script.  It runs one declared
calendar month with manifest verification and saves the authoritative engine
report needed to compare a sizing implementation against an otherwise
identical revision.
"""

from __future__ import annotations

# Must be first: HMM and numerical results must not vary with BLAS threading.
import determinism_guard  # noqa: F401

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _month_bars(manifest: DatasetManifest, start: str) -> dict[str, pd.DataFrame]:
    """Load the same bounded interval for every symbol with real data."""
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    symbol_bars: dict[str, pd.DataFrame] = {}
    start_date = pd.Timestamp(start)
    for record in sorted(manifest.files, key=lambda item: item.symbol):
        frame = loader._load_symbol_csv(record.symbol)
        timezone = frame["timestamp"].dt.tz
        if start_date.tzinfo is None:
            interval_start = start_date.tz_localize(timezone) if timezone is not None else start_date
        elif timezone is not None:
            interval_start = start_date.tz_convert(timezone)
        else:
            interval_start = start_date.tz_localize(None)
        interval_end = interval_start + pd.DateOffset(months=1)
        bounded = frame[(frame["timestamp"] >= interval_start) & (frame["timestamp"] < interval_end)].reset_index(drop=True)
        if not bounded.empty:
            symbol_bars[record.symbol] = bounded
    return symbol_bars


def _sizing_summary(report: dict) -> dict:
    events = [
        row for row in report.get("controller_telemetry", [])
        if row.get("event_type") == "POSITION_SIZING"
    ]
    sized = [row for row in events if row.get("sizing_status") == "sized"]
    starved = [
        row for row in events
        if row.get("base_risk_budget", 0.0) > 0.0 and row.get("final_quantity", 0) == 0
    ]
    violations = [
        row for row in events
        if float(row.get("derated_risk_budget", 0.0)) > float(row.get("base_risk_budget", 0.0)) + 1e-9
    ]
    derates = [float(row["conviction_derate"]) for row in sized if "conviction_derate" in row]
    starvation_by_symbol = Counter(row["symbol"] for row in starved)
    return {
        "position_sizing_events": len(events),
        "sized_events": len(sized),
        "capital_starvation_events": len(starved),
        "capital_starvation_by_symbol": dict(sorted(starvation_by_symbol.items())),
        "risk_derate_violations": len(violations),
        "risk_geometry_intact": not violations,
        "conviction_derate": {
            "min": min(derates) if derates else None,
            "max": max(derates) if derates else None,
            "mean": sum(derates) / len(derates) if derates else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-07-03", help="First calendar day of the one-month interval")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "diagnostic_output" / "external_pypfopt_derater_20230703_1month.json"),
    )
    args = parser.parse_args()

    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(f"manifest verification failed: {verification.message}")

    bars = _month_bars(manifest, args.start)
    if len(bars) < 2:
        raise RuntimeError("fewer than two symbols have data in the requested month")

    registry = CanonicalParameterRegistry()
    orchestrator = Revision2ExternalEngineOrchestrator(
        sorted(bars), registry, starting_equity=1_000_000.0,
    )
    report = orchestrator.run(bars, warmup=60)
    sizing = _sizing_summary(report)
    if not sizing["risk_geometry_intact"]:
        raise RuntimeError("risk derater exceeded the ATR base risk budget")

    artifact = {
        "run_type": "fixed_parameter_shared_portfolio_measurement",
        "engine": "Revision2ExternalEngineOrchestrator",
        "period_start": args.start,
        "period_end_exclusive": str(pd.Timestamp(args.start) + pd.DateOffset(months=1)),
        "symbols_loaded": sorted(bars),
        "total_input_bars": sum(len(frame) for frame in bars.values()),
        "dataset_manifest_hash": manifest.manifest_hash,
        "dataset_verification": {
            "checked_files": verification.checked_files,
            "message": verification.message,
        },
        "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
        "metrics": {
            "net_pnl": report["net_pnl"],
            "gross_pnl": report["gross_pnl"],
            "ending_equity": report["ending_equity"],
            "completed_trades": report["completed_trades"],
            "mtm_max_drawdown_fraction": report["mtm_max_drawdown_fraction"],
        },
        "sizing": sizing,
        "report": report,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({
        "output": str(output),
        **artifact["metrics"],
        "capital_starvation_events": sizing["capital_starvation_events"],
        "risk_geometry_intact": sizing["risk_geometry_intact"],
    }, indent=2))


if __name__ == "__main__":
    main()
