"""Causal three-loop controller model for paper-replay research.

This module is deliberately an *actuator contract*, not an alpha generator.
It makes every feedback relationship inspectable and bounded:

* entry quality learns a symbol/side probability profile only after exits;
* trade path compares each held bar with a frozen R-progress path and can only
  tighten its stop;
* portfolio risk compares live heat/drawdown with fixed risk budgets and can
  only derate or halt new risk.

No loop may increase risk in response to a loss or a missed daily P&L target.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, List

from .closed_loop_v2 import CompletedOutcome, EntryQualityLoop, _clip


@dataclass(frozen=True)
class TradePathDecision:
    trade_id: str
    bars_held: int
    max_hold_bars: int
    setpoint_r: float
    measurement_r: float
    error_r: float
    prior_stop_r: float
    proposed_stop_r: float
    stop_r: float
    action: str


class TradePathLoop:
    """Fast, per-trade protective loop using frozen geometry.

    ``target_r`` and ``max_hold_bars`` are frozen at entry.  The reference
    path is concave: a trade gets time to develop early, then must demonstrate
    progress later in its permitted lifetime.  A negative error can tighten a
    stop; positive error never loosens one.
    """
    def __init__(self, progress_gamma: float = 1.75, max_tighten_r_per_bar: float = 0.20) -> None:
        if progress_gamma <= 0 or max_tighten_r_per_bar <= 0:
            raise ValueError("trade-path controller constants must be positive")
        self.progress_gamma = float(progress_gamma)
        self.max_tighten_r_per_bar = float(max_tighten_r_per_bar)

    def update(
        self,
        *,
        trade_id: str,
        bars_held: int,
        max_hold_bars: int,
        target_r: float,
        measurement_r: float,
        prior_stop_r: float,
    ) -> TradePathDecision:
        if max_hold_bars <= 0 or target_r <= 0:
            raise ValueError("frozen trade geometry must be positive")
        fraction = _clip(bars_held / max_hold_bars, 0.0, 1.0)
        setpoint = target_r * (fraction ** self.progress_gamma)
        error = measurement_r - setpoint
        # Only shortfall is actionable.  Never make a stop worse than its
        # existing value, and never tighten beyond the current measurement.
        shortfall = max(0.0, -error)
        tighten = min(self.max_tighten_r_per_bar, shortfall)
        proposed = min(measurement_r, prior_stop_r + tighten)
        stop = max(prior_stop_r, proposed)
        action = "RATCHET" if stop > prior_stop_r else "HOLD"
        return TradePathDecision(trade_id, bars_held, max_hold_bars, setpoint, measurement_r, error, prior_stop_r, proposed, stop, action)


@dataclass(frozen=True)
class PortfolioRiskDecision:
    exposure_setpoint: float
    exposure_measurement: float
    drawdown_limit: float
    drawdown_measurement: float
    exposure_error: float
    drawdown_error: float
    new_risk_derate: float
    halted: bool
    reason: str


class PortfolioRiskLoop:
    """Fast supervisory loop.  It only limits the next order's risk."""
    def __init__(self, target_heat: float = 0.25, drawdown_limit: float = 0.03) -> None:
        if not 0 < target_heat <= 1 or not 0 < drawdown_limit <= 1:
            raise ValueError("portfolio setpoints must lie in (0, 1]")
        self.target_heat = float(target_heat)
        self.drawdown_limit = float(drawdown_limit)

    def decide(self, *, exposure_fraction: float, drawdown_fraction: float) -> PortfolioRiskDecision:
        exposure = _clip(exposure_fraction, 0.0, 1.0)
        drawdown = max(0.0, float(drawdown_fraction))
        halted = drawdown >= self.drawdown_limit
        exposure_error = self.target_heat - exposure
        drawdown_error = self.drawdown_limit - drawdown
        # One-way brake: below target heat does NOT push risk above base.
        exposure_derate = _clip(self.target_heat / max(exposure, self.target_heat), 0.0, 1.0)
        drawdown_derate = _clip(drawdown_error / self.drawdown_limit, 0.0, 1.0)
        derate = 0.0 if halted else min(exposure_derate, drawdown_derate)
        reason = "DRAWDOWN_HALT" if halted else ("EXPOSURE_DERATE" if exposure_derate < 1.0 else "DRAWDOWN_DERATE" if drawdown_derate < 1.0 else "WITHIN_BUDGET")
        return PortfolioRiskDecision(self.target_heat, exposure, self.drawdown_limit, drawdown, exposure_error, drawdown_error, derate, halted, reason)


class ClosedLoopV3Tester:
    """Telemetry-first harness proving the forward/feedback paths are causal."""
    def __init__(
        self,
        entry_quality: EntryQualityLoop | None = None,
        trade_path: TradePathLoop | None = None,
        portfolio_risk: PortfolioRiskLoop | None = None,
    ) -> None:
        self.entry_quality = entry_quality or EntryQualityLoop()
        self.trade_path = trade_path or TradePathLoop()
        self.portfolio_risk = portfolio_risk or PortfolioRiskLoop()
        self.events: List[Dict[str, object]] = []

    def record_outcome(self, outcome: CompletedOutcome) -> None:
        self.entry_quality.record(outcome)
        self.events.append({"event": "COMPLETED_OUTCOME_FEEDBACK", **asdict(outcome)})

    def entry(self, **kwargs: object) -> object:
        decision = self.entry_quality.decide(**kwargs)  # type: ignore[arg-type]
        self.events.append({"event": "ENTRY_QUALITY_CONTROL", **asdict(decision)})
        return decision

    def held_bar(self, **kwargs: object) -> TradePathDecision:
        decision = self.trade_path.update(**kwargs)  # type: ignore[arg-type]
        self.events.append({"event": "TRADE_PATH_CONTROL", **asdict(decision)})
        return decision

    def portfolio(self, **kwargs: object) -> PortfolioRiskDecision:
        decision = self.portfolio_risk.decide(**kwargs)  # type: ignore[arg-type]
        self.events.append({"event": "PORTFOLIO_RISK_CONTROL", **asdict(decision)})
        return decision

    def validate(self) -> None:
        for event in self.events:
            if event["event"] == "ENTRY_QUALITY_CONTROL":
                assert 0.0 <= float(event["entry_derate"]) <= 1.0
            elif event["event"] == "TRADE_PATH_CONTROL":
                assert float(event["stop_r"]) >= float(event["prior_stop_r"])
            elif event["event"] == "PORTFOLIO_RISK_CONTROL":
                assert 0.0 <= float(event["new_risk_derate"]) <= 1.0
                if bool(event["halted"]):
                    assert float(event["new_risk_derate"]) == 0.0
