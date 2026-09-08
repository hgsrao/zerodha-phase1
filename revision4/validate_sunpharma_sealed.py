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

from revision4.contracts import EffectiveConfig, Bar, ExitEvent
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider
from revision4.gates_proper import ProperGateEvaluator
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.config_access import calculate_transaction_cost
import pandas as pd


def _compute_dataset_hash(manifest_path: str, data_dir: str) -> str:
    """Compute SHA-256 hash of all declared CSV files.

    Validates each file against its manifest SHA-256 and produces a
    deterministic hash of the full dataset identity.

    Returns:
        SHA-256 hash of concatenated file hashes (dataset identity)
        or "error:..." if validation fails
    """
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)

        if "files" not in manifest:
            return "error:no files in manifest"

        # Validate each file and collect its hash
        file_hashes = []
        for file_entry in manifest["files"]:
            filename = file_entry["filename"]
            expected_sha = file_entry["sha256"]
            symbol = file_entry["symbol"]

            file_path = f"{data_dir}/{filename}"

            try:
                with open(file_path, 'rb') as f:
                    actual_sha = hashlib.sha256(f.read()).hexdigest()

                if actual_sha != expected_sha:
                    return f"error:hash mismatch for {symbol}: expected {expected_sha}, got {actual_sha}"

                file_hashes.append(actual_sha)
            except FileNotFoundError:
                return f"error:file not found: {file_path}"
            except Exception as e:
                return f"error:failed to read {filename}: {str(e)}"

        # Hash the concatenation of all file hashes
        concatenated = "".join(sorted(file_hashes))
        dataset_hash = hashlib.sha256(concatenated.encode()).hexdigest()
        return dataset_hash
    except Exception as e:
        return f"error:{str(e)}"


def _compute_config_hash(config: EffectiveConfig) -> str:
    """Compute SHA-256 hash of all 89 effective config parameters."""
    try:
        # Get all 89 parameters from config
        all_params = config.get_all_params()

        # Serialize deterministically (sorted keys, JSON)
        config_json = json.dumps(all_params, sort_keys=True, default=str)
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

    print(f"\n[HASH] Computing dataset and config identity...")
    dataset_hash = _compute_dataset_hash(manifest_path, data_dir)
    config_hash = _compute_config_hash(config)
    print(f"  Dataset hash: {dataset_hash}")
    print(f"  Config hash: {config_hash}")

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
            "dataset_hash": dataset_hash,
            "config_hash": config_hash,
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
            "dataset_hash": dataset_hash,
            "config_hash": config_hash,
        }

    print(f"  ✓ Completed")

    # EOD FLATTENING: Close all remaining open positions using canonical exit costs
    print(f"\n[EOD FLATTEN] Closing all remaining positions...")
    eod_flatten_exits = []
    remaining_positions = list(ledger.positions.values())
    if remaining_positions:
        print(f"  Forcing close of {len(remaining_positions)} remaining positions")
        last_bar = bars[-1] if bars else None

        for position in remaining_positions:
            if last_bar and last_bar.symbol == position.symbol:
                # Use canonical exit cost model (direction-aware SELL)
                exit_price = last_bar.close
                side = "SELL" if position.direction == 1 else "BUY"  # Opposite direction to close
                exit_cost_canonical = calculate_transaction_cost(
                    exit_price, abs(position.quantity), side
                )

                # Create valid ExitEvent using canonical costs
                exit_event = ExitEvent(
                    exit_id=f"eod_flatten_{position.order_id}",
                    order_id=position.order_id,
                    timestamp_exited=last_bar.timestamp,
                    bar_index_exited=len(bars) - 1,
                    exit_price=exit_price,
                    exit_cost_paid=exit_cost_canonical,
                    exit_reason="EOD_FLATTEN",
                )

                # Close in ledger
                ok, reason = ledger.close_position(exit_event, config)
                if not ok:
                    print(f"    Warning: Failed to close {position.symbol}: {reason}")
                else:
                    eod_flatten_exits.append(exit_event)
                    print(f"    ✓ Closed {position.symbol} at {exit_price:.2f} (cost: {exit_cost_canonical:.2f})")
    else:
        print(f"  No remaining positions to flatten")

    # Analyze results
    print(f"\n[METRICS]")
    print(f"  Timestamps: {result.timestamps_processed}")
    print(f"  Bars: {result.bars_processed}")
    print(f"  Orders submitted: {len(result.orders_submitted)}")
    print(f"  Fills: {len(result.fills)}")
    print(f"  Exits: {len(result.exits)}")

    # Build daily P&L series from fills and exits (includes EOD flattens)
    # NOTE: This captures transaction costs only; gross P&L requires CompletedTrade records
    daily_pnl_series = {}

    for fill in result.fills:
        date = fill.timestamp_filled.split("T")[0]
        pnl = -(fill.cost_paid)  # Entry cost reduces equity
        daily_pnl_series[date] = daily_pnl_series.get(date, 0.0) + pnl

    for exit_event in result.exits:
        date = exit_event.timestamp_exited.split("T")[0]
        pnl = -(exit_event.exit_cost_paid)  # Exit cost reduces equity
        daily_pnl_series[date] = daily_pnl_series.get(date, 0.0) + pnl

    # Include EOD flatten exits (canonical costs)
    for exit_event in eod_flatten_exits:
        date = exit_event.timestamp_exited.split("T")[0]
        pnl = -(exit_event.exit_cost_paid)  # Exit cost reduces equity
        daily_pnl_series[date] = daily_pnl_series.get(date, 0.0) + pnl

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

    # Financial metrics: calculate both entry and exit costs (including EOD flattens)
    print(f"\n[FINANCIALS]")
    entry_costs = sum(fill.cost_paid for fill in result.fills)
    # Use exit_cost_paid (not exit_cost) from ExitEvent contract
    result_exit_costs = sum(
        exit_event.exit_cost_paid for exit_event in result.exits
        if hasattr(exit_event, 'exit_cost_paid')
    )
    # Add EOD flatten exit costs
    eod_exit_costs = sum(
        exit_event.exit_cost_paid for exit_event in eod_flatten_exits
        if hasattr(exit_event, 'exit_cost_paid')
    )
    exit_costs = result_exit_costs + eod_exit_costs
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
        "dataset_hash": _compute_dataset_hash(manifest_path, data_dir),
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
            "final_daily_pnl": ledger.daily_pnl,
        },
        "daily_pnl_series": daily_pnl_series,
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
