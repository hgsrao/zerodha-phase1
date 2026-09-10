import unittest

from canonical_parameter_registry import CanonicalParameterRegistry
from scripts.run_external_sizing_calibration import ECONOMIC_SEARCH_SURFACE, _score, validate_search_surface


class TestExternalSizingCalibrationContract(unittest.TestCase):
    def test_declared_surface_is_accepted(self):
        validate_search_surface(CanonicalParameterRegistry())

    def test_rejects_fixed_safety_contract_parameter(self):
        with self.assertRaisesRegex(ValueError, "immutable safety/fixed"):
            validate_search_surface(CanonicalParameterRegistry(), ("slippage_tolerance_percent",))

    def test_search_surface_is_small_and_explicit(self):
        self.assertEqual(set(ECONOMIC_SEARCH_SURFACE), {
            "entry_confidence_threshold", "profit_target_atr_mult", "stop_loss_atr_mult",
            "minimum_profit_margin_over_cost", "max_hold_bars",
        })

    def test_score_requires_meaningful_execution(self):
        self.assertLess(_score({"completed_trades": 19, "net_pnl": 1e9, "mtm_max_drawdown_fraction": 0.0}), -1e8)
        self.assertGreater(
            _score({"completed_trades": 20, "net_pnl": 100.0, "mtm_max_drawdown_fraction": 0.001}),
            _score({"completed_trades": 20, "net_pnl": 100.0, "mtm_max_drawdown_fraction": 0.01}),
        )


if __name__ == "__main__":
    unittest.main()
