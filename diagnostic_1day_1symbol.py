#!/usr/bin/env python3
"""
1-Day, 1-Symbol Full Engine Test
=================================

Run SUNPHARMA for ONE trading day through the complete orchestrator.
Capture all metrics: signals, approvals, plans, orders, fills, trades, P&L.
"""

import json
import os
from pathlib import Path
from datetime import datetime

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_FOR_ONE_DAY = 390  # 1 trading day = 390 minutes

def run():
    """Run 1-day, 1-symbol test."""

    print()
    print("="*100)
    print("1-DAY, 1-SYMBOL FULL ENGINE TEST")
    print("="*100)
    print()

    # Load manifest
    print(f"[LOAD] Loading {SYMBOL} data...")
    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    if entry is None:
        raise RuntimeError(f"{SYMBOL} not in manifest")

    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename

    # Verify hash
    actual = loader._compute_file_hash(path)
    if actual != entry.sha256:
        raise RuntimeError(f"{SYMBOL} hash mismatch")

    # Load data
    bars = pd.read_csv(path)
    if len(bars) <= WARMUP + BARS_FOR_ONE_DAY:
        raise RuntimeError(f"Insufficient bars")

    # Extract 1 day worth of data (after warmup)
    bars_to_use = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()

    print(f"  ✓ File hash verified: {actual[:16]}...")
    print(f"  ✓ Data range: {bars_to_use.iloc[WARMUP]['timestamp']} to {bars_to_use.iloc[-1]['timestamp']}")
    print(f"  ✓ Bars: {len(bars_to_use)} total ({WARMUP} warmup + {BARS_FOR_ONE_DAY} active)")
    print()

    # Initialize orchestrator with default parameters
    print(f"[INIT] Initializing orchestrator (all 8 steps)...")
    registry = CanonicalParameterRegistry()
    orchestrator = Revision2PortfolioOrchestrator(
        [SYMBOL],
        registry=registry,
        starting_equity=100_000.0,
    )
    print(f"  ✓ Registry hash: {registry.FROZEN_IDENTITY_SHA256[:16]}...")
    print(f"  ✓ Config hash: {orchestrator.config.config_hash[:16]}...")
    print()

    # Run orchestrator
    print(f"[RUN] Running orchestrator on 1 day of {SYMBOL} data...")
    result = orchestrator.run({SYMBOL: bars_to_use}, warmup=WARMUP)
    print()

    # Display results
    print("="*100)
    print("EXECUTION FUNNEL")
    print("="*100)
    print(f"Bars processed:           {result['bars_processed']}")
    print(f"PA signals generated:     {result['pa_signals']}")
    print(f"ID approvals:             {result['id_approvals']}")
    print(f"ID rejections:            {result['id_rejections']}")
    print(f"MPC plans built:          {result['mpc_plans']}")
    print(f"Safety pre-sizing passes: {result['safety_approvals']}")
    print(f"Safety pre-sizing fails:  {result['safety_rejections']}")
    print(f"Orders submitted:         {result['orders_submitted']}")
    print(f"Fills executed:           {result['fills']}")
    print(f"Trades completed:         {result['completed_trades']}")
    print()

    print("="*100)
    print("RECONCILIATION & STATE")
    print("="*100)
    print(f"Open positions:           {len(orchestrator.open_trades)}")
    print(f"Pending entries:          {len(orchestrator.pending_entries)}")
    print(f"Completed trades:         {len(orchestrator.completed_trades)}")
    print(f"Event ledger size:        {len(orchestrator.event_ledger)}")
    print(f"Reconciliation exact:     {result.get('reconciliation_exact', 'N/A')}")
    print(f"Audit chain valid:        {result.get('audit_chain_valid', 'N/A')}")
    print()

    print("="*100)
    print("P&L METRICS")
    print("="*100)
    print(f"Starting equity:          ₹{result['starting_equity']:,.2f}")
    print(f"Ending equity:            ₹{result['ending_equity']:,.2f}")
    print(f"Net P&L:                  ₹{result['net_pnl']:,.2f}")
    print(f"Gross P&L:                ₹{result['gross_pnl']:,.2f}")
    print(f"Costs:                    ₹{result['total_cost']:,.2f}")
    print(f"Return %:                 {(result['ending_equity'] - result['starting_equity']) / result['starting_equity'] * 100:.4f}%")
    print()

    print("="*100)
    print("STATUS")
    print("="*100)
    print(f"Overall status:           {result['status']}")
    print(f"Gate16 violations:        {result['safety_violations']}")
    print()

    # Show trades if any
    if result['completed_trades'] > 0:
        print("="*100)
        print("TRADE DETAILS")
        print("="*100)
        for i, trade in enumerate(result['trades'], 1):
            print(f"\nTrade {i}:")
            print(f"  Symbol:       {trade['symbol']}")
            print(f"  Direction:    {'BUY' if trade['direction'] == 1 else 'SELL'}")
            print(f"  Entry:        {trade['entry_timestamp']} @ ₹{trade['entry_price']:.2f}")
            print(f"  Exit:         {trade['exit_timestamp']} @ ₹{trade['exit_price']:.2f}")
            print(f"  Quantity:     {trade['quantity']}")
            print(f"  Exit reason:  {trade['exit_reason']}")
            print(f"  P&L:          ₹{trade['net_pnl']:,.2f}")
    else:
        print("="*100)
        print("NO TRADES EXECUTED")
        print("="*100)
        print("\nRejection breakdown:")
        if result['id_rejections'] > 0:
            print(f"  ID gates rejected {result['id_rejections']} signals")
        if result['safety_rejections'] > 0:
            print(f"  Safety gates rejected {result['safety_rejections']} plans")
        if result['orders_submitted'] == 0 and result['mpc_plans'] > 0:
            print(f"  {result['mpc_plans']} MPC plans built but 0 orders submitted")
        print()

    print("="*100)
    print("✅ 1-DAY TEST COMPLETE")
    print("="*100)
    print()

    return result


if __name__ == "__main__":
    try:
        result = run()
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
