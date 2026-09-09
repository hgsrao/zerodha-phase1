#!/usr/bin/env python3
"""
Dissect One Losing Trade Through All Gates
===========================================

Step 1: Re-run 1-day SUNPHARMA calibration with parameter saving
Step 2: Extract best candidate parameters
Step 3: Re-run orchestrator with those EXACT parameters
Step 4: Take ONE losing trade and trace it through all 10 boxes
        showing EXACT inputs/outputs at each gate

No guessing. No wrong overrides. Use actual calibrated parameters.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import time

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.calibration_supervisor import (
    CalibrationSupervisor, CalibrationRunConfig, AcceptanceGates
)
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_FOR_ONE_DAY = 390

def load_sunpharma_1day():
    """Load SUNPHARMA for 1 day."""
    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename
    actual = loader._compute_file_hash(path)
    if actual != entry.sha256:
        raise RuntimeError(f"Hash mismatch")

    bars = pd.read_csv(path)
    bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
    return {SYMBOL: bars_subset}

def step1_calibrate():
    """Step 1: Run calibration and extract best parameters."""
    print()
    print("="*120)
    print("STEP 1: RUN CALIBRATION AND EXTRACT BEST PARAMETERS")
    print("="*120)
    print()

    symbol_bars = load_sunpharma_1day()
    registry = CanonicalParameterRegistry()

    gates = AcceptanceGates(
        min_trades=1,
        min_profit_factor=0.90,
        max_drawdown_fraction=0.50,
        require_positive_net_pnl=False,
        max_safety_violations=10,
    )

    run_config = CalibrationRunConfig(
        phase1_trials=8,
        phase2_generations=2,
        phase3_iterations=5,
        wall_clock_budget_seconds=None,
        seed=42,
    )

    print(f"Running calibration...")
    supervisor = CalibrationSupervisor(
        registry=registry,
        symbols=[SYMBOL],
        symbol_bars=symbol_bars,
        run_config=run_config,
        gates=gates,
        warmup=WARMUP,
        starting_equity=100_000.0,
    )

    result = supervisor.run()

    print(f"Candidates evaluated: {len(result.candidates)}")
    print(f"Candidates passed gates: {len([c for c in result.candidates if c.accepted])}")
    print()

    if not result.candidates:
        print("❌ No candidates evaluated")
        sys.exit(1)

    # Get best by guidance score (highest is best, even if negative)
    best_candidate = max(result.candidates, key=lambda c: c.guidance_score)
    print(f"✅ BEST CANDIDATE (by guidance score)")
    print(f"   Phase: {best_candidate.phase}")
    print(f"   Guidance score: {best_candidate.guidance_score:.4f}")
    print(f"   Trades: {best_candidate.metrics.get('completed_trades', 0)}")
    print(f"   P&L: ₹{best_candidate.metrics.get('net_pnl', 0):.2f}")
    print(f"   Params: {len(best_candidate.params)} calibrated")
    print()

    return best_candidate.params, registry

def step2_rerun_with_calibrated(params, registry):
    """Step 2: Re-run orchestrator with calibrated parameters."""
    print()
    print("="*120)
    print("STEP 2: RE-RUN ORCHESTRATOR WITH CALIBRATED PARAMETERS")
    print("="*120)
    print()

    symbol_bars = load_sunpharma_1day()
    symbols = [SYMBOL]

    print(f"Calibration overrides ({len(params)} params):")
    for k, v in sorted(params.items())[:5]:
        print(f"  {k}: {v}")
    if len(params) > 5:
        print(f"  ... and {len(params) - 5} more")
    print()

    orchestrator = Revision2PortfolioOrchestrator(
        symbols,
        registry=registry,
        starting_equity=100_000.0,
        calibration_overrides=params,
    )

    print(f"Running orchestrator with calibrated params...")
    result = orchestrator.run(symbol_bars, warmup=WARMUP)

    print(f"  Trades executed: {result['completed_trades']}")
    print(f"  Net P&L: ₹{result['net_pnl']:.2f}")
    print(f"  Execution funnel:")
    print(f"    PA signals: {result['pa_signals']}")
    print(f"    ID approvals: {result['id_approvals']}")
    print(f"    MPC plans: {result['mpc_plans']}")
    print(f"    Orders: {result['orders_submitted']}")
    print(f"    Fills: {result['fills']}")
    print()

    trades = result.get('trades', [])
    if not trades:
        print("❌ NO TRADES - Cannot dissect")
        sys.exit(1)

    return trades

def step3_dissect_one_trade(trades):
    """Step 3: Pick ONE losing trade and trace through all gates."""
    print()
    print("="*120)
    print("STEP 3: DISSECT ONE LOSING TRADE THROUGH ALL GATES")
    print("="*120)
    print()

    # Find first losing trade
    losing_trade = None
    for t in trades:
        if t.get('net_pnl', 0) < 0:
            losing_trade = t
            break

    if losing_trade is None:
        losing_trade = trades[0]  # Take first if none are losing

    print(f"Trade: {SYMBOL} #{trades.index(losing_trade) + 1}")
    print()

    # BOX-BY-BOX TRACE
    print("[BOX 1] PA SIGNAL GENERATION INPUT")
    print("-" * 120)
    print(f"  Timestamp: {losing_trade.get('entry_timestamp', 'N/A')}")
    print(f"  Symbol: {losing_trade.get('symbol', SYMBOL)}")
    print()

    print("[BOX 1 OUTPUT] PA SIGNAL")
    print("-" * 120)
    print(f"  Direction: {'BUY' if losing_trade['direction'] == 1 else 'SELL'}")
    print(f"  Confidence: {losing_trade.get('confidence', 'N/A'):.4f}")
    print(f"  Quality band: {losing_trade.get('quality_band', 'N/A')}")
    print()

    print("[BOX 2] ID (INTELLIGENT DISCRIMINATION) INPUT")
    print("-" * 120)
    print(f"  PA signal: direction={losing_trade['direction']}, confidence={losing_trade.get('confidence', 'N/A'):.4f}")
    print()

    print("[BOX 2 OUTPUT] ID DECISION")
    print("-" * 120)
    print(f"  Approved: YES ✓ (trade executed means ID approved)")
    print()

    print("[BOX 3] MPC (MARKET PREDICTION + PLAN) INPUT")
    print("-" * 120)
    print(f"  Entry price: ₹{losing_trade['entry_price']:.4f}")
    print(f"  Entry time: {losing_trade['entry_timestamp']}")
    print()

    print("[BOX 3 OUTPUT] MPC PLAN")
    print("-" * 120)
    print(f"  Expected profit target: ₹{losing_trade.get('profit_target', 'N/A'):.4f}")
    print(f"  Stop-loss price: ₹{losing_trade.get('stop_loss_price', losing_trade.get('stop_loss', 'N/A')):.4f}")
    print()

    print("[BOX 4-5] SAFETY GATES + POSITION SIZING INPUT")
    print("-" * 120)
    print(f"  Plan: profit_target=₹{losing_trade.get('profit_target', 'N/A'):.2f}, stop=₹{losing_trade.get('stop_loss', 'N/A'):.2f}")
    print()

    print("[BOX 4-5 OUTPUT] POSITION SIZING")
    print("-" * 120)
    print(f"  Quantity approved: {losing_trade.get('quantity', 'N/A')} units")
    print(f"  Exposure: {losing_trade.get('exposure_pct', 'N/A')}%")
    print()

    print("[BOX 6] ORDER SUBMISSION INPUT")
    print("-" * 120)
    print(f"  Quantity: {losing_trade.get('quantity', 'N/A')}")
    print(f"  Direction: {'BUY' if losing_trade['direction'] == 1 else 'SELL'}")
    print()

    print("[BOX 6 OUTPUT] ORDER")
    print("-" * 120)
    print(f"  Order submitted: YES ✓")
    print()

    print("[BOX 7] BROKER FILL (ENTRY) INPUT")
    print("-" * 120)
    print(f"  Order: {losing_trade.get('quantity', 'N/A')} {'BUY' if losing_trade['direction'] == 1 else 'SELL'}")
    print()

    print("[BOX 7 OUTPUT] ENTRY FILL")
    print("-" * 120)
    print(f"  Entry price: ₹{losing_trade['entry_price']:.4f}")
    print(f"  Entry slippage: ₹{losing_trade.get('entry_slippage', 0):.4f}")
    print()

    print("[BOX 8] POSITION MANAGER TRACKING")
    print("-" * 120)
    print(f"  Position opened: {losing_trade['entry_timestamp']}")
    print(f"  Quantity held: {losing_trade.get('quantity', 'N/A')}")
    print()

    print("[BOX 9] EXIT CONTROLLER INPUT")
    print("-" * 120)
    print(f"  Current price: ₹{losing_trade['exit_price']:.4f}")
    print(f"  Profit target: ₹{losing_trade.get('profit_target', 'N/A'):.4f}")
    print(f"  Stop-loss: ₹{losing_trade.get('stop_loss', 'N/A'):.4f}")
    print(f"  Bars held: {losing_trade.get('bars_held', 'N/A')}")
    print()

    print("[BOX 9 OUTPUT] EXIT SIGNAL")
    print("-" * 120)
    print(f"  Exit reason: {losing_trade.get('exit_reason', 'N/A')}")
    print()

    print("[BOX 10] BROKER FILL (EXIT) INPUT")
    print("-" * 120)
    print(f"  Position: {losing_trade.get('quantity', 'N/A')} units at ₹{losing_trade['entry_price']:.4f}")
    print(f"  Exit signal: {losing_trade.get('exit_reason', 'N/A')}")
    print()

    print("[BOX 10 OUTPUT] EXIT FILL")
    print("-" * 120)
    print(f"  Exit price: ₹{losing_trade['exit_price']:.4f}")
    print(f"  Exit slippage: ₹{losing_trade.get('exit_slippage', 0):.4f}")
    print()

    print("[TRADE COMPLETION] P&L CALCULATION")
    print("="*120)
    entry = losing_trade['entry_price']
    exit_p = losing_trade['exit_price']
    qty = losing_trade.get('quantity', 0)
    gross = losing_trade.get('pnl', 0)
    cost = losing_trade.get('transaction_cost', 0)
    net = losing_trade.get('net_pnl', 0)

    print()
    print(f"Entry: ₹{entry:.4f} × {qty} = ₹{entry*qty:.2f}")
    print(f"Exit:  ₹{exit_p:.4f} × {qty} = ₹{exit_p*qty:.2f}")
    print()
    print(f"Gross P&L (price move):  ₹{gross:.2f}")
    print(f"Transaction costs:       ₹{cost:.2f}")
    print(f"NET P&L:                 ₹{net:.2f}")
    print()

    if net < 0:
        print("❌ LOSS")
        if gross > 0:
            print(f"   Price moved favorably (+₹{gross:.2f}) but costs (₹{cost:.2f}) exceeded profit")
            print(f"   Costs were {cost/abs(gross)*100:.1f}% of gross profit")
        else:
            print(f"   Price moved against position (-₹{abs(gross):.2f})")
            print(f"   Exit reason: {losing_trade.get('exit_reason', 'N/A')}")
    else:
        print("✅ PROFIT")

    print()

def main():
    print()
    print("╔" + "="*118 + "╗")
    print("║" + " "*30 + "DISSECT ONE LOSING TRADE" + " "*64 + "║")
    print("║" + " "*20 + "Using ACTUAL Calibrated Parameters (No Guessing)" + " "*51 + "║")
    print("╚" + "="*118 + "╝")

    # Step 1: Calibrate and get best parameters
    best_params, registry = step1_calibrate()

    # Step 2: Re-run with calibrated parameters
    trades = step2_rerun_with_calibrated(best_params, registry)

    # Step 3: Dissect one losing trade
    step3_dissect_one_trade(trades)

    print()
    print("="*120)
    print("✅ DISSECTION COMPLETE")
    print("="*120)
    print()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
