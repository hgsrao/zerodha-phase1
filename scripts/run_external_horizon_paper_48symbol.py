#!/usr/bin/env python3
"""Run one fixed maximum-hold horizon through an external paper replay.

This is a diagnostic measurement runner, not a calibration or promotion path.
It changes only ``max_hold_bars``; execution costs, safety gates, signals,
targets, stops, and sizing remain the canonical external-engine defaults.
"""
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


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _interval(frame: pd.DataFrame, start: str, end_exclusive: str) -> pd.DataFrame:
    timezone = frame["timestamp"].dt.tz
    left = pd.Timestamp(start)
    right = pd.Timestamp(end_exclusive)
    if timezone is not None:
        left = left.tz_localize(timezone) if left.tzinfo is None else left.tz_convert(timezone)
        right = right.tz_localize(timezone) if right.tzinfo is None else right.tz_convert(timezone)
    return frame[(frame["timestamp"] >= left) & (frame["timestamp"] < right)].reset_index(drop=True)


def _funnel(report: dict) -> dict:
    return {
        key: value for key, value in report.items()
        if key.endswith("_approvals") or key.endswith("_rejections")
        or key in {"bars_processed", "pa_signals", "mpc_plans", "gates_evaluated", "gates_passed", "gates_rejected"}
    }


def _artifact(report: dict, *, start: str, end_exclusive: str, max_hold_bars: int,
              manifest: DatasetManifest, verification: object, symbols: list[str]) -> dict:
    trades = report["trades"]
    holds = [int(trade["bars_held"]) for trade in trades if trade.get("bars_held") is not None]
    return {
        "run_type": "fixed_horizon_external_48symbol_paper_replay",
        "status": "EXECUTION_OBSERVED" if trades else "NO_EXECUTION",
        "research_boundary": "Diagnostic only. No calibration, promotion, or live-trading inference.",
        "period_start": start,
        "period_end_exclusive": end_exclusive,
        "override_contract": {
            "changed_parameter": "max_hold_bars",
            "value": max_hold_bars,
            "all_other_parameters": "canonical defaults",
            "closed_loop_mode": "shadow",
            "telemetry_mode": "compact",
        },
        "symbols_loaded": symbols,
        "dataset_manifest_hash": manifest.manifest_hash,
        "dataset_verification": {
            "checked_files": verification.checked_files,
            "message": verification.message,
        },
        "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
        "metrics": {
            key: report[key] for key in (
                "gross_pnl", "net_pnl", "ending_equity", "completed_trades", "mtm_max_drawdown_fraction",
            )
        },
        "funnel": _funnel(report),
        "exit_reasons": dict(sorted(Counter(str(trade["reason"]) for trade in trades).items())),
        "holding_bars": {
            "minimum": min(holds) if holds else None,
            "maximum": max(holds) if holds else None,
            "mean": sum(holds) / len(holds) if holds else None,
        },
        "controller_telemetry_summary": report["controller_telemetry_summary"],
        "trade_ledger": trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive exchange-local date, YYYY-MM-DD")
    parser.add_argument("--end-exclusive", required=True, help="exclusive exchange-local date, YYYY-MM-DD")
    parser.add_argument("--max-hold-bars", type=int, required=True)
    parser.add_argument("--starting-equity", type=float, default=1_000_000.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if pd.Timestamp(args.end_exclusive) <= pd.Timestamp(args.start):
        raise ValueError("end-exclusive must be after start")

    registry = CanonicalParameterRegistry()
    errors = registry.validate_calibration_payload({"max_hold_bars": args.max_hold_bars})
    if errors:
        raise ValueError(f"invalid max_hold_bars: {errors}")
    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    print("[VERIFY] Re-hashing manifest-declared data files...", flush=True)
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(f"manifest verification failed: {verification.message}")

    print(f"[LOAD] {args.start} through {args.end_exclusive} for 48 symbols...", flush=True)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = {
        record.symbol: selected
        for record in sorted(manifest.files, key=lambda item: item.symbol)
        if len(selected := _interval(loader._load_symbol_csv(record.symbol), args.start, args.end_exclusive)) > 60
    }
    if len(bars) < 2:
        raise RuntimeError("fewer than two symbols have sufficient bars")
    print(f"[RUN] {len(bars)} symbols, {sum(len(frame) for frame in bars.values()):,} bars, max_hold={args.max_hold_bars}", flush=True)
    report = Revision2ExternalEngineOrchestrator(
        sorted(bars), registry, calibration_overrides={"max_hold_bars": args.max_hold_bars},
        starting_equity=args.starting_equity, closed_loop_mode="shadow", telemetry_mode="compact",
    ).run(bars, warmup=60)
    artifact = _artifact(
        report, start=args.start, end_exclusive=args.end_exclusive, max_hold_bars=args.max_hold_bars,
        manifest=manifest, verification=verification, symbols=sorted(bars),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(output), **artifact["metrics"], "exit_reasons": artifact["exit_reasons"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
