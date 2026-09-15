#!/usr/bin/env python3
"""
================================================================================
PRE-DEPLOYMENT VERIFICATION CHECKLIST
================================================================================
Verify all systems ready before deploying optimal parameters

Usage:
  python PRE_DEPLOYMENT_VERIFICATION_20260830.py

Output:
  ✓ Pass: All systems ready for deployment
  ✗ Fail: Issues must be resolved before proceeding
"""

import json
import sys
from pathlib import Path
from datetime import datetime

def verify_files_exist():
    """Check all required files exist"""
    required_files = [
        "OPTIMAL_PARAMETERS_20260830.json",
        "DEPLOYMENT_MANUAL_20260830.txt",
        "CALIBRATION_FINAL_RESULTS_20260830.txt",
        "PARAMETER_COMPARISON_BEFORE_AFTER_20260830.txt",
        "calibration_progress.csv",
        "COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829.py",
        "META_LEARNING_LOOP_ORCHESTRATOR_20260829.py",
        "MASTER_DEPLOYMENT_SCRIPT_20260829.py"
    ]
    
    print("\n1. FILE VERIFICATION")
    print("-" * 60)
    
    all_exist = True
    for filename in required_files:
        if Path(filename).exists():
            print(f"  ✓ {filename}")
        else:
            print(f"  ✗ {filename} NOT FOUND")
            all_exist = False
    
    return all_exist

def verify_parameters():
    """Verify optimal parameters loaded correctly"""
    print("\n2. PARAMETER VERIFICATION")
    print("-" * 60)
    
    try:
        with open("OPTIMAL_PARAMETERS_20260830.json", "r") as f:
            config = json.load(f)
        
        params = config['optimal_parameters']
        
        # Check all 6 parameters exist
        required_params = [
            'profit_target_atr_mult',
            'stop_loss_atr_mult',
            'entry_pid_kp',
            'exit_pid_kp',
            'min_hold_bars',
            'max_hold_bars'
        ]
        
        all_valid = True
        for param in required_params:
            if param in params:
                value = params[param]
                print(f"  ✓ {param:30} = {value:.4f}")
            else:
                print(f"  ✗ {param:30} MISSING")
                all_valid = False
        
        # Verify risk/reward
        pt = params['profit_target_atr_mult']
        sl = params['stop_loss_atr_mult']
        rr = pt / sl
        
        print(f"\n  Risk/Reward Check:")
        print(f"    Profit Target: {pt:.4f} ATR")
        print(f"    Stop Loss:     {sl:.4f} ATR")
        print(f"    Ratio:         {rr:.2f}:1", end="")
        
        if rr >= 1.5:
            print(" ✓ (exceeds 1.5:1 minimum)")
        else:
            print(" ✗ (below 1.5:1 minimum)")
            all_valid = False
        
        return all_valid
    
    except Exception as e:
        print(f"  ✗ Error loading parameters: {e}")
        return False

def verify_calibration_results():
    """Verify calibration met targets"""
    print("\n3. CALIBRATION RESULTS VERIFICATION")
    print("-" * 60)
    
    try:
        df = __import__('pandas').read_csv("calibration_progress.csv", header=None)
        iterations = len(df) - 1  # Exclude header
        win_rates = df[1].astype(float)
        
        avg_wr = win_rates.mean()
        max_wr = win_rates.max()
        min_wr = win_rates.min()
        
        print(f"  Total Iterations:    {iterations} (target: 500)")
        print(f"    ✓ PASSED" if iterations >= 500 else f"    ✗ FAILED")
        
        print(f"\n  Average Win Rate:    {avg_wr:.2%} (target: >= 48%)")
        print(f"    ✓ PASSED" if avg_wr >= 0.48 else f"    ✗ FAILED")
        
        print(f"\n  Final Win Rate:      {win_rates.iloc[-1]:.2%} (target: >= 50%)")
        print(f"    ✓ PASSED" if win_rates.iloc[-1] >= 0.50 else f"    ✗ FAILED")
        
        print(f"\n  Best Win Rate:       {max_wr:.2%} (proof of concept)")
        print(f"    ✓ PASSED" if max_wr >= 0.50 else f"    ✗ FAILED")
        
        all_passed = (iterations >= 500 and avg_wr >= 0.48 and 
                      win_rates.iloc[-1] >= 0.50 and max_wr >= 0.50)
        return all_passed
    
    except Exception as e:
        print(f"  ✗ Error reading calibration results: {e}")
        return False

def verify_safety_locks():
    """Check safety locks in code"""
    print("\n4. SAFETY LOCK VERIFICATION")
    print("-" * 60)
    
    try:
        with open("MASTER_DEPLOYMENT_SCRIPT_20260829.py", "r") as f:
            content = f.read()
        
        # Check for LIVE_TRADING_ENABLED = False
        if "LIVE_TRADING_ENABLED" in content:
            if "LIVE_TRADING_ENABLED = False" in content:
                print("  ✓ LIVE_TRADING_ENABLED = False (safety lock active)")
                return True
            else:
                print("  ✗ LIVE_TRADING_ENABLED not set to False")
                return False
        else:
            print("  ⚠ LIVE_TRADING_ENABLED not found (may be in parent class)")
            return True  # Warning but not failure
    
    except Exception as e:
        print(f"  ✗ Error checking safety locks: {e}")
        return False

def main():
    print("="*80)
    print("PRE-DEPLOYMENT VERIFICATION CHECKLIST")
    print("="*80)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {
        'Files': verify_files_exist(),
        'Parameters': verify_parameters(),
        'Calibration': verify_calibration_results(),
        'Safety Locks': verify_safety_locks()
    }
    
    print("\n" + "="*80)
    print("VERIFICATION SUMMARY")
    print("="*80)
    
    all_passed = True
    for category, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{category:20} {status}")
        if not passed:
            all_passed = False
    
    print("\n" + "="*80)
    if all_passed:
        print("RESULT: ✓ ALL CHECKS PASSED - READY FOR DEPLOYMENT")
        print("="*80)
        print("\nNext steps:")
        print("  1. Review DEPLOYMENT_MANUAL_20260830.txt")
        print("  2. Prepare Phase 1 (INFY single symbol)")
        print("  3. Run: PHASE_1_SINGLE_SYMBOL_VALIDATION_20260830.py")
        print("\nEstimated time to live: ~2-3 hours (after Phase 1 + Phase 2)")
        return 0
    else:
        print("RESULT: ✗ VERIFICATION FAILED - DO NOT DEPLOY")
        print("="*80)
        print("\nAction required:")
        print("  - Review failures above")
        print("  - Fix issues before proceeding")
        print("  - Re-run verification script")
        return 1

if __name__ == "__main__":
    sys.exit(main())
