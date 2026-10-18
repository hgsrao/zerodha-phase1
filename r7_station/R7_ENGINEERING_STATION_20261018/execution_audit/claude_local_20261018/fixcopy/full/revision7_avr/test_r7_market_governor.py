from dataclasses import replace, FrozenInstanceError
import math
import pytest
from .r7_market_governor import MarketGovernor
from revision6_avr.r6_turbine_governor import repository_defaults, TurbineGovernorUnit


def test_tracking_error_subtracts_actual_once(config):
    g = MarketGovernor(config)
    r = g.step(0.6, -0.3, 60)
    assert r.error == pytest.approx(0.9)
    assert r.p == pytest.approx(config.kp * 0.9)
    r = g.step(0.6, 0.6, 60)
    assert r.error == 0


def test_slew_and_antiwindup(config):
    g = MarketGovernor(config)
    previous = 0
    for _ in range(1000):
        out = g.step(1, -5, 1)
        assert abs(g.command - previous) <= config.command_slew_per_second + 1e-12
        previous = g.command
        assert abs(out.applied) <= 1
    assert abs(g.integral) <= config.integral_limit
    assert g.integral == 0


def test_market_response_time_is_seconds(config):
    g = MarketGovernor(replace(config, command_slew_per_second=10, ki_per_second=0, kd_seconds=0))
    r = g.step(1, 0, 60)
    assert r.applied == pytest.approx(1 - math.exp(-60 / config.market_response_tau_seconds))


def test_governor_has_no_market_speed_input(config):
    import inspect
    assert 'market_speed_pu' not in inspect.signature(MarketGovernor.step).parameters
    out = MarketGovernor(config).step(0.2, 0.0, 60)
    assert out.target == 0.2 and out.error == pytest.approx(0.2)


def test_at_target_pid_zero_keeps_engagement(config):
    g = MarketGovernor(replace(config, ki_per_second=0, kd_seconds=0))
    for _ in range(40):
        out = g.step(0.5, 0.5, 60)
    assert out.error == 0
    assert out.applied == pytest.approx(0.5)


def test_five_physical_units_have_fixed_r_and_real_swing():
    configs = repository_defaults()
    assert [c.droop_r for c in configs.values()] == [0.04] * 5
    assert [c.inertia_m for c in configs.values()] == [8, 8, 5, 5, 5]
    for c in configs.values():
        assert c.inertia_h_s == c.inertia_m / 2
        with pytest.raises(FrozenInstanceError):
            c.droop_r = 0.5
        u = TurbineGovernorUnit(c, p_mech_pu=0.3)
        r = u.step(dt_s=0.1, p_ref_pu=0.5, p_electrical_pu=0.4)
        assert r['accepted']
        assert u.state.speed_pu != 1.0
