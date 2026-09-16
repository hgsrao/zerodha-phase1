"""Frozen-threshold, out-of-sample feature separability diagnostics."""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, Iterable

FEATURES = ("volume_ratio", "breakout_extension_atr", "vwap_distance_atr", "ema_slope_atr")


def _cohort(rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "count": len(rows),
        "target_first_rate": sum(bool(r["target_before_stop"]) for r in rows) / len(rows) if rows else None,
        "net_pnl_per_share": sum(float(r["net_pnl_per_share"]) for r in rows),
    }


def diagnose(train_rows: Iterable[Dict[str, Any]], windows: Dict[str, Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
    """Freeze median splits on train and report both sides in every window."""
    train = list(train_rows)
    result: Dict[str, Any] = {
        "method": "Training median is frozen; low and high cohorts are both reported without selecting a trade rule.",
        "features": {},
    }
    for feature in FEATURES:
        train_usable = [r for r in train if feature in r.get("setup", {})]
        threshold = median(float(r["setup"][feature]) for r in train_usable) if train_usable else None
        per_window = {}
        for name, source in windows.items():
            rows = [r for r in source if threshold is not None and feature in r.get("setup", {})]
            low = [r for r in rows if float(r["setup"][feature]) < threshold]
            high = [r for r in rows if float(r["setup"][feature]) >= threshold]
            per_window[name] = {"low": _cohort(low), "high": _cohort(high)}
        result["features"][feature] = {"train_median_threshold": threshold, "windows": per_window}
    return result
