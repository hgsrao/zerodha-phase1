#!/usr/bin/env python3
"""Analyze rejection funnel from SUNPHARMA diagnostic report.

Monitors for report completion and identifies first blocking stage:
- PA signals
- ID approvals
- MPC plans
- Safety approvals
- 18-gate approvals
- Orders queued
- Fills
- Completed trades
"""

import json
import sys
import time
from pathlib import Path


def load_report(report_path):
    """Load and parse SUNPHARMA diagnostic report."""
    try:
        with open(report_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Report JSON invalid: {e}")
        return None


def analyze_funnel(report):
    """Analyze rejection funnel to identify first blocking stage."""

    if "rejection_funnel" not in report:
        return {
            "status": "NO_FUNNEL",
            "message": "Report missing rejection_funnel (old validator used)",
            "blocking_stage": "UNKNOWN",
        }

    funnel = report["rejection_funnel"]

    # Extract metrics
    pa_signals = funnel.get("pa_signals", 0)
    id_approvals = funnel.get("id_approvals", 0)
    id_rejections = funnel.get("id_rejections", 0)
    mpc_plans = funnel.get("mpc_plans", 0)
    safety_approvals = funnel.get("safety_approvals", 0)
    safety_rejections = funnel.get("safety_rejections", 0)
    orders_queued = funnel.get("orders_queued", 0)
    fills = funnel.get("fills", 0)
    completed_trades = funnel.get("completed_trades", 0)

    # Diagnose blocking stage
    diagnosis = {
        "pa_signals": pa_signals,
        "id_approvals": id_approvals,
        "id_rejections": id_rejections,
        "mpc_plans": mpc_plans,
        "safety_approvals": safety_approvals,
        "safety_rejections": safety_rejections,
        "orders_queued": orders_queued,
        "fills": fills,
        "completed_trades": completed_trades,
        "blocking_stage": None,
        "issue": None,
        "action": None,
    }

    # Funnel: PA signals → ID approvals → MPC plans → Safety approvals → Orders → Fills → Trades

    if pa_signals == 0:
        diagnosis["blocking_stage"] = "PA_SIGNAL_GENERATION"
        diagnosis["issue"] = "PA parameters (entry_threshold, confidence) too strict"
        diagnosis["action"] = "Adjust PA tuning, not safety thresholds"

    elif id_rejections > 0 and id_approvals == 0:
        diagnosis["blocking_stage"] = "ID_DECISION"
        diagnosis["issue"] = f"ID gates filtering all {id_rejections} signals (0 approvals)"
        diagnosis["action"] = "Review ID decision logic and thresholds"

    elif mpc_plans == 0 and id_approvals > 0:
        diagnosis["blocking_stage"] = "MPC_PLAN_BUILD"
        diagnosis["issue"] = f"MPC not building plans despite {id_approvals} ID approvals"
        diagnosis["action"] = "Debug MPC plan building logic and position sizing"

    elif safety_rejections > 0 and safety_approvals == 0:
        diagnosis["blocking_stage"] = "SAFETY_GATES"
        diagnosis["issue"] = f"Safety gates rejecting all {safety_rejections} plans (0 approvals)"
        diagnosis["action"] = "Review safety gate thresholds (not safety contract)"

    elif orders_queued == 0 and safety_approvals > 0:
        diagnosis["blocking_stage"] = "ORDER_QUEUING"
        diagnosis["issue"] = f"Orders not queued despite {safety_approvals} safety approvals"
        diagnosis["action"] = "Debug order queueing logic"

    elif fills == 0 and orders_queued > 0:
        diagnosis["blocking_stage"] = "FILL_EXECUTION"
        diagnosis["issue"] = f"{orders_queued} orders queued but 0 fills"
        diagnosis["action"] = "Debug broker fill logic and market conditions"

    elif completed_trades == 0 and fills > 0:
        diagnosis["blocking_stage"] = "TRADE_COMPLETION"
        diagnosis["issue"] = f"{fills} fills but 0 completed trades"
        diagnosis["action"] = "Debug exit logic and position closure"

    else:
        # Check reconciliation
        status = report.get("status", "UNKNOWN")
        reconciliation_exact = report.get("reconciliation_exact", False)

        if completed_trades > 0 and reconciliation_exact:
            diagnosis["blocking_stage"] = None
            diagnosis["issue"] = "NO BLOCKAGE - Real execution with exact reconciliation"
            diagnosis["action"] = "Proceed to 48-symbol validation"
        elif completed_trades > 0 and not reconciliation_exact:
            diagnosis["blocking_stage"] = "RECONCILIATION"
            diagnosis["issue"] = "Trades executed but reconciliation failed"
            diagnosis["action"] = "Debug reconciliation logic and state tracking"
        else:
            diagnosis["blocking_stage"] = "UNKNOWN"
            diagnosis["issue"] = "Cannot determine blocking stage from funnel"
            diagnosis["action"] = "Review full report and log for details"

    return diagnosis


def format_report(diagnosis):
    """Format diagnosis as readable report."""
    report_lines = [
        "=" * 90,
        "SUNPHARMA DIAGNOSTIC ANALYSIS",
        "=" * 90,
        "",
    ]

    # Funnel metrics
    report_lines.extend([
        "REJECTION FUNNEL FLOW:",
        f"  PA signals generated:        {diagnosis['pa_signals']}",
        f"  ID approvals:                {diagnosis['id_approvals']}",
        f"  ID rejections:               {diagnosis['id_rejections']}",
        f"  MPC plans built:             {diagnosis['mpc_plans']}",
        f"  Safety approvals:            {diagnosis['safety_approvals']}",
        f"  Safety rejections:           {diagnosis['safety_rejections']}",
        f"  Orders queued:               {diagnosis['orders_queued']}",
        f"  Fills:                       {diagnosis['fills']}",
        f"  Completed trades:            {diagnosis['completed_trades']}",
        "",
    ])

    # Blocking stage diagnosis
    if diagnosis["blocking_stage"]:
        report_lines.extend([
            "FIRST BLOCKING STAGE:",
            f"  ❌ {diagnosis['blocking_stage']}",
            "",
            "ISSUE:",
            f"  {diagnosis['issue']}",
            "",
            "ACTION:",
            f"  {diagnosis['action']}",
            "",
        ])
    else:
        report_lines.extend([
            "STATUS:",
            f"  ✅ {diagnosis['issue']}",
            "",
            "NEXT:",
            f"  {diagnosis['action']}",
            "",
        ])

    report_lines.append("=" * 90)

    return "\n".join(report_lines)


def main():
    """Monitor for report and analyze when complete."""
    report_path = Path("diagnostic_output/inhouse_sunpharma_sealed_report.json")

    print("Monitoring for SUNPHARMA diagnostic report...")
    print(f"Expected: {report_path}")
    print("")

    # Wait for report with timeout
    timeout = 600  # 10 minutes
    start = time.time()

    while time.time() - start < timeout:
        report = load_report(report_path)

        if report:
            print("✅ Report found!")
            print("")

            # Analyze
            diagnosis = analyze_funnel(report)

            # Print formatted report
            print(format_report(diagnosis))

            # Save diagnosis
            diagnosis_path = Path("diagnostic_output/rejection_funnel_analysis.json")
            with open(diagnosis_path, "w") as f:
                json.dump(diagnosis, f, indent=2)

            print(f"Diagnosis saved to: {diagnosis_path}")
            return 0

        # Still waiting
        elapsed = int(time.time() - start)
        print(f"⏳ Waiting for report... ({elapsed}s elapsed)", end="\r")
        time.sleep(2)

    print(f"❌ Timeout: Report not found after {timeout}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
