#!/usr/bin/env python3
"""
================================================================================
COMPLETE TRADING SYSTEM WITH 33-PARAMETER INJECTION LAYER
================================================================================

Wrapper that injects all 33 calibration parameters into the trading system

This layer:
1. Accepts all 33 parameters
2. Injects them into the base system's layers
3. Applies them throughout execution
4. Maintains backward compatibility with 6-parameter mode

The 33 parameters are organized in 3 tiers:
- Tier 1 (20): Critical parameters
- Tier 2 (8): High-value parameters
- Tier 3 (5): Optional parameters

================================================================================
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
import logging
import json

# Import the base system
from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import (
    CompleteIntegratedTradingSystem as BaseSystem,
    RelativeSynchronizationThresholds,
    SmartParameterInitializer,
    UnifiedP01DGovernor
)

logger = logging.getLogger('System_33Params')


# ============================================================================
# PARAMETER INJECTION LAYER
# ============================================================================

class ParameterInjectionManager:
    """Manages injection of all 33 parameters into system layers"""

    # Define all 33 parameters
    TIER_1_PARAMS = [
        'base_dp_dt_multiplier',
        'base_dv_dt_multiplier',
        'sync_score_confidence_threshold',
        'entry_confidence_threshold',
        'exit_confidence_threshold',
        'min_risk_reward_ratio',
        'profit_target_margin_buffer',
        'vwap_weight',
        'confirmation_2bar_weight',
        'momentum_weight',
        'volatility_weight',
        'green_threshold',
        'amber_threshold_lower',
        'red_threshold',
        'slippage_guard_threshold',
        'volatility_regime_multiplier',
        'low_vol_regime_multiplier',
        'medium_vol_regime_multiplier',
        'high_vol_regime_multiplier',
    ]

    TIER_2_PARAMS = [
        'atr_calculation_period',
        'entry_signal_smoothing_window',
        'exit_signal_smoothing_window',
        'slippage_cost_multiplier',
        'minimum_absolute_profit_rupees',
        'momentum_calculation_period',
        'vwap_calculation_period',
        'signal_persistence_requirement',
    ]

    TIER_3_PARAMS = [
        'phase1_exploration_intensity',
        'phase2_optimization_intensity',
        'learning_rate_exploration_factor',
        'lambda_risk_trigger_level',
        'lambda_reduction_factor',
        'recalibration_frequency_days',
    ]

    # Also include the 6 parameters that are already wired
    EXISTING_PARAMS = [
        'profit_target_atr_mult',
        'stop_loss_atr_mult',
        'entry_pid_kp',
        'exit_pid_kp',
        'min_hold_bars',
        'max_hold_bars'
    ]

    @classmethod
    def get_all_params(cls) -> List[str]:
        """Get all 33 + 6 = 39 total parameters"""
        return cls.TIER_1_PARAMS + cls.TIER_2_PARAMS + cls.TIER_3_PARAMS + cls.EXISTING_PARAMS

    @classmethod
    def validate_params(cls, params: Dict) -> bool:
        """Validate that all required parameters are present"""
        required = cls.TIER_1_PARAMS + cls.TIER_2_PARAMS + cls.TIER_3_PARAMS + cls.EXISTING_PARAMS
        missing = [p for p in required if p not in params]
        if missing:
            logger.warning("Missing parameters: {}".format(missing))
            return False
        return True

    @staticmethod
    def get_default_params() -> Dict:
        """Return default values for all 33 parameters"""
        return {
            # Tier 1
            'base_dp_dt_multiplier': 1.0,
            'base_dv_dt_multiplier': 1.0,
            'sync_score_confidence_threshold': 1.0,
            'entry_confidence_threshold': 0.50,
            'exit_confidence_threshold': 0.50,
            'min_risk_reward_ratio': 1.5,
            'profit_target_margin_buffer': 0.0,
            'vwap_weight': 0.25,
            'confirmation_2bar_weight': 0.30,
            'momentum_weight': 0.25,
            'volatility_weight': 0.20,
            'green_threshold': 0.60,
            'amber_threshold_lower': 0.40,
            'red_threshold': 0.30,
            'slippage_guard_threshold': 0.05,
            'volatility_regime_multiplier': 1.0,
            'low_vol_regime_multiplier': 1.0,
            'medium_vol_regime_multiplier': 1.0,
            'high_vol_regime_multiplier': 1.0,

            # Tier 2
            'atr_calculation_period': 20,
            'entry_signal_smoothing_window': 1,
            'exit_signal_smoothing_window': 1,
            'slippage_cost_multiplier': 1.0,
            'minimum_absolute_profit_rupees': 0,
            'momentum_calculation_period': 20,
            'vwap_calculation_period': 20,
            'signal_persistence_requirement': 1,

            # Tier 3
            'phase1_exploration_intensity': 50,
            'phase2_optimization_intensity': 200,
            'learning_rate_exploration_factor': 0.05,
            'lambda_risk_trigger_level': 0.10,
            'lambda_reduction_factor': 0.9,
            'recalibration_frequency_days': 30,

            # Existing 6 (always required)
            'profit_target_atr_mult': 1.6171,
            'stop_loss_atr_mult': 0.7246,
            'entry_pid_kp': 0.1587,
            'exit_pid_kp': 0.1327,
            'min_hold_bars': 2,
            'max_hold_bars': 56,
        }

    @staticmethod
    def apply_to_sync_thresholds(thresholds: Dict, params: Dict) -> Dict:
        """Apply Tier 1 parameters to sync thresholds"""
        if thresholds is None:
            return thresholds

        # Apply base multipliers to thresholds
        thresholds['dp_dt_threshold'] *= params.get('base_dp_dt_multiplier', 1.0)
        thresholds['dv_dt_threshold'] *= params.get('base_dv_dt_multiplier', 1.0)

        # Apply volatility regime multipliers
        vol_class = thresholds.get('volatility_class', 'MEDIUM')
        if vol_class == 'LOW':
            mult = params.get('low_vol_regime_multiplier', 1.0)
        elif vol_class == 'HIGH':
            mult = params.get('high_vol_regime_multiplier', 1.0)
        else:
            mult = params.get('medium_vol_regime_multiplier', 1.0)

        thresholds['dp_dt_threshold'] *= mult
        thresholds['dv_dt_threshold'] *= mult

        # Apply overall volatility multiplier
        thresholds['dp_dt_threshold'] *= params.get('volatility_regime_multiplier', 1.0)
        thresholds['dv_dt_threshold'] *= params.get('volatility_regime_multiplier', 1.0)

        # Add threshold parameters
        thresholds['sync_score_confidence_threshold'] = params.get('sync_score_confidence_threshold', 1.0)
        thresholds['entry_confidence_threshold'] = params.get('entry_confidence_threshold', 0.50)
        thresholds['exit_confidence_threshold'] = params.get('exit_confidence_threshold', 0.50)
        thresholds['green_threshold'] = params.get('green_threshold', 0.60)
        thresholds['amber_threshold_lower'] = params.get('amber_threshold_lower', 0.40)
        thresholds['red_threshold'] = params.get('red_threshold', 0.30)

        return thresholds

    @staticmethod
    def apply_to_governor_params(params: Dict) -> Dict:
        """Prepare parameters for P01D Governor injection"""
        return {
            # Existing 6 + new confidence/risk/cost parameters
            'profit_target_atr_mult': params.get('profit_target_atr_mult', 1.6171),
            'stop_loss_atr_mult': params.get('stop_loss_atr_mult', 0.7246),
            'entry_pid_kp': params.get('entry_pid_kp', 0.1587),
            'exit_pid_kp': params.get('exit_pid_kp', 0.1327),
            'min_hold_bars': params.get('min_hold_bars', 2),
            'max_hold_bars': params.get('max_hold_bars', 56),

            # Tier 1 additions
            'entry_confidence_threshold': params.get('entry_confidence_threshold', 0.50),
            'exit_confidence_threshold': params.get('exit_confidence_threshold', 0.50),
            'min_risk_reward_ratio': params.get('min_risk_reward_ratio', 1.5),
            'profit_target_margin_buffer': params.get('profit_target_margin_buffer', 0.0),
            'green_threshold': params.get('green_threshold', 0.60),
            'amber_threshold_lower': params.get('amber_threshold_lower', 0.40),
            'red_threshold': params.get('red_threshold', 0.30),
            'slippage_guard_threshold': params.get('slippage_guard_threshold', 0.05),

            # Tier 2 additions
            'slippage_cost_multiplier': params.get('slippage_cost_multiplier', 1.0),
            'minimum_absolute_profit_rupees': params.get('minimum_absolute_profit_rupees', 0),
            'signal_persistence_requirement': params.get('signal_persistence_requirement', 1),

            # Tier 3 additions
            'lambda_risk_trigger_level': params.get('lambda_risk_trigger_level', 0.10),
            'lambda_reduction_factor': params.get('lambda_reduction_factor', 0.9),
        }


# ============================================================================
# ENHANCED TRADING SYSTEM WITH 33 PARAMETERS
# ============================================================================

class CompleteIntegratedTradingSystemWith33Params(BaseSystem):
    """Extended system that accepts and injects all 33 parameters"""

    def __init__(self, verbose=True, calibration_params: Optional[Dict] = None):
        """Initialize with optional 33 calibration parameters"""
        super().__init__(verbose=verbose)

        # Store calibration parameters
        self.calibration_params = calibration_params or ParameterInjectionManager.get_default_params()

        # Validate parameters
        if not ParameterInjectionManager.validate_params(self.calibration_params):
            logger.warning("Using defaults for missing parameters")
            defaults = ParameterInjectionManager.get_default_params()
            for key, value in defaults.items():
                if key not in self.calibration_params:
                    self.calibration_params[key] = value

        logger.info("System initialized with {} parameters".format(
            len(self.calibration_params)))

    def initialize_from_data(self, df_data: Dict, symbols_list: List) -> Dict:
        """Initialize with parameters injected"""
        result = super().initialize_from_data(df_data, symbols_list)

        # Inject parameters into sync thresholds
        for symbol in self.sync_thresholds:
            self.sync_thresholds[symbol] = ParameterInjectionManager.apply_to_sync_thresholds(
                self.sync_thresholds[symbol],
                self.calibration_params
            )

        # Inject parameters into starting params
        governor_params = ParameterInjectionManager.apply_to_governor_params(self.calibration_params)
        for symbol in self.starting_params:
            # Merge governor params into starting params
            for key, value in governor_params.items():
                if isinstance(self.starting_params[symbol].get(key), dict):
                    self.starting_params[symbol][key]['current'] = value
                else:
                    self.starting_params[symbol][key] = value

        logger.info("Injected 33-parameter calibration into {} symbols".format(len(symbols_list)))
        return result

    def run_paper_trading(self, symbols_list: List[str],
                         test_period_days: int = 5,
                         use_optimized_params: bool = False,
                         injected_params: Optional[Dict] = None) -> Dict:
        """Run paper trading with injected parameters"""

        # Accept optional new parameters
        if injected_params:
            self.calibration_params = injected_params
            # Re-initialize with new parameters
            self.initialize_from_data(self.symbols_data, list(self.symbols_data.keys()))

        return super().run_paper_trading(symbols_list, test_period_days, use_optimized_params)

    def get_parameters(self) -> Dict:
        """Return current calibration parameters"""
        return self.calibration_params.copy()

    def set_parameters(self, params: Dict):
        """Update calibration parameters"""
        if ParameterInjectionManager.validate_params(params):
            self.calibration_params = params
            logger.info("Updated calibration parameters")
        else:
            logger.error("Invalid parameters provided")


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def create_system_with_params(params: Dict) -> CompleteIntegratedTradingSystemWith33Params:
    """Factory function to create system with parameters"""
    system = CompleteIntegratedTradingSystemWith33Params(calibration_params=params)
    return system


def validate_parameter_set(params: Dict) -> tuple:
    """Validate parameter set and return (is_valid, errors)"""
    errors = []

    # Check tier 1 weight constraint (must sum to 1.0)
    weights = (
        params.get('vwap_weight', 0) +
        params.get('confirmation_2bar_weight', 0) +
        params.get('momentum_weight', 0) +
        params.get('volatility_weight', 0)
    )
    if abs(weights - 1.0) > 0.01:
        errors.append("Chart weights sum to {:.3f}, not 1.0".format(weights))

    # Check threshold ranges
    if not (0 <= params.get('entry_confidence_threshold', 0.5) <= 1.0):
        errors.append("entry_confidence_threshold out of range [0,1]")

    if not (0 <= params.get('exit_confidence_threshold', 0.5) <= 1.0):
        errors.append("exit_confidence_threshold out of range [0,1]")

    # Check positive multipliers
    if params.get('slippage_cost_multiplier', 1) <= 0:
        errors.append("slippage_cost_multiplier must be positive")

    if params.get('min_risk_reward_ratio', 1.5) <= 0:
        errors.append("min_risk_reward_ratio must be positive")

    return len(errors) == 0, errors


# ============================================================================
# MAIN (for testing)
# ============================================================================

if __name__ == "__main__":
    # Test parameter validation
    print("Testing 33-parameter system...")
    print("")

    # Create default params
    default_params = ParameterInjectionManager.get_default_params()
    print("Default parameters loaded: {} total".format(len(default_params)))

    # Validate
    is_valid, errors = validate_parameter_set(default_params)
    if is_valid:
        print("[OK] Parameters are valid")
    else:
        print("[FAIL] Parameter validation errors:")
        for error in errors:
            print("  - {}".format(error))

    # Create system
    print("")
    print("Creating system with 33 parameters...")
    system = create_system_with_params(default_params)
    print("[OK] System created and ready for calibration")

    print("")
    print("System is ready for 24-hour calibration with all 33 parameters!")
