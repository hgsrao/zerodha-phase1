from pathlib import Path

import pytest

import read_only_shadow_collector as collector


class FakeQuoteReader:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def quote(self, instruments):
        self.calls.append(instruments)
        return self.response


def response():
    return {
        "NSE:RELIANCE": {
            "last_price": 100,
            "net_change": 3,
            "ohlc": {"high": 105, "low": 98},
            "depth": {"buy": [{"quantity": 80}], "sell": [{"quantity": 20}]},
        },
        "NSE:INFY": {
            "last_price": 200,
            "net_change": -1,
            "ohlc": {"high": 220, "low": 180},
            "depth": {"buy": [{"quantity": 50}], "sell": [{"quantity": 50}]},
        },
    }


def test_collect_once_is_read_only_and_risk_blocked():
    reader = FakeQuoteReader(response())
    result = collector.collect_once(reader, universe=("RELIANCE", "INFY"))
    assert reader.calls == [["NSE:RELIANCE", "NSE:INFY"]]
    assert result["decision"] == "BLOCK"
    assert result["candidate"]["symbol"] == "RELIANCE"
    assert result["source"] == "KITE_QUOTE_READ_ONLY"
    assert result["capabilities"]["broker_read"] is True
    assert result["capabilities"]["broker_write"] is False
    assert result["capabilities"]["order_api"] is False
    assert result["capabilities"]["request_entry"] is False
    assert result["capabilities"]["state_mutation"] is False
    assert result["checkpoints"][-1]["reason"] == (
        "OBSERVATION_ONLY_NO_EXECUTION_AUTHORIZATION"
    )


def test_malformed_response_fails_closed():
    with pytest.raises(RuntimeError, match="malformed quote response"):
        collector.collect_once(FakeQuoteReader([]), universe=("RELIANCE", "INFY"))


def test_interval_below_one_second_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="at least 1.0"):
        collector.run_loop(
            FakeQuoteReader(response()),
            output=tmp_path / "shadow_strategy_telemetry.json",
            interval_seconds=0.5,
            universe=("RELIANCE", "INFY"),
        )


def test_source_contains_no_order_or_production_state_operations():
    source = Path(collector.__file__).read_text(encoding="utf-8")
    forbidden = (
        ".place_order(",
        ".modify_order(",
        ".cancel_order(",
        "request_entry(",
        "bot_state_v34.json",
        "bot_state_v34.lock",
        "run_production_p01d_candidate",
        "institutional_engine_v34_p01d_candidate",
    )
    assert all(token not in source for token in forbidden)


def test_collection_contains_observation_timestamp():
    result = collector.collect_once(
        FakeQuoteReader(response()), universe=("RELIANCE", "INFY")
    )
    assert result["observed_at_utc"]


def test_shadow_entry_switch_logs_hypothetical_buy_without_execution_capability():
    reader = FakeQuoteReader(response())
    result = collector.collect_once(
        reader, universe=("RELIANCE", "INFY"), shadow_entry_intents=True,
    )
    assert result["decision"] == "HYPOTHETICAL_BUY"
    assert result["candidate"]["symbol"] == "RELIANCE"
    assert result["shadow_entry_intents_enabled"] is True
    assert result["execution_posture"] == "PHYSICALLY_UNAVAILABLE"
    assert result["capabilities"]["broker_write"] is False
    assert result["capabilities"]["order_api"] is False
    assert result["capabilities"]["request_entry"] is False
    assert result["capabilities"]["state_mutation"] is False
