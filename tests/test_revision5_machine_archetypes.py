from revision5.ccpp_unified_plant import (
    CentralPlantMasterDCS,
)
from revision5.machine_archetypes import (
    MACHINE_ARCHETYPES,
    PROVENANCE,
    validate_machine_archetypes,
)
from revision5.topology import BAY_IDS


def test_registry_exactly_matches_five_bay_topology():
    validate_machine_archetypes(BAY_IDS)

    assert set(
        MACHINE_ARCHETYPES
    ) == set(BAY_IDS)


def test_every_profile_is_explicit_candidate_not_oem():
    for profile in MACHINE_ARCHETYPES.values():
        assert profile.provenance == PROVENANCE
        assert "NOT_OEM" in profile.provenance


def test_frozen_droop_classes():
    assert MACHINE_ARCHETYPES[
        "GTG1_HEAVY_INDUSTRY"
    ].dynamics.droop_fraction == 0.040

    assert MACHINE_ARCHETYPES[
        "GTG2_TECH_TELECOM"
    ].dynamics.droop_fraction == 0.040

    assert MACHINE_ARCHETYPES[
        "CSTG1_BFSI"
    ].dynamics.droop_fraction == 0.055

    assert MACHINE_ARCHETYPES[
        "CSTG2_CONSUMER_AUTO"
    ].dynamics.droop_fraction == 0.055

    assert MACHINE_ARCHETYPES[
        "BPSTG_HEALTHCARE"
    ].dynamics.droop_fraction == 0.075


def test_fast_medium_slow_dynamic_order():
    gtg = MACHINE_ARCHETYPES[
        "GTG1_HEAVY_INDUSTRY"
    ].dynamics

    cstg = MACHINE_ARCHETYPES[
        "CSTG1_BFSI"
    ].dynamics

    bp = MACHINE_ARCHETYPES[
        "BPSTG_HEALTHCARE"
    ].dynamics

    assert (
        gtg.governor_time_constant_s
        < cstg.governor_time_constant_s
        < bp.governor_time_constant_s
    )

    assert (
        gtg.actuator_time_constant_s
        < cstg.actuator_time_constant_s
        < bp.actuator_time_constant_s
    )

    assert (
        gtg.turbine_time_constant_s
        < cstg.turbine_time_constant_s
        < bp.turbine_time_constant_s
    )

    assert (
        gtg.ramp_up_pu_per_s
        > cstg.ramp_up_pu_per_s
        > bp.ramp_up_pu_per_s
    )


def test_inertia_class_order():
    gtg = MACHINE_ARCHETYPES[
        "GTG1_HEAVY_INDUSTRY"
    ].dynamics

    cstg = MACHINE_ARCHETYPES[
        "CSTG1_BFSI"
    ].dynamics

    bp = MACHINE_ARCHETYPES[
        "BPSTG_HEALTHCARE"
    ].dynamics

    assert (
        gtg.inertia_h_seconds
        < cstg.inertia_h_seconds
        < bp.inertia_h_seconds
    )


def test_gtg_and_steam_protection_are_semantically_distinct():
    gtg = MACHINE_ARCHETYPES[
        "GTG1_HEAVY_INDUSTRY"
    ].protection

    steam = MACHINE_ARCHETYPES[
        "CSTG1_BFSI"
    ].protection

    assert gtg.exhaust_spread_trip_c is not None
    assert gtg.flame_loss_trip_delay_s is not None

    assert steam.exhaust_spread_trip_c is None
    assert steam.flame_loss_trip_delay_s is None


def test_real_dcs_auto_installs_five_machine_models(tmp_path):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "plant.sqlite3",
    )

    assert set(
        plant.machine_archetypes
    ) == set(BAY_IDS)

    for bay_id in BAY_IDS:
        bay = plant.bays[bay_id]

        assert (
            bay.machine_model.spec
            == MACHINE_ARCHETYPES[
                bay_id
            ].dynamics
        )

        assert (
            bay.mechanical_protection.spec
            == MACHINE_ARCHETYPES[
                bay_id
            ].protection
        )


def test_actual_initial_response_order(tmp_path):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "response.sqlite3",
    )

    result = {}

    for bay_id in (
        "GTG1_HEAVY_INDUSTRY",
        "CSTG1_BFSI",
        "BPSTG_HEALTHCARE",
    ):
        machine = plant.bays[
            bay_id
        ].machine_model

        state = machine.step(
            speed_reference_hz=50.20,
            load_reference_pu=0.50,
            electrical_power_pu=0.00,
            dt_seconds=0.10,
        )

        result[bay_id] = (
            state.mechanical_power_pu
        )

    assert (
        result["GTG1_HEAVY_INDUSTRY"]
        > result["CSTG1_BFSI"]
        > result["BPSTG_HEALTHCARE"]
    )
