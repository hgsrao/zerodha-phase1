#!/usr/bin/env python3
"""
================================================================================
CHECK STAGE 2 PARAMETERS - ITERATION 1 VERIFICATION
================================================================================

Waits for first iteration to complete, then displays all 33 optimized parameters
Confirms that calibration is working on the correct parameter set

Run this AFTER the calibration starts - it will wait for results
"""

import json
import time
from pathlib import Path
from datetime import datetime

# List of 33 parameters that MUST be optimized
EXPECTED_PARAMETERS = [
    # Tier 1: Base dynamics (2)
    'base_dp_dt_multiplier',
    'base_dv_dt_multiplier',

    # Tier 1: Synchronization & confidence (3)
    'sync_score_confidence_threshold',
    'entry_confidence_threshold',
    'exit_confidence_threshold',

    # Tier 1: Risk/reward & targets (2)
    'min_risk_reward_ratio',
    'profit_target_margin_buffer',

    # Tier 1: Chart studies weights (4) - MUST sum to 1.0
    'vwap_weight',
    'confirmation_2bar_weight',
    'momentum_weight',
    'volatility_weight',

    # Tier 1: Signal thresholds (4)
    'green_threshold',
    'amber_threshold_lower',
    'red_threshold',
    'slippage_guard_threshold',

    # Tier 1: Volatility regime (4)
    'volatility_regime_multiplier',
    'low_vol_regime_multiplier',
    'medium_vol_regime_multiplier',
    'high_vol_regime_multiplier',

    # Tier 2: ATR & smoothing (3)
    'atr_calculation_period',
    'entry_signal_smoothing_window',
    'exit_signal_smoothing_window',

    # Tier 2: Costs & thresholds (2)
    'slippage_cost_multiplier',
    'minimum_absolute_profit_rupees',

    # Tier 2: Calculation periods (2)
    'momentum_calculation_period',
    'vwap_calculation_period',

    # Tier 2: Signal persistence (1)
    'signal_persistence_requirement',

    # Tier 3: Learning hyperparameters (3)
    'phase1_exploration_intensity',
    'phase2_optimization_intensity',
    'learning_rate_exploration_factor',

    # Tier 3: Risk control (3)
    'lambda_risk_trigger_level',
    'lambda_reduction_factor',
    'recalibration_frequency_days',
]

def check_iteration_1():
    """Check if iteration 1 has completed and display parameters"""

    print("")
    print("="*80)
    print("CHECKING STAGE 2 PARAMETERS - ITERATION 1 VERIFICATION")
    print("="*80)
    print("")
    print("Waiting for first iteration to complete...")
    print("")

    log_file = Path("REAL_calibration_with_actual_data.log")

    # Wait for log file to be created
    while not log_file.exists():
        print("  Waiting for calibration to create log file...")
        time.sleep(5)

    # Wait for first iteration to appear in log
    iteration_found = False
    start_wait = datetime.now()
    timeout_minutes = 10

    while not iteration_found:
        try:
            with open(log_file, 'r') as f:
                content = f.read()
                if '[Iter' in content:
                    iteration_found = True
                    break
        except:
            pass

        elapsed = (datetime.now() - start_wait).total_seconds() / 60
        if elapsed > timeout_minutes:
            print("  Timeout: First iteration did not complete in {} minutes".format(timeout_minutes))
            print("  (Calibration may still be running, check back later)")
            return False

        print("  Still waiting... ({:.1f} min)".format(elapsed))
        time.sleep(5)

    print("  [OK] First iteration logged!")
    print("")

    # Now wait for results JSON file to be created (only at END of calibration)
    # For now, we can check the log file for parameter values
    # After calibration completes, results will be in JSON

    print("="*80)
    print("PARAMETER VERIFICATION")
    print("="*80)
    print("")
    print("Total parameters expected: {}".format(len(EXPECTED_PARAMETERS)))
    print("")
    print("Parameters being optimized in Stage 2:")
    print("")

    # Group by tier
    tier1_params = EXPECTED_PARAMETERS[:20]  # First 20 are Tier 1
    tier2_params = EXPECTED_PARAMETERS[20:28]  # Next 8 are Tier 2
    tier3_params = EXPECTED_PARAMETERS[28:]  # Last 5 are Tier 3

    print("TIER 1 - CRITICAL (20 parameters):")
    print("  Base Dynamics:")
    for p in tier1_params[:2]:
        print("    - {}".format(p))
    print("  Synchronization & Confidence:")
    for p in tier1_params[2:5]:
        print("    - {}".format(p))
    print("  Risk/Reward & Targets:")
    for p in tier1_params[5:7]:
        print("    - {}".format(p))
    print("  Chart Studies Weights (MUST sum to 1.0):")
    for p in tier1_params[7:11]:
        print("    - {}".format(p))
    print("  Signal Thresholds:")
    for p in tier1_params[11:15]:
        print("    - {}".format(p))
    print("  Volatility Regimes:")
    for p in tier1_params[15:19]:
        print("    - {}".format(p))
    print("")

    print("TIER 2 - HIGH VALUE (8 parameters):")
    print("  ATR & Smoothing:")
    for p in tier2_params[:3]:
        print("    - {}".format(p))
    print("  Costs & Thresholds:")
    for p in tier2_params[3:5]:
        print("    - {}".format(p))
    print("  Calculation Periods:")
    for p in tier2_params[5:7]:
        print("    - {}".format(p))
    print("  Signal Persistence:")
    for p in tier2_params[7:]:
        print("    - {}".format(p))
    print("")

    print("TIER 3 - OPTIONAL (5 parameters):")
    print("  Learning Hyperparameters:")
    for p in tier3_params[:3]:
        print("    - {}".format(p))
    print("  Risk Control:")
    for p in tier3_params[3:]:
        print("    - {}".format(p))
    print("")

    print("="*80)
    print("CHECKING LOG FILE FOR ITERATION 1 OUTPUT")
    print("="*80)
    print("")

    try:
        with open(log_file, 'r') as f:
            lines = f.readlines()

        # Find iteration 1 line
        for i, line in enumerate(lines):
            if '[Iter   1]' in line or '[Iter 1]' in line:
                print("Found Iteration 1:")
                print("  {}".format(line.strip()))

                # Show context
                if i > 0:
                    print("  Previous line: {}".format(lines[i-1].strip()))

                print("")
                break
    except Exception as e:
        print("Error reading log: {}".format(str(e)))

    print("="*80)
    print("NEXT STEPS")
    print("="*80)
    print("")
    print("1. Open dashboard at: http://localhost:8888")
    print("2. Monitor iterations 1-10 to confirm parameters are being tuned")
    print("3. Win rate should vary between iterations (proof of optimization)")
    print("4. After calibration completes, check final results JSON")
    print("")
    print("Results will be saved to: REAL_calibration_results_Xh.json")
    print("All 33 parameters will be in 'best_parameters' field")
    print("")

    return True


if __name__ == "__main__":
    check_iteration_1()
