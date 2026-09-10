#!/usr/bin/env python3
"""Causally label completed external-engine trades for entry exhaustion.

This is an observational screen, not a filtered backtest.  Removing entries
changes later portfolio availability, so a threshold is eligible for a real
replay only after it separates bad entries from winners here.  Features use
only the decision bar and earlier bars; no terminal-bar or future OHLC is
consulted.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd
import talib

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Kolkata")
    return timestamp.tz_convert("UTC")


def entry_features(
    bars: pd.DataFrame, decision_timestamp: object, side: str, *,
    roc_period: int = 20, bb_period: int = 20, volume_lookback: int = 300,
) -> Dict[str, float]:
    """Return direction-aware entry features without looking past `now`."""
    timestamps = pd.to_datetime(bars["timestamp"], utc=True, errors="raise")
    target = _as_utc(decision_timestamp)
    # Compare normalized UTC nanoseconds rather than pandas' timezone-aware
    # extension array with a timezone-naive numpy datetime64.
    matches = np.flatnonzero(timestamps.array.asi8 == target.value)
    if len(matches) != 1:
        raise ValueError(f"decision timestamp must match exactly one bar: {decision_timestamp}")
    index = int(matches[0])
    needed = max(roc_period, bb_period, 14) + 1
    if index < needed:
        raise ValueError(f"insufficient causal history at {decision_timestamp}")

    # All vectors end at the decision bar.  This is the central causality
    # invariant: no high, low, close, or volume after the decision is read.
    history = bars.iloc[:index + 1]
    close = history["close"].to_numpy(dtype=float)
    high = history["high"].to_numpy(dtype=float)
    low = history["low"].to_numpy(dtype=float)
    volume = history["volume"].to_numpy(dtype=float)
    current_close, current_high, current_low = close[-1], high[-1], low[-1]

    roc = (current_close / close[-1 - roc_period] - 1.0) * 100.0
    upper, middle, lower = talib.BBANDS(close, timeperiod=bb_period, nbdevup=2.0, nbdevdn=2.0)
    std = (upper[-1] - middle[-1]) / 2.0
    bb_z = (current_close - middle[-1]) / std if std > 0.0 else 0.0
    prior_volume = volume[max(0, len(volume) - 1 - volume_lookback):-1]
    volume_ratio = current_volume / prior_volume.mean() if (current_volume := volume[-1]) and len(prior_volume) and prior_volume.mean() > 0.0 else 0.0
    atr = float(talib.ATR(high, low, close, timeperiod=14)[-1])
    bar_range = current_high - current_low
    close_location = (current_close - current_low) / bar_range if bar_range > 0.0 else 0.5

    side_sign = 1.0 if side == "BUY" else -1.0
    return {
        "roc_percent": float(roc),
        "bb_z": float(bb_z),
        "volume_ratio": float(volume_ratio),
        "range_atr": float(bar_range / atr) if atr > 0.0 else 0.0,
        "close_location": float(close_location),
        "directional_roc_percent": float(side_sign * roc),
        "directional_bb_z": float(side_sign * bb_z),
        "directional_close_location": float(close_location if side == "BUY" else 1.0 - close_location),
    }


def _cohort(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    winners = [row for row in rows if row["net_pnl"] > 0.0]
    immediate = [row for row in rows if row["immediate_rejection"]]
    return {
        "trades": len(rows),
        "net_pnl": sum(float(row["net_pnl"]) for row in rows),
        "mean_net_pnl": sum(float(row["net_pnl"]) for row in rows) / len(rows) if rows else None,
        "winners": len(winners),
        "immediate_rejections": len(immediate),
        "immediate_rejection_rate": len(immediate) / len(rows) if rows else None,
    }


def build_attribution(reports: Iterable[Dict[str, Any]], bars: pd.DataFrame) -> Dict[str, Any]:
    """Join accepted trades to causal decision-bar exhaustion features."""
    rows: List[Dict[str, Any]] = []
    for report in reports:
        entry_events = {
            event["candidate_id"]: event for event in report.get("controller_telemetry", [])
            if event.get("event_type") == "ENTRY_CONFIDENCE_THROTTLE" and event.get("candidate_id")
        }
        for trade in report.get("trades", []):
            candidate_id = trade.get("candidate_id")
            event = entry_events.get(candidate_id)
            if event is None:
                raise ValueError(f"missing causal entry event for {candidate_id}")
            if trade.get("mfe_r") is None or trade.get("terminal_bar_excursion") != "intrabar_order_unknown":
                raise ValueError(f"incomplete path-aware excursion telemetry for {trade.get('trade_id')}")
            row = {
                "trade_id": trade.get("trade_id"), "candidate_id": candidate_id,
                "decision_timestamp": str(event["timestamp"]), "side": trade["side"],
                "net_pnl": float(trade["net_pnl"]), "mfe_r": float(trade["mfe_r"]),
                "immediate_rejection": float(trade["net_pnl"]) <= 0.0 and float(trade["mfe_r"]) < 0.25,
            }
            row.update(entry_features(bars, event["timestamp"], trade["side"]))
            rows.append(row)
    if not rows:
        raise ValueError("no completed trades")

    # Quantiles reveal the feature distribution without presenting a
    # hand-picked threshold as an already-validated production rule.
    features = ("directional_bb_z", "volume_ratio", "directional_roc_percent", "range_atr")
    thresholds = {
        feature: [float(np.quantile([row[feature] for row in rows], q)) for q in (.75, .90, .95)]
        for feature in features
    }
    screens: Dict[str, Any] = {}
    for feature, values in thresholds.items():
        for quantile, threshold in zip(("p75", "p90", "p95"), values):
            vetoed = [row for row in rows if row[feature] >= threshold]
            screens[f"{feature}_{quantile}"] = {
                "feature": feature, "threshold": threshold, "would_veto": _cohort(vetoed),
                "would_keep": _cohort(row for row in rows if row[feature] < threshold),
            }
    return {
        "kind": "EXTERNAL_ENTRY_EXHAUSTION_SHADOW_ATTRIBUTION",
        "status": "ATTRIBUTION_COMPLETE",
        "shadow_only": True,
        "promotion_authorized": False,
        "warning": "Cohort filtering is not a portfolio counterfactual. Any proposed veto requires a fresh sealed replay.",
        "terminal_bar_policy": "intrabar_order_unknown excluded from MFE/MAE",
        "all_trades": _cohort(rows), "exploratory_screens": screens, "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="raw report paths or glob patterns")
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = sorted({Path(path) for pattern in args.reports for path in glob.glob(pattern)})
    if not paths:
        raise SystemExit("no raw reports matched")
    manifest = DatasetManifest.load(args.manifest)
    record = next((item for item in manifest.files if item.symbol == args.symbol), None)
    if record is None:
        raise SystemExit(f"{args.symbol} absent from manifest")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    data_path = loader._resolve_csv(args.symbol)
    if data_path is None or _sha256(data_path) != record.sha256:
        raise SystemExit("stock dataset seal failed")
    result = build_attribution([json.loads(path.read_text()) for path in paths], loader._load_symbol_csv(args.symbol))
    result["identity"] = {"manifest_hash": manifest.manifest_hash, "stock_file_sha256": record.sha256}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("kind", "status", "all_trades", "exploratory_screens")}, indent=2))
    print(f"[ENTRY EXHAUSTION] {output}")


if __name__ == "__main__":
    main()
