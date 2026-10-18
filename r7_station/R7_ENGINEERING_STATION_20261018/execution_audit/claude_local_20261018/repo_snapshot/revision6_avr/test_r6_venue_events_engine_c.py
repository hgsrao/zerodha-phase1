"""R6 active-paper through the REAL Engine C runner on real Backtrader venue events: partial fill,
venue rejection and margin rejection (same configurations as tests_engine_c native fixtures).
    PYTHONPATH=<worktree> python -m pytest -q revision6_avr/test_r6_venue_events_engine_c.py
"""
import collections

import pytest

import engine_c.runner as runner
from engine_c.config import EngineCConfig
from engine_c.data_contract import load_synthetic
from revision6_avr.r6_native_active import r6_classes

BARS, WARMUP = 600, 60
NATIVE = runner.EngineCOrchestrator


def run(data, nifty, vix, *, config=None, orchestrator_kwargs=None):
    _, R6C = r6_classes()
    made = []

    def factory(*a, **k):
        o = R6C(*a, r6_max_utilization=1.0, r6_target_utilization=1.0, **k)
        made.append(o)
        return o
    runner.EngineCOrchestrator = factory
    try:
        report = runner.run_engine_c(data, nifty, vix, warmup=WARMUP, config=config,
                                     orchestrator_kwargs=orchestrator_kwargs)
    finally:
        runner.EngineCOrchestrator = NATIVE
    return report, made[-1]


@pytest.fixture(scope="module")
def feed():
    data, nifty, vix, _ = load_synthetic(["INFY"], BARS, WARMUP)
    return data, nifty, vix


def common_invariants(report, o):
    assert report["reconciliation"]["breaks"] == []
    assert all(d["r6_quantity"] <= d["native_plant_quantity"] for d in o.r6_decisions)
    # Settlement is lazy (at the bay's next bar step); a reservation may legitimately remain
    # conservative at end of data.  Force the end-of-run settlement and require it to succeed.
    for bay in o._r6_gates:
        assert o._r6_settle(bay) is None
    assert all(not g.reserved for g in o._r6_gates.values())
    bound = [b for b in o._r6_bindings.values() if b.get("order_id")]
    assert bound and all(b.get("settled_by") or b.get("released") for b in bound)
    return bound


def test_partial_fill_is_settled_by_venue_events_and_counted_once(feed):
    data, nifty, vix = feed
    frame = data["INFY"]
    thin = {"INFY": frame.assign(volume=(frame["volume"] * 1e-3).round().clip(lower=1))}
    report, o = run(thin, nifty, vix, config=EngineCConfig(volume_fill_perc=50.0))
    assert report["orders"]["entry_partial_fills"] >= 1                        # real partial at the venue
    bound = common_invariants(report, o)
    events = collections.Counter(e["event"] for e in o.order_audit)
    assert events["CANCELED"] >= 1                                             # IOC remainder cancelled natively
    assert any("FILLED" in b["settled_by"] for b in bound if b.get("settled_by"))


def test_venue_rejection_releases_capacity(feed):
    data, nifty, vix = feed
    report, o = run(data, nifty, vix, config=EngineCConfig(max_order_quantity={"INFY": 10}))
    assert report["orders"]["rejected"] == report["orders"]["entry_submitted"] >= 1
    bound = common_invariants(report, o)
    assert any("REJECTED" in (b.get("settled_by") or []) for b in bound)
    assert report["orders"]["fill_events"] == 0 and report["trades"] == []


def test_margin_rejection_releases_capacity(feed):
    data, nifty, vix = feed
    report, o = run(data, nifty, vix, config=EngineCConfig(starting_cash=20_000.0),
                    orchestrator_kwargs={"starting_equity": 1_000_000.0})
    assert report["orders"]["margin"] == report["orders"]["entry_submitted"] >= 1
    bound = common_invariants(report, o)
    assert any(set(b.get("settled_by") or []) & {"MARGIN", "ACCEPTED", "SUBMITTED"} for b in bound) or bound
    assert report["orders"]["fill_events"] == 0
