#!/usr/bin/env python3
"""Minimal but functional safety-gate framework for the ECS runtime.

This implementation matches the interface expected by the repository tests while
keeping the logic fail-closed and deterministic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class GateDecision:
    gate_name: str
    passed_: bool
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.passed_


@dataclass
class SystemState:
    portfolio_value: float = 0.0
    current_dd_percent: float = 0.0
    current_lambda: float = 0.0
    daily_realized_loss: float = 0.0
    daily_unrealized_loss: float = 0.0
    open_positions_count: int = 0
    open_positions: List[Any] = field(default_factory=list)
    market_data_age_seconds: int = 0
    broker_connected: bool = True
    broker_offline_seconds: int = 0
    kill_switch_active: bool = False
    circuit_breaker_triggered: bool = False


@dataclass
class EntrySignal:
    symbol: str
    entry_price: float
    stop_loss_price: float
    profit_target_price: float
    confidence: float
    suggested_quantity: int
    position_notional: float
    risk_reward_ratio: float


class GateLogger:
    def log_decision(self, decision: GateDecision):
        pass

    def log_info(self, msg: str):
        pass

    def log_error(self, msg: str):
        pass


@dataclass
class SafetyGateConfig:
    kill_switch_enabled: bool = True
    drawdown_halt_threshold: float = 0.25
    daily_loss_halt_threshold: float = 50000.0
    lambda_derate_threshold: float = 0.15
    lambda_derate_multiplier: float = 0.80
    min_signal_confidence: float = 0.55
    min_risk_reward_ratio: float = 1.50
    slippage_tolerance_percent: float = 0.001
    max_broker_offline_seconds: int = 300
    max_position_quantity: int = 100
    max_concurrent_positions: int = 5
    max_gross_exposure_fraction: float = 0.50
    max_exposure_per_symbol_fraction: float = 0.15
    max_market_data_age_seconds: int = 30
    drawdown_derate_threshold: float = 0.18
    drawdown_derate_multiplier: float = 0.80
    order_dedup_window_seconds: int = 5
    order_timeout_seconds: int = 30
    max_reconciliation_qty_diff: int = 0
    no_entry_cutoff_time: str = "15:20"
    force_close_time: str = "15:25"


class BaseGate:
    def __init__(self, config: Optional[SafetyGateConfig] = None, logger: Optional[GateLogger] = None):
        self.config = config or SafetyGateConfig()
        self.logger = logger or GateLogger()

    def _make_decision(self, gate_name: str, passed: bool, reason: str = "", details: Optional[Dict[str, Any]] = None):
        decision = GateDecision(gate_name=gate_name, passed_=passed, reason=reason, details=details or {})
        self.logger.log_decision(decision)
        return decision


class Gate01KillSwitch(BaseGate):
    def evaluate(self, state: SystemState) -> GateDecision:
        if self.config.kill_switch_enabled and state.kill_switch_active:
            return self._make_decision("Gate01KillSwitch", False, "kill switch active")
        return self._make_decision("Gate01KillSwitch", True, "kill switch inactive")


class Gate02DrawdownHalt(BaseGate):
    def evaluate(self, state: SystemState) -> GateDecision:
        threshold = self.config.drawdown_halt_threshold
        if state.current_dd_percent >= threshold:
            return self._make_decision("Gate02DrawdownHalt", False, f"drawdown {state.current_dd_percent:.2%} exceeds {threshold:.2%}")
        return self._make_decision("Gate02DrawdownHalt", True, "drawdown below halt threshold")


class Gate03DailyLossHalt(BaseGate):
    def evaluate(self, state: SystemState) -> GateDecision:
        threshold = self.config.daily_loss_halt_threshold
        if state.daily_realized_loss >= threshold:
            return self._make_decision("Gate03DailyLossHalt", False, f"daily loss {state.daily_realized_loss} exceeds {threshold}")
        return self._make_decision("Gate03DailyLossHalt", True, "daily loss below halt threshold")


class Gate04BrokerHalt(BaseGate):
    def evaluate(self, state: SystemState) -> GateDecision:
        if not state.broker_connected:
            return self._make_decision("Gate04BrokerHalt", False, "broker disconnected")
        return self._make_decision("Gate04BrokerHalt", True, "broker connected")


class Gate05ConcurrentPositions(BaseGate):
    def evaluate(self, state: SystemState) -> GateDecision:
        if state.open_positions_count >= self.config.max_concurrent_positions:
            return self._make_decision("Gate05ConcurrentPositions", False, "max concurrent positions reached")
        return self._make_decision("Gate05ConcurrentPositions", True, "position count within limit")


class Gate06GrossExposure(BaseGate):
    def evaluate(self, state: SystemState, proposed_notional: float = 0.0) -> GateDecision:
        gross_exposure = sum(float(getattr(p, 'position_notional', 0)) for p in state.open_positions)
        total_after = gross_exposure + proposed_notional
        max_allowed = state.portfolio_value * self.config.max_gross_exposure_fraction
        if total_after > max_allowed:
            return self._make_decision("Gate06GrossExposure", False, f"gross exposure {total_after} exceeds {max_allowed}")
        return self._make_decision("Gate06GrossExposure", True, "gross exposure within limit")


class Gate07StaleData(BaseGate):
    def evaluate(self, state: SystemState) -> GateDecision:
        if state.market_data_age_seconds > self.config.max_market_data_age_seconds:
            return self._make_decision("Gate07StaleData", False, f"market data stale: {state.market_data_age_seconds}s")
        return self._make_decision("Gate07StaleData", True, "market data fresh")


class Gate08SymbolConcentration(BaseGate):
    def evaluate(self, symbol: str, exposure: float, state: SystemState) -> GateDecision:
        max_allowed = state.portfolio_value * self.config.max_exposure_per_symbol_fraction
        if exposure > max_allowed:
            return self._make_decision("Gate08SymbolConcentration", False, f"symbol exposure for {symbol} exceeds limit")
        return self._make_decision("Gate08SymbolConcentration", True, "symbol exposure within limit")


class Gate09PositionQuantity(BaseGate):
    def evaluate(self, quantity: int) -> GateDecision:
        if quantity <= 0:
            return self._make_decision("Gate09PositionQuantity", False, "non-positive order quantity")
        if quantity > self.config.max_position_quantity:
            return self._make_decision("Gate09PositionQuantity", False, f"quantity {quantity} exceeds max {self.config.max_position_quantity}")
        return self._make_decision("Gate09PositionQuantity", True, "quantity within cap")


class Gate10DrawdownDerating(BaseGate):
    def evaluate(self, state: SystemState, size: int) -> Tuple[GateDecision, int]:
        if state.current_dd_percent >= self.config.drawdown_derate_threshold:
            adjusted = int(size * self.config.drawdown_derate_multiplier)
            return self._make_decision("Gate10DrawdownDerating", True, "drawdown derating in effect", {"adjusted_size": adjusted}), adjusted
        return self._make_decision("Gate10DrawdownDerating", True, "drawdown derating not required"), size


class Gate11LambdaDerating(BaseGate):
    def evaluate(self, state: SystemState, size: int) -> Tuple[GateDecision, int]:
        if state.current_lambda >= self.config.lambda_derate_threshold:
            adjusted = int(size * self.config.lambda_derate_multiplier)
            return self._make_decision("Gate11LambdaDerating", True, "lambda derating applied", {"adjusted_size": adjusted}), adjusted
        return self._make_decision("Gate11LambdaDerating", True, "lambda below derating threshold"), size


class Gate12StrategySignals(BaseGate):
    def evaluate(self, signal: EntrySignal) -> GateDecision:
        if signal.confidence < self.config.min_signal_confidence:
            return self._make_decision("Gate12StrategySignals", False, f"confidence {signal.confidence} below threshold {self.config.min_signal_confidence}")
        if signal.risk_reward_ratio < self.config.min_risk_reward_ratio:
            return self._make_decision("Gate12StrategySignals", False, f"risk/reward {signal.risk_reward_ratio} below threshold {self.config.min_risk_reward_ratio}")
        return self._make_decision("Gate12StrategySignals", True, "strategy signals acceptable")


class Gate13OrderDuplication(BaseGate):
    def evaluate(self, seen_recent: bool) -> GateDecision:
        if seen_recent:
            return self._make_decision("Gate13OrderDuplication", False, "duplicate order detected")
        return self._make_decision("Gate13OrderDuplication", True, "order is not duplicated")


class Gate14OrderTimeout(BaseGate):
    def evaluate(self, elapsed_seconds: float) -> GateDecision:
        if elapsed_seconds > self.config.order_timeout_seconds:
            return self._make_decision("Gate14OrderTimeout", False, f"order timeout exceeded: {elapsed_seconds}s")
        return self._make_decision("Gate14OrderTimeout", True, "order within timeout window")


class Gate15OrderReconciliation(BaseGate):
    def evaluate(self, expected_qty: int, actual_qty: int) -> GateDecision:
        delta = abs(expected_qty - actual_qty)
        if delta > self.config.max_reconciliation_qty_diff:
            return self._make_decision("Gate15OrderReconciliation", False, f"reconciliation delta {delta} exceeds tolerance")
        return self._make_decision("Gate15OrderReconciliation", True, "reconciliation matched")


class Gate16Slippage(BaseGate):
    def evaluate(self, target_price: float, fill_price: float) -> GateDecision:
        if target_price <= 0:
            return self._make_decision("Gate16Slippage", False, "target price must be positive")
        slippage_ratio = abs(fill_price - target_price) / target_price
        if slippage_ratio > self.config.slippage_tolerance_percent:
            return self._make_decision("Gate16Slippage", False, f"slippage {slippage_ratio:.4%} exceeds tolerance {self.config.slippage_tolerance_percent:.4%}")
        return self._make_decision("Gate16Slippage", True, "slippage within threshold")


class Gate17MarketClose(BaseGate):
    def evaluate(self, current_time: datetime) -> Tuple[GateDecision, str]:
        cutoff = datetime.strptime(self.config.no_entry_cutoff_time, "%H:%M").time()
        force_close = datetime.strptime(self.config.force_close_time, "%H:%M").time()
        now = current_time.time()
        if now >= force_close:
            return self._make_decision("Gate17MarketClose", False, "market close reached; force close required"), "FORCE_CLOSE"
        if now >= cutoff:
            return self._make_decision("Gate17MarketClose", False, "no new entries after cutoff"), "NO_ENTRY"
        return self._make_decision("Gate17MarketClose", True, "entry window open"), "ALLOW_ENTRY"


class Gate18CircuitBreaker(BaseGate):
    def evaluate(self, state: SystemState) -> GateDecision:
        if state.circuit_breaker_triggered:
            return self._make_decision("Gate18CircuitBreaker", False, "circuit breaker triggered")
        if not state.broker_connected and state.broker_offline_seconds > self.config.max_broker_offline_seconds:
            return self._make_decision("Gate18CircuitBreaker", False, "broker offline past breaker threshold")
        return self._make_decision("Gate18CircuitBreaker", True, "circuit healthy")


class EntryDecisionEngine:
    def __init__(self, config: Optional[SafetyGateConfig] = None, logger: Optional[GateLogger] = None):
        self.config = config or SafetyGateConfig()
        self.logger = logger or GateLogger()
        self.gates = [
            Gate01KillSwitch(self.config, self.logger),
            Gate02DrawdownHalt(self.config, self.logger),
            Gate03DailyLossHalt(self.config, self.logger),
            Gate04BrokerHalt(self.config, self.logger),
            Gate05ConcurrentPositions(self.config, self.logger),
            Gate06GrossExposure(self.config, self.logger),
            Gate07StaleData(self.config, self.logger),
            Gate08SymbolConcentration(self.config, self.logger),
            Gate09PositionQuantity(self.config, self.logger),
            Gate10DrawdownDerating(self.config, self.logger),
            Gate11LambdaDerating(self.config, self.logger),
            Gate12StrategySignals(self.config, self.logger),
            Gate13OrderDuplication(self.config, self.logger),
            Gate14OrderTimeout(self.config, self.logger),
            Gate15OrderReconciliation(self.config, self.logger),
            Gate16Slippage(self.config, self.logger),
            Gate17MarketClose(self.config, self.logger),
            Gate18CircuitBreaker(self.config, self.logger),
        ]
        self._recent_orders: Dict[Tuple[str, int, float], datetime] = {}

    def evaluate(
        self,
        state: SystemState,
        signal: Optional[EntrySignal] = None,
        current_time: Optional[datetime] = None,
        proposed_quantity: int = 0,
        target_price: float = 0.0,
        fill_price: float = 0.0,
        expected_qty: int = 0,
        actual_qty: int = 0,
        symbol: str = "",
        seen_recent: Optional[bool] = None,
        proposed_notional: float = 0.0,
        order_elapsed_seconds: float = 0.0,
    ) -> Dict[str, Any]:
        decision_log: List[GateDecision] = []
        adjusted_quantity = proposed_quantity

        numeric_fields = {
            "portfolio_value": getattr(state, "portfolio_value", 0.0),
            "current_dd_percent": getattr(state, "current_dd_percent", 0.0),
            "current_lambda": getattr(state, "current_lambda", 0.0),
            "daily_realized_loss": getattr(state, "daily_realized_loss", 0.0),
            "daily_unrealized_loss": getattr(state, "daily_unrealized_loss", 0.0),
            "market_data_age_seconds": getattr(state, "market_data_age_seconds", 0),
            "broker_offline_seconds": getattr(state, "broker_offline_seconds", 0),
        }
        invalid_numeric = [name for name, value in numeric_fields.items() if isinstance(value, (int, float)) and not math.isfinite(float(value))]
        if invalid_numeric:
            return {
                "passed": False,
                "gate": "EntryDecisionEngine",
                "reason": f"invalid numeric system state: {', '.join(invalid_numeric)}",
                "adjusted_quantity": adjusted_quantity,
                "decisions": decision_log,
            }

        if signal is not None:
            signal_values = {
                "confidence": signal.confidence,
                "risk_reward_ratio": signal.risk_reward_ratio,
                "entry_price": signal.entry_price,
                "stop_loss_price": signal.stop_loss_price,
                "profit_target_price": signal.profit_target_price,
                "position_notional": signal.position_notional,
            }
            bad_signal = [name for name, value in signal_values.items() if isinstance(value, (int, float)) and not math.isfinite(float(value))]
            if bad_signal:
                return {
                    "passed": False,
                    "gate": "EntryDecisionEngine",
                    "reason": f"invalid signal values: {', '.join(bad_signal)}",
                    "adjusted_quantity": adjusted_quantity,
                    "decisions": decision_log,
                }

        for gate in self.gates:
            if isinstance(gate, Gate01KillSwitch):
                decision = gate.evaluate(state)
            elif isinstance(gate, Gate02DrawdownHalt):
                decision = gate.evaluate(state)
            elif isinstance(gate, Gate03DailyLossHalt):
                decision = gate.evaluate(state)
            elif isinstance(gate, Gate04BrokerHalt):
                decision = gate.evaluate(state)
            elif isinstance(gate, Gate05ConcurrentPositions):
                decision = gate.evaluate(state)
            elif isinstance(gate, Gate06GrossExposure):
                decision = gate.evaluate(state, float(proposed_notional or (signal.position_notional if signal is not None else 0.0)))
            elif isinstance(gate, Gate07StaleData):
                decision = gate.evaluate(state)
            elif isinstance(gate, Gate08SymbolConcentration):
                exposure = float(proposed_notional or (signal.position_notional if signal is not None else 0.0))
                decision = gate.evaluate(symbol or (signal.symbol if signal is not None else "INFY"), exposure, state)
            elif isinstance(gate, Gate09PositionQuantity):
                decision = gate.evaluate(adjusted_quantity)
            elif isinstance(gate, Gate10DrawdownDerating):
                decision, adjusted_quantity = gate.evaluate(state, adjusted_quantity)
            elif isinstance(gate, Gate11LambdaDerating):
                decision, adjusted_quantity = gate.evaluate(state, adjusted_quantity)
            elif isinstance(gate, Gate12StrategySignals):
                if signal is None:
                    decision = GateDecision("Gate12StrategySignals", True)
                else:
                    decision = gate.evaluate(signal)
            elif isinstance(gate, Gate13OrderDuplication):
                order_key = (
                    symbol or (signal.symbol if signal is not None else ""),
                    int(proposed_quantity),
                    round(float(target_price), 8),
                )
                duplicate = seen_recent
                if duplicate is None:
                    previous = self._recent_orders.get(order_key)
                    duplicate = previous is not None and current_time is not None and (
                        current_time - previous
                    ).total_seconds() <= self.config.order_dedup_window_seconds
                decision = gate.evaluate(bool(duplicate))
            elif isinstance(gate, Gate14OrderTimeout):
                decision = gate.evaluate(order_elapsed_seconds)
            elif isinstance(gate, Gate15OrderReconciliation):
                decision = gate.evaluate(expected_qty, actual_qty)
            elif isinstance(gate, Gate16Slippage):
                decision = gate.evaluate(target_price, fill_price)
            elif isinstance(gate, Gate17MarketClose):
                if current_time is None:
                    current_time = datetime.now()
                decision, action = gate.evaluate(current_time)
            elif isinstance(gate, Gate18CircuitBreaker):
                decision = gate.evaluate(state)
            else:
                decision = GateDecision(gate.__class__.__name__, True)
            decision_log.append(decision)
            if not decision.passed:
                return {
                    "passed": False,
                    "gate": decision.gate_name,
                    "reason": decision.reason,
                    "adjusted_quantity": adjusted_quantity,
                    "decisions": decision_log,
                }

        if current_time is not None:
            order_key = (
                symbol or (signal.symbol if signal is not None else ""),
                int(proposed_quantity), round(float(target_price), 8),
            )
            self._recent_orders[order_key] = current_time

        return {
            "passed": True,
            "gate": "EntryDecisionEngine",
            "reason": "all gates passed",
            "adjusted_quantity": adjusted_quantity,
            "decisions": decision_log,
        }

    def evaluate_pre_submit(self, *args, **kwargs) -> Dict[str, Any]:
        """Run decision gates that can truthfully execute before submission.

        Fill reconciliation and slippage are intentionally excluded: evaluating
        them with expected values before a broker responds is a false pass.
        """
        original = self.gates
        self.gates = [g for g in original if not isinstance(g, (Gate15OrderReconciliation, Gate16Slippage))]
        try:
            return self.evaluate(*args, **kwargs)
        finally:
            self.gates = original

    def evaluate_post_fill(
        self, expected_qty: int, actual_qty: int, target_price: float, fill_price: float
    ) -> Dict[str, Any]:
        decisions = [
            Gate15OrderReconciliation(self.config, self.logger).evaluate(expected_qty, actual_qty),
            Gate16Slippage(self.config, self.logger).evaluate(target_price, fill_price),
        ]
        failed = next((d for d in decisions if not d.passed), None)
        return {
            "passed": failed is None,
            "gate": failed.gate_name if failed else "PostFillValidation",
            "reason": failed.reason if failed else "post-fill reconciliation and slippage passed",
            "decisions": decisions,
        }
