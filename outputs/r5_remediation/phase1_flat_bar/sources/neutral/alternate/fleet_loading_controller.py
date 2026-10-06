"""Opt-in fleet exposure PID (prototype interface, paper only).

Execution-neutral and standalone: no orders, no broker, no dispatch/grid coupling.  The caller owns
all wiring; nothing imports this module yet.

Units (all per unit of equity, "pu"):

* ``reference_pu``        target TOTAL gross exposure / equity (not trade R, not conviction)
* ``actual_exposure_pu``  measured TOTAL gross exposure / equity (the feedback)
* ``allowed_capacity_pu`` permitted TOTAL gross exposure / equity (the output)

New-risk headroom is ``allowed_capacity_pu - actual_exposure_pu`` and is formed exactly once, by the
executor, via ``new_risk_headroom_pu``.  The controller never subtracts actual exposure itself and
expresses no BUY/SELL preference.

Math, with e = reference - actual, dt in seconds::

    I'     = clamp(I + e*dt, -L, +L)            (L = integral_limit_pu_seconds)
    P = kp*e        I_term = ki*I        D = kd*(e - e_prev)/dt   (D = 0 on the first sample)
    raw     = reference + P + I_term + D
    cap     = max(0, min(max_capacity_pu, capacity_pu))
    allowed = clamp(raw, 0, cap)

Anti-windup is conditional integration: the integrator holds when the output is saturated and the
error would push it further into that saturation.  A protective trip yields zero capacity, freezes
the integrator and clears the derivative memory (no derivative kick on recovery).  A disabled
policy returns the supplied capacity unchanged and holds no PID state.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from math import isfinite
from typing import Any, Dict, Optional

STATE_SCHEMA = "fleet_loading_controller/1"


def _finite(name: str, value: Any, *, minimum: Optional[float] = None, strictly_positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if strictly_positive and value <= 0.0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value!r}")
    return value


@dataclass(frozen=True)
class FleetLoadingPolicy:
    """Prototype defaults are conservative placeholders, not tuned or certified values."""

    enabled: bool = False
    kp: float = 0.25                          # pu output per pu error
    ki: float = 0.01                          # pu output per (pu*s) of integrated error
    kd: float = 0.0                           # pu output per (pu/s) of error rate
    integral_limit_pu_seconds: float = 10.0   # |integral| bound, pu*s
    max_capacity_pu: float = 1.0              # ceiling on total gross exposure / equity

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be bool, got {self.enabled!r}")
        for name in ("kp", "ki", "kd"):
            object.__setattr__(self, name, _finite(name, getattr(self, name), minimum=0.0))
        for name in ("integral_limit_pu_seconds", "max_capacity_pu"):
            object.__setattr__(self, name, _finite(name, getattr(self, name), strictly_positive=True))

    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "kp": self.kp, "ki": self.ki, "kd": self.kd,
                "integral_limit_pu_seconds": self.integral_limit_pu_seconds,
                "max_capacity_pu": self.max_capacity_pu}

    @property
    def identity(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FleetLoadingTelemetry:
    error_pu: float
    integral_pu_seconds: float
    derivative_pu_per_second: float
    p: float
    i: float
    d: float
    raw_output_pu: float
    allowed_capacity_pu: float
    saturated: bool
    protection_tripped: bool


def new_risk_headroom_pu(allowed_capacity_pu: float, actual_exposure_pu: float) -> float:
    """Executor-side headroom: the single place actual exposure is subtracted from the output."""
    allowed = _finite("allowed_capacity_pu", allowed_capacity_pu, minimum=0.0)
    actual = _finite("actual_exposure_pu", actual_exposure_pu, minimum=0.0)
    return max(0.0, allowed - actual)


class FleetLoadingController:
    def __init__(self, policy: FleetLoadingPolicy) -> None:
        if not isinstance(policy, FleetLoadingPolicy):
            raise TypeError("policy must be a FleetLoadingPolicy")
        self._policy = policy
        self._integral = 0.0
        self._prev_error: Optional[float] = None
        self._updates = 0

    @property
    def policy(self) -> FleetLoadingPolicy:
        return self._policy

    def update(self, reference_pu: float, actual_exposure_pu: float, dt_seconds: float,
               capacity_pu: float, protection_tripped: bool = False) -> FleetLoadingTelemetry:
        reference = _finite("reference_pu", reference_pu, minimum=0.0)
        actual = _finite("actual_exposure_pu", actual_exposure_pu, minimum=0.0)
        dt = _finite("dt_seconds", dt_seconds, strictly_positive=True)
        capacity = _finite("capacity_pu", capacity_pu, minimum=0.0)
        if not isinstance(protection_tripped, bool):
            raise ValueError(f"protection_tripped must be bool, got {protection_tripped!r}")
        pol = self._policy
        error = reference - actual

        if protection_tripped:
            self._prev_error = None
            self._updates += 1
            return FleetLoadingTelemetry(error, self._integral, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, True)

        if not pol.enabled:
            self._updates += 1
            return FleetLoadingTelemetry(error, 0.0, 0.0, 0.0, 0.0, 0.0, capacity, capacity, False, False)

        cap = max(0.0, min(pol.max_capacity_pu, capacity))
        derivative = 0.0 if self._prev_error is None else (error - self._prev_error) / dt
        p = pol.kp * error
        d = pol.kd * derivative
        pre = reference + p + pol.ki * self._integral + d
        winding = (pre > cap and error > 0.0) or (pre < 0.0 and error < 0.0)
        if not winding:
            limit = pol.integral_limit_pu_seconds
            self._integral = max(-limit, min(limit, self._integral + error * dt))
        i = pol.ki * self._integral
        raw = reference + p + i + d
        allowed = max(0.0, min(cap, raw))
        self._prev_error = error
        self._updates += 1
        return FleetLoadingTelemetry(error, self._integral, derivative, p, i, d, raw, allowed,
                                     raw > cap or raw < 0.0, False)

    # ------------------------------------------------------------------ persistence
    def export_state(self) -> str:
        return json.dumps({"schema": STATE_SCHEMA, "policy": self._policy.to_dict(),
                           "policy_identity": self._policy.identity, "integral_pu_seconds": self._integral,
                           "prev_error_pu": self._prev_error, "updates": self._updates},
                          sort_keys=True, allow_nan=False)

    def restore_state(self, payload: str) -> None:
        def reject_constant(token: str) -> None:
            raise ValueError(f"non-finite JSON constant {token}")

        if not isinstance(payload, str):
            raise ValueError("state payload must be a JSON string")
        try:
            data = json.loads(payload, parse_constant=reject_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid state JSON: {exc}") from exc
        expected = {"schema", "policy", "policy_identity", "integral_pu_seconds", "prev_error_pu", "updates"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("state keys mismatch")
        if data["schema"] != STATE_SCHEMA:
            raise ValueError(f"unsupported state schema {data['schema']!r}")
        stored = data["policy"]
        if not isinstance(stored, dict) or set(stored) != set(self._policy.to_dict()):
            raise ValueError("state policy keys mismatch")
        rebuilt = FleetLoadingPolicy(**stored)  # re-validates every field
        if rebuilt != self._policy or data["policy_identity"] != self._policy.identity \
                or rebuilt.identity != data["policy_identity"]:
            raise ValueError("state policy identity does not match controller policy")
        integral = _finite("integral_pu_seconds", data["integral_pu_seconds"])
        if abs(integral) > self._policy.integral_limit_pu_seconds:
            raise ValueError("integral exceeds policy limit")
        prev = data["prev_error_pu"]
        prev = None if prev is None else _finite("prev_error_pu", prev)
        updates = data["updates"]
        if isinstance(updates, bool) or not isinstance(updates, int) or updates < 0:
            raise ValueError("updates must be a nonnegative integer")
        self._integral, self._prev_error, self._updates = integral, prev, updates
