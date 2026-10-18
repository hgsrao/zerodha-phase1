#!/usr/bin/env python3
"""Run a frozen joint-feature model against a fresh July 2025 holdout.

Train = Jan-Mar and validation = April from the already sealed evidence table.
May and June are intentionally excluded: they informed earlier research.
July is loaded only after the model contract and train/validation report exist.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.frozen_joint_probability import MODEL_FEATURES, fit_train_only, score_frozen
from revision2_external.triple_barrier_discovery import extract_day, summarize

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"
EVIDENCE = ROOT / "diagnostic_output" / "feature_discovery_2025_summary.rows.csv.gz"


def _july_rows(manifest: DatasetManifest) -> list[dict]:
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    rows: list[dict] = []
    for number, record in enumerate(sorted(manifest.files, key=lambda item: item.symbol), 1):
        frame = loader._load_symbol_csv(record.symbol)
        timezone = frame.timestamp.dt.tz
        start, end = pd.Timestamp("2025-07-01"), pd.Timestamp("2025-08-01")
        if timezone is not None:
            start, end = start.tz_localize(timezone), end.tz_localize(timezone)
        part = frame[(frame.timestamp >= start) & (frame.timestamp < end)]
        print(f"[LOAD {number:02d}/{len(manifest.files)}] {record.symbol}", flush=True)
        for _, day in part.groupby(part.timestamp.dt.date, sort=True):
            rows.extend(extract_day(record.symbol, day))
    return rows


def _serializable(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "model"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(EVIDENCE))
    parser.add_argument("--output", default=str(ROOT / "diagnostic_output" / "july_2025_joint_probability_holdout.json"))
    args = parser.parse_args()

    columns = ["split", "label", "side", "time_of_day", *MODEL_FEATURES[:-2]]
    evidence = pd.read_csv(args.input, usecols=columns, compression="gzip")
    train = evidence[evidence.split == "train"]
    validation = evidence[evidence.split == "validation"]
    # Fit and freeze before July is read from the manifest.
    frozen = fit_train_only(train)
    preliminary = score_frozen(frozen, [("validation", validation)])

    manifest = DatasetManifest.load(str(MANIFEST))
    verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(verified.message)
    july = pd.DataFrame(_july_rows(manifest))
    final = score_frozen(frozen, [("validation", validation), ("july_holdout", july)])
    if final["train_score_decile_edges"] != preliminary["train_score_decile_edges"]:
        raise RuntimeError("model state changed after July was loaded")
    report = {
        "run_type": "frozen_joint_probability_july_2025_holdout",
        "selection_boundary": "Fit Jan-Mar 2025; inspect April validation; May/June excluded; July first read after model freeze.",
        "manifest_hash": manifest.manifest_hash,
        "july_all_rows": summarize(july.to_dict("records")),
        **_serializable(final),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps({"output": str(output), "july_rows": len(july)}, indent=2))


if __name__ == "__main__":
    main()
