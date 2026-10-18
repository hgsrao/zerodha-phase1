"""Focused tests for the eleven governor parameters and the capacity allocator."""
from dataclasses import replace, asdict
import inspect
import json
import math
import pytest

from .r7_state import ControlConfig, Context, DROOP_LIMITS, default_configs, registry_bay_settings, validation_config
from .r7_allocator import Utilization, approve_target, droop_weights, allocate_headroom, increase_demand_pu
from .r7_market_governor import MarketGovernor
from .r7_coordinated_controller import SymbolController
from .conftest import make_bar, context
from revision5.topology import FLEET_TOPOLOGY, SYMBOL_TO_BAY


# ---- Parameter 1: three-source signed reference ----------------------------------
def test_p1_reference_is_weighted_sum_of_signed_inputs(config):
    c = SymbolController(config)
    d = c.step(make_bar(0), Context(0.0, 0.8, -0.6))
    assert d.reference == pytest.approx(config.avr_weight * d.avr.score + 0.3 * 0.8 + 0.2 * -0.6)
    with pytest.raises(ValueError):
        Context(0.0, 1.2, 0.0)
    with pytest.raises(ValueError):
        Context(0.0, float('nan'), 0.0)


# ---- Parameter 2: common portfolio utilization -----------------------------------
def test_p2_utilization_is_not_clamped_and_counts_pending():
    u = Utilization(1_400_000, 100_000, 1_000_000)
    assert u.actual == pytest.approx(1.4)
    assert u.projected == pytest.approx(1.5)
    assert u.headroom_notional == 0
    u = Utilization(300_000, 200_000, 1_000_000)
    assert (u.actual, u.projected, u.headroom_notional) == (pytest.approx(0.3), pytest.approx(0.5), pytest.approx(500_000))


def test_p2_governor_and_controller_take_no_price_speed_input(config):
    assert 'market_speed_pu' not in inspect.signature(MarketGovernor.step).parameters
    src = inspect.getsource(MarketGovernor)
    assert 'speed_droop_command' not in src


def test_p2_ordinary_price_trend_does_not_saturate_target(config):
    # A strong steady 1%-per-bar rally with neutral studies and dispatch: the old
    # 1/R price-speed path drove the target to full scale. Now only the AVR term
    # (weight 0.5) can contribute, so the reference stays bounded by 0.5.
    c = SymbolController(config)
    for i in range(60):
        d = c.step(make_bar(i, close=100 * 1.01 ** i), Context(0.0, 0.0, 0.0))
        assert abs(d.target) <= config.avr_weight + 1e-12


# ---- Parameter 3: settable droop and droop-inspired sharing ----------------------
def test_p3_registry_bay_droops_commissioned():
    configs = {c.bay_id: c.droop_r for c in default_configs(list(SYMBOL_TO_BAY))}
    assert configs == {'GTG1_HEAVY_INDUSTRY': 0.04, 'GTG2_TECH_TELECOM': 0.04, 'CSTG1_BFSI': 0.055,
                       'CSTG2_CONSUMER_AUTO': 0.055, 'BPSTG_HEALTHCARE': 0.075}
    assert configs == {b: v['droop_r'] for b, v in registry_bay_settings().items()}


@pytest.mark.parametrize('r', [0.04, 0.055, 0.075])
def test_p3_settable_droop_accepted(config, r):
    assert replace(config, droop_r=r).droop_r == r


@pytest.mark.parametrize('r', [0.0, 0.005, 0.15, -0.04])
def test_p3_droop_outside_commissioning_limits_rejected(config, r):
    with pytest.raises(ValueError):
        replace(config, droop_r=r)
    assert DROOP_LIMITS == (0.02, 0.10)


def test_p3_budgets_from_registry_and_subset_does_not_reallocate():
    full = default_configs(list(SYMBOL_TO_BAY))
    assert sum(c.budget_fraction for c in full) == pytest.approx(1.0)
    one = default_configs(['INFY'])[0]
    assert one.budget_fraction == pytest.approx(0.20 / len(FLEET_TOPOLOGY['GTG2_TECH_TELECOM']))


def test_p3_weights_are_budget_over_r():
    a = ControlConfig('A', 'X', 0.1, droop_r=0.04)
    b = ControlConfig('B', 'Y', 0.1, droop_r=0.075)
    w = droop_weights([a, b])
    assert w['A'] / w['B'] == pytest.approx(0.075 / 0.04)


def test_p3_allocation_rules():
    assert approve_target(0.0, 0.7, 0.0) == 0.0                 # neutral never creates exposure
    assert approve_target(0.0, 0.0, None) == 0.0
    assert approve_target(-0.6, -0.2, 0.1) == pytest.approx(-0.3)
    assert approve_target(-0.6, -0.2, 0.0) == pytest.approx(-0.2)   # rising U: not more negative
    assert approve_target(0.8, -0.5, 0.0) == 0.0                # bullish never becomes short
    assert approve_target(0.8, -0.5, 0.3) == 0.0                # no direct reversal: close first
    assert approve_target(0.3, 0.9, 0.0) == pytest.approx(0.3)  # reduction allowed at zero headroom
    assert approve_target(1.0, 1.4, 0.0) == pytest.approx(1.0)  # over-budget long reduces


def test_p3_high_utilization_alone_creates_no_pulse(config):
    c = SymbolController(config)
    pulses = [c.step(make_bar(i, close=100.0), Context(0.0, 0.0, 0.0, increase_capacity=0.0)).pulse
              for i in range(40)]
    assert set(pulses) == {'HOLD'}


# ---- Parameter 4 and 5: signed unclamped exposure and tracking error -------------
@pytest.mark.parametrize('actual', [-1.4, -1.0, 0.0, 1.0, 1.4])
def test_p4_actual_exposure_preserved_and_error_signed(config, actual):
    g = MarketGovernor(config)
    out = g.step(0.5, actual, 60)
    assert out.actual == actual
    assert out.error == pytest.approx(0.5 - actual)


@pytest.mark.parametrize('target,actual,meaning', [
    (0.6, 0.0, 'long entry'), (-0.6, 0.0, 'short entry'),
    (0.2, 0.8, 'long reduction'), (-0.1, -0.7, 'short cover')])
def test_p5_error_is_exposure_adjustment(config, target, actual, meaning):
    out = MarketGovernor(config).step(target, actual, 60)
    assert out.error == pytest.approx(target - actual)


# ---- Parameter 6: proportional term independent of R ------------------------------
def test_p6_kp_independent_of_droop(config):
    a = MarketGovernor(replace(config, droop_r=0.04)).step(0.4, 0.1, 60)
    b = MarketGovernor(replace(config, droop_r=0.075)).step(0.4, 0.1, 60)
    assert a.p == b.p == pytest.approx(config.kp * 0.3)


# ---- Parameter 7: integral with anti-windup and execution freeze ------------------
def test_p7_integral_frozen_while_native_order_working(config):
    g = MarketGovernor(replace(config, command_slew_per_second=10))
    g.step(0.5, 0.0, 60)
    held = g.integral
    out = g.step(0.5, 0.0, 60, execution_blocked=True)
    assert out.integration_frozen and g.integral == held
    g.step(0.5, 0.0, 60)
    assert g.integral != held


def test_p7_zero_error_does_not_integrate(config):
    g = MarketGovernor(config)
    for _ in range(10):
        g.step(0.3, 0.3, 60)
    assert g.integral == 0


# ---- Parameter 8: derivative on measurement, optional filter, Kd=0 profile -------
def test_p8_no_derivative_kick_on_target_step(config):
    g = MarketGovernor(config)
    g.step(0.0, 0.2, 60)
    out = g.step(0.9, 0.2, 60)            # target jumps, measurement unchanged
    assert out.d == 0


def test_p8_filter_attenuates_fill_step(config):
    raw = MarketGovernor(config)
    filt = MarketGovernor(replace(config, derivative_tau_seconds=300))
    raw.step(0.5, 0.0, 60); filt.step(0.5, 0.0, 60)
    d_raw = raw.step(0.5, 0.4, 60).d          # a fill steps the measured exposure
    d_f = filt.step(0.5, 0.4, 60).d
    assert d_raw < 0 and d_raw < d_f < 0


def test_p8_validation_profile_kd_zero_keeps_baseline_default(config):
    v = validation_config(config)
    assert v.kd_seconds == 0 and config.kd_seconds == 0.5
    g = MarketGovernor(v)
    g.step(0.5, 0.0, 60)
    assert g.step(0.5, 0.4, 60).d == 0


# ---- Parameter 9 and 10: slew per second, lag, session time -----------------------
def test_p9_slew_scales_with_seconds(config):
    cfg = replace(config, kp=10, command_slew_per_second=0.004)
    g = MarketGovernor(cfg)
    g.step(1, 0, 60)
    assert g.command == pytest.approx(0.004 * 60)
    g2 = MarketGovernor(cfg)
    g2.step(1, 0, 120)
    assert g2.command == pytest.approx(0.004 * 120)


def test_p9_overnight_is_one_bar_of_session_time(config):
    from datetime import datetime, timedelta
    c = SymbolController(config)
    first = make_bar(0)
    nxt = replace(make_bar(1), timestamp=(datetime.fromisoformat(first.timestamp) + timedelta(hours=18)).isoformat())
    c.step(first, context())
    f = c.features.extract(nxt)
    assert f.dt_seconds == config.bar_seconds and f.gap


def test_p10_lag_step_response_monotone_and_bounded(config):
    g = MarketGovernor(replace(config, command_slew_per_second=10, ki_per_second=0, kd_seconds=0))
    outs = [g.step(0.6, 0.0, 60).applied for _ in range(20)]
    assert all(b >= a for a, b in zip(outs, outs[1:])) and max(outs) <= 1
    assert outs[0] == pytest.approx(min(1.0, 0.6 + config.kp * 0.6) * (1 - math.exp(-60 / config.market_response_tau_seconds)))


# ---- Parameter 11: physical inertia stays in R6 -----------------------------------
def test_p11_trading_governor_has_no_swing_equation():
    src = inspect.getsource(MarketGovernor)
    assert 'inertia' not in src and 'swing' not in src.lower().replace('swing equation) remains', '')


# ---- Checkpoint determinism with the new state -------------------------------------
def test_checkpoint_restore_reproduces_future_decisions(config):
    cfg = replace(config, derivative_tau_seconds=120)
    a = SymbolController(cfg)
    for i in range(25):
        a.step(make_bar(i), Context(0.1 * (i % 3), 0.7, 0.2, increase_capacity=0.4))
    snap = json.loads(json.dumps(a.snapshot()))
    assert snap['schema'] == 2
    b = SymbolController(cfg)
    b.restore(snap)
    for i in range(25, 50):
        ctx = Context(0.05, -0.4, 0.1, increase_capacity=0.2)
        assert asdict(a.step(make_bar(i), ctx)) == asdict(b.step(make_bar(i), ctx))


# ---- Engine integration of allocation and execution state -------------------------
def _engine(config):
    from .test_r7_engine_integration import TestEngine
    return TestEngine(['INFY'], r7_configs=[config])


@pytest.mark.parametrize('pu', [-1.4, -1.0, 0.0, 1.0, 1.4])
def test_engine_actual_exposure_unclamped(config, pu):
    e = _engine(config)
    close = 100.0
    e.quantity = pu * e._equity() * config.budget_fraction / close
    assert e._r7_actual('INFY', close) == pytest.approx(pu)


def test_engine_records_allocation_and_freezes_on_working_order(config):
    e = _engine(config)
    for i in range(10):
        e._r7_step('INFY', i, asdict(make_bar(i)), 0.8, True)
    rec = e.r7_decisions[-1]
    assert rec['allocation']['weight'] == pytest.approx(config.budget_fraction / config.droop_r)
    assert rec['allocation']['execution_blocked'] is False
    e._symbol_inflight = {'INFY': 'coid-1'}
    e._r7_step('INFY', 10, asdict(make_bar(10)), 0.8, True)
    assert e.r7_decisions[-1]['allocation']['execution_blocked'] is True
    assert e.r7_decisions[-1]['governor']['integration_frozen'] is True


def test_engine_b_pending_entry_counts_conservatively(config):
    e = _engine(config)
    e._last_close = {'INFY': 100.0}
    e._inflight = {'c1': {'kind': 'entry', 'symbol': 'INFY', 'requested': 30, 'filled': 10},
                   'c2': {'kind': 'exit', 'symbol': 'INFY', 'requested': 99, 'filled': 0}}
    # Full requested quantity is reserved until the entry completes (filled part of a
    # working Engine B partial is not yet in open_trades).
    assert e._r7_pending_increase_notional() == pytest.approx(3000.0)


def test_engine_full_headroom_used_zero_capacity_blocks_increase_only(config):
    e = _engine(config)
    e.quantity = 1000      # gross 100,000 = equity x limit (1.0): no headroom
    for i in range(10):
        e._r7_step('INFY', i, asdict(make_bar(i)), 0.9, True)
    rec = e.r7_decisions[-1]
    assert rec['allocation']['headroom_notional'] == 0
    assert rec['allocation']['increase_capacity_pu'] == 0
    assert rec['target'] <= max(rec['reference'], 0) + 1e-12


def test_engine_b_pending_entry_without_valid_price_fails_closed(config):
    from types import SimpleNamespace
    e = _engine(config)
    e._last_close = {}
    e._inflight = {'c1': {'kind': 'entry', 'symbol': 'INFY', 'requested': 30, 'filled': 0, 'intent': {}}}
    with pytest.raises(ValueError):
        e._r7_pending_increase_notional()
    for i in range(10):
        e._r7_step('INFY', i, asdict(make_bar(i)), 0.9, True)
    rec = e.r7_decisions[-1]
    assert rec['allocation']['increase_capacity_pu'] == 0
    assert rec['allocation']['utilization'] is None and rec['allocation']['measurement_error']
    assert rec['target'] == 0.0                      # flat: no new exposure authorized
    assert e.r7_gate_counts['UTILIZATION_UNAVAILABLE_FAIL_CLOSED'] >= 1
    # A valid planned price values the order conservatively instead.
    e._inflight['c1']['intent'] = {'plan': SimpleNamespace(entry_price=101.0)}
    e._last_close = {'INFY': 99.0}
    assert e._r7_pending_increase_notional() == pytest.approx(30 * 101.0)
    e._last_close = {'INFY': 0.0}
    assert e._r7_pending_increase_notional() == pytest.approx(30 * 101.0)


def test_fail_closed_still_allows_reduction(config):
    e = _engine(config)
    e._equity = lambda: 100000
    e.quantity = 50                     # long
    e._r7_gross_limit_notional = lambda: (_ for _ in ()).throw(ValueError('limit unavailable'))
    for i in range(10):
        e._r7_step('INFY', i, asdict(make_bar(i)), 0.0, True)
    rec = e.r7_decisions[-1]
    assert rec['allocation']['increase_capacity_pu'] == 0
    assert abs(rec['target']) <= abs(rec['actual']) + 1e-12


def test_checkpoint_size_bounded_and_external_log_complete(config, tmp_path):
    from hashlib import sha256
    from .test_r7_engine_integration import TestEngine
    log = tmp_path / 'decisions.jsonl'
    e = TestEngine(['INFY'], r7_configs=[config], r7_decision_log_path=str(log), r7_decision_memory=50)
    sizes = []
    for i in range(300):
        e._r7_step('INFY', i, asdict(make_bar(i)), 0.6, True)
        if i in (100, 299):
            sizes.append(len(json.dumps(e.r7_snapshot())))
    e.r7_close_decision_log()
    assert len(e.r7_decisions) == 50
    assert abs(sizes[1] - sizes[0]) < 200          # snapshot does not grow with bars
    lines = log.read_text().splitlines()
    assert len(lines) == 300 == e.r7_decision_count
    digest = '0' * 64
    for line in lines:
        digest = sha256((digest + line).encode()).hexdigest()
    assert digest == e.r7_decision_digest


def test_restore_continues_decision_chain(config, tmp_path):
    from .test_r7_engine_integration import TestEngine
    a = TestEngine(['INFY'], r7_configs=[config])
    for i in range(20):
        a._r7_step('INFY', i, asdict(make_bar(i)), 0.6, True)
    snap = json.loads(json.dumps(a.r7_snapshot()))
    b = TestEngine(['INFY'], r7_configs=[config])
    b.r7_restore(snap)
    for e in (a, b):
        for i in range(20, 30):
            e._r7_step('INFY', i, asdict(make_bar(i)), 0.6, True)
    assert (a.r7_decision_count, a.r7_decision_digest) == (b.r7_decision_count, b.r7_decision_digest)
    bad = dict(snap, decision_count=-1)
    with pytest.raises(ValueError):
        TestEngine(['INFY'], r7_configs=[config]).r7_restore(bad)
