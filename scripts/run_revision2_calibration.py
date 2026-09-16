#!/usr/bin/env python3
"""Controlled command-line launcher for Revision 2 calibration.

The smoke profile proves the complete Random/TPE/CMA-ES/fine-tune path on
real frozen data.  Production selection deliberately remains fail-closed
until train/validation/test sealing is implemented; an unsealed three-year
run must not be presented as a calibrated production result.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.calibration_supervisor import (
    AcceptanceGates,
    CalibrationRunConfig,
    CalibrationSupervisor,
)
from revision2.dataset_manifest import DatasetManifest, verify_manifest

DEFAULT_MANIFEST = PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("smoke", "production"), default="smoke")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--checkpoint", type=Path, default=PROJECT_ROOT / "output" / "revision2_calibration_checkpoint.json")
    parser.add_argument("--summary", type=Path, default=PROJECT_ROOT / "output" / "revision2_calibration_summary.json")
    parser.add_argument("--max-bars", type=int, default=600, help="Trailing bars per symbol; smoke only")
    parser.add_argument("--symbol-limit", type=int, default=48, help="First N frozen symbols; smoke only")
    parser.add_argument("--phase1-trials", type=int, default=4)
    parser.add_argument("--phase2-generations", type=int, default=1)
    parser.add_argument("--phase3-iterations", type=int, default=2)
    parser.add_argument("--wall-clock-seconds", type=float)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_bars < 50 or not 1 <= args.symbol_limit <= 48:
        parser.error("--max-bars must be >= 50 and --symbol-limit must be 1..48")
    if min(args.phase1_trials, args.phase2_generations, args.phase3_iterations) < 0:
        parser.error("trial/generation/iteration counts cannot be negative")
    return args


def main() -> int:
    args = parse_args()
    if args.profile == "production":
        print(
            "REFUSED: production calibration is not sealed yet. The current Revision 2 "
            "supervisor accepts one caller-provided dataset and does not enforce immutable "
            "train/validation/untouched-test partitions or validate/freeze a winner. "
            "Use --profile smoke only until Stage E is implemented.",
            file=sys.stderr,
        )
        return 2

    manifest = DatasetManifest.load(str(args.manifest))
    verification = verify_manifest(manifest)
    if not verification.valid:
        print(f"REFUSED: {verification.message}", file=sys.stderr)
        return 2

    symbols = [record.symbol for record in manifest.files[: args.symbol_limit]]
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = {}
    for symbol in symbols:
        frame = loader._load_symbol_csv(symbol)
        if frame is None or not loader._validate_dataframe(frame):
            print(f"REFUSED: invalid or missing real data for {symbol}", file=sys.stderr)
            return 2
        bars[symbol] = frame.tail(args.max_bars).reset_index(drop=True)

    estimated_candidates = (
        args.phase1_trials
        + args.phase2_generations * (4 + int(3 * __import__("math").log(43)))
        + args.phase3_iterations + 1
    )
    preflight = {
        "profile": args.profile,
        "manifest_hash": manifest.manifest_hash,
        "verified_files": verification.checked_files,
        "symbols": len(symbols),
        "bars_per_symbol": {s: len(frame) for s, frame in bars.items()},
        "estimated_candidates": estimated_candidates,
        "checkpoint": str(args.checkpoint),
    }
    print(json.dumps(preflight, indent=2))
    if args.dry_run:
        return 0

    config = CalibrationRunConfig(
        phase1_trials=args.phase1_trials,
        phase2_generations=args.phase2_generations,
        phase3_iterations=args.phase3_iterations,
        wall_clock_budget_seconds=args.wall_clock_seconds,
        checkpoint_path=str(args.checkpoint),
        seed=args.seed,
    )
    supervisor = CalibrationSupervisor(
        CanonicalParameterRegistry(), symbols, bars, run_config=config,
        gates=AcceptanceGates.smoke_test_defaults(),
    )
    result = supervisor.run()
    summary = {
        **preflight,
        "stopped_reason": result.stopped_reason,
        "candidates_completed": len(result.candidates),
        "accepted_candidates": sum(candidate.accepted for candidate in result.candidates),
        "best_score": result.best_score if math.isfinite(result.best_score) else None,
        "best_params": result.best_params,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
