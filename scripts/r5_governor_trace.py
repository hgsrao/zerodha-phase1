#!/usr/bin/env python3
"""Measured governor traces for one or more R5 bays on a sealed Step 5 Stage A block.

Diagnostic only.  It reuses the Step 5 worker's block loader (``prepare_block``) and builds the
engine exactly as ``execute_block`` does, restricted to one bay's canonical symbols
(``revision5/topology.py``).  The governor objects are instrumented with pass-through wrappers
that record inputs and outputs; no engine code, protocol, registry or Stage A state is touched.

Recorded per run (bay x authority):
  * gain schedule: every ``begin_bar`` call, whether a dynamic environment was passed, every
    ``apply_runtime_profile`` call, and each distinct (kp, ki, kd, droop_r, base_z, ...) tuple
  * outer realized-R PID: every ``register_trade`` (error, integral, derivative, control_u,
    feedback offset and its saturation, dynamic_z at zero grid)
  * entry comparator: dynamic_z, droop penalty, overspeed limit, signed z, action
  * inner per-position PID: error, integral, derivative, control_u, exit envelope, ratchet floor
  * Mark V gate: FSR_selected and the controlling limiter at entry and in position
  * bay protection: outcome registration (consecutive stops, trip, cooldown) and the paper
    admission result with the bay's protection reason

Usage (from the repo root, with the project venv):
    python scripts/r5_governor_trace.py --block 1 \
        --bays GTG1_HEAVY_INDUSTRY,GTG2_TECH_TELECOM --authority full,advisory \
        --output-dir outputs/r5_governor_trace

Default parameters are the protocol's search-space defaults (Stage A trial 0).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_PROTOCOL = ROOT / "revision5/step5_sealed_calibration_protocol_v2.json"
GAIN_FIELDS = ("runtime_kp", "runtime_ki", "runtime_kd", "runtime_droop_r", "runtime_base_z",
               "runtime_target_r", "runtime_grid_droop_gain", "runtime_grid_droop_max",
               "runtime_dynamic_offset_min", "runtime_dynamic_offset_max",
               "runtime_integral_clamp", "runtime_outcome_window")
KEPT_EVENTS = {"GOVERNOR_ENTRY_DECISION", "GOVERNOR_POSITION_DECISION", "GOVERNOR_EXIT_ARMED",
               "ADVISORY_EXIT_NOT_ACTUATED", "MICOM_GRID_TRIP", "MICOM_GRID_RECLOSE"}


def _load_worker():
    """Import the Step 5 worker read-only for its sealed block loader."""
    path = ROOT / "scripts/run_r5_step5_candidate.py"
    spec = importlib.util.spec_from_file_location("r5_step5_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _q(values):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    if not values:
        return None
    pick = lambda p: values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))]
    return {"n": len(values), "min": values[0], "p10": pick(0.10), "p50": pick(0.50),
            "p90": pick(0.90), "max": values[-1], "mean": statistics.fmean(values)}


def _gains(gov):
    return tuple(float(getattr(gov, f)) for f in GAIN_FIELDS)


class Tracer:
    """Pass-through wrappers on one orchestrator's governor objects.  Records, never alters."""

    def __init__(self, orch, plant, bay_ids):
        self.orch, self.plant, self.bay_ids = orch, plant, tuple(bay_ids)
        self.clock = {"ts": None}
        self.rows = defaultdict(list)
        self.counts = Counter()
        self.gain_sets = {b: {_gains(plant.bays[b].governor)} for b in bay_ids}
        self.gain_changes = []
        self._wrap_plant()
        for bay_id in bay_ids:
            self._wrap_governor(bay_id, plant.bays[bay_id].governor)
            self._wrap_bay(bay_id, plant.bays[bay_id])
        self._wrap_orchestrator()

    # -- plant clock and gain schedule
    def _wrap_plant(self):
        plant, original = self.plant, self.plant.begin_bar

        def begin_bar(current_dt, bar_index, environment=None):
            self.clock["ts"] = str(current_dt)
            self.counts["begin_bar_calls"] += 1
            self.counts["begin_bar_with_environment"] += int(environment is not None)
            out = original(current_dt, bar_index, environment)
            for bay_id in self.bay_ids:
                gains = _gains(plant.bays[bay_id].governor)
                if gains not in self.gain_sets[bay_id]:
                    self.gain_changes.append({"ts": self.clock["ts"], "bay": bay_id,
                                              **dict(zip(GAIN_FIELDS, gains))})
                self.gain_sets[bay_id].add(gains)
            return out

        plant.begin_bar = begin_bar

    def _wrap_governor(self, bay_id, gov):
        rows, counts, clock = self.rows, self.counts, self.clock
        dyn0 = type(gov).dynamic_z.__get__(gov)            # side-effect free
        apply_profile, register, entry_req, position = (
            gov.apply_runtime_profile, gov.register_trade, gov.evaluate_entry_request,
            gov.evaluate_position_control)

        def apply_runtime_profile(profile):
            counts[f"apply_runtime_profile:{bay_id}"] += 1
            return apply_profile(profile)

        def register_trade(realized_r):
            before_integral = gov.integral_error
            u = register(realized_r)
            offset = dyn0(0.0) - gov.runtime_base_z
            rows["outer"].append({
                "ts": clock["ts"], "bay": bay_id, "realized_r": float(realized_r),
                "window_mean_r": statistics.fmean(gov.history_r), "window_n": len(gov.history_r),
                "error": gov.last_error, "integral": gov.integral_error,
                "integral_delta": gov.integral_error - before_integral, "control_u": u,
                "kp": gov.runtime_kp, "ki": gov.runtime_ki, "kd": gov.runtime_kd,
                "feedback_offset": offset,
                "offset_at_min": math.isclose(offset, gov.runtime_dynamic_offset_min, abs_tol=1e-12),
                "offset_at_max": math.isclose(offset, gov.runtime_dynamic_offset_max, abs_tol=1e-12),
                "dynamic_z_zero_grid": dyn0(0.0)})
            return u

        def evaluate_entry_request(*, z_score, grid_return_fraction=0.0, side="BUY",
                                   entry_mode="mean_reversion"):
            out = entry_req(z_score=z_score, grid_return_fraction=grid_return_fraction, side=side,
                            entry_mode=entry_mode)
            sign = 1.0 if side == "BUY" else -1.0
            offset = dyn0(0.0) - gov.runtime_base_z
            droop = dyn0(0.0) - dyn0(sign * float(grid_return_fraction))
            rows["entry"].append({
                "ts": clock["ts"], "bay": bay_id, "side": side, "mode": entry_mode,
                "grid_return": float(grid_return_fraction), "signed_z": out["signed_z"],
                "dynamic_z": out["dynamic_z"], "limit": out["signed_z_limit"],
                "margin": out["signed_z_limit"] - out["signed_z"], "feedback_offset": offset,
                "droop_penalty": droop,
                "droop_at_cap": math.isclose(droop, gov.runtime_grid_droop_max, abs_tol=1e-12),
                "last_control_u": gov.last_control_u, "action": out["action"]})
            return out

        def evaluate_position_control(**kwargs):
            out = position(**kwargs)
            noise, sigma = kwargs.get("path_noise_r"), kwargs.get("path_error_sigma")
            envelope = (float(sigma) * float(noise) * math.sqrt(max(int(kwargs["elapsed_bars"]), 1))
                        if noise is not None else max(abs(gov.runtime_target_r),
                                                      abs(gov.runtime_dynamic_offset_max)))
            rows["inner"].append({
                "ts": clock["ts"], "bay": bay_id, "position_id": str(kwargs.get("position_id")),
                "elapsed": int(kwargs["elapsed_bars"]), "measured_r": out["measured_r"],
                "reference_r": out["reference_r"], "error": out["error"],
                "integral": out["integral_error"], "derivative": out["derivative"],
                "control_u": out["control_u"], "exit_envelope": envelope,
                "exit_control": max(abs(gov.runtime_target_r),
                                    abs(gov.runtime_ki * gov.runtime_integral_clamp)),
                "path_noise_r": noise, "floor": out["protected_r_floor"],
                "mfe_r": out["max_favorable_r"], "action": out["action"], "reason": out["reason"]})
            return out

        gov.apply_runtime_profile = apply_runtime_profile
        gov.register_trade = register_trade
        gov.evaluate_entry_request = evaluate_entry_request
        gov.evaluate_position_control = evaluate_position_control

    def _wrap_bay(self, bay_id, bay):
        original = bay.register_outcome

        def register_outcome(*, realized_r, reason, bar_index):
            u = original(realized_r=realized_r, reason=reason, bar_index=bar_index)
            self.rows["protection"].append({
                "ts": self.clock["ts"], "bay": bay_id, "realized_r": float(realized_r),
                "reason": reason, "bar_index": bar_index, "consecutive_stops": bay.consecutive_stops,
                "tripped_offline": bay.tripped_offline,
                "cooldown_until": bay.cooldown_until_bar_exclusive})
            return u

        bay.register_outcome = register_outcome

    def _wrap_orchestrator(self):
        from revision5.protection_snapshot import build_plant_protection_snapshot
        from revision5.topology import SYMBOL_TO_BAY
        orch, record, limit = self.orch, self.orch._record_controller_event, self.orch._paper_plant_entry_limit

        def record_controller_event(event_type, timestamp, symbol, payload):
            if event_type in KEPT_EVENTS:
                self.rows["events"].append({"event_type": event_type, "timestamp": str(timestamp),
                                            "symbol": symbol, **payload})
            return record(event_type, timestamp, symbol, payload)

        def paper_plant_entry_limit(symbol, quantity, entry_price, timestamp):
            protection = build_plant_protection_snapshot(self.plant)
            bay_id = SYMBOL_TO_BAY.get(symbol)
            bay_reason = protection.bay(bay_id).reason if protection.connected and bay_id else None
            allowed = limit(symbol, quantity, entry_price, timestamp)
            self.rows["admission"].append({"timestamp": str(timestamp), "symbol": symbol, "bay": bay_id,
                                           "requested": quantity, "allowed": allowed,
                                           "bay_protection_reason": bay_reason or "AVAILABLE",
                                           "master_block": protection.master_block})
            return allowed

        orch._record_controller_event = record_controller_event
        orch._paper_plant_entry_limit = paper_plant_entry_limit


def _summarize(tracer, report, bay_ids, metrics):
    rows = tracer.rows
    entry, inner, outer = rows["entry"], rows["inner"], rows["outer"]
    events = rows["events"]
    ent_ev = [e for e in events if e["event_type"] == "GOVERNOR_ENTRY_DECISION"]
    pos_ev = [e for e in events if e["event_type"] == "GOVERNOR_POSITION_DECISION"]
    trades = report.get("trades", [])
    by_trade = defaultdict(list)
    for row in inner:
        by_trade[row["position_id"]].append(row)
    exit_rows = [r for r in inner if r["action"] == "EXIT"]
    return {
        "metrics": metrics,
        "exit_reasons": dict(Counter(str(t.get("reason", "?")).split(":")[0] for t in trades)),
        "exit_reason_detail": dict(Counter(str(t.get("reason", "?")) for t in trades)),
        "sides": dict(Counter(t.get("side") for t in trades)),
        "bars_held": _q([t.get("bars_held") for t in trades]),
        "gain_schedule": {
            "begin_bar_calls": tracer.counts["begin_bar_calls"],
            "begin_bar_with_environment": tracer.counts["begin_bar_with_environment"],
            "apply_runtime_profile_calls": {b: tracer.counts[f"apply_runtime_profile:{b}"] for b in bay_ids},
            "distinct_gain_tuples": {b: len(tracer.gain_sets[b]) for b in bay_ids},
            "gains": {b: [dict(zip(GAIN_FIELDS, g)) for g in sorted(tracer.gain_sets[b])] for b in bay_ids},
            "changes": tracer.gain_changes[:50],
            "verdict": {b: ("FIXED" if len(tracer.gain_sets[b]) == 1 else "SCHEDULED") for b in bay_ids},
        },
        "outer_loop": {
            "updates": len(outer),
            "realized_r": _q([r["realized_r"] for r in outer]),
            "error": _q([r["error"] for r in outer]),
            "integral": _q([r["integral"] for r in outer]),
            "control_u": _q([r["control_u"] for r in outer]),
            "feedback_offset": _q([r["feedback_offset"] for r in outer]),
            "offset_saturated_min_fraction": (sum(r["offset_at_min"] for r in outer) / len(outer)) if outer else None,
            "offset_saturated_max_fraction": (sum(r["offset_at_max"] for r in outer) / len(outer)) if outer else None,
            "dynamic_z_zero_grid": _q([r["dynamic_z_zero_grid"] for r in outer]),
        },
        "entry_comparator": {
            "evaluations": len(entry),
            "actions": dict(Counter(r["action"] for r in entry)),
            "sides": dict(Counter(r["side"] for r in entry)),
            "dynamic_z": _q([r["dynamic_z"] for r in entry]),
            "overspeed_limit": _q([r["limit"] for r in entry]),
            "signed_z": _q([r["signed_z"] for r in entry]),
            "margin": _q([r["margin"] for r in entry]),
            "feedback_offset": _q([r["feedback_offset"] for r in entry]),
            "droop_penalty": _q([r["droop_penalty"] for r in entry]),
            "droop_active_fraction": (sum(r["droop_penalty"] > 0 for r in entry) / len(entry)) if entry else None,
            "droop_at_cap_fraction": (sum(r["droop_at_cap"] for r in entry) / len(entry)) if entry else None,
        },
        "mark_v_entry": {
            "decisions": dict(Counter(f"{e['action']}:{e['reason']}" for e in ent_ev)),
            "controlling_limiter": dict(Counter(e.get("controlling_limiter") for e in ent_ev
                                                if e.get("fsr_selected") is not None)),
            "fsr_selected": _q([e.get("fsr_selected") for e in ent_ev]),
            "entry_hurdle": _q([e.get("entry_hurdle") for e in ent_ev]),
        },
        "inner_loop": {
            "evaluations": len(inner), "positions": len(by_trade),
            "actions": dict(Counter(f"{r['action']}:{r['reason']}" for r in inner)),
            "error": _q([r["error"] for r in inner]),
            "integral": _q([r["integral"] for r in inner]),
            "derivative": _q([r["derivative"] for r in inner]),
            "control_u": _q([r["control_u"] for r in inner]),
            "exit_envelope": _q([r["exit_envelope"] for r in inner]),
            "path_noise_r": _q([r["path_noise_r"] for r in inner]),
            "error_over_envelope_fraction": (sum(r["error"] >= r["exit_envelope"] for r in inner) / len(inner)) if inner else None,
            "control_u_over_exit_control_fraction": (sum(r["control_u"] >= r["exit_control"] for r in inner) / len(inner)) if inner else None,
            "floor_raised_fraction": (sum(r["floor"] > -1.0 + 1e-9 for r in inner) / len(inner)) if inner else None,
            "mfe_r_at_last_step": _q([rs[-1]["mfe_r"] for rs in by_trade.values()]),
            "inner_exit_reasons": dict(Counter(r["reason"] for r in exit_rows)),
        },
        "mark_v_position": {
            "decisions": dict(Counter(f"{e['action']}:{e['reason']}" for e in pos_ev)),
            "controlling_limiter": dict(Counter(e.get("controlling_limiter") for e in pos_ev
                                                if e.get("fsr_selected") is not None)),
            "fsr_selected": _q([e.get("fsr_selected") for e in pos_ev]),
        },
        "advisory_exits_not_actuated": sum(e["event_type"] == "ADVISORY_EXIT_NOT_ACTUATED" for e in events),
        "protection": {
            "outcomes": len(rows["protection"]),
            "trips": sum(r["tripped_offline"] and r["consecutive_stops"] == 2 for r in rows["protection"]),
            "outcomes_while_tripped": sum(r["consecutive_stops"] > 2 for r in rows["protection"]),
            "admission": dict(Counter(f"{r['bay_protection_reason']}:{'ALLOWED' if r['allowed'] > 0 else 'BLOCKED'}"
                                      for r in rows["admission"])),
        },
        "governor_authority_report": report.get("governor_authority"),
        "micom": report.get("micom"),
    }


def run_one(worker, protocol, block, frames, feeds, symbols, bay_ids, authority, params, trace_dir):
    from canonical_parameter_registry import CanonicalParameterRegistry
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS

    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    errors = registry.validate_calibration_payload(params, engine="EXTERNAL")
    if errors:
        raise ValueError("invalid calibration payload: " + "; ".join(errors))
    equity = float(protocol["block_execution_contract"]["starting_equity_per_block"])
    plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
    # Identical to run_r5_step5_candidate.execute_block except the symbol set and authority.
    orch = Revision2ExternalEngineOrchestrator(
        symbols, registry, calibration_overrides=params, starting_equity=equity,
        grid_context_provider=SealedGridContextProvider(feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"]),
        real_plant_dcs=plant, plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
        telemetry_mode="compact", governor_authority=authority)
    tracer = Tracer(orch, plant, bay_ids)
    started = time.time()
    report = orch.run({s: frames[s] for s in symbols},
                      warmup=int(protocol["block_execution_contract"]["stock_warmup_bars_per_symbol"]))
    metrics = worker.metrics(report)
    summary = _summarize(tracer, report, bay_ids, metrics)
    summary["runtime_seconds"] = round(time.time() - started, 1)
    summary["trade_list_sha256"] = hashlib.sha256(json.dumps(
        report.get("trades", []), sort_keys=True, default=str).encode()).hexdigest()
    trace_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tracer.rows.items():
        with (trace_dir / f"{name}.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
    (trace_dir / "trades.json").write_text(json.dumps(report.get("trades", []), indent=1, default=str))
    (trace_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


def run_uninstrumented(protocol, frames, feeds, symbols, authority, params):
    """Same run without wrappers: proves the tracer is pass-through (identical trade list)."""
    from canonical_parameter_registry import CanonicalParameterRegistry
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS
    registry = CanonicalParameterRegistry()
    equity = float(protocol["block_execution_contract"]["starting_equity_per_block"])
    plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
    orch = Revision2ExternalEngineOrchestrator(
        symbols, registry, calibration_overrides=params, starting_equity=equity,
        grid_context_provider=SealedGridContextProvider(feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"]),
        real_plant_dcs=plant, plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
        telemetry_mode="compact", governor_authority=authority)
    report = orch.run({s: frames[s] for s in symbols},
                      warmup=int(protocol["block_execution_contract"]["stock_warmup_bars_per_symbol"]))
    return hashlib.sha256(json.dumps(report.get("trades", []), sort_keys=True, default=str).encode()).hexdigest()


def main(argv=None) -> int:
    from revision5.topology import FLEET_TOPOLOGY
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--block", type=int, action="append",
                        help="Stage A block number (repeatable); default 1")
    parser.add_argument("--bays", default="GTG1_HEAVY_INDUSTRY,GTG2_TECH_TELECOM")
    parser.add_argument("--authority", default="full,advisory")
    parser.add_argument("--params", type=Path, help="calibration payload JSON; default = protocol defaults")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/r5_governor_trace")
    parser.add_argument("--verify-passthrough", action="store_true",
                        help="also run each case uninstrumented and require an identical trade list")
    args = parser.parse_args(argv)

    out_dir = args.output_dir.resolve()
    if "r5_step5_stage_a" in str(out_dir):
        parser.error("refusing to write inside Stage A state")
    protocol = json.loads(args.protocol.read_text())
    params = (json.loads(args.params.read_text()) if args.params else
              {k: v["default"] for k, v in protocol["search_space"].items()})
    bay_ids = [b.strip() for b in args.bays.split(",") if b.strip()]
    unknown = [b for b in bay_ids if b not in FLEET_TOPOLOGY]
    if unknown:
        parser.error(f"unknown bays {unknown}; choose from {sorted(FLEET_TOPOLOGY)}")
    authorities = [a.strip() for a in args.authority.split(",") if a.strip()]
    if not set(authorities) <= {"full", "advisory"}:
        parser.error("--authority takes full and/or advisory")
    blocks = {int(b["block"]): b for b in protocol["sampling_plan"]["stage_a"]}
    worker = _load_worker()
    overview = {"protocol_id": protocol["protocol_id"], "params": params, "runs": []}
    for number in args.block or [1]:
        block = blocks[number]
        frames, feeds, audit = worker.prepare_block(ROOT, protocol, block)
        for bay_id in bay_ids:
            symbols = sorted(s for s in FLEET_TOPOLOGY[bay_id] if s in frames)
            for authority in authorities:
                trace_dir = out_dir / f"block{number}" / bay_id / authority
                print(f"block {number} {bay_id} ({len(symbols)} symbols) {authority} ...", flush=True)
                summary = run_one(worker, protocol, block, frames, feeds, symbols, [bay_id],
                                  authority, params, trace_dir)
                if args.verify_passthrough:
                    base = run_uninstrumented(protocol, frames, feeds, symbols, authority, params)
                    summary["passthrough_verified"] = base == summary["trade_list_sha256"]
                    (trace_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
                    if not summary["passthrough_verified"]:
                        raise SystemExit(f"TRACER_ALTERED_RESULT: {trace_dir}")
                m = summary["metrics"]
                overview["runs"].append({
                    "block": number, "bay": bay_id, "authority": authority, "symbols": symbols,
                    "sessions": block["sessions"], "slice_sha256": audit["slice_sha256"],
                    "trades": m["completed_trades"], "net_pnl": round(m["net_pnl"], 2),
                    "gross_pnl": round(m["gross_pnl"], 2), "profit_factor": m["profit_factor"],
                    "gain_verdict": summary["gain_schedule"]["verdict"],
                    "exit_reasons": summary["exit_reasons"], "sides": summary["sides"],
                    "trace_dir": str(trace_dir)})
                print(json.dumps(overview["runs"][-1], default=str), flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "overview.json").write_text(json.dumps(overview, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
