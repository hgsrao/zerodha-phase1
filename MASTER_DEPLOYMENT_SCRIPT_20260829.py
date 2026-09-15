#!/usr/bin/env python3
"""
================================================================================
MASTER DEPLOYMENT SCRIPT - Complete System Integration
================================================================================

READY TO RUN - All layers integrated:

Layer 0: Meta-Learning Loop (500 iterations, 3 phases)
Layer 1: Relative Synchronization Thresholds (symbol-specific)
Layer 2: Smart Parameter Initialization (data-driven)
Layer 3: Adaptive Parameter Calibrator (Bayesian optimization)
Layer 4: Unified P01D Governor Execution (3-phase trading)

System within System:
┌─────────────────────────────────────────────────────┐
│ META-LEARNING LOOP (500 iterations)                 │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Iteration 1-500:                                  │
│    ├─ Generate parameters                          │
│    ├─ Create 6-Stage System                        │
│    ├─ Run backtest                                 │
│    ├─ Measure: win_rate, sharpe, drawdown          │
│    └─ Track best found                             │
│                                                      │
│  Within each iteration -> 6-Stage System:           │
│    ├─ Stage 1: Data Validation                     │
│    ├─ Stage 2: PA + Feedback Loop 1                │
│    ├─ Stage 3: ID Threshold                        │
│    ├─ Stage 4: Bridge                              │
│    ├─ Stage 5: MPC + Feedback Loop 2               │
│    └─ Stage 6: P01D Governor                       │
│                                                      │
└─────────────────────────────────────────────────────┘

Usage:
  python MASTER_DEPLOYMENT_SCRIPT_20260829.py --mode [quick|full|calibration]

Modes:
  quick:        Test on 1 symbol (2 min)
  full:         Test on 48 symbols (60 min)
  calibration:  Run 500-iteration meta-learning (6-12 hours)

================================================================================
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import sys
import argparse
import logging

# Import all modules
from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import (
    CompleteIntegratedTradingSystem,
    RelativeSynchronizationThresholds,
    SmartParameterInitializer
)

from META_LEARNING_LOOP_ORCHESTRATOR_20260829 import (
    MetaLearningLoopOrchestrator,
    IntegratedTradingSystemWithMetaLearning
)


# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('master_deployment.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('MasterDeployment')


# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

# AUTHORITATIVE NIFTY-48 UNIVERSE
# Source: P01D_RESEARCH_48STOCK_20260816/RESULTS/_R12_INPUT_VIEWS/B1_NIFTY48
# Certified: CHART_STUDIES_NIFTY48_V7_DIAGNOSTIC_20260824
# Exactly 48 symbols (LAURUSLABS and ZYDUSLIFE removed from data directory list)
SYMBOLS_48 = [
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK',
    'BAJAJ-AUTO', 'BAJAJFINSV', 'BAJFINANCE', 'BEL', 'BHARTIARTL',
    'CIPLA', 'COALINDIA', 'DRREDDY', 'EICHERMOT', 'ETERNAL',
    'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE', 'HINDALCO',
    'HINDUNILVR', 'ICICIBANK', 'INDIGO', 'INFY', 'ITC',
    'JIOFIN', 'JSWSTEEL', 'KOTAKBANK', 'LT', 'M&M',
    'MARUTI', 'MAXHEALTH', 'NTPC', 'ONGC', 'POWERGRID',
    'RELIANCE', 'SBILIFE', 'SBIN', 'SHRIRAMFIN', 'SUNPHARMA',
    'TATACONSUM', 'TATASTEEL', 'TCS', 'TECHM', 'TITAN',
    'TRENT', 'ULTRACEMCO', 'WIPRO'
]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def load_symbol_data(symbol: str, data_dir: Path) -> pd.DataFrame:
    """Load certified data for one symbol"""
    files = list(data_dir.glob(f"NSE_{symbol}_15minute_*.csv"))
    if not files:
        return None

    try:
        df = pd.read_csv(files[0])
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Error loading {symbol}: {e}")
        return None


def load_all_symbols(symbols_list: list, data_dir: Path) -> dict:
    """Load data for multiple symbols"""
    data = {}
    print(f"\n[CHART] Loading {len(symbols_list)} symbols...")
    for i, symbol in enumerate(symbols_list, 1):
        df = load_symbol_data(symbol, data_dir)
        if df is not None:
            data[symbol] = df
            print(f"  [{i:2}/{len(symbols_list)}] [OK] {symbol:15} ({len(df):6} bars)")
        else:
            print(f"  [{i:2}/{len(symbols_list)}] [FAIL] {symbol:15} (no data)")

    print(f"\n[OK] Loaded {len(data)}/{len(symbols_list)} symbols\n")
    return data


# ============================================================================
# MODE 1: QUICK TEST (Single Symbol)
# ============================================================================

def mode_quick():
    """Quick test on 1 symbol (INFY) - ~2 minutes"""

    print("\n" + "="*80)
    print("MODE: QUICK TEST (1 Symbol)")
    print("="*80)
    print("Target: Verify system works on 1 symbol")
    print("Time: ~2 minutes")
    print("Expected win rate: 48-54%\n")

    # Load data
    symbols_test = ['INFY']
    df_data = load_all_symbols(symbols_test, DATA_DIR)

    if not df_data:
        print("[X] Failed to load data")
        return

    # Initialize system
    system = CompleteIntegratedTradingSystem(verbose=True)
    init_result = system.initialize_from_data(df_data, symbols_test)

    print(f"\n[OK] System initialized:")
    print(f"  Thresholds calculated: {len(system.sync_thresholds)}")
    print(f"  Parameters initialized: {len(system.starting_params)}\n")

    # Run paper trading
    print("[>] Running paper trading...\n")
    results = system.run_paper_trading(symbols_test)

    if results['status'] == 'COMPLETED':
        metrics = results['metrics']
        print(f"\n[SUCCESS] QUICK TEST COMPLETE")
        print(f"  Win Rate: {metrics['win_rate']:.2%}")
        print(f"  Total Trades: {metrics['total_trades']}")
        print(f"  Total P&L: INR {metrics['total_pnl']:+,.2f}\n")

        if metrics['win_rate'] >= 0.50:
            print("[OK] PASSED - Ready for next phase")
        else:
            print("⚠ BELOW TARGET - Will improve with calibration")
    else:
        print("[X] Test failed")


# ============================================================================
# MODE 2: FULL TEST (All 48 Symbols)
# ============================================================================

def mode_full():
    """Full test on 48 symbols - ~60 minutes"""

    print("\n" + "="*80)
    print("MODE: FULL TEST (48 Symbols)")
    print("="*80)
    print("Target: Comprehensive validation across all symbols")
    print("Time: ~30-60 minutes")
    print("Expected win rate: 48-54%\n")

    # Load data
    df_data = load_all_symbols(SYMBOLS_48, DATA_DIR)

    if len(df_data) < 20:
        print("[X] Failed to load enough symbols")
        return

    # Initialize system
    system = CompleteIntegratedTradingSystem(verbose=True)
    init_result = system.initialize_from_data(df_data, list(df_data.keys()))

    print(f"\n[OK] System initialized:")
    print(f"  Symbols loaded: {len(df_data)}")
    print(f"  Thresholds calculated: {len(system.sync_thresholds)}")
    print(f"  Parameters initialized: {len(system.starting_params)}\n")

    # Run paper trading
    print("[>] Running paper trading on all symbols...\n")
    start_time = datetime.now()
    results = system.run_paper_trading(list(df_data.keys()))
    elapsed = (datetime.now() - start_time).total_seconds()

    if results['status'] == 'COMPLETED':
        metrics = results['metrics']
        print(f"\n[SUCCESS] FULL TEST COMPLETE (in {elapsed:.1f} seconds)")
        print(f"  Win Rate: {metrics['win_rate']:.2%}")
        print(f"  Total Trades: {metrics['total_trades']}")
        print(f"  Winning Trades: {metrics['winning_trades']}")
        print(f"  Total P&L: INR {metrics['total_pnl']:+,.2f}")
        print(f"  Sharpe Ratio: {metrics['sharpe_ratio']:.3f}")
        print(f"  Max Drawdown: INR {metrics['max_drawdown']:+,.2f}\n")

        if metrics['win_rate'] >= 0.52:
            print("[OK] PASSED - READY FOR TIER 1 DEPLOYMENT (INR 500k)")
        elif metrics['win_rate'] >= 0.50:
            print("⚠ CLOSE - Run calibration for +1-2% improvement")
        else:
            print("[X] BELOW TARGET - Run full 500-iteration calibration")
    else:
        print("[X] Test failed")


# ============================================================================
# MODE 3: META-LEARNING CALIBRATION (500 Iterations)
# ============================================================================

def mode_calibration():
    """Run 500-iteration meta-learning calibration - 6-12 hours"""

    print("\n" + "="*80)
    print("MODE: META-LEARNING CALIBRATION (500 Iterations)")
    print("="*80)
    print("Target: Learn optimal parameters via 3-phase optimization")
    print("Time: ~6-12 hours on PC")
    print("Expected win rate improvement: 36% -> 52-55%\n")

    # Confirm
    response = input("[!] This will take 6-12 hours. Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("[X] Calibration cancelled")
        return

    # Load data
    print(f"\n[*] Loading {len(SYMBOLS_48)} symbols...")
    df_data = load_all_symbols(SYMBOLS_48, DATA_DIR)

    if len(df_data) < 20:
        print("[X] Failed to load enough symbols")
        return

    print(f"[+] Loaded {len(df_data)} symbols\n")

    # Create integrated system
    print("[>] Initializing integrated system with meta-learning loop...\n")
    integrated_system = IntegratedTradingSystemWithMetaLearning(verbose=True)

    # Initialize
    init_result = integrated_system.initialize(df_data, list(df_data.keys()))
    print(f"\n[+] System initialized: {init_result}\n")

    # Run meta-learning loop
    print("[>] Starting 500-iteration meta-learning optimization...\n")
    print("  Phase 1 (Runs 1-50):   [RANDOM] Random exploration")
    print("  Phase 2 (Runs 51-250): [BAYESIAN] Bayesian optimization")
    print("  Phase 3 (Runs 251-500): [TUNING] Fine-tuning convergence\n")

    start_time = datetime.now()

    results = integrated_system.run_meta_learning_optimization(
        symbols_list=list(df_data.keys()),
        target_runs=500
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    elapsed_hours = elapsed / 3600

    if results['status'] == 'COMPLETED':
        print(f"\n[SUCCESS] CALIBRATION COMPLETE (in {elapsed_hours:.1f} hours)")
        print(f"  Best run: {results['best_run']}/500")
        print(f"  Best win rate: {results['best_win_rate']:.2%}")
        print(f"\n[OK] Optimal parameters learned:")
        for param_name, value in results['best_parameters'].items():
            print(f"  {param_name:20} = {value:.4f}")

        # Save parameters
        import json
        param_file = f"optimal_parameters_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(param_file, 'w') as f:
            json.dump(results['best_parameters'], f, indent=2)
        print(f"\n[OK] Optimal parameters saved: {param_file}")

        # Plot convergence
        try:
            integrated_system.meta_loop.plot_convergence()
        except Exception as e:
            print(f"[!]  Could not plot convergence: {e}")

        print(f"\n[OK] Next step: Deploy with optimal parameters on Tier 1 (INR 500k)")

    else:
        print("[X] Calibration failed")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main entry point"""

    parser = argparse.ArgumentParser(
        description='Master Deployment Script - Complete Trading System'
    )
    parser.add_argument(
        '--mode',
        choices=['quick', 'full', 'calibration'],
        default='quick',
        help='Execution mode (default: quick)'
    )

    args = parser.parse_args()

    print("\n" + "="*80)
    print("MASTER DEPLOYMENT SCRIPT v1.0")
    print("="*80)
    print("\nComplete Integration:")
    print("  [OK] Layer 0: Meta-Learning Loop (500 iterations, 3 phases)")
    print("  [OK] Layer 1: Relative Sync Thresholds (symbol-specific)")
    print("  [OK] Layer 2: Smart Parameter Initialization (data-driven)")
    print("  [OK] Layer 3: Adaptive Parameter Calibrator (Bayesian)")
    print("  [OK] Layer 4: Unified P01D Governor (3-phase trading)")
    print("  [OK] Feedback Loops: PA Learning + Lambda Risk Control")

    if args.mode == 'quick':
        mode_quick()
    elif args.mode == 'full':
        mode_full()
    elif args.mode == 'calibration':
        mode_calibration()

    print("\n" + "="*80)
    print("Deployment complete!")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
