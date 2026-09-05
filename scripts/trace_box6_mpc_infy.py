#!/usr/bin/env python3
"""Step-by-step trace of Box 6 (MPC/simple-pid) for every real signal that
reached it during the actual 3-year INFY run -- reconstructs every
intermediate quantity (raw ATR distances, PID adjustments, exit
tightness, final stop/target, projected profit vs the floor) from the
REAL captured inputs/outputs of SimplePIDModelPredictiveControlBox.
build_plan(), not a re-derivation with different logic -- so this can't
silently diverge from what the real run actually computed.

Reuses the already-ingested ArcticDB copy of INFY from the earlier 3-year
run instead of re-ingesting.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.data_loader_arctic import ArcticMarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
TRACE_PATH = OUTPUT_DIR / "box6_mpc_full_trace.jsonl"
STEP_STATS_PATH = OUTPUT_DIR / "box6_mpc_step_stats.json"


def main():
    registry = CanonicalParameterRegistry()
    symbol = "INFY"

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    arctic_loader = ArcticMarketDataLoader(str(OUTPUT_DIR / "arctic_infy_3year_db"), manifest.data_dir)
    frame = arctic_loader.load_symbol(symbol)
    assert frame is not None, "run the earlier 3-year ArcticDB ingest first"

    # Fixed config values used by every call this run (no calibration
    # override), read once so each trace row doesn't need to re-fetch them.
    values = {name: spec.default for name, spec in registry.params.items()}
    profit_mult = float(values["profit_target_atr_mult"])
    stop_mult = float(values["stop_loss_atr_mult"])
    margin_buffer = float(values["profit_target_margin_buffer"])
    min_abs_profit = float(values["minimum_absolute_profit_rupees"])
    min_rr = float(values["min_risk_reward_ratio"])
    slippage_cost_mult = float(values["slippage_cost_multiplier"])
    floor = min_abs_profit / 10.0

    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)

    trace_rows = []

    # pid_info from build_plan() only carries entry_adjustment/exit_adjustment
    # on the ACCEPTED path -- boxes.py's own code returns just
    # {"reason": ...} on the profit-floor rejection, which is 100% of real
    # calls here. Hooking the PID objects' own __call__ (via _get_pid, which
    # creates/caches one per symbol) captures the real adjustment
    # regardless of what build_plan() ends up returning, without touching
    # revision2_external/pid_controller.py's source.
    pid_call_log = []

    class _TracedPID:
        """Proxy wrapper, not an instance-attribute override: pid(x) invokes
        type(pid).__call__(pid, x), which looks up __call__ on the CLASS,
        not the instance -- so reassigning pid.__call__ directly (the first
        version of this hook) is silently never invoked. Wrapping in a real
        object whose own class defines __call__ sidesteps that."""
        def __init__(self, pid, role):
            self._pid = pid
            self._role = role

        def __call__(self, input_, dt=None):
            output = self._pid(input_, dt=dt)
            pid_call_log.append({"role": self._role, "input": input_, "output": output})
            return output

    wrapped_cache = {}
    orig_get_pid = orch.mpc._get_pid

    def get_pid_traced(store, symbol_, kp, ki, kd, target, clamp):
        pid = orig_get_pid(store, symbol_, kp, ki, kd, target, clamp)
        cache_key = id(store), symbol_
        if cache_key not in wrapped_cache:
            role = "entry" if store is orch.mpc._entry_pids else "exit"
            wrapped_cache[cache_key] = _TracedPID(pid, role)
        return wrapped_cache[cache_key]
    orch.mpc._get_pid = get_pid_traced

    orig_build_plan = orch.mpc.build_plan

    def traced_build_plan(signal, decision, entry_price, atr, config):
        log_len_before = len(pid_call_log)
        plan, pid_info, box_trace = orig_build_plan(signal, decision, entry_price, atr, config)
        new_calls = pid_call_log[log_len_before:]
        entry_calls = [c for c in new_calls if c["role"] == "entry"]
        exit_calls = [c for c in new_calls if c["role"] == "exit"]
        entry_adjustment = entry_calls[-1]["output"] if entry_calls else None
        exit_adjustment = exit_calls[-1]["output"] if exit_calls else None

        side = "BUY" if signal.direction > 0 else "SELL"
        sign = 1 if side == "BUY" else -1

        # Step-by-step reconstruction from the REAL captured inputs/outputs
        # -- every number below is derived from what build_plan() actually
        # received or actually computed (via the PID hook above for the
        # adjustments specifically), using the exact same formulas it runs
        # internally (contracts.py / boxes.py's own arithmetic), not an
        # independent re-implementation that could silently diverge.
        raw_effective_entry = entry_price * (1 + slippage_cost_mult * 0.0005 * sign)
        raw_stop_distance = atr * stop_mult
        raw_target_distance = atr * profit_mult * (1 + margin_buffer)
        rr_extended_target_distance = max(raw_target_distance, raw_stop_distance * min_rr) if raw_stop_distance > 0 else raw_target_distance
        # entry_timing_multiplier is also only in pid_info on the accepted
        # path -- recomputed here from the real, hooked entry_adjustment
        # using boxes.py's exact clamp formula.
        entry_timing_multiplier = (
            max(0.3, min(1.0, 1.0 - abs(entry_adjustment))) if entry_adjustment is not None else None
        )

        row = {
            "timestamp": signal.timestamp,
            "step1_inputs": {
                "direction": signal.direction, "signal_confidence": round(signal.confidence, 6),
                "decision_confidence": round(decision.confidence, 6),
                "decision_timing_quality": round(decision.timing_quality, 6),
                "entry_price_next_open": round(entry_price, 4), "atr": round(atr, 6),
            },
            "step2_side": side,
            "step3_raw_effective_entry": round(raw_effective_entry, 4),
            "step4_raw_distances": {
                "raw_stop_distance": round(raw_stop_distance, 6),
                "raw_target_distance": round(raw_target_distance, 6),
                "rr_extended_target_distance": round(rr_extended_target_distance, 6),
            },
        }

        if entry_adjustment is not None:
            nudged_effective_entry = raw_effective_entry * (1 + entry_adjustment * 0.001)
            exit_tightness = max(0.5, min(1.0, 1.0 - abs(exit_adjustment)))
            final_stop_distance = raw_stop_distance * exit_tightness
            final_target_distance = rr_extended_target_distance * exit_tightness
            row["step5_entry_pid"] = {
                "entry_adjustment": round(entry_adjustment, 6),
                "entry_timing_multiplier": round(entry_timing_multiplier, 6),
                "nudged_effective_entry": round(nudged_effective_entry, 4),
            }
            row["step6_exit_pid"] = {
                "exit_adjustment": round(exit_adjustment, 6),
                "exit_tightness": round(exit_tightness, 6),
            }
            row["step7_final_distances"] = {
                "final_stop_distance": round(final_stop_distance, 6),
                "final_target_distance": round(final_target_distance, 6),
            }
            if plan is not None:
                row["step8_final_prices"] = {
                    "stop_price": round(plan.stop_price, 4), "target_price": round(plan.target_price, 4),
                    "entry_price": round(plan.entry_price, 4),
                }
            row["step9_profit_check"] = {
                "projected_profit": round(final_target_distance, 6),
                "floor_rupees": floor,
                "clears_floor": final_target_distance >= floor,
            }
        row["step10_result"] = "ACCEPTED" if plan is not None else "REJECTED"
        row["reject_reason"] = None if plan is not None else pid_info.get("reason", "unknown (decision not approved)")

        trace_rows.append(row)
        return plan, pid_info, box_trace

    orch.mpc.build_plan = traced_build_plan

    t0 = time.time()
    report = orch.run({symbol: frame}, warmup=60)
    elapsed = time.time() - t0

    with open(TRACE_PATH, "w") as f:
        for row in trace_rows:
            f.write(json.dumps(row, default=str) + "\n")

    # Aggregate step-level stats across every real MPC call this run.
    import statistics as st
    def _vals(key_path):
        out = []
        for r in trace_rows:
            d = r
            ok = True
            for k in key_path:
                if k in d:
                    d = d[k]
                else:
                    ok = False
                    break
            if ok and isinstance(d, (int, float)):
                out.append(d)
        return out

    def _summ(vals):
        if not vals:
            return None
        return {"n": len(vals), "min": min(vals), "max": max(vals), "mean": st.mean(vals), "median": st.median(vals)}

    step_stats = {
        "total_calls": len(trace_rows),
        "accepted": sum(1 for r in trace_rows if r["step10_result"] == "ACCEPTED"),
        "rejected": sum(1 for r in trace_rows if r["step10_result"] == "REJECTED"),
        "raw_target_distance": _summ(_vals(["step4_raw_distances", "raw_target_distance"])),
        "rr_extended_target_distance": _summ(_vals(["step4_raw_distances", "rr_extended_target_distance"])),
        "exit_adjustment": _summ(_vals(["step6_exit_pid", "exit_adjustment"])),
        "exit_tightness": _summ(_vals(["step6_exit_pid", "exit_tightness"])),
        "final_target_distance_aka_projected_profit": _summ(_vals(["step7_final_distances", "final_target_distance"])),
        "floor_rupees": floor,
        "count_final_target_distance_above_floor": sum(
            1 for r in trace_rows
            if "step7_final_distances" in r and r["step7_final_distances"]["final_target_distance"] >= floor
        ),
        "count_exit_tightness_at_min_clamp_0_5": sum(
            1 for r in trace_rows if "step6_exit_pid" in r and r["step6_exit_pid"]["exit_tightness"] <= 0.5001
        ),
    }
    with open(STEP_STATS_PATH, "w") as f:
        json.dump(step_stats, f, indent=2, default=str)

    print(json.dumps({"elapsed": elapsed, "traced_calls": len(trace_rows), **{k: v for k, v in step_stats.items() if k in ("total_calls", "accepted", "rejected")}}, default=str))


if __name__ == "__main__":
    main()
