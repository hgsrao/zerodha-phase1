from revision5.machine_dynamics import (
    MachineDynamicSpec,
    MechanicalProtectionSpec,
    MechanicalMeasurements,
    TurbineMachineModel,
    TurbineMechanicalProtection,
)
from revision5.ccpp_unified_plant import (
    CentralPlantMasterDCS,
)


#
# TEST PARAMETERS ONLY.
# These are not certified GT/ST OEM settings.
#

def dynamic_spec(
    *,
    name="TEST_GTG",
    kind="GAS_TURBINE",
    response="FAST",
    droop=0.04,
    inertia=4.0,
    damping=1.0,
    tg=0.20,
    ta=0.15,
    tt=0.40,
):
    return MachineDynamicSpec(
        name=name,
        machine_kind=kind,
        response_class=response,

        nominal_frequency_hz=50.0,

        droop_fraction=droop,
        inertia_h_seconds=inertia,
        damping_pu=damping,

        governor_time_constant_s=tg,
        actuator_time_constant_s=ta,
        turbine_time_constant_s=tt,

        mechanical_power_min_pu=0.0,
        mechanical_power_max_pu=1.0,

        ramp_up_pu_per_s=0.50,
        ramp_down_pu_per_s=0.50,

        speed_deadband_hz=0.01,

        acceleration_limit_hz_per_s=2.0,
        deceleration_limit_hz_per_s=2.0,
    )


def gtg_protection():
    return MechanicalProtectionSpec(
        machine_kind="GAS_TURBINE",

        overspeed_alarm_hz=51.5,
        overspeed_trip_hz=52.5,

        acceleration_trip_hz_per_s=3.0,

        vibration_alarm_mm_s=5.0,
        vibration_trip_mm_s=8.0,

        low_lube_oil_trip_bar=1.0,
        low_hydraulic_pressure_trip_bar=1.0,

        bearing_temperature_alarm_c=90.0,
        bearing_temperature_trip_c=110.0,

        exhaust_temperature_alarm_c=550.0,
        exhaust_temperature_trip_c=650.0,

        exhaust_spread_hold_c=40.0,
        exhaust_spread_trip_c=70.0,

        flame_loss_trip_delay_s=0.5,
        fuel_loss_trip_delay_s=0.5,

        lockout_trip_codes=(
            "MECH_OVERSPEED",
            "MECH_VIBRATION",
            "MECH_LOW_LUBE_OIL",
        ),
    )


def steam_protection():
    return MechanicalProtectionSpec(
        machine_kind="STEAM_TURBINE",

        overspeed_alarm_hz=51.5,
        overspeed_trip_hz=52.5,

        acceleration_trip_hz_per_s=3.0,

        vibration_alarm_mm_s=5.0,
        vibration_trip_mm_s=8.0,

        low_lube_oil_trip_bar=1.0,
        low_hydraulic_pressure_trip_bar=1.0,

        bearing_temperature_alarm_c=90.0,
        bearing_temperature_trip_c=110.0,

        exhaust_temperature_alarm_c=None,
        exhaust_temperature_trip_c=None,

        exhaust_spread_hold_c=None,
        exhaust_spread_trip_c=None,

        flame_loss_trip_delay_s=None,
        fuel_loss_trip_delay_s=None,

        lockout_trip_codes=(
            "MECH_OVERSPEED",
            "MECH_VIBRATION",
        ),
    )


def healthy_gtg(
    *,
    frequency=50.0,
    acceleration=0.0,
    spread=10.0,
    flame=True,
    fuel=True,
    dt=0.1,
):
    return MechanicalMeasurements(
        frequency_hz=frequency,
        acceleration_hz_per_s=acceleration,
        vibration_mm_s=2.0,
        lube_oil_pressure_bar=3.0,
        hydraulic_pressure_bar=3.0,
        bearing_temperature_c=70.0,
        dt_seconds=dt,
        flame_proven=flame,
        fuel_available=fuel,
        exhaust_temperature_c=450.0,
        exhaust_spread_c=spread,
    )


def test_droop_changes_governor_demand():
    strong = TurbineMachineModel(
        dynamic_spec(
            droop=0.04
        )
    )

    weak = TurbineMachineModel(
        dynamic_spec(
            droop=0.08
        )
    )

    strong.step(
        speed_reference_hz=50.2,
        load_reference_pu=0.0,
        electrical_power_pu=0.0,
        dt_seconds=0.1,
    )

    weak.step(
        speed_reference_hz=50.2,
        load_reference_pu=0.0,
        electrical_power_pu=0.0,
        dt_seconds=0.1,
    )

    assert (
        strong.governor_output_pu
        > weak.governor_output_pu
    )


def test_higher_inertia_reduces_frequency_rate_of_change():
    low_h = TurbineMachineModel(
        dynamic_spec(
            inertia=2.0
        )
    )

    high_h = TurbineMachineModel(
        dynamic_spec(
            inertia=8.0
        )
    )

    low_h.reset(
        mechanical_power_pu=0.5
    )

    high_h.reset(
        mechanical_power_pu=0.5
    )

    a = low_h.step(
        speed_reference_hz=50.0,
        load_reference_pu=0.5,
        electrical_power_pu=0.2,
        dt_seconds=0.1,
    )

    b = high_h.step(
        speed_reference_hz=50.0,
        load_reference_pu=0.5,
        electrical_power_pu=0.2,
        dt_seconds=0.1,
    )

    assert (
        abs(a.acceleration_hz_per_s)
        > abs(b.acceleration_hz_per_s)
    )


def test_fast_machine_has_faster_initial_step_response_than_slow_machine():
    """
    Compare the initial transient, not mechanical power after a long
    closed-loop interval.

    A fast machine corrects frequency sooner. Once it does so, droop
    feedback naturally reduces its governor demand, so its mechanical
    power is not required to remain above the slower machine forever.
    """
    fast = TurbineMachineModel(
        dynamic_spec(
            response="FAST",
            inertia=3.0,
            tg=0.10,
            ta=0.10,
            tt=0.20,
        )
    )

    slow = TurbineMachineModel(
        dynamic_spec(
            response="SLOW",
            inertia=7.0,
            tg=0.50,
            ta=0.40,
            tt=1.00,
        )
    )

    fast_state = fast.step(
        speed_reference_hz=50.2,
        load_reference_pu=0.5,
        electrical_power_pu=0.0,
        dt_seconds=0.1,
    )

    slow_state = slow.step(
        speed_reference_hz=50.2,
        load_reference_pu=0.5,
        electrical_power_pu=0.0,
        dt_seconds=0.1,
    )

    # Faster governor.
    assert (
        fast_state.governor_output_pu
        > slow_state.governor_output_pu
    )

    # Faster actuator.
    assert (
        fast_state.actuator_output_pu
        > slow_state.actuator_output_pu
    )

    # Faster turbine/mechanical-power response.
    assert (
        fast_state.mechanical_power_pu
        > slow_state.mechanical_power_pu
    )

    # Lower inertia + faster power response causes the rotor frequency
    # to begin moving sooner.
    assert (
        abs(
            fast_state.frequency_hz
            - fast.spec.nominal_frequency_hz
        )
        > abs(
            slow_state.frequency_hz
            - slow.spec.nominal_frequency_hz
        )
    )



def test_mechanical_power_ramp_is_limited():
    machine = TurbineMachineModel(
        dynamic_spec()
    )

    before = machine.mechanical_power_pu

    machine.step(
        speed_reference_hz=51.0,
        load_reference_pu=1.0,
        electrical_power_pu=0.0,
        dt_seconds=0.1,
    )

    assert (
        machine.mechanical_power_pu
        - before
        <= 0.05 + 1e-12
    )


def test_overspeed_trips_independently():
    relay = TurbineMechanicalProtection(
        gtg_protection()
    )

    result = relay.evaluate(
        healthy_gtg(
            frequency=53.0
        )
    )

    assert result.tripped
    assert result.trip_code == "MECH_OVERSPEED"
    assert result.lockout_86


def test_exhaust_spread_first_holds_then_trips():
    relay = TurbineMechanicalProtection(
        gtg_protection()
    )

    hold = relay.evaluate(
        healthy_gtg(
            spread=50.0
        )
    )

    assert not hold.tripped
    assert hold.hold_startup

    trip = relay.evaluate(
        healthy_gtg(
            spread=80.0
        )
    )

    assert trip.tripped
    assert (
        trip.trip_code
        == "MECH_EXHAUST_SPREAD"
    )


def test_loss_of_flame_uses_time_delay():
    relay = TurbineMechanicalProtection(
        gtg_protection()
    )

    first = relay.evaluate(
        healthy_gtg(
            flame=False,
            dt=0.2,
        )
    )

    assert not first.tripped

    second = relay.evaluate(
        healthy_gtg(
            flame=False,
            dt=0.3,
        )
    )

    assert second.tripped
    assert (
        second.trip_code
        == "MECH_FLAME_LOSS"
    )


def test_steam_turbine_has_no_fake_flame_or_exhaust_inputs():
    spec = dynamic_spec(
        name="TEST_ST",
        kind="STEAM_TURBINE",
        response="SLOW",
    )

    spec.validate()
    steam_protection().validate()


def test_dcs_mechanical_trip_opens_only_affected_unit(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "machine.sqlite3",
    )

    gtg = next(
        bay_id
        for bay_id in plant.bays
        if bay_id.startswith("GTG")
    )

    other = next(
        bay_id
        for bay_id in plant.bays
        if bay_id != gtg
    )

    plant.configure_unit_machine(
        bay_id=gtg,
        dynamic_spec=dynamic_spec(),
        mechanical_protection_spec=(
            gtg_protection()
        ),
    )

    assert (
        plant.electrical_network
        .unit_available(gtg)
    )

    assert (
        plant.electrical_network
        .unit_available(other)
    )

    result = plant.run_unit_machine_scan(
        bay_id=gtg,

        dt_seconds=0.1,

        speed_reference_hz=50.0,
        load_reference_pu=0.0,
        electrical_power_pu=0.0,

        vibration_mm_s=10.0,

        lube_oil_pressure_bar=3.0,
        hydraulic_pressure_bar=3.0,
        bearing_temperature_c=70.0,

        flame_proven=True,
        fuel_available=True,

        exhaust_temperature_c=450.0,
        exhaust_spread_c=10.0,
    )

    assert result["mechanical_trip"]

    assert not (
        plant.electrical_network
        .unit_available(gtg)
    )

    assert (
        plant.electrical_network
        .unit_available(other)
    )


def test_25a_speed_reference_is_consumed_by_machine_model(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "feedback.sqlite3",
    )

    gtg = next(
        bay_id
        for bay_id in plant.bays
        if bay_id.startswith("GTG")
    )

    plant.configure_unit_machine(
        bay_id=gtg,
        dynamic_spec=dynamic_spec(),
        mechanical_protection_spec=(
            gtg_protection()
        ),
    )

    bay = plant.bays[gtg]

    bay.governor.configure_synchronizing_speed_reference(
        initial_reference_hz=50.0,
        minimum_reference_hz=49.0,
        maximum_reference_hz=51.0,
        reference_rate_hz_per_second=0.10,
    )

    bay.governor.apply_synchronizing_speed_pulse(
        command="SPEED_RAISE",
        pulse_width_seconds=1.0,
    )

    assert (
        bay.governor.sync_speed_reference_hz
        == 50.1
    )

    before = (
        bay.machine_model.frequency_hz
    )

    for _ in range(10):
        result = plant.run_unit_machine_scan(
            bay_id=gtg,

            dt_seconds=0.1,

            load_reference_pu=0.2,
            electrical_power_pu=0.0,

            vibration_mm_s=2.0,

            lube_oil_pressure_bar=3.0,
            hydraulic_pressure_bar=3.0,
            bearing_temperature_c=70.0,

            flame_proven=True,
            fuel_available=True,

            exhaust_temperature_c=450.0,
            exhaust_spread_c=10.0,
        )

    assert (
        result["machine_state"].frequency_hz
        > before
    )
