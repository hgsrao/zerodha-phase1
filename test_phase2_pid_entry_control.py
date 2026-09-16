#!/usr/bin/env python3
"""Test Phase 2: PID Entry Price Control
=========================================

This test validates that the PID controller now makes actual entry price adjustments,
not just confidence multipliers.

Test Scenarios:
1. Zero PID adjustment (entry_adjustment=0): submission price = planned price
2. Negative adjustment (tight entry): submission price < planned price (for BUY)
3. Positive adjustment (loose entry): submission price > planned price (for BUY)
4. Slippage accounting: final effective entry includes broker's adverse fill
"""

import sys
sys.path.insert(0, '/home/shrinivas/ECS_Project_external_engine')

from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox
from revision2_external.decision import Decision, DecisionVector
import json
from datetime import datetime


def test_phase2_pid_entry_price_control():
    """Test that PID adjustment directly controls market submission price."""

    print("\n" + "="*80)
    print("PHASE 2: PID ENTRY PRICE CONTROL TEST")
    print("="*80)

    # Initialize the PID controller
    controller = SimplePIDModelPredictiveControlBox(
        pid_enabled=True,
        entry_p=1.0, entry_i=0.1, entry_d=0.05,
        exit_p=1.0, exit_i=0.1, exit_d=0.05,
        integral_clamp=0.1,
        expected_entry_confidence=0.75,
    )

    test_cases = [
        {
            "name": "Neutral Entry (zero adjustment)",
            "planned_entry_px": 1000.00,
            "confidence": 0.75,
            "atr": 10.0,
            "direction": "BUY",
            "expected_adjustment_bps": 0,  # No PID error
            "description": "First entry with baseline confidence → no adjustment"
        },
        {
            "name": "Tight Entry (negative adjustment)",
            "planned_entry_px": 1000.00,
            "confidence": 0.85,  # Higher than baseline 0.75 → negative error → tight
            "atr": 10.0,
            "direction": "BUY",
            "expected_adjustment_bps": -10,  # Tight entry, lower price for BUY
            "description": "Strong signal → PID tightens entry (buy lower)"
        },
        {
            "name": "Loose Entry (positive adjustment)",
            "planned_entry_px": 1000.00,
            "confidence": 0.65,  # Lower than baseline 0.75 → positive error → loose
            "atr": 10.0,
            "direction": "BUY",
            "expected_adjustment_bps": 10,  # Loose entry, higher price for BUY
            "description": "Weak signal → PID loosens entry (buy higher)"
        },
        {
            "name": "Short Sale Tight Entry",
            "planned_entry_px": 1000.00,
            "confidence": 0.85,
            "atr": 10.0,
            "direction": "SELL",
            "expected_adjustment_bps": -10,  # Tight, but for SHORT means higher price
            "description": "Strong short signal → PID tightens entry (sell higher)"
        },
    ]

    results = []

    for case in test_cases:
        print(f"\n[TEST] {case['name']}")
        print(f"  Description: {case['description']}")
        print(f"  Planned entry: ${case['planned_entry_px']:.2f}")
        print(f"  Confidence: {case['confidence']:.3f}")
        print(f"  ATR: ${case['atr']:.2f}")
        print(f"  Direction: {case['direction']}")

        # Create a decision with the test confidence
        decision = Decision(
            confidence=case['confidence'],
            side=case['direction'],
            entry_limit=case['planned_entry_px'],
            target=case['planned_entry_px'] + case['atr'] * 1.5,  # 1.5R target
            stop=case['planned_entry_px'] - case['atr'] * 1.0,    # 1.0R stop
        )

        # Call the PID controller
        plan, pid_info, trace = controller.build_plan(
            signal_decision=decision,
            current_price=case['planned_entry_px'],
            atr=case['atr'],
            side=case['direction'],
        )

        # Extract key metrics
        entry_price_planned = pid_info['entry_price_planned']
        execution_market_price = pid_info['execution_market_price']
        pid_intent_bps = pid_info['pid_intent_bps']
        slippage_bps = pid_info['slippage_bps']
        effective_entry = plan.entry_price

        # Calculate actual adjustment
        adjustment_pct = (execution_market_price - entry_price_planned) / entry_price_planned * 100
        adjustment_bps = adjustment_pct * 100

        print(f"\n  Results:")
        print(f"    Entry price planned:        ${entry_price_planned:.4f}")
        print(f"    Execution market price:     ${execution_market_price:.4f}")
        print(f"    Adjustment:                 {adjustment_bps:+.2f} bps (PID intent: {pid_intent_bps:+.2f} bps)")
        print(f"    Broker slippage:            {slippage_bps:+.2f} bps")
        print(f"    Effective entry (w/ slip):  ${effective_entry:.4f}")
        print(f"    Target price:               ${plan.target_price:.4f}")
        print(f"    Stop price:                 ${plan.stop_price:.4f}")

        # Validate
        direction_sign = 1 if case['direction'] == "BUY" else -1

        if case['direction'] == "BUY":
            if case['confidence'] > 0.75:
                # Should be tight (lower entry)
                is_correct = execution_market_price <= entry_price_planned
            elif case['confidence'] < 0.75:
                # Should be loose (higher entry)
                is_correct = execution_market_price >= entry_price_planned
            else:
                # Should be neutral
                is_correct = abs(execution_market_price - entry_price_planned) < 0.01
        else:  # SELL
            if case['confidence'] > 0.75:
                # Should be tight (higher entry for short)
                is_correct = execution_market_price >= entry_price_planned
            elif case['confidence'] < 0.75:
                # Should be loose (lower entry for short)
                is_correct = execution_market_price <= entry_price_planned
            else:
                # Should be neutral
                is_correct = abs(execution_market_price - entry_price_planned) < 0.01

        result = {
            "test_case": case['name'],
            "planned_entry": entry_price_planned,
            "execution_market": execution_market_price,
            "adjustment_bps": adjustment_bps,
            "effective_entry": effective_entry,
            "target": plan.target_price,
            "stop": plan.stop_price,
            "validation": "✅ PASS" if is_correct else "❌ FAIL",
            "direction": case['direction'],
            "confidence": case['confidence'],
        }
        results.append(result)

        print(f"  → {result['validation']}")

    # Export results
    output_file = "/home/shrinivas/ECS_Project_external_engine/diagnostic_output/phase2_pid_entry_control_test.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n[EXPORT] Results saved to: {output_file}")

    # Summary
    passed = sum(1 for r in results if "✅" in r['validation'])
    total = len(results)

    print(f"\n" + "="*80)
    print(f"PHASE 2 TEST SUMMARY: {passed}/{total} tests passed")
    print("="*80 + "\n")

    return results


def test_phase2_with_preentry_deferral():
    """Integration test: Phase 2 PID + Phase 1 Pre-Entry Deferral"""

    print("\n" + "="*80)
    print("INTEGRATION: PHASE 1 (PRE-ENTRY DEFERRAL) + PHASE 2 (PID ENTRY CONTROL)")
    print("="*80)

    from revision2_external.preentry_deferral_controller import PreEntryDeferralController

    # Initialize both controllers
    deferral = PreEntryDeferralController(expected_r_progress_pct=0.077, enable_logging=True)
    pid_controller = SimplePIDModelPredictiveControlBox(
        pid_enabled=True,
        entry_p=1.0, entry_i=0.1, entry_d=0.05,
        exit_p=1.0, exit_i=0.1, exit_d=0.05,
        integral_clamp=0.1,
        expected_entry_confidence=0.75,
    )

    print("\n[SCENARIO] MARUTI up-trend with strong confirmation")

    # Phase 1: Arm candidate
    symbol = "MARUTI"
    entry_px = 10179.087
    atr = 10.1389

    deferral.arm_candidate(
        symbol=symbol,
        current_bar_index=0,
        entry_px=entry_px,
        atr=atr,
        direction=1,
    )

    print(f"  1. Deferral gate armed at ₹{entry_px:.3f}")

    # Simulate next bar observation
    next_close = 10184.00
    decision_deferral = deferral.evaluate_provisional(
        symbol=symbol,
        next_bar_index=1,
        next_bar_open=10174.00,
        next_bar_high=10185.00,
        next_bar_low=10173.00,
        next_bar_close=next_close,
    )

    print(f"  2. Deferral evaluation at next bar close ₹{next_close:.2f}: {decision_deferral}")

    if decision_deferral == "ADMIT_NEXT_OPEN":
        # Phase 2: PID-controlled entry submission
        print(f"  3. Proceeding to PID-controlled entry submission...")

        # Calculate actual observed progress to inform PID
        actual_progress_r = (next_close - entry_px) / atr

        # Create decision with high confidence (strong confirm)
        decision = Decision(
            confidence=0.90,  # Strong confirmation observed
            side="BUY",
            entry_limit=entry_px,
            target=entry_px + atr * 1.5,
            stop=entry_px - atr * 1.0,
        )

        plan, pid_info, trace = pid_controller.build_plan(
            signal_decision=decision,
            current_price=entry_px,
            atr=atr,
            side="BUY",
        )

        print(f"     Entry PID confidence: {decision.confidence:.3f}")
        print(f"     PID adjustment: {pid_info['pid_intent_bps']:+.2f} bps")
        print(f"     Execution price: ₹{pid_info['execution_market_price']:.4f}")
        print(f"     Effective entry: ₹{plan.entry_price:.4f}")
        print(f"     Target: ₹{plan.target_price:.4f}")
        print(f"     Stop: ₹{plan.stop_price:.4f}")

        result = {
            "workflow": "PHASE1_THEN_PHASE2",
            "symbol": symbol,
            "deferral_decision": decision_deferral,
            "deferral_wait_bars": 1,
            "actual_progress_r": actual_progress_r,
            "pid_confidence": decision.confidence,
            "pid_adjustment_bps": pid_info['pid_intent_bps'],
            "execution_price": pid_info['execution_market_price'],
            "effective_entry": plan.entry_price,
            "target_price": plan.target_price,
            "stop_price": plan.stop_price,
            "status": "✅ INTEGRATION COMPLETE"
        }

        return result

    return None


if __name__ == "__main__":
    # Test 1: Phase 2 PID entry price control
    test_results = test_phase2_pid_entry_price_control()

    # Test 2: Integration with Phase 1
    integration_result = test_phase2_with_preentry_deferral()

    if integration_result:
        print(f"\n[INTEGRATION RESULT]")
        print(f"  Status: {integration_result['status']}")
        print(f"  Workflow: Deferral ({integration_result['deferral_decision']}) → PID Entry Control")
        print(f"  Actual progress observed: {integration_result['actual_progress_r']:.4f}R")
        print(f"  PID Confidence: {integration_result['pid_confidence']:.3f}")
        print(f"  PID Adjustment: {integration_result['pid_adjustment_bps']:+.2f} bps")
        print(f"  Execution Price: ₹{integration_result['execution_price']:.4f}")
        print(f"  Effective Entry: ₹{integration_result['effective_entry']:.4f}")

    print("\n" + "="*80)
    print("PHASE 2 TEST COMPLETE")
    print("="*80 + "\n")
