"""Offline Engine B / Engine C replay: native baseline vs R6 active-paper Unit Master.

    PYTHONPATH=<worktree> python revision6_avr/run_r6_native_replay.py {B|C} {baseline|active} \
        --symbols HCLTECH,INFY,TCS,WIPRO --bars 2000 --out out.json [--synthetic]

Uses the project's own runners (engine_b.runner.run_engine_b / engine_c.runner.run_engine_c) and
frozen data contract.  In ``active`` mode the runner module's orchestrator class name is rebound to
the R6 subclass for this process only; nothing on disk changes.  No broker, no Kite, offline only.
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
os.environ.setdefault("EA1_SHADOW_MODE", "True")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

GRID = Path("/home/srinivas/projects/zerodha-phase1/local_workspace/records/grid-manifest-local.json")


def main() -> int:
    from runtime.live_trading_guard import require_live_trading_disabled
    require_live_trading_disabled()
    p = argparse.ArgumentParser()
    p.add_argument("engine", choices=["B", "C"])
    p.add_argument("mode", choices=["baseline", "active"])
    p.add_argument("--revision", choices=["6", "7"], default="6")
    p.add_argument("--symbols", default="HCLTECH,INFY,TCS,WIPRO")
    p.add_argument("--bars", type=int, default=2000)
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--max-util", type=float, default=1.0)
    p.add_argument("--target-util", type=float, default=1.0)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    symbols = a.symbols.split(",")
    from engine_c.data_contract import load_real, load_synthetic
    data, nifty, vix, info = (load_synthetic(symbols, a.bars, a.warmup) if a.synthetic
                              else load_real(symbols, a.bars, None, GRID, a.warmup))
    captured = []
    if a.engine == "B":
        import engine_b.runner as runner
        native_cls, run = runner.EngineBOrchestrator, runner.run_engine_b
    else:
        import engine_c.runner as runner
        native_cls, run = runner.EngineCOrchestrator, runner.run_engine_c
    kwargs = {}
    if a.mode == "active":
        if a.revision == "7":
            from revision7_avr.r7_engine import r7_classes
            cls_b, cls_c = r7_classes()
        else:
            from r6_native_active import r6_classes
            cls_b, cls_c = r6_classes()
        r6_cls = cls_b if a.engine == "B" else cls_c

        def factory(*args, **kw):
            o = r6_cls(*args, r6_max_utilization=a.max_util, r6_target_utilization=a.target_util, **kw)
            captured.append(o)
            return o
    else:
        def factory(*args, **kw):
            o = native_cls(*args, **kw)
            captured.append(o)
            return o
    if a.engine == "B":
        runner.EngineBOrchestrator = factory
    else:
        runner.EngineCOrchestrator = factory
    t0 = time.time()
    report = run(data, nifty, vix, warmup=a.warmup, orchestrator_kwargs=kwargs)
    secs = time.time() - t0
    o = captured[-1]
    tel = o.controller_telemetry
    native_tel = [e for e in tel if e["event_type"] != "R6_UNIT_MASTER_ENTRY"]
    dig = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()
    ledger = report.get("r5_ledger", {})
    gov = collections.Counter((e.get("action"), e.get("reason")) for e in tel if e["event_type"] == "GOVERNOR_ENTRY_DECISION")
    fsr = collections.Counter(round(float(e.get("size_multiplier") or 0), 3) for e in tel
                              if e["event_type"] == "GOVERNOR_ENTRY_DECISION" and e.get("action") == "ENTRY")
    plant = collections.Counter(str(e.get("reason")).split(":")[0] if not e.get("admitted") else "ADMITTED"
                                for e in tel if e["event_type"] == "PLANT_DCS_ENTRY")
    audit_events = collections.Counter(str(e.get("event")) for e in o.order_audit)
    out = {
        "engine": a.engine, "mode": a.mode, "revision": a.revision, "seconds": round(secs, 1), "source": info.get("source"),
        "modes": {"r7_adaptation_enabled": getattr(o, "r7_adaptation_enabled", None),
                  "r7_adaptation_mode": getattr(o, "r7_adaptation_mode", None),
                  **{k: getattr(o, k, None) for k in ("full_adaptation", "governor_authority", "governor_position_mode",
                                                   "avr_voltage_mode", "dispatcher_allocation_mode",
                                                   "dynamic_profile_mode", "avr_mvar_limiter_mode",
                                                   "entry_fill_anchor_mode", "unit_protection_mode",
                                                   "final_exit_authority", "closed_loop_mode")}},
        "r7": {"class": type(o).__name__, "adaptation_enabled": getattr(o, "r7_adaptation_enabled", None),
               "adaptation_mode": getattr(o, "r7_adaptation_mode", None),
               "decisions": getattr(o, "r7_decisions", [])},
        "r6": {"class": type(o).__name__, "max_util": getattr(o, "r6_max_utilization", None),
               "target_util": getattr(o, "r6_target_utilization", None),
               "counts": dict(getattr(o, "r6_counts", {})), "decisions": getattr(o, "r6_decisions", []),
               "bindings": getattr(o, "_r6_bindings", {}),
               "unsettled_at_end": {b: list(g.reserved) for b, g in getattr(o, "_r6_gates", {}).items() if g.reserved}},
        "funnel": ledger.get("funnel"), "orders": report.get("orders"),
        "trades": report.get("trades"), "exit_reasons": ledger.get("exit_reasons"),
        "net_pnl": ledger.get("net_pnl"), "gross_pnl": ledger.get("gross_pnl"), "costs": ledger.get("costs"),
        "reconciliation": report.get("reconciliation"),
        "governor_entry": {f"{k[0]}:{k[1]}": v for k, v in gov.items()},
        "fsr_distribution_entry": {str(k): v for k, v in sorted(fsr.items())},
        "plant_entry": dict(plant), "order_audit_events": dict(audit_events),
        "native_telemetry_sha256": dig(native_tel), "native_telemetry_len": len(native_tel),
        "order_audit": o.order_audit,
    }
    Path(a.out).write_text(json.dumps(out, default=str, indent=1))
    print(json.dumps({k: out[k] for k in ("engine", "mode", "seconds", "funnel", "orders", "net_pnl", "exit_reasons",
                                           "governor_entry", "fsr_distribution_entry", "plant_entry",
                                           "order_audit_events", "native_telemetry_sha256")}
                     | {"r6_counts": out["r6"]["counts"]}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
