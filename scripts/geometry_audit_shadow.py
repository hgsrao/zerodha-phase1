#!/usr/bin/env python3
"""July-only shadow audit of predeclared payoff geometries for frozen top scores.

July is already observed and therefore serves only as geometry-selection
research. This program neither reads August nor changes an engine entry rule.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.frozen_joint_probability import fit_train_only, predict_frozen
from revision2_external.geometry_audit_shadow import evaluate_path, summarize_geometry
from revision2_external.triple_barrier_discovery import HORIZON_BARS, extract_day

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"
EVIDENCE = ROOT / "diagnostic_output" / "feature_discovery_2025_summary.rows.csv.gz"
GEOMETRIES = ((0.4, 1.0), (0.6, 1.0), (0.8, 1.0), (1.0, 1.0))


def _july_bounds(frame: pd.DataFrame) -> pd.DataFrame:
    timezone = frame.timestamp.dt.tz
    start, end = pd.Timestamp("2025-07-01"), pd.Timestamp("2025-08-01")
    if timezone is not None:
        start, end = start.tz_localize(timezone), end.tz_localize(timezone)
    return frame[(frame.timestamp >= start) & (frame.timestamp < end)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(EVIDENCE))
    parser.add_argument("--output", default=str(ROOT / "diagnostic_output" / "july_2025_geometry_audit_shadow.json"))
    args = parser.parse_args()

    evidence = pd.read_csv(args.input, compression="gzip")
    frozen = fit_train_only(evidence[evidence.split == "train"])
    top_decile_floor = float(frozen.train_score_decile_edges[-2])

    manifest = DatasetManifest.load(str(MANIFEST))
    verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(verified.message)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    outcomes = {f"target_{target:.1f}_stop_{stop:.1f}": [] for target, stop in GEOMETRIES}
    selected = 0

    for number, record in enumerate(sorted(manifest.files, key=lambda item: item.symbol), 1):
        frame = _july_bounds(loader._load_symbol_csv(record.symbol))
        print(f"[LOAD {number:02d}/{len(manifest.files)}] {record.symbol}", flush=True)
        for _, day in frame.groupby(frame.timestamp.dt.date, sort=True):
            day = day.reset_index(drop=True)
            rows = extract_day(record.symbol, day)
            if not rows:
                continue
            candidates = pd.DataFrame(rows)
            probabilities = predict_frozen(frozen, candidates)
            for row_index, probability in probabilities.items():
                if float(probability) < top_decile_floor:
                    continue
                row = candidates.loc[row_index]
                future = day.iloc[int(row.bar_index) + 2:int(row.bar_index) + 2 + HORIZON_BARS]
                if len(future) != HORIZON_BARS:
                    continue
                selected += 1
                for target, stop in GEOMETRIES:
                    key = f"target_{target:.1f}_stop_{stop:.1f}"
                    outcomes[key].append(evaluate_path(
                        entry_price=float(row.entry_reference), initial_risk=float(row.barrier_price),
                        side=str(row.side), future=future, target_r=target, stop_r=stop,
                    ))

    report = {
        "run_type": "july_2025_path_aware_geometry_audit_shadow",
        "selection_boundary": "Frozen Jan-Mar model; July top-score candidates; July may select at most one geometry; August remains unread.",
        "manifest_hash": manifest.manifest_hash,
        "top_decile_floor_from_train": top_decile_floor,
        "selected_candidate_paths": selected,
        "geometry_contract": {
            "geometries": [{"target_r": target, "stop_r": stop} for target, stop in GEOMETRIES],
            "entry": "existing next-bar adverse paper fill",
            "path": "chronological OHLC; target/stop dual touches excluded as intrabar-order-unknown",
            "costs": "existing adverse fills and two-leg StudyEntryShadowLedger costs",
            "execution": "shadow only; no entry, exit, PID, sizing, or safety behavior is changed",
        },
        "results": {key: summarize_geometry(value) for key, value in outcomes.items()},
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "selected_candidate_paths": selected, "results": report["results"]}, indent=2))


if __name__ == "__main__":
    main()
