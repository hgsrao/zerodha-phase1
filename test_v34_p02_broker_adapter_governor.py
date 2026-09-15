"""Tests for KiteBrokerAdapterMultiPos's optional request-governor wiring
(EA1-R1 step 3 integration, v34_p02_broker_adapter.py).

Constructs the real adapter directly (not through make_stack(), which
this change deliberately leaves untouched) against a FakeBroker + a real
KiteRequestGovernor with a fake clock/sleep for determinism - proving the
actual call sites are gated with the correct endpoint class, not just
that the constructor accepts the parameter.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from kite_request_governor import DEFAULT, ORDER, QUOTE, KiteRequestGovernor
from test_v34_p02_lifecycle_integration import make_context
from test_v34_p02_multipos_engine import FakeBroker
from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_broker_adapter import AuthorizerRegistryStore, KiteBrokerAdapterMultiPos
from v34_p02_state import Config


class _FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _RecordingSleep:
    def __init__(self, clock: _FakeClock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


def _make_adapter(tmp_path, *, raw_broker=None, clock=None, sleeper=None):
    clock = clock or _FakeClock()
    sleeper = sleeper or _RecordingSleep(clock)
    governor = KiteRequestGovernor(state_dir=tmp_path, now_fn=clock.now, sleep_fn=sleeper)
    raw_broker = raw_broker or FakeBroker()
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"))
    context = make_context()
    adapter = KiteBrokerAdapterMultiPos(
        raw_broker=raw_broker,
        authorizer_store=AuthorizerRegistryStore(AuthorizerRegistry()),
        cfg=cfg, context_provider=lambda: context, governor=governor,
    )
    return adapter, raw_broker, clock, sleeper


def test_no_governor_supplied_behaves_exactly_as_before(tmp_path):
    """Default (governor=None) - the overwhelming majority of existing
    tests construct the adapter this way and must see zero behavior
    change."""
    raw_broker = FakeBroker()
    raw_broker.quotes = {"NSE:SBIN": {"last_price": 500.0}}
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"))
    adapter = KiteBrokerAdapterMultiPos(
        raw_broker=raw_broker, authorizer_store=AuthorizerRegistryStore(AuthorizerRegistry()),
        cfg=cfg, context_provider=lambda: make_context(),
    )
    adapter.ltp(["SBIN"])
    adapter.get_positions()
    adapter.get_orders()
    # No exception, no governor state file created anywhere - proves this
    # really is a complete no-op when governor is omitted.
    assert not (tmp_path / "kite_rate_governor_ledger.json").exists()


def test_ltp_pass_through_is_gated_under_the_quote_class(tmp_path):
    raw_broker = FakeBroker()
    raw_broker.quotes = {"NSE:SBIN": {"last_price": 500.0}}
    adapter, raw_broker, clock, sleeper = _make_adapter(tmp_path, raw_broker=raw_broker)

    adapter.ltp(["SBIN"])  # consumes the one QUOTE slot for this window
    assert sleeper.calls == []
    adapter.ltp(["SBIN"])  # second call in the same window must wait
    assert sleeper.calls == [pytest.approx(1.0, abs=1e-6)]


def test_get_positions_and_get_orders_are_gated_under_default_class(tmp_path):
    adapter, raw_broker, clock, sleeper = _make_adapter(tmp_path)
    # DEFAULT allows 10/second - positions() and orders() share that one
    # budget (both DEFAULT-class), so 10 combined calls should be free.
    for i in range(5):
        adapter.get_positions()
        adapter.get_orders()
    assert sleeper.calls == []
    adapter.get_positions()  # the 11th DEFAULT-class call this window
    assert len(sleeper.calls) == 1


def test_place_order_full_authorization_path_is_gated_under_order_class(tmp_path):
    raw_broker = FakeBroker()
    raw_broker.quotes = {"NSE:SBIN": {"last_price": 500.0}}
    raw_broker.place_order_fn = lambda **kwargs: "ORD-1"

    from portfolio_brain_v9 import SECTORS as REAL_SECTORS
    from v34_p02_accounting import initial_checkpoint

    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    governor = KiteRequestGovernor(state_dir=tmp_path, now_fn=clock.now, sleep_fn=sleeper)
    context = make_context(
        sector_lookup=REAL_SECTORS,
        checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital),
    )
    adapter = KiteBrokerAdapterMultiPos(
        raw_broker=raw_broker, authorizer_store=AuthorizerRegistryStore(AuthorizerRegistry()),
        cfg=cfg, context_provider=lambda: context, governor=governor,
    )

    order_id = adapter.place_order(
        exchange="NSE", tradingsymbol="SBIN", transaction_type="BUY", quantity=10,
        product="CNC", order_type="LIMIT", price=Decimal("500"), tag="V3.4_P02_ENTRY",
    )
    assert order_id == "ORD-1"
    # Real call gets ledger entries under BOTH classes it actually used
    # internally (positions/orders -> DEFAULT, ltp -> QUOTE, the final
    # place_order -> ORDER) - proving every real Kite call inside this
    # one method is gated, not just the outermost one.
    import json
    ledger = json.loads((tmp_path / "kite_rate_governor_ledger.json").read_text())
    assert len(ledger.get(DEFAULT, [])) == 2  # get_positions + get_orders
    assert len(ledger.get(QUOTE, [])) == 1
    assert len(ledger.get(ORDER, [])) == 1


def test_submit_emergency_exit_is_never_gated_by_the_governor(tmp_path):
    """Load-bearing safety assertion, not a convenience test: an
    emergency exit must never wait behind a rate limiter, matching this
    class's own docstring ('P02 refuses any doubt whatsoever on the
    liquidation path'). Fills the ORDER-class budget completely first,
    then proves an emergency exit still goes through with zero wait."""
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    governor = KiteRequestGovernor(state_dir=tmp_path, now_fn=clock.now, sleep_fn=sleeper)
    raw_broker = FakeBroker()
    raw_broker.submit_emergency_exit_fn = lambda **kwargs: "EXIT-1"
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"))
    adapter = KiteBrokerAdapterMultiPos(
        raw_broker=raw_broker, authorizer_store=AuthorizerRegistryStore(AuthorizerRegistry()),
        cfg=cfg, context_provider=lambda: make_context(), governor=governor,
    )

    for _ in range(10):
        governor.acquire(ORDER)  # exhaust the real ORDER budget for this window
    assert sleeper.calls == []

    result = adapter.submit_emergency_exit(symbol="RELIANCE", quantity=10)
    assert result == "EXIT-1"
    assert sleeper.calls == []  # the exit itself never waited, despite ORDER being full
