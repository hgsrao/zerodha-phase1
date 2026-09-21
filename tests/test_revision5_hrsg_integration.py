from datetime import datetime

import pytest

from revision5.ccpp_unified_plant import (
    CentralPlantMasterDCS,
)
from revision5.engine_state import ENGINE_A
from revision5.hrsg import (
    HeatRecoverySteamGenerator,
)
from revision5.topology import (
    BPSTG_HEALTHCARE,
    CSTG1_BFSI,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
)


@pytest.fixture
def plant(tmp_path):
    return CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=(
            tmp_path
            / "r5_hrsg_integration.sqlite3"
        ),
    )


def test_master_dcs_owns_hrsg(plant):
    assert isinstance(
        plant.hrsg,
        HeatRecoverySteamGenerator,
    )

    assert plant.hrsg_last_balance is None

    assert (
        plant.hrsg_reserve_cash
        == pytest.approx(0.0)
    )


def test_healthy_hrsg_preserves_dispatcher_allocations(
    plant,
):
    plant.begin_bar(
        datetime(2026, 7, 31, 9, 15),
        0,
    )

    expected = {
        bay_id: (
            plant.dispatcher.get_allocation(
                bay_id
            )
        )
        for bay_id in plant.bays
    }

    balance = plant.apply_hrsg_balance(
        bar_index=0
    )

    assert balance.reserve_cash == pytest.approx(
        0.0
    )

    assert balance.allocations == pytest.approx(
        expected
    )

    assert (
        sum(balance.allocations.values())
        + balance.reserve_cash
    ) == pytest.approx(
        1_000_000.0
    )


def test_gtg_trip_recovers_fraction_to_stg_and_reserve(
    plant,
):
    plant.begin_bar(
        datetime(2026, 7, 31, 11, 0),
        100,
    )

    gtg_base = (
        plant.dispatcher.get_allocation(
            GTG1_HEAVY_INDUSTRY
        )
    )

    stg_base = (
        plant.dispatcher.get_allocation(
            CSTG1_BFSI
        )
    )

    plant.bays[
        GTG1_HEAVY_INDUSTRY
    ].tripped_offline = True

    balance = plant.apply_hrsg_balance(
        bar_index=100
    )

    assert balance.allocations[
        GTG1_HEAVY_INDUSTRY
    ] == pytest.approx(0.0)

    assert (
        balance.curtailed_gtg_capital
        == pytest.approx(gtg_base)
    )

    assert (
        balance.recovered_capital
        == pytest.approx(
            gtg_base * 0.60
        )
    )

    assert balance.reserve_cash == pytest.approx(
        gtg_base * 0.40
    )

    assert balance.allocations[
        CSTG1_BFSI
    ] > stg_base

    assert (
        sum(balance.allocations.values())
        + balance.reserve_cash
    ) == pytest.approx(
        1_000_000.0
    )


def test_bar_cooldown_is_seen_by_hrsg(
    plant,
):
    plant.begin_bar(
        datetime(2026, 7, 31, 11, 0),
        100,
    )

    bay = plant.bays[
        GTG2_TECH_TELECOM
    ]

    bay.cooldown_until_bar_exclusive = 116

    during = plant.apply_hrsg_balance(
        bar_index=101
    )

    assert during.allocations[
        GTG2_TECH_TELECOM
    ] == pytest.approx(0.0)

    assert (
        bay.effective_allocation
        == pytest.approx(0.0)
    )

    recovered = plant.apply_hrsg_balance(
        bar_index=116
    )

    assert recovered.allocations[
        GTG2_TECH_TELECOM
    ] > 0.0

    assert bay.effective_allocation > 0.0


def test_offline_stg_goes_to_reserve(
    plant,
):
    plant.begin_bar(
        datetime(2026, 7, 31, 12, 0),
        165,
    )

    base = (
        plant.dispatcher.get_allocation(
            BPSTG_HEALTHCARE
        )
    )

    plant.bays[
        BPSTG_HEALTHCARE
    ].tripped_offline = True

    balance = plant.apply_hrsg_balance(
        bar_index=165
    )

    assert balance.allocations[
        BPSTG_HEALTHCARE
    ] == pytest.approx(0.0)

    assert balance.reserve_cash == pytest.approx(
        base
    )


def test_synchronous_correlation_derating_creates_reserve(
    plant,
):
    sequence = [
        0.02,
        -0.01,
        0.03,
        -0.02,
        0.04,
        -0.01,
        0.02,
    ]

    for value in sequence:
        plant.record_hrsg_return_snapshot(
            {
                GTG1_HEAVY_INDUSTRY: value,
                CSTG1_BFSI: value,
            }
        )

    plant.begin_bar(
        datetime(2026, 7, 31, 12, 30),
        195,
    )

    balance = plant.apply_hrsg_balance(
        bar_index=195
    )

    assert balance.correlation_penalties[
        GTG1_HEAVY_INDUSTRY
    ] < 1.0

    assert balance.correlation_penalties[
        CSTG1_BFSI
    ] < 1.0

    assert balance.reserve_cash > 0.0

    assert (
        sum(balance.allocations.values())
        + balance.reserve_cash
    ) == pytest.approx(
        1_000_000.0
    )


def test_evaluate_entry_routes_through_hrsg(
    plant,
):
    assert plant.hrsg_last_balance is None

    result = plant.evaluate_entry(
        engine_mode=ENGINE_A,
        symbol="HDFCBANK",
        bar_dt=datetime(
            2026, 7, 31, 10, 30
        ),
        bar_index=75,
        z_score=-3.0,
        price=1500.0,
        atr=15.0,
        bid=1499.8,
        ask=1500.2,
        tick_age_s=1.0,
        bar_range=16.0,
        nifty_15m_ret=0.0,
        nifty_vol_z=0.0,
        fleet_equity_dd_pct=0.0,
    )

    assert plant.hrsg_last_balance is not None

    assert (
        "hrsg_effective_allocation"
        in result
    )

    assert "hrsg_reserve_cash" in result

    assert result[
        "hrsg_effective_allocation"
    ] > 0.0


def test_hrsg_status_reports_conserved_plant_state(
    plant,
):
    plant.begin_bar(
        datetime(2026, 7, 31, 13, 0),
        225,
    )

    plant.apply_hrsg_balance(
        bar_index=225
    )

    status = plant.hrsg_status()

    assert status["has_balance"] is True

    deployed = sum(
        status[
            "effective_allocations"
        ].values()
    )

    assert (
        deployed
        + status["reserve_cash"]
    ) == pytest.approx(
        1_000_000.0
    )
