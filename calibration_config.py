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

