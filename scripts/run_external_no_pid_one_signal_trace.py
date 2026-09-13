#!/usr/bin/env python3
"""Trace the first completed external-engine paper trade with PID disabled.

This is a diagnostic ablation, not a strategy run.  The only architectural
change is that MPC's entry/exit PID transformations are identities.  The
candidate still passes the ordinary ingestion, PA, chart-studies, ID, safety,
portfolio sizing, paper-fill and exit paths.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
from typing import Any

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _as_dict(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(key): _as_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_dict(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _day(frame: pd.DataFrame, date: str) -> pd.DataFrame:
    tz = frame["timestamp"].dt.tz
    start = pd.Timestamp(date)
    end = start + pd.Timedelta(days=1)
    if tz is not None:
        start, end = start.tz_localize(tz), end.tz_localize(tz)
    return frame[(frame["timestamp"] >= start) & (frame["timestamp"] < end)].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="INFY")
    parser.add_argument("--date", default="2023-09-01")
    parser.add_argument("--output", default="diagnostic_output/no_pid_one_signal_trace_INFY_20230901.json")
    args = parser.parse_args()

    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(verification.message)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = _day(loader._load_symbol_csv(args.symbol), args.date)
    if len(bars) <= 60:
        raise RuntimeError(f"{args.symbol} has insufficient bars on {args.date}")

    engine = Revision2ExternalEngineOrchestrator(
        [args.symbol], CanonicalParameterRegistry(), starting_equity=1_000_000.0,
        closed_loop_mode="shadow", telemetry_mode="full", pid_mode="disabled",
    )
    candidates: list[dict[str, Any]] = []
    latest: dict[str, Any] = {}

    pa_evaluate = engine.pa.evaluate
    def traced_pa(snapshot, config):
        signal, trace = pa_evaluate(snapshot, config)
        latest["signal"] = _as_dict(signal)
        latest["pa_input_last_bar"] = _as_dict(snapshot.bars.iloc[-1].to_dict())
        return signal, trace
    engine.pa.evaluate = traced_pa

    studies_evaluate = engine.chart_studies.evaluate
    def traced_studies(symbol, snapshot_bars):
        result = studies_evaluate(symbol, snapshot_bars)
        latest["chart_studies"] = _as_dict(result)
        return result
    engine.chart_studies.evaluate = traced_studies

    id_evaluate = engine.id_box.evaluate
    def traced_id(signal, config, latest_close):
        decision, trace = id_evaluate(signal, config, latest_close)
        latest["id_decision"] = _as_dict(decision)
        return decision, trace
    engine.id_box.evaluate = traced_id

    plan_build = engine.mpc.build_plan
    def traced_plan(signal, decision, entry_price, atr, config):
        plan, pid_info, trace = plan_build(signal, decision, entry_price, atr, config)
        if plan is not None:
            candidates.append({
                "candidate_id": f"candidate-{len(candidates) + 1}",
                "signal_timestamp": signal.timestamp,
                "pa_input_last_bar": latest.get("pa_input_last_bar"),
                "pa_signal": latest.get("signal"),
                "chart_studies": latest.get("chart_studies"),
                "id_decision": latest.get("id_decision"),
                "mpc_inputs": {"next_open": entry_price, "atr": atr, "pid_mode": "disabled"},
                "mpc_plan": _as_dict(plan),
                "pid_output": _as_dict(pid_info),
            })
        return plan, pid_info, trace
    engine.mpc.build_plan = traced_plan

    safety_evaluate = engine.safety_gates_target.evaluate_pre_sizing
    def traced_safety(equity_curve, config):
        approved, reason, multiplier, trace = safety_evaluate(equity_curve, config)
        if candidates:
            candidates[-1]["pre_sizing_safety"] = {
                "approved": approved, "reason": reason, "size_multiplier_before_position_manager": multiplier,
            }
        return approved, reason, multiplier, trace
    engine.safety_gates_target.evaluate_pre_sizing = traced_safety

    size = engine.position_manager.size
    def traced_size(*call_args, **call_kwargs):
        quantity, trace = size(*call_args, **call_kwargs)
        if candidates:
            candidates[-1]["position_sizing"] = {
                "quantity": quantity,
                "telemetry": _as_dict(engine.position_manager.last_sizing_telemetry),
            }
        return quantity, trace
    engine.position_manager.size = traced_size

    report = engine.run({args.symbol: bars}, warmup=60)
    if not report["trades"]:
        raise RuntimeError("No completed trade for this symbol/day under the no-PID baseline")
    trade = report["trades"][0]
    candidate = next(
        (row for row in candidates if row["candidate_id"] == trade.get("candidate_id")), None,
    )
    if candidate is None:
        raise RuntimeError("Could not join first trade to its candidate trace")
    candidate["paper_execution_and_outcome"] = _as_dict(trade)
    candidate["controller_events_for_trade"] = [
        event for event in report["controller_telemetry"]
        if event.get("candidate_id") == trade.get("candidate_id") or event.get("trade_id") == trade.get("trade_id")
    ]
    artifact = {
        "run_type": "one_signal_external_no_pid_paper_trace",
        "research_boundary": "Diagnostic ablation only; no live trading or strategy promotion.",
        "symbol": args.symbol,
        "date": args.date,
        "pid_mode": "disabled",
        "closed_loop_mode": "shadow",
        "manifest_hash": manifest.manifest_hash,
        "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
        "first_completed_trade_trace": candidate,
        "day_summary": {
            key: report[key] for key in ("completed_trades", "gross_pnl", "net_pnl", "ending_equity", "mtm_max_drawdown_fraction")
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    trace = artifact["first_completed_trade_trace"]
    outcome = trace["paper_execution_and_outcome"]
    print(json.dumps({
        "output": str(output), "candidate_id": trace["candidate_id"],
        "entry_timestamp": outcome["entry_timestamp"], "entry_price": outcome["entry_price"],
        "exit_timestamp": outcome["exit_timestamp"], "exit_price": outcome["exit_price"],
        "reason": outcome["reason"], "bars_held": outcome["bars_held"], "net_pnl": outcome["net_pnl"],
    }, indent=2))


if __name__ == "__main__":
    main()
