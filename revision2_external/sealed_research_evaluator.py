"""Deterministic promotion/rejection rules for fixed research hypotheses."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class WindowVerdict:
    window: str
    candidates: int
    gross_pnl_per_share: float
    net_pnl_per_share: float
    target_first_rate: float | None
    verdict: str
    reason: str


def evaluate_fixed_alpha(results: Dict[str, Dict[str, Any]], minimum_candidates: int = 30) -> Dict[str, Any]:
    """Evaluate train/validation/test without choosing parameters from results.

    This is intentionally demanding: a candidate is only eligible for further
    shadow work when all windows have enough observations and positive net
    outcomes.  Training performance alone never promotes a rule.
    """
    verdicts = []
    for window in ("train", "validation", "test"):
        row = results.get(window, {})
        n = int(row.get("candidates", 0))
        gross = float(row.get("gross_pnl_per_share", 0.0))
        net = float(row.get("net_pnl_per_share", 0.0))
        rate = row.get("target_first_rate")
        if n < minimum_candidates:
            verdict, reason = "INSUFFICIENT_EVIDENCE", f"{n} candidates < required {minimum_candidates}"
        elif net <= 0.0:
            verdict, reason = "REJECTED", "net outcome is not positive after costs"
        elif gross <= 0.0:
            verdict, reason = "REJECTED", "gross outcome is not positive"
        else:
            verdict, reason = "WINDOW_PASSED", "positive gross and net outcome with sufficient sample"
        verdicts.append(WindowVerdict(window, n, gross, net, rate, verdict, reason))
    promote = all(v.verdict == "WINDOW_PASSED" for v in verdicts)
    return {
        "minimum_candidates_per_window": minimum_candidates,
        "window_verdicts": [asdict(v) for v in verdicts],
        "overall_verdict": "ELIGIBLE_FOR_CLOSED_LOOP_SHADOW" if promote else "NOT_ELIGIBLE_FOR_CLOSED_LOOP_SHADOW",
        "promotion_note": "Eligibility authorizes only a separate fixed closed-loop shadow test; it does not authorize live trading or parameter tuning.",
    }
