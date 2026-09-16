#!/usr/bin/env python3
"""Multi-session, shadow-only audit of curve synchronizer → entry PID input."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import talib

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.curve_entry_pid_shadow import CurveEntryPidShadow
from revision2_external.curve_synchronizer_shadow import CurveSynchronizerShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _session(frame: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    return frame[(frame.timestamp >= start) & (frame.timestamp < start + pd.DateOffset(days=1))].reset_index(drop=True)


def _run_session(symbol: str, day: pd.DataFrame, phase_center: float, phase_tolerance: float, relative_tolerance: float, warmup: int) -> dict:
    atr = talib.ATR(day.high.to_numpy(float), day.low.to_numpy(float), day.close.to_numpy(float), timeperiod=14)
    ledger = StudyEntryShadowLedger()
    pid = CurveEntryPidShadow(phase_center, phase_tolerance, relative_tolerance)
    sync = CurveSynchronizerShadow(ledger, phase_center_degrees=phase_center, phase_tolerance_degrees=phase_tolerance, entry_pid=pid)
    for index in range(warmup, len(day)):
        bar = day.iloc[index]
        ledger.advance(symbol, index, bar.timestamp, bar)
        if index < len(day) - 1:
            current_atr = float(atr[index]) if pd.notna(atr[index]) else max(float(bar.high - bar.low), .001)
            sync.observe(symbol, index, bar.timestamp, bar, day.iloc[: index + 1], current_atr)
    ledger.finalize(symbol, day.iloc[-1].timestamp, day.iloc[-1])
    ready = [x["entry_pid"] for x in ledger.observations if x.get("entry_pid", {}).get("entry_pid_ready")]
    return {
        "date": str(day.iloc[0].timestamp.date()), "bars": len(day), "pid_ready_bars": len(ready),
        "phase_in_range": sum(x["phase_in_range"] for x in ready),
        "voltage_in_range": sum(x["voltage_in_range"] for x in ready),
        "frequency_in_range": sum(x["frequency_in_range"] for x in ready),
        "fully_synchronized": sum(x["synchronized"] for x in ready),
        "entry_multiplier_min": min((x["entry_timing_multiplier"] for x in ready), default=None),
        "entry_multiplier_max": max((x["entry_timing_multiplier"] for x in ready), default=None),
        "setups": ledger.summary()["setups"], "resolved": ledger.summary()["resolved"],
        "target_before_stop": ledger.summary()["target_before_stop"], "net_pnl_per_share": ledger.summary()["net_pnl_per_share"],
        "outcomes": ledger.summary()["outcomes"],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="SUNPHARMA"); p.add_argument("--start", default="2023-09-01")
    p.add_argument("--calendar-days", type=int, default=7); p.add_argument("--phase-center", type=float, default=0.0)
    p.add_argument("--phase-tolerance", type=float, default=20.0); p.add_argument("--relative-tolerance", type=float, default=.10)
    p.add_argument("--warmup", type=int, default=124); p.add_argument("--output", default=None)
    args = p.parse_args()
    manifest = DatasetManifest.load(str(MANIFEST)); verified = verify_manifest(manifest)
    if not verified.valid: raise RuntimeError(verified.message)
    frame = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)._load_symbol_csv(args.symbol)
    tz = frame.timestamp.dt.tz; start = pd.Timestamp(args.start).tz_localize(tz) if tz is not None else pd.Timestamp(args.start)
    sessions = []
    for offset in range(args.calendar_days):
        day = _session(frame, start + pd.DateOffset(days=offset))
        if len(day) > args.warmup + 1:
            sessions.append(_run_session(args.symbol, day, args.phase_center, args.phase_tolerance, args.relative_tolerance, args.warmup))
    totals = Counter()
    for row in sessions:
        for key in ("bars", "pid_ready_bars", "phase_in_range", "voltage_in_range", "frequency_in_range", "fully_synchronized", "setups", "resolved", "target_before_stop"):
            totals[key] += row[key]
    totals["net_pnl_per_share"] = sum(row["net_pnl_per_share"] for row in sessions)
    artifact = {
        "run_type": "curve_synchronizer_entry_pid_shadow_week", "symbol": args.symbol,
        "start": args.start, "calendar_days": args.calendar_days, "manifest_hash": manifest.manifest_hash,
        "ranges": {"phase_center_degrees": args.phase_center, "phase_plus_minus_degrees": args.phase_tolerance, "voltage_plus_minus_percent_of_causal_median": args.relative_tolerance * 100, "frequency_plus_minus_percent_of_causal_median": args.relative_tolerance * 100},
        "pid": {"kp": .35, "ki": .02, "kd": .05, "gain_scheduling": False, "note": "Inputs, references, integral state, and output are dynamic; gains are fixed."},
        "sessions": sessions, "totals": dict(totals),
        "note": "Shadow-only. No entry, size, stop, target, or active PID gain changes are permitted by this run.",
    }
    output = Path(args.output or ROOT / "diagnostic_output" / f"curve_entry_pid_shadow_{args.symbol}_{args.start.replace('-', '')}_{args.calendar_days}d.json")
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "sessions": len(sessions), "totals": dict(totals)}, indent=2))

if __name__ == "__main__": main()
