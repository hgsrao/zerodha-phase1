"""Exact R5 dynamic-parameter evaluation for the R7 engine.

R7 does not duplicate the R5 formulas.  It calls the certified R5 evaluator and
records every field in the immutable ``DynamicParameterSnapshot``.  The evidence-PID
gains are never overwritten. In the engine, ``shadow`` records the governor scales
derived from the native plant's per-bar snapshot and ``paper`` also applies them to
the R7 market governor (see ``governor_scales``).
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import math
from typing import Any, Mapping

from revision5.dynamic_parameters import (
    DynamicParameterSnapshot,
    R5EnvironmentState,
    Revision5DynamicParameterController,
)


class R7AdaptationMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    PAPER = "paper"


def flatten_snapshot(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten every snapshot field without introducing replacement constants."""
    result: dict[str, Any] = {}
    if is_dataclass(value):
        for field in fields(value):
            name = f"{prefix}.{field.name}" if prefix else field.name
            result.update(flatten_snapshot(getattr(value, field.name), name))
        return result
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten_snapshot(item, name))
        return result
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            result.update(flatten_snapshot(item, f"{prefix}[{index}]"))
        return result
    result[prefix] = value
    return result


class R7DynamicParameterAdapter:
    """R5-backed parameter adapter used by R7.

    ``off`` returns no adaptive values. ``shadow`` and ``paper`` evaluate the
    complete R5 snapshot. Safety application is deliberately tighten-only and
    max-hold is capped at the R7 base value. No R5 gain is silently copied into
    an R7 evidence PID.
    """

    def __init__(self, mode: str = "off") -> None:
        try:
            self.mode = R7AdaptationMode(mode)
        except ValueError as exc:
            raise ValueError("adaptation mode must be off, shadow, or paper") from exc
        self.last_snapshot: DynamicParameterSnapshot | None = None
        self.last_parameters: dict[str, Any] = {}

    def evaluate(
        self,
        environment: R5EnvironmentState,
        controller: Revision5DynamicParameterController | None = None,
    ) -> dict[str, Any]:
        if self.mode is R7AdaptationMode.OFF:
            self.last_snapshot = None
            self.last_parameters = {}
            return {}
        evaluator = controller or Revision5DynamicParameterController()
        snapshot = evaluator.evaluate(environment)
        if not isinstance(snapshot, DynamicParameterSnapshot):
            raise TypeError("R5 evaluator did not return DynamicParameterSnapshot")
        self.last_snapshot = snapshot
        self.last_parameters = flatten_snapshot(snapshot)
        return dict(self.last_parameters)

    def effective_max_hold_bars(
        self,
        base_max_hold_bars: int,
        environment: R5EnvironmentState,
        controller: Revision5DynamicParameterController | None = None,
    ) -> int:
        values = self.evaluate(environment, controller)
        candidate = values.get("strategy.max_hold_bars", base_max_hold_bars)
        return min(base_max_hold_bars, int(candidate))

    def tighten_safety_limits(
        self,
        current: Mapping[str, float],
        environment: R5EnvironmentState,
        controller: Revision5DynamicParameterController | None = None,
    ) -> dict[str, float]:
        proposed = self.evaluate(environment, controller)
        result = dict(current)
        lower_is_tighter = {
            "safety.max_gross_exposure_fraction",
            "safety.max_symbol_exposure_fraction",
            "safety.max_concurrent_positions",
            "safety.daily_loss_limit_multiplier",
            "execution.max_market_data_age_seconds",
            "execution.order_timeout_seconds",
        }
        for name in lower_is_tighter:
            if name in current and name in proposed:
                result[name] = min(float(current[name]), float(proposed[name]))
        return result


# The eleven R7 governor parameters. Five follow the R5 governor schedule; six stay at
# the commissioned value because R5 has no approved schedule for them (droop R is fixed
# per run by the allocation spec). All eleven are read by the decision loop every bar.
R7_GOVERNOR_PARAMETERS = (
    'kp', 'ki_per_second', 'kd_seconds', 'integral_limit', 'command_slew_per_second',
    'derivative_tau_seconds', 'market_response_tau_seconds', 'droop_r',
    'avr_weight', 'study_weight', 'dispatch_weight')
R7_ADAPTED_GOVERNOR_PARAMETERS = R7_GOVERNOR_PARAMETERS[:5]


def governor_scales(snapshot: DynamicParameterSnapshot, governor_specs: Mapping[str, Any],
                    bay_id: str) -> dict[str, float]:
    """Dimensionless multipliers for one bay from the native R5 snapshot.

    Each is the R5 schedule's current value over its own commissioned base, the same
    ratio the native engine applies to its execution PIDs, so no R5 gain is copied
    across units. Integral limit and slew are tighten-only (multiplier <= 1).
    """
    profile, spec = snapshot.governor_by_bay[bay_id], governor_specs[bay_id]

    def ratio(name):
        base = float(getattr(spec, name))
        return float(getattr(profile, name)) / base if base else 1.0

    scales = {'kp': ratio('kp'), 'ki_per_second': ratio('ki'), 'kd_seconds': ratio('kd'),
              'integral_limit': min(1.0, ratio('integral_clamp')),
              'command_slew_per_second': min(1.0, float(snapshot.risk_derate_factor))}
    for name, value in scales.items():
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f'invalid R5 governor scale for {bay_id}.{name}: {value!r}')
    if scales['integral_limit'] <= 0.0 or scales['command_slew_per_second'] <= 0.0:
        raise ValueError(f'R5 governor scale would disable a limit for {bay_id}')
    return scales


def adapted_governor_config(base, scales: Mapping[str, float]):
    """The base ControlConfig with the scaled governor parameters (validated on build)."""
    return replace(base, **{name: getattr(base, name) * float(scales[name])
                            for name in R7_ADAPTED_GOVERNOR_PARAMETERS})


def governor_parameter_values(config) -> dict[str, float]:
    return {name: float(getattr(config, name)) for name in R7_GOVERNOR_PARAMETERS}


def parameter_hash(values: Mapping[str, Any]) -> str:
    return sha256(json.dumps(values, sort_keys=True, allow_nan=False).encode()).hexdigest()
