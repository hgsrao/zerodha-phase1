"""Tests for kite_request_governor.py (EA1-R1 step 3).

No real Kite/network/credentials anywhere - a fake, injectable clock and
sleep function make every test deterministic and fast, while the actual
file-locking and ledger read/write happen for real against tmp_path, so
the cross-process coordination claim is proven against the real
filesystem primitive, not mocked away.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

import kite_request_governor as gov
from kite_request_governor import (
    DEFAULT,
    HISTORICAL,
    ORDER,
    QUOTE,
    KiteRateGovernorError,
    KiteRequestGovernor,
    governed_call,
)


class _FakeClock:
    """Simple advancing fake clock - starts at 1_000_000.0 (an arbitrary
    epoch-like base, far from 0 so a bug that treats "no ledger entry" as
    timestamp 0.0 would be caught by an accidental non-empty window)."""

    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _RecordingSleep:
    """Records every sleep call and ADVANCES the shared fake clock by
    exactly that amount - a faithful fake, not just a no-op, so the
    governor's own "sleep until the window frees up" logic is exercised
    for real rather than short-circuited."""

    def __init__(self, clock: _FakeClock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


def _governor(tmp_path, clock: _FakeClock, sleeper: _RecordingSleep) -> KiteRequestGovernor:
    return KiteRequestGovernor(state_dir=tmp_path, now_fn=clock.now, sleep_fn=sleeper)


# ---------------------------------------------------------------------------
# Basic per-class rate limiting
# ---------------------------------------------------------------------------

def test_first_quote_call_is_allowed_with_no_wait(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    g.acquire(QUOTE)
    assert sleeper.calls == []


def test_second_quote_call_in_the_same_second_waits_for_the_window(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    g.acquire(QUOTE)  # consumes the one QUOTE slot for this window
    g.acquire(QUOTE)  # must wait ~1.0s for the window to roll
    assert sleeper.calls == [pytest.approx(1.0, abs=1e-6)]


def test_order_class_allows_ten_before_any_wait(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    for _ in range(10):
        g.acquire(ORDER)
    assert sleeper.calls == []  # all 10 fit in the same 1-second window
    g.acquire(ORDER)  # the 11th must wait
    assert len(sleeper.calls) == 1


def test_historical_class_allows_three_before_any_wait(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    for _ in range(3):
        g.acquire(HISTORICAL)
    assert sleeper.calls == []
    g.acquire(HISTORICAL)
    assert len(sleeper.calls) == 1


def test_different_classes_have_independent_budgets(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    g.acquire(QUOTE)          # consumes QUOTE's only slot
    g.acquire(ORDER)          # ORDER budget is untouched by QUOTE's usage
    g.acquire(DEFAULT)        # likewise DEFAULT
    assert sleeper.calls == []  # none of these should have waited on each other


def test_unknown_endpoint_class_is_refused(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    with pytest.raises(ValueError):
        g.acquire("not_a_real_class")


# ---------------------------------------------------------------------------
# Sliding window - old entries actually expire, not just "reset every N calls"
# ---------------------------------------------------------------------------

def test_window_frees_up_naturally_once_a_second_has_genuinely_passed(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    g.acquire(QUOTE)
    clock.advance(1.01)  # a second genuinely passes via a real external cause,
                         # not this governor's own sleep_fn
    g.acquire(QUOTE)     # should NOT need to wait - the prior entry is stale
    assert sleeper.calls == []


# ---------------------------------------------------------------------------
# THE core claim: two SEPARATE governor instances, same state_dir, see and
# respect each other's usage - this is what makes it a real cross-process
# governor rather than a per-process limiter that happens to share a class name.
# ---------------------------------------------------------------------------

def test_two_independent_governor_instances_share_the_same_budget(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    # Two separate KiteRequestGovernor objects, pointed at the SAME
    # state_dir - simulating two separate OS processes (Terminal A and
    # Terminal B, or V11 and P02) that would each construct their own
    # instance in production, coordinating purely through the shared
    # on-disk ledger, never through shared Python state.
    governor_a = _governor(tmp_path, clock, sleeper)
    governor_b = _governor(tmp_path, clock, sleeper)

    governor_a.acquire(QUOTE)   # "Terminal A" takes the one QUOTE slot
    governor_b.acquire(QUOTE)   # "Terminal B" must see A's usage and wait
    assert sleeper.calls == [pytest.approx(1.0, abs=1e-6)]


def test_real_concurrent_threads_never_exceed_the_class_limit(tmp_path):
    """Real OS-level file locking under real concurrent contention (real
    threads, real time.sleep/time.monotonic - the one test in this file
    that deliberately does NOT use the fake clock, to prove the actual
    locking primitive works under real concurrency, not just sequential
    fake-clock calls)."""
    g = KiteRequestGovernor(state_dir=tmp_path)  # real time.monotonic/time.sleep
    granted_at = []
    lock = threading.Lock()

    def worker():
        g.acquire(ORDER)  # ORDER: 10/second
        with lock:
            granted_at.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(15)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(granted_at) == 15  # every thread eventually got in, none lost/deadlocked
    # No 1-second window contains more than 10 grants - the actual
    # invariant this whole module exists to guarantee.
    granted_at.sort()
    for i in range(len(granted_at) - 10):
        assert granted_at[i + 10] - granted_at[i] >= 0.99, (
            "10 grants landed inside one window - the shared limit was violated"
        )


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_corrupt_ledger_file_fails_safe_not_crashed(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    ledger_path = tmp_path / "kite_rate_governor_ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("{not valid json at all", encoding="utf-8")
    g.acquire(QUOTE)  # must not raise - treats a corrupt ledger as empty
    assert sleeper.calls == []


def test_stale_cross_boot_ledger_entry_is_not_treated_as_recent(tmp_path):
    """Found live 2026-08-24: a real ledger on disk had entries far LARGER
    than the current session's clock (e.g. HISTORICAL: [42497.3, 42498.2,
    42498.2] while the new process's own now_fn() was only ~500) because
    now_fn defaults to time.monotonic(), which resets near-zero on every
    reboot but the ledger file survives across reboots. The old filter
    (`t > window_start`) treated those numerically-huge stale entries as
    "within the last second" forever, since the new session's clock would
    need ~11.6 hours to numerically catch up - acquire() then computed a
    multi-hour wait_seconds and slept in one uninterruptible call that
    _MAX_ACQUIRE_WAIT_SECONDS could never preempt. A legitimate entry can
    never be from the future relative to this session's own now_fn(), so
    it must be discarded exactly like a corrupt ledger - fail safe towards
    *more* permissive, never less, matching test_corrupt_ledger_file_fails
    _safe_not_crashed just above."""
    clock = _FakeClock(start=500.0)  # low uptime - a session freshly rebooted
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    ledger_path = tmp_path / "kite_rate_governor_ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    # Three stale HISTORICAL entries from a previous (larger-valued) boot
    # session - already at the class limit, so an unpatched governor would
    # compute wait_seconds against these and sleep for hours.
    ledger_path.write_text(json.dumps({HISTORICAL: [42497.3, 42498.2, 42498.2]}), encoding="utf-8")
    g.acquire(HISTORICAL)  # must be granted immediately, not sleep for hours
    assert sleeper.calls == []
    ledger = json.loads(ledger_path.read_text())
    # The stale entries must be dropped from the rewritten ledger, not
    # merely bypassed this one time - otherwise they'd keep blocking the
    # 2nd and 3rd real HISTORICAL calls of this session too.
    assert ledger[HISTORICAL] == [pytest.approx(500.0, abs=1e-6)]


def test_ledger_persists_correctly_between_separate_instances(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    _governor(tmp_path, clock, sleeper).acquire(QUOTE)
    ledger = json.loads((tmp_path / "kite_rate_governor_ledger.json").read_text())
    assert ledger[QUOTE] == [pytest.approx(1_000_000.0, abs=1e-6)]


def test_max_wait_bound_raises_rather_than_hanging_forever(tmp_path):
    """Simulates a pathological starvation case (another process
    perpetually refreshing the same slot right before every check) by
    having the fake clock's own advance also top up the ledger - proving
    the defensive _MAX_ACQUIRE_WAIT_SECONDS bound is real and reachable,
    not just a number that's never actually checked."""
    clock = _FakeClock()

    def perpetually_contending_sleep(seconds: float) -> None:
        clock.advance(seconds)
        # Simulate a sibling process taking the freed slot the instant it
        # opens, forever - this governor's own acquire() can never win.
        ledger_path = tmp_path / "kite_rate_governor_ledger.json"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps({QUOTE: [clock.now()]}), encoding="utf-8")

    g = _governor(tmp_path, clock, perpetually_contending_sleep)
    # Pre-seed the ledger as already full - an empty ledger would let the
    # very first acquire() through trivially, never reaching the wait
    # branch (and therefore never invoking perpetually_contending_sleep)
    # at all.
    ledger_path = tmp_path / "kite_rate_governor_ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps({QUOTE: [clock.now()]}), encoding="utf-8")

    with pytest.raises(KiteRateGovernorError):
        g.acquire(QUOTE)


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def test_gate_context_manager_acquires_before_the_block_runs(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    order = []
    with g.gate(QUOTE):
        order.append("inside")
    assert order == ["inside"]
    ledger = json.loads((tmp_path / "kite_rate_governor_ledger.json").read_text())
    assert len(ledger[QUOTE]) == 1


def test_governed_call_acquires_then_calls_fn_with_args(tmp_path):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    g = _governor(tmp_path, clock, sleeper)
    calls = []
    result = governed_call(g, QUOTE, lambda *a, **kw: calls.append((a, kw)) or "ok", "SBIN", exchange="NSE")
    assert result == "ok"
    assert calls == [(("SBIN",), {"exchange": "NSE"})]
