from types import SimpleNamespace

from revision5.ccpp_unified_plant import (
    CentralPlantMasterDCS,
)
from revision5.startup_synchronization import (
    SpeedPulse,
    VoltagePulse,
    StartupState,
)


class FakeAuto25A:
    def __init__(self):
        self.close_opportunity = False
        self.speed_command = SpeedPulse.NONE
        self.voltage_command = VoltagePulse.NONE
        self.speed_width = 0.0
        self.voltage_width = 0.0
        self.calls = 0

    def reset(self):
        self.calls = 0

    def step(self, **kwargs):
        self.calls += 1

        return SimpleNamespace(
            speed_command=self.speed_command,
            voltage_command=self.voltage_command,
            speed_pulse_width_seconds=(
                self.speed_width
            ),
            voltage_pulse_width_seconds=(
                self.voltage_width
            ),
            close_opportunity=(
                self.close_opportunity
            ),
            reason="FAKE_25A",
        )


class FakeRelay25:
    def __init__(self):
        self.permitted = False

        self.spec = SimpleNamespace(
            dead_bus_voltage_pu=0.10
        )

    def evaluate(self, **kwargs):
        return SimpleNamespace(
            permitted=self.permitted,
            reason=(
                "25_SYNC_CHECK_PERMISSIVE"
                if self.permitted
                else "25_BLOCK"
            ),
        )


def install(plant, bay_id):
    auto = FakeAuto25A()
    relay = FakeRelay25()

    #
    # TEST VALUES ONLY.
    # Not Revision-5 production settings.
    #
    plant.configure_unit_synchronizer(
        bay_id=bay_id,
        auto_synchronizer=auto,
        synchrocheck_relay=relay,

        initial_speed_reference_hz=50.0,
        minimum_speed_reference_hz=49.0,
        maximum_speed_reference_hz=51.0,
        speed_reference_rate_hz_per_second=0.10,

        initial_voltage_reference_pu=1.0,
        minimum_voltage_reference_pu=0.90,
        maximum_voltage_reference_pu=1.10,
        voltage_reference_rate_pu_per_second=0.05,
    )

    return auto, relay


def first_bay(plant):
    return next(iter(plant.bays))


def prepare_sync_ready(plant, bay_id):
    plant.prepare_unit_startup(
        bay_id=bay_id
    )

    plant.bays[
        bay_id
    ].startup_sequencer.state = (
        StartupState.SYNC_READY
    )


def test_25a_speed_and_voltage_pulses_reach_real_controllers(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "pulse.sqlite3",
    )

    bay_id = first_bay(plant)
    auto, relay = install(
        plant,
        bay_id,
    )

    prepare_sync_ready(
        plant,
        bay_id,
    )

    bay = plant.bays[bay_id]

    speed_before = (
        bay.governor
        .sync_speed_reference_hz
    )

    voltage_before = (
        bay.avr
        .sync_voltage_reference_pu
    )

    auto.speed_command = SpeedPulse.RAISE
    auto.voltage_command = VoltagePulse.RAISE
    auto.speed_width = 0.20
    auto.voltage_width = 0.20

    result = (
        plant.run_unit_synchronization(
            bay_id=bay_id,
            dt_seconds=0.20,
            generator_frequency_hz=49.8,
            bus_frequency_hz=50.0,
            generator_voltage_pu=0.97,
            bus_voltage_pu=1.0,
            generator_phase_deg=-20.0,
            bus_phase_deg=0.0,
        )
    )

    assert result["breaker_closed"] is False

    assert (
        bay.governor
        .sync_speed_reference_hz
        > speed_before
    )

    assert (
        bay.avr
        .sync_voltage_reference_pu
        > voltage_before
    )

    assert auto.calls == 1


def test_independent_25_blocks_real_52g_even_if_25a_requests_close(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "block.sqlite3",
    )

    bay_id = first_bay(plant)
    auto, relay = install(
        plant,
        bay_id,
    )

    prepare_sync_ready(
        plant,
        bay_id,
    )

    auto.close_opportunity = True
    relay.permitted = False

    result = (
        plant.run_unit_synchronization(
            bay_id=bay_id,
            dt_seconds=0.10,
            generator_frequency_hz=50.0,
            bus_frequency_hz=50.0,
            generator_voltage_pu=1.0,
            bus_voltage_pu=1.0,
            generator_phase_deg=0.0,
            bus_phase_deg=0.0,
        )
    )

    assert result["combined_permissive"] is False
    assert result["breaker_closed"] is False

    assert (
        plant.electrical_network
        .unit_available(
            bay_id
        )
        is False
    )


def test_real_52g_closes_only_when_25a_and_25_both_permit(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "close.sqlite3",
    )

    bay_id = first_bay(plant)
    auto, relay = install(
        plant,
        bay_id,
    )

    prepare_sync_ready(
        plant,
        bay_id,
    )

    auto.close_opportunity = True
    relay.permitted = True

    result = (
        plant.run_unit_synchronization(
            bay_id=bay_id,
            dt_seconds=0.10,
            generator_frequency_hz=50.0,
            bus_frequency_hz=50.0,
            generator_voltage_pu=1.0,
            bus_voltage_pu=1.0,
            generator_phase_deg=0.0,
            bus_phase_deg=0.0,
        )
    )

    assert result["combined_permissive"] is True
    assert result["breaker_closed"] is True

    assert (
        plant.electrical_network
        .unit_available(
            bay_id
        )
        is True
    )

    assert (
        plant.bays[
            bay_id
        ].startup_sequencer.state
        == StartupState.SYNCHRONIZED
    )


def test_ansi86_still_has_final_blocking_authority(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "86.sqlite3",
    )

    bay_id = first_bay(plant)
    auto, relay = install(
        plant,
        bay_id,
    )

    prepare_sync_ready(
        plant,
        bay_id,
    )

    auto.close_opportunity = True
    relay.permitted = True

    breaker = (
        plant.electrical_network
        .unit_breaker(
            bay_id
        )
    )

    breaker.lockout_86 = True

    result = (
        plant.run_unit_synchronization(
            bay_id=bay_id,
            dt_seconds=0.10,
            generator_frequency_hz=50.0,
            bus_frequency_hz=50.0,
            generator_voltage_pu=1.0,
            bus_voltage_pu=1.0,
            generator_phase_deg=0.0,
            bus_phase_deg=0.0,
        )
    )

    assert result["breaker_closed"] is False
    assert result["reason"] == "52G_BLOCKED_ANSI86"


def test_dead_bus_first_unit_does_not_run_25a_against_zero_hz_bus(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "dead.sqlite3",
    )

    bay_id = first_bay(plant)
    auto, relay = install(
        plant,
        bay_id,
    )

    prepare_sync_ready(
        plant,
        bay_id,
    )

    relay.permitted = True

    result = (
        plant.run_unit_synchronization(
            bay_id=bay_id,
            dt_seconds=0.10,

            generator_frequency_hz=50.0,
            bus_frequency_hz=0.0,

            generator_voltage_pu=1.0,
            bus_voltage_pu=0.0,

            generator_phase_deg=0.0,
            bus_phase_deg=0.0,

            dead_bus_close_authorized=True,
        )
    )

    assert result["dead_bus"] is True

    # Critical: 25A must not chase a dead bus.
    assert auto.calls == 0

    assert result["breaker_closed"] is True


def test_installed_unit_cannot_enter_market_before_dispatch_ready(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "dispatch.sqlite3",
    )

    bay_id = first_bay(plant)
    install(
        plant,
        bay_id,
    )

    bay = plant.bays[bay_id]

    symbol = bay.symbols[0]

    result = bay.evaluate_admission(
        symbol=symbol,
        bar_index=0,
        z_score=0.0,
        price=100.0,
        atr=1.0,
        bid=99.9,
        ask=100.1,
        tick_age_s=0.0,
        bar_range=1.0,
    )

    assert result["admitted"] is False

    assert result["reason"].startswith(
        "UNIT_NOT_DISPATCH_READY:"
    )
