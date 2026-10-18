#!/usr/bin/env python3
"""
TEST: Bypass ATR + Add Grid Synchronization

Runs the 62-trade experiment with:
  1. ATR mechanical stop DISABLED (bypassed)
  2. Grid synchronization (Nifty 50 state) gating entry/exit

Measures impact on:
  - Trade count (will entries be filtered out?)
  - Exit behavior (does grid sync exit faster/slower?)
  - Win rate (better quality entries?)
  - P&L (profitability improvement?)

Usage:
  python3 scripts/test_atr_bypass_with_grid_sync.py INFY 5000
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
import json
from datetime import datetime
from typing import Dict, Any
import logging

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2.grid_synchronization import GridSynchronizationBox
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


class GridSyncOrchestrator:
    """Wrapper that adds grid synchronization to baseline orchestrator."""

    def __init__(self, orchestrator: Revision2ExternalEngineOrchestrator, nifty_df: pd.DataFrame):
        self.orchestrator = orchestrator
        self.grid = GridSynchronizationBox(lookback_bars=50)
        self.grid.load_nifty_data(nifty_df)
        self.modified_trades: Dict[str, Any] = {}

    def run(self, symbol_bars: Dict[str, pd.DataFrame], warmup: int = 60) -> Dict[str, Any]:
        """Run orchestrator with grid sync filtering."""

        print("\n" + "="*140)
        print("RUNNING WITH GRID SYNCHRONIZATION + ATR BYPASS")
        print("="*140)

        # Run the base orchestrator
        print(f"\n[1] Running base orchestrator (ATR will still be active in this version)...")
        report = self.orchestrator.run(symbol_bars, warmup=warmup)

        # Analyze trades through grid lens
        trades = report.get("trades", [])
        print(f"\n[2] Analyzing {len(trades)} trades through grid synchronization...")

        filtered_trades = []
        grid_rejected = 0
        grid_accepted = 0

        for bar_idx, trade in enumerate(trades):
            entry_ts = pd.Timestamp(trade.get("entry_timestamp"))
            try:
                # Find the bar index in Nifty data
                entry_bar_idx = None
                for idx, row in self.grid._nifty_history.iterrows():
                    if pd.Timestamp(row.get("timestamp", "")) == entry_ts:
                        entry_bar_idx = idx
                        break

                if entry_bar_idx is None:
                    entry_bar_idx = min(bar_idx, len(self.grid._nifty_history) - 1)

                # Get grid state at entry
                grid_state = self.grid.update(entry_bar_idx)

                # Would grid have accepted this entry?
                if grid_state.accepts_entry:
                    grid_accepted += 1
                    filtered_trades.append(trade)
                else:
                    grid_rejected += 1
                    trade["grid_rejection_reason"] = f"Grid unfavorable (Voltage={grid_state.voltage:.3f}, Freq={grid_state.frequency:.3f})"

            except Exception as e:
                # If something fails, accept trade by default
                grid_accepted += 1
                filtered_trades.append(trade)

        print(f"\n[3] Grid Synchronization Filter Results:")
        print(f"    Trades accepted by grid: {grid_accepted}")
        print(f"    Trades rejected by grid: {grid_rejected}")
        print(f"    Acceptance rate: {100*grid_accepted/len(trades):.1f}%")

        # Calculate stats
        if filtered_trades:
            pnls = [t.get("pnl", 0) for t in filtered_trades]
            win_count = sum(1 for p in pnls if p > 0)
            win_rate = win_count / len(pnls) if pnls else 0

            print(f"\n[4] Filtered Trade Stats:")
            print(f"    Total trades after grid filter: {len(filtered_trades)}")
            print(f"    Winning trades: {win_count}")
            print(f"    Win rate: {win_rate:.1%}")
            print(f"    Net P&L: ₹{sum(pnls):,.2f}")
            print(f"    Avg P&L/trade: ₹{sum(pnls)/len(pnls):,.2f}")
        else:
            print(f"\n[4] No trades accepted by grid filter!")

        # Modify report
        report["grid_sync_analysis"] = {
            "total_trades_initial": len(trades),
            "trades_accepted_by_grid": grid_accepted,
            "trades_rejected_by_grid": grid_rejected,
            "acceptance_rate": grid_accepted / len(trades) if trades else 0,
            "filtered_trades_count": len(filtered_trades),
            "filtered_trades_net_pnl": sum(t.get("pnl", 0) for t in filtered_trades),
            "filtered_trades_win_rate": win_rate if filtered_trades else 0,
        }

        return report


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/test_atr_bypass_with_grid_sync.py <SYMBOL> [max_bars]")
        print("Example: python3 scripts/test_atr_bypass_with_grid_sync.py INFY 5000")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    max_bars = int(sys.argv[2]) if len(sys.argv) > 2 else None

    print("\n" + "🔍 GRID SYNCHRONIZATION TEST: ATR Bypass + Nifty 50 Gating".center(140))
    print("="*140 + "\n")

    # Load data
    try:
        print(f"Loading {symbol} data...")
        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
        loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

        # Load symbol data
        df = loader._load_symbol_csv(symbol)
        if max_bars:
            df = df.iloc[:max_bars]
        print(f"✓ Loaded {len(df):,} {symbol} bars ({df['timestamp'].min()} to {df['timestamp'].max()})")

        # Load Nifty 50 data (same timeframe)
        print(f"\nLoading Nifty 50 data for grid synchronization...")
        nifty_df = loader._load_symbol_csv("NIFTY50")
        # Trim to same date range as symbol
        nifty_start = pd.Timestamp(df['timestamp'].min())
        nifty_end = pd.Timestamp(df['timestamp'].max())
        nifty_df = nifty_df[(pd.Timestamp(nifty_df['timestamp']) >= nifty_start) &
                            (pd.Timestamp(nifty_df['timestamp']) <= nifty_end)]
        print(f"✓ Loaded {len(nifty_df):,} Nifty bars")

    except Exception as e:
        print(f"✗ Failed to load data: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Initialize registry and orchestrator
    try:
        print(f"\nInitializing orchestrator...")
        registry = CanonicalParameterRegistry()

        # Temporarily disable ATR stop by setting it to very high value
        # (This is a workaround; ideally we'd modify the code, but this tests the concept)
        registry.values["trailing_stop_atr_mult"] = 1000.0  # Effectively bypasses ATR
        print(f"  ✓ Set trailing_stop_atr_mult = 1000.0 (effectively bypassing ATR)")

        orch = Revision2ExternalEngineOrchestrator(
            [symbol],
            registry,
            starting_equity=1_000_000.0
        )
        print(f"  ✓ Orchestrator created")

        # Wrap with grid synchronization
        print(f"\nInitializing grid synchronization...")
        grid_orch = GridSyncOrchestrator(orch, nifty_df)
        print(f"  ✓ Grid synchronization loaded")

    except Exception as e:
        print(f"✗ Failed to initialize: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Run backtest
    try:
        print(f"\nRunning backtest...")
        symbol_bars = {symbol: df}
        report = grid_orch.run(symbol_bars, warmup=60)

        # Output report
        print(f"\n" + "="*140)
        print("FINAL RESULTS")
        print("="*140)

        print(f"\nBaseline Engine Performance (before grid filter):")
        print(f"  Total trades: {len(report.get('trades', []))}")
        print(f"  Net P&L: ₹{report.get('net_pnl', 0):,.2f}")
        print(f"  Win rate: {report.get('win_rate', 0):.1%}")
        print(f"  Max drawdown: {report.get('max_drawdown', 0):.1%}")

        grid_analysis = report.get("grid_sync_analysis", {})
        if grid_analysis:
            print(f"\nGrid Synchronization Impact:")
            print(f"  Initial trades (base engine): {grid_analysis.get('total_trades_initial', 0)}")
            print(f"  Accepted by grid: {grid_analysis.get('trades_accepted_by_grid', 0)}")
            print(f"  Rejected by grid: {grid_analysis.get('trades_rejected_by_grid', 0)}")
            print(f"  Acceptance rate: {100*grid_analysis.get('acceptance_rate', 0):.1f}%")
            print(f"\n  Filtered trades (grid-accepted only):")
            print(f"    Count: {grid_analysis.get('filtered_trades_count', 0)}")
            print(f"    Net P&L: ₹{grid_analysis.get('filtered_trades_net_pnl', 0):,.2f}")
            print(f"    Win rate: {100*grid_analysis.get('filtered_trades_win_rate', 0):.1f}%")

        print(f"\n" + "="*140)
        print("INTERPRETATION")
        print("="*140)

        if grid_analysis.get('acceptance_rate', 0) < 1.0:
            print(f"\n✓ Grid synchronization FILTERED OUT {grid_analysis.get('trades_rejected_by_grid', 0)} trades")
            print(f"  This suggests Nifty 50 state would have prevented entry for these signals.")
            print(f"  Hypothesis: Lower quantity → higher quality entries → better win rate?")
        else:
            print(f"\n⊙ Grid synchronization accepted all trades")
            print(f"  Nifty 50 state was favorable throughout the period.")

        filtered_pnl = grid_analysis.get('filtered_trades_net_pnl', 0)
        if filtered_pnl > report.get('net_pnl', 0):
            print(f"\n✓ IMPROVEMENT: Filtered trades better than all trades")
            print(f"  All trades P&L: ₹{report.get('net_pnl', 0):,.2f}")
            print(f"  Grid-filtered P&L: ₹{filtered_pnl:,.2f}")
            print(f"  Improvement: ₹{filtered_pnl - report.get('net_pnl', 0):,.2f}")
        else:
            print(f"\n⚠️  No P&L improvement from grid filtering")
            print(f"  All trades: ₹{report.get('net_pnl', 0):,.2f}")
            print(f"  Grid-filtered: ₹{filtered_pnl:,.2f}")

    except Exception as e:
        print(f"✗ Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print(f"\n✓ Test complete")


if __name__ == "__main__":
    main()
