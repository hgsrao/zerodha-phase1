#!/usr/bin/env python3
"""Describe conservative MFE/MAE archetypes from sealed raw monthly reports.

This is observational only.  It does not propose or apply an entry, macro, or
exit rule; terminal-bar excursions are explicitly excluded by the producer.
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="raw report paths or glob patterns")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = sorted({Path(p) for pattern in args.reports for p in glob.glob(pattern)})
    if not paths:
        raise SystemExit("no raw reports matched")
    losers, winners, missing = [], [], []
    for path in paths:
        report = json.loads(path.read_text())
        for event in report.get("controller_telemetry", []):
            if event.get("event_type") != "CONTROLLER_OUTCOME":
                continue
            if event.get("terminal_bar_excursion") != "intrabar_order_unknown" or event.get("mfe_r") is None or event.get("mae_r") is None:
                missing.append(f"{path}:{event.get('trade_id')}")
                continue
            row = {"mfe_r": float(event["mfe_r"]), "mae_r": float(event["mae_r"]),
                   "net_pnl": float(event["net_pnl"]), "trade_id": event.get("trade_id")}
            (winners if row["net_pnl"] > 0 else losers).append(row)
    if missing:
        raise SystemExit(f"refusing incomplete excursion telemetry ({len(missing)} rows; e.g. {missing[0]})")
    if not losers and not winners:
        raise SystemExit("no completed trades")
    buckets = {
        "immediate_rejection_mfe_below_0_25r": [x for x in losers if x["mfe_r"] < .25],
        "stalled_bleed_mfe_0_25r_to_1r": [x for x in losers if .25 <= x["mfe_r"] < 1.0],
        "near_target_reversal_mfe_at_least_1r": [x for x in losers if x["mfe_r"] >= 1.0],
    }
    result = {
        "kind": "PATH_AWARE_EXCURSION_ARCHETYPES", "observational_only": True,
        "promotion_authorized": False, "completed_trades": len(losers) + len(winners),
        "losers": len(losers), "winners": len(winners),
        "archetypes": {name: {"trades": len(rows), "fraction_of_losers": len(rows) / len(losers) if losers else 0.0,
                              "mean_net_pnl": sum(x["net_pnl"] for x in rows) / len(rows) if rows else None}
                       for name, rows in buckets.items()},
        "winner_mean_mae_r": sum(x["mae_r"] for x in winners) / len(winners) if winners else None,
        "winner_deep_heat_below_minus_0_75r": sum(x["mae_r"] < -.75 for x in winners),
        "terminal_bar_policy": "intrabar_order_unknown excluded from MFE/MAE",
    }
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
