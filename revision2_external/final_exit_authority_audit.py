"""Observational equivalence audit for the final exit-decision surface.

The final controller must not become an exit authority merely because it
emits a decision event.  This module compares those events with realised
paper exits and labels what the controller represented, what a hard price
barrier pre-empted, and what it did not represent at all.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


HARD_PRICE_REASONS = {"stop", "stop_gap", "target", "target_gap"}


def _events_and_trades(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Accept either a full replay report or a one-trade trace artifact."""
    if artifact.get("controller_telemetry") is not None:
        return list(artifact["controller_telemetry"]), list(artifact.get("trades", []))
    trace = artifact.get("first_completed_trade_trace")
    if isinstance(trace, dict):
        trade = trace.get("paper_execution_and_outcome")
        return list(trace.get("controller_events_for_trade", [])), [trade] if isinstance(trade, dict) else []
    raise ValueError("artifact must contain controller_telemetry/trades or first_completed_trade_trace")


def _audit_trade(trade: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    trade_id = trade.get("trade_id")
    exit_reason = str(trade.get("reason", "unknown"))
    decisions = sorted(
        (event for event in events if event.get("event_type") == "FINAL_EXECUTION_EXIT_DECISION"),
        key=lambda event: str(event.get("timestamp", "")),
    )
    actions = [str(event.get("action", "")) for event in decisions]
    reasons = [str(event.get("reason", "")) for event in decisions]

    # Intrabar price barriers are intentionally authoritative.  A completed
    # bar's high/low ordering is unknown, so a later controller decision
    # cannot legitimately claim it should have prevented them.
    if exit_reason in HARD_PRICE_REASONS:
        verdict = "HARD_PRICE_BARRIER_PREEMPTED"
        equivalent: bool | None = None
    elif exit_reason == "controller_path_exit":
        equivalent = "EXIT_NEXT_BAR" in actions
        verdict = "MATCHED_CONTROLLER_PATH" if equivalent else "MISSING_PATH_EXIT_DECISION"
    elif exit_reason == "max_hold":
        equivalent = any(reason == "MAXIMUM_HOLD_REACHED" for reason in reasons)
        verdict = "MATCHED_MAX_HOLD" if equivalent else "MISSING_MAX_HOLD_DECISION"
    elif exit_reason == "saturation_exit_studies":
        equivalent = any(reason == "STUDIES_PID_SUSTAINED_LOW_CONFIDENCE" for reason in reasons)
        verdict = "MATCHED_STUDIES_SATURATION" if equivalent else "MISSING_STUDIES_SATURATION_DECISION"
    elif exit_reason == "saturation_exit_pa":
        equivalent = False
        verdict = "UNREPRESENTED_PA_SATURATION_EXIT"
    elif exit_reason == "regime_stressed_exit":
        equivalent = False
        verdict = "UNREPRESENTED_REGIME_EXIT"
    else:
        equivalent = None
        verdict = "OUT_OF_SCOPE_EXIT_REASON"
    return {
        "trade_id": trade_id,
        "candidate_id": trade.get("candidate_id"),
        "entry_timestamp": trade.get("entry_timestamp"),
        "exit_timestamp": trade.get("exit_timestamp"),
        "actual_exit_reason": exit_reason,
        "final_decision_count": len(decisions),
        "final_actions": actions,
        "final_reasons": reasons,
        "equivalent": equivalent,
        "verdict": verdict,
    }


def build_final_exit_authority_audit(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return a causal audit; never infer a favourable intrabar sequence."""
    telemetry, trades = _events_and_trades(artifact)
    events_by_trade: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in telemetry:
        if event.get("trade_id") is not None:
            events_by_trade[str(event["trade_id"])].append(event)
    rows = [_audit_trade(trade, events_by_trade[str(trade.get("trade_id"))]) for trade in trades]
    counts = Counter(row["verdict"] for row in rows)
    blockers = sum(
        count for verdict, count in counts.items()
        if verdict.startswith("MISSING_") or verdict.startswith("UNREPRESENTED_")
    )
    represented = sum(row["equivalent"] is True for row in rows)
    comparable = sum(row["equivalent"] is not None for row in rows)
    return {
        "research_boundary": "Telemetry-only authority-equivalence audit. It does not alter exits or establish trading performance.",
        "trades_audited": len(rows),
        "final_exit_decisions_observed": sum(row["final_decision_count"] for row in rows),
        "verdict_counts": dict(sorted(counts.items())),
        "comparable_exits": comparable,
        "represented_exits": represented,
        "unresolved_authority_blockers": blockers,
        "authority_promotion_allowed": blockers == 0 and comparable > 0,
        "trades": rows,
    }
