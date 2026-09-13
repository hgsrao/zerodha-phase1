#!/usr/bin/env python3
"""Frozen MARUTI target/horizon paper check with pre-September auto peers."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.dynamic_target_setpoint import FrozenTargetSetpointProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

ROOT = Path(__file__).resolve().parents[1]
SEED_START, SEED_END, TEST_START, TEST_END = "2023-07-03", "2023-09-01", "2023-09-01", "2023-10-01"
LOCAL, PEERS = "MARUTI", ("M&M", "BAJAJ-AUTO", "EICHERMOT")


def interval(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    tz = frame["timestamp"].dt.tz
    a, b = pd.Timestamp(start).tz_localize(tz), pd.Timestamp(end).tz_localize(tz)
    return frame[(frame["timestamp"] >= a) & (frame["timestamp"] < b)].reset_index(drop=True)


def usable(trades: list[dict]) -> int:
    return sum(t.get("net_pnl", 0) > 0 and t.get("mfe_r") is not None and t.get("mfe_r", 0) > 0 for t in trades)


def main() -> None:
    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
    verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(verified.message)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    seed_trades: dict[str, list[dict]] = {}
    for symbol in (LOCAL, *PEERS):
        bars = interval(loader._load_symbol_csv(symbol), SEED_START, SEED_END)
        print(f"[SEED] {symbol}: {len(bars):,} bars", flush=True)
        report = Revision2ExternalEngineOrchestrator([symbol], CanonicalParameterRegistry(), telemetry_mode="compact").run({symbol: bars}, warmup=60)
        seed_trades[symbol] = report["trades"]
    local = seed_trades[LOCAL]
    prior = [trade for peer in PEERS for trade in seed_trades[peer]]
    provider = FrozenTargetSetpointProvider.fit(
        local + prior, seed_start=SEED_START, seed_end_exclusive=SEED_END,
    )
    test_bars = interval(loader._load_symbol_csv(LOCAL), TEST_START, TEST_END)
    print(f"[TEST] {LOCAL}: {len(test_bars):,} September bars", flush=True)
    report = Revision2ExternalEngineOrchestrator(
        [LOCAL], CanonicalParameterRegistry(), closed_loop_mode="active_paper", telemetry_mode="full",
        dynamic_target_setpoint_provider=provider, dynamic_target_setpoint_mode="paper_apply",
    ).run({LOCAL: test_bars}, warmup=60)
    artifact = {
        "research_boundary": "Pre-September peer-pooled frozen paper experiment; not promotion.",
        "seed": [SEED_START, SEED_END], "test": [TEST_START, TEST_END],
        "local": LOCAL, "peers": list(PEERS),
        "local_usable_cost_positive_paths": usable(local),
        "peer_usable_cost_positive_paths": usable(prior),
        "local_pool_weight_by_sample_count": usable(local) / max(1, usable(local) + usable(prior)),
        "provider": provider.__dict__,
        "report": {k: report[k] for k in ("completed_trades", "gross_pnl", "net_pnl", "ending_equity", "mtm_max_drawdown_fraction", "trades")},
        "setpoint_events": [x for x in report["controller_telemetry"] if x["event_type"].startswith("DYNAMIC_TARGET_SETPOINT")],
    }
    output = ROOT / "diagnostic_output/maruti_peer_pooled_dynamic_target_september_2023.json"
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "provider": provider.__dict__, "net_pnl": report["net_pnl"], "trades": report["completed_trades"]}, indent=2))


if __name__ == "__main__":
    main()
