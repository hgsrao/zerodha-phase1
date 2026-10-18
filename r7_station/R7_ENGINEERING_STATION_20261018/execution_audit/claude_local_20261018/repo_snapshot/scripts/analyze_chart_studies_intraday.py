#!/usr/bin/env python3
"""Read-only audit of the chart-studies composite on one real trading day.

This script deliberately does not call the orchestrator or place simulated
orders. It evaluates the same stateful composite sequentially and labels each
observation with later price movement only for research attribution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.composite_study_signal import CompositeStudySignal


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--date", default="2023-09-01")
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--forward-bars", type=int, default=30)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    manifest = DatasetManifest.load(str(MANIFEST))
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(f"manifest verification failed: {verification.message}")
    frame = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)._load_symbol_csv(args.symbol)
    timezone = frame["timestamp"].dt.tz
    start = pd.Timestamp(args.date).tz_localize(timezone) if timezone is not None else pd.Timestamp(args.date)
    day = frame[(frame["timestamp"] >= start) & (frame["timestamp"] < start + pd.DateOffset(days=1))].reset_index(drop=True)
    if len(day) <= args.warmup + 1:
        raise RuntimeError("insufficient bars for requested warmup")

    studies = CompositeStudySignal()
    observations: list[dict] = []
    for index in range(args.warmup, len(day) - 1):
        observation = studies.evaluate(args.symbol, day.iloc[: index + 1])
        close = float(day.iloc[index]["close"])
        future = day.iloc[index + 1 : min(len(day), index + 1 + args.forward_bars)]
        observations.append({
            "timestamp": str(day.iloc[index]["timestamp"]),
            "close": close,
            "direction": int(observation["direction"]),
            "confidence": float(observation["confidence"]),
            "weighted_score": float(observation["weighted_score"]),
            "votes": observation["votes"],
            "weights": observation["weights"],
            "hit_rates": observation["hit_rates"],
            # Future labels are explicitly research-only and are never made
            # available to CompositeStudySignal or the engine.
            "research_next_high_return_pct": (
                (float(future["high"].max()) / close - 1.0) * 100 if not future.empty else None
            ),
            "research_next_low_return_pct": (
                (float(future["low"].min()) / close - 1.0) * 100 if not future.empty else None
            ),
        })

    low_index = int(day["low"].idxmin())
    low_time = str(day.iloc[low_index]["timestamp"])
    low_timestamp = pd.Timestamp(low_time)
    neighborhood = [
        row for row in observations
        if low_timestamp - pd.Timedelta(minutes=5) <= pd.Timestamp(row["timestamp"])
        <= low_timestamp + pd.Timedelta(minutes=5)
    ]
    direction_summary = {}
    for direction, label in ((1, "bullish"), (0, "neutral"), (-1, "bearish")):
        rows = [row for row in observations if row["direction"] == direction]
        direction_summary[label] = {
            "observations": len(rows),
            "mean_next_high_return_pct": (
                sum(row["research_next_high_return_pct"] for row in rows if row["research_next_high_return_pct"] is not None)
                / sum(row["research_next_high_return_pct"] is not None for row in rows)
                if any(row["research_next_high_return_pct"] is not None for row in rows) else None
            ),
        }
    output = Path(args.output or ROOT / "diagnostic_output" / f"chart_studies_{args.symbol}_{args.date.replace('-', '')}.json")
    artifact = {
        "run_type": "chart_studies_intraday_read_only_attribution",
        "symbol": args.symbol,
        "date": args.date,
        "warmup_bars": args.warmup,
        "forward_label_bars": args.forward_bars,
        "dataset_manifest_hash": manifest.manifest_hash,
        "manifest_checked_files": verification.checked_files,
        "day_low": {"timestamp": low_time, "price": float(day.iloc[low_index]["low"])},
        "observations_near_day_low": neighborhood,
        "direction_summary": direction_summary,
        "observations": observations,
        "note": "Future return fields are retrospective labels only, excluded from the studies/controller inputs.",
    }
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({
        "output": str(output), "day_low": artifact["day_low"],
        "near_low_observations": neighborhood,
        "direction_summary": direction_summary,
    }, indent=2))


if __name__ == "__main__":
    main()
