#!/usr/bin/env python3
"""
================================================================================
ECS PARAMETER MANAGEMENT LAYER - INTEGRATED INTO R1 (LIVE STRATEGY PARAMETERS)
================================================================================

Black Box 4: 28-Parameter Injection Manager (after lambda param separation)
Black Box 5: Parameter Range Validator
Black Box 6: Parameter Tier Classifier
Black Box 7: Parameter Sampler

Manages 28 LIVE TRADING PARAMETERS with:
- Tier 1: 20 critical entry/exit parameters
- Tier 2: 8 tactical signal tuning parameters
- Tier 3: 3 optimizer control parameters (moved to calibration_config.py)
- Position management: 2 parameters

IMPORTANT: lambda_risk_trigger_level and lambda_reduction_factor
have been moved to safety_gates_config.py (hard-coded operational).
See LAMBDA_VS_DRAWDOWN_TECHNICAL_SPEC.md for details.

Features:
- Tier classification (Tier 1/2/3)
- Range validation (bounds checking)
- Safe injection into system
- Random + intelligent sampling

================================================================================
"""

import json
import logging
import numpy as np
from typing import Dict, List, Tuple, Optional
from enum import Enum
from dataclasses import dataclass
import threading

# ============================================================================
# PARAMETER TIER CLASSIFICATION
# ============================================================================

class ParameterTier(Enum):
    """Parameter tiers for calibration priority"""
    TIER_1_CRITICAL = "tier_1_critical"      # Entry/exit confidence, risk/reward
    TIER_2_TACTICAL = "tier_2_tactical"      # Signal tuning, cost adjustments
    TIER_3_ADVANCED = "tier_3_advanced"      # Calibration control, learning

# ============================================================================
# PARAMETER DEFINITIONS WITH RANGES & TIERS
# ============================================================================

@dataclass
class ParameterSpec:
    """Complete specification for one parameter"""
    name: str
    tier: ParameterTier
    param_type: str  # 'float', 'int', 'choice'
    min_value: float
    max_value: float
    default: float
    step: float  # Granularity for sampling
    description: str
    unit: str  # 'fraction', 'percent', 'bars', 'rupees', etc.

    def validate(self, value: float) -> Tuple[bool, Optional[str]]:
        """Validate if value is within bounds"""
        if value < self.min_value:
            return False, f"{self.name} ({value}) < min ({self.min_value})"
        if value > self.max_value:
            return False, f"{self.name} ({value}) > max ({self.max_value})"
        return True, None


# ============================================================================
# PARAMETER TIER CLASSIFIER
# ============================================================================

class ParameterTierClassifier:
    """
    Classifies and organizes all 33 parameters by tier.
    Enables prioritized calibration strategy.
    """

    # All 28 LIVE TRADING PARAMETERS with complete specifications
    # (Lambda parameters moved to safety_gates_config.py)
    PARAMETER_SPECS = {
        # ====================================================================
        # TIER 1: CRITICAL ENTRY/EXIT PARAMETERS (20 params)
        # ====================================================================
        'base_dp_dt_multiplier': ParameterSpec(
            name='base_dp_dt_multiplier',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.5, max_value=2.0, default=1.0, step=0.05,
            description='Price momentum (dP/dt) multiplier',
            unit='fraction'
        ),
        'base_dv_dt_multiplier': ParameterSpec(
            name='base_dv_dt_multiplier',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.5, max_value=2.0, default=1.0, step=0.05,
            description='Volume momentum (dV/dt) multiplier',
            unit='fraction'
        ),
        'entry_confidence_threshold': ParameterSpec(
            name='entry_confidence_threshold',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.3, max_value=0.8, default=0.5, step=0.02,
            description='Min confidence for entry signal',
            unit='fraction'
        ),
        'exit_confidence_threshold': ParameterSpec(
            name='exit_confidence_threshold',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.4, max_value=0.9, default=0.6, step=0.02,
            description='Min confidence for exit signal',
            unit='fraction'
        ),
        'min_risk_reward_ratio': ParameterSpec(
            name='min_risk_reward_ratio',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=1.0, max_value=3.0, default=1.5, step=0.1,
            description='Minimum risk/reward ratio for trade entry',
            unit='ratio'
        ),
        'profit_target_margin_buffer': ParameterSpec(
            name='profit_target_margin_buffer',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.0, max_value=0.5, default=0.1, step=0.02,
            description='Buffer above expected profit target',
            unit='fraction'
        ),
        'vwap_weight': ParameterSpec(
            name='vwap_weight',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.1, max_value=0.4, default=0.25, step=0.02,
            description='Weight for VWAP signal',
            unit='fraction'
        ),
        'confirmation_2bar_weight': ParameterSpec(
            name='confirmation_2bar_weight',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.1, max_value=0.4, default=0.25, step=0.02,
            description='Weight for 2-bar confirmation signal',
            unit='fraction'
        ),
        'momentum_weight': ParameterSpec(
            name='momentum_weight',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.1, max_value=0.4, default=0.25, step=0.02,
            description='Weight for momentum signal',
            unit='fraction'
        ),
        'volatility_weight': ParameterSpec(
            name='volatility_weight',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.05, max_value=0.4, default=0.25, step=0.02,
            description='Weight for volatility signal (balances to 1.0)',
            unit='fraction'
        ),
        'green_threshold': ParameterSpec(
            name='green_threshold',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.6, max_value=0.95, default=0.75, step=0.02,
            description='Green (strong buy) signal threshold',
            unit='fraction'
        ),
        'amber_threshold_lower': ParameterSpec(
            name='amber_threshold_lower',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.3, max_value=0.7, default=0.50, step=0.02,
            description='Amber (caution) signal lower threshold',
            unit='fraction'
        ),
        'red_threshold': ParameterSpec(
            name='red_threshold',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.1, max_value=0.5, default=0.30, step=0.02,
            description='Red (hold/exit) signal threshold',
            unit='fraction'
        ),
        'slippage_guard_threshold': ParameterSpec(
            name='slippage_guard_threshold',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.01, max_value=0.15, default=0.05, step=0.01,
            description='Max acceptable slippage % at entry',
            unit='percent'
        ),
        'volatility_regime_multiplier': ParameterSpec(
            name='volatility_regime_multiplier',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.7, max_value=1.5, default=1.0, step=0.05,
            description='Confidence multiplier in high volatility',
            unit='fraction'
        ),
        'low_vol_regime_multiplier': ParameterSpec(
            name='low_vol_regime_multiplier',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.8, max_value=1.5, default=1.0, step=0.05,
            description='Confidence multiplier in low volatility',
            unit='fraction'
        ),
        'medium_vol_regime_multiplier': ParameterSpec(
            name='medium_vol_regime_multiplier',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.7, max_value=1.5, default=1.0, step=0.05,
            description='Confidence multiplier in medium volatility',
            unit='fraction'
        ),
        'high_vol_regime_multiplier': ParameterSpec(
            name='high_vol_regime_multiplier',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.8, max_value=1.5, default=1.0, step=0.05,
            description='Confidence multiplier in very high volatility',
            unit='fraction'
        ),
        'profit_target_atr_mult': ParameterSpec(
            name='profit_target_atr_mult',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.8, max_value=2.5, default=1.5, step=0.1,
            description='Profit target = ATR × this multiplier',
            unit='multiple'
        ),
        'stop_loss_atr_mult': ParameterSpec(
            name='stop_loss_atr_mult',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='float',
            min_value=0.3, max_value=1.2, default=0.75, step=0.05,
            description='Stop loss = ATR × this multiplier',
            unit='multiple'
        ),

        # ====================================================================
        # TIER 2: TACTICAL SIGNAL TUNING (8 params)
        # ====================================================================
        'atr_calculation_period': ParameterSpec(
            name='atr_calculation_period',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='int',
            min_value=10, max_value=30, default=20, step=1,
            description='Bars for ATR calculation',
            unit='bars'
        ),
        'entry_signal_smoothing_window': ParameterSpec(
            name='entry_signal_smoothing_window',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='int',
            min_value=1, max_value=8, default=3, step=1,
            description='Bars for entry signal EMA smoothing',
            unit='bars'
        ),
        'exit_signal_smoothing_window': ParameterSpec(
            name='exit_signal_smoothing_window',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='int',
            min_value=1, max_value=4, default=2, step=1,
            description='Bars for exit signal EMA smoothing',
            unit='bars'
        ),
        'slippage_cost_multiplier': ParameterSpec(
            name='slippage_cost_multiplier',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='float',
            min_value=0.8, max_value=1.5, default=1.0, step=0.05,
            description='Multiplier for assumed slippage costs',
            unit='fraction'
        ),
        'minimum_absolute_profit_rupees': ParameterSpec(
            name='minimum_absolute_profit_rupees',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='float',
            min_value=0, max_value=200, default=50, step=10,
            description='Minimum absolute profit required per trade',
            unit='rupees'
        ),
        'momentum_calculation_period': ParameterSpec(
            name='momentum_calculation_period',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='int',
            min_value=10, max_value=30, default=20, step=1,
            description='Bars for momentum (SPEED) calculation',
            unit='bars'
        ),
        'vwap_calculation_period': ParameterSpec(
            name='vwap_calculation_period',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='int',
            min_value=10, max_value=30, default=20, step=1,
            description='Bars for VWAP calculation',
            unit='bars'
        ),
        'signal_persistence_requirement': ParameterSpec(
            name='signal_persistence_requirement',
            tier=ParameterTier.TIER_2_TACTICAL,
            param_type='float',
            min_value=1.0, max_value=2.5, default=1.5, step=0.1,
            description='Bars required for signal confirmation',
            unit='bars'
        ),

        # ====================================================================
        # TIER 3: OPTIMIZER CONTROL ONLY (3 params)
        # NOTE: lambda_risk_trigger_level and lambda_reduction_factor
        #       have been moved to safety_gates_config.py (hard-coded operational parameters)
        #       They are NOT strategy parameters and should NOT be calibrated.
        # See: LAMBDA_VS_DRAWDOWN_TECHNICAL_SPEC.md, PARAMETER_RENAME_GUIDE.md
        # ====================================================================
        'phase1_exploration_intensity': ParameterSpec(
            name='phase1_exploration_intensity',
            tier=ParameterTier.TIER_3_ADVANCED,
            param_type='int',
            min_value=30, max_value=100, default=50, step=5,
            description='Randomness level in Phase 1 (higher=more random)',
            unit='percent'
        ),
        'phase2_optimization_intensity': ParameterSpec(
            name='phase2_optimization_intensity',
            tier=ParameterTier.TIER_3_ADVANCED,
            param_type='int',
            min_value=100, max_value=500, default=250, step=25,
            description='Bayesian optimization iterations in Phase 2',
            unit='iterations'
        ),
        'learning_rate_exploration_factor': ParameterSpec(
            name='learning_rate_exploration_factor',
            tier=ParameterTier.TIER_3_ADVANCED,
            param_type='float',
            min_value=0.01, max_value=0.1, default=0.05, step=0.01,
            description='Learning rate for meta-learning in exploration',
            unit='fraction'
        ),

        # ====================================================================
        # POSITION MANAGEMENT (2 existing params moved to Tier 1)
        # ====================================================================
        'min_hold_bars': ParameterSpec(
            name='min_hold_bars',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='int',
            min_value=1, max_value=5, default=2, step=1,
            description='Minimum bars to hold position',
            unit='bars'
        ),
        'max_hold_bars': ParameterSpec(
            name='max_hold_bars',
            tier=ParameterTier.TIER_1_CRITICAL,
            param_type='int',
            min_value=20, max_value=120, default=60, step=5,
            description='Maximum bars to hold position',
            unit='bars'
        ),
    }

    def __init__(self):
        self.logger = logging.getLogger("ParameterTierClassifier")

    def get_tier_1_params(self) -> Dict[str, ParameterSpec]:
        """Get all Tier 1 (critical) parameters"""
        return {
            name: spec for name, spec in self.PARAMETER_SPECS.items()
            if spec.tier == ParameterTier.TIER_1_CRITICAL
        }

    def get_tier_2_params(self) -> Dict[str, ParameterSpec]:
        """Get all Tier 2 (tactical) parameters"""
        return {
            name: spec for name, spec in self.PARAMETER_SPECS.items()
            if spec.tier == ParameterTier.TIER_2_TACTICAL
        }

    def get_tier_3_params(self) -> Dict[str, ParameterSpec]:
        """Get all Tier 3 (advanced) parameters"""
        return {
            name: spec for name, spec in self.PARAMETER_SPECS.items()
            if spec.tier == ParameterTier.TIER_3_ADVANCED
        }

    def get_spec(self, param_name: str) -> Optional[ParameterSpec]:
        """Get specification for one parameter"""
        return self.PARAMETER_SPECS.get(param_name)

    def list_all_specs(self) -> Dict[str, ParameterSpec]:
        """Get all 33 parameter specifications"""
        return self.PARAMETER_SPECS.copy()


# ============================================================================
# PARAMETER RANGE VALIDATOR
# ============================================================================

class ParameterRangeValidator:
    """
    Validates parameters against bounds and type requirements.
    Ensures all parameters stay within safe operating range.
    """

    def __init__(self):
        self.classifier = ParameterTierClassifier()
        self.logger = logging.getLogger("ParameterRangeValidator")

    def validate_parameter(self, name: str, value: float) -> Tuple[bool, Optional[str]]:
        """Validate single parameter"""
        spec = self.classifier.get_spec(name)
        if not spec:
            return False, f"Unknown parameter: {name}"

        # Type check
        if spec.param_type == 'int' and not isinstance(value, int):
            try:
                value = int(value)
            except:
                return False, f"{name} must be integer, got {type(value)}"

        # Range check
        return spec.validate(value)

    def validate_parameter_set(self, params: Dict) -> Tuple[bool, List[str]]:
        """Validate entire parameter set"""
        errors = []

        # Check that all required parameters are present
        all_specs = self.classifier.list_all_specs()
        for param_name in all_specs.keys():
            if param_name not in params:
                errors.append(f"Missing parameter: {param_name}")

        # Validate each parameter
        for param_name, value in params.items():
            is_valid, error_msg = self.validate_parameter(param_name, value)
            if not is_valid:
                errors.append(error_msg)

        # Chart weights must sum to ~1.0 (with small tolerance)
        weights = (
            params.get('vwap_weight', 0) +
            params.get('confirmation_2bar_weight', 0) +
            params.get('momentum_weight', 0) +
            params.get('volatility_weight', 0)
        )
        if abs(weights - 1.0) > 0.01:
            errors.append(f"Chart weights sum to {weights:.4f}, must be ~1.0")

        return len(errors) == 0, errors

    def sanitize_params(self, params: Dict) -> Dict:
        """Clip parameters to valid ranges"""
        sanitized = {}
        classifier = self.classifier

        for param_name, value in params.items():
            spec = classifier.get_spec(param_name)
            if spec:
                # Clip to bounds
                clipped = max(spec.min_value, min(value, spec.max_value))
                sanitized[param_name] = clipped
            else:
                sanitized[param_name] = value

        return sanitized


# ============================================================================
# PARAMETER SAMPLER - Random & Intelligent Sampling
# ============================================================================

class ParameterSampler:
    """
    Samples parameters intelligently for calibration.
    Supports random sampling (Phase 1) and guided sampling (Phase 2/3).
    """

    def __init__(self):
        self.classifier = ParameterTierClassifier()
        self.validator = ParameterRangeValidator()
        self.logger = logging.getLogger("ParameterSampler")

    def sample_random(self) -> Dict:
        """Generate random parameter set (Phase 1)"""
        params = {}
        specs = self.classifier.list_all_specs()

        for param_name, spec in specs.items():
            if spec.param_type == 'int':
                # Random integer within range
                params[param_name] = np.random.randint(
                    int(spec.min_value),
                    int(spec.max_value) + 1
                )
            else:
                # Random float within range
                params[param_name] = np.random.uniform(
                    spec.min_value,
                    spec.max_value
                )

        # Normalize chart weights to sum to 1.0
        params = self._normalize_chart_weights(params)

        return params

    def sample_around_best(self, best_params: Dict, deviation: float = 0.1) -> Dict:
        """
        Sample parameters near best found so far (Phase 2/3).
        deviation: How far to deviate (0.0-1.0 of parameter range)
        """
        params = {}
        specs = self.classifier.list_all_specs()

        for param_name, spec in specs.items():
            best_value = best_params.get(param_name, spec.default)

            if spec.param_type == 'int':
                # Integer with noise
                range_width = int(spec.max_value) - int(spec.min_value)
                noise = int(range_width * deviation * np.random.uniform(-1, 1))
                new_value = best_value + noise
                params[param_name] = int(np.clip(
                    new_value,
                    spec.min_value,
                    spec.max_value
                ))
            else:
                # Float with noise
                range_width = spec.max_value - spec.min_value
                noise = range_width * deviation * np.random.uniform(-1, 1)
                new_value = best_value + noise
                params[param_name] = np.clip(
                    new_value,
                    spec.min_value,
                    spec.max_value
                )

        # Normalize chart weights
        params = self._normalize_chart_weights(params)

        return params

    def _normalize_chart_weights(self, params: Dict) -> Dict:
        """Ensure chart weights sum to 1.0"""
        weights = (
            params.get('vwap_weight', 0.25) +
            params.get('confirmation_2bar_weight', 0.25) +
            params.get('momentum_weight', 0.25) +
            params.get('volatility_weight', 0.25)
        )

        if weights > 0:
            params['vwap_weight'] /= weights
            params['confirmation_2bar_weight'] /= weights
            params['momentum_weight'] /= weights
            params['volatility_weight'] /= weights

        return params


# ============================================================================
# 28-PARAMETER INJECTION MANAGER (Live Strategy Parameters Only)
# ============================================================================

class ParameterInjectionManager:
    """
    Injects parameters safely into ECS system.
    Ensures atomic updates without race conditions.
    """

    def __init__(self, ecs_system):
        self.ecs_system = ecs_system
        self.validator = ParameterRangeValidator()
        self.logger = logging.getLogger("ParameterInjectionManager")
        self._lock = threading.RLock()

    def inject_parameters(self, params: Dict) -> Tuple[bool, Optional[str]]:
        """
        Inject parameters into ECS system.
        Validates before injection.
        """
        # Validate
        is_valid, errors = self.validator.validate_parameter_set(params)
        if not is_valid:
            error_msg = "; ".join(errors)
            self.logger.error(f"[FAIL] Validation: {error_msg}")
            return False, error_msg

        # Inject atomically
        with self._lock:
            try:
                # This would call into actual ECS system
                if self.ecs_system:
                    self.ecs_system.set_parameters(params)
                self.logger.info(f"[OK] Injected 33 parameters")
                return True, None
            except Exception as e:
                self.logger.error(f"[FAIL] Injection error: {str(e)}")
                return False, str(e)

    def get_current_parameters(self) -> Dict:
        """Get currently active parameters from ECS"""
        with self._lock:
            if self.ecs_system:
                return self.ecs_system.get_parameters()
            return {}


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test parameter tier classifier
    print("\n=== PARAMETER TIER CLASSIFIER ===")
    classifier = ParameterTierClassifier()
    print(f"Tier 1 (Critical): {len(classifier.get_tier_1_params())} params")
    print(f"Tier 2 (Tactical): {len(classifier.get_tier_2_params())} params")
    print(f"Tier 3 (Advanced): {len(classifier.get_tier_3_params())} params")
    print(f"Total: {len(classifier.list_all_specs())} params")

    # Test parameter sampler
    print("\n=== PARAMETER SAMPLER ===")
    sampler = ParameterSampler()
    random_params = sampler.sample_random()
    print(f"Generated random params: {len(random_params)} params")

    # Test parameter validator
    print("\n=== PARAMETER VALIDATOR ===")
    validator = ParameterRangeValidator()
    is_valid, errors = validator.validate_parameter_set(random_params)
    print(f"Validation result: {is_valid}")
    if errors:
        print(f"Errors: {errors}")

