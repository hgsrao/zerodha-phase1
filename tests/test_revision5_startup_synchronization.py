from revision5.startup_synchronization import (
    AutoSynchronizerSpec,
    AutomaticSynchronizer25A,
    GeneratorSynchronizingController,
    MachineKind,
    SpeedPulse,
    StartupInputs,
    StartupState,
    SynchrocheckRelay25,
    SynchrocheckSpec,
    SyncMode,
    TurbineStartupSequencer,
    VoltagePulse,
)


#
# TEST SETTINGS ONLY.
# These are deliberately not R5 production settings.
#

def check_spec():
    return SynchrocheckSpec(
        nominal_frequency_hz=50.0,
        max_delta_frequency_hz=0.20,
        max_delta_voltage_pu=0.05,
        max_phase_angle_deg=10.0,
        breaker_closing_time_seconds=0.10,
        dead_bus_voltage_pu=0.10,
    )


def auto_spec():
    return AutoSynchronizerSpec(
        preferred_slip_hz=0.0,
        frequency_deadband_hz=0.10,
        voltage_deadband_pu=0.05,
        phase_capture_deg=15.0,
        breaker_closing_time_seconds=0.10,
        speed_pulse_width_seconds=0.20,
        voltage_pulse_width_seconds=0.20,
        pulse_interval_seconds=0.10,
        synchronization_timeout_seconds=30.0,
        max_control_pulses=100,
    )


def startup_inputs(**changes):
    values = dict(
        start_command=False,
        starting_means_ready=False,
        crank_complete=False,
        flame_proven=False,
        exhaust_spread_ok=True,
        exhaust_spread_trip=False,
        fsnl_reached=False,
        synchronizer_ready=False,
        synchronizer_permissive=False,
        generator_breaker_closed=False,
        minimum_load_reached=False,
        auxiliary_services_stable=False,
        mechanical_trip=False,
    )

    values.update(changes)

    return StartupInputs(**values)


def test_25a_commands_speed_raise_and_voltage_raise():
    auto = AutomaticSynchronizer25A(
        auto_spec()
    )

    out = auto.step(
        dt_seconds=0.10,
        generator_frequency_hz=49.70,
        bus_frequency_hz=50.00,
        generator_voltage_pu=0.93,
        bus_voltage_pu=1.00,
        generator_phase_deg=0.0,
        bus_phase_deg=0.0,
    )

    assert (
        out.speed_command
        == SpeedPulse.RAISE
    )

    assert (
        out.voltage_command
        == VoltagePulse.RAISE
    )


def test_25a_commands_speed_lower_and_voltage_lower():
    auto = AutomaticSynchronizer25A(
        auto_spec()
    )

    out = auto.step(
        dt_seconds=0.10,
        generator_frequency_hz=50.30,
        bus_frequency_hz=50.00,
        generator_voltage_pu=1.07,
        bus_voltage_pu=1.00,
        generator_phase_deg=0.0,
        bus_phase_deg=0.0,
    )

    assert (
        out.speed_command
        == SpeedPulse.LOWER
    )

    assert (
        out.voltage_command
        == VoltagePulse.LOWER
    )


def test_25a_never_itself_closes_breaker():
    auto = AutomaticSynchronizer25A(
        auto_spec()
    )

    out = auto.step(
        dt_seconds=0.10,
        generator_frequency_hz=50.01,
        bus_frequency_hz=50.00,
        generator_voltage_pu=1.01,
        bus_voltage_pu=1.00,
        generator_phase_deg=1.0,
        bus_phase_deg=0.0,
    )

    assert out.close_opportunity is True

    # There is deliberately no breaker-close output
    # in the 25A result.
    assert not hasattr(
        out,
        "breaker_close_request",
    )


def test_25_is_independent_final_close_supervision():
    controller = GeneratorSynchronizingController(
        auto_spec=auto_spec(),
        check_spec=SynchrocheckSpec(
            nominal_frequency_hz=50.0,

            # Tighter than 25A.
            max_delta_frequency_hz=0.05,
            max_delta_voltage_pu=0.02,
            max_phase_angle_deg=5.0,

            breaker_closing_time_seconds=0.10,
            dead_bus_voltage_pu=0.10,
        ),
    )

    result = controller.step(
        dt_seconds=0.10,

        # Inside 25A's wider capture area,
        # but outside the independent 25 relay.
        generator_frequency_hz=50.08,
        bus_frequency_hz=50.00,

        generator_voltage_pu=1.04,
        bus_voltage_pu=1.00,

        generator_phase_deg=3.0,
        bus_phase_deg=0.0,

        synchronizing_enabled=True,
    )

    assert (
        result.auto.close_opportunity
        is True
    )

    assert result.check.permitted is False

    assert (
        result.breaker_close_request
        is False
    )


def test_52g_requires_25a_and_25():
    controller = GeneratorSynchronizingController(
        auto_spec=auto_spec(),
        check_spec=check_spec(),
    )

    result = controller.step(
        dt_seconds=0.10,

        generator_frequency_hz=50.01,
        bus_frequency_hz=50.00,

        generator_voltage_pu=1.01,
        bus_voltage_pu=1.00,

        generator_phase_deg=1.0,
        bus_phase_deg=0.0,

        synchronizing_enabled=True,
    )

    assert (
        result.auto.close_opportunity
        is True
    )

    assert result.check.permitted is True

    assert (
        result.breaker_close_request
        is True
    )

    assert (
        result.reason
        == "52G_LIVE_BUS_CLOSE_REQUEST"
    )


def test_breaker_closing_time_predicts_phase():
    relay = SynchrocheckRelay25(
        check_spec()
    )

    result = relay.evaluate(
        generator_frequency_hz=50.20,
        bus_frequency_hz=50.00,

        generator_voltage_pu=1.00,
        bus_voltage_pu=1.00,

        generator_phase_deg=8.0,
        bus_phase_deg=0.0,
    )

    assert result.permitted is False

    assert (
        result.reason
        == "25_PHASE_OUTSIDE_WINDOW"
    )


def test_dead_bus_requires_explicit_authority():
    controller = GeneratorSynchronizingController(
        auto_spec=auto_spec(),
        check_spec=check_spec(),
    )

    blocked = controller.step(
        dt_seconds=0.10,

        generator_frequency_hz=50.0,
        bus_frequency_hz=0.0,

        generator_voltage_pu=1.0,
        bus_voltage_pu=0.0,

        generator_phase_deg=0.0,
        bus_phase_deg=0.0,

        synchronizing_enabled=True,
        dead_bus_close_authorized=False,
    )

    assert (
        blocked.breaker_close_request
        is False
    )

    permitted = controller.step(
        dt_seconds=0.10,

        generator_frequency_hz=50.0,
        bus_frequency_hz=0.0,

        generator_voltage_pu=1.0,
        bus_voltage_pu=0.0,

        generator_phase_deg=0.0,
        bus_phase_deg=0.0,

        synchronizing_enabled=True,
        dead_bus_close_authorized=True,
    )

    assert permitted.check.permitted is True

    assert (
        permitted.check.mode
        == SyncMode.DEAD_BUS
    )

    assert (
        permitted.breaker_close_request
        is True
    )


def test_bad_exhaust_spread_blocks_fsnl():
    seq = TurbineStartupSequencer(
        MachineKind.GAS_TURBINE
    )

    seq.step(
        startup_inputs(
            start_command=True
        )
    )

    seq.step(
        startup_inputs(
            starting_means_ready=True
        )
    )

    seq.step(
        startup_inputs(
            crank_complete=True
        )
    )

    seq.step(
        startup_inputs(
            flame_proven=True
        )
    )

    seq.step(
        startup_inputs()
    )

    assert (
        seq.state
        == StartupState.ACCELERATING
    )

    result = seq.step(
        startup_inputs(
            fsnl_reached=True,
            exhaust_spread_ok=False,
        )
    )

    assert (
        result.current_state
        == StartupState.ACCELERATING
    )

    assert (
        result.reason
        == "EXHAUST_SPREAD_HOLD"
    )


def test_severe_exhaust_spread_trips_gtg():
    seq = TurbineStartupSequencer(
        MachineKind.GAS_TURBINE
    )

    seq.state = StartupState.ACCELERATING

    result = seq.step(
        startup_inputs(
            exhaust_spread_ok=False,
            exhaust_spread_trip=True,
        )
    )

    assert (
        result.current_state
        == StartupState.TRIPPED
    )


def test_complete_gtg_startup_to_dispatch():
    seq = TurbineStartupSequencer(
        MachineKind.GAS_TURBINE
    )

    seq.step(
        startup_inputs(
            start_command=True
        )
    )

    seq.step(
        startup_inputs(
            starting_means_ready=True
        )
    )

    seq.step(
        startup_inputs(
            crank_complete=True
        )
    )

    seq.step(
        startup_inputs(
            flame_proven=True
        )
    )

    seq.step(
        startup_inputs()
    )

    seq.step(
        startup_inputs(
            fsnl_reached=True,
            exhaust_spread_ok=True,
        )
    )

    assert seq.state == StartupState.FSNL

    seq.step(
        startup_inputs(
            synchronizer_ready=True
        )
    )

    assert (
        seq.state
        == StartupState.SYNC_READY
    )

    close = seq.step(
        startup_inputs(
            synchronizer_permissive=True
        )
    )

    assert (
        close.breaker_close_requested
        is True
    )

    seq.step(
        startup_inputs(
            generator_breaker_closed=True
        )
    )

    assert (
        seq.state
        == StartupState.SYNCHRONIZED
    )

    seq.step(
        startup_inputs(
            minimum_load_reached=True
        )
    )

    assert (
        seq.state
        == StartupState.MINIMUM_LOAD
    )

    final = seq.step(
        startup_inputs(
            auxiliary_services_stable=True
        )
    )

    assert (
        final.current_state
        == StartupState.DISPATCH_READY
    )

    assert final.dispatch_enabled is True


def test_steam_turbine_skips_combustion_stages():
    seq = TurbineStartupSequencer(
        MachineKind.STEAM_TURBINE
    )

    seq.step(
        startup_inputs(
            start_command=True
        )
    )

    seq.step(
        startup_inputs(
            starting_means_ready=True
        )
    )

    seq.step(
        startup_inputs(
            crank_complete=True
        )
    )

    assert (
        seq.state
        == StartupState.ACCELERATING
    )


def test_mechanical_trip_overrides_sync_sequence():
    seq = TurbineStartupSequencer(
        MachineKind.GAS_TURBINE
    )

    seq.state = StartupState.SYNC_READY

    result = seq.step(
        startup_inputs(
            mechanical_trip=True
        )
    )

    assert (
        result.current_state
        == StartupState.TRIPPED
    )
