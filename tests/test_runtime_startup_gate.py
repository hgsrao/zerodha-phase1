import unittest
from typing import Optional

from canonical_parameter_registry import CanonicalParameterRegistry
from runtime.operating_mode import OperatingMode, RuntimeConfig, StartupGate, SimulatedBrokerAdapter, PaperBrokerAdapter, KiteBrokerAdapter


class DummyBroker:
    def __init__(self, environment: str = "simulated", account_id: Optional[str] = None):
        self.environment = environment
        self.account_id = account_id or "ACC123"


class TestRuntimeStartupGate(unittest.TestCase):
    def test_default_mode_is_research(self):
        config = RuntimeConfig()
        self.assertEqual(config.operating_mode, OperatingMode.RESEARCH)

    def test_research_rejects_live_adapter(self):
        gate = StartupGate()
        config = RuntimeConfig(operating_mode=OperatingMode.RESEARCH)
        broker = KiteBrokerAdapter(account_id="ACC123")
        report = gate.certify_startup(config, broker, signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("adapter", " ".join(report["reasons"]))

    def test_live_mode_requires_signing_key(self):
        gate = StartupGate()
        config = RuntimeConfig(
            operating_mode=OperatingMode.LIVE,
            live_trading_enabled=True,
            broker_account_id="ACC123",
        )
        report = gate.certify_startup(config, KiteBrokerAdapter(account_id="ACC123"), signing_key="", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("signing", " ".join(report["reasons"]).lower())

    def test_live_mode_requires_matching_account(self):
        gate = StartupGate()
        config = RuntimeConfig(
            operating_mode=OperatingMode.LIVE,
            live_trading_enabled=True,
            broker_account_id="ACC999",
        )
        report = gate.certify_startup(config, KiteBrokerAdapter(account_id="ACC123"), signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("account", " ".join(report["reasons"]).lower())

    def test_paper_mode_rejects_live_credentials(self):
        gate = StartupGate()
        config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=True,
            broker_account_id="ACC123",
        )
        report = gate.certify_startup(config, PaperBrokerAdapter(account_id="ACC123"), signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("live", " ".join(report["reasons"]).lower())

    def test_rejects_missing_mode_value(self):
        gate = StartupGate()
        config = RuntimeConfig(operating_mode=None)
        broker = SimulatedBrokerAdapter(account_id="ACC123")
        report = gate.certify_startup(config, broker, signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])

    def test_rejects_unknown_runtime_parameters(self):
        gate = StartupGate()
        config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            broker_account_id="ACC123",
            runtime_parameters={
                "base_dp_dt_multiplier": 1.2,
                "not_in_manifest": 99,
            },
            parameter_registry=CanonicalParameterRegistry(),
        )
        broker = PaperBrokerAdapter(account_id="ACC123")
        report = gate.certify_startup(config, broker, signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("unknown parameter", " ".join(report["reasons"]).lower())

    def test_accepts_canonical_surface_subset_for_runtime_startup(self):
        gate = StartupGate()
        config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            broker_account_id="ACC123",
            runtime_parameters={
                "base_dp_dt_multiplier": 1.2,
                "entry_confidence_threshold": 0.6,
                "max_positions_live": 4,
            },
            parameter_registry=CanonicalParameterRegistry(),
        )
        broker = PaperBrokerAdapter(account_id="ACC123")
        report = gate.certify_startup(config, broker, signing_key="abc123", durable_db=True)
        self.assertTrue(report["passed"])

    def test_rejects_out_of_range_runtime_parameters(self):
        gate = StartupGate()
        config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            broker_account_id="ACC123",
            runtime_parameters={
                "base_dp_dt_multiplier": 3.5,
                "entry_confidence_threshold": 0.9,
            },
            parameter_registry=CanonicalParameterRegistry(),
        )
        broker = PaperBrokerAdapter(account_id="ACC123")
        report = gate.certify_startup(config, broker, signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("range", " ".join(report["reasons"]).lower())

    def test_rejects_wrong_type_runtime_parameters(self):
        gate = StartupGate()
        config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            broker_account_id="ACC123",
            runtime_parameters={
                "max_positions_live": "not_an_int",
            },
            parameter_registry=CanonicalParameterRegistry(),
        )
        broker = PaperBrokerAdapter(account_id="ACC123")
        report = gate.certify_startup(config, broker, signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("type", " ".join(report["reasons"]).lower())

    def test_rejects_non_calibratable_or_fixed_calibration_updates(self):
        registry = CanonicalParameterRegistry()

        valid = registry.validate_calibration_payload({
            "base_dp_dt_multiplier": 1.2,
            "entry_confidence_threshold": 0.55,
            "vwap_weight": 0.35,
        })
        self.assertEqual(valid, [])

        unknown = registry.validate_calibration_payload({
            "base_dp_dt_multiplier": 1.2,
            "ghost_param": 123,
        })
        self.assertTrue(any("unknown parameter" in reason.lower() for reason in unknown))

        forbidden = registry.validate_calibration_payload({
            "base_dp_dt_multiplier": 1.2,
            "drawdown_halt_threshold": 0.30,
        })
        self.assertTrue(any("non-calibratable" in reason.lower() for reason in forbidden))

        out_of_range = registry.validate_calibration_payload({
            "base_dp_dt_multiplier": 9.0,
        })
        self.assertTrue(any("range" in reason.lower() for reason in out_of_range))

    def test_rejects_execution_payloads_that_violate_safety_contract(self):
        registry = CanonicalParameterRegistry()

        valid = registry.validate_execution_payload({
            "kill_switch_enabled": True,
            "drawdown_halt_threshold": 0.25,
            "max_daily_loss_rupees": 50000,
            "max_concurrent_positions": 5,
        })
        self.assertEqual(valid, [])

        kill_switch = registry.validate_execution_payload({
            "kill_switch_enabled": False,
        })
        self.assertTrue(any("kill switch" in reason.lower() for reason in kill_switch))

        risk_cap = registry.validate_execution_payload({
            "max_daily_loss_rupees": 150001,
        })
        self.assertTrue(any("range" in reason.lower() or "max daily" in reason.lower() for reason in risk_cap))

        unknown = registry.validate_execution_payload({
            "not_in_contract": True,
        })
        self.assertTrue(any("unknown parameter" in reason.lower() for reason in unknown))

    def test_execution_gate_rejects_trade_before_broker_submit(self):
        from runtime.operating_mode import ExecutionGate

        gate = ExecutionGate()
        registry = CanonicalParameterRegistry()
        valid = gate.validate_pre_submit(
            config={
                "kill_switch_enabled": True,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
            order={
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
            },
            parameter_registry=registry,
        )
        self.assertTrue(valid["passed"])

        invalid = gate.validate_pre_submit(
            config={
                "kill_switch_enabled": False,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
            order={
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
            },
            parameter_registry=registry,
        )
        self.assertFalse(invalid["passed"])
        self.assertIn("kill switch", " ".join(invalid["reasons"]).lower())

    def test_broker_submit_adapter_requires_contract_validation(self):
        from runtime.operating_mode import SafeBrokerAdapter

        broker = SafeBrokerAdapter(account_id="ACC123")
        broker.submitted = []

        result = broker.submit_order(
            symbol="NIFTY",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            config={
                "kill_switch_enabled": True,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
        )
        self.assertTrue(result["passed"])
        self.assertEqual(len(broker.submitted), 1)

        blocked = broker.submit_order(
            symbol="NIFTY",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            config={
                "kill_switch_enabled": False,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
        )
        self.assertFalse(blocked["passed"])

    def test_rejects_empty_runtime_config_and_conflicting_aliases(self):
        gate = StartupGate()
        registry = CanonicalParameterRegistry()

        empty_cfg = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            runtime_parameters={},
            parameter_registry=registry,
        )
        report = gate.certify_startup(empty_cfg, PaperBrokerAdapter(account_id="ACC123"), signing_key="abc123", durable_db=True)
        self.assertFalse(report["passed"])
        self.assertIn("runtime", " ".join(report["reasons"]).lower())

        alias_conflict = registry.validate_execution_payload({
            "drawdown_halt_threshold": 0.30,
            "safety_drawdown_halt_threshold": 0.25,
        })
        self.assertTrue(any("conflict" in reason.lower() or "alias" in reason.lower() for reason in alias_conflict))

    def test_requires_complete_safety_contract_and_broker_mode_match(self):
        registry = CanonicalParameterRegistry()
        partial = registry.validate_execution_payload({
            "kill_switch_enabled": True,
            "drawdown_halt_threshold": 0.25,
        })
        self.assertTrue(any("missing" in reason.lower() for reason in partial))

        gate = StartupGate()
        paper_cfg = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            runtime_parameters={
                "base_dp_dt_multiplier": 1.2,
                "entry_confidence_threshold": 0.6,
                "max_positions_live": 4,
            },
            parameter_registry=registry,
        )
        mismatch = gate.certify_startup(paper_cfg, KiteBrokerAdapter(account_id="ACC123"), signing_key="abc123", durable_db=True)
        self.assertFalse(mismatch["passed"])
        self.assertIn("broker", " ".join(mismatch["reasons"]).lower())

    def test_rejects_nonfinite_and_bool_order_quantities(self):
        from runtime.operating_mode import ExecutionGate

        gate = ExecutionGate()
        registry = CanonicalParameterRegistry()
        config = {
            "kill_switch_enabled": True,
            "safety_drawdown_halt_threshold": 0.25,
            "max_daily_loss_rupees": 50000,
            "max_concurrent_positions": 5,
            "max_gross_exposure_fraction": 0.50,
            "max_market_data_age_seconds": 30,
            "max_exposure_per_symbol_fraction": 0.15,
            "min_position_quantity": 1,
            "max_position_quantity": 100,
            "drawdown_derate_threshold": 0.18,
            "drawdown_derate_multiplier": 0.80,
            "lambda_derate_threshold": 0.15,
            "lambda_derate_multiplier": 0.80,
            "min_signal_confidence": 0.55,
            "safety_min_risk_reward_ratio": 1.50,
            "order_dedup_window_seconds": 5,
            "order_timeout_seconds_execution": 30,
            "max_reconciliation_qty_diff": 0,
            "max_slippage_fraction": 0.001,
            "no_entry_cutoff_time": "15:20",
        }

        bad_quantity = gate.validate_pre_submit(config, {"symbol": "INFY", "side": "BUY", "quantity": float("nan"), "order_type": "MARKET"}, parameter_registry=registry)
        self.assertFalse(bad_quantity["passed"])

        bool_quantity = gate.validate_pre_submit(config, {"symbol": "INFY", "side": "BUY", "quantity": True, "order_type": "MARKET"}, parameter_registry=registry)
        self.assertFalse(bool_quantity["passed"])

        empty_symbol = gate.validate_pre_submit(config, {"symbol": "", "side": "BUY", "quantity": 5, "order_type": "MARKET"}, parameter_registry=registry)
        self.assertFalse(empty_symbol["passed"])

    def test_end_to_end_trade_cycle_accepts_valid_contract_and_blocks_invalid_one(self):
        from runtime.operating_mode import simulate_trade_cycle

        valid = simulate_trade_cycle(
            runtime_config=RuntimeConfig(
                operating_mode=OperatingMode.PAPER,
                live_trading_enabled=False,
                broker_account_id="ACC123",
                runtime_parameters={
                    "base_dp_dt_multiplier": 1.2,
                    "entry_confidence_threshold": 0.6,
                    "max_positions_live": 4,
                },
                parameter_registry=CanonicalParameterRegistry(),
            ),
            broker=PaperBrokerAdapter(account_id="ACC123"),
            safety_config={
                "kill_switch_enabled": True,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
            order={
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
            },
        )
        self.assertTrue(valid["passed"])
        self.assertTrue(valid["submitted"])

        invalid = simulate_trade_cycle(
            runtime_config=RuntimeConfig(
                operating_mode=OperatingMode.PAPER,
                live_trading_enabled=False,
                broker_account_id="ACC123",
                runtime_parameters={
                    "base_dp_dt_multiplier": 1.2,
                    "entry_confidence_threshold": 0.6,
                    "max_positions_live": 4,
                },
                parameter_registry=CanonicalParameterRegistry(),
            ),
            broker=PaperBrokerAdapter(account_id="ACC123"),
            safety_config={
                "kill_switch_enabled": False,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
            order={
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
            },
        )
        self.assertFalse(invalid["passed"])
        self.assertIn("kill switch", " ".join(invalid["reasons"]).lower())

    def test_contract_validator_service_checks_every_stage(self):
        from runtime.contract_validator import ContractValidator

        validator = ContractValidator()
        ok = validator.validate_full_cycle(
            runtime_config=RuntimeConfig(
                operating_mode=OperatingMode.PAPER,
                live_trading_enabled=False,
                broker_account_id="ACC123",
                runtime_parameters={
                    "base_dp_dt_multiplier": 1.2,
                    "entry_confidence_threshold": 0.55,
                    "max_positions_live": 4,
                },
                parameter_registry=CanonicalParameterRegistry(),
            ),
            safety_config={
                "kill_switch_enabled": True,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
            order={
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
            },
        )
        self.assertTrue(ok["passed"])

        bad = validator.validate_full_cycle(
            runtime_config=RuntimeConfig(
                operating_mode=OperatingMode.PAPER,
                live_trading_enabled=False,
                broker_account_id="ACC123",
                runtime_parameters={
                    "base_dp_dt_multiplier": 9.0,
                },
                parameter_registry=CanonicalParameterRegistry(),
            ),
            safety_config={
                "kill_switch_enabled": False,
                "drawdown_halt_threshold": 0.25,
                "max_daily_loss_rupees": 50000,
                "max_concurrent_positions": 5,
            },
            order={
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
            },
        )
        self.assertFalse(bad["passed"])
        self.assertTrue(any("range" in reason.lower() or "kill switch" in reason.lower() for reason in bad["reasons"]))

if __name__ == "__main__":
    unittest.main()
