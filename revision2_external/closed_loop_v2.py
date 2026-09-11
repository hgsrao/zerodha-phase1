"""V2 closed-loop research architecture.

This module deliberately separates three different control problems:

* entry quality: slow, statistical outcome feedback; it may only derate;
* trade path: fast, per-position protection; it may only tighten/exit;
* portfolio risk: fast, portfolio-wide exposure control; it may only derate/halt.

It is a research/tester contract, not a production order actuator.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass(frozen=True)
class CompletedOutcome:
    timestamp: str
    symbol: str
    side: str
    target_r: float
    round_trip_cost_r: float
    target_before_stop: bool
    net_r: float


@dataclass(frozen=True)
class EntryDecision:
    symbol: str
    side: str
    decision_timestamp: str
    target_r: float
    round_trip_cost_r: float
    required_probability: float
    posterior_probability: float
    posterior_lower_bound: float
    completed_samples: int
    entry_derate: float
    approved_for_shadow: bool
    reason: str


class EntryQualityLoop:
    """Slow causal feedback from *completed* trade outcomes only.

    A normal PID is not appropriate for binary, delayed trade outcomes.  The
    process variable is a conservative Bayesian estimate of target-before-stop
    probability. Its setpoint is the cost-aware break-even probability.  The
    actuator is one-way: it can only lower entry permission.
    """
    def __init__(self, minimum_samples: int = 30, prior_strength: int = 20) -> None:
        self.minimum_samples = int(minimum_samples)
        self.prior_strength = int(prior_strength)
        self._outcomes: List[CompletedOutcome] = []

    @staticmethod
    def required_probability(target_r: float, round_trip_cost_r: float) -> float:
        # Win receives +target_r less costs; loss receives -1R less costs.
        return _clip((1.0 + round_trip_cost_r) / (1.0 + target_r), 0.0, 1.0)

    def record(self, outcome: CompletedOutcome) -> None:
        self._outcomes.append(outcome)

    def decide(self, symbol: str, side: str, decision_timestamp: str, target_r: float, round_trip_cost_r: float) -> EntryDecision:
        # Timestamp seal: no outcome at/after this candidate can enter its
        # feedback state. The lexical ISO timestamp contract is explicit.
        rows = [o for o in self._outcomes if o.symbol == symbol and o.side == side and o.timestamp < decision_timestamp]
        pooled = [o for o in self._outcomes if o.side == side and o.timestamp < decision_timestamp]
        source = rows if len(rows) >= self.minimum_samples else pooled
        wins = sum(o.target_before_stop for o in source)
        n = len(source)
        # Beta prior centred at 0.5; avoids declaring a symbol "good" after a
        # handful of lucky trades. The lower confidence bound is conservative.
        alpha = wins + self.prior_strength / 2
        beta = (n - wins) + self.prior_strength / 2
        mean = alpha / (alpha + beta)
        variance = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
        lower = _clip(mean - 1.645 * math.sqrt(variance), 0.0, 1.0)
        required = self.required_probability(target_r, round_trip_cost_r)
        margin = lower - required
        # No outcome history is not proof of an edge.  This is intentionally
        # conservative and cannot ever scale a base position above 1.0.
        derate = _clip(0.25 + max(0.0, margin) / max(1.0 - required, 1e-9), 0.0, 1.0)
        approved = n >= self.minimum_samples and lower >= required
        reason = "QUALITY_CONFIRMED" if approved else ("INSUFFICIENT_COMPLETED_OUTCOMES" if n < self.minimum_samples else "COST_AWARE_EDGE_NOT_CONFIRMED")
        return EntryDecision(symbol, side, decision_timestamp, target_r, round_trip_cost_r, required, mean, lower, n, derate, approved, reason)


class ClosedLoopV2Tester:
    """Small deterministic tester for control contracts and causal sealing."""
    def __init__(self, entry_quality: EntryQualityLoop | None = None) -> None:
        self.entry_quality = entry_quality or EntryQualityLoop()
        self.events: List[Dict[str, object]] = []

    def candidate(self, **kwargs: object) -> EntryDecision:
        decision = self.entry_quality.decide(**kwargs)  # type: ignore[arg-type]
        self.events.append({"event": "ENTRY_QUALITY_DECISION", **asdict(decision)})
        return decision

    def outcome(self, outcome: CompletedOutcome) -> None:
        self.entry_quality.record(outcome)
        self.events.append({"event": "COMPLETED_OUTCOME", **asdict(outcome)})

    def validate(self) -> None:
        for row in self.events:
            if row["event"] != "ENTRY_QUALITY_DECISION":
                continue
            if not 0.0 <= float(row["entry_derate"]) <= 1.0:
                raise AssertionError("entry loop attempted to increase risk")
            if not 0.0 <= float(row["required_probability"]) <= 1.0:
                raise AssertionError("invalid cost-aware setpoint")
