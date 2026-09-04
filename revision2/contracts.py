"""Typed input/output contracts for the Revision 2 execution pipeline.

These types exist so each black box has an explicit, checkable boundary
instead of a shared, mutable dict. Nothing here does I/O.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd


def _freeze(value: Any) -> Any:
    """Recursively freeze a value: dict -> MappingProxyType, list -> tuple.
    Scalars pass through unchanged. Used so EffectiveConfig/SafetyContract
    can't be mutated through a nested container even though the dataclass
    itself is frozen."""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    """Inverse of _freeze(): MappingProxyType -> dict, tuple -> list.
    Needed when handing values to code that does a strict `isinstance(x,
    dict)` / `isinstance(x, list)` check — MappingProxyType and tuple are
    deliberately not those types."""
    if isinstance(value, MappingProxyType):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


class MissingParameterError(KeyError):
    """Raised when a box asks for a parameter the effective config doesn't have."""


@dataclass(frozen=True)
class EffectiveConfig:
    """One validated, immutable configuration for a single run.

    `require()` is the only sanctioned read path inside a box — there is no
    `.get(name, default)` escape hatch, so a box can never silently fall back
    to a hardcoded value and still claim to have "consumed" a parameter.
    """

    values: Mapping[str, Any]
    registry_hash: str
    config_hash: str

    def require(self, name: str) -> Any:
        if name not in self.values:
            raise MissingParameterError(name)
        return self.values[name]

    def as_dict(self) -> Dict[str, Any]:
        """A plain, fully-thawed `dict` copy (nested MappingProxyType/tuple
        included) for interfacing with code that does a strict
        `isinstance(x, dict)` / `isinstance(x, list)` check — the registry
        validators, StartupGate, ExecutionGate. Mutating this copy never
        touches the frozen EffectiveConfig itself."""
        return {k: _thaw(v) for k, v in self.values.items()}

    @staticmethod
    def build(values: Dict[str, Any], registry_hash: str) -> "EffectiveConfig":
        payload = json.dumps(values, sort_keys=True, default=str).encode("utf-8")
        config_hash = hashlib.sha256(payload).hexdigest()
        frozen_values = MappingProxyType({k: _freeze(v) for k, v in values.items()})
        return EffectiveConfig(values=frozen_values, registry_hash=registry_hash, config_hash=config_hash)


@dataclass(frozen=True)
class SafetyContract:
    """The 20 hard safety invariants, held apart from the 68-parameter surface.

    This is never merged into a calibration candidate. `values` mirrors the
    canonical safety-parameter defaults; `contract_hash` lets a run assert it
    never drifted from the frozen registry.
    """

    values: Mapping[str, Any]
    contract_hash: str

    def as_dict(self) -> Dict[str, Any]:
        return {k: _thaw(v) for k, v in self.values.items()}

    @staticmethod
    def from_registry(registry) -> "SafetyContract":
        values = {name: spec.default for name, spec in registry.safety_params.items()}
        payload = json.dumps(values, sort_keys=True, default=str).encode("utf-8")
        contract_hash = hashlib.sha256(payload).hexdigest()
        frozen_values = MappingProxyType({k: _freeze(v) for k, v in values.items()})
        return SafetyContract(values=frozen_values, contract_hash=contract_hash)


@dataclass(frozen=True)
class MarketSnapshot:
    """The bars available as of a completed bar `t` — nothing after it."""

    symbol: str
    timestamp: str
    bars: pd.DataFrame  # index 0..t, chronological, bar t is the last row
    next_bar_open: Optional[float] = None  # execution price for t+1, if known


@dataclass(frozen=True)
class PASignal:
    symbol: str
    timestamp: str
    direction: int  # +1 long bias, -1 short bias, 0 neutral
    confidence: float  # normalized 0..1
    momentum: float
    volatility: float
    vwap_deviation: float
    volume_confirmation: float
    exit_confidence: float = 0.0  # separately-smoothed signal for exit timing
    quality_band: str = "neutral"  # green/amber/neutral/red, from PA's threshold ladder


@dataclass(frozen=True)
class IDDecision:
    approved: bool
    reason: str
    confidence: float
    risk_reward_ratio: float
    timing_quality: float  # PID-derived modifier, advisory only — never a hard veto


@dataclass(frozen=True)
class TradePlan:
    side: str
    entry_price: float
    stop_price: float
    target_price: float
    minimum_hold_bars: int
    maximum_hold_bars: int


@dataclass(frozen=True)
class ProposedOrder:
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[float]
    timeout_seconds: int
    max_retries: int
    retry_delay_seconds: int
    slippage_tolerance_fraction: float


@dataclass
class ParameterUse:
    parameter: str
    owner: str
    value: Any
    calculation: str
    output_field: str


@dataclass
class BoxResult:
    """Wraps a box's typed output together with what it actually consumed."""

    output: Any
    trace: list = field(default_factory=list)


class StartupNotCertifiedError(RuntimeError):
    """Raised when an orchestrator is constructed without a passing
    StartupCertificate. No certificate means no run — there is no code path
    that lets a run proceed past this."""


@dataclass(frozen=True)
class StartupCertificate:
    """The result of StartupGate.certify_startup(), bound to the exact
    config/safety hashes it certified and stamped with an integrity hash.

    This is an integrity check, not a cryptographic signature — there is no
    real key-management infrastructure behind it. Calling it "signed" would
    overclaim a security property this project doesn't actually have.
    """

    passed: bool
    operating_mode: str
    broker_environment: str
    reasons: tuple
    config_hash: str
    safety_contract_hash: str
    issued_at: str
    integrity_hash: str

    @staticmethod
    def issue(gate_report: Dict[str, Any], config_hash: str, safety_contract_hash: str) -> "StartupCertificate":
        issued_at = datetime.now(timezone.utc).isoformat()
        reasons = tuple(gate_report.get("reasons", []))
        payload = "|".join([
            str(gate_report["passed"]), str(gate_report["operating_mode"]),
            str(gate_report["broker_environment"]), config_hash, safety_contract_hash, issued_at,
        ])
        integrity_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return StartupCertificate(
            passed=bool(gate_report["passed"]),
            operating_mode=str(gate_report["operating_mode"]),
            broker_environment=str(gate_report["broker_environment"]),
            reasons=reasons,
            config_hash=config_hash,
            safety_contract_hash=safety_contract_hash,
            issued_at=issued_at,
            integrity_hash=integrity_hash,
        )
