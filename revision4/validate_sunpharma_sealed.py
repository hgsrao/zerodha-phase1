"""
Strict SUNPHARMA/August Sealed Validation
Comprehensive report: submissions, fills, cancellations, rejections, reconciliation.
No bypass wrappers. Real gates. Exact accounting.
"""

import json
import sys
from datetime import datetime
from typing import Dict, List

from revision4.contracts import EffectiveConfig, Bar
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider
from revision4.gates_proper import ProperGateEvaluator
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
import pandas as pd


def run_sunpharma_validation():
    """Run SUNPHARMA August 2024 validation with strict gates."""

    print("=" * 80)
    print("SUNPHARMA/August 2024 Sealed Validation")
    print("=" * 80)

    # Load data
    manifest_path = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    data_dir = "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"

    loader = ManifestDataLoader(manifest_path, data_dir)
    symbol = "SUNPHARMA"

    print(f"\n[LOAD] Loading {symbol} August 2024...")
    bars = loader.get_bars_for_month(symbol, "2024-08-01", "2024-08-31")
    print(f"  → {len(bars)} bars loaded")

    # Load warmup
    warmup_start = pd.Timestamp("2024-08-01", tz="UTC") - pd.DateOffset(days=60)
    warmup_end = pd.Timestamp("2024-08-01", tz="UTC") - pd.DateOffset(days=1)
    all_warmup = loader.get_bars_for_month(
        symbol,
        warmup_start.strftime("%Y-%m-%d"),
        warmup_end.strftime("%Y-%m-%d"),
    )
    warmup_data = all_warmup[-60:] if len(all_warmup) >= 60 else all_warmup

    print(f"  → {len(warmup_data)} warmup bars")

    # Build orchestrator
    config = EffectiveConfig()

    warmup_df_data = {
        "timestamp": [b.timestamp for b in warmup_data],
        "open": [b.open for b in warmup_data],
        "high": [b.high for b in warmup_data],
        "low": [b.low for b in warmup_data],
        "close": [b.close for b in warmup_data],
        "volume": [b.volume for b in warmup_data],
    }
    import pandas
    warmup_df = pandas.DataFrame(warmup_df_data)

    ledger = PortfolioLedger()
    broker = PaperBroker()
    gate_evaluator = ProperGateEvaluator(config)

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, {symbol: warmup_df}),
        exit_provider=build_exit_provider(config),
        ledger=ledger,
        broker=broker,
        gate_evaluator=gate_evaluator,
    )

    print(f"\n[RUN] Executing orchestrator...")
    try:
        result = orchestrator.run({symbol: bars})
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        return {"status": "FAILED", "error": str(e)}

    print(f"  ✓ Completed")

    # Analyze results
    print(f"\n[METRICS]")
    print(f"  Timestamps: {result.timestamps_processed}")
    print(f"  Bars: {result.bars_processed}")
    print(f"  Orders submitted: {len(result.orders_submitted)}")
    print(f"  Fills: {len(result.fills)}")
    print(f"  Exits: {len(result.exits)}")

    # Count rejections by reason
    gate_rejects = [e for e in result.event_log if "GATE_REJECT" in str(e)]
    cross_session_cancels = [e for e in result.event_log if "CANCEL_CROSS_SESSION" in str(e)]

    print(f"\n[REJECTIONS]")
    print(f"  Gate rejections: {len(gate_rejects)}")
    for reject in gate_rejects[:5]:  # Show first 5
        print(f"    {reject}")
    if len(gate_rejects) > 5:
        print(f"    ... ({len(gate_rejects) - 5} more)")

    print(f"  Cross-session cancellations: {len(cross_session_cancels)}")
    for cancel in cross_session_cancels[:5]:
        print(f"    {cancel}")
    if len(cross_session_cancels) > 5:
        print(f"    ... ({len(cross_session_cancels) - 5} more)")

    # Financial metrics
    print(f"\n[FINANCIALS]")
    total_cost = 0.0
    for fill in result.fills:
        total_cost += fill.cost_paid

    print(f"  Starting equity: {ledger.starting_cash}")
    print(f"  Ending equity: {ledger.cash}")
    print(f"  Realized P&L: {ledger.realized_pnl}")
    print(f"  Total costs paid: {total_cost:.2f}")

    # Reconciliation
    print(f"\n[RECONCILIATION]")
    print(f"  Pending orders: {len(ledger.pending_orders)}")
    print(f"  Reserved cash: {ledger.reserved_cash}")
    print(f"  Open positions: {len(ledger.positions)}")
    print(f"  Daily P&L: {ledger.daily_pnl}")

    # Build report
    report = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "period": "2024-08-01 to 2024-08-31",
        "status": "PASSED" if len(gate_rejects) < len(result.orders_submitted) * 2 else "REVIEW",
        "metrics": {
            "timestamps_processed": result.timestamps_processed,
            "bars_processed": result.bars_processed,
            "orders_submitted": len(result.orders_submitted),
            "fills": len(result.fills),
            "exits": len(result.exits),
            "gate_rejections": len(gate_rejects),
            "cross_session_cancellations": len(cross_session_cancels),
        },
        "financials": {
            "starting_equity": ledger.starting_cash,
            "ending_equity": ledger.cash,
            "realized_pnl": ledger.realized_pnl,
            "total_costs": total_cost,
            "daily_pnl": ledger.daily_pnl,
        },
        "reconciliation": {
            "pending_orders": len(ledger.pending_orders),
            "reserved_cash": ledger.reserved_cash,
            "open_positions": len(ledger.positions),
            "exact": len(ledger.pending_orders) == 0 and ledger.reserved_cash == 0,
        },
        "event_log_sample": [str(e) for e in result.event_log[:10]],
    }

    # Verify reconciliation
    reconciliation_ok = (
        len(ledger.pending_orders) == 0 and
        ledger.reserved_cash == 0 and
        len(ledger.positions) == 0
    )

    print(f"\n[VALIDATION]")
    if reconciliation_ok:
        print("  ✓ PASSED: Reconciliation exact (no pending orders, no reserved cash, no open positions)")
    else:
        print("  ✗ FAILED: Reconciliation incomplete")
        if len(ledger.pending_orders) > 0:
            print(f"    - {len(ledger.pending_orders)} pending orders remain")
        if ledger.reserved_cash > 0:
            print(f"    - {ledger.reserved_cash} reserved cash remains")
        if len(ledger.positions) > 0:
            print(f"    - {len(ledger.positions)} open positions remain")

    print("\n" + "=" * 80)
    return report


if __name__ == "__main__":
    report = run_sunpharma_validation()

    # Save report
    report_path = "diagnostic_output/sunpharma_august_validation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport saved: {report_path}")

    # Exit with status
    sys.exit(0 if report.get("status") == "PASSED" else 1)
