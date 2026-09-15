"""Tests for ShadowModeBrokerClient's optional request-governor wiring
(EA1-R1 step 3 integration continued, v34_bridge_shadow_broker_client.py).

The load-bearing claim here: place_order() makes several of its OWN real
Kite calls internally while building its WOULD_SUBMIT audit record - a
gap the outer KiteBrokerAdapterMultiPos gate (one acquire() before ever
calling place_order() at all) cannot see. These tests prove each of
those internal calls is independently gated.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kite_request_governor import DEFAULT, QUOTE, KiteRequestGovernor
from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_audit_sink import AuditSink
from v34_bridge_authorizer_registry_store import AuthorizerRegistryStore as DurableAuthorizerRegistryStore
from v34_bridge_daily_accounting_state import initial_daily_accounting_state
from v34_bridge_engine_adapters import EngineAuthorizerRegistryAdapter
from v34_bridge_kite_broker_client import KiteBrokerClient
from v34_bridge_shadow_broker_client import ShadowModeBrokerClient
from v34_p02_state import Config, EntryPolicyDeclinedError

NOW = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    defaults.update(overrides)
    return Config(**defaults)


class _FakeGovClock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _RecordingSleep:
    def __init__(self, clock: _FakeGovClock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


def make_shadow_with_governor(tmp_path, *, kite=None):
    kite = kite or FakeKiteConnectWithOrders()
    real_broker = KiteBrokerClient(kite, live_trading_enabled=False, product="CNC")
    audit = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
    authreg_adapter = EngineAuthorizerRegistryAdapter(DurableAuthorizerRegistryStore(tmp_path / "registry.json"))
    holder = {"state": initial_daily_accounting_state(trading_day="2026-08-17", trial_capital=Decimal("100000"))}
    gov_clock = _FakeGovClock()
    sleeper = _RecordingSleep(gov_clock)
    governor = KiteRequestGovernor(state_dir=tmp_path / "gov", now_fn=gov_clock.now, sleep_fn=sleeper)
    shadow = ShadowModeBrokerClient(
        real_broker, audit=audit, clock=FakeClock(NOW), cfg=cfg(),
        authreg_adapter=authreg_adapter, accounting_state_holder=holder, governor=governor,
    )
    return shadow, kite, sleeper, governor


def _place_order(shadow):
    with pytest.raises(EntryPolicyDeclinedError):
        shadow.place_order(
            exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10,
            product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY",
        )


def test_no_governor_supplied_is_a_complete_no_op(tmp_path):
    kite = FakeKiteConnectWithOrders()
    real_broker = KiteBrokerClient(kite, live_trading_enabled=False, product="CNC")
    audit = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
    authreg_adapter = EngineAuthorizerRegistryAdapter(DurableAuthorizerRegistryStore(tmp_path / "registry.json"))
    holder = {"state": initial_daily_accounting_state(trading_day="2026-08-17", trial_capital=Decimal("100000"))}
    shadow = ShadowModeBrokerClient(
        real_broker, audit=audit, clock=FakeClock(NOW), cfg=cfg(),
        authreg_adapter=authreg_adapter, accounting_state_holder=holder,
    )
    _place_order(shadow)
    assert not (tmp_path / "gov" / "kite_rate_governor_ledger.json").exists()


def test_a_single_would_submit_decline_consumes_governed_slots_under_every_class_it_uses(tmp_path):
    shadow, kite, sleeper, governor = make_shadow_with_governor(tmp_path)
    kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2500.0}}

    _place_order(shadow)

    ledger = json.loads((tmp_path / "gov" / "kite_rate_governor_ledger.json").read_text())
    # _best_effort_live_quote -> QUOTE (1); _best_effort_portfolio_snapshot
    # -> DEFAULT x2 (positions+orders); _best_effort_daily_entry_stats ->
    # DEFAULT x1 (orders); _clear_now_provably_stale_reservation -> DEFAULT
    # x2 (orders+positions) = 5 DEFAULT-class calls total, all previously
    # completely ungoverned.
    assert len(ledger.get(QUOTE, [])) == 1
    assert len(ledger.get(DEFAULT, [])) == 5


def test_a_second_would_submit_decline_in_the_same_window_is_actually_throttled(tmp_path):
    """The real, load-bearing proof: two independent shadow declines
    (e.g. two symbols each firing WOULD_SUBMIT in the same poll cycle)
    genuinely wait their turn on the QUOTE budget (1/second) - not just
    that a ledger entry gets written."""
    shadow, kite, sleeper, governor = make_shadow_with_governor(tmp_path)
    kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2500.0}}

    _place_order(shadow)
    assert sleeper.calls == []
    _place_order(shadow)  # second decline, same window - QUOTE budget (1/s) is already spent
    assert sleeper.calls == [pytest.approx(1.0, abs=1e-6)]
