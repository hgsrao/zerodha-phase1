"""Frozen, outcome-backed target/horizon proposals for paper-replay research.

The provider is intentionally conservative. It learns only from completed,
pre-cutoff trades and emits a proposal; it does not alter an MPC plan.  A
proposal is unavailable until enough cost-positive, path-observed samples
exist.  This prevents a short losing streak (or a single lucky winner) from
becoming a pretend symbol-specific target parameter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

import numpy as np


def _r(entry: float, stop: float, target: float) -> float:
    risk = abs(float(entry) - float(stop))
    return abs(float(target) - float(entry)) / risk if risk > 0.0 else 0.0


@dataclass(frozen=True)
class FrozenTargetSetpointProvider:
    """A frozen pre-session proposal built from completed historical paths."""

    minimum_samples: int
    usable_samples: int
    target_r_quantile: float | None
    useful_horizon_quantile: float | None
    seed_start: str
    seed_end_exclusive: str

    @classmethod
    def fit(cls, trades: Iterable[Dict[str, Any]], *, seed_start: str,
            seed_end_exclusive: str, minimum_samples: int = 20) -> "FrozenTargetSetpointProvider":
        usable: List[Dict[str, float]] = []
        for trade in trades:
            mfe_r, bars = trade.get("mfe_r"), trade.get("bars_held")
            if (float(trade.get("net_pnl", 0.0)) > 0.0 and mfe_r is not None and bars is not None
                    and math.isfinite(float(mfe_r)) and float(mfe_r) > 0.0 and int(bars) > 0):
                usable.append({"mfe_r": float(mfe_r), "bars_held": float(bars)})
        if len(usable) < minimum_samples:
            return cls(minimum_samples, len(usable), None, None, seed_start, seed_end_exclusive)
        # The median (not the maximum) favourable excursion is deliberately
        # conservative. It is a shadow candidate only; barrier-first and
        # cost-aware out-of-sample validation are required before promotion.
        return cls(
            minimum_samples, len(usable),
            float(np.quantile([row["mfe_r"] for row in usable], 0.50)),
            float(np.quantile([row["bars_held"] for row in usable], 0.50)),
            seed_start, seed_end_exclusive,
        )

    def propose(self, *, entry_price: float, stop_price: float, target_price: float,
                maximum_hold_bars: int) -> Dict[str, Any]:
        baseline_r = _r(entry_price, stop_price, target_price)
        if self.target_r_quantile is None or self.useful_horizon_quantile is None:
            return {
                "available": False, "reason": "INSUFFICIENT_COST_POSITIVE_PATH_HISTORY",
                "seed_start": self.seed_start, "seed_end_exclusive": self.seed_end_exclusive,
                "usable_samples": self.usable_samples, "minimum_samples": self.minimum_samples,
                "baseline_target_r": baseline_r, "baseline_maximum_hold_bars": int(maximum_hold_bars),
                "proposed_target_r": baseline_r, "proposed_maximum_hold_bars": int(maximum_hold_bars),
                "proposal_status": "BOOTSTRAP_MPC_RETAINED",
            }
        # A proposal can only shorten an unproven target/horizon; it cannot
        # encourage target chasing or a longer capital lock-up.
        proposed_target_r = min(baseline_r, max(0.0, float(self.target_r_quantile)))
        proposed_hold = min(int(maximum_hold_bars), max(1, int(round(self.useful_horizon_quantile))))
        return {
            "available": True, "reason": "FROZEN_PRE_CUTOFF_MEDIAN_FAVOURABLE_PATH",
            "seed_start": self.seed_start, "seed_end_exclusive": self.seed_end_exclusive,
            "usable_samples": self.usable_samples, "minimum_samples": self.minimum_samples,
            "baseline_target_r": baseline_r, "baseline_maximum_hold_bars": int(maximum_hold_bars),
            "proposed_target_r": proposed_target_r, "proposed_maximum_hold_bars": proposed_hold,
            "proposal_status": "UNVALIDATED_SHADOW_ONLY",
        }
