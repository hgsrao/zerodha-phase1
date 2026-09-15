import unittest

from oos_calibration_engine import BacktestMetrics, CalibrationPerformanceGate


class TestOOSPerformanceGate(unittest.TestCase):
    def test_gate_passes_when_thresholds_are_met(self):
        metrics = BacktestMetrics(
            total_return=0.18,
            annualized_return=0.22,
            sharpe=1.4,
            max_drawdown=0.18,
            win_rate=0.58,
            profit_factor=1.7,
            exposure=0.35,
            best_score=0.42,
        )
        gate = CalibrationPerformanceGate()
        result = gate.evaluate(metrics)
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "PASS")

    def test_gate_fails_when_thresholds_are_breached(self):
        metrics = BacktestMetrics(
            total_return=0.02,
            annualized_return=0.03,
            sharpe=0.5,
            max_drawdown=0.38,
            win_rate=0.42,
            profit_factor=0.9,
            exposure=0.75,
            best_score=0.02,
        )
        gate = CalibrationPerformanceGate()
        result = gate.evaluate(metrics)
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["reasons"])


if __name__ == "__main__":
    unittest.main()
