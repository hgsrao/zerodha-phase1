"""Tests for v34_bridge_runner_main.py.

PHASE 3.6: _build_engine() no longer refuses with NotImplementedError -
v34_bridge_runner_startup.build_production_engine() (pure integration of
already-sealed 3.1-3.5A components) now genuinely builds a real,
production-shaped TradingEngineV34P02. These tests prove that real
construction actually happens end to end through THIS file's own
environment wiring (RUNNER_DATA_DIR, credentials, Config), and that
main()'s lock-lifecycle guarantee (release in `finally`, on every normal
exit path including a halt) actually holds - not a test of the container
itself (no Docker involved here), that's the Dockerfile's own job.
"""

import sys
import types

import pytest

import v34_bridge_runner_main as runner_main
from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders


class FakeKiteConnectSDK(FakeKiteConnectWithOrders):
    """Matches the real kiteconnect.KiteConnect construction protocol
    v34_bridge_kite_credentials.build_kite_client_from_env() actually
    uses (`KiteConnect(api_key=...)` then `.set_access_token(token)`),
    built on the same full-featured fake every other Phase 3.6 test in
    this project already uses - not a second, independently-drifting
    fake."""

    def __init__(self, api_key=None):
        super().__init__()
        self.api_key = api_key
        self.access_token = None

    def set_access_token(self, token):
        self.access_token = token


def _set_working_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("KITE_API_KEY", "some-key")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "some-token")
    monkeypatch.setenv("RUNNER_DATA_DIR", str(tmp_path / "runner_data"))
    monkeypatch.setenv("DP_CHARGE_PER_SYMBOL", "15.34")  # Gate 3 - now a required, fail-closed value
    monkeypatch.setenv("KITE_RATE_GOVERNOR_DIR", str(tmp_path / "kite_rate_governor"))  # EA1-R1 - required, fail-closed
    fake_module = types.ModuleType("kiteconnect")
    fake_module.KiteConnect = FakeKiteConnectSDK
    monkeypatch.setitem(sys.modules, "kiteconnect", fake_module)


def test_live_trading_enabled_is_hardcoded_false():
    assert runner_main.LIVE_TRADING_ENABLED is False


def test_build_engine_constructs_a_real_running_engine_with_working_credentials(monkeypatch, tmp_path):
    _set_working_credentials(monkeypatch, tmp_path)
    engine = runner_main._build_engine(on_event=lambda msg: None)
    assert engine.terminator.halted is False
    from v34_p02_state import EngineStatus
    assert engine.state.status == EngineStatus.RUNNING
    engine.lock_provider.release()


def test_build_engine_still_refuses_without_credentials(monkeypatch, tmp_path):
    # Construction genuinely needs real credentials (to build the real
    # Kite connection) - this is a real, expected FAIL_CLOSED from
    # build_kite_client_from_env(), not the old NotImplementedError.
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("RUNNER_DATA_DIR", str(tmp_path / "runner_data"))
    with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_API_KEY"):
        runner_main._build_engine(on_event=lambda msg: None)


def test_main_calls_run_forever_and_releases_the_lock_on_a_clean_return(monkeypatch, tmp_path):
    _set_working_credentials(monkeypatch, tmp_path)
    monkeypatch.setenv("PLAN_STORE_DIR", str(tmp_path / "plans"))
    called = []
    monkeypatch.setattr("v34_bridge_runner_entrypoint.run_forever", lambda **kwargs: called.append(kwargs))

    runner_main.main()
    assert len(called) == 1

    # The lock was released by main()'s own finally block - a fresh
    # build_engine() call must succeed again, proving it's actually free.
    engine2 = runner_main._build_engine(on_event=lambda msg: None)
    engine2.lock_provider.release()


def test_main_releases_the_lock_even_when_run_forever_raises(monkeypatch, tmp_path):
    _set_working_credentials(monkeypatch, tmp_path)
    monkeypatch.setenv("PLAN_STORE_DIR", str(tmp_path / "plans"))

    def boom(**kwargs):
        raise RuntimeError("simulated daemon crash")
    monkeypatch.setattr("v34_bridge_runner_entrypoint.run_forever", boom)

    with pytest.raises(RuntimeError, match="simulated daemon crash"):
        runner_main.main()

    # Exceptional shutdown must not falsely leave the lock held either -
    # a fresh construction must still succeed.
    engine2 = runner_main._build_engine(on_event=lambda msg: None)
    engine2.lock_provider.release()


class TestParseDpChargePerSymbol:
    """Gate 3 requirement #2: fail-closed on every malformed shape, never
    a silent default to 0."""

    def test_a_valid_positive_value_parses(self):
        from decimal import Decimal
        assert runner_main._parse_dp_charge_per_symbol("15.34") == Decimal("15.34")

    def test_none_raises(self):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: DP_CHARGE_PER_SYMBOL.*missing"):
            runner_main._parse_dp_charge_per_symbol(None)

    def test_empty_string_raises(self):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: DP_CHARGE_PER_SYMBOL.*missing"):
            runner_main._parse_dp_charge_per_symbol("")

    def test_whitespace_only_raises(self):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: DP_CHARGE_PER_SYMBOL.*missing"):
            runner_main._parse_dp_charge_per_symbol("   ")

    def test_non_numeric_raises(self):
        with pytest.raises(RuntimeError, match="not a valid decimal"):
            runner_main._parse_dp_charge_per_symbol("fifteen")

    def test_nan_raises(self):
        with pytest.raises(RuntimeError, match="finite"):
            runner_main._parse_dp_charge_per_symbol("NaN")

    def test_infinity_raises(self):
        with pytest.raises(RuntimeError, match="finite"):
            runner_main._parse_dp_charge_per_symbol("Infinity")

    def test_zero_raises(self):
        with pytest.raises(RuntimeError, match="must be positive"):
            runner_main._parse_dp_charge_per_symbol("0")

    def test_negative_raises(self):
        with pytest.raises(RuntimeError, match="must be positive"):
            runner_main._parse_dp_charge_per_symbol("-5")


class TestBuildEngineRefusesWithoutDpCharge:
    def test_missing_dp_charge_per_symbol_prevents_construction(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.delenv("DP_CHARGE_PER_SYMBOL", raising=False)
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: DP_CHARGE_PER_SYMBOL"):
            runner_main._build_engine(on_event=lambda msg: None)

    def test_zero_dp_charge_per_symbol_prevents_construction(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.setenv("DP_CHARGE_PER_SYMBOL", "0")
        with pytest.raises(RuntimeError, match="must be positive"):
            runner_main._build_engine(on_event=lambda msg: None)


class TestParseKiteRateGovernorDir:
    """EA1-R1: fail-closed, same discipline as DP_CHARGE_PER_SYMBOL above -
    an unset value must never silently disable cross-process rate
    governance."""

    def test_a_valid_path_parses(self, tmp_path):
        assert runner_main._parse_kite_rate_governor_dir(str(tmp_path)) == tmp_path

    def test_none_raises(self):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR.*missing"):
            runner_main._parse_kite_rate_governor_dir(None)

    def test_empty_string_raises(self):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR.*missing"):
            runner_main._parse_kite_rate_governor_dir("")

    def test_whitespace_only_raises(self):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR.*missing"):
            runner_main._parse_kite_rate_governor_dir("   ")


class TestBuildEngineRefusesWithoutKiteRateGovernorDir:
    def test_missing_governor_dir_prevents_construction(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.delenv("KITE_RATE_GOVERNOR_DIR", raising=False)
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR"):
            runner_main._build_engine(on_event=lambda msg: None)


class TestBuildEngineWiresARealGovernorIntoTheAdapter:
    def test_the_constructed_engines_broker_has_a_real_governor(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        engine = runner_main._build_engine(on_event=lambda msg: None)
        try:
            from kite_request_governor import KiteRequestGovernor
            assert isinstance(engine.broker.governor, KiteRequestGovernor)
            assert engine.broker.governor.state_dir == tmp_path / "kite_rate_governor"
        finally:
            engine.lock_provider.release()


class TestShadowModeEnvVar:
    """EA-1: SHADOW_MODE is environment-controlled (unlike LIVE_TRADING_
    ENABLED), defaults to False (today's unchanged behavior)."""

    def test_defaults_to_off_when_unset(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.delenv("SHADOW_MODE", raising=False)
        engine = runner_main._build_engine(on_event=lambda msg: None)
        assert engine.broker.raw_broker.__class__.__name__ == "KiteBrokerClient"
        engine.lock_provider.release()

    def test_true_activates_shadow_mode(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.setenv("SHADOW_MODE", "true")
        engine = runner_main._build_engine(on_event=lambda msg: None)
        assert engine.broker.raw_broker.__class__.__name__ == "ShadowModeBrokerClient"
        engine.lock_provider.release()

    def test_case_insensitive(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.setenv("SHADOW_MODE", "True")
        engine = runner_main._build_engine(on_event=lambda msg: None)
        assert engine.broker.raw_broker.__class__.__name__ == "ShadowModeBrokerClient"
        engine.lock_provider.release()

    def test_any_other_value_stays_off(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.setenv("SHADOW_MODE", "1")  # only the literal "true" (any case) opts in
        engine = runner_main._build_engine(on_event=lambda msg: None)
        assert engine.broker.raw_broker.__class__.__name__ == "KiteBrokerClient"
        engine.lock_provider.release()

    def test_live_trading_enabled_stays_hardcoded_false_regardless_of_shadow_mode(self, monkeypatch, tmp_path):
        _set_working_credentials(monkeypatch, tmp_path)
        monkeypatch.setenv("SHADOW_MODE", "true")
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")  # must have zero effect - not read anywhere
        engine = runner_main._build_engine(on_event=lambda msg: None)
        assert runner_main.LIVE_TRADING_ENABLED is False
        engine.lock_provider.release()


def test_main_does_not_call_run_forever_when_the_engine_is_already_halted(monkeypatch, tmp_path):
    _set_working_credentials(monkeypatch, tmp_path)
    monkeypatch.setenv("PLAN_STORE_DIR", str(tmp_path / "plans"))

    # Build a real engine the normal way, then forcibly halt it - proves
    # main()'s own halted-engine branch specifically, independent of
    # which startup-sequence step actually caused the halt (that's
    # already covered by test_v34_bridge_runner_startup.py).
    engine = runner_main._build_engine(on_event=lambda msg: None)
    engine.terminator.halt("pre-halted for this test")
    monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: engine)

    called = []
    monkeypatch.setattr("v34_bridge_runner_entrypoint.run_forever", lambda **kwargs: called.append(kwargs))

    runner_main.main()  # must not raise - a halted engine at startup is an honest, clean exit
    assert called == []
    engine.lock_provider.release()
