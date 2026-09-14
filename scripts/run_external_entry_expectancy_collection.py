#!/usr/bin/env python3
"""Collect compact, causal entry/outcome evidence from a canonical paper replay.

This runner changes no signal, target, stop, holding horizon, sizing, or
closed-loop authority.  It merely persists the pre-entry state of fills and
their later completed outcomes for offline, train-only entry-expectancy
research.  It is not a calibration, model-fitting, or promotion path.
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
    left, right = pd.Timestamp(start), pd.Timestamp(end_exclusive)
    if timezone is not None:
        left = left.tz_localize(timezone) if left.tzinfo is None else left.tz_convert(timezone)
        right = right.tz_localize(timezone) if right.tzinfo is None else right.tz_convert(timezone)
    return frame[(frame["timestamp"] >= left) & (frame["timestamp"] < right)].reset_index(drop=True)


def _artifact(report: dict, *, start: str, end_exclusive: str, manifest: DatasetManifest,
              verification: object, symbols: list[str]) -> dict:
    trades = report["trades"]
    evidence = report["entry_expectancy_evidence"]
    return {
        "run_type": "canonical_external_entry_expectancy_evidence_collection",
        "status": "EXECUTION_OBSERVED" if trades else "NO_EXECUTION",
        "research_boundary": (
            "Observational collection only. This artifact neither fits nor activates an entry model; "
            "use a future, unobserved chronological block for any train/validation/test decision."
        ),
        "period_start": start,
        "period_end_exclusive": end_exclusive,
        "execution_contract": {
            "parameters": "canonical defaults",
            "closed_loop_mode": "shadow",
            "telemetry_mode": "compact",
            "entry_expectancy_ledger": "trade-level causal observation only",
        },
        "symbols_loaded": symbols,
        "dataset_manifest_hash": manifest.manifest_hash,
        "dataset_verification": {"checked_files": verification.checked_files, "message": verification.message},
        "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
        "metrics": {key: report[key] for key in (
            "gross_pnl", "net_pnl", "ending_equity", "completed_trades", "mtm_max_drawdown_fraction",
        )},
        "exit_reasons": dict(sorted(Counter(str(trade["reason"]) for trade in trades).items())),
        "entry_expectancy_evidence": evidence,
        "entry_candidate_observations": report["entry_candidate_observations"],
        "trade_ledger": trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive exchange-local date, YYYY-MM-DD")
    parser.add_argument("--end-exclusive", required=True, help="exclusive exchange-local date, YYYY-MM-DD")
    parser.add_argument("--starting-equity", type=float, default=1_000_000.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if pd.Timestamp(args.end_exclusive) <= pd.Timestamp(args.start):
        raise ValueError("end-exclusive must be after start")

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
    print(f"[RUN] canonical shared-portfolio paper replay: {len(bars)} symbols, "
          f"{sum(len(frame) for frame in bars.values()):,} bars", flush=True)
    report = Revision2ExternalEngineOrchestrator(
        sorted(bars), CanonicalParameterRegistry(), starting_equity=args.starting_equity,
        closed_loop_mode="shadow", telemetry_mode="compact",
    ).run(bars, warmup=60)
    artifact = _artifact(report, start=args.start, end_exclusive=args.end_exclusive,
                         manifest=manifest, verification=verification, symbols=sorted(bars))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(output), **artifact["metrics"],
                      "resolved_entry_evidence": evidence_count(artifact)}, indent=2), flush=True)


def evidence_count(artifact: dict) -> int:
    return int(artifact["entry_expectancy_evidence"]["resolved_candidates"])


if __name__ == "__main__":
    main()
