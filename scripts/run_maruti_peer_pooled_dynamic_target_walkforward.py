#!/usr/bin/env python3
"""Paired, causal walk-forward test of MARUTI's frozen target proposal.

This is research infrastructure, not a promotion runner.  For each test
month, the dynamic arm is fitted only on data ending before that month; the
baseline arm uses the identical execution engine without a provider.  Both
arms are then replayed independently on the same monthly MARUTI bars.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.dynamic_target_setpoint import FrozenTargetSetpointProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


ROOT = Path(__file__).resolve().parents[1]
SEED_START = "2023-07-03"
LOCAL = "MARUTI"
PEERS = ("M&M", "BAJAJ-AUTO", "EICHERMOT")
SCHEDULE = (
    ("2023-09-01", "2023-10-01"),
    ("2023-10-01", "2023-11-01"),
    ("2023-11-01", "2023-12-01"),
    ("2023-12-01", "2024-01-01"),
    ("2024-01-01", "2024-02-01"),
)
METRICS = ("completed_trades", "gross_pnl", "net_pnl", "ending_equity", "mtm_max_drawdown_fraction")


def interval(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    tz = frame["timestamp"].dt.tz
    a, b = pd.Timestamp(start).tz_localize(tz), pd.Timestamp(end).tz_localize(tz)
    return frame[(frame["timestamp"] >= a) & (frame["timestamp"] < b)].reset_index(drop=True)


def usable_count(trades: list[dict[str, Any]]) -> int:
    return sum(
        trade.get("net_pnl", 0.0) > 0.0
        and trade.get("mfe_r") is not None
        and trade.get("mfe_r", 0.0) > 0.0
        and trade.get("bars_held") is not None
        for trade in trades
    )


def compact(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in METRICS}


def run_arm(bars: pd.DataFrame, provider: FrozenTargetSetpointProvider | None) -> dict[str, Any]:
    return Revision2ExternalEngineOrchestrator(
        [LOCAL],
        CanonicalParameterRegistry(),
        closed_loop_mode="active_paper",
        telemetry_mode="full",
        dynamic_target_setpoint_provider=provider,
        dynamic_target_setpoint_mode="paper_apply" if provider is not None else "shadow",
    ).run({LOCAL: bars}, warmup=60)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="diagnostic_output/maruti_peer_pooled_dynamic_target_walkforward_202309_202401.json")
    parser.add_argument("--months", type=int, default=len(SCHEDULE), choices=range(1, len(SCHEDULE) + 1))
    args = parser.parse_args()

    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
    verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(verified.message)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    raw = {symbol: loader._load_symbol_csv(symbol) for symbol in (LOCAL, *PEERS)}
    rows: list[dict[str, Any]] = []

    for index, (test_start, test_end) in enumerate(SCHEDULE[:args.months], start=1):
        print(f"[MONTH {index}/{args.months}] seed={SEED_START}..{test_start}; test={test_start}..{test_end}", flush=True)
        seed_trades: dict[str, list[dict[str, Any]]] = {}
        for symbol in (LOCAL, *PEERS):
            seed_bars = interval(raw[symbol], SEED_START, test_start)
            print(f"  [SEED] {symbol}: {len(seed_bars):,} bars", flush=True)
            seed_report = Revision2ExternalEngineOrchestrator(
                [symbol], CanonicalParameterRegistry(), telemetry_mode="compact",
            ).run({symbol: seed_bars}, warmup=60)
            seed_trades[symbol] = seed_report["trades"]

        local_seed = seed_trades[LOCAL]
        peer_seed = [trade for symbol in PEERS for trade in seed_trades[symbol]]
        provider = FrozenTargetSetpointProvider.fit(
            local_seed + peer_seed,
            seed_start=SEED_START,
            seed_end_exclusive=test_start,
        )
        test_bars = interval(raw[LOCAL], test_start, test_end)
        baseline = run_arm(test_bars, None)
        dynamic = run_arm(test_bars, provider)
        baseline_metrics, dynamic_metrics = compact(baseline), compact(dynamic)
        delta = {
            "net_pnl": dynamic_metrics["net_pnl"] - baseline_metrics["net_pnl"],
            "gross_pnl": dynamic_metrics["gross_pnl"] - baseline_metrics["gross_pnl"],
            "mtm_max_drawdown_fraction": dynamic_metrics["mtm_max_drawdown_fraction"] - baseline_metrics["mtm_max_drawdown_fraction"],
            "completed_trades": dynamic_metrics["completed_trades"] - baseline_metrics["completed_trades"],
        }
        rows.append({
            "test": [test_start, test_end],
            "seed": [SEED_START, test_start],
            "local_usable_cost_positive_paths": usable_count(local_seed),
            "peer_usable_cost_positive_paths": usable_count(peer_seed),
            "provider": provider.__dict__,
            "baseline": baseline_metrics,
            "dynamic": dynamic_metrics,
            "delta_dynamic_minus_baseline": delta,
            "dynamic_setpoint_event_count": sum(
                event["event_type"].startswith("DYNAMIC_TARGET_SETPOINT")
                for event in dynamic["controller_telemetry"]
            ),
        })
        print(json.dumps({"test": test_start, "provider_available": provider.target_r_quantile is not None, "delta": delta}, indent=2), flush=True)

    total = {
        arm: {metric: sum(row[arm][metric] for row in rows) for metric in ("completed_trades", "gross_pnl", "net_pnl")}
        for arm in ("baseline", "dynamic")
    }
    total["delta_dynamic_minus_baseline"] = {
        metric: total["dynamic"][metric] - total["baseline"][metric]
        for metric in total["baseline"]
    }
    artifact = {
        "research_boundary": "Paired causal walk-forward research. Dynamic geometry is unvalidated and cannot be promoted from this artifact.",
        "local": LOCAL,
        "peers": list(PEERS),
        "schedule": [{"seed_start": SEED_START, "test_start": start, "test_end_exclusive": end} for start, end in SCHEDULE[:args.months]],
        "months": rows,
        "aggregate": total,
        "decision_rule": "Require a favourable aggregate post-cost delta and stable month-level behaviour before a separately sealed confirmation run."
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "aggregate": total}, indent=2), flush=True)


if __name__ == "__main__":
    main()
