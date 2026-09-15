#!/usr/bin/env python3
"""
================================================================================
PHASE 1: SINGLE SYMBOL VALIDATION DEPLOYMENT
================================================================================
Validates optimal parameters on INFY before multi-symbol deployment

Status: PRE-DEPLOYMENT VALIDATION
Target: 20 trades, 50%+ win rate on INFY
Safety: PAPER TRADING (no real money)

Usage:
  python PHASE_1_SINGLE_SYMBOL_VALIDATION_20260830.py

Expected Output:
  - 20-30 trades generated
  - Win rate >= 50%
  - P&L >= INR 5,000 (100 bps × 50 rupees average move)
  - Then proceed to PHASE 2

================================================================================
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Load optimal parameters
params_file = Path("OPTIMAL_PARAMETERS_20260830.json")
if not params_file.exists():
    print("[X] ERROR: OPTIMAL_PARAMETERS_20260830.json not found!")
    sys.exit(1)

with open(params_file, 'r') as f:
    config = json.load(f)

optimal_params = config['optimal_parameters']

print("="*80)
print("PHASE 1: SINGLE SYMBOL VALIDATION")
print("="*80)
print(f"\nDeployment Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("\nOptimal Parameters Loaded:")
for param_name, value in optimal_params.items():
    print(f"  {param_name:30} = {value:.4f}")

print("\nValidation Targets:")
print(f"  Symbol:           INFY")
print(f"  Trades Target:    20")
print(f"  Win Rate Target:  50%+")
print(f"  Trading Mode:     PAPER TRADING")
print(f"  Real Money:       NO (Safety)")

print("\n" + "="*80)
print("ACTION: Import optimal parameters into trading system")
print("="*80)
print("\nSteps to proceed:")
print("  1. Load OPTIMAL_PARAMETERS_20260830.json")
print("  2. Update COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829.py:")
print(f"     - profit_target_atr_mult  = {optimal_params['profit_target_atr_mult']}")
print(f"     - stop_loss_atr_mult      = {optimal_params['stop_loss_atr_mult']}")
print(f"     - entry_pid_kp            = {optimal_params['entry_pid_kp']}")
print(f"     - exit_pid_kp             = {optimal_params['exit_pid_kp']}")
print(f"     - min_hold_bars           = {optimal_params['min_hold_bars']}")
print(f"     - max_hold_bars           = {optimal_params['max_hold_bars']}")
print("  3. Run: MASTER_DEPLOYMENT_SCRIPT_20260829.py --mode quick")
print("  4. Monitor: Check win rate >= 50%")
print("  5. If PASSED: Proceed to PHASE 2")
print("\n" + "="*80)
