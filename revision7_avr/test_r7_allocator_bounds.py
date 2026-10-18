"""Allocator bounds: pure-function properties and engine-level grants against a common portfolio state."""
from collections import defaultdict
import random
import pytest

from .r7_allocator import allocate_headroom, approve_target, increase_demand_pu
from .test_r7_demand_allocation import engine, run_bars, EQUITY
from revision5.topology import SYMBOL_TO_BAY

EPS = 1e-6


def random_case(rnd):
    n = rnd.randint(1, 12)
    syms = [f"S{i}" for i in range(n)]
    demands = {s: rnd.choice([0.0, rnd.uniform(0, 100)]) for s in syms}
    weights = {s: rnd.uniform(0.01, 5) for s in syms}
    return demands, weights, rnd.choice([0.0, rnd.uniform(0, 300), rnd.uniform(0, 30)])


def test_pure_allocation_bounds_hold_over_random_cases():
    rnd = random.Random(7)
    seen = defaultdict(int)
    for _ in range(3000):
        d, w, h = random_case(rnd)
        grants, case = allocate_headroom(d, w, h)
        seen[case] += 1
        assert sum(grants.values()) <= h + EPS
        assert all(-1e-12 <= grants[s] <= d[s] + 1e-9 for s in d)
        if case == 'FITS':
            assert all(abs(grants[s] - d[s]) < 1e-9 for s in d)
        if case == 'OVERSUBSCRIBED':
            assert abs(sum(grants.values()) - h) < EPS
    assert seen['FITS'] and seen['OVERSUBSCRIBED']                # both regimes exercised


def test_allocation_is_independent_of_symbol_order():
    rnd = random.Random(11)
    for _ in range(500):
        d, w, h = random_case(rnd)
        a, _ = allocate_headroom(d, w, h)
        items = list(d.items()); rnd.shuffle(items)
        b, _ = allocate_headroom(dict(items), w, h)
        assert all(abs(a[s] - b[s]) < 1e-9 for s in d)


@pytest.mark.parametrize('bad', [float('nan'), float('inf')])
def test_non_finite_headroom_is_rejected(bad):
    with pytest.raises(ValueError):
        allocate_headroom({'A': 1.0}, {'A': 1.0}, bad)


def test_negative_headroom_grants_nothing():
    grants, _ = allocate_headroom({'A': 10.0}, {'A': 1.0}, -5.0)
    assert grants == {'A': 0.0}


def test_approved_target_never_exceeds_reference_or_flips_sign():
    rnd = random.Random(3)
    for _ in range(5000):
        r, a = rnd.uniform(-1, 1), rnd.uniform(-2, 2)
        cap = rnd.choice([None, 0.0, rnd.uniform(0, 1)])
        t = approve_target(r, a, cap)
        assert abs(t) <= abs(r) + 1e-12 or abs(t) <= abs(a) + 1e-12
        assert t == 0 or (t > 0) == (r > 0)
        if r * a < 0:
            assert t == 0.0                                         # opposing reference authorises flat only
        assert increase_demand_pu(r, a) >= 0


def grants_by_timestamp(e):
    by = defaultdict(list)
    for d in e.r7_decisions:
        by[d['timestamp']].append(d['allocation'])
    return by


def check_engine_bounds(e, symbols, cap_fraction):
    for ts, allocs in grants_by_timestamp(e).items():
        headroom = allocs[0]['headroom_notional']
        assert all(a['headroom_notional'] == headroom for a in allocs)        # one common state per timestamp
        assert sum(a['granted_notional'] for a in allocs) <= headroom + EPS
        assert all(a['granted_notional'] <= a['demand_notional'] + EPS for a in allocs)
        assert all(a['demand_notional'] <= cap_fraction * EQUITY + EPS for a in allocs)   # native per-symbol cap bounds demand


def test_engine_grants_never_exceed_common_headroom_or_native_symbol_cap():
    symbols = sorted(SYMBOL_TO_BAY)[:12]
    e = engine(symbols)
    run_bars(e, 45)
    assert any(a['granted_notional'] > 0 for allocs in grants_by_timestamp(e).values() for a in allocs)
    check_engine_bounds(e, symbols, 0.15)


def test_engine_with_limited_headroom_is_oversubscribed_and_bounded():
    symbols = sorted(SYMBOL_TO_BAY)[:12]
    e = engine(symbols)
    e.positions['INFY' if 'INFY' in symbols else symbols[0]] = 4000.0         # 400 000 of the 500 000 gross limit is filled
    run_bars(e, 45)
    allocs = [a for v in grants_by_timestamp(e).values() for a in v]
    assert any(a['case'] == 'OVERSUBSCRIBED' for a in allocs)
    assert max(a['headroom_notional'] for a in allocs) <= 100_000.0 + EPS
    check_engine_bounds(e, symbols, 0.15)


def test_engine_over_limit_grants_nothing():
    symbols = sorted(SYMBOL_TO_BAY)[:6]
    e = engine(symbols)
    e.positions[symbols[0]] = 6000.0                                         # 600 000 > 500 000 limit: overrun stays visible
    run_bars(e, 45)
    for allocs in grants_by_timestamp(e).values():
        assert all(a['granted_notional'] == 0.0 and a['headroom_notional'] == 0.0 for a in allocs)
    assert max(a['utilization'] for v in grants_by_timestamp(e).values() for a in v) > 1.0
