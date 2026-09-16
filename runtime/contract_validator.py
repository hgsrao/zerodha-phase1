from __future__ import annotations

from typing import Any, Dict, List, Optional

from canonical_parameter_registry import CanonicalParameterRegistry
from runtime.operating_mode import (
    ExecutionGate,
    KiteBrokerAdapter,
    OperatingMode,
    PaperBrokerAdapter,
    RuntimeConfig,
    SimulatedBrokerAdapter,
    StartupGate,
)


class ContractValidator:
    """Central validator for runtime config, calibration payloads, safety invariants, and orders."""

    def __init__(self, registry: Optional[CanonicalParameterRegistry] = None):
        self.registry = registry or CanonicalParameterRegistry()

    def validate_runtime_config(self, config: RuntimeConfig) -> List[str]:
        gate = StartupGate()

        if hasattr(config, "runtime_broker") and config.runtime_broker is not None:
            broker = config.runtime_broker
        elif config.operating_mode == OperatingMode.PAPER:
            broker = PaperBrokerAdapter(account_id=config.broker_account_id)
        elif config.operating_mode == OperatingMode.LIVE:
            broker = KiteBrokerAdapter(account_id=config.broker_account_id)
        else:
            broker = SimulatedBrokerAdapter(account_id=config.broker_account_id)

        report = gate.certify_startup(
            config=config,
            broker=broker,
            signing_key=config.signing_key,
            durable_db=config.durable_db,
        )
        return report["reasons"] if not report["passed"] else []

    def validate_calibration_payload(self, payload: Dict[str, Any]) -> List[str]:
        return self.registry.validate_calibration_payload(payload)

    def validate_execution_payload(self, payload: Dict[str, Any]) -> List[str]:
        return self.registry.validate_execution_payload(payload)

    def validate_order_payload(self, order: Dict[str, Any]) -> List[str]:
        reasons: List[str] = []
        if not isinstance(order, dict):
            return ["order payload must be a dictionary"]
        required = {"symbol", "side", "quantity", "order_type"}
        missing = sorted(required - set(order.keys()))
        if missing:
            reasons.append(f"missing order fields: {', '.join(missing)}")
        if order.get("quantity") is not None and (not isinstance(order.get("quantity"), (int, float)) or order.get("quantity") <= 0):
            reasons.append("order quantity must be positive")
        if order.get("side") not in {"BUY", "SELL"}:
            reasons.append("order side must be BUY or SELL")
        return reasons

    def validate_full_cycle(
        self,
        runtime_config: RuntimeConfig,
        safety_config: Dict[str, Any],
        order: Dict[str, Any],
    ) -> Dict[str, Any]:
        reasons: List[str] = []

        runtime_reasons = self.validate_runtime_config(runtime_config)
        if runtime_reasons:
            reasons.extend(runtime_reasons)

        calibration_reasons = self.validate_calibration_payload(runtime_config.runtime_parameters)
        if calibration_reasons:
            reasons.extend(calibration_reasons)

        safety_reasons = self.validate_execution_payload(safety_config)
        if safety_reasons:
            reasons.extend(safety_reasons)

        order_reasons = self.validate_order_payload(order)
        if order_reasons:
            reasons.extend(order_reasons)

        return {"passed": not reasons, "reasons": reasons}
