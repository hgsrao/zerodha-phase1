#!/usr/bin/env python3
"""Aggregate a sealed, shadow-only R-trajectory study over training months.

This runner never changes the engine configuration and never promotes the
shadow stop to live execution.  It runs the existing causally sealed monthly
runner independently for each month, then compares each completed live trade
with its shadow counterfactual using the same adverse paper-fill convention.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd

from scripts.run_external_grid_shadow_month import (
    DEFAULT_CONTEXT_MANIFEST,
    DEFAULT_STOCK_MANIFEST,
    run_shadow_month,
)


TRAIN_START = "2023-09-01"
TRAIN_END = "2024-08-31"
CHECKPOINT_MONTHS = (1, 3, 6, 9, 12)


def month_windows(start: str, months: int) -> List[tuple[str, str]]:
    """Return consecutive inclusive calendar-month windows without overlap."""
    if months < 1:
        raise ValueError("months must be positive")
    first = pd.Timestamp(start).replace(day=1)
    return [
        (
            (first + pd.DateOffset(months=index)).date().isoformat(),
            (first + pd.DateOffset(months=index) + pd.offsets.MonthEnd(0)).date().isoformat(),
        )
        for index in range(months)
    ]


def _shadow_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    trades = list(report.get("trades", []))
    shadow_trades = [trade for trade in trades if trade.get("shadow_r_trajectory")]
    nontrivial = [
        trade for trade in shadow_trades
        if abs(float(trade["shadow_r_trajectory"]["shadow_net_pnl"]) - float(trade["net_pnl"])) > 1e-9
    ]
    earlier = [
        trade for trade in shadow_trades
        if trade["shadow_r_trajectory"]["shadow_exit_timestamp"] < trade["exit_timestamp"]
    ]
    targets_choked = [
        trade for trade in shadow_trades if trade.get("reason") in {"target", "target_gap"}
    ]
    live_net = sum(float(trade["net_pnl"]) for trade in trades)
    shadow_net = sum(
        float(trade.get("shadow_r_trajectory", {}).get("shadow_net_pnl", trade["net_pnl"]))
        for trade in trades
    )
    telemetry = report.get("controller_telemetry_summary", {})
    return {
        "trades": len(trades),
        "live_net_pnl": live_net,
        "shadow_counterfactual_net_pnl": shadow_net,
        "shadow_delta_net_pnl": shadow_net - live_net,
        "shadow_exits": len(shadow_trades),
        "initial_stop_only_shadow_exits": sum(
            int(trade["shadow_r_trajectory"]["shadow_exit_bars_held"]) == 0 for trade in shadow_trades
        ),
        "nontrivial_shadow_outcomes": len(nontrivial),
        "earlier_shadow_exits": len(earlier),
        "targets_choked": len(targets_choked),
        "shadow_updates": int(telemetry.get("shadow_r_trajectory_updates", 0)),
    }


def aggregate_reports(reports: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate reports only when their fixed identities are identical."""
    reports = list(reports)
    if not reports:
        raise ValueError("at least one monthly report is required")
    identity_keys = ("stock_manifest_hash", "context_manifest_hash", "config_hash", "safety_contract_hash")
    identity = reports[0]["identity"]
    for report in reports[1:]:
        if any(report["identity"][key] != identity[key] for key in identity_keys):
            raise ValueError("cannot aggregate reports with mismatched sealed identities")

    monthly = []
    for report in reports:
        metrics = _shadow_metrics(report)
        monthly.append({"scope": report["scope"], "status": report["status"], **metrics})

    def total(key: str) -> float:
        return sum(float(row[key]) for row in monthly)

    checkpoints = {}
    for count in CHECKPOINT_MONTHS:
        if len(monthly) >= count:
            subset = monthly[:count]
            checkpoints[f"{count}_months"] = {
                "live_net_pnl": sum(float(row["live_net_pnl"]) for row in subset),
                "shadow_counterfactual_net_pnl": sum(float(row["shadow_counterfactual_net_pnl"]) for row in subset),
                "shadow_delta_net_pnl": sum(float(row["shadow_delta_net_pnl"]) for row in subset),
                "nontrivial_shadow_outcomes": sum(int(row["nontrivial_shadow_outcomes"]) for row in subset),
                "targets_choked": sum(int(row["targets_choked"]) for row in subset),
            }

    return {
        "kind": "EXTERNAL_SHADOW_R_TRAJECTORY_TRAINING_STUDY",
        "status": "SHADOW_COMPLETE",
        "shadow_only": True,
        "promotion_authorized": False,
        "scope": {"period": "training", "months": len(monthly)},
        "identity": {key: identity[key] for key in identity_keys},
        "monthly": monthly,
        "aggregate": {
            "trades": int(total("trades")),
            "live_net_pnl": total("live_net_pnl"),
            "shadow_counterfactual_net_pnl": total("shadow_counterfactual_net_pnl"),
            "shadow_delta_net_pnl": total("shadow_delta_net_pnl"),
            "shadow_exits": int(total("shadow_exits")),
            "initial_stop_only_shadow_exits": int(total("initial_stop_only_shadow_exits")),
            "nontrivial_shadow_outcomes": int(total("nontrivial_shadow_outcomes")),
            "earlier_shadow_exits": int(total("earlier_shadow_exits")),
            "targets_choked": int(total("targets_choked")),
            "shadow_updates": int(total("shadow_updates")),
        },
        "checkpoints": checkpoints,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--start", default=TRAIN_START)
    parser.add_argument("--months", type=int, choices=CHECKPOINT_MONTHS, default=12)
    parser.add_argument("--monthly-report-dir", type=Path,
                        help="directory for sealed raw monthly reports, required for later macro attribution")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    reports = []
    for index, (start, end) in enumerate(month_windows(args.start, args.months), start=1):
        print(f"[MONTH {index}/{args.months}] {args.symbol} {start} through {end}", flush=True)
        report = run_shadow_month(argparse.Namespace(
            symbol=args.symbol, start=start, end=end,
            stock_manifest=str(DEFAULT_STOCK_MANIFEST), context_manifest=str(DEFAULT_CONTEXT_MANIFEST),
            context_warmup_bars=1000, min_engine_warmup_bars=60, starting_equity=100_000.0,
        ))
        reports.append(report)
        if args.monthly_report_dir is not None:
            args.monthly_report_dir.mkdir(parents=True, exist_ok=True)
            raw_output = args.monthly_report_dir / f"{args.symbol}_{start.replace('-', '')}.json"
            raw_output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    study = aggregate_reports(reports)
    output = Path(args.output) if args.output else Path(
        f"diagnostic_output/external_shadow_r_trajectory_{args.symbol}_{args.start.replace('-', '')}_{args.months}m.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(study, indent=2, default=str) + "\n")
    print(json.dumps(study["aggregate"], indent=2))
    print(f"[SHADOW STUDY] {output}")


if __name__ == "__main__":
    main()
