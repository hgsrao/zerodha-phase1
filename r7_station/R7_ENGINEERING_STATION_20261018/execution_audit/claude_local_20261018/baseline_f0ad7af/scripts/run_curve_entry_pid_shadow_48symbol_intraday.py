#!/usr/bin/env python3
"""48-symbol, one-session curve-synchronizer → entry-PID shadow audit.

Per-symbol PID state and voltage/frequency references are deliberately
isolated. This is not a shared portfolio replay and cannot place an order.
"""
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


def _day(frame: pd.DataFrame, date: str) -> pd.DataFrame:
    start = pd.Timestamp(date)
    if frame.timestamp.dt.tz is not None:
        start = start.tz_localize(frame.timestamp.dt.tz)
    return frame[(frame.timestamp >= start) & (frame.timestamp < start + pd.DateOffset(days=1))].reset_index(drop=True)


def _run_symbol(symbol: str, day: pd.DataFrame, phase_center: float, phase_tolerance: float, relative_tolerance: float, warmup: int) -> dict:
    atr = talib.ATR(day.high.to_numpy(float), day.low.to_numpy(float), day.close.to_numpy(float), timeperiod=14)
    ledger = StudyEntryShadowLedger()
    pid = CurveEntryPidShadow(phase_center, phase_tolerance, relative_tolerance)
    sync = CurveSynchronizerShadow(ledger, phase_center_degrees=phase_center, phase_tolerance_degrees=phase_tolerance, entry_pid=pid)
    for i in range(warmup, len(day)):
        bar = day.iloc[i]; ledger.advance(symbol, i, bar.timestamp, bar)
        if i < len(day) - 1:
            current_atr = float(atr[i]) if pd.notna(atr[i]) else max(float(bar.high - bar.low), .001)
            sync.observe(symbol, i, bar.timestamp, bar, day.iloc[:i + 1], current_atr)
    ledger.finalize(symbol, day.iloc[-1].timestamp, day.iloc[-1])
    ready = [x["entry_pid"] for x in ledger.observations if x.get("entry_pid", {}).get("entry_pid_ready")]
    summary = ledger.summary()
    return {
        "bars": len(day), "pid_ready_bars": len(ready),
        "phase_in_range": sum(x["phase_in_range"] for x in ready),
        "voltage_in_range": sum(x["voltage_in_range"] for x in ready),
        "frequency_in_range": sum(x["frequency_in_range"] for x in ready),
        "fully_synchronized": sum(x["synchronized"] for x in ready),
        "entry_multiplier_min": min((x["entry_timing_multiplier"] for x in ready), default=None),
        "entry_multiplier_max": max((x["entry_timing_multiplier"] for x in ready), default=None),
        "setups": summary["setups"], "resolved": summary["resolved"],
        "target_before_stop": summary["target_before_stop"], "net_pnl_per_share": summary["net_pnl_per_share"],
        "outcomes": summary["outcomes"],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2023-09-01"); p.add_argument("--phase-center", type=float, default=0.)
    p.add_argument("--phase-tolerance", type=float, default=20.); p.add_argument("--relative-tolerance", type=float, default=.10)
    p.add_argument("--warmup", type=int, default=124); p.add_argument("--output", default=None)
    a = p.parse_args(); manifest = DatasetManifest.load(str(MANIFEST)); verified = verify_manifest(manifest)
    if not verified.valid: raise RuntimeError(verified.message)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    per_symbol, missing = {}, {}
    for n, record in enumerate(sorted(manifest.files, key=lambda r: r.symbol), 1):
        frame = loader._load_symbol_csv(record.symbol); day = _day(frame, a.date)
        print(f"[LOAD {n:02d}/{len(manifest.files)}] {record.symbol}: {len(day)} bars", flush=True)
        if len(day) <= a.warmup + 1: missing[record.symbol] = len(day); continue
        per_symbol[record.symbol] = _run_symbol(record.symbol, day, a.phase_center, a.phase_tolerance, a.relative_tolerance, a.warmup)
    totals = Counter()
    for row in per_symbol.values():
        for key in ("bars", "pid_ready_bars", "phase_in_range", "voltage_in_range", "frequency_in_range", "fully_synchronized", "setups", "resolved", "target_before_stop"):
            totals[key] += row[key]
    totals["net_pnl_per_share"] = sum(x["net_pnl_per_share"] for x in per_symbol.values())
    artifact = {
        "run_type": "48symbol_curve_synchronizer_entry_pid_shadow_intraday", "date": a.date,
        "dataset_manifest_hash": manifest.manifest_hash, "dataset_verification": verified.message,
        "ranges": {"phase_center_degrees": a.phase_center, "phase_plus_minus_degrees": a.phase_tolerance, "voltage_plus_minus_percent_of_causal_median": a.relative_tolerance * 100, "frequency_plus_minus_percent_of_causal_median": a.relative_tolerance * 100},
        "pid": {"kp": .35, "ki": .02, "kd": .05, "dynamic": ["per-symbol rolling baselines", "measurement", "integral state", "derate output"], "not_dynamic": ["PID gains", "real order size", "real engine gates"]},
        "symbols": per_symbol, "missing": missing, "totals": dict(totals),
        "note": "Historical shadow research only. No engine order or portfolio P&L is created; net P&L is summed one-share shadow outcomes, not a portfolio result.",
    }
    output = Path(a.output or ROOT / "diagnostic_output" / f"curve_entry_pid_shadow_48symbol_{a.date.replace('-', '')}.json")
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "symbols": len(per_symbol), "totals": dict(totals)}, indent=2), flush=True)

if __name__ == "__main__": main()
