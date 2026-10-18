#!/usr/bin/env python3
"""Read-only analysis of a closed-loop intraday paper-replay artifact."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


def _cohort(rows: list[dict]) -> dict:
    pnl = [float(row["net_pnl"]) for row in rows]
    return {
        "trades": len(rows),
        "net_pnl": sum(pnl),
        "average_net_pnl": mean(pnl) if pnl else None,
        "wins": sum(value > 0 for value in pnl),
        "win_rate": sum(value > 0 for value in pnl) / len(pnl) if pnl else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", help="closed_loop_48symbol_YYYYMMDD.json artifact")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    source = Path(args.report)
    report = json.loads(source.read_text())
    events = report["control_trace_events"]
    trades = report["trades"]

    entry = {row["candidate_id"]: row for row in events if row["event_type"] == "ENTRY_QUALITY_COMPARATOR"}
    portfolio = {row["candidate_id"]: row for row in events if row["event_type"] == "PORTFOLIO_RISK_COMPARATOR"}
    dynamic = {row["candidate_id"]: row for row in events if row["event_type"] == "DYNAMIC_SIZE_ACTUATION"}
    snapshots = {row["trade_id"]: row for row in events if row["event_type"] == "CLOSED_LOOP_ENTRY_SNAPSHOT"}
    path = defaultdict(list)
    for row in events:
        if row["event_type"] == "TRADE_PATH_COMPARATOR":
            path[row["trade_id"]].append(row)
    stop_events = defaultdict(list)
    for row in events:
        if row["event_type"] == "TRADE_PATH_STOP_ACTUATION":
            stop_events[row["trade_id"]].append(row)

    enriched: list[dict] = []
    for trade in trades:
        candidate = entry.get(trade["candidate_id"], {})
        risk = portfolio.get(trade["candidate_id"], {})
        sizing = dynamic.get(trade["candidate_id"], {})
        path_rows = path[trade["trade_id"]]
        stops = stop_events[trade["trade_id"]]
        effective = [row for row in stops if float(row["stop_after"]) != float(row["stop_before"])]
        enriched.append({
            **trade,
            "id_confidence": candidate.get("id_confidence"),
            "entry_quality_derate": sizing.get("entry_quality_derate"),
            "portfolio_risk_derate": sizing.get("portfolio_risk_derate"),
            "entry_gross_exposure_fraction": risk.get("gross_exposure_fraction"),
            "response_time_bars": snapshots.get(trade["trade_id"], {}).get("reference_path", {}).get("response_time_bars"),
            "path_observations": len(path_rows),
            "path_behind_count": sum(bool(row.get("behind_path")) for row in path_rows),
            "stop_actuation_events": len(stops),
            "effective_stop_tightenings": len(effective),
        })

    confidences = sorted(row["id_confidence"] for row in enriched if row["id_confidence"] is not None)
    confidence_cohorts: dict[str, dict] = {}
    if confidences:
        q1, q2, q3 = (confidences[(len(confidences) - 1) * n // 4] for n in (1, 2, 3))
        for label, selected in {
            f"<= {q1:.4f}": [row for row in enriched if row["id_confidence"] <= q1],
            f"{q1:.4f} .. {q2:.4f}": [row for row in enriched if q1 < row["id_confidence"] <= q2],
            f"{q2:.4f} .. {q3:.4f}": [row for row in enriched if q2 < row["id_confidence"] <= q3],
            f"> {q3:.4f}": [row for row in enriched if row["id_confidence"] > q3],
        }.items():
            confidence_cohorts[label] = _cohort(selected)

    effective_stop_ids = {row["trade_id"] for row in enriched if row["effective_stop_tightenings"]}
    with_stops = [row for row in enriched if row["trade_id"] in effective_stop_ids]
    without_stops = [row for row in enriched if row["trade_id"] not in effective_stop_ids]
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in enriched:
        by_symbol[row["symbol"]].append(row)
    analysis = {
        "source_report": str(source),
        "run_status": report["status"],
        "headline": _cohort(enriched),
        "exit_reasons": dict(Counter(row["reason"] for row in enriched)),
        "entry_confidence_quartiles": confidence_cohorts,
        "trade_path": {
            "trades_with_path_observations": sum(bool(row["path_observations"]) for row in enriched),
            "trades_behind_path": sum(bool(row["path_behind_count"]) for row in enriched),
            "trades_with_effective_stop_tightening": len(with_stops),
            "effective_stop_tightening_cohort": _cohort(with_stops),
            "no_effective_stop_tightening_cohort": _cohort(without_stops),
        },
        "portfolio_risk": {
            "max_entry_exposure_fraction": max((float(row["entry_gross_exposure_fraction"] or 0.0) for row in enriched), default=0.0),
            "derated_trade_entries": sum(float(row["portfolio_risk_derate"] or 1.0) < 1.0 for row in enriched),
        },
        "entry_quality": {
            "derated_trade_entries": sum(float(row["entry_quality_derate"] or 1.0) < 1.0 for row in enriched),
            "confidence_min": min(confidences) if confidences else None,
            "confidence_max": max(confidences) if confidences else None,
            "confidence_mean": mean(confidences) if confidences else None,
        },
        "symbols": {
            symbol: _cohort(rows) | {"exit_reasons": dict(Counter(row["reason"] for row in rows))}
            for symbol, rows in sorted(by_symbol.items())
        },
        "trades": enriched,
        "interpretation": (
            "Descriptive, same-day paper-replay analysis only. Cohorts are not causal proof and must not be used "
            "to change controls without an out-of-sample protocol."
        ),
    }
    output = Path(args.output or source.with_name(source.stem + "_analysis.json"))
    output.write_text(json.dumps(analysis, indent=2, default=str))
    print(json.dumps({
        "output": str(output),
        "headline": analysis["headline"],
        "exit_reasons": analysis["exit_reasons"],
        "trade_path": analysis["trade_path"],
        "entry_quality": analysis["entry_quality"],
        "portfolio_risk": analysis["portfolio_risk"],
    }, indent=2))


if __name__ == "__main__":
    main()
