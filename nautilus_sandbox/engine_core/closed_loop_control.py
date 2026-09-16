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

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def regime_risk_derate(observation: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a causal HMM posterior into a bounded shadow risk recommendation.

    It cannot create a risk increase or override an existing safety gate.
    Unavailable/unsupported state splits honestly contribute no signal.
    """
    probability = observation.get("stress_probability")
    if not observation.get("available") or probability is None:
        return {"available": False, "stress_probability": None, "suggested_regime_derate": 1.0,
                "reason": observation.get("reason", "UNAVAILABLE")}
    stress = _clip(float(probability), 0.0, 1.0)
    return {"available": True, "stress_probability": stress,
            "suggested_regime_derate": 1.0 - stress, "reason": "POSTERIOR_SHADOW"}


@dataclass
class HMMRiskHysteresis:
    """Causal anti-whipsaw state for an HMM stress posterior.

    The constants are disclosed research settings, not calibrated trading
    parameters.  They require a sustained filtered posterior to latch a
    *shadow* halt recommendation, and a lower sustained posterior to release
    it.  The output is always a one-way brake in the inclusive [0, 1] range.
    """
    enter_stress_probability: float = 0.75
    exit_stress_probability: float = 0.55
    confirmation_bars: int = 3
    smoothing_alpha: float = 0.25
    minimum_derate_step: float = 0.15
    filtered_probability: float | None = None
    stressed_latched: bool = False
    enter_count: int = 0
    exit_count: int = 0
    applied_derate: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.exit_stress_probability < self.enter_stress_probability <= 1.0:
            raise ValueError("hysteresis requires 0 <= exit < enter <= 1")
        if (self.confirmation_bars < 1 or not 0.0 < self.smoothing_alpha <= 1.0
                or not 0.0 < self.minimum_derate_step <= 1.0):
            raise ValueError("invalid hysteresis confirmation or smoothing")

    def update(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        probability = observation.get("stress_probability")
        if not observation.get("available") or probability is None:
            return {
                "available": False, "raw_stress_probability": None,
                "filtered_stress_probability": self.filtered_probability,
                "stressed_latched": self.stressed_latched,
                "suggested_hysteresis_derate": self.applied_derate,
                "reason": observation.get("reason", "UNAVAILABLE"),
            }
        raw = _clip(float(probability), 0.0, 1.0)
        if self.filtered_probability is None:
            self.filtered_probability = raw
        else:
            self.filtered_probability = (
                self.smoothing_alpha * raw + (1.0 - self.smoothing_alpha) * self.filtered_probability
            )
        filtered = self.filtered_probability
        if not self.stressed_latched:
            self.enter_count = self.enter_count + 1 if filtered >= self.enter_stress_probability else 0
            self.exit_count = 0
            if self.enter_count >= self.confirmation_bars:
                self.stressed_latched = True
        else:
            self.exit_count = self.exit_count + 1 if filtered <= self.exit_stress_probability else 0
            self.enter_count = 0
            if self.exit_count >= self.confirmation_bars:
                self.stressed_latched = False
        raw_derate = 1.0 - filtered
        # Snap insignificant stress to the normal baseline, avoiding a
        # stream of pointless 0.97/0.95 resize proposals.
        if raw_derate >= 0.90:
            raw_derate = 1.0
        if self.stressed_latched:
            self.applied_derate = 0.0
        elif abs(raw_derate - self.applied_derate) >= self.minimum_derate_step:
            self.applied_derate = raw_derate
        return {
            "available": True, "raw_stress_probability": raw,
            "filtered_stress_probability": filtered,
            "stressed_latched": self.stressed_latched,
            # Latching is the shadow hard-brake recommendation. Before it
            # latches, smoothed linear derating avoids bar-to-bar jumps.
            "raw_hysteresis_derate": raw_derate,
            "suggested_hysteresis_derate": self.applied_derate,
            "enter_count": self.enter_count, "exit_count": self.exit_count,
            "enter_stress_probability": self.enter_stress_probability,
            "exit_stress_probability": self.exit_stress_probability,
            "confirmation_bars": self.confirmation_bars,
            "minimum_derate_step": self.minimum_derate_step,
            "reason": "LATCHED_STRESS" if self.stressed_latched else "SMOOTH_DERATE_SHADOW",
        }


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
    response_time_bars: float = 20.0

    def progress(self, bars_held: int) -> float:
        """Causal first-order response curve normalized at max hold.

        ``response_time_bars`` is the market analogue of a plant time
        constant. A small value expects a fast move; a large value gives a
        slower symbol more time. It is frozen into the path at entry.
        """
        tau = max(float(self.response_time_bars), 1e-6)
        held = _clip(float(bars_held), 0.0, float(self.max_hold_bars))
        numerator = 1.0 - math.exp(-held / tau)
        denominator = 1.0 - math.exp(-float(self.max_hold_bars) / tau)
        return _clip(numerator / max(denominator, 1e-12), 0.0, 1.0) ** self.curve_gamma

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


class SymbolDynamicsProfiler:
    """Estimate a causal response time and damping from already-closed bars.

    The response time is an effective, bounded half-life of deviations from
    a causal EMA. It is not a claim that a stock is a mechanical oscillator;
    it is a transparent first-order approximation used to shape a reference
    path. The profile is frozen on entry and never recalculated from a
    position's future bars.
    """

    def __init__(self, lookback_bars: int = 60, ema_span: int = 20) -> None:
        self.lookback_bars = max(20, int(lookback_bars))
        self.ema_span = max(2, int(ema_span))

    def estimate(self, bars: Any) -> Dict[str, Any]:
        closes = [float(value) for value in list(bars["close"])[-self.lookback_bars:]]
        if len(closes) < 3:
            return {
                "sample_bars": len(closes), "response_time_bars": 20.0,
                "deviation_persistence": None, "directional_efficiency": 0.0,
                "damping_ratio": 1.0, "suggested_pid_gain_scale": 1.0,
            }
        alpha = 2.0 / (self.ema_span + 1.0)
        ema = closes[0]
        deviations: List[float] = []
        for close in closes:
            ema = alpha * close + (1.0 - alpha) * ema
            deviations.append(close - ema)
        previous, current = deviations[:-1], deviations[1:]
        denominator = sum(value * value for value in previous)
        persistence = sum(a * b for a, b in zip(previous, current)) / denominator if denominator > 1e-12 else 0.5
        # A stable, finite half-life requires 0 < phi < 1. Out-of-range
        # samples fall back to the closest defensible bounded response.
        bounded_phi = _clip(persistence, 0.05, 0.99)
        base_response_time = _clip(math.log(0.5) / math.log(bounded_phi), 5.0, 45.0)
        absolute_path = sum(abs(b - a) for a, b in zip(closes[:-1], closes[1:]))
        efficiency = abs(closes[-1] - closes[0]) / absolute_path if absolute_path > 1e-12 else 0.0
        damping = _clip(1.0 - efficiency, 0.0, 1.0)
        # A choppy path (high damping) needs MORE, not less, time before a
        # trajectory breach becomes meaningful. Without this correction a
        # mean-reverting/noisy symbol can be assigned the minimum half-life
        # and be force-exited precisely because it is noisy.
        response_time = _clip(base_response_time * (1.0 + 2.0 * damping), 5.0, 45.0)
        # Recorded for future PID gain scheduling research. It is not yet
        # fed into the PID gains, avoiding unvalidated mid-trade retuning.
        gain_scale = _clip(20.0 / response_time, 0.5, 1.5)
        return {
            "sample_bars": len(closes), "base_response_time_bars": base_response_time,
            "response_time_bars": response_time,
            "deviation_persistence": persistence, "directional_efficiency": efficiency,
            "damping_ratio": damping, "suggested_pid_gain_scale": gain_scale,
        }


class ClosedLoopSupervisor:
    """Owns the three causal loop calculations, not trade execution."""

    def __init__(self) -> None:
        self.outcomes = CausalOutcomeLedger()
        self.dynamics_profiler = SymbolDynamicsProfiler()

    def entry_snapshot(
        self, symbol: str, side: str, entry_price: float, stop_price: float,
        target_price: float, max_hold_bars: int, regime: str = "unknown",
        dynamics: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        risk = abs(float(entry_price) - float(stop_price))
        if risk <= 0.0:
            raise ValueError("closed-loop reference path requires non-zero initial risk")
        target_r = abs(float(target_price) - float(entry_price)) / risk
        path = TradeReferencePath(
            symbol=symbol, side=side, entry_price=float(entry_price), initial_risk=risk,
            target_r=target_r, max_hold_bars=max(1, int(max_hold_bars)),
            response_time_bars=float((dynamics or {}).get("response_time_bars", 20.0)),
        )
        return {
            "regime": regime,
            "reference_path": path.to_dict(),
            "entry_quality": self.outcomes.profile(symbol, side, regime),
            "symbol_dynamics": dict(dynamics or {}),
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
