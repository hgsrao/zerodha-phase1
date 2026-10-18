"""Safe supervisory feedback for a daily net-P&L band, shadow-only.

The positive daily target is deliberately *not* an instruction to take more
risk.  A trading PID which chases a missing profit target would accelerate
after losses.  This controller only derates after realised losses, halts at
the loss boundary, and locks new entries after the net target is reached.
"""

from __future__ import annotations

from typing import Any, Dict, List


class IntradayNetPnlBandShadow:
    def __init__(self, profit_target_rupees: float, loss_limit_rupees: float, quantity: int) -> None:
        if profit_target_rupees <= 0 or loss_limit_rupees <= 0 or quantity <= 0:
            raise ValueError("profit target, loss limit, and quantity must be positive")
        self.profit_target_rupees = float(profit_target_rupees)
        self.loss_limit_rupees = float(loss_limit_rupees)
        self.quantity = int(quantity)
        self.net_pnl_rupees = 0.0
        self.state = "ACTIVE"
        self.events: List[Dict[str, Any]] = []

    def allow_entry(self) -> bool:
        return self.state == "ACTIVE"

    @property
    def throttle(self) -> float:
        # Bounded one-way feedback. It never boosts risk above baseline.
        drawdown_fraction = max(0.0, -self.net_pnl_rupees / self.loss_limit_rupees)
        return max(0.0, min(1.0, 1.0 - drawdown_fraction))

    def record_outcome(self, outcome: Dict[str, Any]) -> None:
        if self.state != "ACTIVE":
            return
        trade_net = float(outcome["net_pnl_per_share"]) * self.quantity
        before = self.net_pnl_rupees
        self.net_pnl_rupees += trade_net
        if self.net_pnl_rupees <= -self.loss_limit_rupees:
            self.state = "LOSS_LIMIT_HALTED"
        elif self.net_pnl_rupees >= self.profit_target_rupees:
            self.state = "PROFIT_TARGET_LOCKED"
        self.events.append({
            "resolution_timestamp": outcome["resolution_timestamp"], "candidate_id": outcome["candidate_id"],
            "trade_net_pnl_rupees_after_costs": trade_net, "daily_net_before": before,
            "daily_net_after": self.net_pnl_rupees, "target_rupees": self.profit_target_rupees,
            "loss_limit_rupees": -self.loss_limit_rupees, "entry_throttle": self.throttle,
            "state": self.state,
        })
