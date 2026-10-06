"""Path-aware, non-executing payoff-geometry research.

This module does not modify the frozen ±1R evidence labels.  It replays a
selected candidate's later OHLC bars in chronological order for a separately
declared target/stop geometry.  A bar touching both barriers is explicitly
ambiguous because one-minute OHLC data cannot reveal which was touched first.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

import pandas as pd

from revision2_external.study_entry_shadow import StudyEntryShadowLedger


def evaluate_path(
    *, entry_price: float, initial_risk: float, side: str, future: pd.DataFrame,
    target_r: float, stop_r: float,
) -> Dict[str, Any]:
    """Evaluate one future path under a predeclared geometry.

    ``future`` must begin only after the next-bar entry fill. Terminal bars do
    not contribute to excursions because their intra-bar order is unknown.
    """
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if initial_risk <= 0.0 or target_r <= 0.0 or stop_r <= 0.0:
        raise ValueError("risk and geometry must be positive")
    sign = 1.0 if side == "BUY" else -1.0
    target = float(entry_price) + sign * float(target_r) * float(initial_risk)
    stop = float(entry_price) - sign * float(stop_r) * float(initial_risk)
    safe_mfe_r: float | None = None
    safe_mae_r: float | None = None
    time_to_mfe: int | None = None
    time_to_mae: int | None = None
    exit_side = "SELL" if side == "BUY" else "BUY"

    for bars_held, (_, bar) in enumerate(future.iterrows(), 1):
        high, low, opening, close = (float(bar[name]) for name in ("high", "low", "open", "close"))
        hit_target = high >= target if side == "BUY" else low <= target
        hit_stop = low <= stop if side == "BUY" else high >= stop
        if hit_target and hit_stop:
            return {
                "outcome": "INTRABAR_ORDER_UNKNOWN", "bars_held": bars_held,
                "entry_price": entry_price, "target_price": target, "stop_price": stop,
                "safe_mfe_r": safe_mfe_r, "safe_mae_r": safe_mae_r,
                "time_to_mfe": time_to_mfe, "time_to_mae": time_to_mae,
                "terminal_bar_policy": "excluded_from_mfe_mae_intrabar_order_unknown",
            }
        if hit_target or hit_stop:
            if hit_target:
                market_exit = max(opening, target) if side == "BUY" else min(opening, target)
                outcome = "TARGET_FIRST"
            else:
                market_exit = min(opening, stop) if side == "BUY" else max(opening, stop)
                outcome = "STOP_FIRST"
            exit_price = StudyEntryShadowLedger._fill_price(market_exit, exit_side)
            gross = sign * (exit_price - float(entry_price))
            costs = StudyEntryShadowLedger._leg_cost(entry_price, side) + StudyEntryShadowLedger._leg_cost(exit_price, exit_side)
            return {
                "outcome": outcome, "bars_held": bars_held, "entry_price": entry_price,
                "target_price": target, "stop_price": stop, "exit_price": exit_price,
                "gross_pnl_per_share": gross, "costs_per_share": costs,
                "net_pnl_per_share": gross - costs, "safe_mfe_r": safe_mfe_r,
                "safe_mae_r": safe_mae_r, "time_to_mfe": time_to_mfe,
                "time_to_mae": time_to_mae,
                "terminal_bar_policy": "excluded_from_mfe_mae_intrabar_order_unknown",
            }

        favorable = sign * (high - float(entry_price)) / float(initial_risk) if side == "BUY" else sign * (low - float(entry_price)) / float(initial_risk)
        adverse = sign * (low - float(entry_price)) / float(initial_risk) if side == "BUY" else sign * (high - float(entry_price)) / float(initial_risk)
        if safe_mfe_r is None or favorable > safe_mfe_r:
            safe_mfe_r, time_to_mfe = favorable, bars_held
        if safe_mae_r is None or adverse < safe_mae_r:
            safe_mae_r, time_to_mae = adverse, bars_held

    # A bounded 30-bar research horizon exits at the final close, then pays
    # the same adverse paper fill and round-trip costs as every other result.
    final_close = float(future.iloc[-1]["close"])
    exit_price = StudyEntryShadowLedger._fill_price(final_close, exit_side)
    gross = sign * (exit_price - float(entry_price))
    costs = StudyEntryShadowLedger._leg_cost(entry_price, side) + StudyEntryShadowLedger._leg_cost(exit_price, exit_side)
    return {
        "outcome": "TIMEOUT", "bars_held": len(future), "entry_price": entry_price,
        "target_price": target, "stop_price": stop, "exit_price": exit_price,
        "gross_pnl_per_share": gross, "costs_per_share": costs,
        "net_pnl_per_share": gross - costs, "safe_mfe_r": safe_mfe_r,
        "safe_mae_r": safe_mae_r, "time_to_mfe": time_to_mfe,
        "time_to_mae": time_to_mae,
        "terminal_bar_policy": "excluded_from_mfe_mae_intrabar_order_unknown",
    }


def summarize_geometry(outcomes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize path results without disguising ambiguous paths as losses."""
    rows = list(outcomes)
    ambiguous = [row for row in rows if row["outcome"] == "INTRABAR_ORDER_UNKNOWN"]
    usable = [row for row in rows if row["outcome"] != "INTRABAR_ORDER_UNKNOWN"]
    targets = [row for row in usable if row["outcome"] == "TARGET_FIRST"]
    stops = [row for row in usable if row["outcome"] == "STOP_FIRST"]
    timed_out = [row for row in usable if row["outcome"] == "TIMEOUT"]
    net = [float(row["net_pnl_per_share"]) for row in usable]
    return {
        "candidates": len(rows), "usable": len(usable), "ambiguous_excluded": len(ambiguous),
        "target_first": len(targets), "stop_first": len(stops), "timeout": len(timed_out),
        "target_first_rate_resolved_only": len(targets) / (len(targets) + len(stops)) if targets or stops else None,
        "total_net_pnl_per_share": sum(net),
        "mean_net_pnl_per_share": sum(net) / len(net) if net else None,
        "positive_after_costs": sum(value > 0.0 for value in net),
    }
