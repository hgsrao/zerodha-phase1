"""Registry-backed configuration access for BB05/BB06; no second defaults table."""
from functools import lru_cache
from math import isfinite
from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig

@lru_cache(maxsize=1)
def _registry():
    return CanonicalParameterRegistry()

def default_config():
    registry = _registry()
    return EffectiveConfig.build(
        {name: spec.default for name, spec in registry.params.items()},
        registry.FROZEN_IDENTITY_SHA256,
    )

def require(config, name):
    """Validate even direct callers; missing/invalid values never fall back."""
    value = config.require(name)
    spec = _registry().get(name)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value)
            or (spec.param_type == "int" and not isinstance(value, int))
            or not spec.minimum <= value <= spec.maximum):
        raise ValueError(f"{name} outside canonical type/bounds")
    return value
