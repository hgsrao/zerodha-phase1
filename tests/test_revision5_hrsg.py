import pytest

from revision5.hrsg import (
    GAS_TURBINE_BAYS,
    STEAM_TURBINE_BAYS,
    HeatRecoverySteamGenerator,
)
from revision5.topology import (
    BAY_IDS,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    BPSTG_HEALTHCARE,
)


@pytest.fixture
def hrsg():
    return HeatRecoverySteamGenerator(
        base_plant_capital=1_000_000.0,
        recovery_fraction=0.60,
        rho_crit=0.65,
        max_superheat_boost=0.15,
    )


@pytest.fixture
def base_allocations():
    return {
        GTG1_HEAVY_INDUSTRY: 250_000.0,
        GTG2_TECH_TELECOM: 200_000.0,
        CSTG1_BFSI: 250_000.0,
        CSTG2_CONSUMER_AUTO: 180_000.0,
        BPSTG_HEALTHCARE: 120_000.0,
    }


@pytest.fixture
def healthy_states():
    return {
        bay_id: {
            "tripped_offline": False,
            "cooldown_bars_remaining": 0,
        }
        for bay_id in BAY_IDS
    }


def test_gtg_and_stg_partition_is_exact():
    assert set(
        GAS_TURBINE_BAYS
    ).isdisjoint(
        STEAM_TURBINE_BAYS
    )

    assert (
        set(GAS_TURBINE_BAYS)
        | set(STEAM_TURBINE_BAYS)
    ) == set(BAY_IDS)


def test_healthy_plant_is_allocation_neutral(
    hrsg,
    base_allocations,
    healthy_states,
):
    result = hrsg.balance_capital(
        bay_states=healthy_states,
        base_allocations=base_allocations,
    )

    assert result.allocations == pytest.approx(
        base_allocations
    )

    assert result.reserve_cash == pytest.approx(
        0.0
    )

    assert (
        result.curtailed_gtg_capital
        == pytest.approx(0.0)
    )

    assert (
        result.recovered_capital
        == pytest.approx(0.0)
    )


def test_gtg_trip_recovers_only_fraction_and_keeps_reserve(
    hrsg,
    base_allocations,
    healthy_states,
):
    states = {
        bay: dict(state)
        for bay, state
        in healthy_states.items()
    }

    states[
        GTG1_HEAVY_INDUSTRY
    ]["tripped_offline"] = True

    result = hrsg.balance_capital(
        bay_states=states,
        base_allocations=base_allocations,
    )

    assert result.allocations[
        GTG1_HEAVY_INDUSTRY
    ] == pytest.approx(0.0)

    assert (
        result.curtailed_gtg_capital
        == pytest.approx(250_000.0)
    )

    assert (
        result.recovered_capital
        == pytest.approx(150_000.0)
    )

    # 40% of GTG1 allocation remains reserve.
    assert result.reserve_cash == pytest.approx(
        100_000.0
    )

    assert result.allocations[
        CSTG1_BFSI
    ] > base_allocations[
        CSTG1_BFSI
    ]

    assert result.allocations[
        CSTG2_CONSUMER_AUTO
    ] > base_allocations[
        CSTG2_CONSUMER_AUTO
    ]

    assert result.allocations[
        BPSTG_HEALTHCARE
    ] > base_allocations[
        BPSTG_HEALTHCARE
    ]

    assert (
        sum(result.allocations.values())
        + result.reserve_cash
    ) == pytest.approx(
        1_000_000.0
    )


def test_bar_based_gtg_cooldown_is_treated_as_unavailable(
    hrsg,
    base_allocations,
    healthy_states,
):
    states = {
        bay: dict(state)
        for bay, state
        in healthy_states.items()
    }

    states[
        GTG2_TECH_TELECOM
    ]["cooldown_bars_remaining"] = 7

    result = hrsg.balance_capital(
        bay_states=states,
        base_allocations=base_allocations,
    )

    assert result.allocations[
        GTG2_TECH_TELECOM
    ] == pytest.approx(0.0)

    assert (
        result.curtailed_gtg_capital
        == pytest.approx(200_000.0)
    )


def test_synchronous_high_correlation_triggers_damper(
    hrsg,
):
    sequence = [
        0.02,
        -0.01,
        0.03,
        -0.02,
        0.04,
        -0.01,
        0.02,
        0.03,
    ]

    for value in sequence:
        hrsg.record_return_snapshot(
            {
                GTG1_HEAVY_INDUSTRY: value,
                CSTG1_BFSI: value,
            }
        )

    penalties = (
        hrsg.compute_economizer_penalties()
    )

    assert penalties[
        GTG1_HEAVY_INDUSTRY
    ] < 1.0

    assert penalties[
        CSTG1_BFSI
    ] < 1.0

    assert penalties[
        BPSTG_HEALTHCARE
    ] == 1.0


def test_beta_input_damps_higher_beta_member_only(
    hrsg,
):
    sequence = [
        0.02,
        -0.01,
        0.03,
        -0.02,
        0.04,
        -0.01,
    ]

    for value in sequence:
        hrsg.record_return_snapshot(
            {
                GTG1_HEAVY_INDUSTRY: value,
                CSTG1_BFSI: value,
            }
        )

    penalties = (
        hrsg.compute_economizer_penalties(
            bay_beta={
                GTG1_HEAVY_INDUSTRY: 1.40,
                CSTG1_BFSI: 0.80,
            }
        )
    )

    assert penalties[
        GTG1_HEAVY_INDUSTRY
    ] < 1.0

    assert penalties[
        CSTG1_BFSI
    ] == pytest.approx(1.0)


def test_correlation_derating_becomes_reserve_not_forced_redeployment(
    hrsg,
    base_allocations,
    healthy_states,
):
    sequence = [
        0.02,
        -0.01,
        0.03,
        -0.02,
        0.04,
        -0.01,
    ]

    for value in sequence:
        hrsg.record_return_snapshot(
            {
                GTG1_HEAVY_INDUSTRY: value,
                CSTG1_BFSI: value,
            }
        )

    result = hrsg.balance_capital(
        bay_states=healthy_states,
        base_allocations=base_allocations,
    )

    assert result.reserve_cash > 0.0

    assert sum(
        result.allocations.values()
    ) < 1_000_000.0

    assert (
        sum(result.allocations.values())
        + result.reserve_cash
    ) == pytest.approx(
        1_000_000.0
    )


def test_superheat_is_stg_only_and_bounded(
    hrsg,
):
    assert hrsg.superheat_headroom_factor(
        bay_id=GTG1_HEAVY_INDUSTRY,
        recent_expectancy_r=1.0,
    ) == pytest.approx(1.0)

    assert hrsg.superheat_headroom_factor(
        bay_id=BPSTG_HEALTHCARE,
        recent_expectancy_r=0.65,
    ) == pytest.approx(1.15)

    assert hrsg.superheat_headroom_factor(
        bay_id=CSTG1_BFSI,
        recent_expectancy_r=0.50,
    ) == pytest.approx(1.075)

    assert hrsg.superheat_headroom_factor(
        bay_id=CSTG2_CONSUMER_AUTO,
        recent_expectancy_r=0.30,
    ) == pytest.approx(1.0)


def test_invalid_or_incomplete_state_fails_closed(
    hrsg,
    base_allocations,
    healthy_states,
):
    incomplete = dict(
        healthy_states
    )

    incomplete.pop(
        BPSTG_HEALTHCARE
    )

    with pytest.raises(ValueError):
        hrsg.balance_capital(
            bay_states=incomplete,
            base_allocations=base_allocations,
        )

    with pytest.raises(ValueError):
        hrsg.record_return_snapshot(
            {
                "UNKNOWN_BAY": 0.01,
            }
        )


def test_offline_stg_allocation_moves_to_reserve(
    hrsg,
    base_allocations,
    healthy_states,
):
    states = {
        bay_id: dict(state)
        for bay_id, state
        in healthy_states.items()
    }

    states[
        BPSTG_HEALTHCARE
    ]["tripped_offline"] = True

    result = hrsg.balance_capital(
        bay_states=states,
        base_allocations=base_allocations,
    )

    assert result.allocations[
        BPSTG_HEALTHCARE
    ] == pytest.approx(0.0)

    assert result.reserve_cash == pytest.approx(
        base_allocations[
            BPSTG_HEALTHCARE
        ]
    )

    assert (
        sum(result.allocations.values())
        + result.reserve_cash
    ) == pytest.approx(
        1_000_000.0
    )
