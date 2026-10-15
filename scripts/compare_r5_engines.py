"""Read-only three-engine comparison of independently generated R5 reports.

    PYTHONPATH="$PWD" python scripts/compare_r5_engines.py \\
        --engine-a local_workspace/validation/engine_a/engine_a_report.json \\
        --engine-b local_workspace/validation/engine_b/engine_b_report.json \\
        --engine-c local_workspace/validation/engine_c/engine_c_report.json \\
        --out local_workspace/validation/compare/three_engine.json

Inputs: Engine A from scripts/run_engine_a_reference.py, Engine B from scripts/run_engine_b_nautilus.py,
Engine C from scripts/run_engine_c_backtrader.py.  Any two of the three are enough.  The tool only
reads those files and writes its own output; it runs no engine and changes no report.

Differences are classified, not failed:

* DATA_CONFIG_MISMATCH       different symbols, bar counts, date ranges or data hashes; every other
                             class is then unreliable for that pair
* DECISION_DIVERGENCE        a trade decision (symbol, side, decision bar) or exit reason or size
                             present in one engine only
* TIMING_DIVERGENCE          the same decision filled or exited at a different bar
* EXECUTION_MODEL_DIVERGENCE the same decision and bar at a different price (fill model, slippage)
* ACCOUNTING_DIVERGENCE      an engine's own books disagree (reconciliation breaks, P&L arithmetic)

Native execution semantics legitimately differ (see each report's execution model), so identical
P&L is not expected.  Exit code: 0, or 2 if the inputs are unreadable or fewer than two are given.
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

BAR = pd.Timedelta(minutes=1)
MONEY_TOL = 1e-4
CLASSES = ("DATA_CONFIG_MISMATCH", "DECISION_DIVERGENCE", "TIMING_DIVERGENCE",
           "EXECUTION_MODEL_DIVERGENCE", "ACCOUNTING_DIVERGENCE")

ENGINE_B_MODEL = {
    "engine": "ENGINE_B_NAUTILUS_NATIVE",
    "entry_reference_price": "decision bar close",
    "entry_fill": "Nautilus IOC market order filled at the decision bar's close (bar execution), default FillModel",
    "exit_fill": "Nautilus IOC market order at the close of the bar that triggered the exit",
    "end_of_data": "open positions reported open",
}


def _ts(value) -> Optional[pd.Timestamp]:
    if value in (None, "", "None"):
        return None
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("Asia/Kolkata") if stamp.tzinfo is None else stamp.tz_convert("Asia/Kolkata")


def _sign(side: str) -> int:
    return 1 if side == "BUY" else -1


def normalise(name: str, report: Dict[str, Any]) -> Dict[str, Any]:
    engine = report.get("engine", "")
    trades = []
    for t in report.get("trades", []):
        entry = _ts(t.get("entry_timestamp"))
        decision = _ts(t.get("decision_timestamp"))
        inferred = decision is None
        if inferred and entry is not None:
            decision = entry - BAR          # A and B record entry_timestamp = decision bar label + 1 minute
        trades.append({
            "key": (t["symbol"], t["side"], str(decision)), "symbol": t["symbol"], "side": t["side"],
            "decision": str(decision), "decision_inferred": inferred, "entry": str(entry), "exit": str(_ts(t.get("exit_timestamp"))),
            "quantity": int(t["quantity"]), "entry_price": float(t["entry_price"]), "exit_price": float(t["exit_price"]),
            "pnl": float(t["pnl"]), "costs": float(t["costs"]), "net": float(t["net_pnl"]), "reason": t.get("reason"),
            "candidate_id": t.get("candidate_id"),
        })
    trades.sort(key=lambda r: (r["decision"], r["symbol"]))
    identity = report.get("data_identity")
    if engine.startswith("ENGINE_B"):
        model = ENGINE_B_MODEL
        breaks = len(report.get("reconciliation", {}).get("breaks", [])) + int(
            bool(report.get("reconciliation", {}).get("ledger_gross_error")))
        native_dd = None
        slippage = sum(_sign(r["side"]) * (r["fill_price"] - r["reference_price"]) * r["filled_quantity"]
                       for r in report.get("option_a_fills", []))
        open_positions = sorted(report.get("nautilus_accounting", {}).get("open_positions", {}))
        native_net = report.get("nautilus_accounting", {}).get("net_pnl")
        source = report.get("source")
    elif engine.startswith("ENGINE_C"):
        model = report.get("execution_model", {})
        rec = report.get("reconciliation", {})
        breaks = len(rec.get("breaks", [])) + int(bool(rec.get("ledger_gross_error"))) + len(rec.get("chronology_errors", []))
        native_dd = report.get("backtrader_accounting", {}).get("max_drawdown_fraction")
        slippage = sum(_sign(r["side"]) * (r["fill_price"] - r["reference_price"]) * r["filled_quantity"]
                       for r in report.get("entry_fills", []))
        open_positions = sorted(report.get("backtrader_accounting", {}).get("open_positions", {}))
        native_net = report.get("backtrader_accounting", {}).get("net_pnl")
        source = report.get("source")
    else:
        model = report.get("execution_model", {})
        breaks = len(report.get("reconciliation", {}).get("breaks", []))
        native_dd = report.get("r5_ledger", {}).get("mtm_max_drawdown_fraction")
        slippage = sum(_sign(t["side"]) * (float(t["entry_price"]) - float(t["planned_entry_price"])) * int(t["quantity"])
                       for t in report.get("trades", []) if t.get("planned_entry_price") is not None)
        open_positions = []
        native_net = report.get("r5_ledger", {}).get("net_pnl")
        source = report.get("source")
    cumulative, peak, trade_dd = 0.0, 0.0, 0.0
    for row in sorted(trades, key=lambda r: r["exit"]):
        cumulative += row["net"]
        peak = max(peak, cumulative)
        trade_dd = max(trade_dd, peak - cumulative)
    arithmetic = [r["key"] for r in trades
                  if abs(_sign(r["side"]) * (r["exit_price"] - r["entry_price"]) * r["quantity"] - r["pnl"]) > MONEY_TOL
                  or abs(r["pnl"] - r["costs"] - r["net"]) > MONEY_TOL]
    ledger = report.get("r5_ledger", {})
    return {
        "name": name, "engine": engine, "execution_model": model, "source": source,
        "symbols": sorted(report.get("symbols", [])), "bars_received": report.get("bars_received"),
        "data_identity": identity, "trades": trades,
        "totals": {"trades": len(trades), "gross": sum(r["pnl"] for r in trades), "costs": sum(r["costs"] for r in trades),
                   "net": sum(r["net"] for r in trades), "ledger_net": ledger.get("net_pnl"), "native_net": native_net,
                   "entry_slippage_rupees": slippage, "trade_sequence_drawdown_rupees": trade_dd,
                   "native_max_drawdown_fraction": native_dd, "open_positions": open_positions,
                   "exit_reasons": ledger.get("exit_reasons", {})},
        "reconciliation_breaks": breaks, "arithmetic_errors": arithmetic,
    }


def _data_differences(a: Dict[str, Any], b: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    if a["symbols"] != b["symbols"]:
        out.append({"field": "symbols", a["name"]: a["symbols"], b["name"]: b["symbols"]})
    if a["bars_received"] != b["bars_received"]:
        out.append({"field": "bars_received", a["name"]: a["bars_received"], b["name"]: b["bars_received"]})
    ia, ib = a["data_identity"], b["data_identity"]
    if ia and ib:
        for symbol in sorted(set(ia) | set(ib)):
            x, y = ia.get(symbol, {}), ib.get(symbol, {})
            for field in ("bars", "first", "last", "sha256"):
                if x.get(field) != y.get(field):
                    out.append({"field": f"{symbol}.{field}", a["name"]: x.get(field), b["name"]: y.get(field)})
    kinds = [(s or {}).get("kind") if isinstance(s, dict) else ("SYNTHETIC_SMOKE" if "SYNTHETIC" in str(s) else
                                                                   "FROZEN_NSE_48_1MIN" if s else None)
             for s in (a["source"], b["source"])]
    if None not in kinds and kinds[0] != kinds[1]:
        out.append({"field": "source_kind", a["name"]: kinds[0], b["name"]: kinds[1]})
    return out


def compare_pair(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    data = _data_differences(a, b)
    identity_checked = bool(a["data_identity"] and b["data_identity"])
    for d in data:
        findings.append({"class": "DATA_CONFIG_MISMATCH", **d})
    for engine in (a, b):
        if engine["reconciliation_breaks"]:
            findings.append({"class": "ACCOUNTING_DIVERGENCE", "engine": engine["name"],
                             "detail": f"{engine['reconciliation_breaks']} reconciliation break(s) in the engine's own report"})
        if engine["arithmetic_errors"]:
            findings.append({"class": "ACCOUNTING_DIVERGENCE", "engine": engine["name"],
                             "detail": "trade P&L arithmetic inconsistent", "trades": engine["arithmetic_errors"]})
    ta = {r["key"]: r for r in a["trades"]}
    tb = {r["key"]: r for r in b["trades"]}
    first_divergence = None
    pairs = []
    for key in sorted(set(ta) | set(tb), key=lambda k: (k[2], k[0])):
        x, y = ta.get(key), tb.get(key)
        if x is None or y is None:
            only = a["name"] if y is None else b["name"]
            row = {"class": "DECISION_DIVERGENCE", "key": list(key), "detail": f"trade decision only in {only}"}
            findings.append(row)
            first_divergence = first_divergence or row
            continue
        diffs = []
        if x["entry"] != y["entry"]:
            diffs.append(("TIMING_DIVERGENCE", "entry fill bar", x["entry"], y["entry"]))
        elif abs(x["entry_price"] - y["entry_price"]) > 1e-9:
            diffs.append(("EXECUTION_MODEL_DIVERGENCE", "entry fill price", x["entry_price"], y["entry_price"]))
        if x["quantity"] != y["quantity"]:
            diffs.append(("DECISION_DIVERGENCE", "position size", x["quantity"], y["quantity"]))
        if x["reason"] != y["reason"]:
            diffs.append(("DECISION_DIVERGENCE", "exit reason", x["reason"], y["reason"]))
        elif x["exit"] != y["exit"]:
            diffs.append(("TIMING_DIVERGENCE", "exit fill bar", x["exit"], y["exit"]))
        elif abs(x["exit_price"] - y["exit_price"]) > 1e-9:
            diffs.append(("EXECUTION_MODEL_DIVERGENCE", "exit fill price", x["exit_price"], y["exit_price"]))
        pairs.append({"key": list(key), "net": {a["name"]: x["net"], b["name"]: y["net"]},
                      "differences": [{"class": c, "what": w, a["name"]: p, b["name"]: q} for c, w, p, q in diffs]})
        for c, w, p, q in diffs:
            row = {"class": c, "key": list(key), "what": w, a["name"]: p, b["name"]: q}
            findings.append(row)
            first_divergence = first_divergence or row
    counts = {c: sum(1 for f in findings if f["class"] == c) for c in CLASSES}
    totals = {k: {a["name"]: a["totals"][k], b["name"]: b["totals"][k]}
              for k in ("trades", "gross", "costs", "net", "entry_slippage_rupees", "trade_sequence_drawdown_rupees",
                        "native_max_drawdown_fraction", "open_positions")}
    return {
        "pair": [a["name"], b["name"]],
        "data_identity_verified": identity_checked and not data,
        "data_identity_note": None if identity_checked else
        "per-symbol bar hashes are not in both reports; compared symbols, bar counts and source only",
        "classification_counts": counts,
        "classifications_reliable": not data,
        "first_divergence": first_divergence,
        "matched_decisions": len(pairs),
        "matched_trades": pairs,
        "totals": totals,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine-a", type=Path)
    parser.add_argument("--engine-b", type=Path)
    parser.add_argument("--engine-c", type=Path)
    parser.add_argument("--out", type=Path, help="write the full comparison JSON here")
    args = parser.parse_args()
    paths = {n: p for n, p in (("A", args.engine_a), ("B", args.engine_b), ("C", args.engine_c)) if p}
    if len(paths) < 2:
        parser.error("give at least two engine reports")
    try:
        engines = {n: normalise(n, json.loads(Path(p).read_text())) for n, p in paths.items()}
    except (OSError, ValueError, KeyError) as exc:
        print(f"unreadable input: {exc}")
        return 2
    result = {
        "inputs": {n: str(p) for n, p in paths.items()},
        "engines": {n: {k: e[k] for k in ("engine", "execution_model", "source", "symbols", "bars_received",
                                           "totals", "reconciliation_breaks")} for n, e in engines.items()},
        "pairs": [compare_pair(engines[x], engines[y]) for x, y in combinations(sorted(engines), 2)],
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, default=str))
    print("R5 engine comparison (differences are classified, not failed)")
    for n, e in engines.items():
        t = e["totals"]
        print(f"  {n} {e['engine']}: trades {t['trades']}  gross {t['gross']:.2f}  costs {t['costs']:.2f}  "
              f"net {t['net']:.2f}  entry slippage {t['entry_slippage_rupees']:.2f}  "
              f"trade DD {t['trade_sequence_drawdown_rupees']:.2f}  open {t['open_positions']}  "
              f"recon breaks {e['reconciliation_breaks']}")
    for pair in result["pairs"]:
        x, y = pair["pair"]
        counts = ", ".join(f"{k}={v}" for k, v in pair["classification_counts"].items())
        print(f"  {x} vs {y}: data identity {'verified' if pair['data_identity_verified'] else 'NOT verified'}; "
              f"matched decisions {pair['matched_decisions']}; {counts}")
        if pair["first_divergence"]:
            print(f"    first divergence: {pair['first_divergence']}")
    if args.out:
        print(f"  full comparison: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
