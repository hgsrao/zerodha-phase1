from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class OperatingMode(str, Enum):
    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class BrokerAdapter:
    environment: str = "unknown"

    def __init__(self, account_id: Optional[str] = None):
        self.account_id = account_id or ""


class SimulatedBrokerAdapter(BrokerAdapter):
    environment = "simulated"


class OrderState(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class PaperOrder:
    """One order's full lifecycle record inside the paper broker."""

    order_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    state: OrderState = OrderState.PENDING
    limit_price: Optional[float] = None
    filled_quantity: int = 0
    filled_price: Optional[float] = None
    submitted_at: Optional[str] = None
    filled_at: Optional[str] = None
    rejection_reason: Optional[str] = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperBrokerAdapter(BrokerAdapter):
    """A real (non-live) paper broker: order state machine, fill simulation
    against a supplied market price, and position/PnL tracking.

    This does not touch Zerodha/Kite or any network resource. `place_order`
    is the entry point a paper-mode trading loop should call once it has a
    market price for the current bar/tick; it runs the same ExecutionGate
    safety contract a live broker would run before it will simulate a fill.
    """

    environment = "paper"

    def __init__(self, account_id: Optional[str] = None, slippage_fraction: float = 0.0005):
        super().__init__(account_id=account_id)
        self.slippage_fraction = slippage_fraction
        self.orders: Dict[str, PaperOrder] = {}
        self.positions: Dict[str, Dict[str, float]] = {}
        self.realized_pnl: float = 0.0
        self.fills: List[Dict[str, Any]] = []

    def get_position(self, symbol: str) -> Dict[str, float]:
        return self.positions.get(symbol, {"quantity": 0, "avg_price": 0.0})

    def unrealized_pnl(self, symbol: str, mark_price: float) -> float:
        position = self.get_position(symbol)
        if not position["quantity"]:
            return 0.0
        return (mark_price - position["avg_price"]) * position["quantity"]

    def _apply_fill_to_position(self, symbol: str, side: str, quantity: int, price: float) -> None:
        position = self.positions.setdefault(symbol, {"quantity": 0, "avg_price": 0.0})
        signed_qty = quantity if side == "BUY" else -quantity
        existing_qty = position["quantity"]
        new_qty = existing_qty + signed_qty

        same_direction_or_flat = existing_qty == 0 or (existing_qty > 0) == (signed_qty > 0)
        if same_direction_or_flat:
            if new_qty != 0:
                position["avg_price"] = (
                    position["avg_price"] * existing_qty + price * signed_qty
                ) / new_qty
        else:
            closing_qty = min(abs(signed_qty), abs(existing_qty))
            direction = 1 if existing_qty > 0 else -1
            realized = (price - position["avg_price"]) * closing_qty * direction
            self.realized_pnl += realized
            if abs(signed_qty) > abs(existing_qty):
                position["avg_price"] = price

        position["quantity"] = new_qty

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        market_price: Optional[float],
        config: Optional[Dict[str, Any]] = None,
        parameter_registry: Optional[Any] = None,
    ) -> Dict[str, Any]:
        order_id = str(uuid.uuid4())
        order = PaperOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            submitted_at=_utcnow_iso(),
        )
        self.orders[order_id] = order

        gate = ExecutionGate()
        validation = gate.validate_pre_submit(config or {}, {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
        }, parameter_registry=parameter_registry)
        if not validation["passed"]:
            order.state = OrderState.REJECTED
            order.rejection_reason = "; ".join(validation["reasons"])
            return {"passed": False, "order_id": order_id, "state": order.state.value, "reasons": validation["reasons"]}

        if market_price is None or not math.isfinite(market_price) or market_price <= 0:
            order.state = OrderState.REJECTED
            order.rejection_reason = "no valid market price to fill against"
            return {"passed": False, "order_id": order_id, "state": order.state.value, "reasons": [order.rejection_reason]}

        slip = market_price * self.slippage_fraction
        fill_price = round(market_price + slip if side == "BUY" else market_price - slip, 4)

        order.filled_quantity = quantity
        order.filled_price = fill_price
        order.state = OrderState.FILLED
        order.filled_at = _utcnow_iso()

        self._apply_fill_to_position(symbol, side, quantity, fill_price)
        self.fills.append({
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": fill_price,
            "market_price": market_price,
            "filled_at": order.filled_at,
        })

        return {
            "passed": True,
            "order_id": order_id,
            "state": order.state.value,
            "filled_quantity": quantity,
            "filled_price": fill_price,
            "market_price": market_price,
            "realized_pnl": self.realized_pnl,
        }


class KiteBrokerAdapter(BrokerAdapter):
    environment = "live"


@dataclass
class RuntimeConfig:
    operating_mode: Optional[OperatingMode] = OperatingMode.RESEARCH
    live_trading_enabled: bool = False
    broker_account_id: str = "ACC123"
    signing_key: str = ""
    durable_db: bool = True
    paper_certification_valid: bool = True
    unresolved_reconciliation: bool = False
    runtime_parameters: Dict[str, Any] = field(default_factory=dict)
    parameter_registry: Optional[Any] = None


class StartupGate:
    """Fail-closed runtime startup policy."""

    def _validate_runtime_parameters(self, config: RuntimeConfig) -> List[str]:
        reasons: List[str] = []
        if config.runtime_parameters is None:
            return ["runtime parameters missing"]
        if not isinstance(config.runtime_parameters, dict):
            return ["runtime parameters must be a dictionary"]
        if not config.runtime_parameters:
            return ["runtime parameters missing"]

        registry = config.parameter_registry
        if registry is None:
            try:
                from canonical_parameter_registry import CanonicalParameterRegistry
                registry = CanonicalParameterRegistry()
            except Exception:
                registry = None

        if registry is None:
            reasons.append("runtime parameter registry unavailable")
            return reasons

        allowed = set(getattr(registry, "params", {}).keys())
        unknown = sorted(set(config.runtime_parameters.keys()) - allowed)
        if unknown:
            reasons.append(f"unknown parameter(s): {', '.join(unknown)}")

        for name, value in config.runtime_parameters.items():
            if name not in allowed:
                continue
            spec = registry.params[name]
            expected_type = spec.param_type
            if expected_type == "int":
                if not isinstance(value, int) or isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected int")
                    continue
                if not (spec.minimum <= value <= spec.maximum):
                    reasons.append(f"range violation for {name}: {value} outside [{spec.minimum}, {spec.maximum}]")
            elif expected_type == "float":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected float")
                    continue
                numeric_value = float(value)
                if not (float(spec.minimum) <= numeric_value <= float(spec.maximum)):
                    reasons.append(f"range violation for {name}: {numeric_value} outside [{spec.minimum}, {spec.maximum}]")
            elif expected_type == "bool":
                if not isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected bool")
            elif expected_type == "str":
                if not isinstance(value, str):
                    reasons.append(f"type mismatch for {name}: expected str")
            elif expected_type in {"list", "dict"}:
                if not isinstance(value, dict if expected_type == "dict" else list):
                    reasons.append(f"type mismatch for {name}: expected {expected_type}")

        return reasons

    def certify_startup(
        self,
        config: RuntimeConfig,
        broker: BrokerAdapter,
        signing_key: str,
        durable_db: bool,
    ) -> Dict[str, Any]:
        reasons: List[str] = []
        passed = True

        if config.operating_mode is None:
            passed = False
            reasons.append("missing operating mode")

        if config.operating_mode is not OperatingMode.RESEARCH and config.operating_mode is not OperatingMode.BACKTEST and config.operating_mode is not OperatingMode.PAPER and config.operating_mode is not OperatingMode.LIVE:
            passed = False
            reasons.append("invalid operating mode")

        if config.operating_mode == OperatingMode.RESEARCH and broker.environment == "live":
            passed = False
            reasons.append("research mode rejects live adapter")

        if config.operating_mode == OperatingMode.BACKTEST and broker.environment == "live":
            passed = False
            reasons.append("backtest mode rejects live adapter")

        if config.operating_mode == OperatingMode.PAPER and broker.environment != "paper":
            passed = False
            reasons.append("paper mode requires a paper broker")

        if config.operating_mode == OperatingMode.LIVE and broker.environment != "live":
            passed = False
            reasons.append("live mode requires a live broker")

        if config.operating_mode == OperatingMode.BACKTEST and broker.environment != "simulated":
            passed = False
            reasons.append("backtest mode requires a simulated broker")

        if config.operating_mode == OperatingMode.RESEARCH and broker.environment != "simulated":
            passed = False
            reasons.append("research mode requires a simulated broker")

        if config.operating_mode == OperatingMode.PAPER and config.live_trading_enabled:
            passed = False
            reasons.append("paper mode does not allow live trading")

        runtime_parameter_reasons = self._validate_runtime_parameters(config)
        if runtime_parameter_reasons:
            passed = False
            reasons.extend(runtime_parameter_reasons)

        if config.operating_mode == OperatingMode.LIVE:
            if not config.live_trading_enabled:
                passed = False
                reasons.append("live trading disabled")
            if not signing_key:
                passed = False
                reasons.append("missing signing key")
            if not durable_db:
                passed = False
                reasons.append("durable database required")
            if getattr(broker, "account_id", None) != config.broker_account_id:
                passed = False
                reasons.append("broker account mismatch")
            if config.unresolved_reconciliation:
                passed = False
                reasons.append("unresolved reconciliation")
            if not config.paper_certification_valid:
                passed = False
                reasons.append("paper certification invalid")

        return {
            "passed": passed,
            "operating_mode": config.operating_mode,
            "broker_environment": getattr(broker, "environment", "unknown"),
            "reasons": reasons,
        }


class ExecutionGate:
    """Fail-closed execution gate before broker submission."""

    def validate_pre_submit(self, config: Dict[str, Any], order: Dict[str, Any], parameter_registry: Optional[Any] = None) -> Dict[str, Any]:
        reasons: List[str] = []
        passed = True

        registry = parameter_registry
        if registry is None:
            try:
                from canonical_parameter_registry import CanonicalParameterRegistry
                registry = CanonicalParameterRegistry()
            except Exception:
                registry = None

        if registry is None:
            return {"passed": False, "reasons": ["execution registry unavailable"]}

        safety_reasons = registry.validate_execution_payload(config)
        if safety_reasons:
            passed = False
            reasons.extend(safety_reasons)

        if not isinstance(order, dict):
            passed = False
            reasons.append("order payload must be a dictionary")
        else:
            required = {"symbol", "side", "quantity", "order_type"}
            missing = sorted(required - set(order.keys()))
            if missing:
                passed = False
                reasons.append(f"missing order fields: {', '.join(missing)}")

            qty = order.get("quantity")
            if qty is not None:
                if isinstance(qty, bool) or not isinstance(qty, int) or not math.isfinite(float(qty)) or qty <= 0:
                    passed = False
                    reasons.append("order quantity must be a positive integer")
                else:
                    min_qty = config.get("min_position_quantity") if isinstance(config, dict) else None
                    max_qty = config.get("max_position_quantity") if isinstance(config, dict) else None
                    if min_qty is not None and qty < int(min_qty):
                        passed = False
                        reasons.append("order quantity below minimum position quantity")
                    if max_qty is not None and qty > int(max_qty):
                        passed = False
                        reasons.append("order quantity exceeds maximum position quantity")

            symbol = order.get("symbol")
            if symbol is not None and (not isinstance(symbol, str) or not symbol.strip()):
                passed = False
                reasons.append("order symbol must be a non-empty string")

            side = order.get("side")
            if side not in {"BUY", "SELL"}:
                passed = False
                reasons.append("order side must be BUY or SELL")

            order_type = order.get("order_type")
            if order_type is not None and (not isinstance(order_type, str) or not order_type.strip()):
                passed = False
                reasons.append("order type must be a non-empty string")

        return {"passed": passed, "reasons": reasons}


class SafeBrokerAdapter(BrokerAdapter):
    """Adapter that validates the canonical safety contract before invoking the broker."""

    def __init__(self, account_id: Optional[str] = None):
        super().__init__(account_id=account_id)
        self.submitted = []

    def submit_order(self, symbol: str, side: str, quantity: int, order_type: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        gate = ExecutionGate()
        payload = {"symbol": symbol, "side": side, "quantity": quantity, "order_type": order_type}
        validation = gate.validate_pre_submit(config or {}, payload)
        if not validation["passed"]:
            return {"passed": False, "reasons": validation["reasons"], "submitted": False}

        self.submitted.append({
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
        })
        return {"passed": True, "reasons": [], "submitted": True}


def simulate_trade_cycle(
    runtime_config: RuntimeConfig,
    broker: BrokerAdapter,
    safety_config: Dict[str, Any],
    order: Dict[str, Any],
) -> Dict[str, Any]:
    startup_gate = StartupGate()
    startup_report = startup_gate.certify_startup(
        runtime_config,
        broker,
        signing_key=runtime_config.signing_key,
        durable_db=runtime_config.durable_db,
    )
    if not startup_report["passed"]:
        return {"passed": False, "reasons": startup_report["reasons"], "submitted": False}

    execution_gate = ExecutionGate()
    execution_report = execution_gate.validate_pre_submit(safety_config, order)
    if not execution_report["passed"]:
        return {"passed": False, "reasons": execution_report["reasons"], "submitted": False}

    if isinstance(broker, SafeBrokerAdapter):
        result = broker.submit_order(
            symbol=order["symbol"],
            side=order["side"],
            quantity=order["quantity"],
            order_type=order["order_type"],
            config=safety_config,
        )
        if result["passed"]:
            return {"passed": True, "reasons": [], "submitted": True}
        return {"passed": False, "reasons": result["reasons"], "submitted": False}

    return {"passed": True, "reasons": [], "submitted": True}
