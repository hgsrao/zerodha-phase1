from pathlib import Path

import pytest

import read_only_broker_inspection as inspection


def test_normalize_plain_and_redirect_tokens():
    assert inspection.normalize_request_token(" abc ") == "abc"
    assert inspection.normalize_request_token(
        "https://example.test/callback?request_token=xyz&status=success"
    ) == "xyz"


def test_summarize_reports_nonzero_positions_and_active_orders():
    result = inspection.summarize(
        {"net": [{"tradingsymbol": "ABC", "exchange": "NSE", "product": "MIS",
                  "quantity": 3, "average_price": 100}]},
        [{"order_id": "1", "tradingsymbol": "ABC", "status": "OPEN",
          "transaction_type": "SELL", "quantity": 3}],
    )
    assert not result["broker_clean"]
    assert result["nonzero_net_positions"][0]["quantity"] == 3
    assert result["active_orders"][0]["status"] == "OPEN"


def test_summarize_clean_account():
    result = inspection.summarize({"net": []}, [])
    assert result == {
        "nonzero_net_positions": [],
        "ignored_cnc_holdings": [],
        "active_orders": [],
        "broker_clean": True,
    }


def test_summarize_reports_cnc_holdings_without_blocking():
    result = inspection.summarize(
        {"net": [{"tradingsymbol": "NIFTYBEES", "exchange": "NSE",
                  "product": "CNC", "quantity": 5}]},
        [],
    )
    assert result["broker_clean"] is True
    assert result["nonzero_net_positions"] == []
    assert result["ignored_cnc_holdings"][0]["symbol"] == "NIFTYBEES"


def test_malformed_contract_fails_closed():
    with pytest.raises(RuntimeError, match="malformed positions response"):
        inspection.summarize([], [])


def test_source_has_no_trading_or_production_imports():
    source = Path(inspection.__file__).read_text(encoding="utf-8")
    forbidden = (
        ".place_order(",
        ".modify_order(",
        ".cancel_order(",
        "request_entry(",
        "run_production_p01d_candidate",
        "institutional_engine_v34_p01d_candidate",
        "bot_state_v34.json",
    )
    assert all(token not in source for token in forbidden)
