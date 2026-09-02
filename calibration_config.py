#!/usr/bin/env python3
"""
================================================================================
CALIBRATION CONFIGURATION
================================================================================

Configuration parameters for the offline calibration system (R0).
These are NOT deployed to live trading (R1).
These control HOW the optimizer searches, not WHAT the system trades.

IMPORTANT: These parameters should NEVER appear in live R1 configuration.

================================================================================
"""

# ============================================================================
# OPTIMIZER CONTROL PARAMETERS (Calibration Phase Controls)
# ============================================================================

class CalibrationConfig:
    """
    Controls for the 3-phase calibration optimization.
    Extracted from original Tier 3 to prevent accidental inclusion in live config.
    """

    # PHASE 1: EXPLORATION (Random Search)
    phase1_exploration_intensity = 50  # Randomness level (30-100)
    phase1_duration_hours = 8
    phase1_target_iterations = 100

    # PHASE 2: BAYESIAN OPTIMIZATION (Guided Search)
    phase2_optimization_intensity = 250  # Optimization iterations (100-500)
    phase2_duration_hours = 10
    phase2_target_iterations = 200

    # PHASE 3: FINE-TUNING (Convergence)
    phase3_fine_tuning_intensity = 100  # Fine-tuning iterations (50-200)
    phase3_duration_hours = 6
    phase3_target_iterations = 100

    # META-LEARNING CONTROL
    learning_rate_exploration_factor = 0.05  # Learning rate (0.01-0.1)

    # TOTAL CALIBRATION BUDGET
    total_calibration_hours = phase1_duration_hours + phase2_duration_hours + phase3_duration_hours
    total_calibration_iterations = (
        phase1_target_iterations +
        phase2_target_iterations +
        phase3_target_iterations
    )

    # CONVERGENCE CRITERIA
    convergence_threshold_improvement_percent = 1.0  # 1% improvement minimum
    convergence_window_iterations = 50  # Last N iterations to check
    max_stagnation_iterations = 100  # No improvement for N iterations

    # RANDOMIZATION
    random_seed = 42  # For reproducibility

    # OUTPUT
    save_iteration_results = True
    save_checkpoint_every_iterations = 10
    save_best_parameters = True


# ============================================================================
# CALIBRATION STRATEGY PARAMETERS
# ============================================================================

class CalibrationStrategy:
    """Which parameters should be calibrated vs. fixed"""

    # TIER 1 STRATEGY PARAMETERS (ALWAYS CALIBRATE)
    calibrate_tier_1 = True  # All 20 entry/exit signal params

    # TIER 2 TACTICAL PARAMETERS (ALWAYS CALIBRATE)
    calibrate_tier_2 = True  # All 8 signal tuning params

    # POSITION MANAGEMENT (OPTIONAL, PHASE 2+)
    calibrate_position_management = False  # Not in Phase 1

    # PID CONTROL (OPTIONAL, PHASE 3+)
    calibrate_pid_gains = False  # Not in Phase 1

    # OPERATIONAL PARAMETERS (NEVER CALIBRATE)
    calibrate_operational = False  # Risk gates, execution, operational

    # TOTAL PARAMETERS TO CALIBRATE IN PHASE 1
    total_phase_1_calibration_params = 28  # Tier 1 (20) + Tier 2 (8)


class Revision2ParameterManifest:
    @staticmethod
    def base_33():
        return [
            "base_dp_dt_multiplier",
            "base_dv_dt_multiplier",
            "entry_confidence_threshold",
            "exit_confidence_threshold",
            "min_risk_reward_ratio",
            "profit_target_margin_buffer",
            "vwap_weight",
            "confirmation_2bar_weight",
            "momentum_weight",
            "volatility_weight",
            "green_threshold",
            "amber_threshold_lower",
            "red_threshold",
            "slippage_guard_threshold",
            "volatility_regime_multiplier",
            "low_vol_regime_multiplier",
            "medium_vol_regime_multiplier",
            "high_vol_regime_multiplier",
            "profit_target_atr_mult",
            "stop_loss_atr_mult",
            "atr_calculation_period",
            "entry_signal_smoothing_window",
            "exit_signal_smoothing_window",
            "slippage_cost_multiplier",
            "minimum_absolute_profit_rupees",
            "momentum_calculation_period",
            "vwap_calculation_period",
            "signal_persistence_requirement",
            "min_hold_bars",
            "max_hold_bars",
            "phase1_exploration_intensity",
            "phase2_optimization_intensity",
            "learning_rate_exploration_factor",
        ]

    @staticmethod
    def revision2_35():
        return [
            "lot_size_by_symbol",
            "max_positions_live",
            "max_positions_per_symbol",
            "capital_per_trade_fraction",
            "min_capital_buffer_fraction",
            "capital_allocation_mode",
            "rebalance_frequency_minutes",
            "drawdown_normal_threshold",
            "drawdown_derated_threshold",
            "drawdown_halt_threshold",
            "max_loss_per_trade_rupees",
            "max_loss_per_day_rupees",
            "portfolio_lambda_risk_limit",
            "max_sector_exposure_fraction",
            "max_symbol_concentration",
            "pid_kp_entry",
            "pid_ki_entry",
            "pid_kd_entry",
            "pid_kp_exit",
            "pid_ki_exit",
            "pid_kd_exit",
            "pid_integral_window_bars",
            "pid_integral_max_clamp",
            "pid_derivative_smoothing",
            "order_type",
            "limit_order_offset_percent",
            "order_timeout_seconds",
            "max_retry_attempts",
            "retry_delay_seconds",
            "slippage_tolerance_percent",
            "trading_hours_start",
            "trading_hours_end",
            "symbols_to_trade",
            "exclude_symbols",
            "data_validation_mode",
        ]

    @staticmethod
    def hardcoded_20():
        return [
            "kill_switch_enabled",
            "safety_drawdown_halt_threshold",
            "max_daily_loss_rupees",
            "max_concurrent_positions",
            "max_gross_exposure_fraction",
            "max_market_data_age_seconds",
            "max_exposure_per_symbol_fraction",
            "min_position_quantity",
            "max_position_quantity",
            "drawdown_derate_threshold",
            "drawdown_derate_multiplier",
            "lambda_derate_threshold",
            "lambda_derate_multiplier",
            "min_signal_confidence",
            "safety_min_risk_reward_ratio",
            "order_dedup_window_seconds",
            "order_timeout_seconds_execution",
            "max_reconciliation_qty_diff",
            "max_slippage_fraction",
            "no_entry_cutoff_time",
        ]

    @staticmethod
    def all_68():
        return Revision2ParameterManifest.base_33() + Revision2ParameterManifest.revision2_35()

    @staticmethod
    def black_boxes():
        return [
            "StartupCapabilityLock",
            "DataIngestion",
            "L2DataCertifier",
            "PA",
            "ID",
            "MPC",
            "SafetyGates",
            "PositionManager",
            "P01D",
            "UnifiedExecution",
        ]

    def calibratable_45(self):
        from canonical_parameter_registry import CanonicalParameterRegistry
        return CanonicalParameterRegistry().calibratable_names()[:45]


# ============================================================================
# IMPORTANT: NOT IN THIS CONFIG
# ============================================================================

# These are RISK GATES - they should be hard-coded, never calibrated:
# - portfolio_risk_derate_trigger = 0.15
# - portfolio_derated_size_multiplier = 0.80
# - drawdown_derate_threshold = 0.18
# - drawdown_halt_threshold = 0.25
# - max_daily_loss_rupees = 50000
# See: safety_gates_config.py

