from revision4.contracts import ExitReason
from revision4.research_target import CompletedTrade
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision4.contracts import Bar
from revision4.validate_48symbol_sealed import (
    _build_calibration_config,
    _completed_trade_ledger,
    _partition_by_session,
)


def _trade(entry_timestamp: str, exit_timestamp: str) -> CompletedTrade:
    return CompletedTrade(
        trade_id="trade-1", symbol="SUNPHARMA", direction=1,
        entry_timestamp=entry_timestamp, entry_price=100.0, entry_cost=1.0,
        exit_timestamp=exit_timestamp, exit_price=102.0, exit_cost=1.0,
        exit_reason=ExitReason.TARGET_HIT, quantity=10,
        gross_pnl=20.0, net_pnl=18.0,
    )


def test_completed_trade_ledger_preserves_entry_exit_and_intraday_fields():
    record = _completed_trade_ledger([
        _trade("2023-09-01T09:20:00+05:30", "2023-09-01T10:00:00+05:30")
    ])[0]

    assert record["symbol"] == "SUNPHARMA"
    assert record["side"] == "LONG"
    assert record["entry_price"] == 100.0
    assert record["exit_price"] == 102.0
    assert record["quantity"] == 10
    assert record["exit_reason"] == "TARGET_HIT"
    assert record["same_session"] is True


def test_completed_trade_ledger_marks_cross_session_trade():
    record = _completed_trade_ledger([
        _trade("2023-09-01T15:00:00+05:30", "2023-09-04T09:20:00+05:30")
    ])[0]

    assert record["same_session"] is False


def test_calibration_config_allows_economic_override_but_rejects_gate16_override():
    registry = CanonicalParameterRegistry()
    config = _build_calibration_config(registry, {"profit_target_atr_mult": 2.0})
    assert config.profit_target_atr_mult == 2.0

    with pytest.raises(ValueError, match="immutable parameter slippage_tolerance_percent"):
        _build_calibration_config(registry, {"slippage_tolerance_percent": 0.2})


def test_partition_by_session_keeps_symbol_bars_in_their_market_date():
    bars = {
        "ABC": [
            Bar("2023-09-01T03:45:00+00:00", "ABC", 1, 1, 1, 1, 1),
            Bar("2023-09-04T03:45:00+00:00", "ABC", 1, 1, 1, 1, 1),
        ]
    }
    sessions = _partition_by_session(bars)
    assert list(sessions) == ["2023-09-01", "2023-09-04"]
    assert sessions["2023-09-01"]["ABC"][0].timestamp == "2023-09-01T03:45:00+00:00"
