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
    BayUnitProtectionSEL300G,
    MasterGridProtectionMiCOM,
)
from revision5.ccpp_unified_plant import (
    CentralPlantMasterDCS,
)


def calm_env():
    return R5EnvironmentState(
        normalized_vol=0.8,
        trend_probability=0.80,
        chop_probability=0.15,
        turbulent_probability=0.05,
        fleet_drawdown_fraction=0.0,
        control_error_delta=0.0,
    )


def stressed_env():
    return R5EnvironmentState(
        normalized_vol=2.0,
        trend_probability=0.10,
        chop_probability=0.20,
        turbulent_probability=0.70,
        fleet_drawdown_fraction=0.05,
        control_error_delta=0.25,
    )


def test_actual_governor_pid_consumes_runtime_profile():
    bay = next(iter(BAY_GOVERNOR_SPECS))
    spec = BAY_GOVERNOR_SPECS[bay]

    original = (
        spec.kp,
        spec.ki,
        spec.kd,
        spec.target_r,
        spec.integral_clamp,
        spec.grid_droop_gain,
        spec.grid_droop_max,
    )

    controller = Revision5DynamicParameterController()

    calm = controller.evaluate(calm_env())
    stressed = controller.evaluate(stressed_env())

    g1 = BayTurbineClosedLoopGovernor(spec)
    g2 = BayTurbineClosedLoopGovernor(spec)

    g1.apply_runtime_profile(
        calm.governor_by_bay[bay]
    )
    g2.apply_runtime_profile(
        stressed.governor_by_bay[bay]
    )

    z1 = g1.dynamic_z(-0.01)
    z2 = g2.dynamic_z(-0.01)

    u1 = g1.register_trade(-1.0)
    u2 = g2.register_trade(-1.0)

    assert z1 != pytest.approx(z2)
    assert u1 != pytest.approx(u2)

    assert original == (
        spec.kp,
        spec.ki,
        spec.kd,
        spec.target_r,
        spec.integral_clamp,
        spec.grid_droop_gain,
        spec.grid_droop_max,
    )


def test_actual_avr_consumes_runtime_profile():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()

    calm = controller.evaluate(calm_env())
    stressed = controller.evaluate(stressed_env())

    avr1 = BayExcitationAVR(
        bay,
        allocated_capital_inr=1_000_000.0,
        min_notional_uel_inr=15_000.0,
        max_notional_oel_inr=400_000.0,
    )

    avr2 = BayExcitationAVR(
        bay,
        allocated_capital_inr=1_000_000.0,
        min_notional_uel_inr=15_000.0,
        max_notional_oel_inr=400_000.0,
    )

    avr1.apply_runtime_profile(
        calm.avr_by_bay[bay]
    )

    avr2.apply_runtime_profile(
        stressed.avr_by_bay[bay]
    )

    qty_calm, _ = avr1.calculate_lot_size(
        asset_price=100.0,
        asset_atr=1.0,
        z_strength=-3.0,
    )

    qty_stressed, _ = avr2.calculate_lot_size(
        asset_price=100.0,
        asset_atr=1.0,
        z_strength=-3.0,
    )

    assert qty_stressed < qty_calm
    assert avr2.oel_max < avr1.oel_max
    assert avr2.uel_min > avr1.uel_min


def test_actual_sel300g_thresholds_are_dynamic():
    bay = next(iter(BAY_GOVERNOR_SPECS))

    controller = Revision5DynamicParameterController()

    calm = controller.evaluate(calm_env())
    stressed = controller.evaluate(stressed_env())

    r1 = BayUnitProtectionSEL300G(bay)
    r2 = BayUnitProtectionSEL300G(bay)

    r1.apply_runtime_profile(
        calm.unit_protection_by_bay[bay]
    )
    r2.apply_runtime_profile(
        stressed.unit_protection_by_bay[bay]
    )

    # Pick a spread between calm and stressed thresholds.
    threshold = (
        r1.max_spread_fraction
        + r2.max_spread_fraction
    ) / 2.0

    bid = 100.0
    ask = bid * (1.0 + threshold)

    calm_trip = r1.check_pre_synchronization(
        symbol="INFY",
        bid=bid,
        ask=ask,
        last_tick_age_sec=1.0,
        hist_atr=1.0,
        curr_bar_range=1.0,
    )

    stressed_trip = r2.check_pre_synchronization(
        symbol="INFY",
        bid=bid,
        ask=ask,
        last_tick_age_sec=1.0,
        hist_atr=1.0,
        curr_bar_range=1.0,
    )

    assert calm_trip.tripped is False
    assert stressed_trip.tripped is True
    assert stressed_trip.ansi_code == "ANSI 40"


def test_actual_micom_thresholds_are_dynamic():
    controller = Revision5DynamicParameterController()

    calm = controller.evaluate(calm_env())
    stressed = controller.evaluate(stressed_env())

    m1 = MasterGridProtectionMiCOM()
    m2 = MasterGridProtectionMiCOM()

    m1.apply_runtime_profile(
        calm.grid_protection
    )

    m2.apply_runtime_profile(
        stressed.grid_protection
    )

    crash = -(
        m1.nifty_crash_rate
        + m2.nifty_crash_rate
    ) / 2.0

    calm_trip = m1.evaluate_grid_intertie(
        nifty_15m_return=crash,
        nifty_vol_z=0.0,
        fleet_equity_drawdown_pct=0.0,
    )

    stressed_trip = m2.evaluate_grid_intertie(
        nifty_15m_return=crash,
        nifty_vol_z=0.0,
        fleet_equity_drawdown_pct=0.0,
    )

    assert calm_trip.tripped is False
    assert stressed_trip.tripped is True
    assert stressed_trip.ansi_code == "ANSI 81U"


def test_real_dcs_installs_snapshot_into_all_five_bays(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "native_dynamic.sqlite3",
    )

    plant.begin_bar(
        datetime(2026, 7, 31, 10, 0),
        1,
        environment=calm_env(),
    )

    snap1 = plant.dynamic_snapshot

    assert snap1 is not None

    for bay_id, bay in plant.bays.items():
        gp = snap1.governor_by_bay[bay_id]
        ap = snap1.avr_by_bay[bay_id]
        rp = snap1.unit_protection_by_bay[
            bay_id
        ]

        assert bay.governor.runtime_kp == pytest.approx(
            gp.kp
        )

        assert bay.avr.loading_fraction == pytest.approx(
            ap.loading_fraction
        )

        assert (
            bay.relay.max_spread_fraction
            == pytest.approx(
                rp.max_spread_fraction
            )
        )

    assert (
        plant.grid_relay.max_dd_pct
        == pytest.approx(
            snap1.grid_protection
            .max_daily_fleet_drawdown_pct
        )
    )

    # Same bar: a second environment cannot rewrite the bar's
    # parameter image.
    version = snap1.version

    plant.begin_bar(
        datetime(2026, 7, 31, 10, 0),
        1,
        environment=stressed_env(),
    )

    assert plant.dynamic_snapshot.version == version

    # Next bar: a fresh runtime image is installed.
    plant.begin_bar(
        datetime(2026, 7, 31, 10, 1),
        2,
        environment=stressed_env(),
    )

    assert plant.dynamic_snapshot.version == version + 1


def test_dynamic_cooldown_is_consumed_by_real_bay(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "cooldown.sqlite3",
    )

    plant.begin_bar(
        datetime(2026, 7, 31, 10, 0),
        10,
        environment=stressed_env(),
    )

    bay = next(iter(plant.bays.values()))
    expected = (
        plant.dynamic_snapshot
        .plant.loss_cooldown_bars
    )

    bay.register_outcome(
        realized_r=-1.0,
        reason="STOP",
        bar_index=10,
    )

    assert (
        bay.cooldown_until_bar_exclusive
        == 10 + expected + 1
    )


def test_hrsg_capital_update_reaches_real_avr(
    tmp_path,
):
    plant = CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=tmp_path / "capital.sqlite3",
    )

    bay = next(iter(plant.bays.values()))

    bay.update_capital(123_456.0)

    assert bay.avr.allocated_capital == pytest.approx(
        123_456.0
    )
