#!/usr/bin/env python3
"""
TEST: Simple ATR Bypass

Runs the 62-trade experiment with ATR mechanical stop DISABLED.
Uses symbol's own volatility as "grid state" for exit decisions.

Measures impact on:
  - Trade duration (longer holds?)
  - Exit quality (earlier??)
  - Win rate
  - P&L

Usage:
  python3 scripts/test_atr_bypass_simple.py INFY 5000
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import pandas as pd
from datetime import datetime
from typing import Dict, Any

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


def run_test_with_atr_bypass():
    """Run backtest with ATR stop disabled (multiplier = infinity)."""

    print("\n" + "="*140)
    print("TEST: ATR Mechanical Stop Bypassed".center(140))
    print("="*140 + "\n")

    # Load data
    symbol = "INFY"
    max_bars = 5000

    try:
        print(f"Loading {symbol} data...")
        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
        loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
        df = loader._load_symbol_csv(symbol)
        if max_bars:
            df = df.iloc[:max_bars]
        print(f"✓ Loaded {len(df):,} {symbol} bars\n")
    except Exception as e:
        print(f"✗ Failed to load data: {e}")
        return

    # Initialize registry - DISABLE ATR by setting it to very high value
    try:
        print("Initializing orchestrator...")
        registry = CanonicalParameterRegistry()

        original_atr = registry.values.get("trailing_stop_atr_mult", 4.0)
        print(f"  Original trailing_stop_atr_mult: {original_atr}")

        # Bypass ATR by setting it to infinity (stop will never be hit)
        registry.values["trailing_stop_atr_mult"] = 999.0
        print(f"  Modified trailing_stop_atr_mult: 999.0 (BYPASSED)")

        orch = Revision2ExternalEngineOrchestrator(
            [symbol],
            registry,
            starting_equity=1_000_000.0
        )
        print(f"✓ Orchestrator created\n")
    except Exception as e:
        print(f"✗ Failed to initialize: {e}")
        return

    # Run backtest
    try:
        print("Running backtest with ATR bypass...")
        symbol_bars = {symbol: df}
        report = orch.run(symbol_bars, warmup=60)

        trades = report.get("trades", [])

        print(f"\n" + "="*140)
        print("RESULTS: ATR BYPASSED".center(140))
        print("="*140)

        print(f"\nTrade Execution:")
        print(f"  Total trades: {len(trades)}")
        print(f"  Net P&L: ₹{report.get('net_pnl', 0):,.2f}")
        print(f"  Win rate: {report.get('win_rate', 0):.1%}")
        print(f"  Max drawdown: {report.get('max_drawdown', 0):.1%}")
        print(f"  Profit factor: {report.get('profit_factor', 0):.2f}")

        # Analyze trade duration
        if trades:
            bars_held_list = []
            winners = []
            losers = []

            for t in trades:
                bh = t.get("bars_held", 0)
                pnl = t.get("pnl", 0)
                bars_held_list.append(bh)
                if pnl > 0:
                    winners.append((bh, pnl))
                else:
                    losers.append((bh, pnl))

            print(f"\nTrade Duration Analysis:")
            print(f"  Average bars held: {sum(bars_held_list) / len(bars_held_list):.2f}")
            print(f"  Min: {min(bars_held_list)}, Max: {max(bars_held_list)}")
            print(f"  0-bar exits: {sum(1 for b in bars_held_list if b == 0)} ({100*sum(1 for b in bars_held_list if b == 0)/len(bars_held_list):.1f}%)")
            print(f"  1-3 bar holds: {sum(1 for b in bars_held_list if 1 <= b <= 3)}")
            print(f"  4+ bar holds: {sum(1 for b in bars_held_list if b >= 4)}")

            if winners:
                print(f"\nWinning Trades ({len(winners)}):")
                print(f"  Avg P&L: ₹{sum(p for _, p in winners) / len(winners):,.2f}")
                print(f"  Avg bars held: {sum(b for b, _ in winners) / len(winners):.2f}")
            if losers:
                print(f"\nLosing Trades ({len(losers)}):")
                print(f"  Avg P&L: ₹{sum(p for _, p in losers) / len(losers):,.2f}")
                print(f"  Avg bars held: {sum(b for b, _ in losers) / len(losers):.2f}")

            # Compare with original (62 trades, 0 bars held mostly)
            print(f"\n" + "─"*140)
            print(f"COMPARISON WITH ORIGINAL (ATR=4.0)")
            print(f"─"*140)
            print(f"\nOriginal (ATR=4.0):")
            print(f"  Trades: 62")
            print(f"  0-bar exits: 49 (79%)")
            print(f"  Net P&L: ₹-11,350.66")
            print(f"  Win rate: 0%")
            print(f"\nThis test (ATR=999.0):")
            print(f"  Trades: {len(trades)}")
            print(f"  0-bar exits: {sum(1 for b in bars_held_list if b == 0)} ({100*sum(1 for b in bars_held_list if b == 0)/len(bars_held_list):.1f}%)")
            print(f"  Net P&L: ₹{report.get('net_pnl', 0):,.2f}")
            print(f"  Win rate: {report.get('win_rate', 0):.1%}")

            pnl_improvement = report.get('net_pnl', 0) - (-11350.66)
            if pnl_improvement > 0:
                print(f"\n✓ IMPROVEMENT: ₹{pnl_improvement:,.2f} ({100*pnl_improvement/11350.66:.1f}% better)")
            else:
                print(f"\n✗ NO IMPROVEMENT: ₹{pnl_improvement:,.2f} ({100*abs(pnl_improvement)/11350.66:.1f}% worse)")

        print(f"\n" + "="*140)
        print("ANALYSIS")
        print("="*140)
        print(f"""
When ATR mechanical stop is BYPASSED (set to 999x):

1. **Trade exits are now controlled solely by PID controller**
   - No forced early exit from ATR droop
   - PID gets time to accumulate error
   - Integral term can build across many bars

2. **Expected outcomes:**
   - ✓ Longer trade duration (trades survive to PID exit)
   - ✓ Fewer immediate exits (no 0-bar or 1-bar exits)
   - ? Better or worse win rate (depends on whether PID exits at right time)
   - ? P&L change (depends on whether PID is properly tuned)

3. **Key questions to answer:**
   - Did trading duration increase?
   - Did win rate improve?
   - Is the P&L better?

If YES to all → PID controller CAN work if given time (ATR was the problem)
If NO → PID controller needs tuning (gains, target, etc.)
        """)

    except Exception as e:
        print(f"✗ Backtest failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_test_with_atr_bypass()
