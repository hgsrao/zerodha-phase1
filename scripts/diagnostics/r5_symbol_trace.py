#!/usr/bin/env python3
"""R5 single-symbol trace: what every controller does from signal to entry, through each held bar,
to exit -- and which of them actually changes the outcome.

Replays one sealed V2 block (all symbols, so portfolio interactions are real) with a passive
observer, then keeps, for ONE symbol:

  * per bar: the PA reading and each choke-point decision (ID, governor, risk, pre-submit) with
    its exact reason;
  * per candidate that reaches MPC: the entry PID (setpoint = rolling mean of the symbol's own
    confidence, measurement = this confidence, P/I/D, output) and what it actuates -- the size
    multiplier and the execution-price shift -- plus the exit PID's stop/target tightness;
  * per held bar: the governor's position decision (measured R, MFE, protected floor, FSR
    limiters, HOLD/EXIT and why), the exit PID's per-bar telemetry, and whether its advice was
    actuated;
  * the trade: fill, planned stop/target, exit price and reason, R and net P&L.

Neutrality is proven as in the funnel: the block's trade ledger must match the sealed V2 result
field by field, or nothing is written.

    python scripts/diagnostics/r5_symbol_trace.py --stage B --block 1 \\
        --params <trial_007 params>.json --v2-reference <finalist_1_trial_007_result>.json \\
        [--symbol INFY]          # default: the symbol with the most trades in the block
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("r5_gate_funnel_for_trace", ROOT / "scripts/diagnostics/r5_gate_funnel.py")
funnel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(funnel)

ENTRY_EVENTS = ("ENTRY_CONFIDENCE_THROTTLE", "GOVERNOR_ENTRY_DECISION", "POSITION_SIZING",
                "DYNAMIC_SIZE_ACTUATION", "FINAL_EXECUTION_ENTRY_DECISION")
HOLD_EVENTS = ("GOVERNOR_POSITION_DECISION", "EXIT_PROTECTION_UPDATE", "ADVISORY_EXIT_NOT_ACTUATED",
               "GOVERNOR_EXIT_ARMED", "CONTROLLER_PATH_EXIT_ARMED", "TRADE_PATH_STOP_ACTUATION",
               "FINAL_EXECUTION_EXIT_DECISION")


class EventTap:
    """Wraps the orchestrator's controller-event recorder; keeps everything for all symbols in a
    compact per-symbol list.  Calls the original first and never alters its arguments."""

    def __init__(self):
        self.events = []

    def __call__(self, orch):
        original = orch._record_controller_event

        def tapped(event_type, timestamp, symbol, payload):
            original(event_type, timestamp, symbol, payload)
            self.events.append({"event_type": event_type, "timestamp": str(timestamp), "symbol": symbol,
                                **json.loads(json.dumps(payload, default=str))})
        orch._record_controller_event = tapped


def _f(x, fmt="+.3f"):
    try:
        return format(float(x), fmt)
    except (TypeError, ValueError):
        return "n/a"


def build_trace(symbol, trades, events, decisions):
    sym_events = [e for e in events if e["symbol"] == symbol]
    by_candidate, by_trade = {}, {}
    for e in sym_events:
        if e.get("candidate_id"):
            by_candidate.setdefault(e["candidate_id"], []).append(e)
        if e.get("trade_id"):
            by_trade.setdefault(e["trade_id"], []).append(e)
    sym_decisions = [d for d in decisions if d[1] == symbol]
    out_trades = []
    for t in trades:
        if t.get("symbol") != symbol:
            continue
        cand = by_candidate.get(t.get("candidate_id"), [])
        entry = {e["event_type"]: e for e in cand if e["event_type"] in ENTRY_EVENTS}
        held = [e for e in by_trade.get(t.get("trade_id"), []) + cand
                if e["event_type"] in HOLD_EVENTS]
        seen, hold = set(), []
        for e in sorted(held, key=lambda e: e["timestamp"]):
            key = (e["event_type"], e["timestamp"])
            if key not in seen:
                seen.add(key)
                hold.append(e)
        risk = abs(float(t["entry_price"]) - float(t.get("planned_stop_price") or t["entry_price"])) or None
        sign = 1.0 if t["side"] == "BUY" else -1.0
        out_trades.append({
            "trade": t, "entry": entry, "hold": hold,
            "realised_r": (sign * (float(t["exit_price"]) - float(t["entry_price"])) / risk) if risk else None,
        })
    # Per-candidate entry-PID readings, for every candidate of this symbol (traded or not).
    pid = [e for e in sym_events if e["event_type"] == "ENTRY_CONFIDENCE_THROTTLE"]
    governor_hold = Counter(f"{e.get('action')}:{e.get('reason')}" for e in sym_events
                            if e["event_type"] == "GOVERNOR_POSITION_DECISION")
    advisory = Counter(e.get("reason") for e in sym_events if e["event_type"] == "ADVISORY_EXIT_NOT_ACTUATED")
    choke = Counter((d[0], d[4], "PASS" if d[5] else funnel.normalise_reason(d[6])) for d in sym_decisions)
    return {
        "symbol": symbol, "trades": out_trades,
        "entry_pid": [{k: e.get(k) for k in ("timestamp", "pa_confidence", "entry_setpoint", "entry_measurement",
                                              "entry_error", "entry_p", "entry_i", "entry_d", "entry_adjustment",
                                              "entry_clamped", "entry_timing_multiplier", "exit_tightness",
                                              "execution_market_price", "planned_entry_price")} for e in pid],
        "governor_position_decisions": dict(governor_hold),
        "exit_pid_advice_not_actuated": dict(advisory),
        "choke_points": {f"{p}|{s}|{r}": n for (p, s, r), n in sorted(choke.items())},
    }


def print_trace(tr):
    print(f"\n=== SYMBOL TRACE {tr['symbol']} ===")
    print("choke-point decisions (point|side|reason: count):")
    for k, n in tr["choke_points"].items():
        print(f"  {k}: {n}")
    pid = tr["entry_pid"]
    if pid:
        adj = [abs(float(p["entry_adjustment"] or 0)) for p in pid]
        mult = [float(p["entry_timing_multiplier"] or 1) for p in pid]
        tight = [float(p["exit_tightness"] or 1) for p in pid]
        clamped = sum(1 for p in pid if p.get("entry_clamped"))
        print(f"entry PID over {len(pid)} candidates: |output| mean {sum(adj)/len(adj):.4f} max {max(adj):.4f}; "
              f"size multiplier min {min(mult):.3f} mean {sum(mult)/len(mult):.3f}; "
              f"exit tightness min {min(tight):.3f} mean {sum(tight)/len(tight):.3f}; clamped {clamped}")
    print(f"governor position decisions: {tr['governor_position_decisions']}")
    print(f"exit-PID exits advised but NOT actuated (full governor authority): {tr['exit_pid_advice_not_actuated']}")
    for i, x in enumerate(tr["trades"], 1):
        t, e = x["trade"], x["entry"]
        thr = e.get("ENTRY_CONFIDENCE_THROTTLE", {})
        gov = e.get("GOVERNOR_ENTRY_DECISION", {})
        comp = gov.get("comparator") or {}
        print(f"\n--- trade {i}: {t['side']} qty {t['quantity']}  entry {t['entry_timestamp']} @ {_f(t['entry_price'], '.2f')}"
              f"  stop {_f(t.get('planned_stop_price'), '.2f')}  target {_f(t.get('planned_target_price'), '.2f')}")
        print(f"  ENTRY  PA conf {_f(thr.get('pa_confidence'))}  ID rr {_f(thr.get('id_risk_reward_ratio'))}"
              f"  | entry PID: setpoint {_f(thr.get('entry_setpoint'))} meas {_f(thr.get('entry_measurement'))}"
              f" err {_f(thr.get('entry_error'))} P/I/D {_f(thr.get('entry_p'))}/{_f(thr.get('entry_i'))}/{_f(thr.get('entry_d'))}"
              f" out {_f(thr.get('entry_adjustment'), '+.4f')} -> size x{_f(thr.get('entry_timing_multiplier'), '.3f')},"
              f" stop/target x{_f(thr.get('exit_tightness'), '.3f')}")
        print(f"         governor: {gov.get('reason')}  signed z {_f(comp.get('signed_z'))} <= limit {_f(comp.get('signed_z_limit'))}"
              f"  FSR {_f(gov.get('fsr_selected'))} (ctrl {gov.get('controlling_limiter')}) >= hurdle {_f(gov.get('entry_hurdle'))}"
              f"  size x{_f(gov.get('size_multiplier'), '.3f')}")
        print(f"  HOLD   {'bar':>3} {'time':<26} {'R':>7} {'MFE':>7} {'floor':>7} {'FSR':>6} {'ctrl':<5} decision")
        k = 0
        for h in x["hold"]:
            if h["event_type"] == "GOVERNOR_POSITION_DECISION":
                k += 1
                print(f"         {k:>3} {h['timestamp']:<26} {_f(h.get('measured_r'), '+.2f'):>7} {_f(h.get('max_favorable_r'), '+.2f'):>7}"
                      f" {_f(h.get('protected_r_floor'), '+.2f'):>7} {_f(h.get('fsr_selected'), '.2f'):>6}"
                      f" {str(h.get('controlling_limiter'))[:5]:<5} {h.get('action')}:{h.get('reason')}")
            elif h["event_type"] == "ADVISORY_EXIT_NOT_ACTUATED":
                print(f"             {h['timestamp']:<26} exit PID advised EXIT ({h.get('reason')}) -- not actuated")
            elif h["event_type"] == "EXIT_PROTECTION_UPDATE" and h.get("pa_output") is not None:
                print(f"             {h['timestamp']:<26} exit PID: pa err {_f(h.get('pa_error'))} out {_f(h.get('pa_output'))}"
                      f" tightness {_f(h.get('combined_tightness'), '.3f')} stop {_f(h.get('stop_before'), '.2f')}->{_f(h.get('stop_after'), '.2f')}"
                      f" (advisory under full governor)")
        print(f"  EXIT   {t['exit_timestamp']} @ {_f(t['exit_price'], '.2f')}  reason {t['reason']}  bars {t.get('bars_held')}"
              f"  R {_f(x['realised_r'], '+.2f')}  MFE {_f(t.get('mfe_r'), '+.2f')}R  net {_f(t.get('net_pnl'), '+.1f')}"
              f" (costs {_f(t.get('costs'), '.1f')})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--protocol", type=Path, default=funnel.DEFAULT_PROTOCOL)
    parser.add_argument("--stage", choices=["A", "B"], required=True)
    parser.add_argument("--block", type=int, required=True)
    parser.add_argument("--params", type=Path, required=True)
    parser.add_argument("--v2-reference", type=Path, required=True)
    parser.add_argument("--symbol", help="default: the symbol with the most trades in the block")
    parser.add_argument("--label", default=None)
    args = parser.parse_args(argv)

    if any("r5_step6" in str(p) for p in (args.params, args.v2_reference, args.protocol)):
        parser.error("holdout quarantine: Step-6 verification state may not be read")
    protocol_sha = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    protocol = json.loads(args.protocol.read_text())
    params = json.loads(args.params.read_text())
    if set(params) != set(funnel.V2_SURFACE):
        parser.error(f"--params must contain exactly {sorted(funnel.V2_SURFACE)}")
    blocks = [b for b in protocol["sampling_plan"]["stage_a" if args.stage == "A" else "stage_b"]
              if int(b["block"]) == args.block]
    if not blocks:
        parser.error(f"no block {args.block} in stage {args.stage}")
    block = blocks[0]
    reference = json.loads(args.v2_reference.read_text())
    funnel.validate_reference(reference, protocol_sha, args.stage, params, blocks)
    worker = funnel._load("scripts/run_r5_step5_candidate.py", "r5_step5_worker_for_trace")

    tap = EventTap()
    print(f"block {block['block']} {block['sessions'][0]}..{block['sessions'][-1]} ...", flush=True)
    _, report, recorder = funnel.replay_block(worker, protocol, block, params, instrument=tap)
    ref = next(b for b in reference["blocks"] if int(b["block"]) == int(block["block"]))
    problems = funnel.ledger_parity(report["trades"], ref["trades"])
    if funnel.ledger_sha256(report["trades"]) != funnel.ledger_sha256(ref["trades"]) or problems:
        raise SystemExit(f"OBSERVER_NOT_NEUTRAL block {block['block']}: {problems}")
    trades = report["trades"]
    symbol = args.symbol or Counter(t["symbol"] for t in trades).most_common(1)[0][0]
    trace = build_trace(symbol, trades, tap.events, recorder.decisions)
    label = args.label or f"stage_{args.stage.lower()}_block{args.block}_{symbol}"
    out_dir = (funnel.OUTPUT_ROOT / f"symbol_trace_{label}").resolve()
    if funnel.OUTPUT_ROOT.resolve() not in out_dir.parents:
        parser.error("output must stay under outputs/diagnostics/")
    out_dir.mkdir(parents=True, exist_ok=True)
    document = {"label": label, "stage": args.stage, "block": int(block["block"]), "params": params,
                "protocol_sha256": protocol_sha, "ledger_sha256": funnel.ledger_sha256(trades),
                "block_trades": len(trades), "trades_by_symbol": dict(Counter(t["symbol"] for t in trades)),
                **trace}
    (out_dir / "trace.json").write_text(json.dumps(document, indent=1, default=str))
    print(f"neutral: ledger matches the sealed reference ({len(trades)} trades)")
    print(f"trades by symbol: {document['trades_by_symbol']}")
    print_trace(trace)
    print(f"\nwrote {out_dir / 'trace.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
