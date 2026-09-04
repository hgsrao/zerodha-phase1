"""Backend-neutral contract used for in-house/Backtrader parity runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Mapping, Optional

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry


EVENT_TYPES = {"signal", "gate_decision", "order", "fill", "exit", "cost", "equity"}


def normalize_event_timestamp(value: Any) -> str:
    """Canonical, backend-neutral timestamp string: naive, second precision,
    local wall-clock reading (never converted to UTC).

    Both backends must format event timestamps through this function before
    they reach a BackendEvent, or first_divergence() reports spurious
    mismatches from formatting alone (a tz-offset suffix present on one
    side and not the other, differing sub-second precision, ...) that have
    nothing to do with a real decision or execution difference. This is
    precisely the class of bug that motivated it: Backtrader's PandasData
    silently converts a tz-aware index to naive UTC internally, so the
    in-house side (which reads the source CSV's local-time string directly)
    and a naive Backtrader value need explicit reconciliation, not an
    assumption that str(timestamp) already agrees between backends.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def canonical_parameter_snapshot(
    registry: CanonicalParameterRegistry,
    calibration_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the exact effective parameter surface supplied to each backend."""
    overrides = dict(calibration_overrides or {})
    errors = registry.validate_calibration_payload(overrides)
    if errors:
        raise ValueError("invalid calibration overrides: " + "; ".join(errors))
    target = {name: spec.default for name, spec in registry.params.items()}
    target.update(overrides)
    safety = {name: spec.default for name, spec in registry.safety_params.items()}
    payload = {
        "contract_id": registry.CONTRACT_ID,
        "registry_identity_sha256": registry.identity_sha256(),
        "target": dict(sorted(target.items())),
        "safety": dict(sorted(safety.items())),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["effective_config_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    return payload


@dataclass(frozen=True)
class BackendEvent:
    backend: str
    event_type: str
    symbol: str
    decision_timestamp: str
    event_timestamp: str
    sequence: int
    side: Optional[str] = None
    quantity: Optional[int] = None
    price: Optional[float] = None
    reason: Optional[str] = None
    passed: Optional[bool] = None
    config_hash: Optional[str] = None
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type: {self.event_type}")
        if self.quantity is not None and self.quantity < 0:
            raise ValueError("quantity cannot be negative")
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative")

    def canonical(self) -> Dict[str, Any]:
        """Comparable representation; backend identity is deliberately excluded."""
        value = asdict(self)
        value.pop("backend")
        return value


def first_divergence(
    left: Iterable[BackendEvent], right: Iterable[BackendEvent]
) -> Optional[Dict[str, Any]]:
    """Return the first unequal canonical event, including missing-tail cases."""
    lhs, rhs = list(left), list(right)
    for index in range(max(len(lhs), len(rhs))):
        a = lhs[index].canonical() if index < len(lhs) else None
        b = rhs[index].canonical() if index < len(rhs) else None
        if a != b:
            return {"index": index, "in_house": a, "backtrader": b}
    return None
