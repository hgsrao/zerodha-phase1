"""Read-only cost and turnover attribution for sealed completed-trade ledgers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

import pandas as pd


def trade_rows(completed_trade_ledger: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize sealed trade records and derive only reproducible diagnostics."""
    rows: list[dict[str, Any]] = []
    for trade in completed_trade_ledger:
        row = dict(trade)
        entry = pd.Timestamp(row["entry_timestamp"])
        exit_ = pd.Timestamp(row["exit_timestamp"])
        if entry.tzinfo is None or exit_.tzinfo is None:
            raise ValueError(f"trade {row['trade_id']} has a timezone-naive timestamp")
        entry_ist = entry.tz_convert("Asia/Kolkata")
        exit_ist = exit_.tz_convert("Asia/Kolkata")
        row["total_cost"] = float(row["entry_cost"]) + float(row["exit_cost"])
        row["gross_pnl"] = float(row["gross_pnl"])
        row["net_pnl"] = float(row["net_pnl"])
        row["cost_to_gross_ratio"] = (
            None if row["gross_pnl"] == 0 else row["total_cost"] / abs(row["gross_pnl"])
        )
        row["entry_ist"] = entry_ist.isoformat()
        row["exit_ist"] = exit_ist.isoformat()
        row["entry_hour_ist"] = entry_ist.hour
        row["hold_minutes"] = (exit_ - entry).total_seconds() / 60.0
        rows.append(row)
    return rows


def _aggregate(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, float | int]] = defaultdict(lambda: {
        "trades": 0, "gross_pnl": 0.0, "total_cost": 0.0, "net_pnl": 0.0,
        "winning_trades": 0, "losing_trades": 0,
    })
    for row in rows:
        bucket = groups[str(row[key])]
        bucket["trades"] += 1
        bucket["gross_pnl"] += row["gross_pnl"]
        bucket["total_cost"] += row["total_cost"]
        bucket["net_pnl"] += row["net_pnl"]
        if row["net_pnl"] > 0:
            bucket["winning_trades"] += 1
        elif row["net_pnl"] < 0:
            bucket["losing_trades"] += 1
    result = [{key: bucket, **values} for bucket, values in groups.items()]
    return sorted(result, key=lambda item: (item["net_pnl"], item[key]))


def attribution_report(completed_trade_ledger: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return a deterministic breakdown without making any trading decision."""
    rows = trade_rows(completed_trade_ledger)
    gross = sum(row["gross_pnl"] for row in rows)
    costs = sum(row["total_cost"] for row in rows)
    net = sum(row["net_pnl"] for row in rows)
    if abs((gross - costs) - net) > 0.01:
        raise ValueError("completed-trade cost reconciliation failed")
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "trade_count": len(rows),
        "gross_pnl": gross,
        "total_cost": costs,
        "net_pnl": net,
        "cost_share_of_gross": None if gross == 0 else costs / abs(gross),
        "by_symbol": _aggregate(rows, "symbol"),
        "by_exit_reason": _aggregate(rows, "exit_reason"),
        "by_entry_hour_ist": _aggregate(rows, "entry_hour_ist"),
        "trades": rows,
    }
