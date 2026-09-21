import pytest

from canonical_parameter_registry import (
    CanonicalParameterRegistry,
)
from revision2_external.dynamic_parameter_controller import (
    DynamicParameterController,
    MarketEnvironmentState,
)
from revision5.ccpp_unified_plant import (
    LOSS_COOLDOWN_BARS,
    TARGET_COOLDOWN_BARS,
)
from revision5.governor import (
    BAY_GOVERNOR_SPECS,
)
from revision5.supervisory_bridge import (
    Revision5SupervisoryBridge,
)


@pytest.fixture
def bridge():
    return Revision5SupervisoryBridge(
        CanonicalParameterRegistry()
    )


@pytest.fixture
def trend_env():
    return MarketEnvironmentState(
        symbol="INFY",
        close=1500.0,
        current_atr=15.0,
        historical_atr_sma=15.0,
        regime_probs={
            "trend": 0.70,
            "chop": 0.20,
            "turbulent": 0.10,
        },
        normalized_vol=1.0,
    )


def test_tier1_is_partitioned_between_strategy_and_execution(
    bridge,
    trend_env,
):
    result = bridge.evaluate(
        trend_env,
        base_kp=0.15,
        base_ki=0.05,
        base_kd=0.08,
        error_delta=0.10,
    )

    assert "min_atr_floor" in (
        result.strategy_advisory
    )

    assert "atr_floor_mult" in (
        result.strategy_advisory
    )

    assert "dynamic_slippage" in (
        result.execution_advisory
    )

    assert "dynamic_slippage" not in (
        result.strategy_advisory
    )


def test_tier2_entry_threshold_fails_closed_against_current_registry(
    bridge,
    trend_env,
):
    result = bridge.evaluate(
        trend_env,
        base_kp=0.15,
        base_ki=0.05,
        base_kd=0.08,
        error_delta=0.10,
    )

    # Current DPC Tier-2 emits values above the present canonical
    # entry-confidence range, so the bridge must reject the request.
    assert (
        "entry_confidence_threshold"
        in result.blocked
    )

    assert (
        "entry_confidence_threshold"
        not in result.strategy_advisory
    )


def test_dpc_cannot_override_r5_fixed_cooldowns(
    bridge,
    trend_env,
):
    before = (
        LOSS_COOLDOWN_BARS,
        TARGET_COOLDOWN_BARS,
    )

    result = bridge.evaluate(
        trend_env,
        base_kp=0.15,
        base_ki=0.05,
        base_kd=0.08,
        error_delta=0.10,
    )

    assert "bay_cooldown_bars" in (
        result.blocked
    )

    assert (
        LOSS_COOLDOWN_BARS,
        TARGET_COOLDOWN_BARS,
    ) == before

    assert LOSS_COOLDOWN_BARS == 15
    assert TARGET_COOLDOWN_BARS == 3


def test_tier3_schedule_is_computed_but_r5_governor_specs_do_not_mutate(
    bridge,
    trend_env,
):
    before = {
        bay_id: (
            spec.kp,
            spec.ki,
            spec.kd,
        )
        for bay_id, spec
        in BAY_GOVERNOR_SPECS.items()
    }

    expected = (
        DynamicParameterController
        .get_tier3_pid_schedule(
            0.15,
            0.05,
            0.08,
            0.25,
        )
    )

    result = bridge.evaluate(
        trend_env,
        base_kp=0.15,
        base_ki=0.05,
        base_kd=0.08,
        error_delta=0.25,
    )

    assert (
        result.tier3_pid_schedule
        == pytest.approx(expected)
    )

    assert "tier3_pid_schedule" in (
        result.blocked
    )

    after = {
        bay_id: (
            spec.kp,
            spec.ki,
            spec.kd,
        )
        for bay_id, spec
        in BAY_GOVERNOR_SPECS.items()
    }

    assert after == before


def test_tier2_hold_windows_are_advisory_only(
    bridge,
    trend_env,
):
    result = bridge.evaluate(
        trend_env,
        base_kp=0.15,
        base_ki=0.05,
        base_kd=0.08,
        error_delta=0.0,
    )

    min_hold = result.strategy_advisory[
        "min_hold_bars"
    ]

    max_hold = result.strategy_advisory[
        "max_hold_bars"
    ]

    assert 2 <= min_hold <= 6
    assert 12 <= max_hold <= 60
    assert min_hold <= max_hold


def test_bridge_snapshot_is_deterministic_and_read_only(
    bridge,
    trend_env,
):
    kwargs = dict(
        base_kp=0.15,
        base_ki=0.05,
        base_kd=0.08,
        error_delta=0.17,
    )

    a = bridge.evaluate(
        trend_env,
        **kwargs,
    )

    b = bridge.evaluate(
        trend_env,
        **kwargs,
    )

    assert dict(
        a.strategy_advisory
    ) == dict(
        b.strategy_advisory
    )

    assert dict(
        a.execution_advisory
    ) == dict(
        b.execution_advisory
    )

    assert a.tier3_pid_schedule == pytest.approx(
        b.tier3_pid_schedule
    )

    with pytest.raises(TypeError):
        a.strategy_advisory[
            "min_hold_bars"
        ] = 999
