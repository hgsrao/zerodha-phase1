#!/usr/bin/env python3
"""Observe curve ranges plus a cost-inclusive daily P&L feedback band."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import talib

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.curve_synchronizer_shadow import CurveSynchronizerShadow
from revision2_external.intraday_pnl_setpoint_shadow import IntradayNetPnlBandShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--date", default="2023-09-01")
    parser.add_argument("--target", type=float, default=50.0)
    parser.add_argument("--loss-limit", type=float, default=50.0)
    parser.add_argument("--quantity", type=int, default=10)
    parser.add_argument("--phase-center", type=float, default=0.0)
    parser.add_argument("--phase-tolerance", type=float, default=10.0)
    parser.add_argument("--amplitude-min", type=float, default=0.10)
    parser.add_argument("--amplitude-max", type=float, default=2.00)
    parser.add_argument("--frequency-min", type=float, default=1.0)
    parser.add_argument("--frequency-max", type=float, default=20.0)
    parser.add_argument("--warmup", type=int, default=64)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    manifest = DatasetManifest.load(str(MANIFEST)); verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(f"manifest verification failed: {verified.message}")
    frame = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)._load_symbol_csv(args.symbol)
    tz = frame["timestamp"].dt.tz; start = pd.Timestamp(args.date).tz_localize(tz) if tz is not None else pd.Timestamp(args.date)
    day = frame[(frame.timestamp >= start) & (frame.timestamp < start + pd.DateOffset(days=1))].reset_index(drop=True)
    atr = talib.ATR(day.high.to_numpy(float), day.low.to_numpy(float), day.close.to_numpy(float), timeperiod=14)
    controller = IntradayNetPnlBandShadow(args.target, args.loss_limit, args.quantity)
    ledger = StudyEntryShadowLedger(outcome_listener=controller)
    sync = CurveSynchronizerShadow(ledger, phase_center_degrees=args.phase_center, phase_tolerance_degrees=args.phase_tolerance, amplitude_range_atr=(args.amplitude_min, args.amplitude_max), phase_velocity_range=(args.frequency_min, args.frequency_max), admission_policy=controller)
    for index in range(args.warmup, len(day)):
        bar = day.iloc[index]; ledger.advance(args.symbol, index, bar.timestamp, bar)
        if index < len(day) - 1:
            value = float(atr[index]) if pd.notna(atr[index]) else max(float(bar.high - bar.low), 0.001)
            sync.observe(args.symbol, index, bar.timestamp, bar, day.iloc[:index + 1], value)
    ledger.finalize(args.symbol, day.iloc[-1].timestamp, day.iloc[-1])
    result = {
        "run_type": "curve_range_and_daily_net_pnl_setpoint_shadow", "symbol": args.symbol, "date": args.date,
        "manifest_hash": manifest.manifest_hash, "checked_files": verified.checked_files,
        "curve_ranges": {"phase_center_degrees": args.phase_center, "phase_tolerance_degrees": args.phase_tolerance, "amplitude_atr": [args.amplitude_min, args.amplitude_max], "phase_velocity_degrees_per_bar": [args.frequency_min, args.frequency_max]},
        "daily_net_pnl_band": {"profit_target_rupees_after_costs": args.target, "loss_limit_rupees_after_costs": -args.loss_limit, "shadow_quantity": args.quantity, "final_net_pnl_rupees": controller.net_pnl_rupees, "final_state": controller.state, "feedback_events": controller.events},
        "curve_shadow": ledger.summary(), "curve_observations": ledger.observations,
        "note": "Shadow-only. Daily P&L feedback never forces an entry or increases sizing; charges are included in each realised net P&L.",
    }
    output = Path(args.output or ROOT / "diagnostic_output" / f"curve_pid_setpoint_shadow_{args.symbol}_{args.date.replace('-', '')}_t{int(args.target)}.json")
    output.write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({"output": str(output), "setups": result["curve_shadow"]["setups"], "resolved": result["curve_shadow"]["resolved"], "final_net_pnl_rupees": controller.net_pnl_rupees, "final_state": controller.state, "feedback_events": len(controller.events)}, indent=2))


if __name__ == "__main__":
    main()
