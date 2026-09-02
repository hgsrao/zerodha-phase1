"""Pipeline-level invariants for the Revision 2 orchestrator:

- the 23 fixed target parameters cannot be overridden by a calibration candidate
- the 20-item safety contract is immutable and never merges into the 68-surface
- a full run against real historical data consumes all 68 target parameters
- runs are deterministic (no hidden randomness)
"""

import os
import unittest
from types import MappingProxyType

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import SingleSymbolReplayFeed
from revision2.boxes import SafetyGatesTargetBox
from revision2.contracts import EffectiveConfig, SafetyContract, TradePlan
from revision2.orchestrator import Revision2Orchestrator

DATA_DIR = (
    "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/"
    "DATA_1MIN_48_20230703_20260824"
)


class TestFixedAndSafetyInvariance(unittest.TestCase):
    def setUp(self):
        self.registry = CanonicalParameterRegistry()

    def test_fixed_target_parameters_reject_calibration_overrides(self):
        for name in sorted(self.registry.FIXED_TARGET_NAMES):
            with self.subTest(param=name):
                with self.assertRaises(ValueError):
                    Revision2Orchestrator("SUNPHARMA", self.registry, calibration_overrides={name: self.registry.get(name).default})

    def test_fixed_values_are_identical_across_different_candidates(self):
        orch_a = Revision2Orchestrator("SUNPHARMA", self.registry, calibration_overrides={"entry_confidence_threshold": 0.35})
        orch_b = Revision2Orchestrator("SUNPHARMA", self.registry, calibration_overrides={"entry_confidence_threshold": 0.75})
        for name in sorted(self.registry.FIXED_TARGET_NAMES):
            self.assertEqual(orch_a.config.values[name], orch_b.config.values[name], name)

    def test_safety_contract_is_immutable_and_separate_from_target_surface(self):
        contract = SafetyContract.from_registry(self.registry)
        self.assertEqual(len(contract.values), 20)
        self.assertEqual(set(contract.values), set(self.registry.safety_params))
        self.assertFalse(set(contract.values) & set(self.registry.params))
        # Same registry -> same hash, every time.
        self.assertEqual(contract.contract_hash, SafetyContract.from_registry(self.registry).contract_hash)

    def test_safety_contract_and_effective_config_reject_item_assignment(self):
        contract = SafetyContract.from_registry(self.registry)
        self.assertIsInstance(contract.values, MappingProxyType)
        with self.assertRaises(TypeError):
            contract.values["kill_switch_enabled"] = False

        config = EffectiveConfig.build({name: spec.default for name, spec in self.registry.params.items()}, "hash")
        self.assertIsInstance(config.values, MappingProxyType)
        with self.assertRaises(TypeError):
            config.values["entry_confidence_threshold"] = 0.99

    def test_post_sizing_risk_check_uses_rupees_not_per_share_price(self):
        # Regression: worst-case loss must be compared in rupees
        # (per-share distance * quantity), never the raw per-share price
        # distance against a rupee cap.
        registry = CanonicalParameterRegistry()
        box = SafetyGatesTargetBox()
        values = {name: spec.default for name, spec in registry.params.items()}
        values["max_loss_per_trade_rupees"] = 100.0  # small, deliberately tight cap
        config = EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256)

        # Per-share distance of 5 is tiny and would pass a (buggy) per-share
        # check against a Rs.100 cap, but 5 * 50 shares = Rs.250 must fail.
        plan = TradePlan("BUY", 100.0, 95.0, 115.0, 2, 20)
        ok_small_qty, _, _ = box.evaluate_post_sizing([100000.0], plan, 10, config)
        ok_large_qty, reason, _ = box.evaluate_post_sizing([100000.0], plan, 50, config)
        self.assertTrue(ok_small_qty)   # 5 * 10 = Rs.50, within cap
        self.assertFalse(ok_large_qty)  # 5 * 50 = Rs.250, exceeds Rs.100 cap
        self.assertIn("worst-case trade loss", reason)


class TestStopPriorityAndReconciliation(unittest.TestCase):
    """A protective stop must never be suppressed by minimum_hold_bars, and
    the loop must never end with an unreconciled open position."""

    def _bars(self, rows=200):
        idx = pd.date_range("2024-01-01 09:15", periods=rows, freq="min", tz="Asia/Kolkata")
        price = 1000.0
        data = []
        for i in range(rows):
            data.append({"timestamp": idx[i], "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 5000})
        return pd.DataFrame(data)

    def test_open_position_is_reconciled_at_end_of_run(self):
        bars = self._bars(150)
        orch = Revision2Orchestrator("TESTSYM")
        report = orch.run(bars, warmup=60)
        self.assertIsNone(orch._open_trade)
        if report["completed_trades"] > 0:
            self.assertTrue(
                report["open_position_reconciled_at_end"] or report["trades"][-1]["exit_bar_idx"] < len(bars) - 2
            )


@unittest.skipUnless(os.path.isdir(DATA_DIR), f"real historical data dir not present: {DATA_DIR}")
class TestRevision2RealDataRun(unittest.TestCase):
    def setUp(self):
        feed = SingleSymbolReplayFeed("SUNPHARMA", DATA_DIR, max_bars=3000)
        self.bars = feed.load()

    def test_full_parameter_coverage_on_real_data(self):
        orch = Revision2Orchestrator("SUNPHARMA")
        report = orch.run(self.bars, warmup=60)
        self.assertEqual(report["parameter_coverage"]["target_missing"], [])
        self.assertEqual(report["parameter_coverage"]["target_consumed"], 68)
        self.assertEqual(report["parameter_coverage"]["target_total"], 68)

    def test_run_is_deterministic(self):
        report_a = Revision2Orchestrator("SUNPHARMA").run(self.bars, warmup=60)
        report_b = Revision2Orchestrator("SUNPHARMA").run(self.bars, warmup=60)
        self.assertEqual(report_a["completed_trades"], report_b["completed_trades"])
        self.assertEqual(report_a["net_pnl"], report_b["net_pnl"])
        self.assertEqual(report_a["trades"], report_b["trades"])

    def test_produces_reproducible_nonzero_trades(self):
        report = Revision2Orchestrator("SUNPHARMA").run(self.bars, warmup=60)
        self.assertGreater(report["completed_trades"], 0)
        self.assertEqual(report["completed_trades"], len(report["trades"]))
        valid_reasons = {"stop", "target", "max_hold", "signal_exit", "forced_close_drawdown_halt", "end_of_run_reconciliation"}
        for trade in report["trades"]:
            self.assertIn(trade["reason"], valid_reasons)
            # A protective stop or target can legitimately fire on the same
            # bar the entry filled (intrabar move right after the open) —
            # it must never fire *before* the entry bar.
            self.assertGreaterEqual(trade["exit_bar_idx"], trade["entry_bar_idx"])


if __name__ == "__main__":
    unittest.main()
