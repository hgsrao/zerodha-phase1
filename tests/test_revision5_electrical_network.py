from datetime import datetime

import pytest

from revision5.electrical_network import (
    BreakerPosition,
    PlantElectricalMode,
    PlantElectricalNetwork,
)
from revision5.topology import BAY_IDS
from revision5.ccpp_unified_plant import (
    CentralPlantMasterDCS,
)


def test_network_has_five_unit_breakers_and_one_grid_breaker():
    network = PlantElectricalNetwork(
        BAY_IDS
    )

    assert len(
        network.online_units()
    ) == 5

    # 5 generator breakers + 1 grid intertie.
    assert len(network.breakers) == 6

    assert (
        network.mode
        == PlantElectricalMode.GRID_CONNECTED
    )


def test_one_unit_trip_is_strictly_selective():
    network = PlantElectricalNetwork(
        BAY_IDS
    )

    victim = BAY_IDS[0]

    network.trip_unit(
        victim,
        reason="87G differential",
        source="SEL300G",
        lockout=True,
    )

    assert (
        network.unit_available(victim)
        is False
    )

    for bay in BAY_IDS[1:]:
        assert (
            network.unit_available(bay)
            is True
        )

    assert network.grid_connected is True

    assert (
        network.breakers[
            network.GRID_BREAKER
        ].position
        == BreakerPosition.CLOSED
    )


def test_grid_trip_islands_plant_without_tripping_units():
    network = PlantElectricalNetwork(
        BAY_IDS
    )

    network.open_grid_intertie(
        reason="Grid disturbance",
        source="MiCOM",
    )

    assert (
        network.mode
        == PlantElectricalMode.ISLANDED
    )

    assert network.grid_connected is False

    # Critical invariant:
    # grid separation does NOT trip the turbines.
    assert set(
        network.online_units()
    ) == set(BAY_IDS)


def test_86_lockout_requires_explicit_reset_before_reclose():
    network = PlantElectricalNetwork(
        BAY_IDS
    )

    bay = BAY_IDS[0]

    network.trip_unit(
        bay,
        reason="87G",
        source="SEL300G",
        lockout=True,
    )

    with pytest.raises(RuntimeError):
        network.close_unit_breaker(
            bay
        )

    network.reset_unit_lockout(
        bay
    )

    network.close_unit_breaker(
        bay
    )

    assert (
        network.unit_available(bay)
        is True
    )


def test_dcs_unit_trip_blocks_only_affected_bay(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "network.sqlite3",
    )

    bay = BAY_IDS[0]

    plant.trip_unit_breaker(
        bay_id=bay,
        reason="TEST_UNIT_TRIP",
        source="TEST_RELAY",
        lockout=True,
    )

    assert (
        plant.electrical_network
        .unit_available(bay)
        is False
    )

    for other in BAY_IDS:
        if other == bay:
            continue

        assert (
            plant.electrical_network
            .unit_available(other)
            is True
        )

    # Unit fault did not open grid.
    assert (
        plant.electrical_network
        .grid_connected
        is True
    )


def test_dcs_grid_trip_opens_only_intertie(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "grid.sqlite3",
    )

    result = plant.evaluate_entry(
        engine_mode="ENGINE_A",
        symbol="INFY",
        bar_dt=datetime(
            2026, 7, 31, 10, 0
        ),
        bar_index=1,
        z_score=-3.0,
        price=1500.0,
        atr=15.0,
        bid=1499.8,
        ask=1500.2,
        tick_age_s=1.0,
        bar_range=10.0,

        # Deliberately exceed the current MiCOM crash threshold.
        nifty_15m_ret=-0.05,
        nifty_vol_z=0.0,
        fleet_equity_dd_pct=0.0,
    )

    assert result["admitted"] is False

    assert result["reason"].startswith(
        "SUBSTATION_TRIP:"
    )

    assert (
        plant.electrical_network.mode
        == PlantElectricalMode.ISLANDED
    )

    # Critical concept:
    # the five generating units remain connected to PLANT_BUS.
    assert set(
        plant.electrical_network
        .online_units()
    ) == set(BAY_IDS)
