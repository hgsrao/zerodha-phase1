"""
REVISION 04: EffectiveConfig derived from canonical registry.

Loads the real 69-parameter registry (47 calibratable + 20 safety).
No hand-written parameters. No placeholders.
"""

import sys
sys.path.insert(0, '..')  # Parent directory
from canonical_parameter_registry import CanonicalParameterRegistry, ParameterSpec
from typing import Dict, Any
import dataclasses


class CanonicalConfigBuilder:
    """Builds EffectiveConfig from the frozen canonical registry."""

    def __init__(self):
        self.registry = CanonicalParameterRegistry()
        # Verify frozen identity (V2 contract re-frozen with proper governance)
        self.registry.verify_frozen_identity()
        print(f"✓ Canonical registry loaded and verified (ECS_REVISION_2_PARAMETER_SURFACE_V2)")
        print(f"  Total parameters: {self.registry.total_target_surface()}")
        print(f"  Calibratable: {len(self.registry.calibratable_names())}")
        print(f"  Safety (immutable): {len(self.registry.hardcoded_names())}")

    def get_calibratable_config(self, overrides: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Get ONLY calibratable parameters (47 of 69).
        These MAY be overridden for calibration.

        Args:
            overrides: Optional overrides for the 47 calibratable parameters

        Returns:
            Dict with calibratable params (safety params excluded)
        """
        config = {}

        # Add only calibratable parameters
        for name, spec in self.registry.params.items():
            if spec.calibratable:
                config[name] = overrides.get(name, spec.default) if overrides else spec.default

        return config

    def get_effective_config(self, calibratable_overrides: Dict[str, Any] = None) -> 'EffectiveConfig':
        """
        Build frozen EffectiveConfig with all 69 parameters.
        Safety parameters are NEVER overridden.

        Args:
            calibratable_overrides: Optional overrides for the 47 calibratable parameters only

        Returns:
            Frozen EffectiveConfig dataclass (immutable)
        """
        config_dict = {}

        # Add calibratable parameters with optional overrides
        for name, spec in self.registry.params.items():
            if spec.calibratable:
                config_dict[name] = (
                    calibratable_overrides.get(name, spec.default)
                    if calibratable_overrides
                    else spec.default
                )
            else:
                # Fixed (non-calibratable) parameters use defaults only
                config_dict[name] = spec.default

        # Add IMMUTABLE safety parameters (no overrides allowed)
        for name, spec in self.registry.safety_params.items():
            if name in config_dict:
                raise ValueError(f"Safety param {name} conflicts with target param")
            config_dict[name] = spec.default  # NO overrides

        # Build frozen EffectiveConfig
        from revision4.contracts import EffectiveConfig
        return EffectiveConfig(**config_dict)

    def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """Validate config against registry."""
        all_expected = set(self.registry.params.keys()) | set(self.registry.safety_params.keys())
        config_keys = set(config.keys())

        if config_keys != all_expected:
            missing = all_expected - config_keys
            extra = config_keys - all_expected
            return False, f"Missing: {missing}, Extra: {extra}"

        # Validate ranges
        for name, value in config.items():
            if name in self.registry.params:
                spec = self.registry.params[name]
            else:
                spec = self.registry.safety_params[name]

            # Type check
            if not isinstance(value, type(spec.default)) and spec.default is not None:
                # Allow flexible types for dicts/lists
                if spec.param_type not in ["dict", "list"]:
                    return False, f"{name}: type mismatch (expected {type(spec.default).__name__}, got {type(value).__name__})"

            # Range check
            if spec.minimum is not None and spec.maximum is not None and spec.minimum != 0 and spec.maximum != 0:
                if isinstance(value, (int, float)) and not (spec.minimum <= value <= spec.maximum):
                    return False, f"{name}: value {value} outside range [{spec.minimum}, {spec.maximum}]"

        return True, "Valid"

    def get_calibratable_params(self) -> Dict[str, ParameterSpec]:
        """Get only the 47 calibratable parameters."""
        calibratable_names = self.registry.calibratable_names()
        return {name: self.registry.params[name] for name in calibratable_names}

    def get_safety_params(self) -> Dict[str, ParameterSpec]:
        """Get the 20 immutable safety parameters."""
        return self.registry.safety_params.copy()

    def print_registry_summary(self):
        """Print the registry structure."""
        print("\n" + "="*80)
        print("CANONICAL REGISTRY SUMMARY")
        print("="*80)

        print(f"\nTotal Parameters: {self.registry.total_target_surface()}")
        print(f"  Calibratable: {len(self.registry.calibratable_names())}")
        print(f"  Safety (fixed): {len(self.registry.hardcoded_names())}")

        # Group by box
        by_box = {}
        for name, spec in self.registry.params.items():
            box = spec.black_box
            if box not in by_box:
                by_box[box] = []
            by_box[box].append((name, spec.calibratable))

        print("\nBy Box:")
        for box in sorted(by_box.keys()):
            calibratable_count = sum(1 for _, is_cal in by_box[box] if is_cal)
            total = len(by_box[box])
            print(f"  {box:25s}: {calibratable_count:2d}/{total:2d} calibratable")

        print("\nFrozen Identity:")
        print(f"  {self.registry.identity_sha256()}")
        print(f"  Expected: {self.registry.FROZEN_IDENTITY_SHA256}")

        print("\n" + "="*80)


# Build on import
_builder = CanonicalConfigBuilder()

def get_canonical_config(calibratable_overrides: Dict[str, Any] = None) -> 'EffectiveConfig':
    """
    Get a frozen EffectiveConfig with all 69 parameters.
    Safety parameters are immutable.

    Args:
        calibratable_overrides: Overrides for the 47 calibratable params only

    Returns:
        Frozen EffectiveConfig dataclass
    """
    return _builder.get_effective_config(calibratable_overrides)

def get_calibratable_config(overrides: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Get only the 47 calibratable parameters (as dict).
    Use for calibration input; combine with safety params to build config.
    """
    return _builder.get_calibratable_config(overrides)

def validate_config(config: Dict[str, Any]) -> tuple[bool, str]:
    """Validate a config dict."""
    return _builder.validate_config(config)

def get_calibratable_params() -> Dict[str, ParameterSpec]:
    """Get the 47 calibratable parameters."""
    return _builder.get_calibratable_params()

def get_safety_params() -> Dict[str, ParameterSpec]:
    """Get the 20 immutable safety parameters."""
    return _builder.get_safety_params()
