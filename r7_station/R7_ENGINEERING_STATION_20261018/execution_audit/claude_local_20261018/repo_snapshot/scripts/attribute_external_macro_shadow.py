#!/usr/bin/env python3
"""Attribute completed external-engine trades to sealed causal macro labels.

This is an exploratory, shadow-only report.  It neither proposes nor applies a
regime veto.  It refuses incomplete telemetry rather than silently treating
missing macro labels as neutral market conditions.
"""

from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd


REQUIRED_MACRO_FIELDS = (
    "nifty_ema_50",
    "macro_nifty_trend",
    "macro_vix_level",
    "macro_vix_slope",
)
IDENTITY_KEYS = (
    "stock_manifest_hash",
    "context_manifest_hash",
    "config_hash",
    "safety_contract_hash",
)


def _time_key(value: object) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value!r}")
    return timestamp.tz_convert("UTC").isoformat()


def _group(rows: Iterable[Dict[str, Any]], field: str) -> Dict[str, Dict[str, float]]:
    buckets: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        buckets[str(row[field])].append(float(row["net_pnl"]))
    return {
        label: {
            "trades": len(values),
            "net_pnl": sum(values),
            "mean_net_pnl": sum(values) / len(values),
            "win_rate": sum(value > 0.0 for value in values) / len(values),
        }
        for label, values in sorted(buckets.items())
    }


def _phase_label(value: object) -> str:
    return "synchronized" if value is True else "unsynchronized"


def _trend_label(value: object) -> str:
    numeric = float(value)
    return "nifty_up" if numeric > 0 else "nifty_down" if numeric < 0 else "nifty_flat"


def _vix_slope_label(value: object) -> str:
    return "vix_rising" if float(value) > 0.0 else "vix_falling_or_flat"


def extract_trade_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Join completed trades to their candidate-time macro observation.

    The orchestrator writes candidate IDs on entry-throttle events and on
    completed trades.  Grid observations are keyed by the same symbol and
    decision timestamp.  The join therefore uses no future bar or exit data.
    """
    observations = report.get("grid_shadow", {}).get("observations")
    telemetry = report.get("controller_telemetry")
    trades = report.get("trades")
    if not isinstance(observations, list) or not isinstance(telemetry, list) or not isinstance(trades, list):
        raise ValueError("report must include grid_shadow.observations, controller_telemetry, and trades")

    macro_by_decision: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for observation in observations:
        key = (str(observation["symbol"]), _time_key(observation["decision_timestamp"]))
        if key in macro_by_decision:
            raise ValueError(f"duplicate Grid observation for {key}")
        macro_by_decision[key] = observation

    candidate_decisions: Dict[str, Tuple[str, str]] = {}
    for event in telemetry:
        if event.get("event_type") != "ENTRY_CONFIDENCE_THROTTLE":
            continue
        candidate_id = event.get("candidate_id")
        if not candidate_id:
            raise ValueError("entry throttle event lacks candidate_id")
        candidate_decisions[str(candidate_id)] = (str(event["symbol"]), _time_key(event["timestamp"]))

    rows = []
    missing = []
    for trade in trades:
        candidate_id = trade.get("candidate_id")
        if not candidate_id or str(candidate_id) not in candidate_decisions:
            missing.append(f"trade {trade.get('trade_id')} lacks candidate-time telemetry")
            continue
        decision_key = candidate_decisions[str(candidate_id)]
        observation = macro_by_decision.get(decision_key)
        if observation is None:
            missing.append(f"candidate {candidate_id} lacks Grid observation")
            continue
        if not observation.get("available"):
            missing.append(f"candidate {candidate_id} Grid context unavailable: {observation.get('reason')}")
            continue
        absent = [field for field in REQUIRED_MACRO_FIELDS if observation.get(field) is None]
        if absent:
            missing.append(f"candidate {candidate_id} missing macro labels: {', '.join(absent)}")
            continue
        rows.append({
            "trade_id": trade.get("trade_id"),
            "candidate_id": str(candidate_id),
            "symbol": trade["symbol"],
            "decision_timestamp": decision_key[1],
            "net_pnl": float(trade["net_pnl"]),
            "exit_reason": trade.get("reason"),
            "macro_nifty_trend": _trend_label(observation["macro_nifty_trend"]),
            "macro_vix_level": float(observation["macro_vix_level"]),
            "macro_vix_slope": _vix_slope_label(observation["macro_vix_slope"]),
            "phase_state": _phase_label(observation.get("synchronized")),
            "phase_delta_degrees": observation.get("phase_delta_degrees"),
        })
    if missing:
        preview = "; ".join(missing[:5])
        raise ValueError(f"macro attribution rejected: {len(missing)} incomplete trade joins; {preview}")
    if not rows:
        raise ValueError("macro attribution rejected: no completed trades")
    return rows


def build_attribution(reports: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    reports = list(reports)
    if not reports:
        raise ValueError("at least one report is required")
    identity = reports[0]["identity"]
    for report in reports[1:]:
        if any(report["identity"].get(key) != identity.get(key) for key in IDENTITY_KEYS):
            raise ValueError("cannot attribute reports with mismatched sealed identities")
    rows = [row for report in reports for row in extract_trade_rows(report)]
    return {
        "kind": "EXTERNAL_MACRO_REGIME_SHADOW_ATTRIBUTION",
        "status": "ATTRIBUTION_COMPLETE",
        "shadow_only": True,
        "promotion_authorized": False,
        "identity": {key: identity[key] for key in IDENTITY_KEYS},
        "coverage": {"completed_trades_joined": len(rows), "all_completed_trades_joined": True},
        "aggregate": {
            "trades": len(rows),
            "net_pnl": sum(row["net_pnl"] for row in rows),
            "mean_net_pnl": sum(row["net_pnl"] for row in rows) / len(rows),
        },
        "by_nifty_trend": _group(rows, "macro_nifty_trend"),
        "by_vix_slope": _group(rows, "macro_vix_slope"),
        "by_phase_state": _group(rows, "phase_state"),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="sealed raw monthly report paths or glob patterns")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = sorted({Path(path) for pattern in args.reports for path in glob.glob(pattern)})
    if not paths:
        raise SystemExit("no report paths matched")
    attribution = build_attribution([json.loads(path.read_text()) for path in paths])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(attribution, indent=2) + "\n")
    print(json.dumps({"coverage": attribution["coverage"], "aggregate": attribution["aggregate"],
                      "by_nifty_trend": attribution["by_nifty_trend"],
                      "by_vix_slope": attribution["by_vix_slope"]}, indent=2))
    print(f"[ATTRIBUTION] {output}")


if __name__ == "__main__":
    main()
