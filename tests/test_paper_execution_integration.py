"""End-to-end PAPER-mode integration test.

Scope and honesty note: this proves the *plumbing* works end to end —
StartupGate -> real historical bar -> ExecutionGate -> PaperBrokerAdapter
order state machine -> simulated fill -> position/PnL tracking — using real
SUNPHARMA minute bars read from disk. No network call, no Kite credential,
and no live/simulated broker other than PaperBrokerAdapter is touched.

It deliberately does NOT exercise a calibrated trading strategy: the entry/
exit here is a fixed BUY-then-SELL harness sequence, not a signal produced by
the Revision 2 (68-parameter) engine. That engine's black boxes (PA/ID/MPC)
are registered as parameter owners in canonical_parameter_registry.py but do
not yet have a wired signal-generation implementation, so there is nothing
"calibrated" to drive orders from yet. This test only certifies that once
that signal exists, the order/fill/PnL path underneath it is safe and
correct.
"""

import os
import unittest

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import SingleSymbolReplayFeed
from runtime.operating_mode import (
    ExecutionGate,
    OperatingMode,
    PaperBrokerAdapter,
    RuntimeConfig,
    StartupGate,
)

DATA_DIR = (
    "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/"
    "DATA_1MIN_48_20230703_20260824"
)


def _safety_config(registry: CanonicalParameterRegistry):
    return {name: spec.default for name, spec in registry.safety_params.items()}


@unittest.skipUnless(os.path.isdir(DATA_DIR), f"real historical data dir not present: {DATA_DIR}")
class TestPaperExecutionIntegration(unittest.TestCase):
    def setUp(self):
        self.registry = CanonicalParameterRegistry()
        self.broker = PaperBrokerAdapter(account_id="ACC123")
        feed = SingleSymbolReplayFeed("SUNPHARMA", DATA_DIR, max_bars=50)
        self.bars = list(feed)
        self.assertGreater(len(self.bars), 1, "replay feed produced no real bars")

    def test_startup_gate_certifies_paper_session(self):
        config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            broker_account_id="ACC123",
            runtime_parameters={
                "base_dp_dt_multiplier": 1.2,
                "entry_confidence_threshold": 0.6,
                "max_positions_live": 4,
            },
            parameter_registry=self.registry,
        )
        report = StartupGate().certify_startup(config, self.broker, signing_key="abc123", durable_db=True)
        self.assertTrue(report["passed"], report["reasons"])
        self.assertEqual(report["broker_environment"], "paper")

    def test_real_bar_order_flows_through_gate_and_fills(self):
        safety_config = _safety_config(self.registry)
        entry_bar = self.bars[0]
        order = {"symbol": "SUNPHARMA", "side": "BUY", "quantity": 1, "order_type": "MARKET"}

        pre_submit = ExecutionGate().validate_pre_submit(safety_config, order, parameter_registry=self.registry)
        self.assertTrue(pre_submit["passed"], pre_submit["reasons"])

        result = self.broker.place_order(
            symbol="SUNPHARMA",
            side="BUY",
            quantity=1,
            order_type="MARKET",
            market_price=entry_bar.close,
            config=safety_config,
            parameter_registry=self.registry,
        )
        self.assertTrue(result["passed"], result.get("reasons"))
        self.assertEqual(result["state"], "filled")
        self.assertEqual(self.broker.orders[result["order_id"]].state.value, "filled")
        self.assertAlmostEqual(result["filled_price"], entry_bar.close, delta=entry_bar.close * 0.01)

        position = self.broker.get_position("SUNPHARMA")
        self.assertEqual(position["quantity"], 1)

        exit_bar = self.bars[-1]
        sell_result = self.broker.place_order(
            symbol="SUNPHARMA",
            side="SELL",
            quantity=1,
            order_type="MARKET",
            market_price=exit_bar.close,
            config=safety_config,
            parameter_registry=self.registry,
        )
        self.assertTrue(sell_result["passed"], sell_result.get("reasons"))
        self.assertEqual(sell_result["state"], "filled")

        position_after = self.broker.get_position("SUNPHARMA")
        self.assertEqual(position_after["quantity"], 0)

        expected_pnl = sell_result["filled_price"] - result["filled_price"]
        self.assertAlmostEqual(self.broker.realized_pnl, expected_pnl, places=4)

    def test_kill_switch_off_blocks_fill_even_with_valid_order(self):
        unsafe_config = _safety_config(self.registry)
        unsafe_config["kill_switch_enabled"] = False

        result = self.broker.place_order(
            symbol="SUNPHARMA",
            side="BUY",
            quantity=1,
            order_type="MARKET",
            market_price=self.bars[0].close,
            config=unsafe_config,
            parameter_registry=self.registry,
        )
        self.assertFalse(result["passed"])
        self.assertIn("kill switch", " ".join(result["reasons"]).lower())
        self.assertEqual(self.broker.orders[result["order_id"]].state.value, "rejected")
        self.assertEqual(self.broker.get_position("SUNPHARMA")["quantity"], 0)

    def test_invalid_quantity_is_rejected_not_filled(self):
        safety_config = _safety_config(self.registry)
        result = self.broker.place_order(
            symbol="SUNPHARMA",
            side="BUY",
            quantity=0,
            order_type="MARKET",
            market_price=self.bars[0].close,
            config=safety_config,
            parameter_registry=self.registry,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(self.broker.orders[result["order_id"]].state.value, "rejected")


if __name__ == "__main__":
    unittest.main()
