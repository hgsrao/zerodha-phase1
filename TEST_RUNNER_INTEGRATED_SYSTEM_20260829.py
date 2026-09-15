#!/usr/bin/env python3
"""
================================================================================
TEST RUNNER: Complete Integrated Trading System
================================================================================

Quick start guide to test the complete system on your 48 equities

Steps:
  1. Load certified data (P01D-V2B)
  2. Initialize system (calculate thresholds + params)
  3. Run paper trading
  4. Verify win rate ≥ 52%
  5. Save results

================================================================================
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import sys

# Import the integrated system
from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import (
    CompleteIntegratedTradingSystem,
    RelativeSynchronizationThresholds,
    SmartParameterInitializer
)


# ============================================================================
# CONFIGURATION
# ============================================================================

# Data directory
DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

# 48 equities
SYMBOLS_48 = [
    'INFY', 'TCS', 'RELIANCE', 'HDFCBANK', 'SBIN',
    'BAJFINANCE', 'ICICIBANK', 'SUNPHARMA', 'KOTAKBANK', 'LT',
    'AXISBANK', 'BHARTIARTL', 'HINDUNILVR', 'ITC', 'MARUTI',
    'NTPC', 'POWERGRID', 'TATASTEEL', 'ZYDUSLIFE', 'LAURUSLABS',
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'BAJAJ-AUTO',
    'BAJAJFINSV', 'BEL', 'CIPLA', 'COALINDIA', 'DRREDDY',
    'EICHERMOT', 'ETERNAL', 'GRASIM', 'HCLTECH', 'HDFCLIFE',
    'HINDALCO', 'INDIGO', 'JIOFIN', 'JSWSTEEL', 'M&M',
    'MAXHEALTH', 'ONGC', 'SBILIFE', 'SHRIRAMFIN', 'TATACONSUM',
    'TECHM', 'TITAN', 'TRENT', 'ULTRACEMCO', 'WIPRO'
]

# Test symbols (start with 5)
SYMBOLS_TEST = SYMBOLS_48[:5]  # INFY, TCS, RELIANCE, HDFCBANK, SBIN


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def load_symbol_data(symbol: str, data_dir: Path) -> pd.DataFrame:
    """Load certified data for one symbol"""
    files = list(data_dir.glob(f"NSE_{symbol}_15minute_*.csv"))
    if not files:
        print(f"❌ No data found for {symbol}")
        return None

    try:
        df = pd.read_csv(files[0])
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"❌ Error loading {symbol}: {e}")
        return None


def load_all_symbols(symbols_list: list, data_dir: Path) -> dict:
    """Load data for multiple symbols"""
    data = {}
    print(f"\nLoading {len(symbols_list)} symbols...")
    for i, symbol in enumerate(symbols_list, 1):
        df = load_symbol_data(symbol, data_dir)
        if df is not None:
            data[symbol] = df
            print(f"  [{i:2}/{len(symbols_list)}] ✓ {symbol:15} ({len(df):6} bars)")
        else:
            print(f"  [{i:2}/{len(symbols_list)}] ✗ {symbol:15} (no data)")

    print(f"\n✓ Loaded {len(data)}/{len(symbols_list)} symbols\n")
    return data


def print_threshold_summary(thresholds: dict, symbol: str):
    """Print sync threshold summary"""
    t = thresholds.get(symbol, {})
    if t:
        print(f"\n{symbol}:")
        print(f"  dP/dt threshold: ₹{t.get('dp_dt_threshold', 0):.2f}")
        print(f"  dV/dt threshold: {t.get('dv_dt_threshold', 0):,.0f} shares")
        print(f"  Volatility: {t.get('volatility_class', 'UNKNOWN')}")


def print_param_summary(params: dict, symbol: str):
    """Print starting parameters summary"""
    p = params.get(symbol, {})
    if p:
        print(f"\n{symbol}:")
        print(f"  profit_target: ₹{p['profit_target']['current']:.2f}")
        print(f"  stop_loss: ₹{p['stop_loss']['current']:.2f}")
        print(f"  entry_pid_kp: {p['entry_pid_kp']['current']:.3f}")
        print(f"  max_hold_bars: {p['max_hold_bars']['current']}")


# ============================================================================
# MAIN TEST FLOW
# ============================================================================

def main():
    """Main test execution"""

    print("\n" + "="*80)
    print("COMPLETE INTEGRATED TRADING SYSTEM - TEST RUNNER")
    print("="*80)

    # ========================================================================
    # STEP 1: LOAD DATA
    # ========================================================================

    print("\n" + "="*80)
    print("STEP 1: LOAD CERTIFIED DATA")
    print("="*80)

    if not DATA_DIR.exists():
        print(f"❌ Data directory not found: {DATA_DIR}")
        return

    df_data = load_all_symbols(SYMBOLS_TEST, DATA_DIR)

    if not df_data:
        print("❌ No data loaded. Exiting.")
        return

    # ========================================================================
    # STEP 2: INITIALIZE SYSTEM
    # ========================================================================

    print("\n" + "="*80)
    print("STEP 2: INITIALIZE COMPLETE SYSTEM")
    print("="*80)

    system = CompleteIntegratedTradingSystem(verbose=True)
    init_result = system.initialize_from_data(df_data, SYMBOLS_TEST)

    if init_result['status'] != 'INITIALIZED':
        print("❌ System initialization failed")
        return

    # Display summaries for first symbol
    first_symbol = SYMBOLS_TEST[0]
    print(f"\n{'='*80}")
    print(f"SAMPLE: {first_symbol}")
    print(f"{'='*80}")

    print_threshold_summary(system.sync_thresholds, first_symbol)
    print_param_summary(system.starting_params, first_symbol)

    # ========================================================================
    # STEP 3: RUN PAPER TRADING
    # ========================================================================

    print("\n" + "="*80)
    print("STEP 3: RUN PAPER TRADING SIMULATION")
    print("="*80)

    try:
        results = system.run_paper_trading(
            symbols_list=SYMBOLS_TEST,
            test_period_days=5,
            use_optimized_params=False  # Use starting params for first test
        )

        if results['status'] == 'COMPLETED':
            print("\n✓ Paper trading completed successfully!")

            # Detailed summary
            metrics = results['metrics']
            print(f"\n{'='*80}")
            print("DETAILED RESULTS")
            print(f"{'='*80}")
            print(f"Total Trades:      {metrics['total_trades']}")
            print(f"Winning Trades:    {metrics['winning_trades']}")
            print(f"Losing Trades:     {metrics['losing_trades']}")
            print(f"Win Rate:          {metrics['win_rate']:.2%}")
            print(f"Total P&L:         ₹{metrics['total_pnl']:+,.2f}")
            print(f"Avg P&L/Trade:     ₹{metrics['avg_pnl']:+,.2f}")
            print(f"Sharpe Ratio:      {metrics['sharpe_ratio']:.3f}")
            print(f"Max Drawdown:      ₹{metrics['max_drawdown']:+,.2f}")
            print(f"Avg Hold Bars:     {metrics['avg_hold_bars']:.1f}")

            # Win rate assessment
            print(f"\n{'='*80}")
            print("WIN RATE ASSESSMENT")
            print(f"{'='*80}")
            if metrics['win_rate'] >= 0.52:
                print(f"✓ WIN RATE {metrics['win_rate']:.2%} ≥ 52% TARGET")
                print("✓ READY FOR TIER 1 DEPLOYMENT")
            elif metrics['win_rate'] >= 0.50:
                print(f"⚠ WIN RATE {metrics['win_rate']:.2%} (close to 52%)")
                print("  Consider expanding to more symbols or parameter tuning")
            else:
                print(f"❌ WIN RATE {metrics['win_rate']:.2%} < 50% TARGET")
                print("  Needs parameter optimization via 500-run calibration")

        else:
            print("❌ Paper trading failed")
            return

    except Exception as e:
        print(f"❌ Error during paper trading: {e}")
        import traceback
        traceback.print_exc()
        return

    # ========================================================================
    # STEP 4: SAVE RESULTS
    # ========================================================================

    print("\n" + "="*80)
    print("STEP 4: SAVE RESULTS")
    print("="*80)

    try:
        results_file = f"trading_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        system.save_results(results_file)
        print(f"✓ Results saved: {results_file}")

        # Also save as CSV for easy inspection
        trades_file = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        trades_list = results['trades']
        if trades_list:
            trades_df = pd.DataFrame(trades_list)
            trades_df.to_csv(trades_file, index=False)
            print(f"✓ Trades saved: {trades_file}")

    except Exception as e:
        print(f"⚠ Error saving results: {e}")

    # ========================================================================
    # NEXT STEPS
    # ========================================================================

    print("\n" + "="*80)
    print("NEXT STEPS")
    print("="*80)
    print("\n1. REVIEW RESULTS:")
    print(f"   - Open {results_file}")
    print(f"   - Check win_rate value")
    print(f"   - Analyze per-symbol performance")

    if metrics['win_rate'] < 0.52:
        print("\n2. RUN ADAPTIVE CALIBRATION:")
        print("   - Use AdaptiveParameterCalibrator class")
        print("   - Run 500 iterations to learn optimal parameters")
        print("   - Expected improvement: 36% → 52-55%")

    print("\n3. EXPAND TO MORE SYMBOLS:")
    print("   - Test on 10, then 25, then all 48 symbols")
    print("   - Verify consistent performance")

    print("\n4. IMPLEMENT TIER 1 DEPLOYMENT:")
    print("   - Deploy on 5 symbols with ₹500k capital")
    print("   - Run for 1 month")
    print("   - Monitor drawdown and win rate")

    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    main()
