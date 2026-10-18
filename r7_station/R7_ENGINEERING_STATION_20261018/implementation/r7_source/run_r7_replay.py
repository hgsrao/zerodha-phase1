"""Offline Engine B / Engine C historical replay for Revision 7 (no broker, no Kite).

    PYTHONPATH=<root> python revision7_avr/run_r7_replay.py {B|C} {native|r7} --out DIR \
        [--symbols ALL|A,B] [--bars 390] [--warmup 60] [--synthetic] [--profile installed|validation] \
        [--root <repo root to import R7/native from>]

Uses the project's own runners (engine_b.runner.run_engine_b / engine_c.runner.run_engine_c)
and the frozen data contract. In ``r7`` mode the runner module's orchestrator class is
rebound, for this process only, to ``engine_class(base=<native orchestrator>)``. ``--root``
lets the same harness drive a different checkout (e.g. the frozen baseline export), so an
original/modified comparison uses identical harness code. Nothing on disk changes except
files written under --out.
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
os.environ["LIVE_TRADING_ENABLED"] = "False"
os.environ.setdefault("EA1_SHADOW_MODE", "True")
GRID = Path("/home/srinivas/projects/zerodha-phase1/local_workspace/records/grid-manifest-local.json")


def _configs(symbols, profile):
    from revision5.topology import SYMBOL_TO_BAY, FLEET_TOPOLOGY
    import revision7_avr.r7_state as st
    if hasattr(st, "default_configs"):
        configs = st.default_configs(symbols)
        if profile == "validation":
            configs = [st.validation_config(c) for c in configs]
        return configs
    # Frozen baseline: droop was locked at 0.04 and no registry builder existed. Use the same
    # registry budgets so only the controller code differs between the compared runs.
    from revision5.controller_parameters import default_controller_parameters
    specs = default_controller_parameters().governor_specs
    if profile == "validation":
        raise SystemExit("validation profile requires the corrected R7 code")
    return [st.ControlConfig(s, SYMBOL_TO_BAY[s], specs[SYMBOL_TO_BAY[s]].capital_weight
                             / len(FLEET_TOPOLOGY[SYMBOL_TO_BAY[s]])) for s in symbols]


PROTECTIVE_HINTS = ("stop", "target", "max_hold", "mis_session_close", "SESSION_SQUAREOFF", "plant_trip",
                    "governor_exit:", "regime_stressed_exit", "saturation_exit_", "controller_path_exit",
                    "final_controller_exit:", "force", "reconcil")


def _extended(o, a, trades, tel, log_path):
    """Acceptance evidence common to native and R7 runs."""
    from revision5.topology import SYMBOL_TO_BAY
    ext = {"effective_profile": {
        "plant_engine_mode": getattr(o, "plant_engine_mode", None),
        "no_entry_cutoff_time": (getattr(o, "safety_contract", None).values.get("no_entry_cutoff_time")
                                 if getattr(o, "safety_contract", None) else None),
        "max_gross_exposure_fraction": (getattr(o, "safety_contract", None).values.get("max_gross_exposure_fraction")
                                        if getattr(o, "safety_contract", None) else None),
        "max_exposure_per_symbol_fraction": (getattr(o, "safety_contract", None).values.get("max_exposure_per_symbol_fraction")
                                             if getattr(o, "safety_contract", None) else None),
        "live_trading_enabled": os.environ.get("LIVE_TRADING_ENABLED")}}
    broker = getattr(o, "broker", None)
    ext["cost_assumptions"] = {"r5_slippage_fraction": getattr(broker, "slippage_fraction", None),
                               "engine_cost_model": "Engine C: Backtrader NSEEquityIntradayCommission + slippage_perc"
                               if a.engine == "C" else "Engine B: Nautilus R5LegCostFeeModel"}
    curve = [float(x) for x in getattr(o, "_equity_curve", []) or []]
    peak, mdd = (curve[0] if curve else 0.0), 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = max(mdd, (peak - x) / peak if peak > 0 else 0.0)
    ext["drawdown"] = {"realized_equity_max_drawdown_fraction": mdd,
                       "mtm_max_drawdown_fraction": getattr(o, "_mtm_max_drawdown_fraction", None)}
    by_bay = collections.defaultdict(lambda: {"trades": 0, "net_pnl": 0.0, "long": 0, "short": 0})
    for t in trades:
        if not isinstance(t, dict):
            continue
        b = by_bay[SYMBOL_TO_BAY.get(t.get("symbol"), "UNMAPPED")]
        b["trades"] += 1
        b["net_pnl"] += float(t.get("net_pnl") or 0.0)
        b["long" if t.get("side") == "BUY" else "short"] += 1
    ext["per_bay_trades"] = dict(by_bay)
    reasons = collections.Counter(str(t.get("reason")) for t in trades if isinstance(t, dict))
    ext["exits"] = {"r7_coordinated": reasons.get("r7_coordinated_exit", 0),
                    "native_protective_or_native_other": sum(v for k, v in reasons.items() if k != "r7_coordinated_exit"),
                    "by_reason": dict(reasons),
                    "native_exit_advisories": sum(1 for e in tel if e.get("event_type") == "R7_NATIVE_EXIT_ADVISORY")}
    # Duplicate-fill / exposure-violation checks from the order audit and trades.
    audit = getattr(o, "order_audit", [])
    filled, requested = collections.Counter(), {}
    for e in audit:
        key = e.get("client_order_id") or e.get("order_id") or e.get("bt_ref")
        if e.get("event") == "SUBMIT":
            requested[key] = int(e.get("quantity") or 0)
        if e.get("event") == "FILLED":
            filled[key] += int(e.get("quantity") or 0)
    overfills = [k for k, q in filled.items() if k in requested and q > requested[k]]
    ids = [t.get("trade_id") for t in trades if isinstance(t, dict)]
    ext["integrity"] = {"overfilled_orders": len(overfills), "duplicate_trade_ids": len(ids) - len(set(ids)),
                        "audit_events": len(audit)}
    if a.mode == "r7":
        from hashlib import sha256
        digest, rows = "0" * 64, []
        if log_path.exists():
            for line in log_path.read_text().splitlines():
                digest = sha256((digest + line).encode()).hexdigest()
                rows.append(json.loads(line))
            ext["decision_journal"] = {"lines": len(rows), "chain_matches_engine": digest == getattr(o, "r7_decision_digest", None),
                                       "count_matches_engine": len(rows) == getattr(o, "r7_decision_count", None)}
        else:   # frozen baseline: no external journal; use its in-memory decision list
            rows = json.loads(json.dumps(list(getattr(o, "r7_decisions", [])), default=str))
            ext["decision_journal"] = {"lines": len(rows), "external_journal": False}
        gov = [r["governor"] for r in rows if r.get("eligible")]
        sat = sum(1 for g in gov if abs(g["applied"]) >= 0.999 or abs(g["requested"]) >= 0.999)
        ext["pid"] = {"eligible": len(gov), "saturated": sat,
                      "mean_abs_p": sum(abs(g["p"]) for g in gov) / max(1, len(gov)),
                      "mean_abs_i": sum(abs(g["i"]) for g in gov) / max(1, len(gov)),
                      "mean_abs_d": sum(abs(g["d"]) for g in gov) / max(1, len(gov)),
                      "max_abs_i": max((abs(g["i"]) for g in gov), default=0.0),
                      "integration_frozen": sum(1 for g in gov if g.get("integration_frozen"))}
        last, trans, hunting, last_change = {}, 0, 0, {}
        for r in rows:
            s_ = r["symbol"]
            if s_ in last and r["pulse"] != last[s_]:
                trans += 1
                if s_ in last_change and r["bar_index"] - last_change[s_] <= 3:
                    hunting += 1
                last_change[s_] = r["bar_index"]
            last[s_] = r["pulse"]
        ext["fsm"] = {"transitions": trans, "transitions_within_3_bars": hunting}
        allocs = [r["allocation"] for r in rows if r.get("allocation")]
        ua = [x["utilization"] for x in allocs if x.get("utilization") is not None]
        up = [x["projected_utilization"] for x in allocs if x.get("projected_utilization") is not None]
        cases = collections.Counter(x.get("case") for x in allocs)
        granted = collections.defaultdict(float)
        for r in rows:
            if r.get("allocation"):
                granted[r["bay_id"]] += float(r["allocation"].get("granted_notional") or 0.0)
        ext["allocation"] = {"max_filled_utilization": max(ua, default=None),
                             "max_projected_utilization": max(up, default=None),
                             "bars_with_pending_reservation": sum(1 for x, y in zip(ua, up) if y > x),
                             "cases": dict(cases),
                             "rejected_demand_notional": sum(float(x.get("rejected_demand_notional") or 0) for x in allocs),
                             "fail_closed": sum(1 for x in allocs if x.get("measurement_error")),
                             "granted_notional_by_bay": dict(granted)}
        caus = [r["inputs"] for r in rows if r.get("inputs")]
        ext["grid_direction"] = {"available": sum(1 for c in caus if c.get("dispatch_available")),
                                 "scaling_errors": sum(1 for c in caus if c.get("grid_deviation") is not None and
                                                       abs(c["grid_direction_signal"] - max(-1.0, min(1.0, c["grid_deviation"] / 0.01))) > 1e-12)}
        try:
            snap = json.loads(json.dumps(o.r7_snapshot(), default=str))
            o.r7_restore(snap)
            ext["checkpoint_roundtrip"] = {"ok": json.loads(json.dumps(o.r7_snapshot())) == snap,
                                           "bytes": len(json.dumps(snap))}
        except Exception as exc:          # recorded, not hidden
            ext["checkpoint_roundtrip"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return ext


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("engine", choices=["B", "C"])
    p.add_argument("mode", choices=["native", "r7"])
    p.add_argument("--symbols", default="ALL")
    p.add_argument("--bars", type=int, default=390)
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--profile", choices=["installed", "validation"], default="installed")
    p.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    p.add_argument("--out", required=True)
    a = p.parse_args()
    sys.path.insert(0, a.root)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    from engine_c.data_contract import load_real, load_synthetic
    from revision5.topology import SYMBOL_TO_BAY
    symbols = sorted(SYMBOL_TO_BAY) if a.symbols == "ALL" else a.symbols.split(",")
    data, nifty, vix, info = (load_synthetic(symbols, a.bars, a.warmup) if a.synthetic
                              else load_real(symbols, a.bars, None, GRID, a.warmup))
    if a.engine == "B":
        import engine_b.runner as runner
        native_cls, run = runner.EngineBOrchestrator, runner.run_engine_b
    else:
        import engine_c.runner as runner
        native_cls, run = runner.EngineCOrchestrator, runner.run_engine_c
    captured = []
    log_path = out / "r7_decisions.jsonl"
    if a.mode == "r7":
        from revision7_avr.r7_engine import engine_class
        cls = engine_class(native_cls)
        configs = _configs(symbols, a.profile)
        import inspect
        accepts_log = "r7_decision_log_path" in inspect.signature(cls.__init__).parameters or \
            "r7_decision_log_path" in inspect.signature(cls.__mro__[0].__init__).parameters
        if log_path.exists():
            log_path.unlink()

        def factory(*args, **kw):
            extra = {"r7_decision_log_path": str(log_path)} if accepts_log else {}
            o = cls(*args, r7_configs=configs, **extra, **kw)
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
    assert os.environ["LIVE_TRADING_ENABLED"] == "False"
    t0 = time.time()
    report = run(data, nifty, vix, warmup=a.warmup)
    secs = time.time() - t0
    o = captured[-1]
    if hasattr(o, "r7_close_decision_log"):
        o.r7_close_decision_log()
    tel = getattr(o, "controller_telemetry", [])
    native_tel = [e for e in tel if not str(e.get("event_type", "")).startswith("R7_")]
    dig = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()
    ledger = report.get("r5_ledger", {}) or {}
    trades = report.get("trades") or getattr(o, "completed_trades", [])
    exits = collections.Counter(str(t.get("reason") or t.get("exit_reason")) for t in trades if isinstance(t, dict))
    plant = collections.Counter(str(e.get("reason")).split(":")[0] if not e.get("admitted") else "ADMITTED"
                                for e in tel if e.get("event_type") == "PLANT_DCS_ENTRY")
    entry_sides = collections.Counter(str(e.get("side")) for e in getattr(o, "order_audit", [])
                                      if e.get("event") == "SUBMIT" and e.get("kind") == "entry")
    summary = {
        "engine": a.engine, "mode": a.mode, "profile": a.profile, "root": a.root, "seconds": round(secs, 1),
        "source": info.get("source"), "symbols": len(symbols),
        "plant_engine_mode": getattr(o, "plant_engine_mode", None),
        "funnel": ledger.get("funnel"), "orders": report.get("orders"),
        "net_pnl": ledger.get("net_pnl"), "gross_pnl": ledger.get("gross_pnl"), "costs": ledger.get("costs"),
        "exit_reasons": dict(exits), "entry_sides": dict(entry_sides), "plant_entry": dict(plant),
        "reconciliation": report.get("reconciliation"),
        "native_telemetry_sha256": dig(native_tel), "native_telemetry_len": len(native_tel),
        "order_audit_sha256": dig(getattr(o, "order_audit", [])),
        "trades_sha256": dig(trades),
    }
    if a.mode == "r7":
        summary["r7"] = {"gate_counts": dict(getattr(o, "r7_gate_counts", {})),
                         "decision_count": getattr(o, "r7_decision_count", len(getattr(o, "r7_decisions", []))),
                         "decision_digest": getattr(o, "r7_decision_digest", None),
                         "decision_log": str(log_path) if log_path.exists() else None}
        decisions = ([json.loads(x) for x in log_path.read_text().splitlines()] if log_path.exists()
                     else list(getattr(o, "r7_decisions", [])))
        pulses = collections.Counter(d["pulse"] for d in decisions)
        util = [d["allocation"]["utilization"] for d in decisions
                if d.get("allocation") and d["allocation"].get("utilization") is not None]
        summary["r7"].update(
            pulses=dict(pulses), eligible=sum(bool(d["eligible"]) for d in decisions),
            max_utilization=max(util) if util else None,
            mean_utilization=(sum(util) / len(util)) if util else None,
            fail_closed=sum(1 for d in decisions if d.get("allocation", {}).get("measurement_error")),
            capacity_limited=sum(1 for d in decisions if abs(d.get("target", 0)) + 1e-12 < abs(d.get("reference", d.get("target", 0)))),
            saturated_applied=sum(1 for d in decisions if abs(d["signal"]) >= 0.999),
            decisions_sha256=dig(decisions))
    summary.update(_extended(o, a, trades, tel, log_path))
    summary["trades"] = [{k: t.get(k) for k in ("symbol", "side", "entry_timestamp", "exit_timestamp", "quantity",
                                                "entry_price", "exit_price", "reason", "net_pnl", "costs")}
                         for t in trades if isinstance(t, dict)]
    audit = (info.get("audit") or {})
    windows = [v.get("window", {}) for v in audit.values() if isinstance(v, dict)]
    summary["data_window"] = {"symbols": len(windows),
                              "first": min((str(w.get("first")) for w in windows), default=None),
                              "last": max((str(w.get("last")) for w in windows), default=None),
                              "sessions_min": min((w.get("sessions", 0) for w in windows), default=None),
                              "sessions_max": max((w.get("sessions", 0) for w in windows), default=None),
                              "missing_minutes_total": sum(int(w.get("missing_minutes") or 0) for w in windows)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
