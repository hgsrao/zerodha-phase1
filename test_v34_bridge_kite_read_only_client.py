"""Tests for v34_bridge_kite_read_only_client.py.

FakeKiteConnect here is shaped like the real kiteconnect.KiteConnect
SDK's method surface (positions/orders/order_history/ltp/instruments) -
deliberately NOT the raw-broker protocol FakeBroker implements
(test_v34_p02_multipos_engine.py) - this is the layer directly beneath
that one, translating the real SDK's shapes into it.

The exception-passthrough tests are the centerpiece: they use the real
kiteconnect.exceptions.NetworkException (the package is installed in
this environment - test_harness_v34_b1_classification.py and
test_v34_bridge_runner_entrypoint.py already rely on this) and assert the
exact same exception instance propagates through every method, unwrapped
and unmodified - proving the exception taxonomy this session decided on,
not just asserting it in a docstring.
"""

from decimal import Decimal

import kiteconnect.exceptions as ke
import pytest

from v34_bridge_kite_read_only_client import KiteReadOnlyClient, KiteResponseMalformedError


class FakeKiteConnect:
    """Configurable per test - shaped like the real SDK's methods, not
    like the raw-broker protocol this client itself implements."""

    def __init__(self):
        self.positions_response = {"net": [], "day": []}
        self.orders_response = []
        self.order_history_responses = {}
        self.ltp_response = {}
        self.instruments_response = []
        self.trades_response = []
        self.order_trades_responses = {}
        self.virtual_contract_note_response = []
        self.raise_on = {}  # method_name -> exception instance

    def _maybe_raise(self, method_name):
        if method_name in self.raise_on:
            raise self.raise_on[method_name]

    def positions(self):
        self._maybe_raise("positions")
        return self.positions_response

    def orders(self):
        self._maybe_raise("orders")
        return self.orders_response

    def order_history(self, order_id):
        self._maybe_raise("order_history")
        return self.order_history_responses.get(order_id, [])

    def ltp(self, instruments):
        self._maybe_raise("ltp")
        return self.ltp_response

    def instruments(self, exchange):
        self._maybe_raise("instruments")
        return self.instruments_response

    def trades(self):
        self._maybe_raise("trades")
        return self.trades_response

    def order_trades(self, order_id):
        self._maybe_raise("order_trades")
        return self.order_trades_responses.get(order_id, [])

    def get_virtual_contract_note(self, order_params):
        self._maybe_raise("get_virtual_contract_note")
        return self.virtual_contract_note_response


def _position(symbol, qty, product="CNC", avg_price="2500.00"):
    return {"tradingsymbol": symbol, "exchange": "NSE", "product": product, "quantity": qty, "average_price": avg_price}


def _order(order_id, status="COMPLETE", filled_quantity=10, quantity=10):
    return {"order_id": order_id, "status": status, "filled_quantity": filled_quantity, "quantity": quantity}


class TestGetPositionsHappyPath:
    def test_returns_the_net_book(self):
        kite = FakeKiteConnect()
        kite.positions_response = {"net": [_position("RELIANCE", 50)], "day": [_position("RELIANCE", 10)]}
        client = KiteReadOnlyClient(kite)
        positions = client.get_positions()
        assert positions == [_position("RELIANCE", 50)]  # "net", not "day"


class TestGetPositionsMalformedResponse:
    def test_non_dict_response_raises(self):
        kite = FakeKiteConnect()
        kite.positions_response = ["not", "a", "dict"]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a dict"):
            client.get_positions()

    def test_missing_net_key_raises(self):
        kite = FakeKiteConnect()
        kite.positions_response = {"day": []}
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="'net' key"):
            client.get_positions()

    def test_net_not_a_list_raises(self):
        kite = FakeKiteConnect()
        kite.positions_response = {"net": "not-a-list"}
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a list"):
            client.get_positions()

    def test_a_non_dict_position_record_raises(self):
        kite = FakeKiteConnect()
        kite.positions_response = {"net": ["not-a-dict"]}
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed position record"):
            client.get_positions()


class TestGetOrdersHappyPathAndMalformed:
    def test_happy_path(self):
        kite = FakeKiteConnect()
        kite.orders_response = [_order("ORD-1"), _order("ORD-2", status="REJECTED")]
        client = KiteReadOnlyClient(kite)
        assert client.get_orders() == kite.orders_response

    def test_non_list_response_raises(self):
        kite = FakeKiteConnect()
        kite.orders_response = {"not": "a list"}
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a list"):
            client.get_orders()

    def test_a_non_dict_order_record_raises(self):
        kite = FakeKiteConnect()
        kite.orders_response = [123]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed order record"):
            client.get_orders()


class TestGetOrderDetails:
    def test_returns_the_most_recent_history_entry(self):
        kite = FakeKiteConnect()
        kite.order_history_responses["ORD-1"] = [
            _order("ORD-1", status="OPEN", filled_quantity=0),
            _order("ORD-1", status="COMPLETE", filled_quantity=10),
        ]
        client = KiteReadOnlyClient(kite)
        details = client.get_order_details("ORD-1")
        assert details["status"] == "COMPLETE"
        assert details["filled_quantity"] == 10

    def test_empty_order_id_raises(self):
        client = KiteReadOnlyClient(FakeKiteConnect())
        with pytest.raises(KiteResponseMalformedError, match="empty order_id"):
            client.get_order_details("")

    def test_empty_history_raises(self):
        kite = FakeKiteConnect()
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="non-empty list"):
            client.get_order_details("ORD-UNKNOWN")

    def test_non_list_history_raises(self):
        kite = FakeKiteConnect()
        kite.order_history_responses["ORD-1"] = "not-a-list"
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="non-empty list"):
            client.get_order_details("ORD-1")

    def test_a_non_dict_latest_entry_raises(self):
        kite = FakeKiteConnect()
        kite.order_history_responses["ORD-1"] = ["not-a-dict"]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed record"):
            client.get_order_details("ORD-1")


class TestLtp:
    def test_bare_symbols_get_nse_prefixed(self):
        kite = FakeKiteConnect()
        captured = {}
        original_ltp = kite.ltp
        def capturing_ltp(instruments):
            captured["instruments"] = instruments
            return original_ltp(instruments)
        kite.ltp = capturing_ltp
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2500.0}, "NSE:INFY": {"last_price": 1500.0}}
        client = KiteReadOnlyClient(kite)

        result = client.ltp(["RELIANCE", "INFY"])
        assert captured["instruments"] == ["NSE:RELIANCE", "NSE:INFY"]
        assert result == kite.ltp_response

    def test_non_dict_response_raises(self):
        kite = FakeKiteConnect()
        kite.ltp_response = ["not", "a", "dict"]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a dict"):
            client.ltp(["RELIANCE"])

    def test_a_quote_missing_last_price_raises(self):
        kite = FakeKiteConnect()
        kite.ltp_response = {"NSE:RELIANCE": {"instrument_token": 123}}  # no last_price
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed quote"):
            client.ltp(["RELIANCE"])


class TestGetTickSize:
    def test_happy_path_and_caching(self):
        kite = FakeKiteConnect()
        kite.instruments_response = [
            {"tradingsymbol": "RELIANCE", "tick_size": 0.05},
            {"tradingsymbol": "INFY", "tick_size": 0.05},
        ]
        call_count = {"n": 0}
        original_instruments = kite.instruments
        def counting_instruments(exchange):
            call_count["n"] += 1
            return original_instruments(exchange)
        kite.instruments = counting_instruments
        client = KiteReadOnlyClient(kite)

        assert client.get_tick_size("RELIANCE") == Decimal("0.05")
        assert client.get_tick_size("INFY") == Decimal("0.05")  # a different symbol
        assert call_count["n"] == 1  # the bulk dump was fetched exactly once, cached after

    def test_unknown_symbol_raises(self):
        kite = FakeKiteConnect()
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": 0.05}]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="not found"):
            client.get_tick_size("NOTLISTED")

    def test_non_list_instruments_response_raises(self):
        kite = FakeKiteConnect()
        kite.instruments_response = {"not": "a list"}
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a list"):
            client.get_tick_size("RELIANCE")

    def test_malformed_instrument_row_raises(self):
        kite = FakeKiteConnect()
        kite.instruments_response = [{"tradingsymbol": "RELIANCE"}]  # no tick_size
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed instrument row"):
            client.get_tick_size("RELIANCE")

    def test_malformed_tick_size_value_raises(self):
        kite = FakeKiteConnect()
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": "not-a-number"}]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed tick_size"):
            client.get_tick_size("RELIANCE")


def _trade(trade_id, order_id, symbol="RELIANCE", transaction_type="BUY", quantity=10, average_price="2500.00"):
    return {
        "trade_id": trade_id, "order_id": order_id, "tradingsymbol": symbol,
        "transaction_type": transaction_type, "quantity": quantity, "average_price": average_price,
        "product": "CNC", "exchange": "NSE", "fill_timestamp": "2026-08-15 10:00:00",
    }


class TestGetTrades:
    def test_happy_path(self):
        kite = FakeKiteConnect()
        kite.trades_response = [_trade("T1", "ORD-1"), _trade("T2", "ORD-1", transaction_type="SELL")]
        client = KiteReadOnlyClient(kite)
        assert client.get_trades() == kite.trades_response

    def test_non_list_response_raises(self):
        kite = FakeKiteConnect()
        kite.trades_response = {"not": "a list"}
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a list"):
            client.get_trades()

    def test_a_non_dict_trade_record_raises(self):
        kite = FakeKiteConnect()
        kite.trades_response = ["not-a-dict"]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed trade record"):
            client.get_trades()

    def test_missing_trade_id_raises(self):
        kite = FakeKiteConnect()
        bad = _trade("T1", "ORD-1")
        del bad["trade_id"]
        kite.trades_response = [bad]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="trade_id"):
            client.get_trades()

    def test_empty_order_id_raises(self):
        kite = FakeKiteConnect()
        kite.trades_response = [_trade("T1", "")]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="order_id"):
            client.get_trades()

    def test_a_real_network_exception_passes_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Too many requests", code=429)
        kite.raise_on["trades"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.get_trades()
        assert excinfo.value is exc


class TestGetOrderTrades:
    def test_happy_path_filters_to_one_order(self):
        kite = FakeKiteConnect()
        kite.order_trades_responses["ORD-1"] = [_trade("T1", "ORD-1"), _trade("T2", "ORD-1")]
        client = KiteReadOnlyClient(kite)
        assert client.get_order_trades("ORD-1") == kite.order_trades_responses["ORD-1"]

    def test_empty_order_id_raises(self):
        client = KiteReadOnlyClient(FakeKiteConnect())
        with pytest.raises(KiteResponseMalformedError, match="empty order_id"):
            client.get_order_trades("")

    def test_non_list_response_raises(self):
        kite = FakeKiteConnect()

        def bad_order_trades(order_id):
            return "not-a-list"
        kite.order_trades = bad_order_trades
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a list"):
            client.get_order_trades("ORD-1")

    def test_a_real_network_exception_passes_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Gateway unavailable", code=503)
        kite.raise_on["order_trades"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.get_order_trades("ORD-1")
        assert excinfo.value is exc


class TestGetVirtualContractNote:
    def test_happy_path_passes_through(self):
        kite = FakeKiteConnect()
        kite.virtual_contract_note_response = [{"order_id": "ORD-1", "charges": {"total": 41.5}}]
        client = KiteReadOnlyClient(kite)
        params = [{"order_id": "ORD-1", "transaction_type": "BUY", "quantity": 10, "average_price": 2500.0}]
        assert client.get_virtual_contract_note(params) == kite.virtual_contract_note_response

    def test_non_list_response_raises(self):
        kite = FakeKiteConnect()
        kite.virtual_contract_note_response = {"not": "a list"}
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected a list"):
            client.get_virtual_contract_note([{"order_id": "ORD-1"}])

    def test_a_non_dict_item_raises(self):
        kite = FakeKiteConnect()
        kite.virtual_contract_note_response = ["not-a-dict"]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="malformed item"):
            client.get_virtual_contract_note([{"order_id": "ORD-1"}])

    def test_a_real_network_exception_passes_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Too many requests", code=429)
        kite.raise_on["get_virtual_contract_note"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.get_virtual_contract_note([{"order_id": "ORD-1"}])
        assert excinfo.value is exc


class TestGetVirtualContractNoteForOneOrder:
    def test_happy_path_unwraps_the_single_item(self):
        kite = FakeKiteConnect()
        kite.virtual_contract_note_response = [{"tradingsymbol": "SBIN", "charges": {"total": 0.58}}]
        client = KiteReadOnlyClient(kite)
        item = client.get_virtual_contract_note_for_one_order({"tradingsymbol": "SBIN", "quantity": 1})
        assert item == {"tradingsymbol": "SBIN", "charges": {"total": 0.58}}

    def test_zero_items_raises(self):
        kite = FakeKiteConnect()
        kite.virtual_contract_note_response = []
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected exactly one"):
            client.get_virtual_contract_note_for_one_order({"tradingsymbol": "SBIN"})

    def test_more_than_one_item_raises(self):
        kite = FakeKiteConnect()
        kite.virtual_contract_note_response = [{"charges": {"total": 1}}, {"charges": {"total": 2}}]
        client = KiteReadOnlyClient(kite)
        with pytest.raises(KiteResponseMalformedError, match="expected exactly one"):
            client.get_virtual_contract_note_for_one_order({"tradingsymbol": "SBIN"})


class TestExceptionTaxonomyRealNetworkExceptionPassesThroughUnwrapped:
    """The centerpiece proof: kiteconnect's own real exceptions propagate
    exactly as raised - not caught, not wrapped, not re-raised as
    KiteResponseMalformedError or anything else. Both P02's own
    _is_transient_observation_exception and this bridge's own
    _is_rate_limit_exception depend on this being true."""

    def test_get_positions_lets_a_real_network_exception_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Too many requests", code=429)
        kite.raise_on["positions"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.get_positions()
        assert excinfo.value is exc  # the exact same instance, not a copy or a wrapper

    def test_get_orders_lets_a_real_network_exception_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Gateway unavailable", code=503)
        kite.raise_on["orders"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.get_orders()
        assert excinfo.value is exc

    def test_get_order_details_lets_a_real_network_exception_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Gateway timeout", code=504)
        kite.raise_on["order_history"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.get_order_details("ORD-1")
        assert excinfo.value is exc

    def test_ltp_lets_a_real_network_exception_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Too many requests", code=429)
        kite.raise_on["ltp"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.ltp(["RELIANCE"])
        assert excinfo.value is exc

    def test_get_tick_size_lets_a_real_network_exception_through_unwrapped(self):
        kite = FakeKiteConnect()
        exc = ke.NetworkException("Gateway unavailable", code=502)
        kite.raise_on["instruments"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.NetworkException) as excinfo:
            client.get_tick_size("RELIANCE")
        assert excinfo.value is exc

    def test_a_token_exception_also_passes_through_unwrapped(self):
        # Not just NetworkException - the taxonomy is "every kiteconnect
        # exception," not just the one this session has focused on.
        kite = FakeKiteConnect()
        exc = ke.TokenException("Token expired", code=403)
        kite.raise_on["orders"] = exc
        client = KiteReadOnlyClient(kite)
        with pytest.raises(ke.TokenException) as excinfo:
            client.get_orders()
        assert excinfo.value is exc

    def test_a_second_call_after_a_transient_failure_is_not_auto_retried(self):
        # This client does not retry - proven by counting: exactly one
        # underlying call happens per client call, even when it fails.
        kite = FakeKiteConnect()
        call_count = {"n": 0}
        original_orders = kite.orders
        def counting_orders():
            call_count["n"] += 1
            return original_orders()
        kite.orders = counting_orders
        kite.raise_on["orders"] = ke.NetworkException("Gateway unavailable", code=503)
        client = KiteReadOnlyClient(kite)

        with pytest.raises(ke.NetworkException):
            client.get_orders()
        assert call_count["n"] == 1  # no retry loop happened inside get_orders()
