"""Exact R5 dynamic-parameter evaluation for the R7 engine.

R7 does not duplicate the R5 formulas.  It calls the certified R5 evaluator and
records every field in the immutable ``DynamicParameterSnapshot``.  ``shadow``
and ``paper`` are observation modes here; neither mode overwrites R7's four
evidence-PID gains.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
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
