#!/usr/bin/env python3
"""Loss attribution for an R5 replay report (offline, read-only).

Reads replay reports from ``scripts/run_r5_paper_replay.py`` or Step 5 candidate outputs (the
``trades`` list of completed paper trades) and decomposes the result so a structural loss can
be located before any further calibration:

  * gross edge versus transaction costs (is the loss the signal or the friction?)
  * by exit reason, bay, side, entry hour and holding period
  * excursion: losers that had been >= 1R in favour (exit/hold problem) versus losers that
    never went in favour (entry problem)

Usage:
    python scripts/r5_loss_attribution.py REPORT.json [REPORT.json ...] [--markdown OUT.md]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from revision5.topology import SYMBOL_TO_BAY  # noqa: E402


def _bucket(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    net = [float(t["net_pnl"]) for t in rows]
    gross = sum(float(t["pnl"]) for t in rows)
    costs = sum(float(t["costs"]) for t in rows)
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    return {
        "trades": len(rows),
        "gross_pnl": round(gross, 2),
        "costs": round(costs, 2),
        "net_pnl": round(sum(net), 2),
        "win_rate": round(len(wins) / len(rows), 4) if rows else None,
        "profit_factor": (round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None),
        "avg_net": round(sum(net) / len(rows), 2) if rows else None,
        "cost_to_gross_abs": round(costs / abs(gross), 3) if gross else None,
    }


def _group(trades: Iterable[Dict[str, Any]], key) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(key(trade))].append(trade)
    return {name: _bucket(rows) for name, rows in sorted(groups.items(), key=lambda kv: sum(
        float(t["net_pnl"]) for t in kv[1]))}


def _hold_band(trade: Dict[str, Any]) -> str:
    bars = trade.get("bars_held")
    if bars is None:
        return "unknown"
    for limit in (1, 3, 5, 10, 20, 40):
        if bars <= limit:
            return f"<={limit}"
    return ">40"


def _excursion(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    losers = [t for t in trades if float(t["net_pnl"]) <= 0 and t.get("mfe_r") is not None
              and math.isfinite(float(t["mfe_r"]))]
    gave_back = [t for t in losers if float(t["mfe_r"]) >= 1.0]
    never = [t for t in losers if float(t["mfe_r"]) < 0.25]
    return {
        "losers_with_excursion_data": len(losers),
        "losers_that_reached_1R": len(gave_back),
        "losers_that_reached_1R_net": round(sum(float(t["net_pnl"]) for t in gave_back), 2),
        "losers_never_above_0_25R": len(never),
        "losers_never_above_0_25R_net": round(sum(float(t["net_pnl"]) for t in never), 2),
        "reading": ("exit/hold problem dominates" if len(gave_back) > len(never)
                    else "entry problem dominates" if never else "insufficient excursion data"),
    }


def attribute(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "total": _bucket(trades),
        "by_exit_reason": _group(trades, lambda t: str(t.get("reason", "unknown")).split(":")[0]),
        "by_exit_reason_detail": _group(trades, lambda t: t.get("reason", "unknown")),
        "by_bay": _group(trades, lambda t: SYMBOL_TO_BAY.get(t["symbol"], "UNMAPPED")),
        "by_side": _group(trades, lambda t: t["side"]),
        "by_entry_hour": _group(trades, lambda t: str(t.get("entry_timestamp", ""))[11:13] or "unknown"),
        "by_holding_period_bars": _group(trades, _hold_band),
        "by_symbol": _group(trades, lambda t: t["symbol"]),
        "excursion": _excursion(trades),
    }


def _table(title: str, groups: Dict[str, Dict[str, Any]]) -> List[str]:
    lines = [f"### {title}", "", "| group | trades | gross | costs | net | win rate | PF |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, row in groups.items():
        lines.append(f"| {name} | {row['trades']} | {row['gross_pnl']:,.0f} | {row['costs']:,.0f} | "
                     f"{row['net_pnl']:,.0f} | {row['win_rate']} | {row['profit_factor']} |")
    return lines + [""]


def to_markdown(result: Dict[str, Any], sources: List[str]) -> str:
    total = result["total"]
    lines = ["# R5 loss attribution", "", "Sources: " + ", ".join(f"`{s}`" for s in sources), "",
             f"Trades {total['trades']} | gross {total['gross_pnl']:,.2f} | costs {total['costs']:,.2f} | "
             f"net {total['net_pnl']:,.2f} | win rate {total['win_rate']} | PF {total['profit_factor']}", ""]
    for key, title in (("by_exit_reason", "Exit reason"), ("by_bay", "Bay"), ("by_side", "Side"),
                       ("by_entry_hour", "Entry hour"), ("by_holding_period_bars", "Holding period (bars)"),
                       ("by_exit_reason_detail", "Exit reason (detail)")):
        lines += _table(title, result[key])
    lines += ["### Excursion", ""] + [f"- {k}: {v}" for k, v in result["excursion"].items()] + [""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    trades: List[Dict[str, Any]] = []
    for path in args.reports:
        report = json.loads(path.read_text())
        if isinstance(report.get("trades"), list):                 # replay report
            trades.extend(report["trades"])
        elif isinstance(report.get("blocks"), list) and all(       # Step 5 candidate output
                isinstance(b.get("trades"), list) for b in report["blocks"]):
            for block in report["blocks"]:
                trades.extend(block["trades"])
        else:
            raise SystemExit(f"{path}: no 'trades' list (replay report) or per-block 'trades' "
                             "(Step 5 candidate output)")
    result = attribute(trades)
    if args.markdown:
        args.markdown.write_text(to_markdown(result, [str(p) for p in args.reports]))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
