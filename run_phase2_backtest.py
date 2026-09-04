#!/usr/bin/env python3
"""
================================================================================
PHASE 2 BACKTEST - COMPLETE RUN
================================================================================

Executes the complete 5-symbol paper trading backtest with:
1. Data loading (3-year history)
2. Bar-by-bar simulation
3. P&L and risk calculations
4. Performance reporting

Run: python run_phase2_backtest.py

================================================================================
"""

import sys
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

print("\n" + "="*80)
print("PHASE 2 - COMPLETE BACKTEST EXECUTION")
print("="*80 + "\n")

try:
    # Step 1: Load data
    print("STEP 1: Loading historical data...")
    print("-" * 80)

    from data_loader import DataLoader

    loader = DataLoader()
    symbols = ['INFY', 'TCS', 'RELIANCE', 'SUNPHARMA', 'HDFCLIFE']

    data = loader.load_all_symbols(
        start_date="2022-01-01",
        end_date="2024-12-31"
    )

    # Validate
    print("\nValidating data...")
    if not loader.validate_data(data):
        print("\n❌ Data validation failed!")
        sys.exit(1)

    print("✅ Data loading complete\n")

    # Step 2: Run backtest
    print("STEP 2: Running bar-by-bar backtest...")
    print("-" * 80 + "\n")

    from backtest_engine import BacktestEngine

    engine = BacktestEngine(initial_capital=1000000.0)
    metrics = engine.run_backtest(data)

    # Step 3: Print results
    print("\nSTEP 3: Results Summary")
    print("-" * 80)

    engine.print_summary()

    # Step 4: Detailed metrics
    print("\nDETAILED METRICS:")
    print(f"  Total Trades:          {metrics.total_trades}")
    print(f"  Win Rate:              {metrics.win_rate:.1f}%")
    print(f"  Profit Factor:         {metrics.profit_factor:.2f}")
    print(f"  Total P&L:             ₹{metrics.total_pnl:,.0f}")
    print(f"  Max Drawdown:          {metrics.max_drawdown:.2f}%")
    print(f"  Final Capital:         ₹{metrics.final_capital:,.0f}")
    print(f"  Return %:              {metrics.return_percent:.2f}%")

    print("\n" + "="*80)
    print("✅ PHASE 2 BACKTEST COMPLETE")
    print("="*80 + "\n")

except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
