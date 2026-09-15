#!/usr/bin/env python3
"""
DIAGNOSTIC TEST: 5 Symbols Only
Purpose: Isolate the scaling problem
Goal: Win rate ≥ 50%, trades ~500, positive P&L
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import logging

from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import CompleteIntegratedTradingSystem

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

# DIAGNOSTIC: Test just 5 symbols
SYMBOLS_5 = ['INFY', 'TCS', 'RELIANCE', 'HDFCBANK', 'SBIN']

logging.basicConfig(level=logging.WARNING)  # Suppress warnings to see results clearly

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
        print(f"❌ Error loading {symbol}: {e}")
        return None


def load_all_symbols(symbols_list: list, data_dir: Path) -> dict:
    """Load data for multiple symbols"""
    data = {}
    print(f"\n📊 Loading {len(symbols_list)} symbols...")
    for i, symbol in enumerate(symbols_list, 1):
        df = load_symbol_data(symbol, data_dir)
        if df is not None:
            data[symbol] = df
            print(f"  [{i}/{len(symbols_list)}] ✓ {symbol:15} ({len(df):6} bars)")
        else:
            print(f"  [{i}/{len(symbols_list)}] ✗ {symbol:15} (no data)")

    print(f"\n✓ Loaded {len(data)}/{len(symbols_list)} symbols\n")
    return data


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "="*80)
    print("DIAGNOSTIC TEST: 5 Symbols")
    print("="*80)
    print("Purpose: Isolate scaling issue")
    print("Expected: ~525 trades, 50-55% win rate, positive P&L\n")

    # Load data
    df_data = load_all_symbols(SYMBOLS_5, DATA_DIR)

    if len(df_data) < 5:
        print("❌ Failed to load enough symbols")
        return

    # Initialize system
    print("▶ Initializing system...\n")
    system = CompleteIntegratedTradingSystem(verbose=False)
    init_result = system.initialize_from_data(df_data, list(df_data.keys()))

    print(f"✓ System initialized:")
    print(f"  Thresholds calculated: {len(system.sync_thresholds)}")
    print(f"  Parameters initialized: {len(system.starting_params)}\n")

    # Run paper trading
    print("▶ Running paper trading...\n")
    start_time = datetime.now()
    results = system.run_paper_trading(list(df_data.keys()))
    elapsed = (datetime.now() - start_time).total_seconds()

    if results['status'] == 'COMPLETED':
        metrics = results['metrics']

        print("\n" + "="*80)
        print("DIAGNOSTIC RESULTS")
        print("="*80)
        print(f"Time:            {elapsed:.1f} seconds")
        print(f"Win Rate:        {metrics['win_rate']:.2%}")
        print(f"Total Trades:    {metrics['total_trades']}")
        print(f"Winning Trades:  {metrics['winning_trades']}")
        print(f"Total P&L:       ₹{metrics['total_pnl']:+,.2f}")
        print(f"Avg P&L/Trade:   ₹{metrics['avg_pnl']:+,.2f}")
        print(f"Sharpe Ratio:    {metrics['sharpe_ratio']:.3f}")
        print(f"Max Drawdown:    ₹{metrics['max_drawdown']:+,.2f}")
        print(f"Avg Hold Bars:   {metrics['avg_hold_bars']:.1f}")
        print("="*80)

        # Assessment
        print("\n📊 ASSESSMENT:\n")

        if metrics['win_rate'] >= 0.50:
            print("✅ WIN RATE GOOD (≥50%)")
            print("   → Problem was scaling at 48 symbols, not core logic")
            print("   → Can proceed with full test after adjustments")
        else:
            print("❌ WIN RATE POOR (<50%)")
            print("   → Problem is in parameter initialization")
            print("   → Need to debug per-symbol thresholds")

        if metrics['total_pnl'] > 0:
            print("\n✅ P&L POSITIVE")
            print(f"   → Making money: ₹{metrics['total_pnl']:+,.2f}")
        else:
            print("\n❌ P&L NEGATIVE")
            print(f"   → Losing money: ₹{metrics['total_pnl']:+,.2f}")

        avg_trades_per_symbol = metrics['total_trades'] / len(df_data)
        print(f"\n📈 TRADE DISTRIBUTION:")
        print(f"   → Avg trades per symbol: {avg_trades_per_symbol:.0f}")

        if avg_trades_per_symbol > 200:
            print(f"   ⚠️  EXCESSIVE: Should be ~105 per symbol")
            print(f"   → Entry gate is too permissive")
        elif avg_trades_per_symbol < 50:
            print(f"   ⚠️  TOO FEW: Should be ~105 per symbol")
            print(f"   → Entry gate is too strict")
        else:
            print(f"   ✅ REASONABLE: In expected range")

    else:
        print("❌ Test failed")


if __name__ == "__main__":
    main()
