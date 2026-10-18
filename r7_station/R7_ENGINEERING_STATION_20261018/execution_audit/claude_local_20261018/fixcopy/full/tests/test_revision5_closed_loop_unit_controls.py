from datetime import datetime

import pytest

from revision5.dynamic_parameters import (
    R5EnvironmentState,
    Revision5DynamicParameterController,
)
from revision5.governor import (
    BAY_GOVERNOR_SPECS,
    BayTurbineClosedLoopGovernor,
)
from revision5.ccpp_protection_cubicles import (
    BayExcitationAVR,
)
from revision5.ccpp_unified_plant import (
    CentralPlantMasterDCS,
)


def env():
    return R5EnvironmentState(
        normalized_vol=1.2,
        trend_probability=0.55,
        chop_probability=0.30,
        turbulent_probability=0.15,
        fleet_drawdown_fraction=0.01,
        control_error_delta=0.05,
    )


def test_governor_is_final_entry_authority():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()
    snap = controller.evaluate(env())

    gov = BayTurbineClosedLoopGovernor(
        BAY_GOVERNOR_SPECS[bay]
    )

    gov.apply_runtime_profile(
        snap.governor_by_bay[bay]
    )

    threshold = gov.dynamic_z(0.0)

    admit = gov.evaluate_entry_request(
        z_score=threshold - 0.10,
    )

    reject = gov.evaluate_entry_request(
        z_score=threshold + 0.10,
    )

    assert admit["action"] == "ENTRY"
    assert reject["action"] == "NO_ACTION"


def test_governor_ratchet_never_moves_backwards():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()
    snap = controller.evaluate(env())

    gov = BayTurbineClosedLoopGovernor(
        BAY_GOVERNOR_SPECS[bay]
    )

    gov.apply_runtime_profile(
        snap.governor_by_bay[bay]
    )

    gov.begin_position(
        hard_stop_r=-1.0
    )

    floors = []

    for i in range(1, 30):
        result = gov.evaluate_position_control(
            measured_r=0.70,
            reference_r=0.50,
            max_favorable_r=1.00,
            elapsed_bars=i,
            min_hold_bars=2,
            max_hold_bars=100,
            hard_stop_r=-1.0,
            trade_target_r=3.0,
        )

        floors.append(
            result["protected_r_floor"]
        )

    assert floors == sorted(floors)

    before = floors[-1]

    # MFE falls in the supplied telemetry; secured floor must not.
    result = gov.evaluate_position_control(
        measured_r=0.70,
        reference_r=0.50,
        max_favorable_r=0.40,
        elapsed_bars=30,
        min_hold_bars=2,
        max_hold_bars=100,
        hard_stop_r=-1.0,
        trade_target_r=3.0,
    )

    assert (
        result["protected_r_floor"]
        == pytest.approx(before)
    )


def test_governor_ratchet_can_force_exit():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()
    snap = controller.evaluate(env())

    gov = BayTurbineClosedLoopGovernor(
        BAY_GOVERNOR_SPECS[bay]
    )
    gov.apply_runtime_profile(
        snap.governor_by_bay[bay]
    )
    gov.begin_position(hard_stop_r=-1.0)

    result = None

    for i in range(1, 40):
        result = gov.evaluate_position_control(
            measured_r=0.80,
            reference_r=0.50,
            max_favorable_r=1.20,
            elapsed_bars=i,
            min_hold_bars=2,
            max_hold_bars=100,
            hard_stop_r=-1.0,
            trade_target_r=3.0,
        )

    floor = result["protected_r_floor"]

    exit_result = gov.evaluate_position_control(
        measured_r=floor - 0.01,
        reference_r=0.50,
        max_favorable_r=1.20,
        elapsed_bars=40,
        min_hold_bars=2,
        max_hold_bars=100,
        hard_stop_r=-1.0,
        trade_target_r=3.0,
    )

    assert exit_result["action"] == "EXIT"
    assert (
        exit_result["reason"]
        == "GOVERNOR_RATCHET_FLOOR"
    )


def test_avr_voltage_pid_is_bidirectional():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()
    snap = controller.evaluate(env())

    avr = BayExcitationAVR(
        bay,
        allocated_capital_inr=1_000_000.0,
        min_notional_uel_inr=15_000.0,
        max_notional_oel_inr=400_000.0,
    )

    avr.apply_runtime_profile(
        snap.avr_by_bay[bay]
    )

    high_v = avr.evaluate_control(
        mode="BUS_VOLTAGE",
        bus_voltage_reference_pu=1.0,
        terminal_voltage_pu=1.03,
    )

    reduced = high_v[
        "excitation_command"
    ]

    low_v = avr.evaluate_control(
        mode="BUS_VOLTAGE",
        bus_voltage_reference_pu=1.0,
        terminal_voltage_pu=0.97,
    )

    increased = low_v[
        "excitation_command"
    ]

    assert reduced < 1.0
    assert increased > reduced


def test_avr_undervoltage_and_overvoltage_trip():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()
    snap = controller.evaluate(env())

    avr1 = BayExcitationAVR(
        bay,
        allocated_capital_inr=1_000_000.0,
        min_notional_uel_inr=15_000.0,
        max_notional_oel_inr=400_000.0,
    )
    avr1.apply_runtime_profile(
        snap.avr_by_bay[bay]
    )

    uv = avr1.evaluate_control(
        terminal_voltage_pu=(
            avr1.undervoltage_trip_pu
            - 0.01
        ),
    )

    assert uv["tripped"] is True
    assert uv["reason"] == "ANSI_27_UNDERVOLTAGE"

    avr2 = BayExcitationAVR(
        bay,
        allocated_capital_inr=1_000_000.0,
        min_notional_uel_inr=15_000.0,
        max_notional_oel_inr=400_000.0,
    )
    avr2.apply_runtime_profile(
        snap.avr_by_bay[bay]
    )

    ov = avr2.evaluate_control(
        terminal_voltage_pu=(
            avr2.overvoltage_trip_pu
            + 0.01
        ),
    )

    assert ov["tripped"] is True
    assert ov["reason"] == "ANSI_59_OVERVOLTAGE"


def test_avr_pid_directly_derates_real_lot_size():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()
    snap = controller.evaluate(env())

    avr = BayExcitationAVR(
        bay,
        allocated_capital_inr=1_000_000.0,
        min_notional_uel_inr=15_000.0,
        max_notional_oel_inr=400_000.0,
    )

    avr.apply_runtime_profile(
        snap.avr_by_bay[bay]
    )

    qty_before, _ = avr.calculate_lot_size(
        asset_price=100.0,
        asset_atr=1.0,
        z_strength=-3.0,
    )

    # High-but-not-trip voltage causes excitation reduction.
    midpoint = (
        1.0
        + avr.overvoltage_trip_pu
    ) / 2.0

    control = avr.evaluate_control(
        terminal_voltage_pu=midpoint,
    )

    assert control["tripped"] is False
    assert (
        control["excitation_command"]
        < 1.0
    )

    qty_after, _ = avr.calculate_lot_size(
        asset_price=100.0,
        asset_atr=1.0,
        z_strength=-3.0,
    )

    assert qty_after < qty_before


def test_dcs_runs_hold_exit_loop_inside_real_bay(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "loop.sqlite3",
    )

    result = plant.evaluate_position_control(
        engine_mode="ENGINE_A",
        symbol="INFY",
        bar_dt=datetime(
            2026, 7, 31, 10, 1
        ),
        bar_index=2,
        current_r=0.20,
        reference_r=0.25,
        max_favorable_r=0.30,
        elapsed_bars=3,
        min_hold_bars=2,
        max_hold_bars=60,
        hard_stop_r=-1.0,
        trade_target_r=3.0,
        bid=1499.8,
        ask=1500.2,
        tick_age_s=1.0,
        atr=15.0,
        bar_range=10.0,
        dynamic_environment=env(),
    )

    assert result["action"] in {
        "HOLD",
        "EXIT",
    }

    assert "avr_control" in result
    assert result["symbol"] == "INFY"


def test_avr_trip_overrides_governor_hold(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "avr_trip.sqlite3",
    )

    result = plant.evaluate_position_control(
        engine_mode="ENGINE_A",
        symbol="INFY",
        bar_dt=datetime(
            2026, 7, 31, 10, 1
        ),
        bar_index=2,
        current_r=0.20,
        reference_r=0.20,
        max_favorable_r=0.30,
        elapsed_bars=3,
        min_hold_bars=2,
        max_hold_bars=60,
        hard_stop_r=-1.0,
        trade_target_r=3.0,
        bid=1499.8,
        ask=1500.2,
        tick_age_s=1.0,
        atr=15.0,
        bar_range=10.0,
        terminal_voltage_pu=1.20,
        dynamic_environment=env(),
    )

    assert result["action"] == "EXIT"
    assert result["reason"].startswith(
        "AVR_TRIP:"
    )
