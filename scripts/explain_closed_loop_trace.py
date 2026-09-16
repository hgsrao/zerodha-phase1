#!/usr/bin/env python3
"""Print one trade's causal entry-PID, exit-PID and closed-loop trace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EVENTS = {
    "ENTRY_CONFIDENCE_THROTTLE", "ENTRY_QUALITY_COMPARATOR", "PORTFOLIO_RISK_COMPARATOR",
    "DYNAMIC_SIZE_ACTUATION", "CLOSED_LOOP_ENTRY_SNAPSHOT", "EXIT_PROTECTION_UPDATE",
    "TRADE_PATH_COMPARATOR", "TRADE_PATH_STOP_ACTUATION", "CONTROLLER_OUTCOME",
    "OUTCOME_LEDGER_UPDATE",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("--trade-id", default=None)
    args = parser.parse_args()
    artifact = json.loads(Path(args.report).read_text())
    events = artifact.get("control_trace_events", [])
    trades = artifact.get("trades", [])
    if not events:
        raise RuntimeError("report lacks control_trace_events; rerun the intraday runner with current code")
    trade_id = args.trade_id or (trades[0].get("trade_id") if trades else None)
    if trade_id is None:
        raise RuntimeError("no completed trade available")
    trade = next(row for row in trades if row.get("trade_id") == trade_id)
    candidate_id = trade.get("candidate_id")
    print("=== TRADE ===")
    print(json.dumps({key: trade.get(key) for key in (
        "trade_id", "candidate_id", "side", "entry_timestamp", "exit_timestamp", "entry_price",
        "exit_price", "quantity", "reason", "pnl", "costs", "net_pnl",
    )}, indent=2))
    print("\n=== CAUSAL CONTROL TRACE ===")
    for row in events:
        if row.get("trade_id") not in {None, trade_id} and row.get("candidate_id") != candidate_id:
            continue
        if row.get("candidate_id") not in {None, candidate_id} and row.get("trade_id") != trade_id:
            continue
        if row["event_type"] not in EVENTS:
            continue
        print(f"\n--- {row['event_type']} @ {row['timestamp']} ---")
        print(json.dumps({key: value for key, value in row.items() if key not in {"event_type", "timestamp", "symbol"}}, indent=2))


if __name__ == "__main__":
    main()
