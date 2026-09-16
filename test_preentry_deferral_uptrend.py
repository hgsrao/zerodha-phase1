#!/usr/bin/env python3
"""Test Pre-Entry Deferral Controller on an up-trending MARUTI signal.

This test simulates:
  1. Signal arrives at bar t (11:16 in transcript example)
  2. Deferral gate arms the candidate (no order submitted)
  3. Next bar t+1 closes (11:17)
  4. Evaluate: actual R-progress vs expected R-progress
  5. Decision: ADMIT or CANCEL

Expected behavior for up-trending signals:
  - First bar typically shows +0.04 to +0.12R progress
  - If actual_R >= expected_R (error <= 0), ADMIT
  - If actual_R < expected_R, CANCEL (false breakout)
"""

import sys
sys.path.insert(0, '/home/shrinivas/ECS_Project_external_engine')

from revision2_external.preentry_deferral_controller import PreEntryDeferralController
import json
from datetime import datetime


def test_maruti_uptrend():
    """Test on MARUTI up-trending signal from transcript.

    From transcript:
      Signal:        11:16
      Entry:         11:17 at ₹10,179.087
      Target exit:   11:19 at ₹10,193.4264
      Net P&L:       +₹104.15
    """

    print("\n" + "="*80)
    print("PRE-ENTRY DEFERRAL CONTROLLER TEST: MARUTI UP-TREND")
    print("="*80)

    controller = PreEntryDeferralController(expected_r_progress_pct=0.077, enable_logging=True)

    # MARUTI parameters from transcript
    symbol = "MARUTI"
    entry_price = 10179.087
    atr = 10.1389  # Approximate from transcript
    risk_unit = atr  # 1R = 1 ATR for this test
    direction = 1  # Long

    print(f"\n[SIGNAL ARRIVES at 11:16]")
    print(f"  Symbol: {symbol}")
    print(f"  Entry price: ₹{entry_price:.3f}")
    print(f"  ATR: ₹{atr:.4f} (1R = 1 ATR)")
    print(f"  Direction: {'LONG' if direction > 0 else 'SHORT'}")
    print(f"  Expected first-bar R-progress: +{controller.expected_r_progress_pct:.4f}R")

    # Arm the candidate (no order submitted yet)
    result = controller.arm_candidate(
        symbol=symbol,
        current_bar_index=0,
        entry_px=entry_price,
        atr=atr,
        direction=direction,
    )

    print(f"\n  → Deferral gate armed. No order submitted yet.")
    print(f"  → Waiting for next bar to close...")

    # Simulate next bar closing (11:17)
    # From transcript: the actual MARUTI bar went up from entry
    next_bar_open = 10174.00
    next_bar_close = 10184.00  # Hypothetical (actual from transcript would be different)
    next_bar_high = 10185.00
    next_bar_low = 10173.00

    print(f"\n[NEXT BAR CLOSES at 11:17]")
    print(f"  Open:  ₹{next_bar_open:.2f}")
    print(f"  High:  ₹{next_bar_high:.2f}")
    print(f"  Low:   ₹{next_bar_low:.2f}")
    print(f"  Close: ₹{next_bar_close:.2f}")

    # Calculate actual R-progress
    actual_progress_r = (next_bar_close - entry_price) / risk_unit
    expected_progress_r = controller.expected_r_progress_pct
    error_r = expected_progress_r - actual_progress_r

    print(f"\n  Actual R-progress: {actual_progress_r:.4f}R")
    print(f"  Expected R-progress: {expected_progress_r:.4f}R")
    print(f"  Error (expected - actual): {error_r:.4f}R")

    # Evaluate provisional
    decision = controller.evaluate_provisional(
        symbol=symbol,
        next_bar_index=1,
        next_bar_open=next_bar_open,
        next_bar_high=next_bar_high,
        next_bar_low=next_bar_low,
        next_bar_close=next_bar_close,
    )

    print(f"\n[DECISION MADE]")
    print(f"  → {decision}")

    if decision == "ADMIT_NEXT_OPEN":
        print(f"  ✅ Provisional bar showed sufficient velocity.")
        print(f"     Order WILL be submitted at 11:18 open.")
    elif decision == "CANCEL_CANDIDATE":
        print(f"  ❌ Provisional bar lacked expected follow-through.")
        print(f"     Order WILL NOT be submitted (false breakout avoided).")
    elif decision == "DEFER":
        print(f"  ⏸️  Marginal result; defer to next bar for re-evaluation.")

    # Show summary
    summary = controller.get_decision_summary()
    print(f"\n[SUMMARY]")
    print(f"  Total decisions: {summary['total_decisions']}")
    print(f"  Admitted: {summary['admitted']}")
    print(f"  Deferred: {summary['deferred']}")
    print(f"  Cancelled: {summary['cancelled']}")

    # Export decision
    history = controller.export_decision_history_json()
    output_file = "/home/shrinivas/ECS_Project_external_engine/diagnostic_output/preentry_deferral_maruti_test.json"
    with open(output_file, 'w') as f:
        json.dump(history, f, indent=2, default=str)

    print(f"\n[OUTPUT]")
    print(f"  Exported to: {output_file}")

    return controller, decision


def test_multiple_scenarios():
    """Test deferral controller on multiple signals with different outcomes."""

    print("\n" + "="*80)
    print("SCENARIO MATRIX: UP-TREND SIGNALS")
    print("="*80)

    scenarios = [
        {
            "name": "Strong Confirm (+0.10R)",
            "symbol": "INFY",
            "entry_px": 1407.50,
            "atr": 1.01,
            "next_close": 1408.50,
            "expected": "ADMIT (velocity > expected)"
        },
        {
            "name": "Marginal (+0.077R)",
            "symbol": "RELIANCE",
            "entry_px": 2850.00,
            "atr": 8.50,
            "next_close": 2850.65,
            "expected": "ADMIT (meets expected exactly)"
        },
        {
            "name": "Weak (+0.03R)",
            "symbol": "HDFCBANK",
            "entry_px": 1500.00,
            "atr": 2.50,
            "next_close": 1500.075,
            "expected": "CANCEL (insufficient follow-through)"
        },
        {
            "name": "Reversal (-0.05R)",
            "symbol": "KOTAKBANK",
            "entry_px": 650.00,
            "atr": 1.50,
            "next_close": 649.925,
            "expected": "CANCEL (immediate reversal)"
        },
    ]

    controller = PreEntryDeferralController(expected_r_progress_pct=0.077, enable_logging=False)

    results = []
    for scenario in scenarios:
        # Arm
        controller.arm_candidate(
            symbol=scenario["symbol"],
            current_bar_index=len(results),
            entry_px=scenario["entry_px"],
            atr=scenario["atr"],
            direction=1,
        )

        # Evaluate
        decision = controller.evaluate_provisional(
            symbol=scenario["symbol"],
            next_bar_index=len(results),
            next_bar_open=scenario["entry_px"] - scenario["atr"],
            next_bar_high=scenario["next_close"] + scenario["atr"]*0.5,
            next_bar_low=scenario["entry_px"] - scenario["atr"]*0.5,
            next_bar_close=scenario["next_close"],
        )

        # Calculate actual progress
        actual_r = (scenario["next_close"] - scenario["entry_px"]) / scenario["atr"]

        result = {
            "scenario": scenario["name"],
            "symbol": scenario["symbol"],
            "actual_r": round(actual_r, 4),
            "expected_r": 0.077,
            "decision": decision,
            "expected_outcome": scenario["expected"],
            "match": "✅" if ("ADMIT" in decision and "ADMIT" in scenario["expected"]) or
                           ("CANCEL" in decision and "CANCEL" in scenario["expected"]) else "❌"
        }
        results.append(result)

        print(f"\n{scenario['name']:30s} | {decision:20s} | Actual R: {actual_r:+.4f} | {result['match']}")

    # Export all scenarios
    output_file = "/home/shrinivas/ECS_Project_external_engine/diagnostic_output/preentry_deferral_scenarios.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n[EXPORT] Scenarios exported to: {output_file}")

    return results


if __name__ == "__main__":
    # Test 1: Single MARUTI signal
    controller, decision = test_maruti_uptrend()

    # Test 2: Multiple scenarios
    results = test_multiple_scenarios()

    print("\n" + "="*80)
    print("PRE-ENTRY DEFERRAL CONTROLLER TEST COMPLETE")
    print("="*80 + "\n")
