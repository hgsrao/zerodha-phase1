import math

import pytest

from revision5.governor import (
    BAY_GOVERNOR_SPECS,
    BayTurbineClosedLoopGovernor,
    build_fleet_governors,
)
from revision5.topology import (
    BAY_IDS,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    BPSTG_HEALTHCARE,
)


def test_exactly_five_governor_specs():
    assert set(BAY_GOVERNOR_SPECS) == set(BAY_IDS)
    assert len(BAY_GOVERNOR_SPECS) == 5


def test_initial_capital_weights_sum_to_one():
    total = sum(
        spec.capital_weight
        for spec in BAY_GOVERNOR_SPECS.values()
    )
    assert total == pytest.approx(1.0)


def test_droop_classes():
    assert BAY_GOVERNOR_SPECS[GTG1_HEAVY_INDUSTRY].droop_r == 0.040
    assert BAY_GOVERNOR_SPECS[GTG2_TECH_TELECOM].droop_r == 0.040
    assert BAY_GOVERNOR_SPECS[CSTG1_BFSI].droop_r == 0.055
    assert BAY_GOVERNOR_SPECS[CSTG2_CONSUMER_AUTO].droop_r == 0.055
    assert BAY_GOVERNOR_SPECS[BPSTG_HEALTHCARE].droop_r == 0.075


def test_target_is_explicitly_r_multiple():
    for spec in BAY_GOVERNOR_SPECS.values():
        assert spec.target_r == pytest.approx(0.30)


def test_loss_tightens_governor():
    spec = BAY_GOVERNOR_SPECS[GTG2_TECH_TELECOM]
    gov = BayTurbineClosedLoopGovernor(spec)

    baseline = gov.dynamic_z()

    gov.register_trade(-1.0)

    assert gov.dynamic_z() < baseline


def test_win_relaxes_governor():
    spec = BAY_GOVERNOR_SPECS[GTG2_TECH_TELECOM]
    gov = BayTurbineClosedLoopGovernor(spec)

    baseline = gov.dynamic_z()

    gov.register_trade(+1.5)

    assert gov.dynamic_z() > baseline


def test_pid_coefficients_never_mutate():
    spec = BAY_GOVERNOR_SPECS[GTG1_HEAVY_INDUSTRY]
    frozen = (spec.kp, spec.ki, spec.kd)

    gov = BayTurbineClosedLoopGovernor(spec)

    for r_value in (-1.0, 2.0, -0.5, 1.5):
        gov.register_trade(r_value)

    assert (spec.kp, spec.ki, spec.kd) == frozen


def test_adverse_grid_tightens_threshold():
    spec = BAY_GOVERNOR_SPECS[GTG1_HEAVY_INDUSTRY]
    gov = BayTurbineClosedLoopGovernor(spec)

    neutral = gov.dynamic_z(0.0)
    adverse = gov.dynamic_z(-0.01)

    assert adverse < neutral


def test_history_and_integral_are_bounded():
    spec = BAY_GOVERNOR_SPECS[BPSTG_HEALTHCARE]
    gov = BayTurbineClosedLoopGovernor(spec)

    for _ in range(100):
        gov.register_trade(-10.0)

    assert len(gov.history_r) == spec.outcome_window
    assert abs(gov.integral_error) <= spec.integral_clamp


def test_invalid_numeric_feedback_fails_closed():
    spec = BAY_GOVERNOR_SPECS[CSTG1_BFSI]
    gov = BayTurbineClosedLoopGovernor(spec)

    with pytest.raises(ValueError):
        gov.register_trade(math.nan)

    with pytest.raises(ValueError):
        gov.dynamic_z(math.inf)


def test_build_fleet_governors_matches_topology():
    fleet = build_fleet_governors()

    assert set(fleet) == set(BAY_IDS)
    assert len(fleet) == 5
