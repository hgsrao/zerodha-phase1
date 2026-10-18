import sys
sys.path.insert(0, ".")

from unittest.mock import MagicMock, patch

import pytest
from kiteconnect.exceptions import InputException, NetworkException

from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter


def _adapter():
    with patch("revision2_external.broker_adapter_kite.KiteConnect") as MockClient:
        instance = MockClient.return_value
        instance.VARIETY_REGULAR = "regular"
        adapter = KiteConnectBrokerAdapter(api_key="fake", access_token="fake")
        return adapter, instance


def test_valid_market_order_is_translated_and_submitted():
    adapter, client = _adapter()
    client.place_order.return_value = "ORDER123"
    result = adapter.place_order("INFY", "BUY", 10, "MARKET")
    assert result == {"passed": True, "order_id": "ORDER123"}
    kwargs = client.place_order.call_args.kwargs
    assert kwargs["tradingsymbol"] == "INFY"
    assert kwargs["transaction_type"] == "BUY"
    assert kwargs["quantity"] == 10
    assert kwargs["order_type"] == "MARKET"


def test_invalid_side_fails_closed_without_calling_the_broker():
    adapter, client = _adapter()
    result = adapter.place_order("INFY", "HOLD", 10, "MARKET")
    assert result["passed"] is False
    client.place_order.assert_not_called()


def test_limit_order_requires_a_positive_price():
    adapter, client = _adapter()
    result = adapter.place_order("INFY", "BUY", 10, "LIMIT", limit_price=None)
    assert result["passed"] is False
    client.place_order.assert_not_called()


def test_network_exception_is_retried_and_eventually_succeeds():
    adapter, client = _adapter()
    client.place_order.side_effect = [NetworkException("timeout"), NetworkException("timeout"), "ORDER456"]
    result = adapter.place_order("INFY", "BUY", 5, "MARKET")
    assert result == {"passed": True, "order_id": "ORDER456"}
    assert client.place_order.call_count == 3


def test_network_exception_gives_up_after_max_attempts():
    adapter, client = _adapter()
    client.place_order.side_effect = NetworkException("timeout")
    with pytest.raises(NetworkException):
        adapter.place_order("INFY", "BUY", 5, "MARKET")
    assert client.place_order.call_count == 4  # stop_after_attempt(4)


def test_broker_rejection_is_not_retried():
    adapter, client = _adapter()
    client.place_order.side_effect = InputException("insufficient margin")
    result = adapter.place_order("INFY", "BUY", 5, "MARKET")
    assert result["passed"] is False
    assert "insufficient margin" in result["reason"]
    assert client.place_order.call_count == 1
