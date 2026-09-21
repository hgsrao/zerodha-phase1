"""Parameterization of (1) the external-engine closed-loop feedback subsystem
(ClosedLoopSupervisor, outcome ledger, dynamics profiler, HMM hysteresis, portfolio
comparator -- NOT the ECS plant supervisor, which does not exist as a class yet),
(2) the Studies PID / local signal weighting (CompositeStudySignal), and (3) the
per-trade FinalExecutionController (NOT a sector dispatch controller).

Values are ENGINEERING_INITIAL_VALUE / NOT_CALIBRATED.  Defaults are proven equal to the
previous literals by running the ACTUAL previous source (git object 55a8069) side by side.
"""
import ast
import inspect
import subprocess
import sys
import types

sys.path.insert(0, ".")

import numpy as np
import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig
from revision2_external import final_execution_controller as fec_module
from revision2_external.closed_loop_control import (
    CausalOutcomeLedger, ClosedLoopSupervisor, HMMRiskHysteresis, SymbolDynamicsProfiler)
from revision2_external.composite_study_signal import (
    MAX_WEIGHT, MIN_WEIGHT, STUDY_NAMES, CompositeStudySignal)
from revision2_external.final_execution_controller import FinalExecutionController

REG = CanonicalParameterRegistry()
PRIOR_COMMIT = "55a80696f023dae52e50155f7f4674b761ab878a"

OLD_LITERALS = {
    "studies_pid_kp": 0.15, "studies_pid_ki": 0.05, "studies_pid_kd": 0.05,
    "studies_pid_output_clamp": 0.15, "studies_grading_horizon_bars": 5,
    "studies_hit_rate_window_bars": 20,
    "cl_outcome_min_history": 20, "cl_confidence_offset_gain": 0.10, "cl_confidence_offset_max": 0.05,
    "cl_dynamics_lookback_bars": 60, "cl_dynamics_ema_span": 20, "cl_response_time_min_bars": 5.0,
    "cl_response_time_max_bars": 45.0, "cl_response_time_default_bars": 20.0,
    "cl_damping_response_gain": 2.0, "cl_hmm_stress_enter": 0.75, "cl_hmm_stress_exit": 0.55,
    "cl_hmm_confirmation_bars": 3, "cl_hmm_smoothing_alpha": 0.25, "cl_hmm_min_derate_step": 0.15,
    "cl_hmm_deadband_derate": 0.90, "cl_portfolio_soft_budget_fraction": 0.75,
}


def cfg(**overrides):
    values = {n: s.default for n, s in REG.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=REG.FROZEN_IDENTITY_SHA256)


def _prior_module(path, name):
    try:
        source = subprocess.run(["git", "show", f"{PRIOR_COMMIT}:{path}"], check=True,
                                capture_output=True, text=True).stdout
    except Exception:  # pragma: no cover - git object unavailable
        pytest.skip("prior commit unavailable")
    module = types.ModuleType(name)
    sys.modules[name] = module        # dataclasses resolve annotations through sys.modules
    exec(compile(source, name, "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def old_closed_loop():
    return _prior_module("revision2_external/closed_loop_control.py", "old_closed_loop")


@pytest.fixture(scope="module")
def old_studies():
    return _prior_module("revision2_external/composite_study_signal.py", "old_studies")


# --------------------------------------------------------------- ownership

def test_registry_owns_the_new_parameters():
    from revision2.calibration_supervisor import trading_search_space
    assert len(OLD_LITERALS) == 22
    allowed_boxes = {"PA", "MPC"}
    for name, old in OLD_LITERALS.items():
        spec = REG.params[name]
        assert spec.black_box in allowed_boxes
        assert "ENGINEERING_INITIAL_VALUE" in spec.notes and "NOT_CALIBRATED" in spec.notes
        assert spec.default == old and spec.minimum <= spec.default <= spec.maximum
        assert isinstance(spec.default, int) == (spec.param_type == "int")
        # consumed only by the external engine, and calibratable in principle
        assert spec.applicable_engines == "EXTERNAL" and spec.calibratable
    assert set(OLD_LITERALS) <= REG.EXTERNAL_ONLY_NAMES
    assert not set(OLD_LITERALS) & set(trading_search_space(REG, engine="IN_HOUSE").names)
    assert set(OLD_LITERALS) <= set(trading_search_space(REG, engine="EXTERNAL").names)
    assert (len(REG.params), len(REG.fixed_target_names()), len(REG.safety_params),
            len(REG.calibratable_names())) == (136, 28, 20, 108)
    REG.verify_frozen_identity()


def test_hmm_hysteresis_ranges_can_never_cross():
    enter, exit_ = REG.params["cl_hmm_stress_enter"], REG.params["cl_hmm_stress_exit"]
    assert exit_.maximum < enter.minimum


def test_in_house_sensitivity_sweeps_only_include_in_house_parameters():
    """The in-house sensitivity tests sweep every IN_HOUSE-eligible ID/MPC/PA parameter."""
    for box in ("ID", "MPC", "PA"):
        swept = {n for n, s in REG.params.items() if s.black_box == box and REG.is_calibratable(n, "IN_HOUSE")}
        assert not swept & REG.EXTERNAL_ONLY_NAMES


# ------------------------------------------------------- studies PID: setup

def _bars(n=140, seed=0, drift=0.0, vol=0.004, start=1000.0):
    rng = np.random.default_rng(seed)
    prices = start * np.cumprod(1 + drift + rng.normal(0, vol, n))
    idx = pd.date_range("2024-01-02 09:15", periods=n, freq="min", tz="Asia/Kolkata")
    return pd.DataFrame({"timestamp": idx, "open": prices, "high": prices * 1.002,
                         "low": prices * 0.998, "close": prices,
                         "volume": rng.integers(1000, 5000, n)})


def _run(engine, bars, symbol="S", start=30):
    out = []
    for i in range(start, len(bars)):
        out.append(engine.evaluate(symbol, bars.iloc[:i + 1]))
    return out


def test_studies_defaults_equal_previous_literals_and_previous_behavior(old_studies):
    new, old = CompositeStudySignal(), old_studies.CompositeStudySignal()
    assert (new.kp, new.ki, new.kd, new.clamp, new.grading_horizon, new.hit_rate_window) == (
        old.kp, old.ki, old.kd, old.clamp, old_studies._GRADING_HORIZON, old_studies._HIT_RATE_WINDOW)
    assert (MIN_WEIGHT, MAX_WEIGHT) == (old_studies._MIN_WEIGHT, old_studies._MAX_WEIGHT)
    bars = _bars(seed=3)
    for a, b in zip(_run(new, bars), _run(old, bars)):
        assert a["weights"] == b["weights"] and a["confidence"] == b["confidence"]
        assert a["direction"] == b["direction"] and a["hit_rates"] == b["hit_rates"]
        assert a["weight_pid_audit"] == b["weight_pid_audit"]


def test_explicit_legacy_constructor_arguments_still_win():
    engine = CompositeStudySignal(kp=0.2, ki=0.06, kd=0.07, clamp=-0.1)
    assert (engine.kp, engine.ki, engine.kd, engine.clamp) == (0.2, 0.06, 0.07, 0.1)


def _audits(engine, bars):
    return _run(engine, bars)[-1]["weight_pid_audit"]


def test_kp_ki_kd_change_the_matching_pid_terms_and_weights():
    bars = _bars(seed=5, drift=0.0004)
    base = CompositeStudySignal(config=cfg())
    base_last = _run(base, bars)[-1]
    for name, term in (("studies_pid_kp", "p"), ("studies_pid_ki", "i"), ("studies_pid_kd", "d")):
        moved = CompositeStudySignal(config=cfg(**{name: REG.params[name].maximum}))
        last = _run(moved, bars)[-1]
        changed = [s for s in STUDY_NAMES
                   if abs(last["weight_pid_audit"][s][term] - base_last["weight_pid_audit"][s][term]) > 1e-12]
        assert changed, f"{name} did not change PID term {term}"
    hi_kp = CompositeStudySignal(config=cfg(studies_pid_kp=0.30))
    assert _run(hi_kp, bars)[-1]["weights"] != base_last["weights"]


def test_ki_accumulates_across_bars_and_kd_responds_to_transients():
    bars = _bars(seed=8, drift=0.0005)
    integral = {}
    for ki in (0.01, 0.15):
        engine = CompositeStudySignal(config=cfg(studies_pid_ki=ki))
        _run(engine, bars)
        integral[ki] = max(abs(engine._symbols["S"][n].pid.components[1]) for n in STUDY_NAMES)
    assert integral[0.15] > integral[0.01] > 0.0
    derivative = {}
    for kd in (0.01, 0.15):
        engine = CompositeStudySignal(config=cfg(studies_pid_kd=kd))
        derivative[kd] = max(abs(a["d"]) for r in _run(engine, bars) for a in r["weight_pid_audit"].values())
    assert derivative[0.15] > derivative[0.01]


def test_clamp_limits_pid_output_and_weight_swing():
    bars = _bars(seed=9, drift=0.0006)
    for clamp in (0.05, 0.25):
        engine = CompositeStudySignal(config=cfg(studies_pid_output_clamp=clamp))
        for result in _run(engine, bars):
            for audit in result["weight_pid_audit"].values():
                assert abs(audit["raw_output"]) <= clamp + 1e-12
                assert abs(audit["weight_after"] - 0.25) <= clamp + 1e-9 or audit["weight_after"] in (MIN_WEIGHT, MAX_WEIGHT)


def test_weight_envelope_is_fixed_not_a_parameter():
    assert (MIN_WEIGHT, MAX_WEIGHT) == (0.05, 0.60)
    assert not any("weight_min" in n or "weight_max" in n for n in REG.params)


def test_grading_horizon_controls_when_a_vote_becomes_gradeable():
    bars = _bars(seed=2)
    first = {}
    for horizon in (3, 10):
        engine = CompositeStudySignal(config=cfg(studies_grading_horizon_bars=horizon))
        for i in range(0, 40):
            result = engine.evaluate("S", bars.iloc[:i + 1])
            if result["weight_pid_audit"]["session_vwap"]["graded_vote_count"] > 0:
                first[horizon] = i
                break
    assert first == {3: 3, 10: 10}   # VWAP votes from bar 0; graded once `horizon` bars elapsed


def test_grading_uses_only_elapsed_closes():
    bars = _bars(seed=4)
    engine = CompositeStudySignal(config=cfg(studies_grading_horizon_bars=4))
    votes, closes, expected_hits = [], [], []
    for i in range(0, 60):
        result = engine.evaluate("S", bars.iloc[:i + 1])
        votes.append(result["votes"]["session_vwap"])
        closes.append(float(bars["close"].iloc[i]))
        if i >= 4 and votes[i - 4] != 0:
            expected_hits.append(1 if (votes[i - 4] == 1) == (closes[i] > closes[i - 4]) else 0)
    assert list(engine._symbols["S"]["session_vwap"].hit_history) == expected_hits[-20:]


def test_hit_rate_window_changes_retained_history():
    bars = _bars(seed=6)
    for window in (10, 40):
        engine = CompositeStudySignal(config=cfg(studies_hit_rate_window_bars=window))
        _run(engine, bars, start=0)
        history = engine._symbols["S"]["session_vwap"].hit_history
        assert history.maxlen == window and len(history) == window


def test_reconfiguration_preserves_pid_objects_and_state():
    bars = _bars(seed=7, drift=0.0003)
    engine = CompositeStudySignal(config=cfg())
    _run(engine, bars[:100], start=0)
    before = {n: engine._symbols["S"][n] for n in STUDY_NAMES}
    snapshot = {n: (before[n].pid, before[n].pid._integral, before[n].pid._last_input,
                    list(before[n].hit_history), list(before[n].vote_history),
                    list(before[n].close_history), before[n].weight) for n in STUDY_NAMES}
    engine.configure(cfg(studies_pid_kp=0.25, studies_pid_ki=0.1, studies_pid_kd=0.1,
                         studies_pid_output_clamp=0.25, studies_grading_horizon_bars=3,
                         studies_hit_rate_window_bars=10))
    for name in STUDY_NAMES:
        state = engine._symbols["S"][name]
        pid, integral, last_input, hits, votes, closes, weight = snapshot[name]
        assert state.pid is pid and state.weight == weight              # same object, same state
        assert pid._integral == integral and pid._last_input == last_input
        assert pid.tunings == (0.25, 0.1, 0.1) and pid.output_limits == (-0.25, 0.25)
        assert list(state.hit_history) == hits[-10:]                    # newest causal history kept
        assert list(state.vote_history) == votes[-4:] and list(state.close_history) == closes[-4:]
    # growth keeps everything and just allows more history
    engine.configure(cfg(studies_grading_horizon_bars=8, studies_hit_rate_window_bars=30))
    state = engine._symbols["S"]["ichimoku"]
    assert list(state.vote_history) == snapshot["ichimoku"][4][-4:] and state.vote_history.maxlen == 9
    assert state.hit_history.maxlen == 30


def test_clamp_reduction_bounds_integral_without_rebuilding():
    bars = _bars(seed=10, drift=0.0006)
    engine = CompositeStudySignal(config=cfg())
    _run(engine, bars, start=0)
    pid = engine._symbols["S"]["bollinger"].pid
    engine.configure(cfg(studies_pid_output_clamp=0.05))
    assert engine._symbols["S"]["bollinger"].pid is pid
    assert -0.05 <= pid._integral <= 0.05


def test_invalid_reconfiguration_changes_nothing():
    engine = CompositeStudySignal(config=cfg())
    _run(engine, _bars(seed=11), start=0)
    before = (engine.kp, engine.grading_horizon, list(engine._symbols["S"]["ichimoku"].vote_history))
    for bad in (cfg(studies_grading_horizon_bars=1), cfg(studies_pid_kp=9.0), cfg(studies_hit_rate_window_bars=7.5)):
        with pytest.raises(ValueError):
            engine.configure(bad)
    assert before == (engine.kp, engine.grading_horizon, list(engine._symbols["S"]["ichimoku"].vote_history))


def test_per_symbol_state_isolated_across_reconfiguration():
    a, b = _bars(seed=12, drift=0.0005), _bars(seed=13, drift=-0.0005)
    engine = CompositeStudySignal(config=cfg())
    _run(engine, a, symbol="A", start=0)
    _run(engine, b, symbol="B", start=0)
    b_hits = list(engine._symbols["B"]["session_vwap"].hit_history)
    engine.configure(cfg(studies_hit_rate_window_bars=10))
    engine.evaluate("A", a)
    assert engine._symbols["A"]["session_vwap"].pid is not engine._symbols["B"]["session_vwap"].pid
    assert list(engine._symbols["B"]["session_vwap"].hit_history) == b_hits[-10:]


# ------------------------------------------------ supervisor: default equivalence

def _outcomes():
    rng = np.random.default_rng(1)
    return [{"symbol": "A" if i % 3 else "B", "side": "BUY", "regime": "unknown",
             "net_pnl": float(rng.normal(-0.2, 1.0))} for i in range(60)]


def test_ledger_defaults_match_previous_source(old_closed_loop):
    new, old = CausalOutcomeLedger(), old_closed_loop.CausalOutcomeLedger()
    for outcome in _outcomes():
        new.record(outcome)
        old.record(outcome)
        assert new.profile("A", "BUY") == old.profile("A", "BUY")
    assert new.profile("A", "SELL") == old.profile("A", "SELL")


def test_dynamics_defaults_match_previous_source(old_closed_loop):
    bars = _bars(n=150, seed=21)
    new, old = SymbolDynamicsProfiler(), old_closed_loop.SymbolDynamicsProfiler()
    for n in (2, 3, 25, 70, 150):
        assert new.estimate(bars.iloc[:n]) == old.estimate(bars.iloc[:n])


def test_hysteresis_and_portfolio_defaults_match_previous_source(old_closed_loop):
    new, old = HMMRiskHysteresis(), old_closed_loop.HMMRiskHysteresis()
    rng = np.random.default_rng(2)
    for p in list(rng.uniform(0, 1, 60)) + [0.99] * 12 + [0.05] * 12:
        obs = {"available": True, "stress_probability": float(p)}
        assert new.update(obs) == old.update(obs)
    for exposure in (0.0, 0.5, 0.75, 0.8, 0.95, 1.0, 1.2):
        assert (ClosedLoopSupervisor.observe_portfolio_risk(exposure * 1e5, 1e5, 1.0)
                == old_closed_loop.ClosedLoopSupervisor.observe_portfolio_risk(exposure * 1e5, 1e5, 1.0))
    snapshot = ClosedLoopSupervisor().entry_snapshot("S", "BUY", 100.0, 98.0, 104.0, 20)
    old_snapshot = old_closed_loop.ClosedLoopSupervisor().entry_snapshot("S", "BUY", 100.0, 98.0, 104.0, 20)
    assert snapshot == old_snapshot


# ------------------------------------------------ supervisor: parameter behavior

def _loss_ledger(config, n=40):
    ledger = CausalOutcomeLedger(config=config)
    for _ in range(n):
        ledger.record({"symbol": "A", "side": "BUY", "regime": "unknown", "net_pnl": -1.0})
    return ledger.profile("A", "BUY")


def test_outcome_history_and_offset_controls_change_entry_quality():
    base = _loss_ledger(cfg())
    slow = _loss_ledger(cfg(cl_outcome_min_history=40))
    assert slow["partial_pool_weight"] < base["partial_pool_weight"]            # more evidence needed
    assert slow["pooled_win_rate"] > base["pooled_win_rate"]                    # stronger neutral prior
    assert base["suggested_confidence_offset"] == pytest.approx(min(0.05, (0.5 - base["pooled_win_rate"]) * 0.10))
    high_gain = _loss_ledger(cfg(cl_confidence_offset_gain=0.20, cl_confidence_offset_max=0.08))
    assert high_gain["suggested_confidence_offset"] > base["suggested_confidence_offset"]
    capped = _loss_ledger(cfg(cl_confidence_offset_gain=0.20, cl_confidence_offset_max=0.02))
    assert capped["suggested_confidence_offset"] == pytest.approx(0.02)


def test_entry_derate_envelope_is_fixed_and_bounded():
    for wins in (0, 40):
        ledger = CausalOutcomeLedger(config=cfg())
        for _ in range(40):
            ledger.record({"symbol": "A", "side": "BUY", "regime": "unknown", "net_pnl": 1.0 if wins else -1.0})
        profile = ledger.profile("A", "BUY")
        assert 0.5 <= profile["suggested_entry_derate"] <= 1.0
        assert 0.0 <= profile["suggested_confidence_offset"] <= 0.05
    assert not any("derate_floor" in n for n in REG.params)


def test_dynamics_controls_change_the_profile():
    bars = _bars(n=160, seed=22, vol=0.006)
    base = SymbolDynamicsProfiler(config=cfg()).estimate(bars)
    lookback = SymbolDynamicsProfiler(config=cfg(cl_dynamics_lookback_bars=120)).estimate(bars)
    span = SymbolDynamicsProfiler(config=cfg(cl_dynamics_ema_span=40)).estimate(bars)
    assert lookback["sample_bars"] == 120 and base["sample_bars"] == 60
    assert span["deviation_persistence"] != base["deviation_persistence"]
    damped = SymbolDynamicsProfiler(config=cfg(cl_damping_response_gain=3.0)).estimate(bars)
    assert damped["response_time_bars"] >= base["response_time_bars"]
    tight = SymbolDynamicsProfiler(config=cfg(cl_response_time_min_bars=10.0, cl_response_time_max_bars=30.0)).estimate(bars)
    assert 10.0 <= tight["response_time_bars"] <= 30.0
    fallback = SymbolDynamicsProfiler(config=cfg(cl_response_time_default_bars=30.0)).estimate(bars.iloc[:2])
    assert fallback["response_time_bars"] == 30.0
    assert base["suggested_pid_gain_scale"] == pytest.approx(
        min(1.5, max(0.5, 20.0 / base["response_time_bars"])))


def test_profiler_bounds_must_be_ordered():
    with pytest.raises(ValueError):
        SymbolDynamicsProfiler(config=cfg(cl_response_time_min_bars=10.0, cl_response_time_max_bars=10.0 - 1e-9))


def test_reference_path_is_frozen_at_entry_and_ignores_later_reconfiguration():
    supervisor = ClosedLoopSupervisor(config=cfg())
    snapshot = supervisor.entry_snapshot("S", "BUY", 100.0, 98.0, 104.0, 20)
    assert snapshot["reference_path"]["response_time_bars"] == 20.0
    before = supervisor.observe_trade_path(snapshot, 99.0, 8).to_dict()
    supervisor.configure(cfg(cl_response_time_default_bars=30.0))
    assert supervisor.entry_snapshot("S", "BUY", 100.0, 98.0, 104.0, 20)["reference_path"]["response_time_bars"] == 30.0
    assert supervisor.observe_trade_path(snapshot, 99.0, 8).to_dict() == before
    assert snapshot["reference_path"]["response_time_bars"] == 20.0


def _drive(hysteresis, probabilities):
    return [hysteresis.update({"available": True, "stress_probability": p}) for p in probabilities]


def test_hysteresis_thresholds_and_confirmation_latch_and_release():
    stress = [0.9] * 30
    assert _drive(HMMRiskHysteresis(), stress)[-1]["stressed_latched"]
    assert not _drive(HMMRiskHysteresis(enter_stress_probability=None), [0.6] * 30)[-1]["stressed_latched"]
    low_enter = HMMRiskHysteresis()
    low_enter.configure(cfg(cl_hmm_stress_enter=0.7, cl_hmm_stress_exit=0.4))
    assert _drive(low_enter, [0.7] * 30)[-1]["stressed_latched"]
    # confirmation bars: exactly N consecutive filtered bars at/above enter are required
    for bars_needed in (2, 6):
        h = HMMRiskHysteresis()
        h.configure(cfg(cl_hmm_confirmation_bars=bars_needed, cl_hmm_smoothing_alpha=0.5))
        latched_at = next(i for i, r in enumerate(_drive(h, [1.0] * 20)) if r["stressed_latched"])
        assert latched_at == bars_needed - 1
    # release requires a sustained lower posterior
    h = HMMRiskHysteresis()
    _drive(h, [1.0] * 10)
    assert h.stressed_latched
    assert _drive(h, [0.0] * 60)[-1]["stressed_latched"] is False


def test_hysteresis_smoothing_step_and_deadband_change_the_derate_stream():
    stream = [0.05, 0.15, 0.3, 0.5, 0.2, 0.1, 0.4, 0.05] * 3
    base = [r["suggested_hysteresis_derate"] for r in _drive(HMMRiskHysteresis(), stream)]
    for name, value in (("cl_hmm_smoothing_alpha", 0.5), ("cl_hmm_min_derate_step", 0.05),
                        ("cl_hmm_deadband_derate", 0.80)):
        h = HMMRiskHysteresis()
        h.configure(cfg(**{name: value}))
        assert [r["suggested_hysteresis_derate"] for r in _drive(h, stream)] != base, name
    assert 0.0 <= min(base) and max(base) <= 1.0


def test_hysteresis_reconfiguration_preserves_state_and_is_atomic():
    h = HMMRiskHysteresis()
    _drive(h, [0.95] * 6)
    state = (h.filtered_probability, h.stressed_latched, h.enter_count, h.exit_count, h.applied_derate)
    h.configure(cfg(cl_hmm_confirmation_bars=5, cl_hmm_smoothing_alpha=0.4))
    assert state == (h.filtered_probability, h.stressed_latched, h.enter_count, h.exit_count, h.applied_derate)
    assert h.confirmation_bars == 5
    with pytest.raises(ValueError):                                   # exit must stay below enter
        h.configure(cfg(cl_hmm_stress_enter=0.6, cl_hmm_stress_exit=0.7))
    assert (h.enter_stress_probability, h.exit_stress_probability) == (0.75, 0.55)
    # research scripts keep explicit historical arguments
    assert HMMRiskHysteresis(smoothing_alpha=1.0, confirmation_bars=3).smoothing_alpha == 1.0


def test_portfolio_soft_budget_controls_derate_but_hard_limit_stays_authoritative():
    base = ClosedLoopSupervisor.observe_portfolio_risk(80_000.0, 100_000.0, 1.0)
    tight = ClosedLoopSupervisor.observe_portfolio_risk(80_000.0, 100_000.0, 1.0, soft_budget_fraction=0.5)
    loose = ClosedLoopSupervisor.observe_portfolio_risk(80_000.0, 100_000.0, 1.0, soft_budget_fraction=0.9)
    assert loose["suggested_new_risk_derate"] > base["suggested_new_risk_derate"] > tight["suggested_new_risk_derate"]
    assert tight["soft_budget_fraction"] == 0.5
    for fraction in (0.5, 0.75, 0.9):
        at_limit = ClosedLoopSupervisor.observe_portfolio_risk(100_000.0, 100_000.0, 1.0, soft_budget_fraction=fraction)
        assert at_limit["suggested_new_risk_derate"] == 0.0 and at_limit["at_or_over_hard_limit"]
        over = ClosedLoopSupervisor.observe_portfolio_risk(120_000.0, 100_000.0, 1.0, soft_budget_fraction=fraction)
        assert over["suggested_new_risk_derate"] == 0.0
    supervisor = ClosedLoopSupervisor(config=cfg(cl_portfolio_soft_budget_fraction=0.5))
    assert supervisor.soft_budget_fraction == 0.5
    assert not any("hard_limit" in n for n in REG.params)               # owned by the SafetyContract


def test_supervisor_configure_keeps_recorded_outcomes():
    supervisor = ClosedLoopSupervisor(config=cfg())
    supervisor.record_outcome({"symbol": "A", "side": "BUY", "net_pnl": 5.0})
    supervisor.configure(cfg(cl_outcome_min_history=30))
    assert supervisor.outcomes.minimum_history == 30
    assert supervisor.outcomes.profile("A", "BUY")["completed_outcomes_total"] == 1


# ------------------------------------------------ orchestrator wiring

def test_orchestrator_wires_controls_from_effective_config():
    """Non-optimizer parameters cannot enter through calibration overrides (correct: that
    payload is optimizer-only), so inject non-default registry values the way a trial
    registry does: through the registry instance the orchestrator builds its config from."""
    from dataclasses import replace
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    registry = CanonicalParameterRegistry()
    for name, value in {"studies_pid_kp": 0.2, "studies_grading_horizon_bars": 4,
                        "cl_outcome_min_history": 30, "cl_portfolio_soft_budget_fraction": 0.6}.items():
        registry.params[name] = replace(registry.params[name], default=value)
    orch = Revision2ExternalEngineOrchestrator(["S"], registry)
    assert (orch.chart_studies.kp, orch.chart_studies.grading_horizon) == (0.2, 4)
    assert orch.closed_loop.outcomes.minimum_history == 30 and orch.closed_loop.soft_budget_fraction == 0.6
    assert all(n in orch.consumed_parameters for n in OLD_LITERALS if n.startswith(("studies_", "cl_")))
    # external calibration payloads may move external-applicable parameters ...
    tuned = Revision2ExternalEngineOrchestrator(["S"], REG, calibration_overrides={"studies_pid_kp": 0.2})
    assert tuned.chart_studies.kp == 0.2
    # ... but never fixed ones, and the in-house engine refuses external-only names
    with pytest.raises(ValueError):
        Revision2ExternalEngineOrchestrator(["S"], REG, calibration_overrides={"mpc_shadow_r_gamma": 0.1})
    assert REG.validate_calibration_payload({"studies_pid_kp": 0.2}, engine="IN_HOUSE")
    source = inspect.getsource(Revision2ExternalEngineOrchestrator)
    assert "0.60 - 1e-12" not in source and "0.05 + 1e-12" not in source
    assert 'entry_quality["symbol_regime_samples"] >= 20' not in source


# ------------------------------------------------ dispatch / FinalExecutionController

def test_final_execution_controller_has_no_operational_numeric_constants():
    tree = ast.parse(inspect.getsource(fec_module))
    numbers = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
    assert numbers <= {0, 0.0, 1, -1}            # direction signs, zero/none defaults only
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    froms = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imports == set() and froms <= {"__future__", "typing"}    # no plant/governor/broker access
    assert vars(FinalExecutionController()) == {}                    # no retained state
    assert not any(n.startswith(("final_execution", "dispatch")) for n in REG.params)


def _entry(**overrides):
    args = dict(side="BUY", pa_confidence=0.7, id_approved=True, studies_direction=1, studies_confidence=0.6,
                entry_price=100.0, stop_price=98.0, target_price=104.0, maximum_hold_bars=20,
                entry_quality={"symbol_regime_samples": 5, "suggested_entry_derate": 0.9},
                dynamics={"response_time_bars": 12.0})
    args.update(overrides)
    return FinalExecutionController().entry_decision(**args)


def test_entry_action_depends_only_on_id_approval_side_and_studies_direction():
    assert _entry()["action"] == "ADMIT"
    assert (_entry(id_approved=False)["action"], _entry(id_approved=False)["reason"]) == ("CANCEL", "ID_NOT_APPROVED")
    assert _entry(studies_direction=0)["action"] == "DEFER"
    assert _entry(studies_direction=-1)["reason"] == "STUDIES_DIRECTION_CONFLICT"
    assert _entry(side="SELL", studies_direction=-1)["action"] == "ADMIT"
    baseline = _entry()["action"]
    for confidence in (0.0, 0.3, 0.99):                # every numeric input is telemetry, not a threshold
        assert _entry(pa_confidence=confidence, studies_confidence=confidence)["action"] == baseline
    for quality in ({"symbol_regime_samples": 0, "suggested_entry_derate": 0.5},
                    {"symbol_regime_samples": 999, "suggested_entry_derate": 1.0}):
        assert _entry(entry_quality=quality)["action"] == baseline
    for hold in (1, 1000):
        assert _entry(maximum_hold_bars=hold)["action"] == baseline
    assert _entry(dynamics={})["action"] == baseline


def _exit(**overrides):
    args = dict(path={"behind_path": False}, exit_pid={"studies_clamped": False, "stop_before": 99.0, "stop_after": 99.0},
                held_bars=5, minimum_hold_bars=3, maximum_hold_bars=20, side="BUY")
    args.update(overrides)
    return FinalExecutionController().exit_decision(**args)


def test_exit_action_depends_only_on_named_inputs_and_boundaries():
    assert _exit()["action"] == "HOLD"
    assert _exit(held_bars=20)["reason"] == "MAXIMUM_HOLD_REACHED"
    assert _exit(held_bars=19)["action"] == "HOLD" and _exit(held_bars=21)["action"] == "EXIT"
    saturated = {"studies_clamped": True, "stop_before": 99.0, "stop_after": 99.0}
    assert _exit(exit_pid=saturated, held_bars=3)["action"] == "EXIT"        # at minimum hold
    assert _exit(exit_pid=saturated, held_bars=2)["action"] == "HOLD"        # before minimum hold
    assert _exit(path={"behind_path": True}, held_bars=3)["action"] == "EXIT_NEXT_BAR"
    assert _exit(path={"behind_path": True}, held_bars=2)["action"] == "HOLD"
    assert _exit(exit_pid={"stop_before": 99.0, "stop_after": 99.5})["action"] == "PROTECT"
    assert _exit(exit_pid={"stop_before": 99.0, "stop_after": 98.0})["action"] == "HOLD"
    assert _exit(side="SELL", exit_pid={"stop_before": 101.0, "stop_after": 100.5})["action"] == "PROTECT"
    with pytest.raises(ValueError):
        _exit(side="HOLD")
    # telemetry-only fields never change the action
    noisy = {"studies_clamped": False, "stop_before": 99.0, "stop_after": 99.0,
             "studies_output": 9.9, "studies_tightness": 0.0}
    assert _exit(exit_pid=noisy)["action"] == "HOLD"
