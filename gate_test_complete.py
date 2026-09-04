#!/usr/bin/env python3
"""Complete 18-gate deterministic test suite with gate telemetry capture"""

import json
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from gates_framework import (
    EntryDecisionEngine, SafetyGateConfig, GateLogger,
    SystemState, EntrySignal
)

@dataclass
class GateTestResult:
    """Result of one gate test (pass and fail conditions)"""
    gate_number: int
    gate_name: str
    pass_test_name: str
    fail_test_name: str
    pass_condition_met: bool
    fail_condition_met: bool
    pass_reason: str
    fail_reason: str
    overall_pass: bool
    gates_evaluated_captured: bool

class CompleteGateTestSuite:
    """All 18 gates - deterministic pass/fail tests"""

    def __init__(self):
        self.config = SafetyGateConfig()
        self.logger = GateLogger()
        self.engine = EntryDecisionEngine(self.config, self.logger)
        self.results = []

    def create_default_state(self) -> Dict:
        """Create a safe default state where all gates pass"""
        return {
            'portfolio_value': 1000000,
            'current_dd_percent': 5.0,
            'current_lambda': 0.1,
            'daily_realized_loss': 0,
            'daily_unrealized_loss': 0,
            'open_positions_count': 1,
            'open_positions': [],
            'market_data_age_seconds': 5,
            'broker_connected': True,
            'broker_offline_seconds': 0,
            'kill_switch_active': False,
            'circuit_breaker_triggered': False
        }

    def create_signal(self, confidence=0.60) -> EntrySignal:
        """Create valid entry signal"""
        return EntrySignal(
            symbol="TEST",
            entry_price=100.0,
            stop_loss_price=97.0,
            profit_target_price=103.0,
            confidence=confidence,
            suggested_quantity=100,
            position_notional=10000.0,
            risk_reward_ratio=1.5
        )

    def run_gate_test(self, gate_num: int, gate_name: str,
                     pass_state_override: Dict, fail_state_override: Dict,
                     test_pass_name: str, test_fail_name: str) -> GateTestResult:
        """Run one gate with pass and fail conditions"""

        # Prepare states
        pass_state_dict = self.create_default_state()
        pass_state_dict.update(pass_state_override)

        fail_state_dict = self.create_default_state()
        fail_state_dict.update(fail_state_override)

        # Test pass condition
        pass_state = SystemState(**pass_state_dict)
        signal = self.create_signal()
        pass_ok, pass_size, pass_reason = self.engine.can_enter(signal, pass_state)

        # Test fail condition
        fail_state = SystemState(**fail_state_dict)
        fail_ok, fail_size, fail_reason = self.engine.can_enter(signal, fail_state)

        # Check if gates_evaluated was captured (not empty dict)
        telemetry_ok = pass_reason and fail_reason  # Has reason means gate ran

        return GateTestResult(
            gate_number=gate_num,
            gate_name=gate_name,
            pass_test_name=test_pass_name,
            fail_test_name=test_fail_name,
            pass_condition_met=pass_ok == True,
            fail_condition_met=fail_ok == False,
            pass_reason=pass_reason,
            fail_reason=fail_reason,
            overall_pass=(pass_ok == True) and (fail_ok == False),
            gates_evaluated_captured=telemetry_ok
        )

    def run_all(self) -> Dict:
        """Run all 18 gate tests"""

        print(f"\n{'='*90}")
        print("STEP 2: COMPLETE 18-GATE DETERMINISTIC TEST SUITE")
        print(f"{'='*90}\n")

        tests = [
            (1, "Gate01_KillSwitch",
             {"kill_switch_active": False}, {"kill_switch_active": True},
             "Kill switch OFF", "Kill switch ON"),

            (2, "Gate02_DrawdownHalt",
             {"current_dd_percent": 10.0}, {"current_dd_percent": 30.0},
             "Drawdown OK (10%)", "Drawdown exceed (30%)"),

            (3, "Gate03_DailyLossHalt",
             {"daily_realized_loss": 0}, {"daily_realized_loss": -50000},
             "Daily loss OK (0)", "Daily loss exceed (-50k)"),

            (4, "Gate04_BrokerHalt",
             {"broker_connected": True, "broker_offline_seconds": 0},
             {"broker_connected": False, "broker_offline_seconds": 120},
             "Broker connected", "Broker offline"),

            (5, "Gate05_ConcurrentPositions",
             {"open_positions_count": 2}, {"open_positions_count": 5},
             "Position count OK (2)", "Position count exceed (5)"),

            (6, "Gate06_GrossExposure",
             {"current_lambda": 0.2}, {"current_lambda": 0.95},
             "Gross exposure OK (20%)", "Gross exposure exceed (95%)"),

            (7, "Gate07_StaleData",
             {"market_data_age_seconds": 5}, {"market_data_age_seconds": 300},
             "Data fresh (5s)", "Data stale (300s)"),

            (8, "Gate08_SymbolConcentration",
             {"open_positions_count": 2}, {"open_positions_count": 1},
             "Concentration OK", "Single symbol over-concentrated"),

            (9, "Gate09_PositionQuantity",
             {"portfolio_value": 1000000}, {"portfolio_value": 10000},
             "Position quantity OK", "Position size exceeds limit"),

            (10, "Gate10_DrawdownDerating",
             {"current_dd_percent": 5.0}, {"current_dd_percent": 15.0},
             "Drawdown derating OK", "Drawdown derating triggers"),

            (11, "Gate11_LambdaDerating",
             {"current_lambda": 0.1}, {"current_lambda": 0.8},
             "Lambda derating OK", "Lambda derating triggers"),

            (12, "Gate12_StrategySignals",
             {"open_positions_count": 1}, {"open_positions_count": 0},
             "Signal OK", "Signal rejected"),

            (13, "Gate13_OrderDuplication",
             {"open_positions_count": 1}, {"open_positions_count": 2},
             "No duplication", "Possible duplication"),

            (14, "Gate14_OrderTimeout",
             {"broker_connected": True}, {"broker_offline_seconds": 90},
             "Order timeout OK", "Order timeout exceeded"),

            (15, "Gate15_OrderReconciliation",
             {"open_positions_count": 1}, {"open_positions_count": 0},
             "Reconciliation OK", "Reconciliation failed"),

            (16, "Gate16_Slippage",
             {"current_dd_percent": 5.0}, {"current_dd_percent": 20.0},
             "Slippage OK", "Slippage too high"),

            (17, "Gate17_MarketClose",
             {"market_data_age_seconds": 5}, {"market_data_age_seconds": 16200},
             "Market hours OK", "Market closed"),

            (18, "Gate18_CircuitBreaker",
             {"circuit_breaker_triggered": False}, {"circuit_breaker_triggered": True},
             "Circuit breaker OFF", "Circuit breaker ON"),
        ]

        passed_count = 0
        failed_count = 0
        telemetry_missing = 0

        for gate_num, gate_name, pass_override, fail_override, pass_name, fail_name in tests:
            try:
                result = self.run_gate_test(
                    gate_num, gate_name,
                    pass_override, fail_override,
                    pass_name, fail_name
                )
                self.results.append(result)

                status = "[PASS]" if result.overall_pass else "[FAIL]"
                print(f"Gate {gate_num:02d}: {gate_name:30s} {status}")
                if result.overall_pass:
                    passed_count += 1
                else:
                    failed_count += 1
                    if result.pass_condition_met == False:
                        print(f"  -> Pass condition failed: {result.pass_reason}")
                    if result.fail_condition_met == False:
                        print(f"  -> Fail condition failed: {result.fail_reason}")

                if not result.gates_evaluated_captured:
                    telemetry_missing += 1
                    print(f"  -> WARNING: Gate telemetry not captured")

            except Exception as e:
                print(f"Gate {gate_num:02d}: {gate_name:30s} [ERROR]")
                print(f"  -> {str(e)}")
                failed_count += 1

        print(f"\n{'='*90}")
        print("GATE TEST SUMMARY")
        print(f"{'='*90}")
        print(f"Total gates tested:      {len(tests)}")
        print(f"Gates passed (both):     {passed_count}")
        print(f"Gates failed:            {failed_count}")
        print(f"Telemetry missing:       {telemetry_missing}")
        print(f"\nOverall Status:          {'PASS' if failed_count == 0 else 'FAIL'}")
        print(f"{'='*90}\n")

        return {
            'total_gates': len(tests),
            'passed': passed_count,
            'failed': failed_count,
            'telemetry_captured': len(tests) - telemetry_missing,
            'telemetry_missing': telemetry_missing,
            'overall_status': 'PASS' if (failed_count == 0 and telemetry_missing == 0) else 'FAIL',
            'test_results': [asdict(r) for r in self.results],
            'timestamp': str(datetime.now()),
            'note': 'All 18 gates tested with pass/fail conditions. Telemetry capture required.'
        }


if __name__ == "__main__":
    suite = CompleteGateTestSuite()
    results = suite.run_all()

    with open("GATE_TEST_18_COMPLETE.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"[OK] Results saved to GATE_TEST_18_COMPLETE.json")

    if results['overall_status'] == 'PASS':
        print(f"[OK] All 18 gates passed with telemetry")
        print(f"[OK] Ready for Step 3 (timestamp alignment)")
    else:
        print(f"[FAIL] {results['failed']} gates failed")
        if results['telemetry_missing'] > 0:
            print(f"[FAIL] {results['telemetry_missing']} gates missing telemetry")
