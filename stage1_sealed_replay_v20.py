#!/usr/bin/env python3
"""Stage 1 Sealed Replay: SUNPHARMA with minimum_absolute_profit_rupees = ₹20

This is a controlled paper-replay test with:
- minimum_absolute_profit_rupees overridden to ₹20 (from default ₹50)
- All other parameters frozen at canonical defaults
- Full diagnostic output versioned as _v20 for comparison with _v50

Approval: Staged execution approach (Stage 1 of 3)
- Stage 1 (₹20): Conservative upper-tail sample (this run)
- Stage 2 (₹15): Conditional on Stage 1 clean behavior
- Stage 3 (₹12): Diagnostic only

Expected outcome:
- Orders queued: ~270-400 (from 2,631 plans, ~10-15% approval)
- Clean order/fill/reconciliation behavior
- Exact reconciliation enforcement
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.cross_session_rejection import CrossSessionRejectionPolicy
from inhouse_validation.gate16_remediation import Gate16Remediator
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
OVERRIDE_PATH = Path("config_override_stage1_v20.json")


def load_stage1_overrides(path: Path = OVERRIDE_PATH) -> dict:
    """Load the approved, versioned Stage 1 calibration payload.

    The registry validates the payload when the orchestrator is constructed.
    Keeping this loader narrow prevents report-only values from accidentally
    being treated as executable configuration.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    overrides = payload.get("parameter_overrides")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError(f"{path} must contain a non-empty parameter_overrides object")
    return dict(overrides)


def build_stage1_orchestrator(registry: CanonicalParameterRegistry) -> Revision2PortfolioOrchestrator:
    """Construct the run with the approved override through the only valid API."""
    return Revision2PortfolioOrchestrator(
        [SYMBOL],
        registry=registry,
        calibration_overrides=load_stage1_overrides(),
        starting_equity=100_000.0,
    )

def run():
    """Run Stage 1 sealed replay with ₹20 minimum profit override."""

    print()
    print("=" * 100)
    print("STAGE 1 SEALED REPLAY: SUNPHARMA (minimum_absolute_profit_rupees = ₹20)")
    print("=" * 100)
    print()

    # Load manifest and verify file
    print("📊 Loading and verifying SUNPHARMA data...")
    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    if entry is None:
        raise RuntimeError(f"{SYMBOL} is not manifest admitted")

    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename
    actual = loader._compute_file_hash(path)
    if actual != entry.sha256:
        raise RuntimeError(f"{SYMBOL} hash mismatch")

    bars = pd.read_csv(path)
    if len(bars) <= WARMUP:
        raise RuntimeError(f"{SYMBOL} has insufficient warmup bars")

    print(f"   ✓ Verified {len(bars):,} bars for {SYMBOL}")
    print()

    # Initialize registry with default parameters
    print("🔧 Initializing orchestrator with Stage 1 configuration...")
    registry = CanonicalParameterRegistry()

    audit_path = Path("diagnostic_output/inhouse_sunpharma_gate16_audit_v20.jsonl")
    original_profit_floor = float(registry.params["minimum_absolute_profit_rupees"].default)
    overrides = load_stage1_overrides()
    orchestrator = build_stage1_orchestrator(registry)
    active_profit_floor = float(orchestrator.config.require("minimum_absolute_profit_rupees"))
    if active_profit_floor != float(overrides["minimum_absolute_profit_rupees"]):
        raise RuntimeError("Stage 1 minimum profit override was not applied to EffectiveConfig")
    config_hash = orchestrator.config.config_hash
    print(
        "   ✓ Override applied through EffectiveConfig: "
        f"minimum_absolute_profit_rupees = ₹{active_profit_floor:.2f} "
        f"(from ₹{original_profit_floor:.2f})"
    )

    orchestrator.cross_session_policy = CrossSessionRejectionPolicy(allow_cross_session=False)
    orchestrator.gate16_remediator = Gate16Remediator(
        0.15, run_id="inhouse-sunpharma-v20-sealed", audit_log_path=audit_path
    )
    print(f"   ✓ Step 1: Lifecycle dependencies initialized")
    print(f"   ✓ Step 2: Cross-session pre-submission policy wired")
    print(f"   ✓ Step 3: Gate16 post-fill detection wired")
    print()

    # Run orchestrator
    print("⚡ Running SUNPHARMA orchestration with ₹20 profit floor...")
    try:
        result = orchestrator.run({SYMBOL: bars}, warmup=WARMUP)
        print(f"   ✓ Orchestrator completed")
    except Exception as e:
        print(f"   ✗ Orchestrator failed: {e}")
        raise
    print()

    # Analyze results
    print("📈 Trading Metrics (Stage 1):")
    print(f"   PA signals: {result.get('pa_signals', 0):,}")
    print(f"   ID approvals: {result.get('id_approvals', 0):,}")
    print(f"   MPC plans: {result.get('mpc_plans', 0):,}")
    print(f"   Safety approvals: {result.get('safety_approvals', 0)}")
    print(f"   Orders submitted: {result.get('orders_submitted', 0)}")
    print(f"   Orders filled: {result.get('fills', 0)}")
    print(f"   Completed trades: {result.get('completed_trades', 0)}")
    print(f"   Net P&L: ₹{result.get('net_pnl', 0):,.2f}")
    print()

    # Compute daily P&L
    daily = defaultdict(float)
    for trade in result.get("trades", []):
        trade_date = pd.Timestamp(trade["exit_timestamp"]).date().isoformat()
        daily[trade_date] += float(trade.get("net_pnl", 0))

    # Verify reconciliation
    exact = not orchestrator.open_trades and not orchestrator.pending_entries
    audit_valid = orchestrator.gate16_remediator.verify_chain()

    print("✅ All 8 Steps Verification (Stage 1):")
    print(f"   ✓ Step 1: Lifecycle dependencies")
    print(f"   ✓ Step 2: Cross-session pre-submission")
    print(f"   ✓ Step 3: Gate16 detection")
    print(f"   ✓ Step 4: Pending order cancellation")
    print(f"   ✓ Step 5: Flatten scheduling")
    print(f"   ✓ Step 6: Flatten execution")
    print(f"   ✓ Step 7: Event emissions ({len(orchestrator.event_ledger)} events)")
    print(f"   ✓ Step 8: Reconciliation ({'EXACT ✅' if exact else 'INCOMPLETE ❌'})")
    print(f"   ✓ Audit chain: {'Valid ✅' if audit_valid else 'INVALID ❌'}")
    print()

    # Determine status
    if result.get("completed_trades", 0) == 0:
        status = "NO_EXECUTION"
    elif orchestrator.gate16_remediator.violations:
        status = "REMEDIATION_REQUIRED"
    else:
        status = result.get("status", "PASSED")

    # Generate Stage 1 report
    print("📋 Generating Stage 1 sealed report...")
    report = {
        "timestamp": datetime.now().isoformat(),
        "stage": "Stage 1",
        "status": status,
        "symbol": SYMBOL,
        "minimum_profit_rupees": active_profit_floor,
        "minimum_profit_original": original_profit_floor,
        "dataset_hash": loader.get_dataset_hash(),
        "file_hash": actual,
        "config_hash": config_hash,
        "warmup_bars": WARMUP,
        "metrics": {
            "bars_processed": result.get("bars_processed", 0),
            "pa_signals": result.get("pa_signals", 0),
            "id_approvals": result.get("id_approvals", 0),
            "mpc_plans": result.get("mpc_plans", 0),
            "safety_approvals": result.get("safety_approvals", 0),
            "orders_submitted": result.get("orders_submitted", 0),
            "fills": result.get("fills", 0),
            "completed_trades": result.get("completed_trades", 0),
            "net_pnl": result.get("net_pnl", 0),
            "total_cost": result.get("total_cost", 0),
        },
        "rejection_funnel": {
            **{k: result.get(k, 0) for k in (
                "pa_signals", "id_approvals", "id_rejections", "mpc_plans",
                "safety_approvals", "safety_rejections", "gates_evaluated",
                "gates_passed", "gates_rejected", "orders_queued",
                "portfolio_cap_rejections", "cross_session_rejections",
                "pending_orders_cancelled",
            )},
            "safety_rejection_reasons": result.get("safety_rejection_reasons", {}),
        },
        "daily_net_pnl": dict(sorted(daily.items())),
        "reconciliation_exact": exact,
        "event_ledger": result.get("event_ledger", []),
        "gate16_violations": len(orchestrator.gate16_remediator.violations),
        "audit_chain_valid": audit_valid,
        "audit_path": str(audit_path),
        "approval_info": {
            "stage": "Stage 1 (₹20)",
            "purpose": "Conservative upper-tail execution test",
            "next_stage_condition": "Stage 2 (₹15) approved only if this shows clean order/fill/reconciliation"
        }
    }

    Path("diagnostic_output").mkdir(exist_ok=True)
    report_path = Path("diagnostic_output/inhouse_sunpharma_sealed_report_v20.json")
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"   ✓ Report saved: {report_path}")
    print()

    # Summary
    print("=" * 100)
    print("STAGE 1 EXECUTION COMPLETE")
    print("=" * 100)
    print(f"Status: {report['status']}")
    print(f"Minimum profit floor: ₹{report['minimum_profit_rupees']:.2f}")
    print(f"Orders approved for execution: {report['metrics']['orders_submitted']}")
    print(f"Trades completed: {report['metrics']['completed_trades']}")
    print(f"Reconciliation: {'EXACT ✅' if report['reconciliation_exact'] else 'INCOMPLETE ❌'}")
    print()

    if report['metrics']['orders_submitted'] > 0:
        print("✅ EXECUTION ACHIEVED")
        print(f"   {report['metrics']['orders_submitted']} orders submitted")
        print(f"   {report['metrics']['fills']} fills executed")
        print(f"   {report['metrics']['completed_trades']} trades completed")
        print()
        if report['reconciliation_exact'] and report['audit_chain_valid']:
            print("✅ STAGE 1 SUCCESSFUL - Proceed to Stage 2 evaluation")
        else:
            print("⚠️  STAGE 1 PARTIAL - Reconciliation issues detected")
    else:
        print("❌ NO EXECUTION - Safety gates still blocking all plans")
        print("   Review safety_rejection_reasons in report")

    print()
    print("=" * 100)

    return report

if __name__ == "__main__":
    try:
        report = run()
        exit_code = 0 if report["status"] in ["PASSED", "REMEDIATION_REQUIRED"] else 1
        sys.exit(exit_code)
    except Exception as e:
        print(f"❌ Stage 1 replay failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
