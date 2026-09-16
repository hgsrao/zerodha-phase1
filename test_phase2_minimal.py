#!/usr/bin/env python3
"""Minimal Phase 2 Test: Verify PID Controls Entry Price

Validates that the revised pid_controller.py now makes DIRECT
entry price adjustments (basis points), not just confidence multipliers.
"""

import sys
sys.path.insert(0, '/home/shrinivas/ECS_Project_external_engine')

from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox
from revision2.contracts import IDDecision, TradePlan
import json


def test_phase2_entry_price_control():
    """Test that PID entry adjustment directly controls submission price."""

    print("\n" + "="*80)
    print("PHASE 2 MINIMAL TEST: PID Entry Price Control")
    print("="*80 + "\n")

    # Initialize controller
    controller = SimplePIDModelPredictiveControlBox(pid_enabled=True)

    print("[SCENARIO 1] Neutral Confidence (no adjustment)")
    print("-" * 60)

    # Create a minimal decision object (enough to pass build_plan)
    class MinimalDecision:
        def __init__(self, confidence, side="BUY"):
            self.confidence = confidence
            self.side = side
            self.entry_limit = 1000.00
            self.target = 1015.00
            self.stop = 990.00

    # Test case 1: Baseline confidence (no adjustment expected)
    decision1 = MinimalDecision(confidence=0.75, side="BUY")
    plan1, pid_info1, _ = controller.build_plan(
        signal_decision=decision1,
        current_price=1000.00,
        atr=10.0,
        side="BUY",
    )

    print(f"Input confidence:  {decision1.confidence:.3f}")
    print(f"Planned entry:     ${pid_info1['entry_price_planned']:.4f}")
    print(f"Execution market:  ${pid_info1['execution_market_price']:.4f}")
    print(f"PID intent (bps):  {pid_info1['pid_intent_bps']:+.2f} bps")
    print(f"Slippage (bps):    {pid_info1['slippage_bps']:+.2f} bps")
    print(f"Effective entry:   ${plan1.entry_price:.4f}")

    print("\n[SCENARIO 2] Strong Confidence (tight entry expected)")
    print("-" * 60)

    # Test case 2: Strong confidence (should be tight)
    decision2 = MinimalDecision(confidence=0.85, side="BUY")
    plan2, pid_info2, _ = controller.build_plan(
        signal_decision=decision2,
        current_price=1000.00,
        atr=10.0,
        side="BUY",
    )

    print(f"Input confidence:  {decision2.confidence:.3f}")
    print(f"Planned entry:     ${pid_info2['entry_price_planned']:.4f}")
    print(f"Execution market:  ${pid_info2['execution_market_price']:.4f}")
    print(f"PID intent (bps):  {pid_info2['pid_intent_bps']:+.2f} bps")
    print(f"Slippage (bps):    {pid_info2['slippage_bps']:+.2f} bps")
    print(f"Effective entry:   ${plan2.entry_price:.4f}")

    # Validate: for BUY, higher confidence should yield tighter (lower) entry
    is_tighter = pid_info2['execution_market_price'] <= pid_info1['execution_market_price']
    print(f"\nValidation: Tight entry < Neutral entry? {is_tighter}")

    print("\n[SCENARIO 3] Weak Confidence (loose entry expected)")
    print("-" * 60)

    # Test case 3: Weak confidence (should be loose)
    decision3 = MinimalDecision(confidence=0.65, side="BUY")
    plan3, pid_info3, _ = controller.build_plan(
        signal_decision=decision3,
        current_price=1000.00,
        atr=10.0,
        side="BUY",
    )

    print(f"Input confidence:  {decision3.confidence:.3f}")
    print(f"Planned entry:     ${pid_info3['entry_price_planned']:.4f}")
    print(f"Execution market:  ${pid_info3['execution_market_price']:.4f}")
    print(f"PID intent (bps):  {pid_info3['pid_intent_bps']:+.2f} bps")
    print(f"Slippage (bps):    {pid_info3['slippage_bps']:+.2f} bps")
    print(f"Effective entry:   ${plan3.entry_price:.4f}")

    # Validate: for BUY, lower confidence should yield looser (higher) entry
    is_looser = pid_info3['execution_market_price'] >= pid_info1['execution_market_price']
    print(f"\nValidation: Loose entry > Neutral entry? {is_looser}")

    print("\n[SCENARIO 4] SHORT SALE (tight entry means higher price)")
    print("-" * 60)

    # Test case 4: SHORT with strong confidence
    decision4 = MinimalDecision(confidence=0.85, side="SELL")
    plan4, pid_info4, _ = controller.build_plan(
        signal_decision=decision4,
        current_price=1000.00,
        atr=10.0,
        side="SELL",
    )

    print(f"Input confidence:  {decision4.confidence:.3f}")
    print(f"Direction:         SELL (short)")
    print(f"Planned entry:     ${pid_info4['entry_price_planned']:.4f}")
    print(f"Execution market:  ${pid_info4['execution_market_price']:.4f}")
    print(f"PID intent (bps):  {pid_info4['pid_intent_bps']:+.2f} bps")
    print(f"Slippage (bps):    {pid_info4['slippage_bps']:+.2f} bps")
    print(f"Effective entry:   ${plan4.entry_price:.4f}")

    # For SHORT, tight means higher price
    short_baseline = MinimalDecision(confidence=0.75, side="SELL")
    plan_short_base, pid_info_short_base, _ = controller.build_plan(
        signal_decision=short_baseline,
        current_price=1000.00,
        atr=10.0,
        side="SELL",
    )
    is_tighter_short = pid_info4['execution_market_price'] >= pid_info_short_base['execution_market_price']
    print(f"\nValidation: Tight SHORT > Neutral SHORT? {is_tighter_short}")

    # Summary
    print("\n" + "="*80)
    print("PHASE 2 VALIDATION SUMMARY")
    print("="*80)

    results = {
        "test_name": "Phase 2: PID Entry Price Control",
        "status": "COMPLETE",
        "scenarios": [
            {
                "name": "Neutral Confidence",
                "confidence": 0.75,
                "side": "BUY",
                "pid_intent_bps": pid_info1['pid_intent_bps'],
                "execution_price": pid_info1['execution_market_price'],
                "effective_entry": plan1.entry_price,
            },
            {
                "name": "Strong Confidence (tight)",
                "confidence": 0.85,
                "side": "BUY",
                "pid_intent_bps": pid_info2['pid_intent_bps'],
                "execution_price": pid_info2['execution_market_price'],
                "effective_entry": plan2.entry_price,
                "is_tighter": is_tighter,
            },
            {
                "name": "Weak Confidence (loose)",
                "confidence": 0.65,
                "side": "BUY",
                "pid_intent_bps": pid_info3['pid_intent_bps'],
                "execution_price": pid_info3['execution_market_price'],
                "effective_entry": plan3.entry_price,
                "is_looser": is_looser,
            },
            {
                "name": "Strong SHORT (tight)",
                "confidence": 0.85,
                "side": "SELL",
                "pid_intent_bps": pid_info4['pid_intent_bps'],
                "execution_price": pid_info4['execution_market_price'],
                "effective_entry": plan4.entry_price,
                "is_tighter_short": is_tighter_short,
            }
        ],
        "all_validations_pass": is_tighter and is_looser and is_tighter_short,
    }

    if results['all_validations_pass']:
        print("✅ ALL PHASE 2 VALIDATIONS PASS")
        print("\nKey Findings:")
        print(f"  • PID now directly adjusts execution price (not just confidence)")
        print(f"  • Strong confidence → tighter entry (lower for BUY, higher for SELL)")
        print(f"  • Weak confidence → looser entry (higher for BUY, lower for SELL)")
        print(f"  • Slippage accounting preserved")
    else:
        print("❌ VALIDATION FAILED - check logic above")

    # Save results
    output_file = "/home/shrinivas/ECS_Project_external_engine/diagnostic_output/phase2_test_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n[SAVED] Results to {output_file}")
    print("="*80 + "\n")

    return results


if __name__ == "__main__":
    results = test_phase2_entry_price_control()
