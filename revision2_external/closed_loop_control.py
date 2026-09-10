"""Causal, bounded control loops for the external paper-replay engine.

The three loops deliberately have different time scales:

* trade-path: compares a frozen MPC reference path with live R-progress;
* entry-quality: derives a *future-trade* derate from completed outcomes;
* portfolio-risk: derives a one-way new-risk derate from live exposure.

None of these objects may alter a historical outcome after it is recorded.
The first integration is observation-only: its emitted decisions are audited
before any actuator is permitted to consume them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class TradeReferencePath:
    """An entry-time MPC path expressed in invariant R units.

    ``expected_r`` is a planning reference. ``lower_bound_r`` is the
    protection boundary used by the comparator: it starts at the original
    -1R stop and reaches the planned target only at the hard time horizon.
    It is not a price forecast and is not an order instruction.
    """

    symbol: str
    side: str
    entry_price: float
    initial_risk: float
    target_r: float
    max_hold_bars: int
    curve_gamma: float = 1.0

    def progress(self, bars_held: int) -> float:
        return _clip(float(bars_held) / max(1, self.max_hold_bars), 0.0, 1.0)

    def expected_r(self, bars_held: int) -> float:
        return self.target_r * self.progress(bars_held) ** self.curve_gamma

    def lower_bound_r(self, bars_held: int) -> float:
        # At entry the original hard stop is -1R; at expiry it coincides
        # with the target, while the hard maximum-hold exit remains the
        # ultimate time constraint.
        return -1.0 + (self.target_r + 1.0) * self.progress(bars_held) ** self.curve_gamma

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TradePathObservation:
    actual_r: float
    expected_r: float
    lower_bound_r: float
    error_r: float
    behind_path: bool
    suggested_protection_r: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CausalOutcomeLedger:
    """Completed-trade-only feedback store.

    A trade is appended only at exit, therefore entry-quality snapshots can
    never see an open trade's eventual result.  This is intentionally small
    and in-memory for paper replay; persistence is a separate audit concern.
    """

    def __init__(self) -> None:
        self._outcomes: List[Dict[str, Any]] = []

    def record(self, outcome: Dict[str, Any]) -> None:
        if "net_pnl" not in outcome or "symbol" not in outcome:
            raise ValueError("outcome ledger requires symbol and net_pnl")
        self._outcomes.append(dict(outcome))

    def profile(self, symbol: str, side: str, regime: str = "unknown", minimum_history: int = 20) -> Dict[str, Any]:
        all_rows = [row for row in self._outcomes if row.get("side") == side]
        local_rows = [
            row for row in all_rows
            if row.get("symbol") == symbol and row.get("regime", "unknown") == regime
        ]
        # Partial pooling: sparse symbol/regime evidence shrinks toward the
        # side-wide evidence instead of inventing 48 independent parameters.
        # A neutral prior prevents one completed trade from becoming a
        # pretend symbol characteristic.  Evidence earns influence only as
        # its sample count grows.
        global_wins = sum(float(row["net_pnl"]) > 0.0 for row in all_rows)
        global_win_rate = (global_wins + 0.5 * minimum_history) / (len(all_rows) + minimum_history)
        local_win_rate = (
            sum(float(row["net_pnl"]) > 0.0 for row in local_rows) / len(local_rows)
            if local_rows else global_win_rate
        )
        weight = len(local_rows) / (len(local_rows) + max(1, minimum_history))
        pooled_win_rate = weight * local_win_rate + (1.0 - weight) * global_win_rate
        # A suggested derate only. It never raises risk above the base size.
        return {
            "completed_outcomes_total": len(self._outcomes),
            "global_side_samples": len(all_rows),
            "symbol_regime_samples": len(local_rows),
            "partial_pool_weight": weight,
            "global_win_rate": global_win_rate,
            "symbol_regime_win_rate": local_win_rate,
            "pooled_win_rate": pooled_win_rate,
            "suggested_entry_derate": _clip(0.5 + pooled_win_rate, 0.5, 1.0),
            # This is a future-entry correction, not a new safety limit.
            # It becomes actionable only after enough completed evidence.
            "suggested_confidence_offset": _clip((0.5 - pooled_win_rate) * 0.10, 0.0, 0.05),
        }


class ClosedLoopSupervisor:
    """Owns the three causal loop calculations, not trade execution."""

    def __init__(self) -> None:
        self.outcomes = CausalOutcomeLedger()

    def entry_snapshot(
        self, symbol: str, side: str, entry_price: float, stop_price: float,
        target_price: float, max_hold_bars: int, regime: str = "unknown",
    ) -> Dict[str, Any]:
        risk = abs(float(entry_price) - float(stop_price))
        if risk <= 0.0:
            raise ValueError("closed-loop reference path requires non-zero initial risk")
        target_r = abs(float(target_price) - float(entry_price)) / risk
        path = TradeReferencePath(
            symbol=symbol, side=side, entry_price=float(entry_price), initial_risk=risk,
            target_r=target_r, max_hold_bars=max(1, int(max_hold_bars)),
        )
        return {
            "regime": regime,
            "reference_path": path.to_dict(),
            "entry_quality": self.outcomes.profile(symbol, side, regime),
        }

    @staticmethod
    def observe_trade_path(snapshot: Dict[str, Any], current_price: float, bars_held: int) -> TradePathObservation:
        path = TradeReferencePath(**snapshot["reference_path"])
        signed_move = float(current_price) - path.entry_price if path.side == "BUY" else path.entry_price - float(current_price)
        actual_r = signed_move / path.initial_risk
        lower_bound = path.lower_bound_r(bars_held)
        error = lower_bound - actual_r
        # This is only a suggested boundary.  The live controller cannot
        # consume it until a sealed shadow study proves its value.
        # A protective stop beyond the current R would be an immediate,
        # synthetic exit. The path loop may request an explicit exit later,
        # but a stop-protection request is never allowed to cross price.
        suggested_protection = min(actual_r, max(-1.0, lower_bound))
        return TradePathObservation(
            actual_r=actual_r,
            expected_r=path.expected_r(bars_held),
            lower_bound_r=lower_bound,
            error_r=error,
            behind_path=error > 0.0,
            suggested_protection_r=suggested_protection,
        )

    @staticmethod
    def observe_portfolio_risk(gross_exposure: float, equity: float, hard_limit_fraction: float) -> Dict[str, Any]:
        exposure_fraction = gross_exposure / max(float(equity), 1.0)
        hard_limit = max(float(hard_limit_fraction), 1e-12)
        soft_budget = 0.75 * hard_limit
        if exposure_fraction <= soft_budget:
            derate = 1.0
        else:
            derate = _clip((hard_limit - exposure_fraction) / max(hard_limit - soft_budget, 1e-12), 0.0, 1.0)
        return {
            "gross_exposure_fraction": exposure_fraction,
            "soft_budget_fraction": soft_budget,
            "hard_limit_fraction": hard_limit,
            "suggested_new_risk_derate": derate,
            "at_or_over_hard_limit": exposure_fraction >= hard_limit,
        }

    def record_outcome(self, completed_trade: Dict[str, Any], regime: str = "unknown") -> Dict[str, Any]:
        outcome = {
            "symbol": completed_trade["symbol"],
            "side": completed_trade["side"],
            "regime": regime,
            "net_pnl": float(completed_trade["net_pnl"]),
            "trade_id": completed_trade.get("trade_id"),
            "exit_reason": completed_trade.get("reason"),
        }
        self.outcomes.record(outcome)
        return self.outcomes.profile(outcome["symbol"], outcome["side"], regime)
