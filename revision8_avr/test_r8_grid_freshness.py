"""Offline grid-context freshness: the native provider's causal/stale contract and R8's fail-closed use of it."""
from dataclasses import asdict
from types import SimpleNamespace
import math
import pandas as pd
import pytest

from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Core
from .test_r8_demand_allocation import engine
from .conftest import make_bar


def bars(n, start="2026-08-13 09:15"):
    t = pd.date_range(start, periods=n, freq="15min", tz="Asia/Kolkata")
    return pd.DataFrame({"timestamp": t, "close": 21000.0 + pd.Series(range(n)) * 1.0})


def provider(n=8, vix_n=None, **kw):
    return SealedGridContextProvider(bars(n), bars(vix_n if vix_n is not None else n), minimum_aligned_bars=3, **kw)


def ts(minutes_after_first_bar, n_first="2026-08-13 09:15"):
    return pd.Timestamp(n_first, tz="Asia/Kolkata") + pd.Timedelta(minutes=minutes_after_first_bar)


def test_fresh_context_is_available():
    p = provider()
    ctx = p.causal_context(ts(7 * 15 + 5))                      # 5 min after the last bar
    assert ctx.available and ctx.reason == "GRID_CONTEXT_AVAILABLE" and ctx.age_seconds == pytest.approx(300.0)


def test_stale_context_is_unavailable():
    ctx = provider().causal_context(ts(7 * 15 + 21))            # 21 min > 20 min limit
    assert not ctx.available and ctx.reason == "GRID_CONTEXT_STALE"


def test_staleness_boundary_is_inclusive_of_the_limit():
    assert provider().causal_context(ts(7 * 15 + 20)).available
    assert not provider().causal_context(ts(7 * 15 + 20) + pd.Timedelta(seconds=1)).available


def test_caller_supplied_limit_overrides_provider_default():
    p = provider()
    assert p.causal_context(ts(7 * 15 + 10), max_staleness_seconds=300.0).reason == "GRID_CONTEXT_STALE"


def test_decision_before_any_bar_is_not_yet_available():
    assert provider().causal_context(ts(0)).reason == "GRID_CONTEXT_NOT_YET_AVAILABLE"


def test_bar_stamped_at_the_decision_time_is_excluded_causality():
    ctx = provider().causal_context(ts(7 * 15))                 # decision == last bar stamp
    assert ctx.available
    assert ctx.source_timestamp < pd.Timestamp(ts(7 * 15)).tz_convert("UTC")
    assert ctx.nifty_prior.iloc[-1]["timestamp"] == ctx.source_timestamp


def test_nifty_vix_misalignment_is_unavailable():
    ctx = provider(n=8, vix_n=7).causal_context(ts(7 * 15 + 5))
    assert not ctx.available and ctx.reason == "GRID_CONTEXT_TIMESTAMP_MISMATCH"


def test_insufficient_warmup_is_unavailable():
    p = SealedGridContextProvider(bars(4), bars(4))             # default requires 63 aligned bars
    ctx = p.causal_context(ts(3 * 15 + 5))
    assert not ctx.available and ctx.reason == "GRID_CONTEXT_WARMUP_INSUFFICIENT"


def test_naive_decision_timestamp_is_rejected():
    with pytest.raises(ValueError):
        provider().causal_context(pd.Timestamp("2026-08-13 12:00"))


# ---- native governor grid input: None unless this exact timestamp has a finite plant reference ----
def native_grid(snapshot, snap_ts, query_ts):
    ns = SimpleNamespace(_paper_plant_snapshot=snapshot, _paper_plant_timestamp=snap_ts)
    return Core._governor_grid_return(ns, query_ts)


def snap(dev):
    return SimpleNamespace(grid=SimpleNamespace(nifty_deviation=dev))


def test_native_grid_return_is_none_without_snapshot_or_for_another_timestamp():
    assert native_grid(None, "t0", "t0") is None
    assert native_grid(snap(0.004), "t0", "t1") is None         # stale snapshot never reused for a later bar


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf")])
def test_native_grid_return_is_none_for_missing_or_non_finite_deviation(bad):
    assert native_grid(snap(bad), "t0", "t0") is None


def test_native_grid_return_passes_a_finite_deviation():
    assert native_grid(snap(0.004), "t0", "t0") == pytest.approx(0.004)


# ---- R8 fails closed on missing grid ----
def run_with_grid(feed, n=120):
    e = engine(["INFY"])
    e._governor_grid_return = feed
    for i in range(n):
        e._r8_queue("INFY", i, asdict(make_bar(i)), 0.9, True)
        e.r8_flush()
    return e


def test_r8_missing_grid_blocks_eligibility_pulses_and_demand():
    e = run_with_grid(lambda t: None)
    d = list(e.r8_decisions)
    assert d and not any(x["eligible"] for x in d) and all(x["pulse"] == "HOLD" for x in d)
    assert all(x["allocation"]["demand_notional"] == 0.0 and x["allocation"]["granted_notional"] == 0.0 for x in d)
    assert not any(x["inputs"]["dispatch_available"] for x in d)


def test_r8_intermittent_grid_gap_drops_eligibility_only_for_the_gap():
    gap = set(range(60, 80))
    e = SimpleNamespace()
    eng = engine(["INFY"])
    marks = {}
    eng._governor_grid_return = lambda t: None if marks.get("i") in gap else 0.004
    for i in range(120):
        marks["i"] = i
        eng._r8_queue("INFY", i, asdict(make_bar(i)), 0.9, True)
        eng.r8_flush()
    by_bar = {x["bar_index"]: x for x in eng.r8_decisions}
    assert all(not by_bar[i]["eligible"] for i in gap)
    assert any(by_bar[i]["eligible"] for i in range(100, 120))


def test_r8_non_finite_grid_value_raises_instead_of_being_used():
    e = engine(["INFY"])
    e._governor_grid_return = lambda t: float("nan")
    with pytest.raises(ValueError):
        e._r8_queue("INFY", 0, asdict(make_bar(0)), 0.9, True)
