#!/usr/bin/env python3
"""Run a single-symbol curve-synchronizer experiment without trading."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import talib

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.curve_synchronizer_shadow import CurveSynchronizerShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--date", default="2023-09-01")
    parser.add_argument("--warmup", type=int, default=64)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    manifest = DatasetManifest.load(str(MANIFEST))
    verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(f"manifest verification failed: {verified.message}")
    frame = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)._load_symbol_csv(args.symbol)
    timezone = frame["timestamp"].dt.tz
    start = pd.Timestamp(args.date).tz_localize(timezone) if timezone is not None else pd.Timestamp(args.date)
    day = frame[(frame["timestamp"] >= start) & (frame["timestamp"] < start + pd.DateOffset(days=1))].reset_index(drop=True)
    if len(day) <= args.warmup + 1:
        raise RuntimeError(f"{args.symbol} has insufficient session bars")
    atr = talib.ATR(day["high"].to_numpy(float), day["low"].to_numpy(float), day["close"].to_numpy(float), timeperiod=14)
    ledger = StudyEntryShadowLedger()
    synchronizer = CurveSynchronizerShadow(ledger)
    for index in range(args.warmup, len(day)):
        bar = day.iloc[index]
        ledger.advance(args.symbol, index, bar["timestamp"], bar)
        if index >= len(day) - 1:
            continue
        current_atr = float(atr[index]) if pd.notna(atr[index]) else max(float(bar["high"] - bar["low"]), 0.001)
        synchronizer.observe(args.symbol, index, bar["timestamp"], bar, day.iloc[: index + 1], current_atr)
    ledger.finalize(args.symbol, day.iloc[-1]["timestamp"], day.iloc[-1])
    output = Path(args.output or ROOT / "diagnostic_output" / f"curve_synchronizer_shadow_{args.symbol}_{args.date.replace('-', '')}.json")
    artifact = {
        "run_type": "single_symbol_curve_synchronizer_shadow_replay", "symbol": args.symbol,
        "date": args.date, "warmup_bars": args.warmup,
        "dataset_manifest_hash": manifest.manifest_hash, "manifest_checked_files": verified.checked_files,
        "curve_shadow": ledger.summary(),
        # Keep every completed-bar sensor reading so phase claims can be
        # audited against the actual curve rather than inferred from totals.
        "curve_observations": ledger.observations,
        "note": "Observation-only: does not alter engine entries, sizing, gates, stops, targets, or PID gains.",
    }
    output.write_text(json.dumps(artifact, indent=2, default=str))
    summary = artifact["curve_shadow"]
    print(json.dumps({"output": str(output), "observations": summary["observations"], "setups": summary["setups"], "resolved": summary["resolved"], "target_before_stop": summary["target_before_stop"], "net_pnl_per_share": summary["net_pnl_per_share"]}, indent=2))


if __name__ == "__main__":
    main()
