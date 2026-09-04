"""Box 1 (StartupCapabilityLock) -- Pydantic-based runtime parameter validation.

Replaces StartupGate._validate_runtime_parameters()'s hand-written type/range
checks (runtime/operating_mode.py) with a Pydantic model built DYNAMICALLY
from the canonical registry, so the schema can never drift out of sync with
the 68 real parameters -- there is no separately-maintained field list to go
stale.

Two deliberate, documented differences from the original hand-written
validator, not silent behavior changes:
  1. Every declared registry parameter is now REQUIRED. The original only
     validated whichever keys happened to be present in the payload and
     never flagged a missing one -- a real gap this closes.
  2. Unknown parameter names are rejected outright (Pydantic's
     extra="forbid") rather than collected into an "unknown parameter(s)"
     message alongside otherwise-valid ones. Both are fail-closed; this one
     fails on the first pass instead of enumerating every problem at once,
     which is worth knowing if you were relying on the original's more
     verbose report.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from canonical_parameter_registry import CanonicalParameterRegistry

_TYPE_MAP = {"int": int, "float": float, "bool": bool, "str": str, "list": list, "dict": dict}


def build_runtime_parameter_model(registry: CanonicalParameterRegistry) -> Type[BaseModel]:
    """Builds a Pydantic model whose fields are exactly the registry's
    calibratable + fixed target parameter names, each typed and range-
    constrained from its own ParameterSpec. Safety parameters are validated
    separately (they're a distinct, never-merged surface -- see
    validate_safety_contract below), matching the project's own separation
    of the 68-parameter target surface from the 20-item safety contract."""
    fields: Dict[str, Tuple[Any, Any]] = {}
    for name, spec in registry.params.items():
        py_type = _TYPE_MAP.get(spec.param_type, Any)
        # minimum == maximum == 0 is the registry's own documented sentinel
        # for "no real range applies" (used throughout the fixed/safety
        # surface, e.g. every safety param and every string-typed param) --
        # not a literal [0, 0] bound. Enforcing it literally would reject
        # every valid safety value outright.
        has_real_range = (
            spec.param_type in ("int", "float")
            and spec.minimum is not None and spec.maximum is not None
            and not (spec.minimum == 0 and spec.maximum == 0)
        )
        if has_real_range:
            fields[name] = (py_type, Field(ge=spec.minimum, le=spec.maximum))
        else:
            fields[name] = (py_type, ...)
    return create_model(
        "RuntimeParameterModel",
        __config__=ConfigDict(strict=True, extra="forbid"),
        **fields,
    )


def build_safety_contract_model(registry: CanonicalParameterRegistry) -> Type[BaseModel]:
    fields: Dict[str, Tuple[Any, Any]] = {}
    for name, spec in registry.safety_params.items():
        py_type = _TYPE_MAP.get(spec.param_type, Any)
        # minimum == maximum == 0 is the registry's own documented sentinel
        # for "no real range applies" (used throughout the fixed/safety
        # surface, e.g. every safety param and every string-typed param) --
        # not a literal [0, 0] bound. Enforcing it literally would reject
        # every valid safety value outright.
        has_real_range = (
            spec.param_type in ("int", "float")
            and spec.minimum is not None and spec.maximum is not None
            and not (spec.minimum == 0 and spec.maximum == 0)
        )
        if has_real_range:
            fields[name] = (py_type, Field(ge=spec.minimum, le=spec.maximum))
        else:
            fields[name] = (py_type, ...)
    return create_model(
        "SafetyContractModel",
        __config__=ConfigDict(strict=True, extra="forbid"),
        **fields,
    )


def validate_runtime_parameters(registry: CanonicalParameterRegistry, values: Dict[str, Any]) -> List[str]:
    """Returns a list of human-readable failure reasons (empty == passed),
    matching StartupGate._validate_runtime_parameters()'s own return shape
    so this can be dropped into certify_startup() in place of it."""
    if not isinstance(values, dict) or not values:
        return ["runtime parameters missing"]
    model = build_runtime_parameter_model(registry)
    try:
        model.model_validate(values)
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return []


def validate_safety_contract(registry: CanonicalParameterRegistry, values: Dict[str, Any]) -> List[str]:
    if not isinstance(values, dict) or not values:
        return ["safety contract missing"]
    model = build_safety_contract_model(registry)
    try:
        model.model_validate(values)
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return []
