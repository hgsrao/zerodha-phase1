"""Five-turbine R/M/J acceptance tests (items 1-16; 17-18 = native suites, see report).
    PYTHONPATH=<worktree> python -m pytest -q revision6_avr/test_r6_turbine_governor_acceptance.py
"""
from dataclasses import FrozenInstanceError, replace
from math import pi

import pytest

from revision6_avr.r6_turbine_governor import (BAYS, GAS_BAYS, TurbineGovernorUnit, TurbinePhysicalConfig,
                                               inertia_constant_h, moment_from_m, omega_mechanical,
                                               repository_defaults, speed_droop_command, steady_state)

CFG = repository_defaults()
GTG, STG = "GTG1_HEAVY_INDUSTRY", "CSTG1_BFSI"


def unit(bay, **kw):
    return TurbineGovernorUnit(CFG[bay], mechanical_power_pu=0.5, **kw)


# 1-3 configuration ----------------------------------------------------------------------------
def test_01_all_five_r_is_4_percent():
    assert [CFG[b].droop_r for b in BAYS] == [0.04] * 5


def test_02_03_requested_provisional_m():
    assert {b: CFG[b].inertia_m for b in BAYS} == {"GTG1_HEAVY_INDUSTRY": 8.0, "GTG2_TECH_TELECOM": 8.0,
                                                   "CSTG1_BFSI": 5.0, "CSTG2_CONSUMER_AUTO": 5.0,
                                                   "BPSTG_HEALTHCARE": 5.0}
    assert all(CFG[b].turbine_type == ("GAS_TURBINE" if b in GAS_BAYS else "STEAM_TURBINE") for b in BAYS)
    assert all(CFG[b].provenance.startswith("PROVISIONAL") for b in BAYS)
    assert all(CFG[b].inertia_h_s == CFG[b].inertia_m / 2 for b in BAYS)


# 4-5 R invariant ----------------------------------------------------------------------------
def test_04_r_constant_after_thousands_of_steps():
    for b in BAYS:
        u, h0 = unit(b), CFG[b].config_hash()
        for k in range(5000):
            r = u.evaluate(0.5 + 0.2 * ((k // 500) % 2), 0.5, 0.02)
            assert r["accepted"] and r["droop_r"] == 0.04
        assert u.config.droop_r == 0.04 and u.config.config_hash() == h0


def test_05_adaptation_cannot_mutate_r():
    from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
    u = unit(GTG)
    with pytest.raises(FrozenInstanceError):
        u.config.droop_r = 0.05                             # direct mutation
    with pytest.raises(AttributeError):
        u.config = replace(u.config, droop_r=0.05)          # no setter on the unit
    mark_v = BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[GTG])
    for r in (-1.0, 0.4, -0.2, 1.1):                        # Mark V PID adapts its own state
        mark_v.register_trade(r)
    mark_v.runtime_kp, mark_v.runtime_ki = 9.0, 0.9         # even arbitrary gain changes
    assert u.config.droop_r == 0.04
    snap = u.snapshot()
    other = TurbineGovernorUnit(replace(CFG[GTG], droop_r=0.05))
    with pytest.raises(ValueError):
        other.restore(snap)                                 # restore cannot swap configuration


# 6-7 droop sign --------------------------------------------------------------------------------
@pytest.mark.parametrize("bay", BAYS)
def test_06_07_droop_sign(bay):
    c = CFG[bay]
    kw = dict(p_ref_pu=0.5, droop_r=c.droop_r, p_min_pu=c.power_min_pu, p_max_pu=c.power_max_pu)
    assert speed_droop_command(speed_pu=1.0, **kw) == pytest.approx(0.5)
    assert speed_droop_command(speed_pu=0.995, **kw) == pytest.approx(0.625)   # +0.005/0.04
    assert speed_droop_command(speed_pu=1.005, **kw) == pytest.approx(0.375)


# 8 inertia ---------------------------------------------------------------------------------
def test_08_higher_inertia_smaller_initial_acceleration_same_disturbance():
    g, s = unit(GTG), unit(STG)                             # M 8 vs 5, same point, same +0.1 pu load
    rg = g.evaluate(0.6, 0.5, 0.001, dt_max_s=0.001)
    rs = s.evaluate(0.6, 0.5, 0.001, dt_max_s=0.001)
    assert rg["dspeed_dt_pu_per_s"] == pytest.approx(-0.1 / 8.0, rel=0.02)
    assert rs["dspeed_dt_pu_per_s"] == pytest.approx(-0.1 / 5.0, rel=0.02)
    assert abs(rg["dspeed_dt_pu_per_s"]) < abs(rs["dspeed_dt_pu_per_s"])


# 9-10 M / J / H --------------------------------------------------------------------------------
def test_09_m_j_h_consistency():
    s_va, pp, f = 100e6, 1, 50.0
    w = omega_mechanical(f, pp)
    assert w == pytest.approx(100 * pi)
    j = moment_from_m(inertia_m=8.0, rated_mva=100.0, nominal_frequency_hz=f, pole_pairs=pp)
    assert j == pytest.approx(8.0 * s_va / w ** 2)
    assert 2 * inertia_constant_h(j_kg_m2=j, s_base_va=s_va, f_nominal_hz=f, pole_pairs=pp) == pytest.approx(8.0)
    c = replace(CFG[GTG], rated_mva=100.0, pole_pairs=1)
    assert c.derived_j == pytest.approx(j) and c.inertia_h_s == 4.0
    c2 = TurbinePhysicalConfig.from_inertia(j_kg_m2=j, s_base_va=s_va, pole_pairs=1, bay_id=GTG,
                                            machine_kind="GAS_TURBINE")
    assert c2.inertia_m == pytest.approx(8.0)
    # 2 pole pairs halve omega_mech, so the same M needs 4x J
    assert moment_from_m(inertia_m=8.0, rated_mva=100.0, nominal_frequency_hz=f, pole_pairs=2) == pytest.approx(4 * j)


@pytest.mark.parametrize("bad", [dict(inertia_m=0.0), dict(inertia_m=-5.0), dict(inertia_m=float("nan")),
                                 dict(droop_r=0.0), dict(droop_r=-0.04), dict(droop_r=1.0),
                                 dict(rated_mva=100.0), dict(pole_pairs=1), dict(inertia_j=1e4),
                                 dict(rated_mva=100.0, pole_pairs=0), dict(rated_mva=-1.0, pole_pairs=1),
                                 dict(rated_mva=100.0, pole_pairs=1, inertia_j=1.0),       # contradicts M
                                 dict(power_min_pu=1.0, power_max_pu=1.0)])
def test_10_invalid_or_contradictory_parameters(bad):
    with pytest.raises(ValueError):
        replace(CFG[GTG], **bad)


# 11-12 saturation, separate lag and inertia ------------------------------------------------
def test_11_saturation_reported_and_respected():
    u = unit(GTG)
    for _ in range(400):
        r = u.evaluate(1.4, 0.9, 0.1)
    assert r["saturated"] == "HIGH" and r["p_command_pu"] == 1.0 and r["mechanical_power_pu"] <= 1.0 + 1e-12
    low = unit(STG).evaluate(0.0, 0.0, 0.1)
    assert low["p_command_pu"] >= 0.0


def test_12_turbine_lag_and_rotor_inertia_are_separate():
    # (a) with speed held at 1 (huge M), Pm follows the command with time constant tau
    c = replace(CFG[STG], inertia_m=1e9)
    u = TurbineGovernorUnit(c, mechanical_power_pu=0.0)
    r = u.evaluate(0.0, 0.8, c.turbine_response_tau, dt_max_s=c.turbine_response_tau / 1000)
    assert r["mechanical_power_pu"] == pytest.approx(0.8 * (1 - 2.718281828459045 ** -1), rel=1e-3)
    # (b) changing tau changes the power response but not M; changing M changes ROCOF, not tau
    fast = TurbineGovernorUnit(replace(CFG[STG], turbine_response_tau=0.1), mechanical_power_pu=0.5)
    slow = TurbineGovernorUnit(replace(CFG[STG], turbine_response_tau=2.0), mechanical_power_pu=0.5)
    a, b = fast.evaluate(0.6, 0.5, 1.0), slow.evaluate(0.6, 0.5, 1.0)
    assert a["mechanical_power_pu"] > b["mechanical_power_pu"] and a["inertia_m"] == b["inertia_m"]


# 13 independence -------------------------------------------------------------------------------
def test_13_five_instances_do_not_share_state():
    units = {b: unit(b) for b in BAYS}
    units[GTG].evaluate(0.9, 0.5, 2.0)
    assert units[GTG].speed_pu != 1.0
    assert all(units[b].speed_pu == 1.0 and units[b].state is not units[GTG].state for b in BAYS if b != GTG)


# 14 invalid inputs -------------------------------------------------------------------------------
@pytest.mark.parametrize("args", [(0.5, 0.5, 0.0), (0.5, 0.5, -1.0), (float("nan"), 0.5, 0.1),
                                  (0.5, float("inf"), 0.1), (0.5, 0.5, float("nan")), (True, 0.5, 0.1)])
def test_14_invalid_inputs_rejected_without_state_change(args):
    u = unit(GTG)
    before = u.snapshot()
    r = u.evaluate(*args)
    assert r["accepted"] is False and r["reason"]
    assert {k: v for k, v in u.snapshot().items() if k != "operating_state"} == \
        {k: v for k, v in before.items() if k != "operating_state"}
    with pytest.raises(ValueError):
        u.step(dt_s=args[2], p_ref_pu=args[1], p_electrical_pu=args[0])


def test_14b_timestamps_must_advance_by_dt():
    u = unit(GTG)
    assert u.evaluate(0.5, 0.5, 0.1, timestamp_s=10.0)["accepted"]
    assert u.evaluate(0.5, 0.5, 0.1, timestamp_s=10.1)["accepted"]
    assert not u.evaluate(0.5, 0.5, 0.1, timestamp_s=10.1)["accepted"]       # replay
    assert not u.evaluate(0.5, 0.5, 0.1, timestamp_s=9.0)["accepted"]        # backwards
    assert not u.evaluate(0.5, 0.5, 0.1, timestamp_s=11.0)["accepted"]       # gap


# 15 determinism, stability, GTG vs STG disturbance comparison --------------------------------
def disturbance(bay):
    u = unit(bay)
    trace = [u.evaluate(0.6, 0.5, 0.05) for _ in range(1200)]                 # +0.1 pu step, 60 s
    nadir = min(r["speed_pu"] for r in trace)
    final = trace[-1]
    ss = steady_state(CFG[bay], p_ref_pu=0.5, p_elec_pu=0.6)
    settle = next(k for k in range(len(trace)) if all(abs(r["speed_pu"] - ss["speed_pu"]) < 2e-4 for r in trace[k:]))
    return {"nadir": nadir, "final": final, "ss": ss, "settle_s": settle * 0.05, "rocof0": trace[0]["dspeed_dt_pu_per_s"]}


def test_15_deterministic_stable_and_gtg_vs_stg():
    for b in BAYS:
        a, b2 = disturbance(b), disturbance(b)
        assert a == b2                                                         # bit-identical
        assert a["final"]["speed_pu"] == pytest.approx(a["ss"]["speed_pu"], abs=1e-6)
        assert a["final"]["mechanical_power_pu"] == pytest.approx(a["ss"]["mechanical_power_pu"], abs=1e-6)
    g, s = disturbance(GTG), disturbance(STG)
    assert abs(g["rocof0"]) < abs(s["rocof0"])                                 # M 8 vs 5
    assert g["nadir"] >= g["ss"]["speed_pu"] - 0.01 and s["nadir"] >= s["ss"]["speed_pu"] - 0.01


# 16 reset / restore cannot change droop ------------------------------------------------------
def test_16_reset_and_restore_keep_droop():
    u = unit(STG)
    u.evaluate(0.7, 0.5, 1.0)
    snap = u.snapshot()
    u.reset()
    assert u.config.droop_r == 0.04 and u.speed_pu == 1.0
    u.restore(snap)
    assert u.config.droop_r == 0.04 and u.snapshot() == snap
    tampered = dict(snap, config_hash="0" * 64)
    with pytest.raises(ValueError):
        u.restore(tampered)
