from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from orb_shadow_observer import advance, evaluate_orb_breakouts, load_state, save_state, update_opening_ranges

IST = ZoneInfo("Asia/Kolkata")


def _at(hour, minute, day=14):
    return datetime(2026, 8, day, hour, minute, tzinfo=IST)


def _quote(high, low, last_price=None):
    return {"last_price": last_price if last_price is not None else high, "ohlc": {"high": high, "low": low}}


class TestUpdateOpeningRanges:
    def test_does_not_establish_before_the_window_closes(self):
        state = {"trading_day": None, "opening_ranges": {}}
        quotes = {"NSE:INFY": _quote(1500, 1480)}
        result = update_opening_ranges(state, quotes=quotes, universe=["INFY"], now=_at(9, 20), orb_minutes=15)
        assert result["opening_ranges"] == {}

    def test_establishes_once_the_window_closes(self):
        state = {"trading_day": None, "opening_ranges": {}}
        quotes = {"NSE:INFY": _quote(1500, 1480)}
        result = update_opening_ranges(state, quotes=quotes, universe=["INFY"], now=_at(9, 31), orb_minutes=15)
        assert result["opening_ranges"]["INFY"]["high"] == 1500
        assert result["opening_ranges"]["INFY"]["low"] == 1480

    def test_never_overwrites_an_already_established_range(self):
        state = {"trading_day": "2026-08-14", "opening_ranges": {"INFY": {"high": 1500, "low": 1480, "established_at": "x"}}}
        quotes = {"NSE:INFY": _quote(1600, 1400)}  # would be a very different range
        result = update_opening_ranges(state, quotes=quotes, universe=["INFY"], now=_at(11, 0), orb_minutes=15)
        assert result["opening_ranges"]["INFY"]["high"] == 1500

    def test_resets_on_a_new_trading_day(self):
        state = {"trading_day": "2026-08-13", "opening_ranges": {"INFY": {"high": 1500, "low": 1480, "established_at": "x"}}}
        quotes = {"NSE:INFY": _quote(1520, 1500)}
        result = update_opening_ranges(state, quotes=quotes, universe=["INFY"], now=_at(9, 31, day=14), orb_minutes=15)
        assert result["trading_day"] == "2026-08-14"
        assert result["opening_ranges"]["INFY"]["high"] == 1520

    def test_ignores_malformed_quote_payloads(self):
        state = {"trading_day": None, "opening_ranges": {}}
        quotes = {"NSE:INFY": {"last_price": 1500}}  # no ohlc field
        result = update_opening_ranges(state, quotes=quotes, universe=["INFY"], now=_at(9, 31), orb_minutes=15)
        assert result["opening_ranges"] == {}


class TestEvaluateOrbBreakouts:
    def test_no_ranges_yet_is_establishing(self):
        result = evaluate_orb_breakouts({"opening_ranges": {}}, quotes={}, universe=["INFY"])
        assert result["decision"] == "ESTABLISHING_OPENING_RANGE"
        assert result["candidates"] == []

    def test_range_established_but_no_breakout(self):
        state = {"opening_ranges": {"INFY": {"high": 1500, "low": 1480, "established_at": "x"}}}
        quotes = {"NSE:INFY": _quote(1500, 1480, last_price=1490)}
        result = evaluate_orb_breakouts(state, quotes=quotes, universe=["INFY"])
        assert result["decision"] == "NO_BREAKOUT_YET"

    def test_price_at_the_range_high_without_buffer_is_not_a_breakout(self):
        state = {"opening_ranges": {"INFY": {"high": 1500, "low": 1480, "established_at": "x"}}}
        quotes = {"NSE:INFY": _quote(1500, 1480, last_price=1500)}
        result = evaluate_orb_breakouts(state, quotes=quotes, universe=["INFY"], breakout_buffer_bps=10.0)
        assert result["decision"] == "NO_BREAKOUT_YET"

    def test_price_above_the_buffer_is_a_breakout_candidate(self):
        state = {"opening_ranges": {"INFY": {"high": 1500, "low": 1480, "established_at": "x"}}}
        quotes = {"NSE:INFY": _quote(1500, 1480, last_price=1503)}  # +20bps, above a 10bps buffer
        result = evaluate_orb_breakouts(state, quotes=quotes, universe=["INFY"], breakout_buffer_bps=10.0)
        assert result["decision"] == "HYPOTHETICAL_BUY"
        assert result["top_candidate"]["symbol"] == "INFY"

    def test_candidates_are_ranked_by_breakout_strength(self):
        state = {"opening_ranges": {
            "A": {"high": 100, "low": 95, "established_at": "x"},
            "B": {"high": 100, "low": 95, "established_at": "x"},
        }}
        quotes = {"NSE:A": _quote(100, 95, last_price=101), "NSE:B": _quote(100, 95, last_price=105)}
        result = evaluate_orb_breakouts(state, quotes=quotes, universe=["A", "B"], breakout_buffer_bps=10.0)
        assert [c["symbol"] for c in result["candidates"]] == ["B", "A"]

    def test_output_declares_no_execution_capability(self):
        result = evaluate_orb_breakouts({"opening_ranges": {}}, quotes={}, universe=[])
        assert result["authoritative_strategy"] is False
        assert result["backtested"] is False
        assert result["capabilities"] == {
            "broker_network": False, "order_api": False,
            "request_entry": False, "state_mutation": False,
        }


class TestAdvance:
    def test_combines_range_update_and_evaluation_in_one_call(self):
        quotes = {"NSE:INFY": _quote(1500, 1480, last_price=1503)}
        state, telemetry = advance({"trading_day": None, "opening_ranges": {}},
                                    quotes=quotes, universe=["INFY"], now=_at(9, 31),
                                    orb_minutes=15, breakout_buffer_bps=10.0)
        assert "INFY" in state["opening_ranges"]
        assert telemetry["decision"] == "HYPOTHETICAL_BUY"


class TestStatePersistence:
    def test_round_trips(self, tmp_path):
        path = tmp_path / "orb_state.json"
        state = {"trading_day": "2026-08-14", "opening_ranges": {"INFY": {"high": 1500, "low": 1480, "established_at": "x"}}}
        save_state(path, state)
        assert load_state(path) == state

    def test_missing_file_returns_empty_state(self, tmp_path):
        result = load_state(tmp_path / "does_not_exist.json")
        assert result == {"trading_day": None, "opening_ranges": {}}

    def test_refuses_to_write_a_production_state_or_lock_file(self, tmp_path):
        with pytest.raises(ValueError):
            save_state(tmp_path / "bot_state_v34.json", {})
        with pytest.raises(ValueError):
            save_state(tmp_path / "bot_state_v34.lock", {})
