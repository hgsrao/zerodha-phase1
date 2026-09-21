"""Revision-5 BB07 (SafetyGates) / BB08 (PositionManager) parameterization.

These tests prove RUNTIME behavior: every parameter is followed from its
canonical owner to the code that actually consumes it, and the observable
behavior is asserted on both sides of the threshold.  Registry membership
alone is never treated as proof.
"""
from dataclasses import replace
from datetime import datetime
from types import MappingProxyType

import pandas as pd
import pytest

import revision2_external.orchestrator as orch_module
import revision2_external.position_sizing_pyportfolioopt as sizing_module
from calibration_config import Revision2ParameterManifest
from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import (
    EntryDecisionEngine,
    Gate17MarketClose,
    Gate18CircuitBreaker,
    SafetyGateConfig,
    SystemState,
)
from revision2.calibration_supervisor import trading_search_space
from revision2.contracts import EffectiveConfig, IDDecision, PASignal, SafetyContract, TradePlan
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision2_external.position_sizing_pyportfolioopt import (
    PyPortfolioOptPositionManagerBox,
    compute_portfolio_weights,
)

REGISTRY = CanonicalParameterRegistry()

BB08_PARAMS = {
    "portfolio_weight_refit_bars": (500, 60, 2000),
    "portfolio_weight_lookback_minute_bars": (2000, 500, 10000),
    "portfolio_min_15min_observations": (100, 30, 500),
    "portfolio_aggressive_scale": (1.5, 1.0, 2.0),
    "portfolio_optimizer_risk_free_rate": (0.0, -0.05, 0.20),
}
BB07_PARAMS = {"max_broker_offline_seconds": 300, "force_close_time": "15:25"}


def cfg(**overrides):
    values = {n: s.default for n, s in REGISTRY.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=REGISTRY.FROZEN_IDENTITY_SHA256)


def with_safety(orch, **overrides):
    values = dict(orch.safety_contract.values)
    values.update(overrides)
    return replace(orch.safety_contract, values=MappingProxyType(values))


def set_config(orch, **overrides):
    values = dict(orch.config.as_dict())
    values.update(overrides)
    orch.config = EffectiveConfig.build(values, registry_hash=REGISTRY.FROZEN_IDENTITY_SHA256)


# ---------------------------------------------------------------- contract --

def test_canonical_counts_and_frozen_identity():
    assert REGISTRY.total_target_surface() == 141
    assert len(REGISTRY.safety_params) == 22
    assert len(REGISTRY.fixed_target_names()) == 33
    assert len(REGISTRY.calibratable_names()) == 108
    assert REGISTRY.identity_sha256() == REGISTRY.FROZEN_IDENTITY_SHA256 == (
        "965184f27855c1f8eb7e5fc7f29cd5391e92e07aa074308778f6403f751cc6f9"
    )


def test_default_behavior_equivalence_and_single_owner():
    """Explicit parameters keep the exact prior operational values."""
    for name, (default, lo, hi) in BB08_PARAMS.items():
        spec = REGISTRY.get(name)
        assert (spec.default, spec.minimum, spec.maximum) == (default, lo, hi)
        assert spec.calibratable is False
        assert spec.black_box == "PositionManager"
        assert "ENGINEERING_INITIAL_VALUE" in spec.notes
        assert "NOT_CALIBRATED" in spec.notes
        assert name in Revision2ParameterManifest.all_68()
        assert name not in REGISTRY.safety_params
    for name, default in BB07_PARAMS.items():
        spec = REGISTRY.safety_params[name]
        assert spec.default == default
        assert spec.calibratable is False
        assert spec.black_box == "SafetyGates"
        assert "FIXED_SAFETY_ENVELOPE" in spec.notes
        assert name in Revision2ParameterManifest.hardcoded_20()
        assert name not in REGISTRY.params
    # The dataclass defaults in gates_framework agree with the owner.
    gate_defaults = SafetyGateConfig()
    assert gate_defaults.max_broker_offline_seconds == 300
    assert gate_defaults.force_close_time == "15:25"
    # The old module-level constants are gone (single owner).
    assert not hasattr(orch_module, "PORTFOLIO_WEIGHT_REFIT_EVERY_BARS")
    assert not hasattr(orch_module, "PORTFOLIO_WEIGHT_LOOKBACK_MINUTE_BARS")
    assert not hasattr(sizing_module, "MIN_15MIN_PRICE_OBSERVATIONS")


def test_no_bb07_bb08_parameter_is_on_the_optuna_search_surface():
    # Engine-scoped optimizer surfaces (integration: engine is now mandatory).
    for engine in ("IN_HOUSE", "EXTERNAL"):
        searched = set(trading_search_space(REGISTRY, engine=engine).names)
        calibratable = set(REGISTRY.calibratable_names(engine))
        for name in list(BB08_PARAMS) + list(BB07_PARAMS):
            assert name not in searched
            assert name not in calibratable
        # Calibration payloads may not override them either.
        for name in BB08_PARAMS:
            assert REGISTRY.validate_calibration_payload({name: REGISTRY.get(name).default}, engine=engine)


def test_structural_constants_are_retained_not_parameterized():
    m = sizing_module
    assert m.TRADING_DAYS_PER_YEAR == 252
    assert m.PORTFOLIO_15MIN_PERIODS_PER_DAY == 25
    assert m.INTRADAY_15MIN_PERIODS_PER_YEAR == 252 * 25
    assert m.PORTFOLIO_RESAMPLE_RULE == "15min"
    assert m.MIN_PORTFOLIO_ASSETS == 2
    assert m.PYPORTFOLIOOPT_WEIGHT_BOUNDS == (0.0, 1.0)
    assert m.PYPORTFOLIOOPT_CLEAN_CUTOFF == 0.0001
    assert m.PYPORTFOLIOOPT_CLEAN_ROUNDING == 5
    assert m.MAX_CONVICTION_DERATE == 1.0
    for name in ("MIN_PORTFOLIO_ASSETS", "PYPORTFOLIOOPT_CLEAN_CUTOFF", "MAX_CONVICTION_DERATE"):
        assert name.lower() not in REGISTRY.params


# ------------------------------------------- BB07: max_broker_offline_seconds --

def test_broker_offline_owner_contract_and_gate_config_propagation():
    orch = Engine(["INFY"])
    assert orch.safety_contract.values["max_broker_offline_seconds"] == 300
    assert orch._build_safety_gate_config().max_broker_offline_seconds == 300
    assert orch.entry_decision_engine.config.max_broker_offline_seconds == 300

    orch.safety_contract = with_safety(orch, max_broker_offline_seconds=45)
    rebuilt = orch._build_safety_gate_config()
    assert rebuilt.max_broker_offline_seconds == 45
    # A gate built from that config actually consumes the propagated value.
    engine = EntryDecisionEngine(config=rebuilt)
    gate18 = next(g for g in engine.gates if isinstance(g, Gate18CircuitBreaker))
    assert gate18.config.max_broker_offline_seconds == 45


def test_broker_offline_gate_behavior_below_at_and_beyond_threshold():
    orch = Engine(["INFY"])
    gate = Gate18CircuitBreaker(orch._build_safety_gate_config())
    limit = orch.safety_contract.values["max_broker_offline_seconds"]

    def decide(connected, offline):
        return gate.evaluate(SystemState(broker_connected=connected, broker_offline_seconds=offline))

    assert decide(False, limit - 1).passed          # below threshold
    assert decide(False, limit).passed              # source semantics: strictly greater trips
    beyond = decide(False, limit + 1)
    assert not beyond.passed and "broker offline" in beyond.reason
    assert decide(True, limit + 1000).passed        # connected broker never trips this branch

    # Changing the contract value moves the trip point.
    orch.safety_contract = with_safety(orch, max_broker_offline_seconds=10)
    tight = Gate18CircuitBreaker(orch._build_safety_gate_config())
    assert tight.evaluate(SystemState(broker_connected=False, broker_offline_seconds=10)).passed
    assert not tight.evaluate(SystemState(broker_connected=False, broker_offline_seconds=11)).passed


# ---------------------------------------------------- BB07: force_close_time --

def test_force_close_owner_contract_and_gate_config_propagation():
    orch = Engine(["INFY"])
    assert orch.safety_contract.values["force_close_time"] == "15:25"
    assert orch._build_safety_gate_config().force_close_time == "15:25"
    assert orch.entry_decision_engine.config.force_close_time == "15:25"

    orch.safety_contract = with_safety(orch, force_close_time="15:10")
    assert orch._build_safety_gate_config().force_close_time == "15:10"


def test_force_close_gate_behavior_before_at_and_after_boundary():
    orch = Engine(["INFY"])
    gate = Gate17MarketClose(orch._build_safety_gate_config())

    def action(hhmm):
        h, m = map(int, hhmm.split(":"))
        return gate.evaluate(datetime(2024, 1, 1, h, m))[1]

    assert action("15:24") == "NO_ENTRY"       # immediately before boundary: past entry cutoff only
    assert action("15:25") == "FORCE_CLOSE"    # source semantics: >= triggers
    assert action("15:26") == "FORCE_CLOSE"
    assert action("10:00") == "ALLOW_ENTRY"


def test_force_close_orchestrator_exit_consumes_gate_config():
    """_maybe_exit force-closes via entry_decision_engine.config, i.e. the
    value that _build_safety_gate_config() propagated."""
    def run_exit(force_close, hhmm):
        orch = Engine(["INFY"])
        orch.safety_contract = with_safety(orch, force_close_time=force_close)
        orch.entry_decision_engine = EntryDecisionEngine(config=orch._build_safety_gate_config())
        fill = orch.broker.place_order("INFY", "BUY", 10, "MARKET", 100.0,
                                       orch.safety_contract.as_dict(), orch.registry)
        assert fill["passed"]
        entry = fill["filled_price"]
        orch.open_trades["INFY"] = dict(
            side="BUY", entry_price=entry, quantity=10, stop_price=entry - 5,
            target_price=entry + 10, minimum_hold_bars=2, maximum_hold_bars=60,
            entry_timestamp="2024-01-01 10:00", entry_atr=1.0, planned_entry_price=entry,
            planned_stop_price=entry - 5, planned_target_price=entry + 10)
        orch._exit_controller_states["INFY"] = orch.exit_controller.open_position(
            "BUY", entry, entry - 5, entry + 10, 60)
        orch.id_box._current_regime = lambda *a: "calm"
        bar = dict(open=100.0, high=100.5, low=99.5, close=100.0)
        signal = PASignal(symbol="INFY", timestamp="t", direction=1, confidence=.8, momentum=.5,
                          volatility=.01, vwap_deviation=.1, volume_confirmation=.2, exit_confidence=.8)
        orch._maybe_exit("INFY", f"2024-01-01 {hhmm}", bar, signal, 1, False, .8)
        return orch

    closed = run_exit("15:10", "15:10")
    assert not closed.open_trades and closed.completed_trades[-1]["reason"] == "force_close_time"
    still_open = run_exit("15:10", "15:09")
    assert still_open.open_trades
    assert not any(t["reason"] == "force_close_time" for t in still_open.completed_trades)


# -------------------------------------------------- BB08: refit / lookback --

def _minute_bars(n_sessions=2, per_session=375, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    frames = []
    for d in range(n_sessions):
        start = pd.Timestamp("2024-01-01 09:15", tz="Asia/Kolkata") + pd.Timedelta(days=d)
        ts = pd.date_range(start, periods=per_session, freq="min")
        close = 1000.0 + np.cumsum(rng.normal(0, 0.2, per_session)) + d
        frames.append(pd.DataFrame({"timestamp": ts, "open": close, "high": close + .3,
                                    "low": close - .3, "close": close, "volume": 1000.0}))
    return pd.concat(frames, ignore_index=True)


def _run_with_spy(monkeypatch, **overrides):
    calls = []

    def spy(price_history, *, min_observations, risk_free_rate):
        calls.append({"history": {s: p.copy() for s, p in price_history.items()},
                      "min_observations": min_observations, "risk_free_rate": risk_free_rate})
        return {s: 1.0 / len(price_history) for s in price_history}

    monkeypatch.setattr(orch_module, "compute_portfolio_weights", spy)
    orch = Engine(["INFY", "TCS"])
    set_config(orch, **overrides)
    neutral = PASignal(symbol="INFY", timestamp="t", direction=0, confidence=0., momentum=0.,
                       volatility=.01, vwap_deviation=0., volume_confirmation=0., exit_confidence=0.)
    monkeypatch.setattr(orch.pa, "evaluate", lambda snapshot, c: (neutral, []))
    monkeypatch.setattr(orch.id_box, "_current_regime", lambda *a: "calm")
    frames = {"INFY": _minute_bars(seed=1), "TCS": _minute_bars(seed=2)}
    orch.run(frames, warmup=60)
    return orch, calls, frames


def test_refit_bars_controls_when_portfolio_optimizer_is_invoked(monkeypatch):
    _, calls_60, _ = _run_with_spy(monkeypatch, portfolio_weight_refit_bars=60,
                                   portfolio_weight_lookback_minute_bars=500)
    _, calls_120, _ = _run_with_spy(monkeypatch, portfolio_weight_refit_bars=120,
                                    portfolio_weight_lookback_minute_bars=500)
    # 750 bars, warmup 60 -> 689 ticks. Refit at tick k*R; window needs >=500
    # bars (bar index warmup+k*R-1 >= 499).  R=60: k=10..; R=120: k=5..
    assert len(calls_60) > len(calls_120) > 0
    # cadence: consecutive invocations are exactly R ticks (= R minute bars) apart
    def gaps(calls, r):
        ends = [c["history"]["INFY"].index[-1] for c in calls]
        return {(b - a) // pd.Timedelta(minutes=1) for a, b in zip(ends, ends[1:])}, ends
    g60, _ = gaps(calls_60, 60)
    g120, _ = gaps(calls_120, 120)
    assert g60 == {60}
    assert g120 == {120}


def test_lookback_bars_controls_history_supplied_and_leaks_no_future(monkeypatch):
    for lookback in (500, 640):
        _, calls, frames = _run_with_spy(monkeypatch, portfolio_weight_refit_bars=60,
                                         portfolio_weight_lookback_minute_bars=lookback)
        assert calls
        for call in calls:
            for symbol, series in call["history"].items():
                assert len(series) == lookback
                bars = frames[symbol]
                cutoff = series.index[-1]
                # exactly the trailing `lookback` completed bars up to the tick
                expected = bars[bars["timestamp"] <= cutoff].tail(lookback)
                assert list(series.index) == list(expected["timestamp"])
                assert list(series.values) == list(expected["close"])
                # nothing beyond the tick timestamp is present
                assert (series.index <= cutoff).all()
                assert cutoff < bars["timestamp"].iloc[-1]


def test_min_observations_and_risk_free_rate_are_forwarded_by_the_orchestrator(monkeypatch):
    _, calls, _ = _run_with_spy(monkeypatch, portfolio_weight_refit_bars=60,
                                portfolio_weight_lookback_minute_bars=500,
                                portfolio_min_15min_observations=37,
                                portfolio_optimizer_risk_free_rate=0.035)
    assert calls
    assert {c["min_observations"] for c in calls} == {37}
    assert {c["risk_free_rate"] for c in calls} == {0.035}


# ---------------------------------------- BB08: min observations threshold --

def _series_with_completed_15min(n_completed):
    """One-minute closes whose causal completed-15-min count is n_completed."""
    minutes = (n_completed + 1) * 15  # trailing incomplete bucket is dropped
    idx = pd.date_range("2024-01-01 09:15", periods=minutes + 1, freq="min")
    import numpy as np
    rng = np.random.default_rng(3)
    a = pd.Series(1000 + np.cumsum(rng.normal(0, .5, len(idx))), index=idx)
    b = pd.Series(500 + np.cumsum(rng.normal(0, .5, len(idx))), index=idx)
    return {"A": a, "B": b}


def test_min_observations_boundary_equal_weight_fallback_vs_optimizer(monkeypatch):
    invoked = []

    class SpyFrontier:
        def __init__(self, mu, cov, *, weight_bounds):
            pass

        def max_sharpe(self, *, risk_free_rate):
            invoked.append(risk_free_rate)
            return {"A": 0.9, "B": 0.1}

        def clean_weights(self, *, cutoff, rounding):
            return {"A": 0.9, "B": 0.1}

    monkeypatch.setattr(sizing_module, "EfficientFrontier", SpyFrontier)
    prices = _series_with_completed_15min(60)
    n = len(sizing_module._causal_15min_close_prices(prices))
    assert n >= 40

    # below requirement -> equal weight, optimizer never touched
    below = compute_portfolio_weights(prices, min_observations=n + 1, risk_free_rate=0.0)
    assert below == {"A": 0.5, "B": 0.5} and invoked == []
    # exactly at requirement -> optimizer path executes
    at = compute_portfolio_weights(prices, min_observations=n, risk_free_rate=0.0)
    assert at == {"A": 0.9, "B": 0.1} and len(invoked) == 1


# --------------------------------------------- BB08: optimizer risk-free rate --

@pytest.mark.parametrize("rate", [0.0, 0.0625, -0.02])
def test_risk_free_rate_reaches_efficient_frontier_max_sharpe(monkeypatch, rate):
    seen = {}

    class SpyFrontier:
        def __init__(self, mu, cov, *, weight_bounds):
            pass

        def max_sharpe(self, *, risk_free_rate):
            seen["rf"] = risk_free_rate
            return {"A": 0.5, "B": 0.5}

        def clean_weights(self, *, cutoff, rounding):
            return {"A": 0.5, "B": 0.5}

    monkeypatch.setattr(sizing_module, "EfficientFrontier", SpyFrontier)
    compute_portfolio_weights(_series_with_completed_15min(60), min_observations=30,
                              risk_free_rate=rate)
    assert seen["rf"] == rate


def test_compute_portfolio_weights_has_no_hidden_defaults():
    prices = _series_with_completed_15min(60)
    with pytest.raises(TypeError):
        compute_portfolio_weights(prices)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        compute_portfolio_weights(prices, min_observations=30)  # type: ignore[call-arg]


# ------------------------------------------------- BB08: aggressive scale --

def _size(mode, scale, weights, equity=1_000_000.0, per_symbol_cap=1.0, symbol="INFY"):
    plan = TradePlan("BUY", 100.0, 95.0, 110.0, 2, 60)
    box = PyPortfolioOptPositionManagerBox()
    qty, _ = box.size(
        plan, equity, 1.0,
        cfg(capital_allocation_mode=mode, portfolio_aggressive_scale=scale),
        symbol, weights, per_symbol_cap,
    )
    return qty, box.last_sizing_telemetry


def test_aggressive_scale_is_inert_in_equal_mode_and_active_in_aggressive_mode():
    weights = {"INFY": .5, "TCS": .5}
    eq_lo, _ = _size("equal", 1.0, weights)
    eq_hi, _ = _size("equal", 2.0, weights)
    assert eq_lo == eq_hi > 0

    ag_lo, t_lo = _size("aggressive", 1.0, weights)
    ag_mid, t_mid = _size("aggressive", 1.5, weights)
    ag_hi, t_hi = _size("aggressive", 2.0, weights)
    assert ag_lo == eq_lo                                   # scale 1.0 == equal sizing
    assert ag_lo < ag_mid < ag_hi                           # scale changes sizing
    assert t_mid["base_risk_budget"] == pytest.approx(1.5 * t_lo["base_risk_budget"])
    assert t_hi["base_risk_budget"] == pytest.approx(2.0 * t_lo["base_risk_budget"])


def test_optimizer_weight_never_amplifies_risk_and_only_derates():
    equal = {"INFY": .5, "TCS": .5}
    base, t_base = _size("equal", 1.0, equal)
    # Optimizer strongly favors INFY: must NOT exceed the ATR-derived base budget.
    favored, t_fav = _size("equal", 1.0, {"INFY": .95, "TCS": .05})
    assert favored == base
    assert t_fav["conviction_derate"] == 1.0
    assert t_fav["derated_risk_budget"] == pytest.approx(t_fav["base_risk_budget"])
    # Optimizer disfavors INFY: derates.
    disfavored, t_dis = _size("equal", 1.0, {"INFY": .10, "TCS": .90})
    assert disfavored < base
    assert t_dis["conviction_derate"] == pytest.approx(.2)
    assert t_dis["derated_risk_budget"] < t_dis["base_risk_budget"]
    # Zero weight -> zero risk.
    assert _size("equal", 1.0, {"INFY": 0.0, "TCS": 1.0})[0] == 0


def test_final_safety_exposure_ceiling_stays_authoritative_at_max_scale():
    weights = {"INFY": .5, "TCS": .5}
    equity, cap_fraction = 1_000_000.0, 0.02
    usable = equity * (1.0 - REGISTRY.get("min_capital_buffer_fraction").default)
    cap_qty = int((usable * cap_fraction) // 100.0)
    qty, tele = _size("aggressive", 2.0, weights, equity=equity, per_symbol_cap=cap_fraction)
    assert tele["raw_quantity"] > cap_qty                   # scaled risk wants more...
    assert qty == tele["safety_max_quantity"] == cap_qty    # ...but the safety cap wins
    assert qty * 100.0 <= usable * cap_fraction
