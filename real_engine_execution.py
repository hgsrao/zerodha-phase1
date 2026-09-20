#!/usr/bin/env python3
"""
ZERODHA PHASE 1: REAL ENGINE EXECUTION
Against actual 1-minute market data with full orchestrator pipeline

This script:
1. Loads real CSV data from disk
2. Instantiates the actual Revision2ExternalEngineOrchestrator
3. Runs the COMPLETE pipeline (Boxes 1-10):
   - Box 1: Startup validation (Pydantic)
   - Box 2: Data ingestion
   - Box 3: Data certification (Pandera)
   - Box 4: Predictive analytics (TALib)
   - Box 5: Regime detection (HMM)
   - Box 6: MPC with PID control (with Rolling Setpoint + Bounded PID)
   - Box 7: Safety Gates (18-gate evaluation)
   - Box 8: Position sizing (PyPortfolioOpt)
   - Box 9: P01D signing
   - Box 10: Order execution (paper broker)
4. Reports ACTUAL results: trades, P&L, safety gate decisions

SHOW THIS CODE TO USER BEFORE EXECUTION
"""

import sys
import json
from pathlib import Path
from datetime import datetime
import pandas as pd

# ==============================================================================
# CONFIGURATION
# ==============================================================================

DATA_PATH = "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"

# Test with first 3 symbols to keep runtime reasonable
TEST_SYMBOLS = ['NSE_INFY_minute_2023-07-03_2026-08-24',
                'NSE_TCS_minute_2023-07-03_2026-08-24',
                'NSE_RELIANCE_minute_2023-07-03_2026-08-24']

# Use only first 500 bars per symbol (roughly 1/2 day at 1-minute frequency)
BARS_LIMIT = 500
WARMUP_BARS = 60

print("\n" + "="*100)
print("ZERODHA PHASE 1: REAL ENGINE EXECUTION")
print("="*100)
print(f"Data Path: {DATA_PATH}")
print(f"Test Symbols: {len(TEST_SYMBOLS)}")
print(f"Bars per symbol: {BARS_LIMIT}")
print(f"Warmup bars: {WARMUP_BARS}")
print("="*100 + "\n")

# ==============================================================================
# STEP 1: LOAD REAL DATA
# ==============================================================================

print("[STEP 1] Loading real market data from CSV...")

try:
    from revision4_audit_fixed.orchestrator import Revision2ExternalEngineOrchestrator
    print("✓ Orchestrator imported")
except ImportError as e:
    print(f"ERROR: Could not import orchestrator: {e}")
    sys.exit(1)

try:
    from canonical_parameter_registry import CanonicalParameterRegistry
    print("✓ Parameter registry imported")
except ImportError as e:
    print(f"ERROR: Could not import registry: {e}")
    sys.exit(1)

symbol_bars = {}
for symbol in TEST_SYMBOLS:
    file_path = Path(DATA_PATH) / f"{symbol}.csv"

    if not file_path.exists():
        print(f"  ✗ {symbol}: File not found")
        continue

    try:
        # Load CSV
        df = pd.read_csv(file_path)
        print(f"  ✓ {symbol}: {len(df):,} bars loaded")

        # Limit to BARS_LIMIT
        df = df.head(BARS_LIMIT).copy()
        print(f"    Using first {len(df)} bars")

        # Ensure required columns exist (case-insensitive)
        required = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        columns_lower = {col.lower(): col for col in df.columns}

        missing = [col for col in required if col not in columns_lower]
        if missing:
            print(f"    ERROR: Missing columns {missing}")
            continue

        # Store with symbol name (simplified)
        symbol_clean = symbol.replace('NSE_', '').replace('_minute_2023-07-03_2026-08-24', '')
        symbol_bars[symbol_clean] = df

    except Exception as e:
        print(f"  ✗ {symbol}: {e}")
        continue

print(f"\n✓ Loaded {len(symbol_bars)} symbols ready for orchestrator")

if not symbol_bars:
    print("ERROR: No data loaded")
    sys.exit(1)

# ==============================================================================
# STEP 2: INSTANTIATE ORCHESTRATOR
# ==============================================================================

print("\n[STEP 2] Instantiating Revision2ExternalEngineOrchestrator...")

try:
    registry = CanonicalParameterRegistry()

    orchestrator = Revision2ExternalEngineOrchestrator(
        symbols=list(symbol_bars.keys()),
        registry=registry,
        starting_equity=1_000_000.0,
        closed_loop_mode="shadow",  # Paper trading, not real
        telemetry_mode="full",
        pid_mode="enabled",  # PID controllers ACTIVE
    )
    print("✓ Orchestrator instantiated")
    print(f"  Symbols: {orchestrator.symbols}")
    print(f"  Starting equity: ₹{orchestrator.starting_equity:,.0f}")
    print(f"  PID mode: {orchestrator.pid_mode}")
    print(f"  Closed-loop mode: {orchestrator.closed_loop_mode}")

except Exception as e:
    print(f"ERROR: Could not instantiate orchestrator: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ==============================================================================
# STEP 3: RUN FULL PIPELINE
# ==============================================================================

print("\n[STEP 3] Running complete orchestrator pipeline...")
print("  (Boxes 1-10: Validation → Ingestion → Cert → PA → HMM → PID → Gates → Pos.Mgmt → P01D → Execution)")

try:
    results = orchestrator.run(
        symbol_bars=symbol_bars,
        warmup=WARMUP_BARS,
        precomputed_clock=None,
    )
    print("✓ Pipeline execution complete")

except Exception as e:
    print(f"ERROR during orchestrator.run(): {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ==============================================================================
# STEP 4: EXTRACT AND REPORT RESULTS
# ==============================================================================

print("\n" + "="*100)
print("ORCHESTRATOR RESULTS")
print("="*100)

# Funnel metrics
print("\nProcessing Funnel:")
if isinstance(results, dict):
    for key, value in results.items():
        if 'funnel' in str(key).lower() or key in ['bars_processed', 'pa_signals', 'safety_approvals', 'safety_rejections', 'gates_passed', 'gates_rejected', 'orders_submitted', 'fills']:
            print(f"  {key}: {value}")

print(f"\nCompleted Trades: {len(orchestrator.completed_trades)}")
if orchestrator.completed_trades:
    trades_df = pd.DataFrame(orchestrator.completed_trades)
    print(f"\nTrade Summary:")
    print(f"  Total P&L: ₹{trades_df.get('realized_pnl', [0]).sum():+,.2f}")
    print(f"  Average P&L: ₹{trades_df.get('realized_pnl', [0]).mean():+,.2f}" if 'realized_pnl' in trades_df.columns else "  (P&L not yet calculated)")

    print(f"\nFirst 5 trades:")
    print(trades_df[['symbol', 'entry_price', 'exit_price', 'entry_timestamp', 'exit_timestamp']].head(5).to_string() if 'entry_price' in trades_df.columns else "  (No trade details)")

print(f"\nEquity Curve:")
print(f"  Starting: ₹{orchestrator.starting_equity:,.0f}")
print(f"  Final: ₹{orchestrator._equity_curve[-1]:,.2f}" if orchestrator._equity_curve else "  (No equity data)")

print(f"\nController Telemetry Events: {len(orchestrator.controller_telemetry)}")
if orchestrator.controller_telemetry:
    print(f"  (PID setpoints, gate decisions, position sizing recorded)")

print(f"\nSafety Gates:")
print(f"  Total gates evaluated: {sum(1 for t in orchestrator.controller_telemetry if 'gates' in str(t).lower())}")

# ==============================================================================
# STEP 5: SAVE RESULTS
# ==============================================================================

output = {
    'timestamp': datetime.now().isoformat(),
    'execution': 'REAL ORCHESTRATOR PIPELINE',
    'symbols_tested': list(symbol_bars.keys()),
    'bars_per_symbol': BARS_LIMIT,
    'warmup_bars': WARMUP_BARS,
    'starting_equity': orchestrator.starting_equity,
    'final_equity': float(orchestrator._equity_curve[-1]) if orchestrator._equity_curve else None,
    'total_trades': len(orchestrator.completed_trades),
    'controller_events': len(orchestrator.controller_telemetry),
    'results': results,
}

with open('orchestrator_execution_results.json', 'w') as f:
    json.dump(output, f, indent=2, default=str)

print("\n" + "="*100)
print("✓ REAL ENGINE EXECUTION COMPLETE")
print("✓ Results saved to: orchestrator_execution_results.json")
print("="*100 + "\n")
