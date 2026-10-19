from revision5.dynamic_parameters import R5EnvironmentState
from revision7_avr.r7_r5_dynamic_adapter import (
    R7DynamicParameterAdapter,
    flatten_snapshot,
)


def test_shadow_uses_the_complete_r5_snapshot_without_constants():
    adapter = R7DynamicParameterAdapter("shadow")
    values = adapter.evaluate(R5EnvironmentState())

    assert values
    assert "version" in values
    assert "stress_index" in values
    assert any(key.startswith("governor_by_bay.") for key in values)
    assert any(key.startswith("avr_by_bay.") for key in values)
    assert "strategy.max_hold_bars" in values
    assert "safety.max_gross_exposure_fraction" in values


def test_off_does_not_evaluate_or_apply():
    adapter = R7DynamicParameterAdapter("off")
    assert adapter.evaluate(R5EnvironmentState()) == {}
    assert adapter.last_snapshot is None


def test_max_hold_can_only_tighten():
    adapter = R7DynamicParameterAdapter("paper")
    assert adapter.effective_max_hold_bars(60, R5EnvironmentState()) <= 60


def test_safety_limits_can_only_tighten():
    adapter = R7DynamicParameterAdapter("paper")
    current = {
        "safety.max_gross_exposure_fraction": 1.0,
        "safety.max_symbol_exposure_fraction": 0.5,
        "safety.max_concurrent_positions": 10.0,
        "safety.daily_loss_limit_multiplier": 1.0,
    }
    result = adapter.tighten_safety_limits(current, R5EnvironmentState())
    for key, value in result.items():
        assert value <= current[key]


def test_flatten_is_recursive_and_stable():
    adapter = R7DynamicParameterAdapter("shadow")
    snapshot = adapter.last_snapshot
    assert snapshot is None
    values = adapter.evaluate(R5EnvironmentState())
    assert values == flatten_snapshot(adapter.last_snapshot)
