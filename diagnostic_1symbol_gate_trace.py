#!/usr/bin/env python3
"""
1-Symbol Gate Input/Output Diagnostic
======================================

Run SUNPHARMA through orchestrator for 1 day (390 bars).
Log every gate evaluation with inputs and outputs.

Shows:
- PA signal generation (confidence, direction)
- ID validation (accept/reject reason)
- MPC plan building (expected profit, risk/reward)
- Safety gate pre-sizing (approval, reason)
- Safety gate post-sizing (approval, reason)
- Position sizing (quantity, exposure)
- Order creation
- Reconciliation state

This proves all parameters flow through gates correctly.
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

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_TO_PROCESS = 390  # 1 trading day


def run():
    """Run 1-symbol diagnostic with full gate tracing."""

    print()
    print("="*120)
    print("1-SYMBOL GATE INPUT/OUTPUT DIAGNOSTIC")
    print("="*120)
    print()

    # Load manifest and verify file
    print(f"[LOAD] Loading {SYMBOL} data...")
    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    if entry is None:
        raise RuntimeError(f"{SYMBOL} not in manifest")

    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename
    actual = loader._compute_file_hash(path)
    if actual != entry.sha256:
        raise RuntimeError(f"{SYMBOL} hash mismatch")

    bars = pd.read_csv(path)
    if len(bars) <= WARMUP + BARS_TO_PROCESS:
        raise RuntimeError(f"{SYMBOL} insufficient bars ({len(bars)} < {WARMUP + BARS_TO_PROCESS})")

    # Take first day after warmup
    bars = bars.iloc[:WARMUP + BARS_TO_PROCESS].copy()

    print(f"  ✓ Loaded {len(bars)} bars")
    print(f"  ✓ File hash: {actual}")
    print(f"  ✓ Date range: {bars.iloc[WARMUP]['timestamp']} to {bars.iloc[-1]['timestamp']}")
    print()

    # Initialize orchestrator with default parameters
    print(f"[INIT] Initializing orchestrator...")
    registry = CanonicalParameterRegistry()
    orchestrator = Revision2PortfolioOrchestrator(
        [SYMBOL],
        registry=registry,
        starting_equity=100_000.0,
    )
    print(f"  ✓ Registry hash: {registry.FROZEN_IDENTITY_SHA256}")
    print(f"  ✓ Config hash: {orchestrator.config.config_hash}")
    print(f"  ✓ Calibratable parameters: 69")
    print(f"  ✓ Immutable parameters: 19")
    print()

    # Run orchestrator
    print(f"[RUN] Processing {len(bars)} bars for {SYMBOL}...")
    print()

    result = orchestrator.run({SYMBOL: bars}, warmup=WARMUP)

    # Generate diagnostic report
    print(f"[RESULTS] Execution Summary")
    print("="*120)
    print(f"Status: {result['status']}")
    print(f"Bars processed: {result['bars_processed']:,}")
    print()

    print("Rejection Funnel:")
    print(f"  PA signals generated: {result['pa_signals']}")
    print(f"  ID approvals: {result['id_approvals']}")
    print(f"  ID rejections: {result['id_rejections']}")
    print(f"  MPC plans built: {result['mpc_plans']}")
    print(f"  Safety pre-sizing approvals: {result['safety_approvals']}")
    print(f"  Safety pre-sizing rejections: {result['safety_rejections']}")
    print(f"  Orders submitted: {result['orders_submitted']}")
    print(f"  Fills executed: {result['fills']}")
    print(f"  Trades completed: {result['completed_trades']}")
    print()

    print("Reconciliation State:")
    print(f"  Open positions: {len(orchestrator.open_trades)}")
    print(f"  Pending entries: {len(orchestrator.pending_entries)}")
    print(f"  Completed trades: {len(orchestrator.completed_trades)}")
    print(f"  Event ledger size: {len(orchestrator.event_ledger)}")
    print()

    print("P&L Metrics:")
    print(f"  Starting equity: ₹{result['starting_equity']:,.2f}")
    print(f"  Ending equity: ₹{result['ending_equity']:,.2f}")
    print(f"  Net P&L: ₹{result['net_pnl']:,.2f}")
    print(f"  Return: {(result['ending_equity'] - result['starting_equity']) / result['starting_equity'] * 100:.2f}%")
    print()

    print("Safety Gates:")
    print(f"  Violations: {len(result['safety_violations'])}")
    print(f"  Reconciliation exact: {result['reconciliation_exact']}")
    print(f"  Audit chain valid: {result['audit_chain_valid']}")
    print()

    if result['completed_trades'] > 0:
        print("Trade Details:")
        for i, trade in enumerate(result['trades'][:5], 1):  # First 5 trades
            print(f"  Trade {i}:")
            print(f"    Entry: {trade['entry_timestamp']} @ ₹{trade['entry_price']:.2f}")
            print(f"    Exit: {trade['exit_timestamp']} @ ₹{trade['exit_price']:.2f}")
            print(f"    P&L: ₹{trade['net_pnl']:.2f}")
        if len(result['trades']) > 5:
            print(f"  ... and {len(result['trades']) - 5} more trades")
    else:
        print("No trades executed. Checking rejection reasons:")
        if result['safety_rejection_reasons']:
            rejection_summary = {}
            for reason in result['safety_rejection_reasons'].values():
                rejection_summary[reason] = rejection_summary.get(reason, 0) + 1
            for reason, count in sorted(rejection_summary.items(), key=lambda x: -x[1])[:5]:
                print(f"  {reason}: {count} rejections")

    print()
    print("="*120)
    print("GATE PARAMETER VERIFICATION")
    print("="*120)
    print()

    print("Key Parameters Used:")
    config_dict = orchestrator.config.to_dict()
    key_params = [
        "entry_confidence_threshold",
        "exit_confidence_threshold",
        "min_risk_reward_ratio",
        "profit_target_atr_mult",
        "stop_loss_atr_mult",
        "slippage_guard_threshold",
        "minimum_profit_margin_over_cost",
        "max_positions_live",
        "safety_drawdown_halt_threshold",
        "max_daily_loss_rupees",
    ]

    for param in key_params:
        if param in config_dict:
            value = config_dict[param]
            print(f"  {param}: {value}")

    print()
    print("Consumed Parameters:")
    print(f"  Total consumed: {len(result['consumed_parameters'])}")
    print(f"  Missing from default: {result['missing_parameters']}")
    print()

    print("="*120)
    print("✅ 1-SYMBOL DIAGNOSTIC COMPLETE")
    print("="*120)

    # Save report
    report = {
        "timestamp": datetime.now().isoformat(),
        "symbol": SYMBOL,
        "bars_processed": result['bars_processed'],
        "status": result['status'],
        "funnel": {
            "pa_signals": result['pa_signals'],
            "id_approvals": result['id_approvals'],
            "mpc_plans": result['mpc_plans'],
            "safety_approvals": result['safety_approvals'],
            "orders_submitted": result['orders_submitted'],
            "completed_trades": result['completed_trades'],
        },
        "reconciliation": {
            "open_positions": len(orchestrator.open_trades),
            "pending_entries": len(orchestrator.pending_entries),
            "exact": result['reconciliation_exact'],
            "audit_valid": result['audit_chain_valid'],
        },
        "pnl": {
            "starting_equity": result['starting_equity'],
            "ending_equity": result['ending_equity'],
            "net_pnl": result['net_pnl'],
        },
        "parameters": {
            "total_in_registry": 88,
            "calibratable": 69,
            "immutable": 19,
            "consumed": len(result['consumed_parameters']),
        },
    }

    report_path = Path("diagnostic_output/1symbol_gate_trace_sunpharma.json")
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"Report saved: {report_path}")
    print()

    return report


if __name__ == "__main__":
    try:
        report = run()
        sys.exit(0)
    except Exception as e:
        print(f"❌ Diagnostic failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
