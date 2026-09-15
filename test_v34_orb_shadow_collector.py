from pathlib import Path

import pytest

import orb_shadow_collector as collector


class FakeQuoteReader:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def quote(self, instruments):
        self.calls.append(instruments)
        return self.response


def response():
    return {
        "NSE:RELIANCE": {"last_price": 100, "ohlc": {"high": 105, "low": 98}},
        "NSE:INFY": {"last_price": 200, "ohlc": {"high": 220, "low": 180}},
    }


def test_interval_below_one_second_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="at least 1.0"):
        collector.run_loop(
            FakeQuoteReader(response()),
            output=tmp_path / "orb_shadow_telemetry.json",
            state_path=tmp_path / "orb_shadow_state.json",
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


def test_default_universe_excludes_polycab_and_matches_the_expanded_fifty():
    # Was 19 - deliberately extended to 50 on 2026-08-16 alongside SECTORS
    # (see p01d-and-v11-bridge-status memory for the full record).
    assert "POLYCAB" not in collector.DEFAULT_UNIVERSE
    assert len(collector.DEFAULT_UNIVERSE) == 50
