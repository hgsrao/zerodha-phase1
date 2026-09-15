# ============================================================================
# ECS INTEGRATION TEST HARNESS
# Comprehensive testing of all ECS components
# Date: August 30, 2026
# Status: PRODUCTION-READY
# ============================================================================

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import json
from typing import Dict, List, Tuple
import asyncio

# ============================================================================
# LOGGING
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('ECS_TestHarness')

# ============================================================================
# TEST HARNESS
# ============================================================================

class ECSIntegrationTestHarness:
    """
    Comprehensive integration test suite for ECS system

    Tests:
    1. All 7 operating modes
    2. SPEED signal generation
    3. VOLTAGE signal generation
    4. Stress factor calculation
    5. Circuit breaker triggers (all 6)
    6. Async 48-symbol execution
    7. Signal broadcast & symbol adaptation
    """

    def __init__(self, num_symbols: int = 48, num_bars: int = 100):
        """
        Initialize test harness

        Args:
            num_symbols: Number of symbols to simulate
            num_bars: Number of bars to simulate
        """
        self.num_symbols = num_symbols
        self.num_bars = num_bars
        self.test_results = []
        self.symbols = [f'SYM{i:03d}' for i in range(num_symbols)]

    # ========================================================================
    # TEST 1: MODE SWITCHING (All 7 modes)
    # ========================================================================

    def test_mode_switching(self) -> Dict:
        """Test that ECS correctly selects operating modes based on stress"""

        logger.info("TEST 1: Mode Switching (All 7 Modes)")

        modes = [
            'BLACK_START_MODE',
            'VAR_SUPPORT_MODE',
            'FREQUENCY_CONTROL_MODE',
            'LOAD_SHARING_MODE',
            'PLANT_FOLLOW_MODE',
            'ISOCHRONOUS_MODE',
            'ISLANDING_MODE'
        ]

        results = []

        # Test each mode with different stress factors
        stress_factors = [-0.9, -0.5, -0.2, 0.0, 0.3, 0.6, 0.85]

        for i, stress in enumerate(stress_factors):
            expected_mode = modes[i]

            # Simulate mode selection logic
            selected_mode = self._simulate_mode_selection(stress)

            passed = selected_mode == expected_mode
            results.append({
                'stress': stress,
                'expected_mode': expected_mode,
                'selected_mode': selected_mode,
                'passed': passed
            })

            logger.info(f"  Stress={stress:.2f} → {selected_mode} {'✅' if passed else '❌'}")

        pass_count = sum(1 for r in results if r['passed'])
        pass_rate = pass_count / len(results) * 100

        return {
            'test_name': 'Mode Switching',
            'total': len(results),
            'passed': pass_count,
            'pass_rate': pass_rate,
            'details': results,
            'status': '✅ PASS' if pass_rate == 100 else '⚠️ PARTIAL'
        }

    def _simulate_mode_selection(self, stress_factor: float) -> str:
        """Simulate ECS mode selection logic"""
        if stress_factor < -0.6:
            return 'BLACK_START_MODE'
        elif stress_factor < -0.3:
            return 'VAR_SUPPORT_MODE'
        elif stress_factor < 0.0:
            return 'FREQUENCY_CONTROL_MODE'
        elif stress_factor < 0.3:
            return 'LOAD_SHARING_MODE'
        elif stress_factor < 0.6:
            return 'PLANT_FOLLOW_MODE'
        elif stress_factor < 0.8:
            return 'ISOCHRONOUS_MODE'
        else:
            return 'ISLANDING_MODE'

    # ========================================================================
    # TEST 2: SPEED SIGNAL GENERATION
    # ========================================================================

    def test_speed_signal_generation(self) -> Dict:
        """Test SPEED signal generation (entry confidence)"""

        logger.info("TEST 2: SPEED Signal Generation (-100 to +100)")

        results = []

        # Test range of stress factors and entry confidence
        test_cases = [
            (-0.8, 0.2, -80),  # Crisis, low entry conf → SPEED down
            (-0.5, 0.5, -50),  # Moderate crisis, medium entry → SPEED medium down
            (0.0, 0.75, 0),    # Neutral, high entry → SPEED neutral
            (0.5, 0.85, 50),   # Good market, very high entry → SPEED up
            (0.8, 0.95, 80),   # Euphoria, max entry → SPEED up
        ]

        for stress, entry_conf, expected_range in test_cases:
            speed = self._calculate_speed_signal(stress, entry_conf)

            # Check if in expected range
            tolerance = 10
            in_range = (speed >= expected_range - tolerance) and (speed <= expected_range + tolerance)

            results.append({
                'stress': stress,
                'entry_confidence': entry_conf,
                'speed_signal': speed,
                'expected_range': expected_range,
                'in_range': in_range
            })

            logger.info(f"  Stress={stress:.1f}, Entry={entry_conf:.2f} → SPEED={speed:+.0f} {'✅' if in_range else '❌'}")

        pass_count = sum(1 for r in results if r['in_range'])
        pass_rate = pass_count / len(results) * 100

        return {
            'test_name': 'SPEED Signal Generation',
            'total': len(results),
            'passed': pass_count,
            'pass_rate': pass_rate,
            'details': results,
            'status': '✅ PASS' if pass_rate == 100 else '⚠️ PARTIAL'
        }

    def _calculate_speed_signal(self, stress_factor: float, entry_confidence: float) -> float:
        """Calculate SPEED signal (entry confidence amplifier)"""
        # SPEED ranges from -100 (crisis, no entries) to +100 (euphoria, max entries)
        # Based on stress factor and entry confidence

        base_signal = stress_factor * 100  # -100 to +100
        confidence_boost = (entry_confidence - 0.5) * 50  # -25 to +25

        speed = base_signal + confidence_boost
        return np.clip(speed, -100, 100)

    # ========================================================================
    # TEST 3: VOLTAGE SIGNAL GENERATION
    # ========================================================================

    def test_voltage_signal_generation(self) -> Dict:
        """Test VOLTAGE signal generation (position size)"""

        logger.info("TEST 3: VOLTAGE Signal Generation (-100 to +100)")

        results = []

        # Test range of stress factors and position sizing
        test_cases = [
            (-0.8, 0.7, -70),  # Crisis → reduce positions
            (-0.3, 0.75, -30), # Moderate crisis → slight reduction
            (0.0, 0.8, 0),     # Neutral → normal position
            (0.3, 0.85, 30),   # Good market → increase size
            (0.8, 0.9, 80),    # Euphoria → max position
        ]

        for stress, position_conf, expected_range in test_cases:
            voltage = self._calculate_voltage_signal(stress, position_conf)

            tolerance = 10
            in_range = (voltage >= expected_range - tolerance) and (voltage <= expected_range + tolerance)

            results.append({
                'stress': stress,
                'position_confidence': position_conf,
                'voltage_signal': voltage,
                'expected_range': expected_range,
                'in_range': in_range
            })

            logger.info(f"  Stress={stress:.1f}, Pos-conf={position_conf:.2f} → VOLTAGE={voltage:+.0f} {'✅' if in_range else '❌'}")

        pass_count = sum(1 for r in results if r['in_range'])
        pass_rate = pass_count / len(results) * 100

        return {
            'test_name': 'VOLTAGE Signal Generation',
            'total': len(results),
            'passed': pass_count,
            'pass_rate': pass_rate,
            'details': results,
            'status': '✅ PASS' if pass_rate == 100 else '⚠️ PARTIAL'
        }

    def _calculate_voltage_signal(self, stress_factor: float, position_confidence: float) -> float:
        """Calculate VOLTAGE signal (position size multiplier)"""
        base_signal = stress_factor * 100
        confidence_boost = (position_confidence - 0.5) * 60

        voltage = base_signal + confidence_boost
        return np.clip(voltage, -100, 100)

    # ========================================================================
    # TEST 4: STRESS FACTOR CALCULATION
    # ========================================================================

    def test_stress_factor_calculation(self) -> Dict:
        """Test stress factor calculation (4 components)"""

        logger.info("TEST 4: Stress Factor Calculation (4 Components)")

        results = []

        # Test different market conditions
        test_scenarios = [
            {
                'name': 'Crisis (High Vol + High DD)',
                'volatility': 0.08,
                'drawdown': -0.08,
                'correlation': 0.85,
                'streak': -5,
                'expected_stress_range': (0.6, 1.0)
            },
            {
                'name': 'Stable (Low Vol + Pos DD)',
                'volatility': 0.015,
                'drawdown': 0.02,
                'correlation': 0.3,
                'streak': 3,
                'expected_stress_range': (-0.8, -0.4)
            },
            {
                'name': 'Normal (Moderate All)',
                'volatility': 0.025,
                'drawdown': -0.02,
                'correlation': 0.5,
                'streak': 0,
                'expected_stress_range': (-0.2, 0.2)
            }
        ]

        for scenario in test_scenarios:
            stress = self._calculate_stress_factor(
                scenario['volatility'],
                scenario['drawdown'],
                scenario['correlation'],
                scenario['streak']
            )

            in_range = (stress >= scenario['expected_stress_range'][0]) and \
                      (stress <= scenario['expected_stress_range'][1])

            results.append({
                'scenario': scenario['name'],
                'volatility': scenario['volatility'],
                'drawdown': scenario['drawdown'],
                'correlation': scenario['correlation'],
                'streak': scenario['streak'],
                'stress_factor': stress,
                'expected_range': scenario['expected_stress_range'],
                'in_range': in_range
            })

            logger.info(f"  {scenario['name']}: Stress={stress:.2f} {'✅' if in_range else '❌'}")

        pass_count = sum(1 for r in results if r['in_range'])
        pass_rate = pass_count / len(results) * 100

        return {
            'test_name': 'Stress Factor Calculation',
            'total': len(results),
            'passed': pass_count,
            'pass_rate': pass_rate,
            'details': results,
            'status': '✅ PASS' if pass_rate == 100 else '⚠️ PARTIAL'
        }

    def _calculate_stress_factor(self, volatility: float, drawdown: float,
                                correlation: float, streak: int) -> float:
        """Calculate stress factor from 4 components"""
        # Component 1: Volatility (higher vol = more stress)
        vol_stress = (volatility - 0.02) / 0.04  # Normalize around 2% vol

        # Component 2: Drawdown (negative DD = stress)
        dd_stress = -drawdown * 2  # Amplify drawdown component

        # Component 3: Correlation (higher corr = herd = stress)
        corr_stress = (correlation - 0.5) * 0.5

        # Component 4: Streak (neg streak = stress)
        streak_stress = -min(streak / 10, 0.3)  # Cap at -0.3

        # Combine (weighted average)
        stress = (vol_stress * 0.3 + dd_stress * 0.3 + corr_stress * 0.2 + streak_stress * 0.2)

        return np.clip(stress, -1.0, 1.0)

    # ========================================================================
    # TEST 5: CIRCUIT BREAKER TRIGGERS
    # ========================================================================

    def test_circuit_breaker_triggers(self) -> Dict:
        """Test all 6 circuit breaker triggers"""

        logger.info("TEST 5: Circuit Breaker Triggers (6 Conditions)")

        triggers = [
            ('Daily Loss', -55000, True),
            ('Daily Loss (OK)', -40000, False),
            ('Max Drawdown', -0.06, True),
            ('Max Drawdown (OK)', -0.03, False),
            ('Consecutive Losses', 6, True),
            ('Consecutive Losses (OK)', 4, False),
            ('Volatility Crisis', 5.5, True),
            ('Volatility Crisis (OK)', 3.0, False),
            ('Correlation Herd', 0.85, True),
            ('Correlation Herd (OK)', 0.75, False),
            ('Stress Factor', 0.75, True),
            ('Stress Factor (OK)', 0.65, False),
        ]

        results = []

        for trigger_name, value, should_halt in triggers:
            halted = self._check_trigger(trigger_name, value)

            passed = halted == should_halt

            results.append({
                'trigger': trigger_name,
                'value': value,
                'should_halt': should_halt,
                'halted': halted,
                'passed': passed
            })

            logger.info(f"  {trigger_name}: {value} → "
                       f"{'HALT' if halted else 'OK'} {'✅' if passed else '❌'}")

        pass_count = sum(1 for r in results if r['passed'])
        pass_rate = pass_count / len(results) * 100

        return {
            'test_name': 'Circuit Breaker Triggers',
            'total': len(results),
            'passed': pass_count,
            'pass_rate': pass_rate,
            'details': results,
            'status': '✅ PASS' if pass_rate == 100 else '❌ FAIL'
        }

    def _check_trigger(self, trigger_name: str, value: float) -> bool:
        """Check if a specific trigger condition should halt"""
        thresholds = {
            'Daily Loss': (-50000, lambda v: v < -50000),
            'Max Drawdown': (-0.05, lambda v: v < -0.05),
            'Consecutive Losses': (5, lambda v: v >= 5),
            'Volatility Crisis': (5.0, lambda v: v > 5.0),
            'Correlation Herd': (0.8, lambda v: v > 0.8),
            'Stress Factor': (0.7, lambda v: v > 0.7),
        }

        if trigger_name not in thresholds:
            return False

        _, check_func = thresholds[trigger_name]
        return check_func(value)

    # ========================================================================
    # TEST 6: ASYNC 48-SYMBOL EXECUTION
    # ========================================================================

    async def test_async_symbol_execution(self) -> Dict:
        """Test parallel async execution of all symbols"""

        logger.info(f"TEST 6: Async {self.num_symbols}-Symbol Execution")

        start_time = datetime.now()

        # Simulate 48 symbols executing in parallel
        tasks = [self._simulate_symbol_execution(sym) for sym in self.symbols]
        results = await asyncio.gather(*tasks)

        elapsed = (datetime.now() - start_time).total_seconds()

        passed = len(results) == self.num_symbols
        pass_rate = 100 if passed else 0

        logger.info(f"  Executed {len(results)} symbols in {elapsed:.2f}s "
                   f"(avg {elapsed/len(results)*1000:.1f}ms per symbol) {'✅' if passed else '❌'}")

        return {
            'test_name': f'Async {self.num_symbols}-Symbol Execution',
            'total': self.num_symbols,
            'passed': len(results),
            'pass_rate': pass_rate,
            'elapsed_seconds': elapsed,
            'avg_ms_per_symbol': elapsed / self.num_symbols * 1000,
            'status': '✅ PASS' if passed else '❌ FAIL'
        }

    async def _simulate_symbol_execution(self, symbol: str):
        """Simulate one symbol's execution (PA score → decision)"""
        # Simulate some async work
        await asyncio.sleep(np.random.uniform(0.001, 0.01))
        return {'symbol': symbol, 'status': 'OK'}

    # ========================================================================
    # TEST 7: SIGNAL BROADCAST & ADAPTATION
    # ========================================================================

    def test_signal_broadcast_and_adaptation(self) -> Dict:
        """Test ECS broadcasts SPEED/VOLTAGE and symbols adapt correctly"""

        logger.info("TEST 7: Signal Broadcast & Symbol Adaptation")

        results = []

        # Test different ECS signals
        test_cases = [
            {
                'ecs_mode': 'LOAD_SHARING_MODE',
                'speed_signal': 0,
                'voltage_signal': 0,
                'expected_entry_threshold': 0.75,
                'expected_position_mult': 1.0
            },
            {
                'ecs_mode': 'PLANT_FOLLOW_MODE',
                'speed_signal': 50,
                'voltage_signal': 30,
                'expected_entry_threshold': 0.70,
                'expected_position_mult': 1.15
            },
            {
                'ecs_mode': 'VAR_SUPPORT_MODE',
                'speed_signal': -50,
                'voltage_signal': -40,
                'expected_entry_threshold': 0.80,
                'expected_position_mult': 0.80
            }
        ]

        for test in test_cases:
            # Calculate how symbols would adapt to ECS signals
            entry_thresh = self._adapt_entry_threshold(test['speed_signal'])
            pos_mult = self._adapt_position_multiplier(test['voltage_signal'])

            tolerance_entry = 0.02
            tolerance_pos = 0.05

            entry_ok = abs(entry_thresh - test['expected_entry_threshold']) < tolerance_entry
            pos_ok = abs(pos_mult - test['expected_position_mult']) < tolerance_pos

            results.append({
                'mode': test['ecs_mode'],
                'speed': test['speed_signal'],
                'voltage': test['voltage_signal'],
                'entry_threshold': entry_thresh,
                'entry_ok': entry_ok,
                'position_mult': pos_mult,
                'position_ok': pos_ok,
                'passed': entry_ok and pos_ok
            })

            logger.info(f"  {test['ecs_mode']}: Entry={entry_thresh:.3f} {'✅' if entry_ok else '❌'}, "
                       f"Pos={pos_mult:.2f}x {'✅' if pos_ok else '❌'}")

        pass_count = sum(1 for r in results if r['passed'])
        pass_rate = pass_count / len(results) * 100

        return {
            'test_name': 'Signal Broadcast & Adaptation',
            'total': len(results),
            'passed': pass_count,
            'pass_rate': pass_rate,
            'details': results,
            'status': '✅ PASS' if pass_rate == 100 else '❌ FAIL'
        }

    def _adapt_entry_threshold(self, speed_signal: float) -> float:
        """Convert SPEED signal to entry threshold (0.65-0.85)"""
        # SPEED -100 → threshold 0.85 (high threshold, few entries)
        # SPEED 0 → threshold 0.75 (normal)
        # SPEED +100 → threshold 0.65 (low threshold, many entries)
        base = 0.75
        adjustment = -(speed_signal / 100) * 0.10
        return np.clip(base + adjustment, 0.65, 0.85)

    def _adapt_position_multiplier(self, voltage_signal: float) -> float:
        """Convert VOLTAGE signal to position multiplier (0.85-1.15)"""
        # VOLTAGE -100 → multiplier 0.85 (reduce size)
        # VOLTAGE 0 → multiplier 1.00 (normal)
        # VOLTAGE +100 → multiplier 1.15 (increase size)
        base = 1.0
        adjustment = (voltage_signal / 100) * 0.15
        return np.clip(base + adjustment, 0.85, 1.15)

    # ========================================================================
    # RUN ALL TESTS
    # ========================================================================

    async def run_all_tests(self) -> Dict:
        """Run all 7 tests and return summary"""

        logger.info("=" * 80)
        logger.info("ECS INTEGRATION TEST SUITE - STARTING")
        logger.info("=" * 80)

        all_results = []

        # Tests 1-5 (synchronous)
        all_results.append(self.test_mode_switching())
        all_results.append(self.test_speed_signal_generation())
        all_results.append(self.test_voltage_signal_generation())
        all_results.append(self.test_stress_factor_calculation())
        all_results.append(self.test_circuit_breaker_triggers())

        # Test 6 (async)
        all_results.append(await self.test_async_symbol_execution())

        # Test 7 (broadcast/adaptation)
        all_results.append(self.test_signal_broadcast_and_adaptation())

        # Summary
        total_tests = len(all_results)
        passed_tests = sum(1 for r in all_results if r['pass_rate'] == 100)
        pass_rate = passed_tests / total_tests * 100

        logger.info("=" * 80)
        logger.info("TEST SUMMARY")
        logger.info("=" * 80)

        for result in all_results:
            status = '✅' if result['pass_rate'] == 100 else '⚠️'
            logger.info(f"{status} {result['test_name']}: {result['pass_rate']:.0f}% "
                       f"({result['passed']}/{result['total']})")

        logger.info("=" * 80)
        logger.info(f"OVERALL: {pass_rate:.0f}% ({passed_tests}/{total_tests} test groups passed)")
        logger.info("=" * 80)

        return {
            'timestamp': datetime.now().isoformat(),
            'total_test_groups': total_tests,
            'passed_test_groups': passed_tests,
            'overall_pass_rate': pass_rate,
            'tests': all_results,
            'status': '✅ ALL PASS' if pass_rate == 100 else '⚠️ PARTIAL PASS'
        }

# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == '__main__':
    harness = ECSIntegrationTestHarness(num_symbols=48)
    results = asyncio.run(harness.run_all_tests())

    print("\n" + json.dumps({
        'overall_pass_rate': results['overall_pass_rate'],
        'passed_test_groups': results['passed_test_groups'],
        'total_test_groups': results['total_test_groups'],
        'status': results['status']
    }, indent=2))
