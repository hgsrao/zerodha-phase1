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
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from revision2_external.bb05_bb06_parameters import default_config, require

# Values below are NOT registry parameters, by design:
#   STRUCTURAL: the neutral win rate is the definition of "no evidence"; the
#     derate map derate = 0.5 + win_rate; persistence phi must lie in (0, 1)
#     for a finite half-life; the dynamics lookback floor; numerical guards.
#   FIXED_SAFETY_ENVELOPE: entry derate never below 0.5 and never above 1.0
#     (a supervisory derate can only reduce, never add, risk); a latched HMM
#     stress applies derate 0.0.
#   TELEMETRY_ONLY: gain-scale bounds (suggested_pid_gain_scale feeds nothing).
_NEUTRAL_WIN_RATE = 0.5
_ENTRY_DERATE_BOUNDS = (0.5, 1.0)
_PERSISTENCE_BOUNDS = (0.05, 0.99)
_PERSISTENCE_FALLBACK = 0.5
_MINIMUM_LOOKBACK_FLOOR = 20
_GAIN_SCALE_BOUNDS = (0.5, 1.5)
_LATCHED_DERATE = 0.0
# Registry-derived compatibility alias (not an owner).
_DEFAULT_RESPONSE_TIME_BARS = require(default_config(), "cl_response_time_default_bars")


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
    enter_stress_probability: float | None = None
    exit_stress_probability: float | None = None
    confirmation_bars: int | None = None
    smoothing_alpha: float | None = None
    minimum_derate_step: float | None = None
    deadband_derate: float | None = None
    filtered_probability: float | None = None
    stressed_latched: bool = False
    enter_count: int = 0
    exit_count: int = 0
    applied_derate: float = 1.0

    _CONFIG_FIELDS = (
        ("enter_stress_probability", "cl_hmm_stress_enter"),
        ("exit_stress_probability", "cl_hmm_stress_exit"),
        ("confirmation_bars", "cl_hmm_confirmation_bars"),
        ("smoothing_alpha", "cl_hmm_smoothing_alpha"),
        ("minimum_derate_step", "cl_hmm_min_derate_step"),
        ("deadband_derate", "cl_hmm_deadband_derate"),
    )

    def __post_init__(self) -> None:
        # Unspecified controls come from the canonical registry; explicit
        # constructor values (research scripts) keep their historical meaning.
        config = default_config()
        for attribute, name in self._CONFIG_FIELDS:
            if getattr(self, attribute) is None:
                setattr(self, attribute, require(config, name))
        self._validate()

    def configure(self, config) -> None:
        """Refresh the six controls from ``config``; latch, counters and the
        filtered posterior are preserved.  Validates before mutating."""
        values = {attribute: require(config, name) for attribute, name in self._CONFIG_FIELDS}
        previous = {attribute: getattr(self, attribute) for attribute, _ in self._CONFIG_FIELDS}
        for attribute, value in values.items():
            setattr(self, attribute, value)
        try:
            self._validate()
        except ValueError:
            for attribute, value in previous.items():
                setattr(self, attribute, value)
            raise

    def _validate(self) -> None:
        if not 0.0 <= self.exit_stress_probability < self.enter_stress_probability <= 1.0:
            raise ValueError("hysteresis requires 0 <= exit < enter <= 1")
        if (self.confirmation_bars < 1 or not 0.0 < self.smoothing_alpha <= 1.0
                or not 0.0 < self.minimum_derate_step <= 1.0
                or not 0.0 < self.deadband_derate <= 1.0):
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
        if raw_derate >= self.deadband_derate:
            raw_derate = 1.0
        if self.stressed_latched:
            self.applied_derate = _LATCHED_DERATE
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
    response_time_bars: float = _DEFAULT_RESPONSE_TIME_BARS

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

    def __init__(self, config=None) -> None:
        self._outcomes: List[Dict[str, Any]] = []
        self.configure(config if config is not None else default_config())

    def configure(self, config) -> None:
        """Refresh evidence controls; recorded outcomes are untouched."""
        values = [require(config, n) for n in
                  ("cl_outcome_min_history", "cl_confidence_offset_gain", "cl_confidence_offset_max")]
        self.minimum_history, self.confidence_offset_gain, self.confidence_offset_max = values

    def record(self, outcome: Dict[str, Any]) -> None:
        if "net_pnl" not in outcome or "symbol" not in outcome:
            raise ValueError("outcome ledger requires symbol and net_pnl")
        self._outcomes.append(dict(outcome))

    def profile(self, symbol: str, side: str, regime: str = "unknown", minimum_history: int | None = None) -> Dict[str, Any]:
        if minimum_history is None:
            minimum_history = self.minimum_history
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
        global_win_rate = (global_wins + _NEUTRAL_WIN_RATE * minimum_history) / (len(all_rows) + minimum_history)
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
            "suggested_entry_derate": _clip(_NEUTRAL_WIN_RATE + pooled_win_rate, *_ENTRY_DERATE_BOUNDS),
            # This is a future-entry correction, not a new safety limit.
            # It becomes actionable only after enough completed evidence.
            "suggested_confidence_offset": _clip(
                (_NEUTRAL_WIN_RATE - pooled_win_rate) * self.confidence_offset_gain,
                0.0, self.confidence_offset_max),
        }


class SymbolDynamicsProfiler:
    """Estimate a causal response time and damping from already-closed bars.

    The response time is an effective, bounded half-life of deviations from
    a causal EMA. It is not a claim that a stock is a mechanical oscillator;
    it is a transparent first-order approximation used to shape a reference
    path. The profile is frozen on entry and never recalculated from a
    position's future bars.
    """

    def __init__(self, lookback_bars: int | None = None, ema_span: int | None = None, config=None) -> None:
        self.configure(config if config is not None else default_config())
        if lookback_bars is not None:
            self.lookback_bars = max(_MINIMUM_LOOKBACK_FLOOR, int(lookback_bars))
        if ema_span is not None:
            self.ema_span = max(2, int(ema_span))

    def configure(self, config) -> None:
        """Stateless estimator: controls are refreshed for the next estimate."""
        names = ("cl_dynamics_lookback_bars", "cl_dynamics_ema_span", "cl_response_time_min_bars",
                 "cl_response_time_max_bars", "cl_response_time_default_bars", "cl_damping_response_gain")
        values = [require(config, n) for n in names]
        if values[2] >= values[3]:
            raise ValueError("response-time minimum must be below maximum")
        self.lookback_bars = max(_MINIMUM_LOOKBACK_FLOOR, int(values[0]))
        self.ema_span = max(2, int(values[1]))
        (self.response_time_min, self.response_time_max,
         self.response_time_default, self.damping_gain) = values[2:]

    def estimate(self, bars: Any) -> Dict[str, Any]:
        closes = [float(value) for value in list(bars["close"])[-self.lookback_bars:]]
        if len(closes) < 3:
            return {
                "sample_bars": len(closes), "response_time_bars": self.response_time_default,
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
        persistence = sum(a * b for a, b in zip(previous, current)) / denominator if denominator > 1e-12 else _PERSISTENCE_FALLBACK
        # A stable, finite half-life requires 0 < phi < 1. Out-of-range
        # samples fall back to the closest defensible bounded response.
        bounded_phi = _clip(persistence, *_PERSISTENCE_BOUNDS)
        base_response_time = _clip(math.log(0.5) / math.log(bounded_phi),
                                  self.response_time_min, self.response_time_max)
        absolute_path = sum(abs(b - a) for a, b in zip(closes[:-1], closes[1:]))
        efficiency = abs(closes[-1] - closes[0]) / absolute_path if absolute_path > 1e-12 else 0.0
        damping = _clip(1.0 - efficiency, 0.0, 1.0)
        # A choppy path (high damping) needs MORE, not less, time before a
        # trajectory breach becomes meaningful. Without this correction a
        # mean-reverting/noisy symbol can be assigned the minimum half-life
        # and be force-exited precisely because it is noisy.
        response_time = _clip(base_response_time * (1.0 + self.damping_gain * damping),
                              self.response_time_min, self.response_time_max)
        # Recorded for future PID gain scheduling research. It is not yet
        # fed into the PID gains, avoiding unvalidated mid-trade retuning.
        gain_scale = _clip(self.response_time_default / response_time, *_GAIN_SCALE_BOUNDS)
        return {
            "sample_bars": len(closes), "base_response_time_bars": base_response_time,
            "response_time_bars": response_time,
            "deviation_persistence": persistence, "directional_efficiency": efficiency,
            "damping_ratio": damping, "suggested_pid_gain_scale": gain_scale,
        }


class ClosedLoopSupervisor:
    """Owns the three causal loop calculations, not trade execution."""

    def __init__(self, config=None) -> None:
        config = config if config is not None else default_config()
        self.outcomes = CausalOutcomeLedger(config=config)
        self.dynamics_profiler = SymbolDynamicsProfiler(config=config)
        self.soft_budget_fraction = require(config, "cl_portfolio_soft_budget_fraction")
        self.response_time_default = require(config, "cl_response_time_default_bars")

    def configure(self, config) -> None:
        """Per-evaluation refresh of every supervisory control.  Recorded
        outcomes are preserved; reference paths already frozen into open
        trades are NOT re-derived (they are entry-time snapshots)."""
        soft = require(config, "cl_portfolio_soft_budget_fraction")
        default_rt = require(config, "cl_response_time_default_bars")
        self.outcomes.configure(config)
        self.dynamics_profiler.configure(config)
        self.soft_budget_fraction, self.response_time_default = soft, default_rt

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
            response_time_bars=float((dynamics or {}).get("response_time_bars", self.response_time_default)),
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
    def observe_portfolio_risk(gross_exposure: float, equity: float, hard_limit_fraction: float,
                               soft_budget_fraction: float | None = None) -> Dict[str, Any]:
        if soft_budget_fraction is None:
            soft_budget_fraction = require(default_config(), "cl_portfolio_soft_budget_fraction")
        exposure_fraction = gross_exposure / max(float(equity), 1.0)
        hard_limit = max(float(hard_limit_fraction), 1e-12)
        soft_budget = float(soft_budget_fraction) * hard_limit
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
