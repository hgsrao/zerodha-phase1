#!/usr/bin/env python3
"""
PARTIAL CALIBRATION: 5 Symbols Only
Faster testing before full 48-symbol optimization
Expected time: 1-2 hours
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import sys

from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import CompleteIntegratedTradingSystem
from META_LEARNING_LOOP_ORCHESTRATOR_20260829 import IntegratedTradingSystemWithMetaLearning

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

# PARTIAL: Just 5 symbols for faster testing
SYMBOLS_5 = ['INFY', 'TCS', 'RELIANCE', 'HDFCBANK', 'SBIN']

print("\n" + "="*80)
print("PARTIAL CALIBRATION: 5 Symbols")
print("="*80)
print(f"Symbols: {SYMBOLS_5}")
print(f"Expected time: 1-2 hours")
print(f"Target: 52%+ win rate")
print("="*80 + "\n")

# ============================================================================
# LOAD DATA
# ============================================================================

def load_symbol_data(symbol: str, data_dir: Path):
    files = list(data_dir.glob(f"NSE_{symbol}_15minute_*.csv"))
    if not files:
        return None
    try:
        df = pd.read_csv(files[0])
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Error loading {symbol}: {e}")
        return None

print("Loading 5 symbols...")
df_data = {}
for i, symbol in enumerate(SYMBOLS_5, 1):
    df = load_symbol_data(symbol, DATA_DIR)
    if df is not None:
        df_data[symbol] = df
        print(f"  [{i}/5] ✓ {symbol} ({len(df)} bars)")
    else:
        print(f"  [{i}/5] ✗ {symbol} (no data)")

if len(df_data) < 5:
    print("❌ Failed to load all 5 symbols")
    sys.exit(1)

print(f"✓ Loaded {len(df_data)}/5 symbols\n")

# ============================================================================
# RUN META-LEARNING CALIBRATION
# ============================================================================

print("Initializing meta-learning system...")
integrated_system = IntegratedTradingSystemWithMetaLearning(verbose=True)

print("Initializing data...")
init_result = integrated_system.initialize(df_data, list(df_data.keys()))
print(f"✓ System initialized\n")

print("="*80)
print("STARTING 500-ITERATION META-LEARNING CALIBRATION")
print("="*80)
print("Phase 1 (Runs 1-50):   🔀 Random exploration")
print("Phase 2 (Runs 51-250): 🎯 Bayesian optimization")
print("Phase 3 (Runs 251-500): 🔬 Fine-tuning convergence")
print("="*80 + "\n")

start_time = datetime.now()

try:
    results = integrated_system.run_meta_learning_optimization(
        symbols_list=list(df_data.keys()),
        target_runs=500
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    elapsed_hours = elapsed / 3600

    print("\n" + "="*80)
    print("CALIBRATION COMPLETE")
    print("="*80)
    print(f"Time elapsed: {elapsed_hours:.1f} hours ({elapsed/60:.0f} minutes)")
    print(f"\nBest results:")
    print(f"  Run: {results['best_run']}/500")
    print(f"  Win Rate: {results['best_win_rate']:.2%}")
    print(f"  Total P&L: ₹{results.get('best_pnl', 'N/A'):+,.0f}")

    if results['best_win_rate'] >= 0.52:
        print(f"\n✅ TARGET MET: {results['best_win_rate']:.2%} ≥ 52%")
        print(f"✅ READY FOR FULL 48-SYMBOL CALIBRATION")
    else:
        print(f"\n⚠️  Win rate {results['best_win_rate']:.2%} < 52% target")
        print(f"   But parameters have been optimized for {len(df_data)} symbols")

    print(f"\nOptimal parameters:")
    for param_name, value in results.get('best_parameters', {}).items():
        print(f"  {param_name}: {value}")

    # Save results
    import json
    results_file = f"PARTIAL_CALIBRATION_5SYMBOL_RESULTS_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n✓ Results saved: {results_file}")

    print("="*80)

except KeyboardInterrupt:
    print("\n❌ Calibration interrupted by user")
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"Partial run time: {elapsed/60:.1f} minutes")

except Exception as e:
    print(f"\n❌ Error during calibration: {e}")
    import traceback
    traceback.print_exc()
