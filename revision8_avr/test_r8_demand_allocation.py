"""Demand-aware, simultaneous, order-independent capital allocation (17 required cases)."""
from dataclasses import asdict
from types import SimpleNamespace
import json
import pytest

from .r8_allocator import Utilization, allocate_headroom, approve_target, droop_weights, increase_demand_pu
from .r8_engine import R8EngineMixin
from .r8_state import ControlConfig, default_configs
from .conftest import make_bar
from revision5.topology import SYMBOL_TO_BAY

EQUITY = 1_000_000.0


class MultiSeams:
    """Minimal native stand-in: per-symbol signed positions on a common ledger."""
    def __init__(self, symbols):
        self.symbols = symbols
        self.chart_studies = SimpleNamespace(evaluate=lambda s, b: {'direction': 1, 'confidence': 0.8})
        self.dynamic_profile_mode = 'off'
        self._current_bar_idx = 0
        self.positions = {s: 0.0 for s in symbols}
        self._last_close = {s: 100.0 for s in symbols}
        self.safety_contract = SimpleNamespace(values={'max_gross_exposure_fraction': 0.5,
                                                       'max_exposure_per_symbol_fraction': 0.15})
        self.grid = 0.006

    def _observe_conviction(self, *a):
        pass

    def _equity(self):
        return EQUITY

    def _broker_position(self, symbol):
        return {'quantity': self.positions[symbol]}

    def _gross_exposure_notional(self):
        return sum(abs(q) * self._last_close[s] for s, q in self.positions.items())

    def _governor_grid_return(self, ts):
        return self.grid

    def _record_controller_event(self, *a):
        pass


class Engine(R8EngineMixin, MultiSeams):
    __test__ = False


def engine(symbols, **kw):
    return Engine(symbols, r8_configs=default_configs(symbols), **kw)


def run_bars(e, n, study=0.9, order=None):
    for i in range(n):
        for s in (order or e.symbols):
            e._r8_queue(s, i, asdict(make_bar(i)), study, True)
        e.r8_flush()


def last(e, symbol):
    return [d for d in e.r8_decisions if d['symbol'] == symbol][-1]


# 1 --------------------------------------------------------------------------------
def test_01_one_active_symbol_empty_portfolio_not_capped_by_fixed_share():
    # BPSTG: the rejected fixed-share policy capped this bay at 0.323 pu even when empty.
    e = engine(['CIPLA'])
    run_bars(e, 30)
    d = last(e, 'CIPLA')
    assert d['allocation']['case'] == 'FITS'
    assert d['reference'] > 0.323
    assert d['target'] == pytest.approx(d['reference'])    # full reference approved
    assert d['allocation']['rejected_demand_notional'] == 0


# 2 --------------------------------------------------------------------------------
def test_02_several_symbols_fit_within_headroom():
    grants, case = allocate_headroom({'A': 100.0, 'B': 200.0, 'C': 50.0}, {'A': 1, 'B': 1, 'C': 1}, 1000.0)
    assert case == 'FITS' and grants == {'A': 100.0, 'B': 200.0, 'C': 50.0}


# 3 --------------------------------------------------------------------------------
def test_03_demand_exceeds_headroom_total_equals_headroom():
    grants, case = allocate_headroom({'A': 600.0, 'B': 600.0}, {'A': 1, 'B': 1}, 500.0)
    assert case == 'OVERSUBSCRIBED'
    assert sum(grants.values()) == pytest.approx(500.0)
    assert all(grants[s] <= 600.0 for s in grants)


# 4 --------------------------------------------------------------------------------
def test_04_droop_weights_set_oversubscribed_ratio():
    a = ControlConfig('A', 'X', 0.1, droop_r=0.04)
    b = ControlConfig('B', 'Y', 0.1, droop_r=0.075)
    w = droop_weights([a, b])
    grants, _ = allocate_headroom({'A': 1000.0, 'B': 1000.0}, w, 300.0)
    assert grants['A'] / grants['B'] == pytest.approx(0.075 / 0.04)


# 5 --------------------------------------------------------------------------------
def test_05_unclaimed_share_is_redistributed():
    grants, _ = allocate_headroom({'A': 10.0, 'B': 1000.0, 'C': 1000.0}, {'A': 5, 'B': 1, 'C': 1}, 300.0)
    assert grants['A'] == pytest.approx(10.0)              # small demand met in full
    assert grants['B'] == pytest.approx(145.0) and grants['C'] == pytest.approx(145.0)
    assert sum(grants.values()) == pytest.approx(300.0)


# 6 --------------------------------------------------------------------------------
def test_06_zero_headroom_grants_nothing():
    grants, case = allocate_headroom({'A': 10.0, 'B': 5.0}, {'A': 1, 'B': 1}, 0.0)
    assert case == 'OVERSUBSCRIBED' and grants == {'A': 0.0, 'B': 0.0}


# 7 --------------------------------------------------------------------------------
def test_07_portfolio_above_gross_limit_blocks_increases_allows_reduction():
    e = engine(['BEL', 'LT'])
    e.positions['LT'] = 6000.0                              # 600k gross > 500k limit
    run_bars(e, 30)
    assert e.r8_utilization(make_bar(29).timestamp).actual > 1.0   # unclamped overrun visible
    bel = last(e, 'BEL')
    assert bel['allocation']['headroom_notional'] == 0 and bel['target'] == 0.0
    lt = last(e, 'LT')
    assert abs(lt['target']) <= abs(lt['actual'])           # never increased


# 8 --------------------------------------------------------------------------------
def test_08_pending_entry_orders_reduce_headroom():
    e = engine(['BEL'])
    e._inflight = {'c1': {'kind': 'entry', 'symbol': 'BEL', 'requested': 1000, 'filled': 0, 'intent': {}}}
    u = e.r8_utilization('t1')
    assert u.pending_increase_notional == pytest.approx(100_000.0)
    assert u.headroom_notional == pytest.approx(400_000.0)


# 9 --------------------------------------------------------------------------------
def test_09_partial_fill_reserved_until_complete_and_cancel_releases():
    e = engine(['BEL'])
    e._inflight = {'c1': {'kind': 'entry', 'symbol': 'BEL', 'requested': 1000, 'filled': 400, 'intent': {}}}
    assert e._r8_pending_increase_notional() == pytest.approx(100_000.0)   # not 60k: no under-count
    e._inflight = {}                                                       # canceled or completed
    assert e._r8_pending_increase_notional() == 0.0
    c = engine(['BEL'])
    c._pending_entries = lambda: [{'reserved_notional': 25_000.0}]          # Engine C working entry
    assert c._r8_pending_increase_notional() == pytest.approx(25_000.0)


# 10 -------------------------------------------------------------------------------
def test_10_missing_valuation_price_fails_closed():
    e = engine(['BEL'])
    e._last_close = {}
    e._inflight = {'c1': {'kind': 'entry', 'symbol': 'BEL', 'requested': 10, 'filled': 0, 'intent': {}}}
    e._last_close_backup = None
    for i in range(30):
        e._r8_queue('BEL', i, asdict(make_bar(i)), 0.9, True)
        e.r8_flush()
    d = last(e, 'BEL')
    assert d['allocation']['measurement_error'] and d['target'] == 0.0
    assert e.r8_gate_counts['ALLOCATION_FAIL_CLOSED'] >= 1


# 11 -------------------------------------------------------------------------------
def test_11_long_reduction_always_approved():
    assert increase_demand_pu(0.2, 0.8) == 0.0
    assert approve_target(0.2, 0.8, 0.0) == pytest.approx(0.2)


# 12 -------------------------------------------------------------------------------
def test_12_short_cover():
    assert increase_demand_pu(-0.1, -0.7) == 0.0
    assert approve_target(-0.1, -0.7, 0.0) == pytest.approx(-0.1)
    assert approve_target(0.0, -0.7, 0.0) == 0.0


# 13 -------------------------------------------------------------------------------
def test_13_reversal_only_through_close_then_new_authorization():
    # Long open, bearish reference: approve flat only; no direct short.
    assert increase_demand_pu(-0.6, 0.5) == 0.0
    assert approve_target(-0.6, 0.5, 1.0) == 0.0
    # Once flat (native close reconciled), the short is a new, allocated demand.
    assert increase_demand_pu(-0.6, 0.0) == pytest.approx(0.6)
    assert approve_target(-0.6, 0.0, 0.25) == pytest.approx(-0.25)


# 14 -------------------------------------------------------------------------------
def test_14_zero_reference_creates_no_demand_or_position():
    assert increase_demand_pu(0.0, 0.0) == 0.0
    assert approve_target(0.0, 0.0, 10.0) == 0.0
    grants, case = allocate_headroom({'A': 0.0}, {'A': 1}, 1e9)
    assert case == 'NO_DEMAND' and grants == {'A': 0.0}


# 15 -------------------------------------------------------------------------------
def test_15_symbol_order_independence():
    symbols = ['BEL', 'LT', 'INFY', 'SBIN', 'CIPLA', 'ITC']
    a, b = engine(symbols), engine(symbols)
    for e in (a, b):
        e.safety_contract.values['max_gross_exposure_fraction'] = 0.002   # force oversubscription
    run_bars(a, 30, order=symbols)
    run_bars(b, 30, order=list(reversed(symbols)))
    strip = lambda e: sorted(json.dumps(d, sort_keys=True) for d in e.r8_decisions)
    assert strip(a) == strip(b)
    assert any(d['allocation']['case'] == 'OVERSUBSCRIBED' for d in a.r8_decisions)
    assert a.r8_decision_count == b.r8_decision_count


# 16 -------------------------------------------------------------------------------
def test_16_deterministic_snapshot_and_restart():
    symbols = ['BEL', 'INFY', 'CIPLA']
    a = engine(symbols)
    run_bars(a, 20)
    snap = json.loads(json.dumps(a.r8_snapshot()))
    b = engine(symbols)
    b.r8_restore(snap)
    for e in (a, b):
        for i in range(20, 35):
            for s in symbols:
                e._r8_queue(s, i, asdict(make_bar(i)), -0.4, True)
            e.r8_flush()
    assert (a.r8_decision_count, a.r8_decision_digest) == (b.r8_decision_count, b.r8_decision_digest)
    with pytest.raises(RuntimeError):
        a._r8_queue('BEL', 35, asdict(make_bar(35)), 0.1, True)
        a.r8_snapshot()                                    # mid-batch snapshot refused


# 17 -------------------------------------------------------------------------------
def test_17_five_bay_allocation_and_native_admission_consistency():
    symbols = sorted(SYMBOL_TO_BAY)
    e = engine(symbols)
    e.safety_contract.values['max_gross_exposure_fraction'] = 0.05      # 50k headroom
    run_bars(e, 30)
    batch = [d for d in e.r8_decisions if d['bar_index'] == 29]
    assert len(batch) == 48 and {d['bay_id'] for d in batch} == set(e.r8_bay_state)
    assert sum(d['allocation']['granted_notional'] for d in batch) <= 50_000.0 + 1e-6
    assert len({d['allocation']['headroom_notional'] for d in batch}) == 1   # one common state
    for d in batch:                                         # approved target never exceeds grant
        base = EQUITY * e.r8_configs[d['symbol']].budget_fraction
        assert abs(d['target']) <= abs(d['actual']) + d['allocation']['granted_notional'] / base + 1e-9
    # Native admission reads the same committed decision.
    d = last(e, 'BEL')
    ctrl = e.r8_symbol_controllers['BEL']
    assert ctrl.last_result.target == d['target'] and ctrl.steps == 30


def test_symbol_cap_limits_demand_before_allocation():
    e = engine(['BEL'])
    e.safety_contract.values['max_exposure_per_symbol_fraction'] = 0.0001   # 100 rupees
    run_bars(e, 30)
    assert last(e, 'BEL')['allocation']['demand_notional'] <= 100.0 + 1e-9
