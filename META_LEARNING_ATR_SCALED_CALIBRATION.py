#!/usr/bin/env python3
"""
META-LEARNING RECALIBRATION: ATR-SCALED PARAMETERS
Optimize ATR multipliers instead of fixed rupee amounts

Key difference:
  OLD: optimize profit_target ∈ [0.25, 3.00] rupees (fixed)
  NEW: optimize profit_target_atr_mult ∈ [0.20, 1.50] (multiplier of ATR)

This ensures parameters scale with volatility automatically.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')

print("\n" + "="*100)
print("META-LEARNING RECALIBRATION: ATR-SCALED PARAMETERS")
print("="*100)
print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()
print("DESIGN CHANGE:")
print("─" * 100)
print("OLD: profit_target = fixed rupees (0.25-3.00 ₹)")
print("     → Fails at high prices (costs scale with price)")
print()
print("NEW: profit_target = atr × multiplier")
print("     → Works at all prices (both scale with volatility)")
print()
print("LEARNABLE PARAMETERS:")
print("─" * 100)
print()

# Optimizable ATR multipliers (instead of fixed rupee amounts)
OPTIMIZABLE_PARAMS = {
    'profit_target_atr_mult': {
        'description': 'Multiplier: profit_target = ATR × this',
        'range': [0.20, 1.50],
        'reason': 'Scales profit target with volatility (was 0.25-3.00 fixed rupees)'
    },
    'stop_loss_atr_mult': {
        'description': 'Multiplier: stop_loss = -ATR × this',
        'range': [0.50, 2.00],
        'reason': 'Scales stop loss with volatility'
    },
    'entry_pid_kp': {
        'description': 'Entry PID controller gain',
        'range': [0.05, 0.25],
        'reason': 'Unchanged from before'
    },
    'exit_pid_kp': {
        'description': 'Exit PID controller gain',
        'range': [0.05, 0.25],
        'reason': 'Unchanged from before'
    },
    'min_hold_bars': {
        'description': 'Minimum hold time (bars)',
        'range': [1, 5],
        'reason': 'Unchanged from before'
    },
    'max_hold_bars': {
        'description': 'Maximum hold time (bars)',
        'range': [10, 120],
        'reason': 'Unchanged from before'
    }
}

for param_name, config in OPTIMIZABLE_PARAMS.items():
    print(f"{param_name}:")
    print(f"  Range: {config['range']}")
    print(f"  {config['description']}")
    print(f"  Why: {config['reason']}")
    print()

print("="*100)
print()
print("EXPECTED IMPROVEMENTS:")
print("─" * 100)
print()
print("Problem with OLD calibration:")
print("  ✗ Fixed profit_target (₹1.25) only works at prices < ₹1255")
print("  ✗ INFY range ₹985-1960 → 52% of data blocked")
print("  ✗ Validation shows 0 trades (Bridge rejects all)")
print()
print("Expected with NEW calibration:")
print("  ✓ Multiplier-based profit_target scales with volatility")
print("  ✓ ATR at ₹1450: profit_target = 3.66 × 0.50 = ₹1.83 > costs ₹1.45")
print("  ✓ Works across all price ranges")
print("  ✓ Validation should show 50%+ win rate")
print()
print("="*100)
print()
print("NEXT STEPS:")
print("─" * 100)
print()
print("1. Integrate ATR-scaled parameters into COMPLETE_TRADING_SYSTEM")
print("2. Update meta-learning to optimize multipliers (not fixed rupees)")
print("3. Run 500-iteration calibration (6-12 hours)")
print("4. Validate with ATR-scaled parameters")
print("5. Compare: old (0 trades) vs new (expected 50%+ win rate)")
print()
print("="*100 + "\n")

# ============================================================================
# PARAMETER RANGES FOR META-LEARNING
# ============================================================================

PARAM_RANGES_ATR_SCALED = {
    'profit_target_atr_mult': {
        'current': 0.50,
        'min': 0.20,
        'max': 1.50,
        'step': 0.05,
        'type': 'multiplier'
    },
    'stop_loss_atr_mult': {
        'current': 1.00,
        'min': 0.50,
        'max': 2.00,
        'step': 0.10,
        'type': 'multiplier'
    },
    'entry_pid_kp': {
        'current': 0.15,
        'min': 0.05,
        'max': 0.25,
        'step': 0.02,
        'type': 'fixed'
    },
    'exit_pid_kp': {
        'current': 0.13,
        'min': 0.05,
        'max': 0.25,
        'step': 0.02,
        'type': 'fixed'
    },
    'min_hold_bars': {
        'current': 5,
        'min': 1,
        'max': 5,
        'step': 1,
        'type': 'fixed'
    },
    'max_hold_bars': {
        'current': 79,
        'min': 10,
        'max': 120,
        'step': 5,
        'type': 'fixed'
    }
}

print("PARAMETER RANGES FOR CALIBRATION:")
print()
print(f"{'Parameter':<30} {'Min':<10} {'Max':<10} {'Current':<10} {'Type':<12}")
print("─" * 72)
for param, config in PARAM_RANGES_ATR_SCALED.items():
    print(f"{param:<30} {config['min']:<10} {config['max']:<10} {config['current']:<10} {config['type']:<12}")

print()
print("="*100)
print()
print("INSTRUCTIONS TO IMPLEMENT:")
print("─" * 100)
print()
print("Step 1: Modify COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829.py")
print("  Location: initialize_smart_parameters() method")
print("  Change:")
print("    OLD: if atr_pct < 1.0: profit_target = 0.50")
print("    NEW: profit_target = atr * profit_target_atr_mult")
print()
print("Step 2: Update META_LEARNING_LOOP_ORCHESTRATOR_20260829.py")
print("  Location: param_ranges construction (~line 526)")
print("  Change:")
print("    OLD: 'profit_target': {'min': 0.25, 'max': 3.00}")
print("    NEW: 'profit_target_atr_mult': {'min': 0.20, 'max': 1.50}")
print()
print("Step 3: Run recalibration")
print("  Command: python RUN_CALIBRATION_AUTO.py")
print("  Duration: 6-12 hours")
print("  Output: meta_learning_convergence.json with ATR-scaled params")
print()
print("Step 4: Validate")
print("  Run VALIDATION_BACKTEST_OPTIMIZED_PARAMS.py")
print("  Expected: 50%+ win rate across full price range")
print()
print("="*100 + "\n")

# Save configuration
config_output = {
    'timestamp': datetime.now().isoformat(),
    'design_change': 'ATR-scaled multipliers instead of fixed rupees',
    'old_params': {
        'profit_target': {'min': 0.25, 'max': 3.00, 'type': 'fixed_rupees'},
        'stop_loss': {'min': -3.00, 'max': -0.10, 'type': 'fixed_rupees'}
    },
    'new_params': PARAM_RANGES_ATR_SCALED,
    'expected_improvement': '0% → 50%+ win rate (after fixing price range issue)',
    'root_cause_fixed': 'profit_target now scales with volatility (ATR), not fixed rupees'
}

with open('CALIBRATION_RECALIBRATION_PLAN.json', 'w') as f:
    json.dump(config_output, f, indent=2)

print("✓ Configuration saved to CALIBRATION_RECALIBRATION_PLAN.json")
print()
