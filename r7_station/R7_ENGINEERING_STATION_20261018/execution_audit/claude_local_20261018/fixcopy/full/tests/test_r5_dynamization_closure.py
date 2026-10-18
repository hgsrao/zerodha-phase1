"""WP-R5-DYNAMIZATION-CLOSURE proofs (shared core).

Every expectation below is computed independently from the documented formula; nothing calls the code
under test to produce its own expected value.

1. MVAR: optional signed per-unit MEASURED input, never a synthetic zero; limiter saturation, return to
   range, fail-closed when required.
2. Closed-loop entry-derate / persistence / gain-scale and studies-weight envelopes are registry-owned.
3. gov_<bay>_z_exit reaches the shared governor exit decision (sign, units, protection / min-hold order).
4. The seven formerly unreachable gsch / tier3 clips bind under admissible bounds.
5. Dispatcher floor projection (no capital creation) and the stress-tightening order timeout.
"""
from __future__ import annotations

import math
from datetime import datetime
from itertools import product

import numpy as np
import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig
from revision2_external.bb05_bb06_parameters import default_config
from revision2_external.closed_loop_control import CausalOutcomeLedger, ClosedLoopSupervisor, SymbolDynamicsProfiler
from revision2_external.composite_study_signal import STUDY_NAMES, CompositeStudySignal
from revision2_external.dynamic_parameter_controller import DynamicParameterController
from revision5.ccpp_protection_cubicles import BayExcitationAVR
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.controller_parameters import (
    BAY_TOKENS, CONTROLLER_PARAMETERS, ENVELOPE_PARAMETER_NAMES, STUDY_COUNT, ControllerParameterError,
    R5ControllerParameters, controller_parameter_defaults)
from revision5.dynamic_parameters import R5EnvironmentState, Revision5DynamicParameterController
from revision5.engine_state import ENGINE_B
from revision5.governor import BayTurbineClosedLoopGovernor
from revision5.governor_authority import GovernorAuthorityConfig, position_decision
from revision5.plant_control import PlantControlError, SectorDispatchController, project_dispatch_floor
from revision5.topology import BAY_IDS

REG = CanonicalParameterRegistry()
D = controller_parameter_defaults()
clip = lambda x, lo, hi: max(lo, min(hi, x))
BAY = "GTG1_HEAVY_INDUSTRY"


def params_with(**changes) -> R5ControllerParameters:
    values = controller_parameter_defaults()
    values.update(changes)
    return R5ControllerParameters.from_values(values)


def cfg(**overrides):
    values = {n: s.default for n, s in REG.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=REG.FROZEN_IDENTITY_SHA256)


def neutral(**kw):
    """stress 0, risk_derate 1, vol 1, no error: every schedule at its neutral argument."""
    return R5EnvironmentState(normalized_vol=1.0, trend_probability=1.0, chop_probability=0.0,
                              turbulent_probability=0.0, fleet_drawdown_fraction=0.0, control_error_delta=0.0, **kw)


# =========================================================================================== 1. MVAR

def _avr(**changes):
    return BayExcitationAVR(BAY, 100_000.0, parameters=params_with(**changes).avr)


def _drive(avr, command, terminal, mvar):
    avr.excitation_command = command
    avr.avr_integral_error = 0.0
    avr.avr_last_error = 0.0
    return avr.evaluate_control(bus_voltage_reference_pu=1.0, terminal_voltage_pu=terminal, measured_mvar_pu=mvar)


def test_missing_mvar_is_not_a_measured_zero_and_limiter_is_not_evaluated():
    avr = _avr()
    unmeasured = _drive(avr, 0.5, 0.99, None)
    zero = _drive(_avr(), 0.5, 0.99, 0.0)
    assert unmeasured["mvar_limiter"] == "NOT_EVALUATED" and unmeasured["measured_mvar_pu"] is None
    assert zero["mvar_limiter"] == "IN_RANGE" and zero["measured_mvar_pu"] == 0.0     # a measured zero is a value
    # error 0.01, Kp .8, Ki .15 (integral = error), Kd .1 (derivative = error): delta_u = 0.0105
    assert unmeasured["excitation_command"] == pytest.approx(0.5 + 0.0105)
    assert zero["excitation_command"] == pytest.approx(0.5 + 0.0105)
    with pytest.raises(ValueError, match="requires a measured MVAR"):
        avr.evaluate_control(mode="MVAR", mvar_reference_pu=0.2, measured_mvar_pu=None)
    for bad in (math.nan, math.inf):
        with pytest.raises(ValueError, match="finite"):
            avr.evaluate_control(measured_mvar_pu=bad)


@pytest.mark.parametrize("sign", [+1.0, -1.0])
def test_mvar_limiter_holds_the_command_at_saturation_in_the_driving_direction_and_releases_in_range(sign):
    limit = D["avr_mvar_limit_base_pu"]
    # +: positive error raises the command; -: negative error lowers it.  delta_u = 0.0105 * sign.
    terminal = 1.0 - 0.01 * sign
    avr = _avr()
    held = _drive(avr, 0.5, terminal, sign * (limit + 0.05))
    assert held["mvar_limiter"] == ("POSITIVE_SATURATED" if sign > 0 else "NEGATIVE_SATURATED")
    assert held["reason"] == ("OEL_ACTIVE" if sign > 0 else "UEL_ACTIVE")
    assert held["excitation_command"] == 0.5 and held["delta_u"] == pytest.approx(0.0105 * sign)
    at_limit = _drive(avr, 0.5, terminal, sign * limit)                                # |Q| == limit is in range
    assert at_limit["mvar_limiter"] == "IN_RANGE" and at_limit["excitation_command"] == pytest.approx(0.5 + 0.0105 * sign)
    back = _drive(avr, 0.5, terminal, sign * (limit - 0.3))
    assert back["mvar_limiter"] == "IN_RANGE" and back["excitation_command"] == pytest.approx(0.5 + 0.0105 * sign)
    # the limiter never blocks the opposite correction
    other = _drive(avr, 0.5, 1.0 + 0.01 * sign, sign * (limit + 0.05))
    assert other["excitation_command"] == pytest.approx(0.5 - 0.0105 * sign)


def test_mvar_limit_parameters_move_the_limiter_and_the_stress_schedule_follows_its_formula():
    wide, narrow = _avr(avr_mvar_limit_base_pu=1.5), _avr(avr_mvar_limit_base_pu=0.5, avr_mvar_limit_min_pu=0.3)
    assert _drive(wide, 0.5, 0.99, 0.8)["mvar_limiter"] == "IN_RANGE"
    assert _drive(narrow, 0.5, 0.99, 0.8)["mvar_limiter"] == "POSITIVE_SATURATED"
    for name in ("avr_mvar_limit_base_pu", "avr_mvar_limit_stress_gain", "avr_mvar_limit_min_pu"):
        assert CONTROLLER_PARAMETERS[name].safety_class == "FIXED_SAFETY"
    env = R5EnvironmentState(normalized_vol=3.1, trend_probability=0.1, chop_probability=0.2, turbulent_probability=0.7,
                             fleet_drawdown_fraction=0.3)
    stress = clip(D["dyn_stress_turbulent_weight"] * 0.7 + D["dyn_stress_chop_weight"] * 0.2
                  + D["dyn_stress_excess_vol_weight"] * (2.5 - 1.0), 0.0, 1.0)
    rd = clip(1 - D["dyn_risk_derate_stress_gain"] * stress - D["dyn_risk_derate_drawdown_gain"] * 0.3,
              D["dyn_risk_derate_lower_bound"], 1.0)
    loading = clip(D["avr_loading_fraction_base"] * rd, D["avr_loading_fraction_min"], D["avr_loading_fraction_base"])
    control_stress = clip(1.0 - loading / D["avr_loading_fraction_base"], 0.0, 1.0)
    expected = clip(D["avr_mvar_limit_base_pu"] - D["avr_mvar_limit_stress_gain"] * control_stress,
                    D["avr_mvar_limit_min_pu"], D["avr_mvar_limit_base_pu"])
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    plant.step_bar(current_dt=datetime(2024, 1, 2, 10, 0), bar_index=0, environment=env)
    assert expected < D["avr_mvar_limit_base_pu"]
    assert plant.bays[BAY].avr.mvar_limit_pu == pytest.approx(expected, rel=1e-12)
    # and a measurement between the stressed and the neutral limit now saturates the limiter
    avr = plant.bays[BAY].avr
    avr.excitation_command = 0.5
    between = 0.5 * (expected + D["avr_mvar_limit_base_pu"])
    assert avr.evaluate_control(terminal_voltage_pu=0.99, measured_mvar_pu=between)["mvar_limiter"] == "POSITIVE_SATURATED"


def _measured_plant():
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    plant.apply_controller_parameters(R5ControllerParameters.defaults(), avr_voltage_mode="measured")
    return plant


def _regulate(plant, bar, bus, mvar=None, mode="required"):
    plant.begin_bar(datetime(2024, 1, 2, 10, bar), bar)
    return plant.regulate_bay_voltages(bar_index=bar, bus_voltage_pu=bus, measured_mvar_pu=mvar, mvar_limiter_mode=mode)


def test_supplied_measurement_reaches_the_per_bar_avr_saturates_and_returns_to_range():
    limit = D["avr_mvar_limit_base_pu"]
    plant = _measured_plant()
    lowered = _regulate(plant, 0, 1.04, {b: 0.0 for b in BAY_IDS})[BAY]                # a measured ZERO is accepted
    assert lowered["measured_mvar_pu"] == 0.0 and lowered["mvar_limiter"] == "IN_RANGE"
    assert lowered["excitation_command"] < 1.0
    # positive saturation: bus below the reference asks for MORE excitation, the limiter holds it
    held = _regulate(plant, 1, 0.99, {b: limit + 0.4 for b in BAY_IDS})[BAY]
    assert held["measured_mvar_pu"] == limit + 0.4 and held["mvar_limiter"] == "POSITIVE_SATURATED"
    assert held["delta_u"] > 0.0 and held["command_after_limits"] == held["command_before"]
    released = _regulate(plant, 2, 0.99, {b: 0.2 for b in BAY_IDS})[BAY]               # back in range: drive resumes
    assert released["mvar_limiter"] == "IN_RANGE" and released["command_after_limits"] > released["command_before"]
    # negative saturation: bus above the reference asks for LESS excitation, the limiter holds it
    held_neg = _regulate(plant, 3, 1.03, {b: -(limit + 0.4) for b in BAY_IDS})[BAY]
    assert held_neg["mvar_limiter"] == "NEGATIVE_SATURATED" and held_neg["delta_u"] < 0.0
    assert held_neg["command_after_limits"] == held_neg["command_before"]
    back = _regulate(plant, 4, 1.03, {b: -0.2 for b in BAY_IDS})[BAY]
    assert back["mvar_limiter"] == "IN_RANGE" and back["command_after_limits"] < back["command_before"]
    # per-bay: another bay's measurement does not leak
    mixed = _regulate(plant, 5, 0.99, {BAY: limit + 0.4, "GTG2_TECH_TELECOM": 0.0})
    assert mixed[BAY]["mvar_limiter"] == "POSITIVE_SATURATED" and mixed["GTG2_TECH_TELECOM"]["mvar_limiter"] == "IN_RANGE"


def test_required_limiter_fails_closed_on_missing_or_invalid_measurement_and_entry_follows():
    plant = _measured_plant()
    before = plant.bays[BAY].avr.excitation_command
    out = _regulate(plant, 0, 1.0, {b: 0.1 for b in BAY_IDS if b != BAY})                # one bay unmeasured
    assert out[BAY]["tripped"] and out[BAY]["reason"] == "AVR_MVAR_MEASUREMENT_MISSING"
    assert plant.bays[BAY].avr.excitation_command == before                               # no PID update
    assert not out["GTG2_TECH_TELECOM"]["tripped"]
    for bar, value in ((1, math.nan), (2, math.inf), (3, "x"), (4, True)):
        record = _regulate(plant, bar, 1.0, {b: value for b in BAY_IDS})[BAY]
        assert record["tripped"] and record["reason"] == "AVR_MVAR_MEASUREMENT_INVALID"
    assert _regulate(plant, 5, 1.0, None)[BAY]["reason"] == "AVR_MVAR_MEASUREMENT_MISSING"
    kwargs = dict(engine_mode=ENGINE_B, symbol="INFY", z_score=-3.0, price=1500.0, atr=7.5, bid=1499.6, ask=1500.4,
                  tick_age_s=0.0, bar_range=6.0, side="BUY", entry_mode="trend_overspeed")
    plant.begin_bar(datetime(2024, 1, 2, 10, 6), 6)
    plant.regulate_bay_voltages(bar_index=6, bus_voltage_pu=1.0, measured_mvar_pu=None, mvar_limiter_mode="required")
    out = plant.evaluate_entry(bar_dt=datetime(2024, 1, 2, 10, 6), bar_index=6, **kwargs)
    assert not out["admitted"] and out["reason"] == "AVR_TRIP:AVR_MVAR_MEASUREMENT_MISSING"
    # invalid bus voltage still outranks, and off-mode with no measurement is the unmeasured (no-limiter) path
    off = _regulate(plant, 7, 1.0, None, mode="off")[BAY]
    assert not off["tripped"] and off["mvar_limiter"] == "NOT_EVALUATED" and off["measured_mvar_pu"] is None
    with pytest.raises(ValueError, match="unknown bays"):
        _regulate(plant, 8, 1.0, {"NOPE": 0.0}, mode="off")
    with pytest.raises(ValueError, match="mvar_limiter_mode"):
        _regulate(plant, 9, 1.0, None, mode="bogus")


def _engine(**kwargs):
    from tests.test_r5_paper_apply import engine as base_engine
    import tests.test_r5_paper_apply as pa_mod
    original = pa_mod.Engine
    pa_mod.Engine = lambda *a, **k: original(*a, **kwargs, **k)
    try:
        return base_engine()
    finally:
        pa_mod.Engine = original


def test_orchestrator_mvar_contract_requires_a_source_and_propagates_the_supplied_value():
    from tests.test_r5_paper_apply import engine
    with pytest.raises(ValueError, match="requires avr_voltage_mode='measured'"):
        _engine(avr_mvar_limiter_mode="required", mvar_measurement_provider=lambda *a: 0.0)
    with pytest.raises(ValueError, match="requires a callable mvar_measurement_provider"):
        _engine(avr_voltage_mode="measured", avr_mvar_limiter_mode="required")
    with pytest.raises(ValueError, match="avr_mvar_limiter_mode must be one of"):
        _engine(avr_mvar_limiter_mode="on")
    with pytest.raises(ValueError, match="requires avr_voltage_mode='measured'"):
        _engine(mvar_measurement_provider=lambda *a: 0.0)
    calls = []
    values = {"v": D["avr_mvar_limit_base_pu"] + 0.5}

    def provider(timestamp, bar_index, bay_id):
        calls.append((timestamp, bar_index, bay_id))
        return values["v"]
    o, ts = _engine(avr_voltage_mode="measured", avr_mvar_limiter_mode="required", mvar_measurement_provider=provider)
    # Keep the independent voltage channel valid while testing loss of MVAR.
    # The grid fixture otherwise ages out on the second bar and correctly trips
    # voltage validation first, masking the MVAR failure being asserted here.
    o._governor_grid_return = lambda timestamp: 0.0
    assert o.controller_parameter_startup_report()["modes"]["avr_mvar_limiter_mode"] == "required"
    o._native_bar_index = 0
    o._plant_bar_step(ts, [], {})
    assert {c[2] for c in calls} == set(BAY_IDS) and all(c[0] == ts and c[1] == 0 for c in calls)
    records = {b: bay.avr_regulation for b, bay in o.real_plant_dcs.bays.items()}
    assert all(r["measured_mvar_pu"] == values["v"] and r["mvar_limiter"] == "POSITIVE_SATURATED" for r in records.values())
    values["v"] = None                                                                   # source goes away: fail closed
    o._native_bar_index = 1
    o._plant_bar_step(ts + pd.Timedelta(minutes=1), [], {})
    assert all(bay.avr_regulation["reason"] == "AVR_MVAR_MEASUREMENT_MISSING" for bay in o.real_plant_dcs.bays.values())
    # default engine: no provider, nothing measured, no MVAR limiter anywhere
    default, _ = engine()
    assert default.avr_mvar_limiter_mode == "off" and default.mvar_measurement_provider is None


# ======================================================================== 2. envelopes (registry owned)

ENVELOPE_DEFAULTS = {"cl_entry_derate_min": 0.5, "cl_entry_derate_max": 1.0, "cl_persistence_min": 0.05,
                     "cl_persistence_max": 0.99, "cl_gain_scale_min": 0.5, "cl_gain_scale_max": 1.5,
                     "studies_weight_min": 0.05, "studies_weight_max": 0.60}


def test_envelope_rows_are_registered_with_the_former_literal_defaults():
    assert set(ENVELOPE_PARAMETER_NAMES) == set(ENVELOPE_DEFAULTS)
    for name, default in ENVELOPE_DEFAULTS.items():
        assert REG.params[name].default == default and CONTROLLER_PARAMETERS[name].default == default
    from revision2_external import closed_loop_control as cl, composite_study_signal as css
    assert (cl._ENTRY_DERATE_BOUNDS, cl._PERSISTENCE_BOUNDS, cl._GAIN_SCALE_BOUNDS) == (
        (0.5, 1.0), (0.05, 0.99), (0.5, 1.5))
    assert (css.MIN_WEIGHT, css.MAX_WEIGHT) == (0.05, 0.60)                              # registry-default view only
    assert STUDY_COUNT == len(STUDY_NAMES)


def _ledger(outcomes, **changes):
    ledger = CausalOutcomeLedger(config=cfg(**changes))
    for pnl in outcomes:
        ledger.record({"symbol": "A", "side": "BUY", "regime": "unknown", "net_pnl": pnl})
    return ledger.profile("A", "BUY")


@pytest.mark.parametrize("lo, hi", [(0.5, 1.0), (0.7, 0.85), (0.9, 0.95), (0.3, 1.0)])
def test_entry_derate_envelope_binds_at_both_ends(lo, hi):
    losses, wins = [-1.0] * 30, [1.0] * 30
    minimum_history = 20                                                                  # registry cl_outcome_min_history
    pooled = lambda n_win, n: (n_win + 0.5 * minimum_history) / (n + minimum_history)
    local_weight = 30 / (30 + minimum_history)
    for outcomes, n_win in ((losses, 0), (wins, 30)):
        win_rate = (n_win / 30) if outcomes else 0.5
        expected_pooled = local_weight * win_rate + (1 - local_weight) * pooled(n_win, 30)
        expected = clip(0.5 + expected_pooled, lo, hi)
        got = _ledger(outcomes, cl_entry_derate_min=lo, cl_entry_derate_max=hi)["suggested_entry_derate"]
        assert got == pytest.approx(expected, rel=1e-12)
    assert _ledger(wins, cl_entry_derate_min=lo, cl_entry_derate_max=hi)["suggested_entry_derate"] <= hi
    assert _ledger(losses, cl_entry_derate_min=lo, cl_entry_derate_max=hi)["suggested_entry_derate"] >= lo


def test_entry_derate_envelope_reaches_the_supervisor_and_is_never_above_one():
    supervisor = ClosedLoopSupervisor(config=cfg(cl_entry_derate_min=0.8, cl_entry_derate_max=0.9))
    for _ in range(30):
        supervisor.outcomes.record({"symbol": "A", "side": "BUY", "regime": "unknown", "net_pnl": -1.0})
    snapshot = supervisor.entry_snapshot("A", "BUY", 100.0, 99.0, 102.0, 30)
    assert snapshot["entry_quality"]["suggested_entry_derate"] == 0.8
    supervisor.configure(cfg(cl_entry_derate_min=0.95, cl_entry_derate_max=0.95))        # refresh keeps the ledger
    assert supervisor.entry_snapshot("A", "BUY", 100.0, 99.0, 102.0, 30)["entry_quality"]["suggested_entry_derate"] == 0.95
    for bad in ({"cl_entry_derate_min": 0.9, "cl_entry_derate_max": 0.6}, {"cl_entry_derate_max": 1.2},
                {"cl_entry_derate_min": 0.0}):
        with pytest.raises(ValueError):
            CausalOutcomeLedger(config=cfg(**bad))


def _estimate_independent(closes, persistence_bounds, gain_bounds, rt_min=5.0, rt_max=45.0, rt_default=20.0, span=20, gamma=2.0):
    alpha = 2.0 / (span + 1.0)
    ema, deviations = closes[0], []
    for c in closes:
        ema = alpha * c + (1 - alpha) * ema
        deviations.append(c - ema)
    prev, cur = deviations[:-1], deviations[1:]
    denom = sum(v * v for v in prev)
    persistence = sum(a * b for a, b in zip(prev, cur)) / denom
    phi = clip(persistence, *persistence_bounds)
    base = clip(math.log(0.5) / math.log(phi), rt_min, rt_max)
    path = sum(abs(b - a) for a, b in zip(closes[:-1], closes[1:]))
    damping = clip(1.0 - abs(closes[-1] - closes[0]) / path, 0.0, 1.0)
    rt = clip(base * (1.0 + gamma * damping), rt_min, rt_max)
    return persistence, rt, clip(rt_default / rt, *gain_bounds)


def _closes(kind):
    k = np.arange(60)
    if kind == "persistent":         # slow oscillation: deviation persistence close to 1
        return list(1000.0 + 10.0 * np.sin(k / 9.0))
    return list(1000.0 + 3.0 * (-1.0) ** k)               # alternating: persistence strongly negative


@pytest.mark.parametrize("kind, bounds", [
    ("persistent", (0.05, 0.99)), ("persistent", (0.05, 0.90)), ("persistent", (0.05, 0.95)),
    ("alternating", (0.05, 0.99)), ("alternating", (0.90, 0.99)), ("alternating", (0.95, 0.999))])
def test_persistence_envelope_changes_the_response_time_exactly_as_the_formula(kind, bounds):
    closes = _closes(kind)
    bars = pd.DataFrame({"close": closes})
    persistence, rt, _ = _estimate_independent(closes, bounds, (0.5, 1.5))
    profiler = SymbolDynamicsProfiler(config=cfg(cl_persistence_min=bounds[0], cl_persistence_max=bounds[1]))
    got = profiler.estimate(bars)
    assert got["deviation_persistence"] == pytest.approx(persistence)
    assert got["response_time_bars"] == pytest.approx(rt, rel=1e-12)


def test_persistence_clip_is_observable_once_it_leaves_the_response_time_clip_shadow():
    for kind, a, b in (("persistent", (0.05, 0.99), (0.05, 0.90)), ("alternating", (0.05, 0.99), (0.95, 0.999))):
        bars = pd.DataFrame({"close": _closes(kind)})
        base = SymbolDynamicsProfiler(config=cfg(cl_persistence_min=a[0], cl_persistence_max=a[1])).estimate(bars)
        moved = SymbolDynamicsProfiler(config=cfg(cl_persistence_min=b[0], cl_persistence_max=b[1])).estimate(bars)
        assert moved["response_time_bars"] != base["response_time_bars"], kind
    with pytest.raises(ValueError):
        SymbolDynamicsProfiler(config=cfg(cl_persistence_min=0.9, cl_persistence_max=0.8))


@pytest.mark.parametrize("bounds", [(0.5, 1.5), (0.9, 1.1), (0.3, 3.0), (1.0, 1.0)])
def test_gain_scale_envelope_bounds_the_advisory_suggestion(bounds):
    for kind in ("persistent", "alternating"):
        closes = _closes(kind)
        _, _, expected = _estimate_independent(closes, (0.05, 0.99), bounds)
        got = SymbolDynamicsProfiler(config=cfg(cl_gain_scale_min=bounds[0], cl_gain_scale_max=bounds[1])).estimate(
            pd.DataFrame({"close": closes}))["suggested_pid_gain_scale"]
        assert got == pytest.approx(expected, rel=1e-12) and bounds[0] <= got <= bounds[1]
    assert CONTROLLER_PARAMETERS["cl_gain_scale_min"].safety_class == "ADVISORY"        # feeds no actuator


def _study_bars(n=160, seed=3):
    rng = np.random.default_rng(seed)
    close = 1000.0 * np.exp(np.cumsum(0.0006 + 0.004 * rng.standard_normal(n)))
    ts = pd.date_range("2024-03-01 09:15", periods=n, freq="min")
    return pd.DataFrame({"timestamp": ts, "open": close * 0.999, "high": close * 1.002, "low": close * 0.998,
                         "close": close, "volume": 1000.0 + 50 * rng.random(n)})


@pytest.mark.parametrize("lo, hi", [(0.05, 0.60), (0.20, 0.30), (0.24, 0.26), (0.25, 0.25), (0.01, 0.25)])
def test_studies_weight_envelope_bounds_every_weight_and_binds(lo, hi):
    bars, engine = _study_bars(), CompositeStudySignal(config=cfg(studies_weight_min=lo, studies_weight_max=hi))
    seen = set()
    for i in range(80, len(bars)):
        weights = engine.evaluate("S", bars.iloc[:i + 1])["weights"]
        assert all(lo - 1e-12 <= w <= hi + 1e-12 for w in weights.values())
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)
        seen |= {round(w, 12) for w in weights.values()}
    if (lo, hi) != (0.05, 0.60):
        assert round(lo, 12) in seen or round(hi, 12) in seen                            # the envelope actually bound
    assert engine.weight_min == lo and engine.weight_max == hi


def test_studies_weight_envelope_validates_feasibility_of_a_unit_sum():
    with pytest.raises(ValueError):                                                      # registry bound: min <= 1/n
        CompositeStudySignal(config=cfg(studies_weight_min=0.3))
    with pytest.raises(ValueError):                                                      # registry bound: max >= 1/n
        CompositeStudySignal(config=cfg(studies_weight_max=0.2))
    with pytest.raises(ControllerParameterError):
        params_with(studies_weight_min=0.26)
    with pytest.raises(ControllerParameterError):
        params_with(cl_entry_derate_min=0.9, cl_entry_derate_max=0.6)
    with pytest.raises(ControllerParameterError):
        params_with(cl_persistence_min=0.9, cl_persistence_max=0.8)
    with pytest.raises(ControllerParameterError):
        params_with(cl_gain_scale_min=1.0 + 0.0, cl_gain_scale_max=1.0, tier3_kp_scale_min=3.0, tier3_kp_scale_max=2.0)
    # Effective study shares must sum to one within the declared bounds.
    engine = CompositeStudySignal(config=cfg(studies_weight_min=0.2, studies_weight_max=0.3))
    result = engine.evaluate("S", _study_bars().iloc[:120])
    assert sum(result["weights"].values()) == pytest.approx(1.0, abs=1e-12)


# ================================================================================ 3. z-exit (shared exit)

CFG = GovernorAuthorityConfig.from_config(default_config())
BASE = dict(position_id="p", measured_r=0.1, reference_r=0.1, max_favorable_r=0.2, elapsed_bars=6, min_hold_bars=2,
            max_hold_bars=60, trade_target_r=3.0, conviction=0.9, drawdown=0.0, velocity=0.1, session_bar=200,
            path_noise_r=0.3, hard_stop_r=-1.0)


def _governor(bay, **changes):
    values = controller_parameter_defaults()
    values.update(changes)
    return BayTurbineClosedLoopGovernor(R5ControllerParameters.from_values(values).governor_specs[bay])


def _decide(governor, side="BUY", z=None, entry=None, **kw):
    return position_decision(governor, CFG, **{**BASE, **kw}, side=side, z_score=z, entry_signed_z=entry)


@pytest.mark.parametrize("bay", BAY_IDS)
def test_each_bay_z_exit_threshold_changes_the_exit_decision(bay):
    token = BAY_TOKENS[bay]
    name = f"gov_{token}_z_exit"
    default = CONTROLLER_PARAMETERS[name].default
    governor = _governor(bay)
    assert _decide(governor).get("reason") == "GOVERNOR_TRACKING"                        # rule unevaluated -> baseline HOLD
    just_inside, just_outside = -default + 0.01, -default - 0.01                          # sigma units, BUY-signed
    hit = _decide(_governor(bay), z=just_inside, entry=-1.5)
    assert (hit["action"], hit["reason"]) == ("EXIT", "GOVERNOR_Z_EXIT")
    assert hit["z_exit"]["release_level"] == pytest.approx(-default) and hit["z_exit"]["armed"]
    miss = _decide(_governor(bay), z=just_outside, entry=-1.5)
    assert (miss["action"], miss["reason"]) == ("HOLD", "GOVERNOR_TRACKING") and not miss["z_exit"]["release"]
    # the bay's own registry value moves the decision at a fixed measurement
    for raised, lowered in ((default + 0.05, max(0.0, default - 0.05)),):
        assert _decide(_governor(bay, **{name: raised}), z=just_outside, entry=-1.5)["reason"] == "GOVERNOR_Z_EXIT"
        assert _decide(_governor(bay, **{name: lowered}), z=just_inside, entry=-1.5)["reason"] == "GOVERNOR_TRACKING"
    # SELL is the mirror image (side-signed z)
    sell = _decide(_governor(bay), side="SELL", z=-just_inside, entry=-1.5)
    assert sell["reason"] == "GOVERNOR_Z_EXIT"
    assert _decide(_governor(bay), side="SELL", z=-just_outside, entry=-1.5)["reason"] == "GOVERNOR_TRACKING"
    # other bays' thresholds do not contaminate this bay
    others = {f"gov_{BAY_TOKENS[b]}_z_exit": 2.0 for b in BAY_IDS if b != bay}
    assert _decide(_governor(bay, **others), z=just_outside, entry=-1.5)["reason"] == "GOVERNOR_TRACKING"


def test_z_exit_needs_a_prior_crossing_and_respects_the_protection_and_min_hold_hierarchy():
    g = lambda: _governor(BAY)
    assert _decide(g(), z=0.5, entry=-0.1)["reason"] == "GOVERNOR_TRACKING"              # entered above the level: not armed
    assert _decide(g(), z=0.5, entry=0.8)["reason"] == "GOVERNOR_TRACKING"               # momentum entry never z-exits
    armed = dict(z=-0.1, entry=-1.9)
    assert _decide(g(), **armed)["reason"] == "GOVERNOR_Z_EXIT"
    # minimum hold defers the discretionary release (reported), the hold window ends -> exit
    deferred = _decide(g(), elapsed_bars=1, **armed)
    assert (deferred["action"], deferred["z_exit_deferred"]) == ("HOLD", True)
    assert _decide(g(), elapsed_bars=2, **armed)["reason"] == "GOVERNOR_Z_EXIT"
    # inner-loop hard exits outrank the z release, even inside min hold
    assert _decide(g(), measured_r=3.2, **armed)["reason"] == "GOVERNOR_TARGET_REACHED"
    assert _decide(g(), measured_r=-1.1, **armed)["reason"] == "GOVERNOR_HARD_STOP"
    assert _decide(g(), elapsed_bars=60, **armed)["reason"] == "GOVERNOR_MAX_HOLD"
    # exhaust-spread trip and the Mark V limiters outrank it
    assert _decide(g(), bay_exhaust_spread=CFG.exhaust_spread_trip + 0.1, **armed)["reason"] == "EXHAUST_SPREAD_TRIP"
    below = _decide(g(), drawdown=1.0, **armed)
    assert below["reason"].startswith("FSR_BELOW_EXIT")
    # invalid / non-finite inputs fail closed
    assert _decide(g(), z=math.nan, entry=-1.9)["action"] == "EXIT"
    assert _decide(g(), z=-0.1, entry=math.inf)["reason"].startswith("INVALID_GOVERNOR_INPUT")
    # position state is per position id: the same governor, two positions, independent decisions
    shared = g()
    a = _decide(shared, position_id="a", z=-0.1, entry=-1.9)
    b = _decide(shared, position_id="b", z=-0.1, entry=-0.05)
    assert (a["reason"], b["reason"]) == ("GOVERNOR_Z_EXIT", "GOVERNOR_TRACKING")


def test_z_exit_measurement_is_causal_and_per_symbol():
    from revision5.governor_authority import bar_telemetry
    bars = _study_bars(120)
    base = bar_telemetry(bars.iloc[:100], CFG)
    future = bars.copy()
    future.loc[100:, ["open", "high", "low", "close"]] *= 1.5                             # the future changes ...
    assert bar_telemetry(future.iloc[:100], CFG).z_score == base.z_score                  # ... the measurement does not
    other = bars.copy()
    other["close"] = other["close"] * 0.7                                                 # another symbol's prices
    assert bar_telemetry(bars.iloc[:100], CFG).z_score == base.z_score
    assert bar_telemetry(other.iloc[:100], CFG).z_score == pytest.approx(base.z_score)    # scale-free, own-bars only


def test_z_exit_wiring_in_the_orchestrator_records_entry_z_and_passes_the_comparator_z():
    import inspect
    from revision2_external import orchestrator
    source = inspect.getsource(orchestrator.Revision2ExternalEngineOrchestrator)
    assert '"governor_entry_signed_z"' in source and "entry_signed_z=trade.get(\"governor_entry_signed_z\")" in source
    assert "z_score=telemetry.z_score if telemetry is not None and telemetry.available else None" in source


# ===================================================================== 4. the seven formerly unreachable clips

def _snapshot(env=None, **changes):
    controller = Revision5DynamicParameterController(params_with(**changes))
    return controller.evaluate(env or neutral())


def _bay(name):
    return controller_parameter_defaults()[f"gov_{BAY_TOKENS[BAY]}_{name}"]


CLIPS = [  # (parameter, admissible value, default, snapshot field, independent expected at the neutral corner)
    ("gsch_kp_scale_min", 1.5, 0.60, "kp", lambda v: _bay("kp") * clip(1.0, v, D["gsch_kp_scale_max"])),
    ("gsch_kd_scale_min", 1.4, 0.60, "kd", lambda v: _bay("kd") * clip(1.0, v, D["gsch_kd_scale_max"])),
    ("gsch_integral_max_scale", 0.8, 1.25, "integral_clamp",
     lambda v: clip(_bay("integral_clamp") / 1.0, _bay("integral_clamp") * D["gsch_integral_min_scale"], _bay("integral_clamp") * v)),
    ("gsch_droop_max_min_scale", 1.3, 0.80, "grid_droop_max",
     lambda v: clip(_bay("grid_droop_max") * 1.0, _bay("grid_droop_max") * v, _bay("grid_droop_max") * D["gsch_droop_max_max_scale"])),
    ("gsch_offset_min_upper_scale", 1.2, 0.75, "dynamic_offset_min",
     lambda v: clip(_bay("dynamic_offset_min") * 1.0, _bay("dynamic_offset_min") * D["gsch_offset_min_lower_scale"],
                    _bay("dynamic_offset_min") * v)),
]


@pytest.mark.parametrize("name, value, default, field, expected", CLIPS, ids=[c[0] for c in CLIPS])
def test_formerly_unreachable_gsch_clip_binds_at_the_neutral_corner_and_reaches_the_governor(name, value, default, field, expected):
    spec = CONTROLLER_PARAMETERS[name]
    assert spec.default == default and spec.safety_class == "TUNABLE" and spec.minimum <= value <= spec.maximum
    base = getattr(_snapshot().governor_by_bay[BAY], field)
    assert base == pytest.approx(expected(default), rel=1e-12)                            # default: clip is slack (numerics kept)
    moved = getattr(_snapshot(**{name: value}).governor_by_bay[BAY], field)
    assert moved == pytest.approx(expected(value), rel=1e-12) and moved != base           # admissible value: clip binds
    # runtime consumer: the bay governor's operating value follows the clipped schedule
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    plant.apply_controller_parameters(params_with(**{name: value}))
    plant.step_bar(current_dt=datetime(2024, 1, 2, 10, 0), bar_index=0, environment=neutral())
    assert getattr(plant.bays[BAY].governor, f"runtime_{field}") == pytest.approx(expected(value), rel=1e-12)


def test_gsch_clip_orderings_are_validated_and_slack_values_are_still_admissible():
    for changes in ({"gsch_kp_scale_min": 2.5, "gsch_kp_scale_max": 2.2}, {"gsch_kd_scale_min": 2.5, "gsch_kd_scale_max": 2.0},
                    {"gsch_droop_max_min_scale": 1.7, "gsch_droop_max_max_scale": 1.6},
                    {"gsch_integral_max_scale": 0.4, "gsch_integral_min_scale": 0.5},
                    {"gsch_offset_min_upper_scale": 1.6, "gsch_offset_min_lower_scale": 1.5}):
        with pytest.raises(ControllerParameterError):
            params_with(**changes)
    params_with(gsch_kp_scale_min=0.1, gsch_integral_max_scale=2.0, gsch_droop_max_min_scale=0.1)   # old region intact


def test_tier3_kp_kd_floor_binds_in_the_box6_gain_schedule_and_reaches_the_entry_pid():
    for name, index in (("tier3_kp_scale_min", 0), ("tier3_kd_scale_min", 2)):
        spec = CONTROLLER_PARAMETERS[name]
        assert spec.default == 0.5 and spec.safety_class == "FIXED_SAFETY" and spec.maximum >= 2.0
        default = DynamicParameterController.get_tier3_pid_schedule(1.0, 1.0, 1.0, 0.0, cfg())
        bound = DynamicParameterController.get_tier3_pid_schedule(1.0, 1.0, 1.0, 0.0, cfg(**{name: 1.6}))
        assert default[index] == 1.0 and bound[index] == pytest.approx(1.6)               # argument 1 + g*0 = 1 -> floor 1.6
        gain = REG.params["mpc_schedule_kp_gain" if index == 0 else "mpc_schedule_kd_gain"].default
        high = 2.4 if index == 0 else 1.9                                                      # just below the scale max
        assert DynamicParameterController.get_tier3_pid_schedule(1.0, 1.0, 1.0, 2.0, cfg(**{name: high}))[index] == pytest.approx(
            clip(1.0 + gain * 2.0, high, D["tier3_kp_scale_max" if index == 0 else "tier3_kd_scale_max"]))
    with pytest.raises(ControllerParameterError):
        params_with(tier3_kp_scale_min=3.0, tier3_kp_scale_max=2.5)
    # consumer: the Box 6 entry PID tunings of the first plan carry the floor
    from tests.test_r5_dynamization import _consume_engine  # noqa: F401  (engine harness)
    from tests_external.test_audit_remediation import signal
    from revision2.contracts import IDDecision
    from tests.test_r5_parameter_authority import registry_with
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
    times = pd.date_range("2024-03-01 09:15", periods=100, freq="15min", tz="Asia/Kolkata")
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": times, "close": 21000.0}),
                                         pd.DataFrame({"timestamp": times, "close": 15.0}))
    o = Engine(["INFY"], registry=registry_with(tier3_kp_scale_min=1.6, tier3_kd_scale_min=1.7),
               real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
               plant_control_mode="PAPER_APPLY", grid_context_provider=provider)
    o.mpc.build_plan(signal(), IDDecision(True, "x", 0.6, 2.0, 0.6), 1000.0, 5.0, o.config)
    base = [float(o.config.require(n)) for n in ("pid_kp_entry", "pid_ki_entry", "pid_kd_entry")]
    kp, ki, kd = o.mpc._entry_pids["INFY"].tunings
    assert kp == pytest.approx(base[0] * 1.6) and kd == pytest.approx(base[2] * 1.7) and ki == pytest.approx(base[1])


# ============================================================ 5. dispatcher floor projection + order timeout

def _ecs(demand, available=BAY_IDS):
    """The two ECS fields the sector dispatch reads."""
    return type("ECS", (), {"plant_demand_reference_pu": demand,
                            "bay_availability_mask": tuple((b, b in available) for b in BAY_IDS)})()


class _Merit:
    def __init__(self, weights, ceiling, floor=None):
        self.weights, self.max_ceiling, self.runtime_floor = weights, ceiling, floor
        self.dispatch_ceiling = ceiling

    @property
    def dispatch_floor(self):
        return 0.0 if self.runtime_floor is None else self.runtime_floor


W = {"GTG1_HEAVY_INDUSTRY": 0.60, "GTG2_TECH_TELECOM": 0.20, "CSTG1_BFSI": 0.10, "CSTG2_CONSUMER_AUTO": 0.07,
     "BPSTG_HEALTHCARE": 0.03}


def _refs(demand, ceiling, floor, available=BAY_IDS, weights=W):
    return SectorDispatchController(_Merit(weights, ceiling, floor)).dispatch(_ecs(demand, available))


def test_feasible_floor_lifts_low_merit_bays_conserves_the_budget_and_respects_the_ceiling():
    base = _refs(0.9, 0.35, None).as_dict()
    with_floor = _refs(0.9, 0.35, 0.10)
    got = with_floor.as_dict()
    assert sum(got.values()) == pytest.approx(0.9) and with_floor.feasible
    assert all(0.10 - 1e-12 <= v <= 0.35 + 1e-12 for v in got.values())
    assert base["BPSTG_HEALTHCARE"] < 0.10 <= got["BPSTG_HEALTHCARE"]                       # the floor actuates
    # independent water-filling: floor first, merit over the headroom, ceiling pinned
    expected, remaining, active = {b: 0.10 for b in BAY_IDS}, 0.9 - 0.5, list(BAY_IDS)
    while True:
        total = sum(W[b] for b in active)
        shares = {b: remaining * W[b] / total for b in active}
        over = [b for b in active if expected[b] + shares[b] > 0.35 + 1e-12]
        if not over:
            for b in active:
                expected[b] += shares[b]
            break
        for b in over:
            remaining -= 0.35 - expected[b]
            expected[b] = 0.35
        active = [b for b in active if b not in over]
    assert got == pytest.approx(expected, rel=1e-9)
    assert _refs(0.9, 0.35, 0.0).as_dict() == base                                           # floor 0 == baseline


def test_infeasible_floor_is_projected_with_documented_priority_and_never_creates_capital():
    assert project_dispatch_floor(0.10, 0.35, 0.3, 5) == pytest.approx(0.06)                # budget: demand / n
    assert project_dispatch_floor(0.10, 0.05, 0.9, 5) == 0.05                                # ceiling
    assert project_dispatch_floor(0.10, 0.35, 0.9, 0) == 0.0 and project_dispatch_floor(0.0, 0.35, 0.9, 5) == 0.0
    low_demand = _refs(0.3, 0.35, 0.10)
    assert sum(low_demand.as_dict().values()) == pytest.approx(0.3) and "DISPATCH_FLOOR_PROJECTED_BELOW_REQUEST" in low_demand.reasons
    assert all(v == pytest.approx(0.06) for v in low_demand.as_dict().values())
    low_ceiling = _refs(0.9, 0.05, 0.10)                                                     # 5 * 0.05 < demand: ceiling wins
    assert all(v == pytest.approx(0.05) for v in low_ceiling.as_dict().values()) and not low_ceiling.feasible
    assert low_ceiling.unallocated_pu == pytest.approx(0.9 - 0.25) and low_ceiling.allocated_pu <= 0.9
    tight = _refs(0.2, 0.05, 0.10)                                                           # budget (0.2 / 5) outranks the floor
    assert all(v == pytest.approx(0.04) for v in tight.as_dict().values()) and tight.allocated_pu == pytest.approx(0.2)
    partial = _refs(0.4, 0.35, 0.10, available=BAY_IDS[:3])                                   # unavailable bays stay at exactly 0
    values = partial.as_dict()
    assert values[BAY_IDS[3]] == values[BAY_IDS[4]] == 0.0 and sum(values.values()) == pytest.approx(0.4)
    assert all(v >= 0.10 - 1e-12 for b, v in values.items() if b in BAY_IDS[:3])
    for demand, ceiling, floor in product((0.0, 0.2, 0.6, 1.0), (0.15, 0.35, 1.0), (0.0, 0.05, 0.2)):
        result = _refs(demand, ceiling, floor)
        assert result.allocated_pu <= demand + 1e-12 and all(0.0 <= v <= ceiling + 1e-12 for v in result.as_dict().values())
    with pytest.raises(PlantControlError):
        _refs(0.5, 0.35, math.nan)


def test_psch_dispatch_floor_coefficients_each_move_the_runtime_floor_in_a_feasible_corner():
    def floor(env, **changes):
        return Revision5DynamicParameterController(params_with(**changes)).evaluate(env).plant.dispatcher_min_floor
    stressed = R5EnvironmentState(normalized_vol=1.0, trend_probability=0.0, chop_probability=0.0, turbulent_probability=1.0)
    stress = clip(D["dyn_stress_turbulent_weight"], 0.0, 1.0)
    base_floor = D["disp_allocation_floor"]
    expected = lambda gain, lo, hi: clip(base_floor * (1 - gain * stress), lo, hi)
    assert floor(stressed) == pytest.approx(expected(0.2, 0.05, 0.10))                       # default (slack) numerics preserved
    assert floor(neutral()) == pytest.approx(base_floor)
    assert floor(stressed, psch_dispatch_floor_stress_gain=0.9) == pytest.approx(expected(0.9, 0.05, 0.10))
    assert floor(stressed, psch_dispatch_floor_stress_gain=0.9, psch_dispatch_floor_min=0.06) == pytest.approx(0.06)   # min binds
    assert floor(neutral(), psch_dispatch_floor_max=0.07) == pytest.approx(0.07)                                         # max binds
    # ratio: ceiling driven below the floor (risk-derate floor 0.1, ceiling clip 0) -> floor = ratio * ceiling
    deep = R5EnvironmentState(normalized_vol=2.5, trend_probability=0.0, chop_probability=0.0, turbulent_probability=1.0,
                              fleet_drawdown_fraction=1.0)
    corner = dict(dyn_risk_derate_lower_bound=0.1, dyn_risk_derate_stress_gain=1.0, dyn_risk_derate_drawdown_gain=2.0,
                  psch_dispatch_ceiling_min=0.0, psch_dispatch_floor_min=0.0, psch_dispatch_floor_stress_gain=0.0)
    deep_stress = clip(D["dyn_stress_turbulent_weight"] + D["dyn_stress_excess_vol_weight"] * (2.5 - 1.0), 0.0, 1.0)
    rd = clip(1 - 1.0 * deep_stress - 2.0 * 1.0, 0.1, 1.0)
    ceiling = clip(D["disp_allocation_ceiling"] * rd, 0.0, D["disp_allocation_ceiling"])
    assert ceiling <= base_floor                                                              # floor >= ceiling: ratio acts
    for ratio in (0.5, 0.25, 0.9):
        assert floor(deep, psch_dispatch_floor_ceiling_ratio=ratio, **corner) == pytest.approx(min(base_floor, ratio * ceiling))
    snapshot = Revision5DynamicParameterController(params_with(**corner)).evaluate(deep)
    assert snapshot.plant.dispatcher_min_floor < snapshot.plant.dispatcher_max_ceiling


def test_order_timeout_tightens_under_stress_with_registry_bounds_and_warmup_behaviour():
    base = 30                                                                                  # order_timeout_seconds_execution default
    def timeout(env, **changes):
        controller = Revision5DynamicParameterController(params_with(**changes))
        return controller.evaluate(env).execution.order_timeout_seconds
    stress_of = lambda t: clip(D["dyn_stress_turbulent_weight"] * t, 0.0, 1.0)
    turbulent = R5EnvironmentState(normalized_vol=1.0, trend_probability=0.0, chop_probability=0.0, turbulent_probability=1.0)
    assert timeout(neutral()) == base                                                         # stress 0: contract value, defaults kept
    s = stress_of(1.0)
    assert timeout(turbulent) == int(round(clip(base / (1 + D["psch_order_timeout_stress_gain"] * s), 15.0, 30.0))) < base
    assert timeout(turbulent, psch_order_timeout_stress_gain=5.0) == int(round(clip(base / (1 + 5.0 * s), 15.0, 30.0)))
    assert timeout(turbulent, psch_order_timeout_stress_gain=5.0, psch_order_timeout_min_seconds=15.0) == 15       # min binds
    assert timeout(turbulent, psch_order_timeout_stress_gain=5.0, psch_order_timeout_min_seconds=6.0) == int(round(base / (1 + 5.0 * s)))
    assert timeout(neutral(), psch_order_timeout_max_seconds=20.0) == 20                      # max below the base binds everywhere
    assert timeout(turbulent, psch_order_timeout_min_seconds=600.0, psch_order_timeout_max_seconds=600.0) == base   # never lengthens
    # monotone in stress: never above the base, tighter as stress rises
    series = [timeout(R5EnvironmentState(normalized_vol=1.0, trend_probability=1 - t, chop_probability=0.0, turbulent_probability=t))
              for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert series == sorted(series, reverse=True) and series[0] == base and series[-1] < base
    # consumer (tighten-only gate config) and warm-up: before the first bar the contract value stays
    o, ts = _engine(dynamic_profile_mode="consume")
    assert o.entry_decision_engine.config.order_timeout_seconds == o._base_gate_config.order_timeout_seconds
    o._plant_environment = lambda tick_events: turbulent
    o._native_bar_index = 0
    o._plant_bar_step(ts, [], {})
    assert o.entry_decision_engine.config.order_timeout_seconds == min(o._base_gate_config.order_timeout_seconds,
                                                                       timeout(turbulent)) < o._base_gate_config.order_timeout_seconds
    o._plant_environment = lambda tick_events: (_ for _ in ()).throw(ValueError("environment unavailable"))
    with pytest.raises(ValueError):                                                           # failure: fail closed, no stale reuse
        o._plant_bar_step(ts + pd.Timedelta(minutes=1), [], {})


def test_dispatch_floor_reaches_the_sector_dispatch_references_in_consume_mode():
    o, ts = _engine(dynamic_profile_mode="consume")
    merit = o.plant_control.dispatch_controller.merit_source
    assert merit.runtime_floor is None
    stressed = R5EnvironmentState(normalized_vol=1.0, trend_probability=0.0, chop_probability=0.0, turbulent_probability=1.0)
    o._plant_environment = lambda tick_events: stressed
    o._native_bar_index = 0
    o._plant_bar_step(ts, [], {})
    expected = clip(D["disp_allocation_floor"] * (1 - D["psch_dispatch_floor_stress_gain"] * D["dyn_stress_turbulent_weight"]),
                    D["psch_dispatch_floor_min"], D["psch_dispatch_floor_max"])
    assert merit.runtime_floor == pytest.approx(expected) and merit.dispatch_floor == merit.runtime_floor
    result = o.plant_control.dispatch_controller.dispatch(_ecs(0.8))
    assert min(result.as_dict().values()) >= min(expected, merit.dispatch_ceiling) - 1e-12
    assert sum(result.as_dict().values()) <= 0.8 + 1e-12
