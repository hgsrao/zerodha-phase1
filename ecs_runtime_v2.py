#!/usr/bin/env python3
"""Runtime bootstrap and active execution for the 10-box Revision 2 engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List

from canonical_parameter_registry import CanonicalParameterRegistry


class BlackBoxStatus(Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    HOLD = "HOLD"
    BLOCKED = "BLOCKED"


@dataclass
class RuntimeBootstrapStatus:
    ready: bool
    stage: str
    symbol_count: int
    message: str


Revision2ParameterRegistry = CanonicalParameterRegistry


class ECSRuntimeV2:
    def __init__(self):
        self.registry = Revision2ParameterRegistry()
        self.black_box_order = [
            "StartupCapabilityLock",
            "DataIngestion",
            "L2DataCertifier",
            "PA",
            "ID",
            "MPC",
            "SafetyGates",
            "PositionManager",
            "P01D",
            "UnifiedExecution",
        ]
        self.symbols = [
            "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
            "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
            "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
            "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
            "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
            "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
            "MARUTI", "MAXHEALTH", "NTPC", "ONGC", "POWERGRID",
            "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
            "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN",
            "TRENT", "ULTRACEMCO", "WIPRO",
        ]

    def bootstrap(self) -> RuntimeBootstrapStatus:
        try:
            self.registry.validate_contract()
            self.registry.verify_frozen_identity()
        except ValueError as exc:
            return RuntimeBootstrapStatus(False, "HOLD", 0, f"parameter identity failure: {exc}")
        return RuntimeBootstrapStatus(
            True,
            "BOOTSTRAPPED",
            len(self.symbols),
            "Runtime initialized with 48-symbol universe and 10-box architecture",
        )

    def _box_parameter_names(self, box_name: str) -> List[str]:
        return sorted(name for name, spec in self.registry.params.items() if spec.black_box == box_name)

    def _apply_box(self, box_name: str, effective_config: Dict[str, Any]) -> Dict[str, Any]:
        consumed = self._box_parameter_names(box_name)
        missing = [name for name in consumed if name not in effective_config]
        if missing:
            return {
                "box": box_name,
                "status": BlackBoxStatus.BLOCKED.value,
                "consumed_parameters": consumed,
                "missing_parameters": missing,
                "acknowledged_count": 0,
            }

        acknowledged = [name for name in consumed if name in effective_config]
        return {
            "box": box_name,
            "status": BlackBoxStatus.READY.value,
            "consumed_parameters": acknowledged,
            "missing_parameters": [],
            "acknowledged_count": len(acknowledged),
        }

    def run_cycle(self, effective_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        bootstrap = self.bootstrap()
        if not bootstrap.ready:
            return {"ok": False, "decision": "HOLD", "black_box_statuses": []}

        if effective_config is None:
            effective_config = {name: spec.default for name, spec in self.registry.params.items()}

        if not isinstance(effective_config, dict):
            return {"ok": False, "decision": "HOLD", "black_box_statuses": [], "reason": "effective_config must be a dictionary"}

        consumed_union = set()
        statuses = []
        for box in self.black_box_order:
            status = self._apply_box(box, effective_config)
            statuses.append(status)
            consumed_union.update(status["consumed_parameters"])

        if set(self.registry.params) != consumed_union:
            return {
                "ok": False,
                "decision": "BLOCKED",
                "black_box_statuses": statuses,
                "missing_parameters": sorted(set(self.registry.params) - consumed_union),
                "symbol_count": bootstrap.symbol_count,
                "surface": {
                    "base_33": len(self.registry.base_33()),
                    "revision2_35": len(self.registry.revision2_35()),
                    "hardcoded_20": len(self.registry.hardcoded_20()),
                    "calibratable_45": len(self.registry.calibratable_45()),
                    "total_target_surface": self.registry.total_target_surface(),
                    "identity_sha256": self.registry.identity_sha256(),
                },
            }

        return {
            "ok": True,
            "decision": "ALLOW",
            "black_box_statuses": statuses,
            "symbol_count": bootstrap.symbol_count,
            "surface": {
                "base_33": len(self.registry.base_33()),
                "revision2_35": len(self.registry.revision2_35()),
                "hardcoded_20": len(self.registry.hardcoded_20()),
                "calibratable_45": len(self.registry.calibratable_45()),
                "total_target_surface": self.registry.total_target_surface(),
                "identity_sha256": self.registry.identity_sha256(),
            },
        }


if __name__ == "__main__":
    runtime = ECSRuntimeV2()
    print(runtime.bootstrap())
    print(runtime.run_cycle())
