import math

import pytest

from revision5.dynamic_parameters import (
    R5EnvironmentState,
    Revision5DynamicParameterController,
)
from revision5.governor import BAY_GOVERNOR_SPECS


def test_snapshot_contains_all_five_bays():
    controller = Revision5DynamicParameterController()

    snap = controller.evaluate(
        R5EnvironmentState()
    )

    assert set(snap.governor_by_bay) == set(
        BAY_GOVERNOR_SPECS
    )


def test_snapshot_is_dynamic_across_regimes():
    controller = Revision5DynamicParameterController()

    calm = controller.evaluate(
        R5EnvironmentState(
            normalized_vol=0.8,
            trend_probability=0.80,
            chop_probability=0.15,
            turbulent_probability=0.05,
            fleet_drawdown_fraction=0.0,
            control_error_delta=0.0,
        )
    )

    turbulent = controller.evaluate(
        R5EnvironmentState(
            normalized_vol=2.0,
            trend_probability=0.10,
            chop_probability=0.20,
            turbulent_probability=0.70,
            fleet_drawdown_fraction=0.05,
            control_error_delta=0.20,
        )
    )

    assert (
        calm.strategy.entry_confidence_threshold
        != turbulent.strategy.entry_confidence_threshold
    )

    assert (
        calm.plant.loss_cooldown_bars
        != turbulent.plant.loss_cooldown_bars
    )

    assert (
        calm.safety.max_gross_exposure_fraction
        > turbulent.safety.max_gross_exposure_fraction
    )


def test_r5_base_governor_specs_are_never_mutated():
    before = {
        bay: (
            spec.target_r,
            spec.outcome_window,
            spec.integral_clamp,
            spec.grid_droop_gain,
            spec.grid_droop_max,
            spec.kp,
            spec.ki,
            spec.kd,
        )
        for bay, spec
        in BAY_GOVERNOR_SPECS.items()
    }

    controller = Revision5DynamicParameterController()

    controller.evaluate(
        R5EnvironmentState(
            normalized_vol=2.2,
            trend_probability=0.05,
            chop_probability=0.20,
            turbulent_probability=0.75,
            fleet_drawdown_fraction=0.08,
            control_error_delta=0.30,
        )
    )

    after = {
        bay: (
            spec.target_r,
            spec.outcome_window,
            spec.integral_clamp,
            spec.grid_droop_gain,
            spec.grid_droop_max,
            spec.kp,
            spec.ki,
            spec.kd,
        )
        for bay, spec
        in BAY_GOVERNOR_SPECS.items()
    }

    assert after == before


def test_dynamic_safety_can_only_tighten_neutral_envelopes():
    controller = Revision5DynamicParameterController()

    snap = controller.evaluate(
        R5EnvironmentState(
            normalized_vol=2.5,
            trend_probability=0.05,
            chop_probability=0.15,
            turbulent_probability=0.80,
            fleet_drawdown_fraction=0.10,
            control_error_delta=0.20,
        )
    )

    assert (
        snap.safety.drawdown_halt_threshold
        <= 0.25
    )
    assert (
        snap.safety.max_gross_exposure_fraction
        <= 0.50
    )
    assert (
        snap.safety.max_symbol_exposure_fraction
        <= 0.15
    )
    assert (
        snap.safety.max_concurrent_positions
        <= 5
    )
    assert (
        snap.safety.daily_loss_limit_multiplier
        <= 1.0
    )


def test_invalid_numeric_environment_fails_closed():
    controller = Revision5DynamicParameterController()

    with pytest.raises(ValueError):
        controller.evaluate(
            R5EnvironmentState(
                normalized_vol=math.nan
            )
        )

    with pytest.raises(ValueError):
        controller.evaluate(
            R5EnvironmentState(
                trend_probability=-0.1,
                chop_probability=0.5,
                turbulent_probability=0.6,
            )
        )


def test_probability_inputs_are_normalized():
    controller = Revision5DynamicParameterController()

    snap = controller.evaluate(
        R5EnvironmentState(
            trend_probability=7.0,
            chop_probability=2.0,
            turbulent_probability=1.0,
        )
    )

    assert snap.trend_probability == pytest.approx(
        0.70
    )
    assert snap.chop_probability == pytest.approx(
        0.20
    )
    assert snap.turbulent_probability == pytest.approx(
        0.10
    )


def test_snapshot_versions_advance():
    controller = Revision5DynamicParameterController()

    first = controller.evaluate(
        R5EnvironmentState()
    )

    second = controller.evaluate(
        R5EnvironmentState()
    )

    assert second.version == first.version + 1


def test_turbulence_changes_governor_runtime_not_base():
    controller = Revision5DynamicParameterController()

    calm = controller.evaluate(
        R5EnvironmentState(
            normalized_vol=0.8,
            trend_probability=0.8,
            chop_probability=0.15,
            turbulent_probability=0.05,
        )
    )

    stressed = controller.evaluate(
        R5EnvironmentState(
            normalized_vol=2.0,
            trend_probability=0.1,
            chop_probability=0.2,
            turbulent_probability=0.7,
            control_error_delta=0.25,
        )
    )

    for bay in BAY_GOVERNOR_SPECS:
        a = calm.governor_by_bay[bay]
        b = stressed.governor_by_bay[bay]

        assert (
            a.integral_clamp
            != b.integral_clamp
        )

        assert a.kp != b.kp
        assert a.ki != b.ki
        assert a.kd != b.kd


def test_dynamic_ranges_remain_sane():
    controller = Revision5DynamicParameterController()

    snap = controller.evaluate(
        R5EnvironmentState(
            normalized_vol=2.5,
            trend_probability=0.0,
            chop_probability=0.0,
            turbulent_probability=1.0,
            fleet_drawdown_fraction=0.20,
            control_error_delta=1.0,
        )
    )

    assert 0.02 <= (
        snap.strategy.entry_confidence_threshold
    ) <= 0.35

    assert 1.0 <= (
        snap.strategy.min_risk_reward_ratio
    ) <= 3.0

    assert 8 <= (
        snap.plant.loss_cooldown_bars
    ) <= 30

    assert 2 <= (
        snap.plant.target_cooldown_bars
    ) <= 8

    assert 0.0005 <= (
        snap.execution.slippage_tolerance_fraction
    ) <= 0.0020
