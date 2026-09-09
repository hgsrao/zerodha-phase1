from revision4.contracts import ExitReason
from revision4.research_target import CompletedTrade
from revision4.validate_48symbol_sealed import _completed_trade_ledger


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
