from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class Mode(str, Enum):
    NORMAL = "normal"
    DERATED = "derated"
    MINIMUM = "minimum"
    HARD_HALT = "hard_halt"


@dataclass
class CapacityBundle:
    drawdown: float = 1.0
    volatility: float = 1.0
    liquidity: float = 1.0
    daily_loss: float = 1.0


@dataclass
class RiskReport:
    mode: Mode
    risk_capacity: float
    current_drawdown: float
    hard_halt: bool = False
    hard_halt_reason: str = ""
    capacities: CapacityBundle = field(default_factory=CapacityBundle)


class RiskManager:
    def __init__(
        self,
        max_drawdown: float = 0.20,
        max_daily_loss: Decimal = Decimal("-50000"),
        target_atr_pct: float = 0.03,
    ):
        self.max_drawdown = float(max_drawdown)
        self.max_daily_loss = Decimal(max_daily_loss)
        self.target_atr_pct = float(target_atr_pct)
        self.current_mode = Mode.NORMAL

    def calculate(
        self,
        current_equity: Decimal,
        peak_equity: Decimal,
        daily_pnl: Decimal,
        volatility_pct: float,
        bid_ask_spread_pct: float,
        order_book_depth: float,
        open_positions: List[Any],
        broker_healthy: bool,
        data_freshness_ms: int,
        circuit_breaker_healthy: bool,
        consecutive_losses: int,
    ) -> RiskReport:
        current_drawdown = 0.0
        if peak_equity > 0:
            current_drawdown = max(0.0, float((peak_equity - current_equity) / peak_equity))

        capacities = CapacityBundle()
        risk_capacity = 1.0

        if current_drawdown >= self.max_drawdown:
            return RiskReport(
                mode=Mode.HARD_HALT,
                risk_capacity=0.0,
                current_drawdown=current_drawdown,
                hard_halt=True,
                hard_halt_reason=f"drawdown {current_drawdown:.2%} exceeds max {self.max_drawdown:.2%}",
                capacities=capacities,
            )

        drawdown_capacity = max(0.0, (self.max_drawdown - current_drawdown) / max(self.max_drawdown, 1e-9))
        capacities.drawdown = drawdown_capacity
        risk_capacity = min(risk_capacity, drawdown_capacity)

        volatility_capacity = 1.0
        if volatility_pct > 0:
            volatility_capacity = min(1.0, max(0.0, self.target_atr_pct / volatility_pct))
        capacities.volatility = volatility_capacity
        risk_capacity = min(risk_capacity, volatility_capacity)

        if daily_pnl <= self.max_daily_loss:
            return RiskReport(
                mode=Mode.HARD_HALT,
                risk_capacity=0.0,
                current_drawdown=current_drawdown,
                hard_halt=True,
                hard_halt_reason=f"daily loss {daily_pnl} exceeds limit {self.max_daily_loss}",
                capacities=capacities,
            )

        if not broker_healthy or not circuit_breaker_healthy:
            risk_capacity = min(risk_capacity, 0.10)
        if data_freshness_ms > 5000:
            risk_capacity = min(risk_capacity, 0.20)

        risk_capacity = min(risk_capacity, 0.72)

        if self.current_mode == Mode.DERATED:
            if risk_capacity >= 0.75:
                mode = Mode.NORMAL
            elif risk_capacity >= 0.10:
                mode = Mode.DERATED
            else:
                mode = Mode.MINIMUM
        elif risk_capacity >= 0.70:
            mode = Mode.NORMAL
        elif risk_capacity >= 0.10:
            mode = Mode.DERATED
        else:
            mode = Mode.MINIMUM

        self.current_mode = mode

        report = RiskReport(
            mode=mode,
            risk_capacity=risk_capacity,
            current_drawdown=current_drawdown,
            hard_halt=False,
            capacities=capacities,
        )
        report.hard_halt_reason = ""
        return report
