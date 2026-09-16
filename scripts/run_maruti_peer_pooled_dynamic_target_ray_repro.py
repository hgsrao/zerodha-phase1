#!/usr/bin/env python3
"""Ray scheduler reproducibility check for the MARUTI walk-forward study.

This is deliberately not an optimizer.  It executes the exact same five
month-pairs as the sequential runner, with independently loaded worker data,
then compares all decision-level metrics against the completed sequential
artifact.  It never contacts a broker or an external trading sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import ray

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from scripts.run_maruti_peer_pooled_dynamic_target_walkforward import (
    LOCAL,
    PEERS,
    ROOT,
    SCHEDULE,
    build_artifact,
    run_month,
)


@ray.remote(num_cpus=1)
def reproduce_month(test_start: str, test_end: str) -> dict[str, Any]:
    """Worker-local manifest verification and data loading; no ray.get(dict)."""
    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
    verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(verified.message)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    raw = {symbol: loader._load_symbol_csv(symbol) for symbol in (LOCAL, *PEERS)}
    return run_month(raw, test_start, test_end)


def _comparison(sequential: dict[str, Any], ray_artifact: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "completed_trades", "gross_pnl", "net_pnl", "ending_equity",
        "mtm_max_drawdown_fraction",
    )
    mismatches: list[dict[str, Any]] = []
    for expected, observed in zip(sequential["months"], ray_artifact["months"], strict=True):
        for arm in ("baseline", "dynamic"):
            for field in fields:
                if expected[arm][field] != observed[arm][field]:
                    mismatches.append({
                        "test_start": expected["test"][0], "arm": arm, "field": field,
                        "sequential": expected[arm][field], "ray": observed[arm][field],
                    })
        if expected["provider"] != observed["provider"]:
            mismatches.append({"test_start": expected["test"][0], "field": "provider", "sequential": expected["provider"], "ray": observed["provider"]})
    return {"exact_metrics_match": not mismatches, "mismatch_count": len(mismatches), "mismatches": mismatches}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=len(SCHEDULE), choices=range(1, len(SCHEDULE) + 1))
    parser.add_argument("--max-concurrent", type=int, default=2, choices=(1, 2))
    parser.add_argument("--sequential-artifact", default="diagnostic_output/maruti_peer_pooled_dynamic_target_walkforward_202309_202401.json")
    parser.add_argument("--output", default="diagnostic_output/maruti_peer_pooled_dynamic_target_ray_repro_202309_202401.json")
    args = parser.parse_args()
    sequential = json.loads((ROOT / args.sequential_artifact).read_text())
    if len(sequential.get("months", [])) < args.months:
        raise ValueError("sequential artifact does not contain the requested number of months")

    # Numerical libraries otherwise oversubscribe the host inside each Ray
    # worker.  Two independent one-core workers is the strict upper bound.
    runtime_env = {"env_vars": {name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}
    ray.init(num_cpus=args.max_concurrent, include_dashboard=False, log_to_driver=True, runtime_env=runtime_env)
    try:
        refs = [reproduce_month.remote(start, end) for start, end in SCHEDULE[:args.months]]
        # refs is a list[ObjectRef], the only valid ray.get input here.
        rows = ray.get(refs)
    finally:
        ray.shutdown()
    artifact = build_artifact(rows, args.months)
    artifact.update({
        "run_type": "ray_scheduler_reproducibility_check",
        "scheduler": {"name": "Ray", "max_concurrent_workers": args.max_concurrent, "worker_data_policy": "manifest-verified worker-local loads"},
        "comparison_to_sequential": _comparison(sequential, artifact),
    })
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "comparison_to_sequential": artifact["comparison_to_sequential"], "aggregate": artifact["aggregate"]}, indent=2))


if __name__ == "__main__":
    main()
