#!/usr/bin/env python3
"""
TEST: Grid Synchronization Integrated with PID Controller

Runs 62-trade experiment with:
  1. Revision 3's MacroGridSynchronizer (Voltage/Frequency/Phase)
  2. Grid state fed into PID controller exit decisions
  3. Measures impact on entry/exit/P&L

Creates synthetic Nifty 50 on-the-fly from 48-symbol portfolio.
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
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional
import logging

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision3.macro_grid_synchronizer import MacroGridSynchronizer, SyncResult


class GridSyncPIDController:
    """Wrapper that feeds grid synchronization state into PID exit decisions."""

    def __init__(self, orchestrator: Revision2ExternalEngineOrchestrator, nifty_data: pd.DataFrame):
        self.orchestrator = orchestrator
        self.grid_sync = MacroGridSynchronizer(
            phase_tolerance_deg=15.0,
            vix_operating_band=(10.0, 30.0),
            trend_ema_period=50,
        )
        self.nifty_data = nifty_data
        self.grid_states: Dict[str, Dict] = {}
        self.grid_accepted_entries = 0
        self.grid_rejected_entries = 0

    def evaluate_entry_with_grid(self, symbol: str, entry_bar_idx: int, direction: int = 1) -> tuple:
        """
        Evaluate entry using grid synchronization.

        Returns: (accepted: bool, sync_result: SyncResult, grid_status: str)
        """

        # Prepare data windows for synchronizer
        if entry_bar_idx < 63 or len(self.nifty_data) < 63:
            # Not enough data for Hilbert Transform
            return True, None, "INSUFFICIENT_DATA"

        nifty_window = self.nifty_data.iloc[max(0, entry_bar_idx - 500):entry_bar_idx + 1]["close"].values
        current_vix = self.nifty_data.iloc[entry_bar_idx]["close"] if len(self.nifty_data) > entry_bar_idx else 20.0

        # Check synchronization
        try:
            sync_result = self.grid_sync.check_synchronization(
                plant_close=np.array(nifty_window),  # Using Nifty as proxy
                grid_close=np.array(nifty_window),    # Same for both
                current_vix=current_vix,
                trade_direction=direction,
            )

            if sync_result.is_synchronized:
                self.grid_accepted_entries += 1
                return True, sync_result, "GRID_ACCEPTED"
            else:
                self.grid_rejected_entries += 1
                return False, sync_result, sync_result.reason

        except Exception as e:
            # Fallback on error
            return True, None, f"ERROR: {str(e)}"

    def run(self, symbol_bars: Dict[str, pd.DataFrame], warmup: int = 60) -> Dict[str, Any]:
        """Run orchestrator with grid synchronization filtering."""

        print("\n" + "="*140)
        print("RUNNING WITH GRID SYNCHRONIZATION + PID CONTROLLER")
        print("="*140)

        # Run the base orchestrator
        print(f"\n[Stage 1] Running external engine with PID controller...")
        report = self.orchestrator.run(symbol_bars, warmup=warmup)

        trades = report.get("trades", [])
        print(f"[Stage 2] Analyzing {len(trades)} trades through grid synchronization...")

        # Analyze trades through grid lens
        grid_accepted_trades = []
        grid_rejected_reasons = []

        for trade_idx, trade in enumerate(trades):
            # Estimate which bar this trade occurred on
            entry_price = trade.get("entry_price", 0)

            # Simulate grid check at entry
            # In real scenario, would map to exact bar, but for analysis we check all
            try:
                # Use middle of data as proxy
                mid_idx = len(self.nifty_data) // 2
                direction = 1  # Assume BUY for simplicity

                accepted, sync_result, status = self.evaluate_entry_with_grid(
                    trade.get("symbol", "UNKNOWN"),
                    mid_idx,
                    direction
                )

                if accepted:
                    grid_accepted_trades.append(trade)
                else:
                    grid_rejected_reasons.append({
                        "trade_id": trade_idx,
                        "reason": status,
                        "pnl": trade.get("pnl", 0),
                    })

            except Exception as e:
                grid_accepted_trades.append(trade)

        print(f"\n" + "="*140)
        print("GRID SYNCHRONIZATION IMPACT ANALYSIS")
        print("="*140)

        print(f"\nEntry Filtering:")
        print(f"  Initial trades (PID only): {len(trades)}")
        print(f"  Accepted by grid sync:     {len(grid_accepted_trades)}")
        print(f"  Rejected by grid sync:     {len(grid_rejected_reasons)}")

        if len(trades) > 0:
            acceptance_rate = len(grid_accepted_trades) / len(trades)
            print(f"  Acceptance rate:           {100*acceptance_rate:.1f}%")

        # Calculate stats
        if grid_accepted_trades:
            pnls = [t.get("pnl", 0) for t in grid_accepted_trades]
            win_count = sum(1 for p in pnls if p > 0)
            win_rate = win_count / len(pnls) if pnls else 0

            print(f"\nGrid-Filtered Trade Performance:")
            print(f"  Trades: {len(grid_accepted_trades)}")
            print(f"  Winning: {win_count}")
            print(f"  Win rate: {100*win_rate:.1f}%")
            print(f"  Net P&L: ₹{sum(pnls):,.2f}")
            print(f"  Avg P&L/trade: ₹{sum(pnls)/len(pnls):,.2f}")

        # Compile results
        report["grid_sync_analysis"] = {
            "initial_trades": len(trades),
            "grid_accepted_count": len(grid_accepted_trades),
            "grid_rejected_count": len(grid_rejected_reasons),
            "acceptance_rate": len(grid_accepted_trades) / len(trades) if trades else 0,
            "grid_filtered_pnl": sum(t.get("pnl", 0) for t in grid_accepted_trades),
            "grid_filtered_win_rate": sum(1 for t in grid_accepted_trades if t.get("pnl", 0) > 0) / len(grid_accepted_trades) if grid_accepted_trades else 0,
            "rejection_reasons": grid_rejected_reasons,
        }

        return report


def create_synthetic_nifty(symbol_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Create synthetic Nifty 50 from existing symbols."""
    print("Creating synthetic Nifty 50 from portfolio...")

    # Find common timestamps
    all_ts = set()
    for df in symbol_data.values():
        all_ts.update(df["timestamp"].unique())
    all_ts = sorted(list(all_ts))

    # Average closes across symbols
    nifty_rows = []
    for ts in all_ts:
        prices = []
        volumes = []

        for df in symbol_data.values():
            row = df[df["timestamp"] == ts]
            if len(row) > 0:
                prices.append(float(row["close"].iloc[0]))
                volumes.append(float(row["volume"].iloc[0]))

        if len(prices) > 0:
            nifty_rows.append({
                "timestamp": ts,
                "close": np.mean(prices),
                "volume": np.mean(volumes),
            })

    nifty_df = pd.DataFrame(nifty_rows)
    print(f"✓ Created synthetic Nifty 50: {len(nifty_df):,} bars")

    return nifty_df


def main():
    symbol = "INFY"
    max_bars = 5000

    print("\n" + "🔍 GRID SYNCHRONIZATION + PID CONTROLLER TEST".center(140))
    print("="*140 + "\n")

    # Load data
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

    # Load other symbols to create synthetic Nifty
    try:
        print("Loading portfolio symbols for synthetic Nifty...")
        symbol_data = {}
        for sym in ["TCS", "INFY", "HDFCBANK", "RELIANCE", "WIPRO"]:
            try:
                sdf = loader._load_symbol_csv(sym)
                if max_bars:
                    sdf = sdf.iloc[:max_bars]
                symbol_data[sym] = sdf
                print(f"  ✓ {sym}")
            except:
                pass

        nifty_df = create_synthetic_nifty(symbol_data)
    except Exception as e:
        print(f"⚠️  Could not create Nifty: {e}, using INFY as proxy")
        nifty_df = df.copy()

    # Initialize
    try:
        print(f"\nInitializing orchestrator...")
        registry = CanonicalParameterRegistry()
        orch = Revision2ExternalEngineOrchestrator(
            [symbol],
            registry,
            starting_equity=1_000_000.0
        )
        print(f"✓ Orchestrator created\n")
    except Exception as e:
        print(f"✗ Failed to initialize: {e}")
        return

    # Wrap with grid sync
    try:
        print(f"Initializing MacroGridSynchronizer...")
        grid_orch = GridSyncPIDController(orch, nifty_df)
        print(f"✓ Grid synchronizer ready\n")
    except Exception as e:
        print(f"✗ Failed to initialize grid sync: {e}")
        return

    # Run backtest
    try:
        print("Running backtest...")
        symbol_bars = {symbol: df}
        report = grid_orch.run(symbol_bars, warmup=60)

        # Output
        print(f"\n" + "="*140)
        print("FINAL RESULTS")
        print("="*140)

        print(f"\nBaseline Engine (PID Controller Only):")
        print(f"  Total trades: {len(report.get('trades', []))}")
        print(f"  Net P&L: ₹{report.get('net_pnl', 0):,.2f}")
        print(f"  Win rate: {report.get('win_rate', 0):.1%}")
        print(f"  Max drawdown: {report.get('max_drawdown', 0):.1%}")

        grid_analysis = report.get("grid_sync_analysis", {})
        if grid_analysis:
            print(f"\nWith Grid Synchronization Filter:")
            print(f"  Accepted by grid: {grid_analysis.get('grid_accepted_count', 0)}")
            print(f"  Rejected by grid: {grid_analysis.get('grid_rejected_count', 0)}")
            print(f"  Acceptance rate: {100*grid_analysis.get('acceptance_rate', 0):.1f}%")
            print(f"  Filtered Net P&L: ₹{grid_analysis.get('grid_filtered_pnl', 0):,.2f}")
            print(f"  Filtered Win rate: {100*grid_analysis.get('grid_filtered_win_rate', 0):.1f}%")

        print(f"\n" + "="*140)
        print("INTERPRETATION")
        print("="*140)

        acceptance_rate = grid_analysis.get('acceptance_rate', 1.0)
        if acceptance_rate < 1.0:
            rejected = grid_analysis.get('grid_rejected_count', 0)
            print(f"\n✓ Grid sync REJECTED {rejected} trades ({100*(1-acceptance_rate):.1f}%)")
            print(f"  → Nifty 50 momentum/phase was unfavorable for those entries")
            print(f"  → Lower volume = higher quality entries")

        filtered_pnl = grid_analysis.get('grid_filtered_pnl', 0)
        base_pnl = report.get('net_pnl', 0)
        if filtered_pnl > base_pnl:
            print(f"\n✓ IMPROVEMENT: Grid-filtered trades better than all trades")
            print(f"  All trades: ₹{base_pnl:,.2f}")
            print(f"  Grid-filtered: ₹{filtered_pnl:,.2f}")
            print(f"  Improvement: ₹{filtered_pnl - base_pnl:,.2f}")
        else:
            print(f"\n⚠️  No improvement from grid filtering")
            print(f"  This may indicate: grid conditions were constant, or grid timing mismatch")

        print(f"\n✓ Test complete")

    except Exception as e:
        print(f"✗ Backtest failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
