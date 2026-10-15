"""Dynamization proofs for the shared Revision 5 core.

Every declared dynamic value is recomputed every native-plant bar from its documented formula
(revision5/dynamic_parameters.py; coefficients registry-owned in revision5/controller_parameters.py)
and its runtime consumer uses that value.  Expected values below are computed INDEPENDENTLY from
the documented formulas (re-implemented here with the registry default coefficients), never by
calling the code under test.

Covered: governor schedule (per bay), AVR profile and lot sizing, SEL-300G / MiCOM limits, bay
cooldowns, the R5 gain scale onto Box 6, the strategy / execution / safety / dispatch profiles and
their consumers (dynamic_profile_mode='consume'), bounds, warm-up and missing-input behaviour,
causality, state and symbol isolation, and checkpoint / resume equivalence.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.controller_parameters import CONTROLLER_PARAMETERS, default_controller_parameters
from revision5.dynamic_parameters import R5EnvironmentState
from revision5.governor import BAY_GOVERNOR_SPECS
from revision5.topology import BAY_IDS

D = {name: spec.default for name, spec in CONTROLLER_PARAMETERS.items()}
clip = lambda x, lo, hi: max(lo, min(hi, x))

ENVS = [
    R5EnvironmentState(normalized_vol=1.0, trend_probability=0.6, chop_probability=0.3, turbulent_probability=0.1,
                       fleet_drawdown_fraction=0.0, control_error_delta=0.0),
    R5EnvironmentState(normalized_vol=3.1, trend_probability=0.1, chop_probability=0.2, turbulent_probability=0.7,
                       fleet_drawdown_fraction=0.3, control_error_delta=1.7),
    R5EnvironmentState(normalized_vol=0.3, trend_probability=2.0, chop_probability=1.0, turbulent_probability=1.0,
                       fleet_drawdown_fraction=1.4, control_error_delta=-0.6),
    R5EnvironmentState(normalized_vol=1.6, trend_probability=0.0, chop_probability=0.0, turbulent_probability=1.0,
                       fleet_drawdown_fraction=0.9, control_error_delta=3.5),
]


def state(env):
    """Independent re-implementation of the documented normalization and common state."""
    total = env.trend_probability + env.chop_probability + env.turbulent_probability
    trend, chop, turb = env.trend_probability / total, env.chop_probability / total, env.turbulent_probability / total
    vol = clip(env.normalized_vol, D["dyn_vol_min"], D["dyn_vol_max"])
    dd = clip(env.fleet_drawdown_fraction, 0.0, 1.0)
    stress = clip(D["dyn_stress_turbulent_weight"] * turb + D["dyn_stress_chop_weight"] * chop
                  + D["dyn_stress_excess_vol_weight"] * max(0.0, vol - 1.0), 0.0, 1.0)
    rd = clip(1.0 - D["dyn_risk_derate_stress_gain"] * stress - D["dyn_risk_derate_drawdown_gain"] * dd,
              D["dyn_risk_derate_lower_bound"], 1.0)
    return dict(trend=trend, chop=chop, turb=turb, vol=vol, stress=stress, rd=rd,
                tb=clip(trend - turb, -1.0, 1.0), e=abs(env.control_error_delta))


def expected_governor(spec, s):
    g = lambda n: D[f"gsch_{n}"]
    st, e, tb, vol, rd = s["stress"], s["e"], s["tb"], s["vol"], s["rd"]
    return {
        "target_r": clip(spec.target_r * (1 + g("target_trend_gain") * tb - g("target_stress_gain") * st),
                         spec.target_r * g("target_min_scale"), spec.target_r * g("target_max_scale")),
        "outcome_window": int(round(clip(spec.outcome_window * (1 + g("window_stress_gain") * st), g("window_min"), g("window_max")))),
        "integral_clamp": clip(spec.integral_clamp / (1 + g("integral_stress_gain") * st + g("integral_error_gain") * e),
                               spec.integral_clamp * g("integral_min_scale"), spec.integral_clamp * g("integral_max_scale")),
        "grid_droop_gain": clip(spec.grid_droop_gain * vol * (1 + g("droop_gain_stress_gain") * st),
                                spec.grid_droop_gain * g("droop_gain_min_scale"), spec.grid_droop_gain * g("droop_gain_max_scale")),
        "grid_droop_max": clip(spec.grid_droop_max * (1 + g("droop_max_stress_gain") * st),
                               spec.grid_droop_max * g("droop_max_min_scale"), spec.grid_droop_max * g("droop_max_max_scale")),
        "kp": spec.kp * clip(1 + g("kp_error_gain") * e + g("kp_stress_gain") * st, g("kp_scale_min"), g("kp_scale_max")),
        "ki": spec.ki / clip(1 + g("ki_error_gain") * e + g("ki_stress_gain") * st, g("ki_divisor_min"), g("ki_divisor_max")),
        "kd": spec.kd * clip(1 + g("kd_error_gain") * e + g("kd_stress_gain") * st, g("kd_scale_min"), g("kd_scale_max")),
        "base_z": clip(spec.base_z + g("base_z_trend_gain") * tb - g("base_z_stress_gain") * st,
                       spec.base_z - g("base_z_lower_offset"), spec.base_z + g("base_z_upper_offset")),
        "droop_r": clip(spec.droop_r * (1 - g("droop_r_stress_gain") * st), spec.droop_r * g("droop_r_min_scale"), spec.droop_r),
        "dynamic_offset_min": clip(spec.dynamic_offset_min * (1 + g("offset_min_stress_gain") * st),
                                   spec.dynamic_offset_min * g("offset_min_lower_scale"), spec.dynamic_offset_min * g("offset_min_upper_scale")),
        "dynamic_offset_max": clip(spec.dynamic_offset_max * rd, 0.0, spec.dynamic_offset_max),
    }


def expected_avr(s):
    a = lambda n: D[f"avr_{n}"]
    loading = clip(a("loading_fraction_base") * s["rd"], a("loading_fraction_min"), a("loading_fraction_base"))
    cs = clip(1.0 - loading / a("loading_fraction_base"), 0.0, 1.0)
    return {
        "loading_fraction": loading,
        "oel_fraction": clip(a("oel_capital_fraction") * s["rd"], a("oel_fraction_min"), a("oel_capital_fraction")),
        "vol_scalar_max": clip(a("vol_scalar_max_base") * s["rd"], a("vol_scalar_max_min"), a("vol_scalar_max_base")),
        "z_strength_denominator": clip(a("z_strength_denominator_base") * (1 + a("z_strength_denominator_stress_gain") * s["stress"]),
                                       a("z_strength_denominator_base"), a("z_strength_denominator_max")),
        "z_strength_cap": clip(a("z_strength_cap_base") * s["rd"], a("z_strength_cap_min"), a("z_strength_cap_base")),
        "runtime_kp": clip(a("kp_base") + a("kp_stress_gain") * cs, a("kp_base"), a("kp_max")),
        "runtime_ki": clip(a("ki_base") / (1 + a("ki_stress_gain") * cs), a("ki_min"), a("ki_base")),
        "runtime_kd": clip(a("kd_base") + a("kd_stress_gain") * cs, a("kd_base"), a("kd_max")),
        "runtime_integral_clamp": clip(a("integral_clamp_base") - a("integral_clamp_stress_gain") * cs,
                                       a("integral_clamp_min"), a("integral_clamp_base")),
        "undervoltage_trip_pu": clip(a("undervoltage_trip_base_pu") + a("undervoltage_trip_stress_gain") * cs,
                                     a("undervoltage_trip_base_pu"), a("undervoltage_trip_max_pu")),
        "overvoltage_trip_pu": clip(a("overvoltage_trip_base_pu") - a("overvoltage_trip_stress_gain") * cs,
                                    a("overvoltage_trip_min_pu"), a("overvoltage_trip_base_pu")),
        "excitation_min": clip(a("excitation_min_base") + a("excitation_min_stress_gain") * cs, a("excitation_min_base"), a("excitation_min_max")),
        "excitation_max": clip(a("excitation_max_base") - a("excitation_max_stress_gain") * cs, a("excitation_max_min"), a("excitation_max_base")),
    }


def expected_protection(s):
    t = lambda base, gain, low: clip(D[base] * (1 - D[gain] * s["stress"]), D[low], D[base])
    return {
        "stale_tick_limit_seconds": t("sel_stale_tick_limit_seconds", "sel_stale_tick_stress_gain", "sel_stale_tick_min_seconds"),
        "max_spread_fraction": t("sel_max_spread_fraction", "sel_spread_stress_gain", "sel_spread_min_fraction"),
        "vol_surge_mult": t("sel_vol_surge_mult", "sel_vol_surge_stress_gain", "sel_vol_surge_min_mult"),
        "max_slippage_pct": t("sel_max_slippage_fraction", "sel_slippage_stress_gain", "sel_slippage_min_fraction"),
        "max_adverse_bars": int(round(t("sel_max_adverse_bars", "sel_adverse_stress_gain", "sel_adverse_min_bars"))),
        "max_dd_pct": t("micom_max_daily_fleet_drawdown_pct", "micom_drawdown_stress_gain", "micom_drawdown_min_pct"),
        "nifty_crash_rate": t("micom_nifty_crash_rate_pct", "micom_crash_stress_gain", "micom_crash_min_pct"),
        "max_regime_vol_z": t("micom_max_regime_vol_z", "micom_vol_z_stress_gain", "micom_vol_z_min"),
        "loss_cooldown_bars": int(round(clip(D["plant_loss_cooldown_base_bars"] * (D["plant_loss_cooldown_stress_offset"] + D["plant_loss_cooldown_stress_gain"] * s["stress"]),
                                             D["plant_loss_cooldown_min_bars"], D["plant_loss_cooldown_max_bars"]))),
        "target_cooldown_bars": int(round(clip(D["plant_target_cooldown_base_bars"] * (D["plant_target_cooldown_stress_offset"] + D["plant_target_cooldown_stress_gain"] * s["stress"]),
                                               D["plant_target_cooldown_min_bars"], D["plant_target_cooldown_max_bars"]))),
    }


def stepped_plant():
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    for bar, env in enumerate(ENVS):
        plant.step_bar(current_dt=datetime(2024, 1, 2, 10, 0) + timedelta(minutes=bar), bar_index=bar, environment=env)
        yield bar, env, plant


# ------------------------------------------------------- per-bar recomputation + runtime consumers

def test_governor_runtime_values_follow_the_schedule_every_bar_and_are_not_frozen():
    seen = {}
    for bar, env, plant in stepped_plant():
        s = state(env)
        for bay_id, bay in plant.bays.items():
            expected = expected_governor(BAY_GOVERNOR_SPECS[bay_id], s)
            for name, value in expected.items():
                assert getattr(bay.governor, f"runtime_{name}") == pytest.approx(value, rel=1e-12, abs=1e-15), (bar, bay_id, name)
            seen.setdefault(bay_id, []).append(tuple(expected.values()))
    for bay_id, rows in seen.items():
        assert len(set(rows)) == len(rows)                                   # every bar's operating point differs


def test_governor_consumer_uses_the_current_runtime_values():
    for bar, env, plant in stepped_plant():
        g = plant.bays["CSTG1_BFSI"].governor
        g.register_trade(-1.0 if bar % 2 else 0.8)
        u = g.last_control_u
        offset = clip(-g.runtime_feedback_gain * u, g.runtime_dynamic_offset_min, g.runtime_dynamic_offset_max)
        for grid in (0.0, -0.004, -0.03):
            droop = min(max(0.0, -grid) / g.runtime_droop_r * g.runtime_grid_droop_gain, g.runtime_grid_droop_max)
            assert g.dynamic_z(grid) == pytest.approx(g.runtime_base_z + offset - droop, rel=1e-12)
        # Outer-loop integral bounded by THIS bar's scheduled clamp.
        assert abs(g.integral_error) <= g.runtime_integral_clamp + 1e-12


def test_avr_runtime_values_and_lot_sizing_follow_the_schedule_every_bar():
    for bar, env, plant in stepped_plant():
        expected = expected_avr(state(env))
        avr = plant.bays["GTG2_TECH_TELECOM"].avr
        for name, value in expected.items():
            if name != "oel_fraction":
                assert getattr(avr, name) == pytest.approx(value, rel=1e-12), (bar, name)
        assert avr.oel_max == pytest.approx(avr.allocated_capital * expected["oel_fraction"], rel=1e-12)
        for price, atr, z in ((1000.0, 2.0, -3.0), (1500.0, 30.0, 1.2), (800.0, 4.0, -0.7)):
            vol_scalar = clip(1.0 / (atr / price * 100.0), avr.vol_scalar_min, expected["vol_scalar_max"])
            notional = (avr.allocated_capital * expected["loading_fraction"] * avr.excitation_command * vol_scalar
                        * min(expected["z_strength_cap"], abs(z) / expected["z_strength_denominator"]))
            qty, _ = avr.calculate_lot_size(asset_price=price, asset_atr=atr, z_strength=z)
            if notional > avr.oel_max:
                assert qty == int(avr.oel_max / price)
            elif notional < avr.uel_min:
                assert qty == 0
            else:
                assert qty == int(notional / price)


def test_protection_and_cooldown_limits_follow_the_schedule_and_drive_trips():
    for bar, env, plant in stepped_plant():
        expected = expected_protection(state(env))
        bay = plant.bays["BPSTG_HEALTHCARE"]
        for name in ("stale_tick_limit_seconds", "max_spread_fraction", "vol_surge_mult", "max_slippage_pct", "max_adverse_bars"):
            assert getattr(bay.relay, name) == pytest.approx(expected[name], rel=1e-12), (bar, name)
        for name in ("max_dd_pct", "nifty_crash_rate", "max_regime_vol_z"):
            assert getattr(plant.grid_relay, name) == pytest.approx(expected[name], rel=1e-12), (bar, name)
        assert (bay.loss_cooldown_bars, bay.target_cooldown_bars) == (expected["loss_cooldown_bars"], expected["target_cooldown_bars"])
        # Consumers: a bar range just above / below this bar's surge limit trips / passes.
        limit = expected["vol_surge_mult"]
        assert bay.relay.check_pre_synchronization("X", 100.0, 100.01, 0.0, 1.0, limit * 1.01).ansi_code == "ANSI 24"
        assert bay.relay.check_pre_synchronization("X", 100.0, 100.01, 0.0, 1.0, limit * 0.99).ansi_code == "NORMAL"
        assert plant.grid_relay.evaluate_grid_intertie(-expected["nifty_crash_rate"] * 1.001, 0.0, 0.0).ansi_code == "ANSI 81U"
        bay.cooldown_until_bar_exclusive = 0
        bay.consecutive_stops = 0
        bay.register_outcome(realized_r=-0.5, reason="X", bar_index=bar)
        assert bay.cooldown_until_bar_exclusive == bar + expected["loss_cooldown_bars"] + 1


def test_missing_or_invalid_environment_fails_closed_and_warmup_is_neutral():
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    bad = [R5EnvironmentState(normalized_vol=math.nan), R5EnvironmentState(normalized_vol=0.0),
           R5EnvironmentState(trend_probability=-0.1), R5EnvironmentState(trend_probability=0.0, chop_probability=0.0, turbulent_probability=0.0),
           R5EnvironmentState(control_error_delta=math.inf)]
    for k, env in enumerate(bad):
        with pytest.raises(ValueError):
            plant.step_bar(current_dt=datetime(2024, 1, 2, 10, k), bar_index=k, environment=env)
    from tests.test_r5_paper_apply import engine
    o, _ = engine()
    env = o._plant_environment([])                     # warm-up: no measured symbol telemetry yet
    assert (env.normalized_vol, env.turbulent_probability, env.trend_probability, env.chop_probability) == (
        1.0, 1.0 / 3.0, (1 - 1.0 / 3.0) * 0.5, (1 - 1.0 / 3.0) * 0.5)


def test_earlier_snapshot_is_immutable_and_independent_of_later_inputs():
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    first = plant.step_bar(current_dt=datetime(2024, 1, 2, 10, 0), bar_index=0, environment=ENVS[0])["dynamic_snapshot"]
    copy = (first.stress_index, first.governor_by_bay["GTG1_HEAVY_INDUSTRY"], first.strategy, first.safety)
    plant.step_bar(current_dt=datetime(2024, 1, 2, 10, 1), bar_index=1, environment=ENVS[3])
    assert (first.stress_index, first.governor_by_bay["GTG1_HEAVY_INDUSTRY"], first.strategy, first.safety) == copy
    other = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    again = other.step_bar(current_dt=datetime(2024, 1, 2, 10, 0), bar_index=0, environment=ENVS[0])["dynamic_snapshot"]
    assert again.governor_by_bay == first.governor_by_bay and again.strategy == first.strategy


def test_schedule_refresh_preserves_controller_state_and_bays_stay_isolated():
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    g1, g2 = plant.bays["GTG1_HEAVY_INDUSTRY"].governor, plant.bays["GTG2_TECH_TELECOM"].governor
    for r in (-1.0, -0.4):
        g1.register_trade(r)
    g1.evaluate_position_control(measured_r=-0.3, reference_r=0.2, max_favorable_r=0.1, elapsed_bars=3, min_hold_bars=1,
                                 max_hold_bars=30, hard_stop_r=-1.0, trade_target_r=2.0, position_id="p")
    before = (list(g1.history_r), g1.last_error, g1.last_control_u, g1.inner_state("p"))
    plant.step_bar(current_dt=datetime(2024, 1, 2, 10, 0), bar_index=0, environment=ENVS[1])
    assert (list(g1.history_r), g1.last_error, g1.last_control_u) == tuple(before[:3])
    assert g1.inner_state("p").last_error == before[3].last_error and g1.inner_state("p").active
    assert abs(g1.integral_error) <= g1.runtime_integral_clamp + 1e-12
    assert g2.history_r == [] and g2.integral_error == 0.0                     # no cross-bay state


# ------------------------------------------------------- R5 gain scale onto Box 6 (per bar)

def test_r5_gain_scale_reaches_the_box6_pid_tunings_every_bar():
    from tests.test_r5_paper_apply import engine
    from tests_external.test_audit_remediation import signal
    from revision2.contracts import IDDecision
    o, ts = engine()
    o._native_bar_index = 0
    o._plant_bar_step(ts, [], {})
    plant_g = o.real_plant_dcs.bays["GTG2_TECH_TELECOM"].governor
    spec = BAY_GOVERNOR_SPECS["GTG2_TECH_TELECOM"]
    scale = o._gain_scale_for("INFY")
    assert scale == pytest.approx((plant_g.runtime_kp / spec.kp, plant_g.runtime_ki / spec.ki, plant_g.runtime_kd / spec.kd))
    assert scale != (1.0, 1.0, 1.0)
    o.mpc.gain_scale = scale
    o.mpc.build_plan(signal(), IDDecision(True, "x", 0.6, 2.0, 0.6), 1000.0, 5.0, o.config)
    base = tuple(float(o.config.require(n)) for n in ("pid_kp_entry", "pid_ki_entry", "pid_kd_entry"))
    # First plan: baseline == confidence, so the Tier-3 factor is exactly 1 and only the R5 scale remains.
    assert o.mpc._entry_pids["INFY"].tunings == pytest.approx(tuple(b * k for b, k in zip(base, scale)))


# ------------------------------------------------------- runtime profiles (consume mode)

def expected_profiles(s, bases, registry):
    p = lambda n: D[f"psch_{n}"]
    reg = lambda name, v: min(max(v, registry.params[name].minimum), registry.params[name].maximum)
    strategy = {
        "entry_confidence_threshold": reg("entry_confidence_threshold", clip(bases["entry_confidence_threshold"] + p("entry_conf_stress_gain") * s["stress"] + p("entry_conf_chop_gain") * s["chop"], p("entry_conf_min"), p("entry_conf_max"))),
        "min_risk_reward_ratio": reg("min_risk_reward_ratio", clip(bases["min_risk_reward_ratio"] + p("min_rr_stress_gain") * s["stress"], p("min_rr_min"), p("min_rr_max"))),
        "min_hold_bars": int(reg("min_hold_bars", int(round(clip(bases["min_hold_bars"] + p("min_hold_chop_gain") * s["chop"] + p("min_hold_turbulent_gain") * s["turb"], p("min_hold_min"), p("min_hold_max")))))),
        "max_hold_bars": int(reg("max_hold_bars", int(round(clip(bases["max_hold_bars"] - p("max_hold_stress_gain") * s["stress"] + p("max_hold_trend_gain") * s["trend"], p("max_hold_min"), p("max_hold_max")))))),
    }
    safety = {
        "drawdown_halt_threshold": clip(bases["safety_drawdown_halt_threshold"] * (1 - p("drawdown_halt_stress_gain") * s["stress"]), p("drawdown_halt_min"), bases["safety_drawdown_halt_threshold"]),
        "max_gross_exposure_fraction": clip(bases["max_gross_exposure_fraction"] * s["rd"], p("gross_exposure_min"), bases["max_gross_exposure_fraction"]),
        "max_exposure_per_symbol_fraction": clip(bases["max_exposure_per_symbol_fraction"] * s["rd"], p("symbol_exposure_min"), bases["max_exposure_per_symbol_fraction"]),
        "max_concurrent_positions": int(round(clip(bases["max_concurrent_positions"] * s["rd"], p("concurrent_positions_min"), bases["max_concurrent_positions"]))),
        "daily_loss_multiplier": clip(s["rd"], p("daily_loss_multiplier_min"), 1.0),
        "slippage": min(bases["max_slippage_fraction"], clip(bases["max_slippage_fraction"] * (1 + p("slippage_vol_gain") * (s["vol"] - 1)), p("slippage_min_fraction"), p("slippage_max_fraction"))),
        "data_age": int(round(clip(bases["max_market_data_age_seconds"] / (1 + p("data_age_stress_gain") * s["stress"]), p("data_age_min_seconds"), bases["max_market_data_age_seconds"]))),
        "dispatch_ceiling": clip(D["disp_allocation_ceiling"] * s["rd"], p("dispatch_ceiling_min"), D["disp_allocation_ceiling"]),
    }
    return strategy, safety


def _consume_engine():
    from tests.test_r5_paper_apply import engine as base_engine
    import tests.test_r5_paper_apply as pa_mod
    original = pa_mod.Engine
    pa_mod.Engine = lambda *a, **k: original(*a, dynamic_profile_mode="consume", **k)
    try:
        return base_engine()
    finally:
        pa_mod.Engine = original


@pytest.mark.parametrize("env", ENVS, ids=range(len(ENVS)))
def test_runtime_profiles_reach_their_consumers_each_bar(env, monkeypatch):
    o, ts = _consume_engine()
    monkeypatch.setattr(o, "_plant_environment", lambda tick_events: env)
    o._native_bar_index = 0
    o._plant_bar_step(ts, [], {})
    strategy, safety = expected_profiles(state(env), o.controller_parameters.profile_bases, o.registry)
    cfg = o._decision_cfg()
    assert cfg is not o.config
    for name, value in strategy.items():
        assert cfg.require(name) == pytest.approx(value, rel=1e-12), name
    assert o.config.require("entry_confidence_threshold") == 0.15                   # the base is untouched
    gate = o.entry_decision_engine.config
    contract = o.safety_contract.values
    assert gate.drawdown_halt_threshold == pytest.approx(min(contract["safety_drawdown_halt_threshold"], safety["drawdown_halt_threshold"]))
    assert gate.max_gross_exposure_fraction == pytest.approx(safety["max_gross_exposure_fraction"])
    assert gate.max_exposure_per_symbol_fraction == pytest.approx(safety["max_exposure_per_symbol_fraction"])
    assert gate.max_concurrent_positions == safety["max_concurrent_positions"]
    assert gate.daily_loss_halt_threshold == pytest.approx(contract["max_daily_loss_rupees"] * safety["daily_loss_multiplier"])
    assert gate.slippage_tolerance_percent == pytest.approx(safety["slippage"])
    assert gate.max_market_data_age_seconds == safety["data_age"]
    assert gate.order_timeout_seconds == o._base_gate_config.order_timeout_seconds    # tighten-only: never longer
    assert o.real_plant_dcs.dispatcher.dispatch_ceiling == pytest.approx(safety["dispatch_ceiling"])
    # Actuation: the tightened gates reject what the contract gates admit; the plan carries the holds.
    from gates_framework import SystemState
    gates = {type(g).__name__: g for g in o.entry_decision_engine.gates}
    at_limit = SystemState(portfolio_value=1e6, open_positions_count=safety["max_concurrent_positions"],
                           current_dd_percent=gate.drawdown_halt_threshold)
    base_gates = {type(g).__name__: g for g in type(o.entry_decision_engine)(config=o._base_gate_config).gates}
    if safety["max_concurrent_positions"] < contract["max_concurrent_positions"]:
        assert not gates["Gate05ConcurrentPositions"].evaluate(at_limit).passed
        assert base_gates["Gate05ConcurrentPositions"].evaluate(at_limit).passed
    if gate.drawdown_halt_threshold < contract["safety_drawdown_halt_threshold"]:
        assert not gates["Gate02DrawdownHalt"].evaluate(at_limit).passed
        assert base_gates["Gate02DrawdownHalt"].evaluate(at_limit).passed
    from tests_external.test_audit_remediation import signal
    from revision2.contracts import IDDecision
    plan, _, _ = o.mpc.build_plan(signal(), IDDecision(True, "x", 0.6, 2.0, 0.6), 1000.0, 5.0, cfg)
    assert (plan.minimum_hold_bars, plan.maximum_hold_bars) == (strategy["min_hold_bars"], strategy["max_hold_bars"])


def _consumed(o, ts):
    """What the consumers actually receive this bar, per environment (decision overlay, gate config, dispatch cap)."""
    out = []
    for env in ENVS:
        k = o.__dict__.setdefault("_sweep_bar", 0)
        o._sweep_bar = k + 1
        o._plant_environment = lambda tick_events, env=env: env
        o._native_bar_index = k
        o._plant_bar_step(ts + pd.Timedelta(seconds=k), [], {})
        out.append((o._decision_config_key, o.entry_decision_engine.config,
                    o.real_plant_dcs.dispatcher.runtime_ceiling_cap))
    return out


@pytest.fixture(scope="module")
def consume_engine():
    return _consume_engine()


def _profile_candidates(spec):
    d, lo, hi = spec.default, spec.minimum, spec.maximum
    fractions = (0.02, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0)
    return [v for v in dict.fromkeys([d + (hi - d) * f for f in fractions] + [d - (d - lo) * f for f in fractions])
            if v != d and lo <= v <= hi]


from revision5.controller_parameters import (PROFILE_SCHEDULE_PARAMETERS, R5ControllerParameters,  # noqa: E402
                                             ControllerParameterError, controller_parameter_defaults, INACTIVE)


@pytest.mark.parametrize("name", PROFILE_SCHEDULE_PARAMETERS)
def test_every_profile_coefficient_moves_its_consumer_or_is_inactive(name, consume_engine):
    """TUNABLE / FIXED_SAFETY psch_* rows: some in-bounds value changes what a consumer receives.
    INACTIVE psch_* rows: no value changes anything a consumer receives."""
    o, ts = consume_engine
    bases = dict(o.safety_contract.values)
    default = o.controller_parameters

    def install(params):
        o.controller_parameters = params
        o.real_plant_dcs.apply_controller_parameters(params)
    baseline = _consumed(o, ts)
    spec, changed, tried = CONTROLLER_PARAMETERS[name], False, []
    try:
        for value in _profile_candidates(spec):
            values = controller_parameter_defaults()
            values[name] = value
            try:
                params = R5ControllerParameters.from_values(values, safety_bases=bases)
            except ControllerParameterError:
                continue
            tried.append(value)
            install(params)
            changed = _consumed(o, ts) != baseline
            if changed:
                break
    finally:
        install(default)
    assert tried, name
    if spec.safety_class == INACTIVE:
        assert not changed, f"{name} is INACTIVE but changed a consumer"
    else:
        assert changed, f"{name} ({spec.safety_class}) moved no consumer for any of {tried}"


def test_consume_mode_tracks_the_profile_across_bars_and_off_mode_never_consumes():
    o, ts = _consume_engine()
    applied = []
    for k, env in enumerate(ENVS):
        o._plant_environment = lambda tick_events, env=env: env
        o._native_bar_index = k
        o._plant_bar_step(ts + pd.Timedelta(minutes=k), [], {})
        applied.append(o._decision_cfg().require("min_risk_reward_ratio"))
    assert len(set(applied)) > 1                                                 # not frozen at startup
    from tests.test_r5_paper_apply import engine
    off, ts = engine()
    off._native_bar_index = 0
    off._plant_bar_step(ts, [], {})
    assert off._decision_cfg() is off.config and off.real_plant_dcs.dispatcher.runtime_ceiling_cap is None


# ------------------------------------------------------- end to end (shared core, Engine A harness)

SYMS = ["HCLTECH", "INFY", "TCS", "WIPRO"]


def _run(**kwargs):
    from engine_c.data_contract import load_synthetic
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    data, nifty, vix, _ = kwargs.pop("feed")
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    engine = Revision2ExternalEngineOrchestrator(
        sorted(data), grid_context_provider=SealedGridContextProvider(nifty, vix), real_plant_dcs=plant,
        plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="full",
        governor_authority="full", starting_equity=1_000_000.0, **kwargs)
    return engine.run({s: data[s] for s in sorted(data)}, warmup=60)


@pytest.fixture(scope="module")
def feed():
    from engine_c.data_contract import load_synthetic
    return load_synthetic(SYMS, 900, 60)


def test_consume_mode_changes_real_orders_and_is_causal(feed):
    data, nifty, vix, info = feed
    off = _run(feed=(data, nifty, vix, info))
    on = _run(feed=(data, nifty, vix, info), dynamic_profile_mode="consume")
    signature = lambda r: [(t["symbol"], str(t["entry_timestamp"]), t["quantity"], t["reason"]) for t in r["trades"]]
    funnel = lambda r: (r["confidence_qualified"], r["id_approvals"], r["mpc_plans"])
    assert signature(on) != signature(off) or funnel(on) != funnel(off)
    assert any(e["event_type"] == "RUNTIME_PROFILE_APPLIED" for e in on["controller_telemetry"])
    cut = sorted(data["INFY"]["timestamp"])[540]
    horizon = str(pd.Timestamp(cut) - pd.Timedelta(minutes=1))

    def perturb(frame, cols):
        frame = frame.copy()
        mask = pd.to_datetime(frame["timestamp"]) >= pd.Timestamp(cut)
        for col in cols:
            frame.loc[mask, col] = frame.loc[mask, col] * 1.07
        return frame
    later = _run(feed=({s: perturb(f, ("open", "high", "low", "close")) for s, f in data.items()},
                       perturb(nifty, ("close",)), perturb(vix, ("close",)), info), dynamic_profile_mode="consume")
    before = lambda r: [e for e in r["controller_telemetry"] if e["timestamp"] < horizon]
    assert before(on) and before(on) == before(later)


# ------------------------------------------------------- checkpoint / resume (journal replay)

def test_process_restart_matches_uninterrupted_with_consume_mode(tmp_path):
    def run(path, mode):
        return subprocess.run([sys.executable, "-m", "tests.test_r5_dynamization", str(path), mode],
                              capture_output=True, text=True, timeout=120)
    baseline, recovered = tmp_path / "base.sqlite", tmp_path / "recover.sqlite"
    assert run(baseline, "none").returncode == 0
    stopped = run(recovered, "checkpoint")
    assert stopped.returncode == 73, stopped.stderr
    with sqlite3.connect(recovered) as con:
        rows = con.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    assert rows
    resumed = run(recovered, "none")
    assert resumed.returncode == 0, resumed.stderr
    original = json.loads(Path(str(baseline) + ".result.json").read_text())
    actual = json.loads(Path(str(recovered) + ".result.json").read_text())
    assert actual["state"] == original["state"]
    assert actual["reconciled"] == rows
    assert original["dynamic_profile_mode"] == actual["dynamic_profile_mode"] == "consume"


if __name__ == "__main__":
    import tests.test_r5_native_durability as durability
    built = []
    durability.engine = lambda: built.append(_consume_engine()) or built[-1]
    durability.fixture_run(Path(sys.argv[1]), sys.argv[2])
    out = Path(sys.argv[1] + ".result.json")
    result = json.loads(out.read_text())
    result["dynamic_profile_mode"] = built[-1][0].dynamic_profile_mode
    out.write_text(json.dumps(result, default=str, sort_keys=True))
