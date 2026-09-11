"""Causal entry-path diagnostics for shadow outcome rows."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable


def diagnose(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    all_rows = list(rows)
    valid = [r for r in all_rows if r.get("safe_mfe_r") is not None]
    buckets: Counter[str] = Counter()
    pnl: Dict[str, list[float]] = {"immediate_rejection": [], "stalled": [], "near_target_reversal": []}
    for row in valid:
        mfe = float(row["safe_mfe_r"])
        name = "immediate_rejection" if mfe < .25 else "stalled" if mfe < 1.0 else "near_target_reversal"
        buckets[name] += 1
        pnl[name].append(float(row["net_pnl_per_share"]))
    return {
        "resolved_rows": len(all_rows), "safe_excursion_rows": len(valid),
        "terminal_bar_policy": "excluded_from_mfe_mae_intrabar_order_unknown",
        "archetypes": {name: {"count": buckets[name], "fraction_of_safe_rows": buckets[name] / len(valid) if valid else None, "net_pnl_per_share": sum(values)} for name, values in pnl.items()},
    }
