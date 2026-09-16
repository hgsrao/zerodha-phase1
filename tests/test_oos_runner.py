import json
import os
import tempfile
import unittest
from unittest.mock import patch

from oos_calibration_engine import OOSBacktestRunner


class TestOOSBacktestRunner(unittest.TestCase):
    def test_runner_creates_report_and_returns_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OOSBacktestRunner(data_dir=tmpdir, iterations=5, report_path=os.path.join(tmpdir, 'report.json'))
            result = runner.run()
            self.assertIn('passed', result)
            self.assertIn('report_path', result)
            self.assertTrue(os.path.exists(result['report_path']))
            with open(result['report_path'], 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
            self.assertIn('status', payload)
            self.assertIn('metrics', payload)

    def test_programming_errors_are_not_swallowed_as_hold(self):
        # Regression: the runner must only catch *expected* data problems
        # (FileNotFoundError, ValueError). A genuine bug — AttributeError,
        # TypeError, an assertion failure — must propagate and fail the
        # test/run visibly instead of being reported as a calm HOLD.
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OOSBacktestRunner(data_dir=tmpdir, iterations=5, report_path=os.path.join(tmpdir, 'report.json'))
            with patch.object(runner.engine, 'run', side_effect=AttributeError("boom")):
                with self.assertRaises(AttributeError):
                    runner.run()


if __name__ == '__main__':
    unittest.main()
