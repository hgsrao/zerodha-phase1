"""Live-like minute-by-minute offline replay of the R7 engine with governor adaptation (paper only).

    LIVE_TRADING_ENABLED=False PYTHONPATH=<root> python revision7_avr/run_r7_live_like_replay.py \
        {A|B|C} --out DIR [--minutes 60] [--warmup 60] [--end-offset-minutes 0] \
        [--r7-adaptation paper|shadow|off] [--data-dir DIR] [--synthetic]

Each completed exchange minute is delivered once, to every symbol that has a bar at that
timestamp, in chronological order, through the engine's own per-timestamp ``_process_tick``
(Engine A: the shared core's clock; B: Nautilus; C: Backtrader). No broker or Kite client is
constructed. One JSON line per minute goes to ``minutes.jsonl``; ``summary.json`` lists every
acceptance check and its result. ``--synthetic`` is a harness smoke only: the summary marks it
as not historical evidence.

Causality and restart evidence, from three runs on the same inputs:
(1) in every run timestamps strictly increase and every decision is for its own minute;
(2) the main run round-trips the R7 checkpoint through JSON and restores it at the halfway
    minute; a reference run without the restore must produce the same decisions throughout
    (R7 control state only: native journal persistence is not part of this check);
(3) a prefix run on frames truncated after the halfway minute must reproduce the first half's
    decisions byte for byte, so no later bar influenced them.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
GRID = Path("/home/srinivas/projects/zerodha-phase1/local_workspace/records/grid-manifest-local.json")
PROTECTIVE_EXIT = "r7_coordinated_exit"         # the only R7 normal exit; every other reason is native


def _dig(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def _base(engine):
    if engine == "A":
        from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
        return Revision2ExternalEngineOrchestrator
    if engine == "B":
        from engine_b.orchestrator import EngineBOrchestrator
        return EngineBOrchestrator
    from engine_c.orchestrator import EngineCOrchestrator
    return EngineCOrchestrator


def _live_like_class(base, recorder):
    from revision7_avr.r7_engine import engine_class

    class LiveLike(engine_class(base)):
        def _process_tick(self, timestamp, tick_events):
            events = list(tick_events)
            before = recorder.before(self)
            result = super()._process_tick(timestamp, events)
            recorder.after(self, timestamp, events, before)
            return result
    return LiveLike


class MinuteRecorder:
    """Per-minute evidence taken from the engine's own state before and after each tick."""

    def __init__(self, path, universe, checkpoint_ts=None):
        self.path, self.universe = path, sorted(universe)
        self.fh = open(path, "w", encoding="utf-8") if path else None
        self.checkpoint_ts = checkpoint_ts
        self.records, self.violations = [], []
        self.previous_ts = None
        self.checkpoint = None
        self.warmup_settled = 0

    def before(self, o):
        return {"decisions": o.r7_decision_count, "telemetry": len(getattr(o, "controller_telemetry", []) or []),
                "audit": len(getattr(o, "order_audit", []) or []), "trades": len(getattr(o, "completed_trades", []) or []),
                "counts": collections.Counter(o.r7_gate_counts)}

    def after(self, o, timestamp, events, before):
        ts = str(timestamp)
        if self.previous_ts is not None and not (_ts(timestamp) > _ts(self.previous_ts)):
            self.violations.append(f"non-increasing minute {ts} after {self.previous_ts}")
        if any(str(e.timestamp) != ts for e in events):
            self.violations.append(f"events from another timestamp delivered at {ts}")
        self.previous_ts = timestamp
        new = o.r7_decision_count - before["decisions"]
        decisions = list(o.r7_decisions)[-new:] if new else []
        for d in decisions:
            if _ts(d["timestamp"]) > _ts(timestamp):
                self.violations.append(f"future decision {d['timestamp']} committed at {ts}")
            elif _ts(d["timestamp"]) < _ts(timestamp):
                # Only the last warmup batch may settle late, at the head of the first tick.
                if self.records:
                    self.violations.append(f"decision for {d['timestamp']} committed late at {ts}")
                self.warmup_settled += 1
        tel = (getattr(o, "controller_telemetry", []) or [])[before["telemetry"]:]
        audit = (getattr(o, "order_audit", []) or [])[before["audit"]:]
        trades = (getattr(o, "completed_trades", []) or [])[before["trades"]:]
        delta = {k: v - before["counts"].get(k, 0) for k, v in o.r7_gate_counts.items() if v != before["counts"].get(k, 0)}
        symbols = sorted({e.symbol for e in events})
        adapt = [d.get("adaptation") for d in decisions if d.get("adaptation")]
        record = {
            "minute": len(self.records), "timestamp": ts,
            "coverage": {"symbols": len(symbols), "universe": len(self.universe),
                         "missing": sorted(set(self.universe) - set(symbols))},
            "adaptation": {
                "statuses": dict(collections.Counter(a["status"] for a in adapt)),
                "snapshot_versions": sorted({a["snapshot_version"] for a in adapt if a["snapshot_version"] is not None}),
                "parameter_hashes": sorted({a["parameter_hash"] for a in adapt}),
                "reasons": sorted({a["reason"] for a in adapt if a.get("reason")}),
                "inputs": adapt[0].get("environment") if adapt else None,
                "stress_index": adapt[0].get("stress_index") if adapt else None,
                "risk_derate_factor": adapt[0].get("risk_derate_factor") if adapt else None,
                "scales_by_bay": {d["bay_id"]: d["adaptation"].get("scales") for d in decisions if d.get("adaptation")},
            },
            "r7": [{"symbol": d["symbol"], "timestamp": d["timestamp"], "bar_index": d["bar_index"],
                    "pulse": d["pulse"], "fsm_reason": d["reason"],
                    "eligible": d["eligible"], "reference": d["reference"], "inputs": d["inputs"],
                    "governor": d["governor"], "allocation": d["allocation"],
                    "active_parameters": (d.get("adaptation") or {}).get("active_parameters"),
                    "parameter_hash": (d.get("adaptation") or {}).get("parameter_hash")} for d in decisions],
            "r7_decision_digest": o.r7_decision_digest,
            "gate_count_delta": delta,
            "utilization": next((d["allocation"].get("utilization") for d in decisions), None),
            "projected_utilization": next((d["allocation"].get("projected_utilization") for d in decisions), None),
            "orders": [{k: a.get(k) for k in ("event", "kind", "symbol", "side", "quantity", "status", "reason")}
                       for a in audit],
            "pending": sorted(set(getattr(o, "_symbol_working", {}) or {}) | set(getattr(o, "_symbol_inflight", {}) or {})),
            "open_positions": sorted(getattr(o, "open_trades", {}) or {}),
            "exits": [{"symbol": t.get("symbol"), "reason": t.get("reason") or t.get("exit_reason"),
                       "authority": "R7_NORMAL" if (t.get("reason") or t.get("exit_reason")) == PROTECTIVE_EXIT else "NATIVE",
                       "net_pnl": t.get("net_pnl")} for t in trades if isinstance(t, dict)],
            "native_events": dict(collections.Counter(str(e.get("event_type")) for e in tel
                                                      if not str(e.get("event_type", "")).startswith("R7_"))),
            "fail_closed_events": [e for e in (str(x.get("event_type")) for x in tel)
                                   if "FAIL" in e or "HALT" in e or "BLOCK" in e or "STALE" in e],
        }
        if self.checkpoint_ts is not None and _ts(timestamp) == _ts(self.checkpoint_ts):
            snap = json.loads(json.dumps(o.r7_snapshot(), allow_nan=False))
            o.r7_restore(snap)                                    # atomic restore of R7 control state
            again = json.loads(json.dumps(o.r7_snapshot(), allow_nan=False))
            self.checkpoint = {"minute": record["minute"], "timestamp": ts, "sha256": _dig(snap),
                               "restore_reproduces_snapshot": again == snap,
                               "adaptation": snap.get("adaptation"), "adaptation_state": snap.get("adaptation_state")}
            record["checkpoint"] = self.checkpoint
        self.records.append(record)
        if self.fh:
            self.fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.close()


def _ts(x):
    import pandas as pd
    return pd.Timestamp(x)


def _run(engine, data, nifty, vix, warmup, mode, recorder, log_path):
    from revision7_avr.r7_state import default_configs
    import engine_b.runner as rb
    import engine_c.runner as rc
    cls = _live_like_class(_base(engine), recorder)
    configs = default_configs(sorted(data))
    captured = []

    def factory(*args, **kw):
        o = cls(*args, r7_configs=configs, r7_adaptation_mode=mode,
                r7_decision_log_path=str(log_path) if log_path else None, **kw)
        captured.append(o)
        return o
    assert os.environ["LIVE_TRADING_ENABLED"] == "False"          # before any engine construction
    if engine == "A":
        from revision2_external.grid_context import SealedGridContextProvider
        from revision5.ccpp_unified_plant import CentralPlantMasterDCS
        o = factory(sorted(data), grid_context_provider=SealedGridContextProvider(nifty, vix),
                    real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                    plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="full",
                    governor_authority="full", starting_equity=1_000_000.0)
        r5 = o.run({s: data[s] for s in sorted(data)}, warmup=warmup)
        report = {"trades": r5["trades"], "orders": {"entry_submitted": r5["orders_submitted"],
                                                     "exit_submitted": r5["exit_orders_submitted"], "fills": r5["fills"]},
                  "net_pnl": r5.get("net_pnl"), "gross_pnl": r5.get("gross_pnl")}
    else:
        # Rebind the runner's orchestrator for this run only; restore it for the next run.
        runner, name = (rb, "EngineBOrchestrator") if engine == "B" else (rc, "EngineCOrchestrator")
        native = getattr(runner, name)
        setattr(runner, name, factory)
        try:
            report = (rb.run_engine_b(data, nifty, vix, warmup=warmup, telemetry_mode="full") if engine == "B"
                      else rc.run_engine_c(data, nifty, vix, warmup=warmup))
        finally:
            setattr(runner, name, native)
    o = captured[-1]
    o.r7_close_decision_log()
    recorder.close()
    return o, report


def main() -> int:
    from runtime.live_trading_guard import require_live_trading_disabled
    require_live_trading_disabled()
    p = argparse.ArgumentParser()
    p.add_argument("engine", choices=["A", "B", "C"])
    p.add_argument("--out", required=True)
    p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--end-offset-minutes", type=int, default=0,
                   help="drop this many final minutes, so the hour need not end at the session close")
    p.add_argument("--r7-adaptation", choices=["off", "shadow", "paper"], default="paper")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--synthetic", action="store_true")
    a = p.parse_args()
    if a.minutes < 2 or a.warmup < 1 or a.end_offset_minutes < 0:
        raise SystemExit("invalid window")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    from engine_c.data_contract import load_real, load_synthetic
    from revision5.topology import SYMBOL_TO_BAY
    universe = sorted(SYMBOL_TO_BAY)
    # Engine A's native clock never ticks a frame's final bar (it is only the next-open fill
    # price for the last decision minute), so A loads one bar beyond the replayed minutes.
    # Engines B and C deliver every bar as a venue event, the final one included.
    tail = 1 if a.engine == "A" else 0
    total = a.warmup + a.minutes + tail + a.end_offset_minutes
    data, nifty, vix, info = (load_synthetic(universe, total, a.warmup) if a.synthetic
                              else load_real(universe, total, a.data_dir, GRID, a.warmup))
    if a.end_offset_minutes:
        data = {s: f.iloc[:len(f) - a.end_offset_minutes].reset_index(drop=True) for s, f in data.items()}
    missing = sorted(set(universe) - set(data))
    # The replay minutes are the common clock's last `minutes` ticked timestamps.
    clock = sorted({t for f in data.values() for t in f["timestamp"]})
    window = clock[len(clock) - a.minutes - tail:len(clock) - tail]
    half_ts = window[a.minutes // 2 - 1]
    prefix_end = window[a.minutes // 2] if tail else half_ts

    t0 = time.time()
    rec = MinuteRecorder(out / "minutes.jsonl", universe, checkpoint_ts=half_ts)
    o, report = _run(a.engine, data, nifty, vix, a.warmup, a.r7_adaptation, rec, out / "r7_decisions.jsonl")
    full_secs = time.time() - t0
    # Minutes before the replay window are warmup/history ticks of the engine's own clock.
    live = [r for r in rec.records if _ts(r["timestamp"]) >= _ts(window[0])]
    reference = MinuteRecorder(None, universe)
    _run(a.engine, data, nifty, vix, a.warmup, a.r7_adaptation, reference, None)
    cut = {s: f[f["timestamp"] <= prefix_end].reset_index(drop=True) for s, f in data.items()}
    prefix_rec = MinuteRecorder(None, universe)
    _run(a.engine, cut, nifty, vix, a.warmup, a.r7_adaptation, prefix_rec, None)
    idx_half = next(i for i, r in enumerate(rec.records) if _ts(r["timestamp"]) == _ts(half_ts))
    full_half = [r["r7"] for r in rec.records[:idx_half + 1]]
    prefix_half = [r["r7"] for r in prefix_rec.records[:idx_half + 1]]

    configs = {s: c for s, c in o.r7_configs.items()}
    decisions = [json.loads(x) for x in (out / "r7_decisions.jsonl").read_text().splitlines()]
    live_decisions = [d for d in decisions if _ts(d["timestamp"]) >= _ts(window[0])]
    adapt = [d.get("adaptation") or {} for d in live_decisions]
    versions_per_minute = [r["adaptation"]["snapshot_versions"] for r in live]
    relaxed = [d["symbol"] for d in live_decisions if d.get("adaptation") and (
        d["adaptation"]["active_parameters"]["integral_limit"] > configs[d["symbol"]].integral_limit + 1e-15
        or d["adaptation"]["active_parameters"]["command_slew_per_second"] > configs[d["symbol"]].command_slew_per_second + 1e-15)]
    consumed = sum(1 for s, c in o.r7_symbol_controllers.items() if c.governor.config != configs[s])
    trades = report.get("trades") or getattr(o, "completed_trades", [])
    checks = {
        "live_trading_enabled_is_False": os.environ.get("LIVE_TRADING_ENABLED") == "False",
        "data_is_frozen_historical": (info.get("source") or {}).get("kind") == "FROZEN_NSE_48_1MIN",
        "all_48_symbols_loaded": not missing and len(data) == 48,
        "minutes_replayed": len(live) == a.minutes and [r["timestamp"] for r in live] == [str(t) for t in window],
        "every_minute_one_decision_per_delivered_symbol": all(
            sum(1 for d in r["r7"] if _ts(d["timestamp"] if "timestamp" in d else r["timestamp"]) == _ts(r["timestamp"]))
            >= r["coverage"]["symbols"] for r in live),
        "every_minute_full_coverage": all(not r["coverage"]["missing"] for r in live),
        "causal_order_and_own_minute_decisions": not (rec.violations or reference.violations or prefix_rec.violations),
        "prefix_run_reproduces_first_half": len(prefix_half) == idx_half + 1 and full_half == prefix_half,
        "checkpoint_restore_reproduces_snapshot": bool(rec.checkpoint and rec.checkpoint["restore_reproduces_snapshot"]),
        "restored_run_equals_reference_run": [r["r7"] for r in rec.records] == [r["r7"] for r in reference.records],
        "native_profiles_not_consumed": getattr(o, "dynamic_profile_mode", "off") == "off",
    }
    if a.r7_adaptation == "paper":
        checks.update({
            "adaptation_applied_every_live_decision": bool(adapt) and all(x.get("status") == "APPLIED" for x in adapt),
            "fresh_snapshot_every_live_minute": all(len(v) == 1 for v in versions_per_minute)
            and len({v[0] for v in versions_per_minute}) == len(versions_per_minute),
            "active_parameters_differ_from_base": any(x.get("parameter_hash") != x.get("base_parameter_hash") for x in adapt),
            "governor_runs_adapted_config": consumed == len(o.r7_symbol_controllers),
            "limits_never_relaxed": not relaxed,
            "adaptation_fail_closed_count_zero": o.r7_gate_counts.get("ADAPTATION_FAIL_CLOSED", 0) == 0,
        })
    exits = collections.Counter(("R7_NORMAL" if (t.get("reason") or t.get("exit_reason")) == PROTECTIVE_EXIT else "NATIVE",
                                 str(t.get("reason") or t.get("exit_reason"))) for t in trades if isinstance(t, dict))
    summary = {
        "engine": a.engine, "r7_adaptation": a.r7_adaptation, "seconds_full_run": round(full_secs, 1),
        "evidence_class": "HISTORICAL_LIVE_LIKE_PAPER" if not a.synthetic else "SYNTHETIC_HARNESS_SMOKE_NOT_EVIDENCE",
        "engine_clock": "final bar is fill price only" if tail else "every bar ticked",
        "source": info.get("source"), "window": {"first": str(window[0]), "last": str(window[-1]),
                                                 "minutes": a.minutes, "warmup_bars": a.warmup,
                                                 "end_offset_minutes": a.end_offset_minutes},
        "symbols": len(data), "missing_symbols": missing,
        "checks": checks, "status": "PASS" if all(checks.values()) else "FAIL",
        "violations": rec.violations + reference.violations + prefix_rec.violations,
        "warmup_decisions_settled_at_first_tick": rec.warmup_settled,
        "checkpoint": rec.checkpoint,
        "gate_counts": dict(o.r7_gate_counts),
        "orders": report.get("orders"), "net_pnl": report.get("net_pnl"), "trades": len(trades),
        "exits_by_authority": {f"{k[0]}:{k[1]}": v for k, v in exits.items()},
        "safety_contract_sha256": _dig(getattr(getattr(o, "safety_contract", None), "values", {})),
        "base_parameter_sha256": _dig({s: c.__dict__ for s, c in sorted(configs.items())}),
        "decision_count": o.r7_decision_count, "decision_digest": o.r7_decision_digest,
        "minutes_jsonl_sha256": hashlib.sha256((out / "minutes.jsonl").read_bytes()).hexdigest(),
        "r7_decisions_jsonl_sha256": hashlib.sha256((out / "r7_decisions.jsonl").read_bytes()).hexdigest(),
        "trades_sha256": _dig(trades),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: summary[k] for k in ("engine", "evidence_class", "status", "checks")}, default=str))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
