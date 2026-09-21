"""BB05 (HMM regime / IntelligentDiscrimination) and BB06 (PID/MPC + continuous exit)
parameterization: ownership, propagation, sensitivity, cache correctness, defaults.

All values here are ENGINEERING_INITIAL_VALUE / NOT_CALIBRATED; nothing is calibrated.
"""
import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, IDDecision, PASignal
from revision2_external import regime_id_box as rib
from revision2_external.bb05_bb06_parameters import default_config, require
from revision2_external.continuous_exit_controller import ContinuousExitController
from revision2_external.dynamic_parameter_controller import (
    DynamicParameterController, MarketEnvironmentState)
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox
from revision2_external.regime_hmm import GaussianHMM
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox
from revision5.supervisory_bridge import Revision5SupervisoryBridge

REG = CanonicalParameterRegistry()

# Literal values that existed before this change (bf091fd source); defaults must equal them.
OLD_LITERALS = {
    "id_feature_window": 200, "id_refit_every_bars": 20, "id_min_history_bars": 60,
    "id_volatility_window": 10, "id_volatility_min_samples": 3, "id_hmm_iterations": 20,
    "id_hmm_tolerance": 1e-4, "id_min_state_occupancy": 0.05, "id_variance_ratio": 2.5,
    "id_slippage_cap": 0.20, "id_slippage_gain": 2.0, "id_reward_floor": 0.05,
    "id_reward_gain": 4.0, "id_risk_floor": 0.10, "id_risk_gain": 2.0,
    "id_variance_floor": 1e-8, "id_initial_variance_regularizer": 1e-6,
    "mpc_entry_price_gain": 0.001, "mpc_base_slippage_fraction": 0.0005,
    "mpc_time_decay_gain": 0.5, "mpc_shadow_r_gamma": 0.65,
    "mpc_schedule_kp_gain": 1.2, "mpc_schedule_ki_gain": 2.0, "mpc_schedule_kd_gain": 0.8,
    "mpc_environment_lookback": 50, "mpc_range_atr_period": 14,
    "mpc_range_fallback_fraction": 0.01, "mpc_atr_floor_gain": 0.005,
    "mpc_slippage_vol_gain": 0.5,
}
NON_OPTIMIZER = {"mpc_base_slippage_fraction", "mpc_shadow_r_gamma", "mpc_slippage_vol_gain",
                 "id_variance_floor", "id_initial_variance_regularizer"}


def cfg(**overrides):
    values = {n: s.default for n, s in REG.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=REG.FROZEN_IDENTITY_SHA256)


# ---------------------------------------------------------------- ownership

def test_registry_owns_all_parameters_with_labels_and_defaults():
    assert len(OLD_LITERALS) == 29
    for name, old in OLD_LITERALS.items():
        spec = REG.params[name]
        assert spec.black_box == ("ID" if name.startswith("id_") else "MPC")
        assert "ENGINEERING_INITIAL_VALUE" in spec.notes and "NOT_CALIBRATED" in spec.notes
        assert "BB05-BB06" in spec.notes and "BB04" not in spec.notes
        assert spec.default == old and spec.minimum <= spec.default <= spec.maximum


def test_surface_counts_and_identity_and_search_space():
    from revision2.calibration_supervisor import trading_search_space
    assert (len(REG.params), len(REG.fixed_target_names()), len(REG.safety_params),
            len(REG.calibratable_names())) == (114, 27, 20, 87)
    REG.verify_frozen_identity()
    names = set(trading_search_space(REG).names)
    for name in OLD_LITERALS:
        assert (name in names) == (name not in NON_OPTIMIZER), name
        assert REG.params[name].calibratable == (name not in NON_OPTIMIZER)


def test_helper_is_not_a_second_owner_and_validates():
    config = default_config()
    assert config.registry_hash == REG.FROZEN_IDENTITY_SHA256
    assert all(config.values[n] == REG.params[n].default for n in OLD_LITERALS)
    assert set(config.values) == set(REG.params)
    assert require(config, "id_feature_window") == 200
    for bad in (cfg(id_feature_window=10), cfg(id_feature_window=200.0), cfg(id_feature_window=True),
                cfg(id_feature_window=float("nan"))):
        with pytest.raises(ValueError):
            require(bad, "id_feature_window")


def test_no_duplicate_operational_literals_in_bb05_sources():
    import ast, inspect
    import revision2_external.regime_hmm as hmm_module
    owned = {1e-8, 1e-6, 2.5, 0.05, 200, 60, 0.2, 4.0, 0.1, 1e-4}
    for module in (rib, hmm_module):
        tree = ast.parse(inspect.getsource(module))
        found = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                 and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
        assert not (found & owned), (module.__name__, found & owned)


# ------------------------------------------------- defaults preserve behavior

def test_default_hmm_constructor_matches_previous_literals():
    hmm = GaussianHMM(n_states=2)
    assert (hmm.n_iter, hmm.tol, hmm.min_state_occupancy_fraction,
            hmm.variance_floor, hmm.initial_variance_regularizer) == (20, 1e-4, 0.05, 1e-8, 1e-6)


def test_default_plan_and_id_decision_match_previous_formulas():
    box = SimplePIDModelPredictiveControlBox(pid_enabled=False)
    plan, info, _ = box.build_plan(_signal(), _decision(), 1000.0, 8.0, cfg())
    assert plan.entry_price == 1000.0 * (1 + 1.0 * 0.0005)  # unchanged adverse-fill formula
    id_box = HMMIntelligentDiscriminationBox()
    id_box._current_regime = lambda *_: "calm"
    d, _ = id_box.evaluate(_signal(confidence=0.8), cfg(), 1000.0)
    assert d.risk_reward_ratio == pytest.approx((0.8 * 4.0) / (max(0.2, 0.1) * 2.0))


# ------------------------------------------------------------ BB05 behavior

class FakeHMM:
    fits = []
    variances = np.array([[1.0, 1.0], [2.0, 2.0]])

    def __init__(self, n_states, **kwargs):
        self.kwargs = kwargs
        self.vars_ = FakeHMM.variances
        self.valid_state_mask_ = np.array([True, True])
        self.state_occupancy_ = np.array([0.5, 0.5])

    def fit(self, X):
        FakeHMM.fits.append(np.array(X))
        return self

    def filter_proba(self, X):
        return np.tile([0.5, 0.5], (len(X), 1))

    def filter_step(self, x, prior=None):
        return np.array([0.5, 0.5])


@pytest.fixture
def fake_hmm(monkeypatch):
    FakeHMM.fits = []
    FakeHMM.variances = np.array([[1.0, 1.0], [2.0, 2.0]])
    monkeypatch.setattr(rib, "GaussianHMM", FakeHMM)
    return FakeHMM


def _closes(n, seed=1):
    rng = np.random.default_rng(seed)
    return list(1000 * np.cumprod(1 + rng.normal(0, 0.005, n)))


def _run(box, n, symbol="S"):
    return [box._current_regime(symbol, c) for c in _closes(n)]


@pytest.mark.parametrize("name,value,kwarg", [
    ("id_hmm_iterations", 33, "n_iter"), ("id_hmm_tolerance", 5e-4, "tol"),
    ("id_min_state_occupancy", 0.11, "min_state_occupancy_fraction"),
    ("id_variance_floor", 5e-7, "variance_floor"),
    ("id_initial_variance_regularizer", 5e-5, "initial_variance_regularizer"),
])
def test_hmm_fit_controls_reach_the_model(monkeypatch, name, value, kwarg):
    made = []

    class Spy(FakeHMM):
        def __init__(self, n_states, **kw):
            super().__init__(n_states, **kw)
            made.append(kw)

    monkeypatch.setattr(rib, "GaussianHMM", Spy)
    box = HMMIntelligentDiscriminationBox(config=cfg(**{name: value}))
    _run(box, 70)
    assert made[-1][kwarg] == value
    assert made[-1][kwarg] != REG.params[name].default


def test_min_history_gates_regime_evaluation(fake_hmm):
    low = _run(HMMIntelligentDiscriminationBox(config=cfg(id_min_history_bars=40)), 50)
    high = _run(HMMIntelligentDiscriminationBox(config=cfg(id_min_history_bars=80)), 50)
    assert low[38] == "unknown" and "unknown" not in low[39:]
    assert set(high) == {"unknown"}


def test_feature_window_bounds_history_and_fit_rows(fake_hmm):
    for window in (100, 150):
        fake_hmm.fits.clear()
        box = HMMIntelligentDiscriminationBox(config=cfg(id_feature_window=window))
        _run(box, 220)
        assert box._bar_history["S"].maxlen == window
        assert len(fake_hmm.fits[0]) <= window - 1


def test_refit_cadence_changes_fit_count(fake_hmm):
    counts = {}
    for every in (10, 30):
        fake_hmm.fits.clear()
        _run(HMMIntelligentDiscriminationBox(config=cfg(id_refit_every_bars=every)), 200)
        counts[every] = len(fake_hmm.fits)
    assert counts[10] > counts[30] >= 1


def test_volatility_window_and_min_samples_shape_features(fake_hmm):
    seen = {}
    for w, m in ((5, 2), (20, 5)):
        fake_hmm.fits.clear()
        _run(HMMIntelligentDiscriminationBox(config=cfg(id_volatility_window=w, id_volatility_min_samples=m)), 61)
        feats = fake_hmm.fits[0]
        rets = pd.Series(_closes(61)[:60]).pct_change().dropna().to_numpy() * 100
        expected = pd.Series(rets).rolling(w, min_periods=m).std().bfill().to_numpy()
        np.testing.assert_allclose(feats[:, 1], expected)
        seen[(w, m)] = feats[:, 1]
    assert not np.allclose(seen[(5, 2)], seen[(20, 5)])


def test_variance_ratio_decides_distinct_stressed_state(fake_hmm):
    # state variance ratio is exactly 2.0: distinct at 1.5, not distinct at 2.5
    box = HMMIntelligentDiscriminationBox(config=cfg(id_variance_ratio=1.5))
    _run(box, 70)
    assert box._cached_stressed_state["S"] == 1
    box = HMMIntelligentDiscriminationBox(config=cfg(id_variance_ratio=2.5))
    _run(box, 70)
    assert box._cached_stressed_state["S"] is None


def test_hmm_numerical_controls_change_local_behavior():
    X = np.column_stack([np.linspace(-1, 1, 60), np.zeros(60)])  # constant column -> zero variance
    hmm = GaussianHMM(n_states=2, variance_floor=1e-3).fit(X)
    assert (hmm.vars_ >= 1e-3).all() and np.isclose(hmm.vars_[:, 1], 1e-3).all()
    a = GaussianHMM(n_states=2, n_iter=1, tol=0.0).fit(X).vars_
    b = GaussianHMM(n_states=2, n_iter=15, tol=0.0).fit(X).vars_
    assert not np.allclose(a, b)
    r1 = GaussianHMM(n_states=2, initial_variance_regularizer=1e-6)
    r2 = GaussianHMM(n_states=2, initial_variance_regularizer=1e-2)
    for m in (r1, r2):
        m._init_params(X)
    assert r2.vars_[0, 1] - r1.vars_[0, 1] == pytest.approx(1e-2 - 1e-6)


def _signal(confidence=0.8, volatility=0.01, band="green", direction=1):
    return PASignal(symbol="S", timestamp="t", direction=direction, confidence=confidence, momentum=0.5,
                    volatility=volatility, vwap_deviation=0.1, volume_confirmation=0.2, quality_band=band)


def _decision(conf=0.8):
    return IDDecision(True, "approved", conf, 2.0, 0.6)


def _id_eval(signal, **overrides):
    box = HMMIntelligentDiscriminationBox()
    box._current_regime = lambda *_: "calm"
    return box.evaluate(signal, cfg(**overrides), 1000.0)[0]


def test_admission_controls_at_boundaries():
    # slippage estimate: min(cap, volatility * gain) against guard
    guard = dict(slippage_guard_threshold=0.12, entry_confidence_threshold=0.1)
    assert _id_eval(_signal(volatility=0.05), **guard).approved
    assert "slippage" in _id_eval(_signal(volatility=0.05), id_slippage_gain=3.0, **guard).reason
    # cap: volatility 0.5 saturates the estimate at the cap, reported in the rejection reason
    assert "0.2000 exceeds" in _id_eval(_signal(volatility=0.5)).reason
    assert "0.3000 exceeds" in _id_eval(_signal(volatility=0.5), id_slippage_cap=0.3).reason


@pytest.mark.parametrize("conf,name,value,factor", [
    (0.8, "id_reward_gain", 6.0, 1.5), (0.8, "id_risk_gain", 3.0, 2 / 3),
    (0.02, "id_reward_floor", 0.10, 2.0), (0.95, "id_risk_floor", 0.20, 0.5),
])
def test_reward_risk_controls_scale_ratio(conf, name, value, factor):
    base = dict(entry_confidence_threshold=0.02)
    d0 = _id_eval(_signal(confidence=conf), **base)
    d1 = _id_eval(_signal(confidence=conf), **base, **{name: value})
    assert d1.risk_reward_ratio == pytest.approx(d0.risk_reward_ratio * factor)


def test_id_trace_reports_effective_controls():
    box = HMMIntelligentDiscriminationBox()
    box._current_regime = lambda *_: "calm"
    _, trace = box.evaluate(_signal(), cfg(id_reward_gain=5.0), 1000.0)
    assert {t.parameter: t.value for t in trace}["id_reward_gain"] == 5.0


# ------------------------------------------- BB05 state / cache correctness

def test_configure_refreshes_window_keeps_memory_and_invalidates_only_model_changes(fake_hmm):
    box = HMMIntelligentDiscriminationBox(config=cfg())
    _run(box, 70)
    history = list(box._bar_history["S"])
    box.configure(cfg(id_refit_every_bars=30))          # cadence only: no invalidation
    assert "S" in box._cached_model and "S" in box._cached_posterior
    box.configure(cfg(id_refit_every_bars=30, id_feature_window=120))
    assert box._bar_history["S"].maxlen == 120 and list(box._bar_history["S"]) == history
    assert "S" not in box._cached_model                  # window changes the features: refit required
    with pytest.raises(ValueError):
        box.configure(cfg(id_feature_window=5))
    assert box.config.require("id_feature_window") == 120  # failed configure changes nothing


def test_state_count_is_structural():
    with pytest.raises(ValueError):
        HMMIntelligentDiscriminationBox(hmm_states=3)


# ------------------------------------------------------------ BB06 behavior

def _primed_plan(config, box=None, symbol_conf=(0.5, 0.9)):
    box = box or SimplePIDModelPredictiveControlBox()
    out = None
    for c in symbol_conf:
        out = box.build_plan(_signal(confidence=c), _decision(c), 1000.0, 8.0, config)
    return box, out


def test_entry_price_gain_and_base_slippage_move_the_plan():
    _, (p0, i0, _) = _primed_plan(cfg())
    assert i0["entry_adjustment"] != 0
    _, (p1, _, _) = _primed_plan(cfg(mpc_entry_price_gain=0.002))
    _, (p2, _, _) = _primed_plan(cfg(mpc_base_slippage_fraction=0.001))
    assert p1.entry_price != p0.entry_price and p2.entry_price > p0.entry_price
    delta = p2.entry_price - p0.entry_price
    assert delta == pytest.approx(1000.0 * (1 + i0["entry_adjustment"] * 0.001) * (0.001 - 0.0005), abs=0.006)  # paper fills round to paise


def test_schedule_gains_change_tier3_output_and_reach_build_plan(monkeypatch):
    base = DynamicParameterController.get_tier3_pid_schedule(1.0, 1.0, 1.0, 0.5, config=cfg())
    assert base == pytest.approx((1 + 1.2 * 0.5, 1 / (1 + 2.0 * 0.5), 1 + 0.8 * 0.5))
    for name, idx in (("mpc_schedule_kp_gain", 0), ("mpc_schedule_ki_gain", 1), ("mpc_schedule_kd_gain", 2)):
        moved = DynamicParameterController.get_tier3_pid_schedule(1.0, 1.0, 1.0, 0.5, config=cfg(**{name: REG.params[name].maximum}))
        assert moved[idx] != base[idx]
    seen = []
    real = DynamicParameterController.get_tier3_pid_schedule
    monkeypatch.setattr(DynamicParameterController, "get_tier3_pid_schedule",
                        staticmethod(lambda *a, **k: seen.append(k.get("config")) or real(*a, **k)))
    config = cfg()
    _primed_plan(config)
    assert seen and all(c is config for c in seen)


def _bars(n=80):
    rng = np.random.default_rng(3)
    close = 1000 * np.cumprod(1 + rng.normal(0, 0.004, n))
    return pd.DataFrame({"close": close, "high": close * 1.004, "low": close * 0.996})


def _env(nv, close=1000.0):
    return MarketEnvironmentState(symbol="S", close=close, current_atr=5.0, historical_atr_sma=5.0,
                                  regime_probs={"trend": 1.0}, normalized_vol=nv)


def test_environment_and_tier1_controls():
    bars = _bars()
    base = MarketEnvironmentState.from_bars("S", bars, 79, config=cfg())
    lookback = MarketEnvironmentState.from_bars("S", bars, 79, config=cfg(mpc_environment_lookback=30))
    period = MarketEnvironmentState.from_bars("S", bars, 79, config=cfg(mpc_range_atr_period=7))
    assert lookback.normalized_vol != base.normalized_vol and period.normalized_vol != base.normalized_vol
    short = MarketEnvironmentState.from_bars("S", bars, 5, config=cfg(mpc_range_atr_period=28))
    short2 = MarketEnvironmentState.from_bars("S", bars, 5, config=cfg(mpc_range_atr_period=28, mpc_range_fallback_fraction=0.02))
    assert short.current_atr == pytest.approx(short.close * 0.01)     # short history: fraction fallback
    assert short2.current_atr == pytest.approx(short2.close * 0.02)
    env = _env(1.5)
    t = DynamicParameterController.get_tier1_vol_parameters(env, config=cfg())
    g = DynamicParameterController.get_tier1_vol_parameters(env, config=cfg(mpc_atr_floor_gain=0.008))
    s = DynamicParameterController.get_tier1_vol_parameters(env, config=cfg(mpc_slippage_vol_gain=1.0))
    b = DynamicParameterController.get_tier1_vol_parameters(env, config=cfg(mpc_base_slippage_fraction=0.001))
    assert g["min_atr_floor"] > t["min_atr_floor"]
    assert s["dynamic_slippage"] > t["dynamic_slippage"] and b["dynamic_slippage"] == pytest.approx(2 * t["dynamic_slippage"])
    with pytest.raises(ValueError):
        DynamicParameterController.get_tier1_vol_parameters(_env(1.0, close=float("nan")), config=cfg())


def test_safety_envelope_still_clips_tier1():
    env = _env(2.5)
    t = DynamicParameterController.get_tier1_vol_parameters(env, config=cfg(mpc_atr_floor_gain=0.01))
    assert t["min_atr_floor"] == pytest.approx(1000.0 * 0.0120)  # fixed envelope upper bound, not a parameter


# ------------------------------------------- BB06 stale/cached-state fixes

def test_pid_clamp_and_window_follow_config_without_losing_state():
    box = SimplePIDModelPredictiveControlBox()
    _primed_plan(cfg(), box=box, symbol_conf=(0.5, 0.6, 0.7))
    entry_pid = box._entry_pids["S"]
    history = list(box._confidence_history["S"])
    _primed_plan(cfg(pid_integral_max_clamp=0.05, pid_integral_window_bars=REG.params["pid_integral_window_bars"].maximum),
                 box=box, symbol_conf=(0.95,))
    assert box._entry_pids["S"] is entry_pid                       # controller memory preserved
    assert entry_pid.output_limits == (-0.05, 0.05)                # clamp refreshed
    assert box._confidence_history["S"].maxlen == REG.params["pid_integral_window_bars"].maximum
    assert list(box._confidence_history["S"])[:len(history)] == history


def test_exit_controller_configure_propagates_gains_clamp_window_and_keeps_pids():
    ctl = ContinuousExitController(0.12, 0.04, 0.06, 0.1, 1.0, config=cfg())
    state = ctl.open_position("BUY", 1000.0, 990.0, 1020.0, 60)
    for conf in (0.6, 0.5, 0.4):
        state = ctl.update("S", state, conf, 0.6, 1005.0, 5.0)
    pid, hist = ctl._pids["S"], list(ctl._confidence_history["S"])
    ctl.configure(cfg(pid_kp_exit=0.2, pid_ki_exit=0.07, pid_kd_exit=0.09, pid_integral_max_clamp=0.02,
                      trailing_stop_atr_mult=2.0, pid_integral_window_bars=15, saturation_exit_bars=7))
    state = ctl.update("S", state, 0.1, 0.6, 1006.0, 5.0)
    assert ctl._pids["S"] is pid and pid.tunings == (0.2, 0.07, 0.09)
    assert pid.output_limits == (-0.02, 0.02) and abs(state.last_telemetry["pa_output"]) <= 0.02 + 1e-12
    assert ctl._confidence_history["S"].maxlen == 15
    assert list(ctl._confidence_history["S"])[:len(hist)] == hist
    assert ctl.saturation_exit_bars == 7 and ctl.atr_droop_mult == 2.0
    with pytest.raises(ValueError):
        ctl.configure(cfg(pid_integral_max_clamp=99.0))


def test_time_decay_gain_and_shadow_gamma_change_exit_control():
    for gain in (0.25, 0.75):
        ctl = ContinuousExitController(0.12, 0.04, 0.06, 0.1, 1.0, config=cfg(mpc_time_decay_gain=gain))
        state = ctl.open_position("BUY", 1000.0, 990.0, 1020.0, 10)
        state = ctl.update("S", state, 0.6, 0.6, 1001.0, 5.0, held_bars=10)  # at maximum hold
        assert state.last_telemetry["time_tightness"] == pytest.approx(1.0 - gain)
    outs = {}
    for gamma in (0.4, 1.0):
        ctl = ContinuousExitController(0.12, 0.04, 0.06, 0.1, 1.0, config=cfg(mpc_shadow_r_gamma=gamma))
        state = ctl.open_position("BUY", 1000.0, 990.0, 1020.0, 10)
        state.bars_held = 5
        outs[gamma] = ctl.update_shadow_r_trajectory(state, 1000.0)["shadow_gamma"]
    assert outs == {0.4: 0.4, 1.0: 1.0}


def test_shadow_gamma_does_not_change_live_stop():
    stops = []
    for gamma in (0.4, 1.0):
        ctl = ContinuousExitController(0.12, 0.04, 0.06, 0.1, 1.0, config=cfg(mpc_shadow_r_gamma=gamma))
        state = ctl.open_position("BUY", 1000.0, 990.0, 1020.0, 10)
        state = ctl.update("S", state, 0.6, 0.6, 1003.0, 5.0)
        ctl.update_shadow_r_trajectory(state, 1003.0)
        stops.append(state.current_stop_price)
    assert stops[0] == stops[1]


# --------------------------------------------------------- supervisory bridge

def test_bb05_bb06_snapshot_is_information_only():
    from revision2.contracts import TradePlan
    bridge = Revision5SupervisoryBridge.__new__(Revision5SupervisoryBridge)
    plan = TradePlan("BUY", 1000.5, 990.0, 1020.0, 3, 30)
    snap = bridge.snapshot_bb05_bb06(symbol="S", decision=_decision(), plan=plan)
    assert snap.authority == "INFORMATION_ONLY" and snap.proposed_side == "BUY"
    with pytest.raises(Exception):
        snap.proposed_side = "SELL"                     # frozen
    assert not any(hasattr(snap, a) for a in ("execute", "order", "quantity"))
    with pytest.raises(ValueError):
        bridge.snapshot_bb05_bb06(symbol="S", decision=IDDecision(False, "no", 0.5, 1.0, 0.1), plan=plan)
    empty = bridge.snapshot_bb05_bb06(symbol="S", decision=IDDecision(False, "no", 0.5, 1.0, 0.1))
    assert empty.proposed_side is None and empty.minimum_hold_bars is None
