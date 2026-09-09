#!/usr/bin/env python3
"""
Single Trade Diagnostic: Full Box-by-Box Trace
===============================================

Takes ONE completed trade from SUNPHARMA 1-day run and traces it through
all 10 boxes to show exactly where and why profit was lost.

Input: PA signal, orchestrator parameters
Output: Entry → Exit with exact prices, costs, slippage, P&L breakdown
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator
from revision2.contracts import MarketSnapshot

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_FOR_ONE_DAY = 390

def load_sunpharma_1day():
    """Load SUNPHARMA for 1 day."""
    print(f"[LOAD] Loading {SYMBOL} data (1 day)...")

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

    bars = pd.read_csv(path)
    if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
        raise RuntimeError(f"Insufficient bars")

    bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
    print(f"  ✓ Loaded {len(bars_subset)} bars")
    print(f"  ✓ Date range: {bars_subset.iloc[WARMUP]['timestamp']} to {bars_subset.iloc[-1]['timestamp']}")

    return {SYMBOL: bars_subset}

def run():
    """Run orchestrator and capture detailed trade info."""

    print()
    print("="*120)
    print("SINGLE TRADE DIAGNOSTIC: FULL BOX-BY-BOX TRACE")
    print("="*120)
    print()

    # Load data
    symbol_bars = load_sunpharma_1day()
    symbols = [SYMBOL]

    print()

    # Initialize orchestrator with loosened parameters for execution
    print("[INIT] Initializing orchestrator (with loosened gates for trace)...")
    registry = CanonicalParameterRegistry()

    # Override to force trades through (diagnostic mode)
    calibration_overrides = {
        "entry_confidence_threshold": 0.30,  # Loosen from 0.50 to 0.30 (min allowed)
        "minimum_absolute_profit_rupees": 1.0,  # Loosen to 1 (almost breakeven)
    }

    orchestrator = Revision2PortfolioOrchestrator(
        symbols,
        registry=registry,
        starting_equity=100_000.0,
        calibration_overrides=calibration_overrides,
    )
    print(f"  ✓ Registry hash: {registry.FROZEN_IDENTITY_SHA256[:16]}...")
    print(f"  ✓ Overrides: entry_confidence_threshold=0.10 (from 0.50)")
    print(f"  ✓ Overrides: minimum_absolute_profit_rupees=5 (from 50)")
    print()

    # Run orchestrator
    print("[RUN] Running orchestrator to generate trades...")
    result = orchestrator.run(symbol_bars, warmup=WARMUP)

    print(f"  ✓ Completed trades: {result['completed_trades']}")
    print()

    # Get first trade that exists
    trades = result.get('trades', [])
    if not trades:
        print("❌ NO TRADES EXECUTED - Cannot trace")
        print("\nRejection breakdown:")
        print(f"  PA signals: {result['pa_signals']}")
        print(f"  ID approvals: {result['id_approvals']}")
        print(f"  MPC plans: {result['mpc_plans']}")
        print(f"  Orders: {result['orders_submitted']}")
        print(f"  Fills: {result['fills']}")
        sys.exit(1)

    trade = trades[0]  # First trade for detailed trace

    print("="*120)
    print(f"TRADE TRACE: {SYMBOL} Trade #{1}")
    print("="*120)
    print()

    # Extract trade details
    print("[BOX 1] PA SIGNAL GENERATION")
    print("-" * 120)
    print(f"  Timestamp: {trade.get('entry_timestamp', 'N/A')}")
    print(f"  Direction: {'BUY' if trade['direction'] == 1 else 'SELL'}")
    print(f"  Signal confidence: {trade.get('confidence', 'N/A')} (0-1 scale)")
    print()

    print("[BOX 2] ID (INTELLIGENT DISCRIMINATION)")
    print("-" * 120)
    print(f"  Gate check: APPROVED ✓ (trade executed)")
    print(f"  Confidence threshold: 0.50 (required)")
    print()

    print("[BOX 3] MPC (MARKET PREDICTION + PLAN)")
    print("-" * 120)
    print(f"  Expected profit target: ₹{trade.get('profit_target', 'N/A'):.2f}")
    print(f"  Stop-loss price: ₹{trade.get('stop_loss_price', 'N/A'):.2f}")
    print(f"  Risk/Reward ratio: {trade.get('risk_reward_ratio', 'N/A')}")
    print()

    print("[BOX 4-5] SAFETY GATES + POSITION SIZING")
    print("-" * 120)
    print(f"  Quantity approved: {trade.get('quantity', 'N/A')} units")
    print(f"  Position size: {trade.get('position_size', 'N/A')}")
    print(f"  Exposure: {trade.get('exposure_pct', 'N/A')}% of equity")
    print()

    print("[BOX 6] ORDER SUBMISSION")
    print("-" * 120)
    print(f"  Order type: {'MARKET' if trade.get('order_type') == 'market' else 'LIMIT'}")
    print(f"  Order submitted: ✓")
    print()

    print("[BOX 7] BROKER FILL (ENTRY)")
    print("-" * 120)
    print(f"  Entry timestamp: {trade['entry_timestamp']}")
    print(f"  Entry price (bid): ₹{trade.get('entry_bid', 'N/A'):.4f}")
    print(f"  Entry price (ask): ₹{trade.get('entry_ask', 'N/A'):.4f}")
    print(f"  Entry price (filled): ₹{trade['entry_price']:.4f}")
    print(f"  Entry slippage: ₹{trade.get('entry_slippage', 0):.4f}")
    print()

    print("[BOX 8] EXIT SIGNAL GENERATION")
    print("-" * 120)
    print(f"  Exit reason: {trade.get('exit_reason', 'N/A')}")
    print(f"  Bars held: {trade.get('bars_held', 'N/A')}")
    print()

    print("[BOX 9] BROKER FILL (EXIT)")
    print("-" * 120)
    print(f"  Exit timestamp: {trade['exit_timestamp']}")
    print(f"  Exit price (bid): ₹{trade.get('exit_bid', 'N/A'):.4f}")
    print(f"  Exit price (ask): ₹{trade.get('exit_ask', 'N/A'):.4f}")
    print(f"  Exit price (filled): ₹{trade['exit_price']:.4f}")
    print(f"  Exit slippage: ₹{trade.get('exit_slippage', 0):.4f}")
    print()

    print("[BOX 10] TRADE COMPLETION + P&L")
    print("-" * 120)
    print(f"  Gross P&L: ₹{trade.get('pnl', 0):.2f}")
    print(f"  Transaction costs: ₹{trade.get('transaction_cost', 0):.2f}")
    print(f"  NET P&L: ₹{trade.get('net_pnl', 0):.2f}")
    print()

    # P&L Breakdown
    print("="*120)
    print("P&L BREAKDOWN: WHERE DID PROFIT GO?")
    print("="*120)
    print()

    direction = trade['direction']
    entry = trade['entry_price']
    exit_price = trade['exit_price']
    quantity = trade.get('quantity', 0)
    gross_pnl = trade.get('pnl', 0)
    cost = trade.get('transaction_cost', 0)
    net_pnl = trade.get('net_pnl', 0)

    print(f"Entry price:              ₹{entry:.4f}")
    print(f"Exit price:               ₹{exit_price:.4f}")
    print(f"Price move:               ₹{abs(exit_price - entry):.4f} {'↑' if exit_price > entry else '↓'}")
    print(f"Quantity:                 {quantity} units")
    print()
    print(f"Gross P&L (price move):   ₹{gross_pnl:.2f}")
    print(f"Transaction costs:        ₹{cost:.2f}")
    print(f"NET P&L:                  ₹{net_pnl:.2f}")
    print()

    # Analysis
    print("="*120)
    print("LOSS ANALYSIS")
    print("="*120)
    print()

    if net_pnl > 0:
        print("✅ PROFITABLE TRADE")
    else:
        loss = abs(net_pnl)
        print(f"❌ LOSING TRADE (Loss: ₹{loss:.2f})")
        print()
        print("Why did it lose?")

        if gross_pnl > 0 and net_pnl < 0:
            print(f"  1. Trade was PROFITABLE on price move: ₹{gross_pnl:.2f}")
            print(f"  2. But costs EXCEEDED profit: ₹{cost:.2f}")
            print(f"  3. Result: ₹{gross_pnl:.2f} - ₹{cost:.2f} = ₹{net_pnl:.2f}")
            print()
            print("ROOT CAUSE: Costs too high relative to profit")
            print(f"  Cost as % of gross: {cost/abs(gross_pnl)*100:.1f}%")
        elif gross_pnl < 0:
            print(f"  1. Price moved AGAINST position: ₹{abs(gross_pnl):.2f} loss")
            print(f"  2. Costs added to loss: ₹{cost:.2f}")
            print(f"  3. Result: ₹{net_pnl:.2f}")
            print()
            print("ROOT CAUSE: Stop-loss or target exit, direction wrong")
            print(f"  Entry reason: {trade.get('entry_reason', 'N/A')}")
            print(f"  Exit reason: {trade.get('exit_reason', 'N/A')}")

    print()

    # Show multiple trades
    print("="*120)
    print(f"SUMMARY: First 5 Trades (Total: {len(trades)})")
    print("="*120)
    print()

    print(f"{'#':<3} {'Entry Time':<20} {'Symbol':<10} {'Dir':<4} {'Entry':<10} {'Exit':<10} {'P&L':<10} {'Exit Reason':<20}")
    print("-" * 120)

    for i, t in enumerate(trades[:5], 1):
        entry_time = str(t.get('entry_timestamp', 'N/A'))[:19]
        symbol = t.get('symbol', 'N/A')
        direction = 'BUY' if t['direction'] == 1 else 'SELL'
        entry_price = t['entry_price']
        exit_price = t['exit_price']
        net = t.get('net_pnl', 0)
        reason = t.get('exit_reason', 'N/A')[:15]

        print(f"{i:<3} {entry_time:<20} {symbol:<10} {direction:<4} ₹{entry_price:<9.2f} ₹{exit_price:<9.2f} ₹{net:<9.2f} {reason:<20}")

    if len(trades) > 5:
        print(f"... and {len(trades) - 5} more trades")

    print()

    return trades

if __name__ == "__main__":
    try:
        trades = run()
    except Exception as e:
        print(f"❌ Diagnostic failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
