#!/usr/bin/env python3
"""Audit collected entry-expectancy evidence without fitting an entry rule.

The purpose is data integrity: confirm each persisted row contains only
pre-entry inputs and a later completed outcome, and report basic coverage.
It intentionally makes no profitability, threshold, or admission decision.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REQUIRED = {
    "candidate_id", "symbol", "side", "timestamp", "pa_confidence", "id_confidence",
    "studies_confidence", "atr_fraction", "target_r", "exit_timestamp", "exit_reason",
    "bars_held", "gross_pnl", "costs", "net_pnl",
}
NUMERIC = {"pa_confidence", "id_confidence", "studies_confidence", "atr_fraction", "target_r",
           "gross_pnl", "costs", "net_pnl"}
CANDIDATE_REQUIRED = {"candidate_id", "symbol", "side", "timestamp", "execution_disposition", "execution_reason"}


def _validate(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED - set(row))
        if missing:
            errors.append(f"row {index}: missing {missing}")
            continue
        candidate_id = str(row["candidate_id"])
        if not candidate_id or candidate_id in seen:
            errors.append(f"row {index}: empty or duplicate candidate_id")
        seen.add(candidate_id)
        for field in NUMERIC:
            try:
                if not math.isfinite(float(row[field])):
                    errors.append(f"row {index}: non-finite {field}")
            except (TypeError, ValueError):
                errors.append(f"row {index}: invalid numeric {field}")
        if int(row["bars_held"]) < 0:
            errors.append(f"row {index}: negative bars_held")
        if str(row["exit_timestamp"]) <= str(row["timestamp"]):
            errors.append(f"row {index}: exit timestamp is not later than entry timestamp")
    return errors


def _cohort(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    return {
        "resolved": count,
        "net_pnl": sum(float(row["net_pnl"]) for row in rows),
        "mean_net_pnl": (sum(float(row["net_pnl"]) for row in rows) / count) if count else None,
        "positive_net_outcomes": sum(float(row["net_pnl"]) > 0.0 for row in rows),
    }


def _candidate_funnel(observations: dict[str, Any], resolved_rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate the upstream candidate funnel separately from trade outcomes."""
    if not observations:
        return None, []
    rows = list(observations.get("rows", []))
    errors: list[str] = []
    ids: set[str] = set()
    for index, row in enumerate(rows):
        missing = sorted(CANDIDATE_REQUIRED - set(row))
        if missing:
            errors.append(f"candidate row {index}: missing {missing}")
            continue
        candidate_id = str(row["candidate_id"])
        if not candidate_id or candidate_id in ids:
            errors.append(f"candidate row {index}: empty or duplicate candidate_id")
        ids.add(candidate_id)
        if row["execution_disposition"] in {"PENDING", "NOT_FILLED_UNCLASSIFIED"}:
            errors.append(f"candidate row {index}: terminal disposition missing")
    dispositions = Counter(str(row.get("execution_disposition")) for row in rows)
    filled_ids = {str(row["candidate_id"]) for row in rows if row.get("execution_disposition") == "FILLED"}
    resolved_ids = {str(row["candidate_id"]) for row in resolved_rows}
    if filled_ids != resolved_ids:
        errors.append("filled candidate ids do not exactly match resolved entry-evidence ids")
    return {
        "observed_candidates_reported": observations.get("observed_candidates"),
        "observed_rows_written": len(rows),
        "dispositions": dict(sorted(dispositions.items())),
        "filled_candidates": len(filled_ids),
        "resolved_fill_match": filled_ids == resolved_ids,
    }, errors


def build_audit(artifact: dict[str, Any]) -> dict[str, Any]:
    evidence = artifact.get("entry_expectancy_evidence", {})
    rows = list(evidence.get("resolved", []))
    errors = _validate(rows)
    candidate_funnel, candidate_errors = _candidate_funnel(artifact.get("entry_candidate_observations", {}), rows)
    errors.extend(candidate_errors)
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_side: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[str(row.get("symbol"))].append(row)
        by_side[str(row.get("side"))].append(row)
    return {
        "run_type": "entry_expectancy_evidence_integrity_audit",
        "source_period": [artifact.get("period_start"), artifact.get("period_end_exclusive")],
        "research_boundary": "Integrity and coverage only; no threshold, model, or trading decision is produced.",
        "source_summary": {
            "completed_trades": artifact.get("metrics", {}).get("completed_trades"),
            "resolved_candidates_reported": evidence.get("resolved_candidates"),
            "resolved_rows_written": len(rows),
            "pending_candidates": evidence.get("pending_candidates"),
        },
        "integrity": {"valid": not errors, "error_count": len(errors), "errors": errors[:100]},
        "overall": _cohort(rows),
        "by_side": {key: _cohort(value) for key, value in sorted(by_side.items())},
        "exit_reasons": dict(sorted(Counter(str(row.get("exit_reason")) for row in rows).items())),
        "symbols_with_resolved_entries": len(by_symbol),
        "by_symbol": {key: _cohort(value) for key, value in sorted(by_symbol.items())},
        "candidate_funnel": candidate_funnel,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="collection JSON artifact")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    artifact = json.loads(Path(args.input).read_text(encoding="utf-8"))
    audit = build_audit(artifact)
    if not audit["integrity"]["valid"]:
        raise RuntimeError(json.dumps(audit["integrity"], indent=2))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "source_summary": audit["source_summary"],
                      "overall": audit["overall"]}, indent=2))


if __name__ == "__main__":
    main()
