"""BB01/BB02 ownership and R5-supervisory-boundary regression tests."""

from __future__ import annotations

import inspect

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.boxes import DataIngestionBox
from revision5.supervisory_bridge import Revision5SupervisoryBridge


def _runtime_values(registry):
    return {
        name: spec.default
        for name, spec in registry.params.items()
    }


def _safety_values(registry):
    return {
        name: spec.default
        for name, spec in registry.safety_params.items()
    }


def test_bb01_bb02_registry_ownership_and_fixed_safety_contract():
    registry = CanonicalParameterRegistry()

    for name in ("symbols_to_trade", "exclude_symbols"):
        spec = registry.get(name)
        assert spec.black_box == "DataIngestion"
        assert spec.default == []
        assert spec.calibratable is False

    kill_switch = registry.safety_params["kill_switch_enabled"]
    assert kill_switch.black_box == "StartupCapabilityLock"
    assert kill_switch.default is True
    assert kill_switch.calibratable is False


def test_bb01_rejects_attempt_to_disable_fixed_kill_switch():
    registry = CanonicalParameterRegistry()
    bridge = Revision5SupervisoryBridge(registry)
    safety = _safety_values(registry)
    safety["kill_switch_enabled"] = False

    result = bridge.evaluate_upstream_admission(
        symbol="INFY",
        runtime_parameters=_runtime_values(registry),
        safety_parameters=safety,
    )

    assert result.admitted is False
    assert result.config_hash is None
    assert result.reason.startswith("BB01_STARTUP_REJECTED:")
    assert "kill switch cannot be disabled" in result.reason


def test_bb02_allow_and_deny_lists_propagate_to_real_consumer():
    registry = CanonicalParameterRegistry()
    bridge = Revision5SupervisoryBridge(registry)
    runtime = _runtime_values(registry)
    runtime["symbols_to_trade"] = ["INFY"]

    admitted = bridge.evaluate_upstream_admission(
        symbol="INFY",
        runtime_parameters=runtime,
        safety_parameters=_safety_values(registry),
    )
    rejected = bridge.evaluate_upstream_admission(
        symbol="TCS",
        runtime_parameters=runtime,
        safety_parameters=_safety_values(registry),
    )

    assert admitted.admitted is True
    assert admitted.reason == "BB02_ADMITTED"
    assert admitted.consumed_parameters == (
        "symbols_to_trade", "exclude_symbols",
    )
    assert admitted.config_hash is not None
    assert rejected.admitted is False
    assert "NOT_IN_SYMBOLS_TO_TRADE_UNIVERSE" in rejected.reason

    runtime["exclude_symbols"] = ["INFY"]
    denied = bridge.evaluate_upstream_admission(
        symbol="INFY",
        runtime_parameters=runtime,
        safety_parameters=_safety_values(registry),
    )
    assert denied.admitted is False
    assert "IS_IN_EXCLUDE_SYMBOLS" in denied.reason


def test_bb02_has_no_anonymous_operational_parameter_reads():
    source = inspect.getsource(DataIngestionBox.admit)
    assert 'req("symbols_to_trade"' in source
    assert 'req("exclude_symbols"' in source
    assert ".get(" not in source
