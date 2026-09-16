#!/usr/bin/env python3
"""Run one exchange-local session through the shared 48-symbol paper portfolio.

This is a fixed-configuration, historical paper replay.  It uses the active,
bounded closed-loop controls: controls may reduce new size and tighten an
existing stop, but cannot create risk, loosen a stop, or use future bars.
"""

from __future__ import annotations

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


def _local_day(frame: pd.DataFrame, date: str) -> pd.DataFrame:
    """Return exactly one local exchange day, preserving the source timezone."""
    timezone = frame["timestamp"].dt.tz
    start = pd.Timestamp(date)
    if timezone is not None:
        start = start.tz_localize(timezone)
    end = start + pd.DateOffset(days=1)
    return frame[(frame["timestamp"] >= start) & (frame["timestamp"] < end)].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2023-09-01", help="Exchange-local calendar date")
    parser.add_argument("--starting-equity", type=float, default=100_000.0)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    output = Path(args.output or PROJECT_ROOT / "diagnostic_output" / f"closed_loop_48symbol_{args.date.replace('-', '')}.json")
    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    print("[VERIFY] Re-hashing manifest-declared data files...", flush=True)
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(f"manifest verification failed: {verification.message}")

    print(f"[LOAD] Loading 48 symbols for {args.date}...", flush=True)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars: dict[str, pd.DataFrame] = {}
    missing: dict[str, int] = {}
    for index, record in enumerate(sorted(manifest.files, key=lambda item: item.symbol), start=1):
        frame = loader._load_symbol_csv(record.symbol)
        day = _local_day(frame, args.date)
        if len(day) > 60:
            bars[record.symbol] = day
        else:
            missing[record.symbol] = len(day)
        print(f"[LOAD {index:02d}/{len(manifest.files)}] {record.symbol}: {len(day)} bars", flush=True)
    if len(bars) < 2:
        raise RuntimeError(f"only {len(bars)} symbols contain >60 bars for {args.date}")

    print(
        f"[RUN] Shared-portfolio intraday paper replay: {len(bars)} symbols, "
        f"{sum(len(frame) for frame in bars.values()):,} bars, ₹{args.starting_equity:,.0f}",
        flush=True,
    )
    orchestrator = Revision2ExternalEngineOrchestrator(
        sorted(bars), CanonicalParameterRegistry(), starting_equity=args.starting_equity,
        closed_loop_mode="active_paper",
    )
    report = orchestrator.run(bars, warmup=60)

    trades_by_symbol: dict[str, list[dict]] = defaultdict(list)
    for trade in report["trades"]:
        trades_by_symbol[str(trade["symbol"])].append(trade)
    symbol_summary = {
        symbol: {
            "input_bars": len(frame),
            "completed_trades": len(trades_by_symbol[symbol]),
            "net_pnl": sum(float(trade["net_pnl"]) for trade in trades_by_symbol[symbol]),
        }
        for symbol, frame in sorted(bars.items())
    }
    events = report["controller_telemetry"]
    artifact = {
        "run_type": "shared_portfolio_48symbol_intraday_closed_loop_paper_replay",
        "status": "EXECUTION_OBSERVED" if report["completed_trades"] else "NO_EXECUTION",
        "closed_loop_mode": report["closed_loop_mode"],
        "date": args.date,
        "starting_equity": args.starting_equity,
        "symbols_loaded": sorted(bars),
        "symbols_without_60_bar_warmup": missing,
        "total_input_bars": sum(len(frame) for frame in bars.values()),
        "dataset_manifest_hash": manifest.manifest_hash,
        "dataset_verification": {"checked_files": verification.checked_files, "message": verification.message},
        "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
        "metrics": {key: report[key] for key in (
            "orders_submitted", "fills", "completed_trades", "net_pnl", "gross_pnl",
            "ending_equity", "mtm_max_drawdown_fraction",
        )},
        "funnel": {key: value for key, value in report.items() if key.endswith("rejections") or key.endswith("approvals") or key in {"pa_signals", "mpc_plans", "gates_passed", "gates_rejected"}},
        "controller_event_counts": dict(Counter(row["event_type"] for row in events)),
        "per_symbol": symbol_summary,
        "trades": report["trades"],
        "control_trace_events": [
            row for row in events if row["event_type"] in {
                "ENTRY_QUALITY_COMPARATOR", "PORTFOLIO_RISK_COMPARATOR", "DYNAMIC_SIZE_ACTUATION",
                "CLOSED_LOOP_ENTRY_SNAPSHOT", "TRADE_PATH_COMPARATOR", "TRADE_PATH_STOP_ACTUATION",
                "OUTCOME_LEDGER_UPDATE", "CONTROLLER_OUTCOME",
            }
        ],
        "note": "Historical paper replay only. This is not calibration, certification, or live-trading evidence.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "status": artifact["status"], **artifact["metrics"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
