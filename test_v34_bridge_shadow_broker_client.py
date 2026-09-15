"""Tests for v34_bridge_shadow_broker_client.py (EA-1)."""

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_audit_sink import AuditSink
from v34_bridge_authorizer_registry_store import AuthorizerRegistryStore as DurableAuthorizerRegistryStore
from v34_bridge_daily_accounting_state import initial_daily_accounting_state
from v34_bridge_engine_adapters import EngineAuthorizerRegistryAdapter
from v34_bridge_kite_broker_client import KiteBrokerClient
from v34_bridge_shadow_broker_client import ShadowModeBrokerClient
from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_state import Config, EntryPolicyDeclinedError, EntryReservation

NOW = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    defaults.update(overrides)
    return Config(**defaults)


def make_shadow(tmp_path, *, kite=None, real_live_trading_enabled=False, accounting_state=None):
    kite = kite or FakeKiteConnectWithOrders()
    real_broker = KiteBrokerClient(kite, live_trading_enabled=real_live_trading_enabled, product="CNC")
    audit = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
    authreg_adapter = EngineAuthorizerRegistryAdapter(DurableAuthorizerRegistryStore(tmp_path / "registry.json"))
    state = accounting_state or initial_daily_accounting_state(trading_day="2026-08-17", trial_capital=Decimal("100000"))
    holder = {"state": state}
    shadow = ShadowModeBrokerClient(
        real_broker, audit=audit, clock=FakeClock(NOW), cfg=cfg(),
        authreg_adapter=authreg_adapter, accounting_state_holder=holder,
    )
    return shadow, real_broker, kite, audit, authreg_adapter


class TestConstructionRefusesUnlessLiveTradingIsExactlyFalse:
    def test_refuses_when_real_broker_has_live_trading_enabled_true(self, tmp_path):
        with pytest.raises(RuntimeError, match="live_trading_enabled"):
            make_shadow(tmp_path, real_live_trading_enabled=True)

    def test_refuses_when_the_wrapped_object_has_no_such_attribute_at_all(self, tmp_path):
        class NotARealBroker:
            pass
        audit = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
        authreg_adapter = EngineAuthorizerRegistryAdapter(DurableAuthorizerRegistryStore(tmp_path / "registry.json"))
        holder = {"state": initial_daily_accounting_state(trading_day="2026-08-17", trial_capital=Decimal("100000"))}
        with pytest.raises(RuntimeError, match="live_trading_enabled"):
            ShadowModeBrokerClient(NotARealBroker(), audit=audit, clock=FakeClock(NOW), cfg=cfg(), authreg_adapter=authreg_adapter, accounting_state_holder=holder)


class TestPlaceOrderNeverReachesTheRealBroker:
    def test_raises_entry_policy_declined_error(self, tmp_path):
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        with pytest.raises(EntryPolicyDeclinedError, match="EA1_SHADOW_MODE"):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")

    def test_zero_real_place_order_calls(self, tmp_path):
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        with pytest.raises(EntryPolicyDeclinedError):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")
        assert kite.place_order_calls == []


class TestWouldSubmitAuditRecord:
    def test_core_fields_are_logged(self, tmp_path):
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        with pytest.raises(EntryPolicyDeclinedError):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")

        records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        event = next(r for r in records if r["event_type"] == "WOULD_SUBMIT")
        assert event["fields"]["symbol"] == "RELIANCE"
        assert event["fields"]["side"] == "BUY"
        assert event["fields"]["quantity"] == 10
        assert event["fields"]["reference_price"] == "2500.0"
        assert event["fields"]["tag"] == "V3.4_P02_ENTRY"
        assert event["fields"]["product"] == "CNC"

    def test_logged_exactly_once_per_call(self, tmp_path):
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        with pytest.raises(EntryPolicyDeclinedError):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")
        records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len([r for r in records if r["event_type"] == "WOULD_SUBMIT"]) == 1

    def test_live_quote_is_included_when_available(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2510.5}}
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path, kite=kite)
        with pytest.raises(EntryPolicyDeclinedError):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")
        records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        event = next(r for r in records if r["event_type"] == "WOULD_SUBMIT")
        assert event["fields"]["live_quote"] == "2510.5"

    def test_a_broken_quote_fetch_degrades_gracefully_and_still_declines(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        def broken_ltp(instruments):
            raise RuntimeError("simulated quote failure")
        kite.ltp = broken_ltp
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path, kite=kite)
        with pytest.raises(EntryPolicyDeclinedError):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")
        records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        event = next(r for r in records if r["event_type"] == "WOULD_SUBMIT")
        assert event["fields"]["live_quote"] is None
        assert event["fields"]["live_quote_error"] == "RuntimeError"


class TestReservationCleanupAfterDecline:
    def test_a_fingerprinted_reservation_with_no_matching_broker_order_is_cleared(self, tmp_path):
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        fingerprint = {
            "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "product": "CNC",
            "order_type": "LIMIT", "quantity": 10, "price": "2500", "tag": "V3.4_P02_ENTRY",
        }
        reservation = EntryReservation(symbol="RELIANCE", sector="ENERGY", reserved_capital=Decimal("25000"), reserved_at="2026-08-17T09:20:00+00:00", entry_fingerprint=fingerprint)
        authreg.save(AuthorizerRegistry(reservations={"RELIANCE": reservation}))
        # No matching broker order anywhere - kite.orders_response stays empty.

        with pytest.raises(EntryPolicyDeclinedError):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")

        assert "RELIANCE" not in authreg.registry.reservations
        assert "RELIANCE" not in authreg.registry.held_symbols

    def test_a_second_signal_for_the_same_symbol_is_not_blocked_by_a_stale_reservation(self, tmp_path):
        # This is the whole point of the cleanup: without it, EA-1 would
        # cap a full day's evidence at one WOULD_SUBMIT per symbol.
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        fingerprint = {
            "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "product": "CNC",
            "order_type": "LIMIT", "quantity": 10, "price": "2500", "tag": "V3.4_P02_ENTRY",
        }
        reservation = EntryReservation(symbol="RELIANCE", sector="ENERGY", reserved_capital=Decimal("25000"), reserved_at="2026-08-17T09:20:00+00:00", entry_fingerprint=fingerprint)
        authreg.save(AuthorizerRegistry(reservations={"RELIANCE": reservation}))

        with pytest.raises(EntryPolicyDeclinedError):
            shadow.place_order(exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY", quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="V3.4_P02_ENTRY")

        # A brand new candidate for the same symbol - the registry must
        # show it as free (not SYMBOL_ALREADY_HELD-blocked).
        assert "RELIANCE" not in authreg.registry.reservations
        assert "RELIANCE" not in authreg.registry.held_symbols


class TestEmergencyExitIsAPureUnmodifiedPassthrough:
    def test_the_reals_brokers_own_safety_halt_fires_unchanged(self, tmp_path):
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        with pytest.raises(RuntimeError, match="SAFETY_HALT.*submit_emergency_exit"):
            shadow.submit_emergency_exit(
                symbol="RELIANCE", quantity=10, trigger_price=Decimal("2400"), source_ltp=Decimal("2450"),
                tick_size=Decimal("0.05"), market_protection=Decimal("-1"), tag="V3.4_P02_EXIT",
            )

    def test_no_would_submit_or_decline_logic_applies_to_exits(self, tmp_path):
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path)
        try:
            shadow.submit_emergency_exit(
                symbol="RELIANCE", quantity=10, trigger_price=Decimal("2400"), source_ltp=Decimal("2450"),
                tick_size=Decimal("0.05"), market_protection=Decimal("-1"), tag="V3.4_P02_EXIT",
            )
        except RuntimeError:
            pass
        records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()] if (tmp_path / "audit.jsonl").exists() else []
        assert not any(r["event_type"] == "WOULD_SUBMIT" for r in records)


class TestReadMethodsPassThroughUnmodified:
    def test_get_positions(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 10, "average_price": "2500"}], "day": []}
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path, kite=kite)
        assert shadow.get_positions() == real_broker.get_positions()

    def test_get_orders(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.orders_response = [{"order_id": "ORD-1", "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "product": "CNC", "status": "COMPLETE", "quantity": 10, "filled_quantity": 10, "price": 2500.0, "tag": "V3.4_P02_ENTRY"}]
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path, kite=kite)
        assert shadow.get_orders() == real_broker.get_orders()

    def test_ltp(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2500.0}}
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path, kite=kite)
        assert shadow.ltp(["RELIANCE"]) == {"NSE:RELIANCE": {"last_price": 2500.0}}

    def test_get_tick_size(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": 0.05}]
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path, kite=kite)
        assert shadow.get_tick_size("RELIANCE") == Decimal("0.05")

    def test_get_order_details(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.order_history_responses["ORD-1"] = [{"order_id": "ORD-1", "status": "COMPLETE", "filled_quantity": 10, "quantity": 10}]
        shadow, real_broker, kite, audit, authreg = make_shadow(tmp_path, kite=kite)
        assert shadow.get_order_details("ORD-1")["status"] == "COMPLETE"
