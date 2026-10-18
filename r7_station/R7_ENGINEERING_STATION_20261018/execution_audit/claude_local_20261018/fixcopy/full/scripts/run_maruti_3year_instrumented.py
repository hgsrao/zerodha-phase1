#!/usr/bin/env python3
"""Runs INFY through the full 3-year dataset on the external-library
engine, with every box instrumented to record real, per-box statistics --
not just the funnel counts orchestrator.run() already returns.

Box 2 (DataIngestion/loader) genuinely uses ArcticDB, per the box mapping:
the CSV is ingested into a local Arctic library once, then read back from
there for the run, so this is a real exercise of that box, not a stand-in.

Writes results incrementally to disk (JSON) so a long run's findings
survive even if this process is interrupted.
"""
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# TEST_TAIL_BARS truncates to the last N bars for a quick smoke test before
# committing to the real, full-dataset run. Unset (the real run) uses every
# bar Arctic returns.
TEST_TAIL_BARS = int(os.environ["TEST_TAIL_BARS"]) if os.environ.get("TEST_TAIL_BARS") else None

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.data_loader_arctic import ArcticMarketDataLoader
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision2_external.startup_validation import validate_runtime_parameters, validate_safety_contract

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
STATS_PATH = OUTPUT_DIR / "maruti_3year_box_stats.json"
PROGRESS_PATH = OUTPUT_DIR / "maruti_3year_progress.jsonl"
SUMMARY_PATH = OUTPUT_DIR / "maruti_3year_summary.json"

stats = {
    "box1_startup": {},
    "box2_arcticdb": {},
    "box3_pandera": {},
    "box4_pa_talib": {"count": 0, "direction": Counter(), "quality_band": Counter(),
                       "confidence_sum": 0.0, "confidence_min": None, "confidence_max": None,
                       "momentum_sum": 0.0, "volatility_sum": 0.0},
    "box5_id_hmm": {"count": 0, "approved": 0, "rejected": 0, "reject_reasons": Counter(),
                     "regime_stressed_vetoes": 0},
    "box6_mpc_pid": {"count": 0, "rejected_profit_floor": 0, "side": Counter(),
                      "entry_adj_sum": 0.0, "exit_adj_sum": 0.0,
                      "entry_adj_min": None, "entry_adj_max": None},
    "box7_safetygates": {"pre_sizing_pass": 0, "pre_sizing_reject": 0,
                          "post_sizing_pass": 0, "post_sizing_reject": 0,
                          "gate18_pass": 0, "gate18_reject_by_gate": Counter()},
    "box8_positionmanager_pypfopt": {"count": 0, "zero_quantity": 0, "quantity_sum": 0,
                                       "quantity_min": None, "quantity_max": None},
    "box9_p01d": {"orders_created": 0, "orders_rejected_zero_qty": 0},
    "box10_unifiedexecution": {"in_window": 0, "out_of_window": 0,
                                 "note": "kiteconnect broker adapter NOT exercised -- this run is offline/historical, no live broker calls"},
}


def write_progress(event: str, **kw) -> None:
    with open(PROGRESS_PATH, "a") as f:
        f.write(json.dumps({"t": time.time(), "event": event, **kw}) + "\n")


def instrument_and_run():
    write_progress("start")
    registry = CanonicalParameterRegistry()
    symbol = "MARUTI"

    # Box 1: Pydantic startup validation, run explicitly first so its own
    # pass/fail is recorded even though the orchestrator also runs it.
    values = {name: spec.default for name, spec in registry.params.items()}
    safety_values = {name: spec.default for name, spec in registry.safety_params.items()}
    param_errors = validate_runtime_parameters(registry, values)
    safety_errors = validate_safety_contract(registry, safety_values)
    stats["box1_startup"] = {
        "passed": not (param_errors or safety_errors),
        "param_errors": param_errors, "safety_errors": safety_errors,
        "target_parameter_count": len(values), "safety_parameter_count": len(safety_values),
    }
    write_progress("box1_done", passed=stats["box1_startup"]["passed"])

    # Box 2: ArcticDB -- real ingest + read-back, not a CSV passthrough.
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    csv_loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    csv_filename = next(f.filename for f in manifest.files if f.symbol == symbol)
    arctic_db_path = str(OUTPUT_DIR / "arctic_maruti_3year_db")
    arctic_loader = ArcticMarketDataLoader(arctic_db_path, manifest.data_dir)
    t0 = time.time()
    ingest_result = arctic_loader.ingest_symbol(symbol, csv_filename, force=True)
    ingest_seconds = time.time() - t0
    t0 = time.time()
    frame = arctic_loader.load_symbol(symbol, tail=TEST_TAIL_BARS)
    read_seconds = time.time() - t0
    stats["box2_arcticdb"] = {
        "ingested_rows": ingest_result["rows"], "ingest_seconds": round(ingest_seconds, 3),
        "read_seconds": round(read_seconds, 4), "read_rows": len(frame),
    }
    write_progress("box2_done", rows=len(frame), ingest_seconds=ingest_seconds)

    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)

    # Box 3 (Pandera): certify_bars() already runs once inside orch.run()
    # itself; its audit dict is pulled from report["certification_audit"]
    # after the run below rather than instrumented separately here.

    # ---- instrument box 4: TA-Lib PA ----
    orig_pa_evaluate = orch.pa.evaluate
    def pa_evaluate(snapshot, config):
        signal, trace = orig_pa_evaluate(snapshot, config)
        s = stats["box4_pa_talib"]
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

    # ---- instrument box 5: HMM ID ----
    def _reject_category(reason: str) -> str:
        # Several reasons embed a dynamic float (e.g. "confidence 0.4399
        # below entry threshold 0.5000"), which would otherwise create a
        # near-unique dict key per rejection over 3 years of bars --
        # bucketing by category instead of exact string keeps this
        # meaningful at full-dataset scale.
        if reason == "no directional signal":
            return "no_directional_signal"
        if "stressed" in reason:
            return "hmm_regime_stressed"
        if reason == "PA quality band is red":
            return "pa_quality_band_red"
        if reason.startswith("confidence") and "below entry threshold" in reason:
            return "confidence_below_entry_threshold"
        if reason.startswith("estimated slippage"):
            return "slippage_exceeds_guard"
        if reason.startswith("risk:reward"):
            return "risk_reward_below_minimum"
        return f"other: {reason}"

    orig_id_evaluate = orch.id_box.evaluate
    def id_evaluate(signal, config, latest_close):
        decision, trace = orig_id_evaluate(signal, config, latest_close=latest_close)
        s = stats["box5_id_hmm"]
        s["count"] += 1
        if decision.approved:
            s["approved"] += 1
        else:
            s["rejected"] += 1
            s["reject_reasons"][_reject_category(decision.reason)] += 1
            if "stressed" in decision.reason.lower():
                s["regime_stressed_vetoes"] += 1
        return decision, trace
    orch.id_box.evaluate = id_evaluate

    # ---- instrument box 6: simple-pid MPC ----
    orig_mpc_build = orch.mpc.build_plan
    def mpc_build_plan(signal, decision, entry_price, atr, config):
        plan, pid_info, trace = orig_mpc_build(signal, decision, entry_price, atr, config)
        s = stats["box6_mpc_pid"]
        if plan is None:
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

    # ---- instrument box 7: SafetyGates + 18-gate ----
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

    # ---- instrument box 8: PyPortfolioOpt PositionManager ----
    orig_size = orch.position_manager.size
    def size(*a, **kw):
        quantity, trace = orig_size(*a, **kw)
        s = stats["box8_positionmanager_pypfopt"]
        s["count"] += 1
        if quantity <= 0:
            s["zero_quantity"] += 1
        else:
            s["quantity_sum"] += quantity
            s["quantity_min"] = quantity if s["quantity_min"] is None else min(s["quantity_min"], quantity)
            s["quantity_max"] = quantity if s["quantity_max"] is None else max(s["quantity_max"], quantity)
        return quantity, trace
    orch.position_manager.size = size

    # ---- instrument box 9: P01D ----
    orig_p01d = orch.p01d.create_order
    def create_order(symbol_, plan, quantity, config):
        order, trace = orig_p01d(symbol_, plan, quantity, config)
        if order is None:
            stats["box9_p01d"]["orders_rejected_zero_qty"] += 1
        else:
            stats["box9_p01d"]["orders_created"] += 1
        return order, trace
    orch.p01d.create_order = create_order

    # ---- instrument box 10: UnifiedExecution (trading window check) ----
    orig_window = orch._in_trading_window
    def in_trading_window(timestamp):
        result = orig_window(timestamp)
        stats["box10_unifiedexecution"]["in_window" if result else "out_of_window"] += 1
        return result
    orch._in_trading_window = in_trading_window

    write_progress("instrumentation_attached")

    t0 = time.time()
    report = orch.run({symbol: frame}, warmup=60)
    elapsed = time.time() - t0
    write_progress("run_complete", elapsed=elapsed, trades=report["completed_trades"])

    # Box 3 stats come straight from the certification audit already in the report.
    stats["box3_pandera"] = report["certification_audit"][symbol]

    # Finalize derived stats (means, etc.) and make Counters JSON-serializable.
    def _finalize(d):
        out = {}
        for k, v in d.items():
            if isinstance(v, Counter):
                out[k] = dict(v)
            else:
                out[k] = v
        return out

    pa = stats["box4_pa_talib"]
    if pa["count"]:
        pa["confidence_mean"] = pa["confidence_sum"] / pa["count"]
        pa["momentum_mean"] = pa["momentum_sum"] / pa["count"]
        pa["volatility_mean"] = pa["volatility_sum"] / pa["count"]
    mpc = stats["box6_mpc_pid"]
    if mpc["count"]:
        mpc["entry_adj_mean"] = mpc["entry_adj_sum"] / mpc["count"]
        mpc["exit_adj_mean"] = mpc["exit_adj_sum"] / mpc["count"]
    pm = stats["box8_positionmanager_pypfopt"]
    if pm["count"] - pm["zero_quantity"] > 0:
        pm["quantity_mean"] = pm["quantity_sum"] / (pm["count"] - pm["zero_quantity"])

    serializable_stats = {k: _finalize(v) for k, v in stats.items()}

    with open(STATS_PATH, "w") as f:
        json.dump(serializable_stats, f, indent=2, default=str)

    summary = {
        "symbol": symbol,
        "total_bars_in_dataset": len(frame),
        "elapsed_seconds": elapsed,
        "bars_processed": report["bars_processed"],
        "completed_trades": report["completed_trades"],
        "gross_pnl": report["gross_pnl"],
        "net_pnl": report["net_pnl"],
        "ending_equity": report["ending_equity"],
        "mtm_max_drawdown_fraction": report["mtm_max_drawdown_fraction"],
        "parameter_coverage": report["parameter_coverage"],
        "config_hash": report["config_hash"],
        "safety_contract_hash": report["safety_contract_hash"],
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
