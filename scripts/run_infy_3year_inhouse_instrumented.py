#!/usr/bin/env python3
"""Runs INFY through the full 3-year dataset on the ORIGINAL in-house
engine (revision2/orchestrator.py, all 10 boxes in-house, no external
libraries), instrumented identically to
scripts/run_infy_3year_instrumented.py so the two runs are directly
comparable box-by-box.

Uses the plain CSV loader (MarketDataLoader), not ArcticDB -- Box 2 in
the in-house engine has no external-library counterpart to exercise.
"""
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_TAIL_BARS = int(os.environ["TEST_TAIL_BARS"]) if os.environ.get("TEST_TAIL_BARS") else None

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2.orchestrator import Revision2Orchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
STATS_PATH = OUTPUT_DIR / "infy_3year_inhouse_box_stats.json"
PROGRESS_PATH = OUTPUT_DIR / "infy_3year_inhouse_progress.jsonl"
SUMMARY_PATH = OUTPUT_DIR / "infy_3year_inhouse_summary.json"

stats = {
    "box1_startup": {},
    "box2_dataloader_csv": {},
    "box3_l2certifier": {"count": 0, "certified": 0, "rejected": 0},
    "box4_pa": {"count": 0, "direction": Counter(), "quality_band": Counter(),
                "confidence_sum": 0.0, "confidence_min": None, "confidence_max": None,
                "momentum_sum": 0.0, "volatility_sum": 0.0},
    "box5_id": {"count": 0, "approved": 0, "rejected": 0, "reject_reasons": Counter()},
    "box6_mpc_pid": {"count": 0, "rejected_profit_floor": 0, "side": Counter(),
                      "entry_adj_sum": 0.0, "exit_adj_sum": 0.0,
                      "entry_adj_min": None, "entry_adj_max": None},
    "box7_safetygates": {"pre_sizing_pass": 0, "pre_sizing_reject": 0,
                          "post_sizing_pass": 0, "post_sizing_reject": 0,
                          "gate18_pass": 0, "gate18_reject_by_gate": Counter()},
    "box8_positionmanager": {"count": 0, "zero_quantity": 0, "quantity_sum": 0,
                               "quantity_min": None, "quantity_max": None},
    "box9_p01d": {"orders_created": 0, "orders_rejected_zero_qty": 0},
    "box10_unifiedexecution": {"in_window": 0, "out_of_window": 0},
}


def write_progress(event: str, **kw) -> None:
    with open(PROGRESS_PATH, "a") as f:
        f.write(json.dumps({"t": time.time(), "event": event, **kw}) + "\n")


def _reject_category(reason: str) -> str:
    if reason == "no directional signal":
        return "no_directional_signal"
    if reason == "PA quality band is red":
        return "pa_quality_band_red"
    if reason.startswith("confidence") and "below entry threshold" in reason:
        return "confidence_below_entry_threshold"
    if reason.startswith("estimated slippage"):
        return "slippage_exceeds_guard"
    if reason.startswith("risk:reward"):
        return "risk_reward_below_minimum"
    return f"other: {reason}"


def instrument_and_run():
    write_progress("start")
    registry = CanonicalParameterRegistry()
    symbol = "INFY"

    values = {name: spec.default for name, spec in registry.params.items()}
    stats["box1_startup"] = {"target_parameter_count": len(values)}

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    csv_loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    t0 = time.time()
    frame = csv_loader._load_symbol_csv(symbol)
    if TEST_TAIL_BARS:
        frame = frame.tail(TEST_TAIL_BARS).reset_index(drop=True)
    read_seconds = time.time() - t0
    stats["box2_dataloader_csv"] = {"read_seconds": round(read_seconds, 4), "rows": len(frame)}
    write_progress("box2_done", rows=len(frame), read_seconds=read_seconds)

    orch = Revision2Orchestrator(symbol, registry, starting_equity=1_000_000.0)
    stats["box1_startup"]["startup_certificate_passed"] = orch.startup_certificate.passed
    write_progress("box1_done", passed=orch.startup_certificate.passed)

    # ---- box 3: L2DataCertifier ----
    orig_certify = orch.l2_certifier.certify
    def certify(bars_slice, config):
        certified, reason, trace = orig_certify(bars_slice, config)
        stats["box3_l2certifier"]["count"] += 1
        stats["box3_l2certifier"]["certified" if certified else "rejected"] += 1
        return certified, reason, trace
    orch.l2_certifier.certify = certify

    # ---- box 4: PA ----
    orig_pa = orch.pa.evaluate
    def pa_evaluate(snapshot, config):
        signal, trace = orig_pa(snapshot, config)
        s = stats["box4_pa"]
        s["count"] += 1
        s["direction"][signal.direction] += 1
        s["quality_band"][signal.quality_band] += 1
        s["confidence_sum"] += signal.confidence
        s["momentum_sum"] += signal.momentum
        s["volatility_sum"] += signal.volatility
        s["confidence_min"] = signal.confidence if s["confidence_min"] is None else min(s["confidence_min"], signal.confidence)
        s["confidence_max"] = signal.confidence if s["confidence_max"] is None else max(s["confidence_max"], signal.confidence)
        return signal, trace
    orch.pa.evaluate = pa_evaluate

    # ---- box 5: ID ----
    orig_id = orch.id_box.evaluate
    def id_evaluate(signal, config):
        decision, trace = orig_id(signal, config)
        s = stats["box5_id"]
        s["count"] += 1
        if decision.approved:
            s["approved"] += 1
        else:
            s["rejected"] += 1
            s["reject_reasons"][_reject_category(decision.reason)] += 1
        return decision, trace
    orch.id_box.evaluate = id_evaluate

    # ---- box 6: MPC (BoundedPID) ----
    orig_mpc = orch.mpc.build_plan
    def mpc_build_plan(signal, decision, entry_price, atr, config):
        plan, pid_info, trace = orig_mpc(signal, decision, entry_price, atr, config)
        s = stats["box6_mpc_pid"]
        if plan is None:
            if pid_info.get("reason") == "below minimum absolute profit floor":
                s["rejected_profit_floor"] += 1
        else:
            s["count"] += 1
            s["side"][plan.side] += 1
            s["entry_adj_sum"] += pid_info["entry_adjustment"]
            s["exit_adj_sum"] += pid_info["exit_adjustment"]
            s["entry_adj_min"] = pid_info["entry_adjustment"] if s["entry_adj_min"] is None else min(s["entry_adj_min"], pid_info["entry_adjustment"])
            s["entry_adj_max"] = pid_info["entry_adjustment"] if s["entry_adj_max"] is None else max(s["entry_adj_max"], pid_info["entry_adjustment"])
        return plan, pid_info, trace
    orch.mpc.build_plan = mpc_build_plan

    # ---- box 7: SafetyGates + 18-gate ----
    orig_pre = orch.safety_gates_target.evaluate_pre_sizing
    def pre_sizing(equity_curve, config):
        approved, reason, mult, trace = orig_pre(equity_curve, config)
        stats["box7_safetygates"]["pre_sizing_pass" if approved else "pre_sizing_reject"] += 1
        return approved, reason, mult, trace
    orch.safety_gates_target.evaluate_pre_sizing = pre_sizing

    orig_post = orch.safety_gates_target.evaluate_post_sizing
    def post_sizing(equity_curve, plan, quantity, config):
        ok, reason, trace = orig_post(equity_curve, plan, quantity, config)
        stats["box7_safetygates"]["post_sizing_pass" if ok else "post_sizing_reject"] += 1
        return ok, reason, trace
    orch.safety_gates_target.evaluate_post_sizing = post_sizing

    orig_gate_eval = orch.entry_decision_engine.evaluate
    def gate_evaluate(*a, **kw):
        result = orig_gate_eval(*a, **kw)
        if result["passed"]:
            stats["box7_safetygates"]["gate18_pass"] += 1
        else:
            stats["box7_safetygates"]["gate18_reject_by_gate"][result["gate"]] += 1
        return result
    orch.entry_decision_engine.evaluate = gate_evaluate

    # ---- box 8: PositionManager ----
    orig_size = orch.position_manager.size
    def size(*a, **kw):
        quantity, trace = orig_size(*a, **kw)
        s = stats["box8_positionmanager"]
        s["count"] += 1
        if quantity <= 0:
            s["zero_quantity"] += 1
        else:
            s["quantity_sum"] += quantity
            s["quantity_min"] = quantity if s["quantity_min"] is None else min(s["quantity_min"], quantity)
            s["quantity_max"] = quantity if s["quantity_max"] is None else max(s["quantity_max"], quantity)
        return quantity, trace
    orch.position_manager.size = size

    # ---- box 9: P01D ----
    orig_p01d = orch.p01d.create_order
    def create_order(symbol_, plan, quantity, config):
        order, trace = orig_p01d(symbol_, plan, quantity, config)
        if order is None:
            stats["box9_p01d"]["orders_rejected_zero_qty"] += 1
        else:
            stats["box9_p01d"]["orders_created"] += 1
        return order, trace
    orch.p01d.create_order = create_order

    # ---- box 10: UnifiedExecution ----
    orig_window = orch.unified_execution.check_window
    def check_window(timestamp, config):
        in_window, bias, trace = orig_window(timestamp, config)
        stats["box10_unifiedexecution"]["in_window" if in_window else "out_of_window"] += 1
        return in_window, bias, trace
    orch.unified_execution.check_window = check_window

    write_progress("instrumentation_attached")

    t0 = time.time()
    report = orch.run(frame, warmup=60)
    elapsed = time.time() - t0
    write_progress("run_complete", elapsed=elapsed, trades=report["completed_trades"])

    def _finalize(d):
        return {k: (dict(v) if isinstance(v, Counter) else v) for k, v in d.items()}

    pa = stats["box4_pa"]
    if pa["count"]:
        pa["confidence_mean"] = pa["confidence_sum"] / pa["count"]
        pa["momentum_mean"] = pa["momentum_sum"] / pa["count"]
        pa["volatility_mean"] = pa["volatility_sum"] / pa["count"]
    mpc = stats["box6_mpc_pid"]
    if mpc["count"]:
        mpc["entry_adj_mean"] = mpc["entry_adj_sum"] / mpc["count"]
        mpc["exit_adj_mean"] = mpc["exit_adj_sum"] / mpc["count"]
    pm = stats["box8_positionmanager"]
    if pm["count"] - pm["zero_quantity"] > 0:
        pm["quantity_mean"] = pm["quantity_sum"] / (pm["count"] - pm["zero_quantity"])

    with open(STATS_PATH, "w") as f:
        json.dump({k: _finalize(v) for k, v in stats.items()}, f, indent=2, default=str)

    summary = {
        "symbol": symbol, "total_bars_in_dataset": len(frame), "elapsed_seconds": elapsed,
        "bars_processed": report["bars_processed"], "completed_trades": report["completed_trades"],
        "gross_pnl": report["gross_pnl"], "net_pnl": report["net_pnl"],
        "ending_equity": report["ending_equity"], "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
        "parameter_coverage": report["parameter_coverage"],
        "trades": report["trades"],
    }
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    write_progress("saved_results", stats_path=str(STATS_PATH), summary_path=str(SUMMARY_PATH))
    print(json.dumps({"elapsed": elapsed, "trades": report["completed_trades"], "net_pnl": report["net_pnl"]}, default=str))


if __name__ == "__main__":
    try:
        instrument_and_run()
    except Exception as exc:
        write_progress("error", message=str(exc))
        raise
