"""
Strict SUNPHARMA/August Sealed Validation
Comprehensive report: submissions, fills, cancellations, rejections, reconciliation.
No bypass wrappers. Real gates. Exact accounting.
"""

import json
import sys
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
from collections import Counter

from revision4.contracts import EffectiveConfig, Bar
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider
from revision4.gates_proper import ProperGateEvaluator
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
import pandas as pd


def _compute_dataset_hash(manifest_path: str) -> str:
    """Compute SHA-256 hash of manifest file."""
    try:
        with open(manifest_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        return f"error:{str(e)}"


def _compute_config_hash(config: EffectiveConfig) -> str:
    """Compute SHA-256 hash of config dict representation."""
    try:
        config_dict = {
            "authorized_cross_session": config.require("authorized_cross_session"),
            "kill_switch_enabled": config.require("kill_switch_enabled"),
        }
        config_json = json.dumps(config_dict, sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()
    except Exception as e:
        return f"error:{str(e)}"


def _parse_rejection_reason(event_str: str) -> Optional[str]:
    """Extract gate rejection reason from event log entry."""
    if "GATE_REJECT" in event_str and ":" in event_str:
        parts = event_str.split(":")
        if len(parts) >= 3:
            return parts[2].strip()
    return None


def run_sunpharma_validation():
    """Run SUNPHARMA August 2024 validation with strict gates."""

    print("=" * 80)
    print("SUNPHARMA/August 2024 Sealed Validation (Strict Fail-Closed)")
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
    except RuntimeError as e:
        # Post-fill or post-reconciliation gate rejection
        print(f"  ✗ FAILED: {e}")
        return {
            "status": "FAILED",
            "reason": "Gate rejection (fail-closed)",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "dataset_hash": _compute_dataset_hash(manifest_path),
            "config_hash": _compute_config_hash(config),
        }
    except Exception as e:
        # Other error
        print(f"  ✗ FAILED: {e}")
        return {
            "status": "FAILED",
            "reason": "Orchestrator error",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "dataset_hash": _compute_dataset_hash(manifest_path),
            "config_hash": _compute_config_hash(config),
        }

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

    # Extract rejection reasons
    rejection_reasons = Counter()
    for event in gate_rejects:
        reason = _parse_rejection_reason(str(event))
        if reason:
            rejection_reasons[reason] += 1

    print(f"\n[REJECTIONS]")
    print(f"  Gate rejections: {len(gate_rejects)}")
    for reason, count in rejection_reasons.most_common():
        print(f"    {reason}: {count}")
    if gate_rejects and not rejection_reasons:
        print(f"    (no parsed reasons)")

    print(f"  Cross-session cancellations: {len(cross_session_cancels)}")

    # Financial metrics: calculate both entry and exit costs
    print(f"\n[FINANCIALS]")
    entry_costs = sum(fill.cost_paid for fill in result.fills)
    exit_costs = sum(exit_event.exit_cost for exit_event in result.exits
                     if hasattr(exit_event, 'exit_cost') else 0.0)
    total_costs = entry_costs + exit_costs

    print(f"  Starting equity: {ledger.starting_cash}")
    print(f"  Ending equity: {ledger.cash}")
    print(f"  Realized P&L: {ledger.realized_pnl}")
    print(f"  Entry costs: {entry_costs:.2f}")
    print(f"  Exit costs: {exit_costs:.2f}")
    print(f"  Total costs: {total_costs:.2f}")

    # Reconciliation
    print(f"\n[RECONCILIATION]")
    print(f"  Pending orders: {len(ledger.pending_orders)}")
    print(f"  Reserved cash: {ledger.reserved_cash}")
    print(f"  Open positions: {len(ledger.positions)}")

    # STRICT reconciliation check: all three must be zero
    reconciliation_ok = (
        len(ledger.pending_orders) == 0 and
        ledger.reserved_cash == 0.0 and
        len(ledger.positions) == 0
    )

    print(f"\n[VALIDATION]")
    if reconciliation_ok:
        print("  ✓ Reconciliation exact")
    else:
        print("  ✗ Reconciliation INCOMPLETE")
        if len(ledger.pending_orders) > 0:
            print(f"    - {len(ledger.pending_orders)} pending orders remain")
        if ledger.reserved_cash > 0:
            print(f"    - {ledger.reserved_cash:.2f} reserved cash remains")
        if len(ledger.positions) > 0:
            print(f"    - {len(ledger.positions)} open positions remain")

    # FINAL STATUS: only PASSED if reconciliation is exact AND no RuntimeError occurred
    # RuntimeError during run would have returned early, so we only check reconciliation here
    final_status = "PASSED" if reconciliation_ok else "FAILED"

    print(f"  Final Status: {final_status}")

    # Build comprehensive report
    report = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "period": "2024-08-01 to 2024-08-31",
        "status": final_status,
        "dataset_hash": _compute_dataset_hash(manifest_path),
        "config_hash": _compute_config_hash(config),
        "metrics": {
            "timestamps_processed": result.timestamps_processed,
            "bars_processed": result.bars_processed,
            "orders_submitted": len(result.orders_submitted),
            "fills": len(result.fills),
            "exits": len(result.exits),
            "gate_rejections": len(gate_rejects),
            "cross_session_cancellations": len(cross_session_cancels),
        },
        "rejection_breakdown": dict(rejection_reasons),
        "financials": {
            "starting_equity": ledger.starting_cash,
            "ending_equity": ledger.cash,
            "realized_pnl": ledger.realized_pnl,
            "entry_costs": round(entry_costs, 2),
            "exit_costs": round(exit_costs, 2),
            "total_costs": round(total_costs, 2),
        },
        "reconciliation": {
            "pending_orders": len(ledger.pending_orders),
            "reserved_cash": round(ledger.reserved_cash, 2),
            "open_positions": len(ledger.positions),
            "exact": reconciliation_ok,
        },
        "ledger_identity": {
            "starting_cash": ledger.starting_cash,
            "cash": ledger.cash,
            "realized_pnl": ledger.realized_pnl,
            "daily_pnl": ledger.daily_pnl,
        },
    }

    print("\n" + "=" * 80)
    return report


if __name__ == "__main__":
    report = run_sunpharma_validation()

    # Save report
    report_path = "diagnostic_output/sunpharma_august_validation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport saved: {report_path}")
    print(f"Status: {report.get('status')}")
    print(f"Dataset Hash: {report.get('dataset_hash')}")
    print(f"Config Hash: {report.get('config_hash')}")

    # Exit with status: PASSED=0, FAILED=1
    exit_code = 0 if report.get("status") == "PASSED" else 1
    sys.exit(exit_code)
