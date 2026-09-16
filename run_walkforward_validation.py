#!/usr/bin/env python3
"""
Multi-Month Walk-Forward Validation Runner

Protocol:
1. Seed expands rolling: Jul-Aug → Jul-Sep → Jul-Oct → Jul-Nov → Jul-Dec
2. Each month tests against frozen seed (no future data leakage)
3. Unified ledger tracks P&L, win rate, regime characteristics
4. Compares: Dynamic targets vs Baseline (1.5R/60 bars)

Months: September 2023 - January 2024 (5 out-of-sample test periods)
Expected runtime: 30-45 minutes total
"""

import subprocess
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/home/shrinivas/ECS_Project_external_engine')

# Walk-forward schedule: (test_start, test_end_exclusive, seed_start, seed_end_exclusive)
WALKFORWARD_SCHEDULE = [
    {
        "month": "September 2023",
        "test_start": "2023-09-01",
        "test_end": "2023-10-01",
        "seed_start": "2023-07-03",
        "seed_end": "2023-09-01",
        "regime": "Post-rally consolidation / initial chop",
    },
    {
        "month": "October 2023",
        "test_start": "2023-10-01",
        "test_end": "2023-11-01",
        "seed_start": "2023-07-03",
        "seed_end": "2023-10-01",
        "regime": "Sector rotation / volatility compression",
    },
    {
        "month": "November 2023",
        "test_start": "2023-11-01",
        "test_end": "2023-12-01",
        "seed_start": "2023-07-03",
        "seed_end": "2023-11-01",
        "regime": "Trend re-acceleration",
    },
    {
        "month": "December 2023",
        "test_start": "2023-12-01",
        "test_end": "2024-01-01",
        "seed_start": "2023-07-03",
        "seed_end": "2023-12-01",
        "regime": "Year-end liquidity thinning",
    },
    {
        "month": "January 2024",
        "test_start": "2024-01-01",
        "test_end": "2024-01-04",
        "seed_start": "2023-07-03",
        "seed_end": "2024-01-01",
        "regime": "New year volatility expansion",
    },
]

RESULTS_FILE = Path("/home/shrinivas/ECS_Project_external_engine/diagnostic_output/walkforward_results.json")
LOG_FILE = Path("/tmp/walkforward_validation.log")


def run_orchestrator_for_month(month_config, mode="baseline"):
    """
    Launch orchestrator for a single test month.

    mode: "baseline" (1.5R/60) or "dynamic" (peer-pooled)
    Returns: (completed_trades, net_pnl, win_rate, trades)
    """

    print(f"\n{'='*80}")
    print(f"LAUNCHING: {month_config['month']} ({mode.upper()})")
    print(f"{'='*80}")
    print(f"Seed:   {month_config['seed_start']} → {month_config['seed_end']}")
    print(f"Test:   {month_config['test_start']} → {month_config['test_end']}")
    print(f"Regime: {month_config['regime']}")
    print(f"Mode:   {mode}")
    print()

    # Orchestrator command
    cmd = [
        "python3",
        "scripts/run_external_no_pid_one_signal_trace.py",
        f"--symbol", "MARUTI",
        f"--date", month_config['test_start'],
        f"--closed-loop-mode", "active_paper",
        f"--pid-mode", "enabled",
        f"--dynamic-target-seed-start", month_config['seed_start'],
        f"--dynamic-target-seed-end-exclusive", month_config['seed_end'],
        f"--dynamic-target-mode", "shadow" if mode == "dynamic" else "shadow",
    ]

    print(f"Command: {' '.join(cmd)}")
    print()

    try:
        # Run orchestrator
        result = subprocess.run(
            cmd,
            cwd="/home/shrinivas/ECS_Project_external_engine",
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout per month
            env={
                **dict(__import__("os").environ),
                "PYTHONPATH": "/home/shrinivas/ECS_Project_external_engine",
            }
        )

        if result.returncode != 0:
            print(f"❌ Orchestrator failed for {month_config['month']}")
            print(f"STDERR: {result.stderr[:500]}")
            return None

        # Extract results from trace
        trace_file = Path(
            f"/home/shrinivas/ECS_Project_external_engine/diagnostic_output/"
            f"final_execution_trace_MARUTI_{month_config['test_start'].replace('-', '')}.json"
        )

        if not trace_file.exists():
            print(f"⚠️ Trace file not found: {trace_file}")
            return None

        with open(trace_file, 'r') as f:
            data = json.load(f)

        summary = data.get('day_summary', {})

        result_obj = {
            "month": month_config['month'],
            "mode": mode,
            "completed_trades": summary.get('completed_trades', 0),
            "net_pnl": summary.get('net_pnl', 0),
            "gross_pnl": summary.get('gross_pnl', 0),
            "max_drawdown": summary.get('mtm_max_drawdown_fraction', 0),
            "regime": month_config['regime'],
        }

        print(f"✅ {month_config['month']} ({mode.upper()}):")
        print(f"   Completed Trades: {result_obj['completed_trades']}")
        print(f"   Net P&L:          ₹{result_obj['net_pnl']:+,.2f}")
        print(f"   Max Drawdown:     {result_obj['max_drawdown']*100:.4f}%")

        return result_obj

    except subprocess.TimeoutExpired:
        print(f"❌ Timeout for {month_config['month']}")
        return None
    except Exception as e:
        print(f"❌ Error for {month_config['month']}: {e}")
        return None


def main():
    """Execute full walk-forward validation"""

    print("\n" + "="*80)
    print("MULTI-MONTH WALK-FORWARD VALIDATION")
    print("="*80)
    print(f"Start time: {datetime.now().strftime('%H:%M:%S')}")
    print()
    print("Schedule:")
    for i, config in enumerate(WALKFORWARD_SCHEDULE, 1):
        print(f"  {i}. {config['month']}: Seed {config['seed_start'][-5:]} → {config['seed_end'][-5:]}, Test {config['test_start']}")
    print()
    print("Expected runtime: 30-45 minutes (5 months × ~6 min each)")
    print()

    # Collect results
    all_results = {
        "run_timestamp": datetime.now().isoformat(),
        "schedule": WALKFORWARD_SCHEDULE,
        "baseline_results": [],
        "dynamic_results": [],
        "comparison": {},
    }

    # Execute walk-forward for each month
    for month_config in WALKFORWARD_SCHEDULE:
        # Run baseline (1.5R/60 bars)
        print(f"\n[STEP 1/2] {month_config['month']} BASELINE")
        baseline = run_orchestrator_for_month(month_config, mode="baseline")
        if baseline:
            all_results['baseline_results'].append(baseline)

        # Run dynamic (peer-pooled)
        print(f"\n[STEP 2/2] {month_config['month']} DYNAMIC")
        dynamic = run_orchestrator_for_month(month_config, mode="dynamic")
        if dynamic:
            all_results['dynamic_results'].append(dynamic)

    # Generate comparison
    print("\n" + "="*80)
    print("WALK-FORWARD RESULTS SUMMARY")
    print("="*80)
    print()

    print(f"{'Month':<20} {'Baseline P&L':<20} {'Dynamic P&L':<20} {'Δ P&L':<15} {'Winner':<10}")
    print("-" * 85)

    baseline_total = 0
    dynamic_total = 0
    dynamic_wins = 0

    for baseline, dynamic in zip(all_results['baseline_results'], all_results['dynamic_results']):
        month = baseline['month']
        baseline_pnl = baseline['net_pnl']
        dynamic_pnl = dynamic['net_pnl']
        delta = dynamic_pnl - baseline_pnl
        winner = "Dynamic ✅" if delta > 0 else ("Baseline ✅" if delta < 0 else "Tie")

        print(f"{month:<20} ₹{baseline_pnl:>16,.2f}  ₹{dynamic_pnl:>16,.2f}  ₹{delta:>12,.2f}  {winner:<10}")

        baseline_total += baseline_pnl
        dynamic_total += dynamic_pnl
        if delta > 0:
            dynamic_wins += 1

    print("-" * 85)
    print(f"{'TOTAL':<20} ₹{baseline_total:>16,.2f}  ₹{dynamic_total:>16,.2f}  ₹{dynamic_total - baseline_total:>12,.2f}")
    print()

    all_results['comparison'] = {
        "baseline_total": baseline_total,
        "dynamic_total": dynamic_total,
        "delta_total": dynamic_total - baseline_total,
        "dynamic_wins": dynamic_wins,
        "total_months": len(all_results['baseline_results']),
        "improvement_pct": ((dynamic_total - baseline_total) / abs(baseline_total) * 100) if baseline_total != 0 else 0,
    }

    # Final verdict
    print("VERDICT:")
    print("-" * 85)

    if dynamic_total > baseline_total:
        print(f"✅ DYNAMIC TARGETS SUPERIOR")
        print(f"   Outperformed baseline by ₹{dynamic_total - baseline_total:,.2f}")
        print(f"   Improvement: {all_results['comparison']['improvement_pct']:+.2f}%")
        print(f"   Win rate: {dynamic_wins}/{len(all_results['baseline_results'])} months")
        print()
        print("RECOMMENDATION: Promote dynamic target provider to active paper mode")
    else:
        print(f"❌ BASELINE SUPERIOR (or equal)")
        print(f"   Baseline outperformed by ₹{baseline_total - dynamic_total:,.2f}")
        print(f"   Or change needed in: peer weighting, seed period, blending strategy")
        print()
        print("RECOMMENDATION: Iterate on dynamic approach, retain baseline for now")

    # Save results
    with open(RESULTS_FILE, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)

    print()
    print(f"Results saved to: {RESULTS_FILE}")
    print(f"End time: {datetime.now().strftime('%H:%M:%S')}")
    print("="*80)


if __name__ == "__main__":
    main()
