from pathlib import Path

from shadow_entry_exit_lifecycle import advance


def test_hypothetical_buy_then_stop_sell(tmp_path: Path):
    state = tmp_path / "shadow_entry_exit_state.json"
    buy = advance(
        telemetry={"decision": "HYPOTHETICAL_BUY", "candidate": {
            "symbol": "ABC", "last_price": 100, "target_qty": 1,
        }},
        quotes={"NSE:ABC": {"last_price": 100}}, state_path=state,
    )
    assert buy["intent"] == "HYPOTHETICAL_BUY"
    assert buy["stop_price"] == "98.00"

    assert advance(
        telemetry={"decision": "BLOCK"},
        quotes={"NSE:ABC": {"last_price": 99}}, state_path=state,
    ) is None

    sell = advance(
        telemetry={"decision": "BLOCK"},
        quotes={"NSE:ABC": {"last_price": 98}}, state_path=state,
    )
    assert sell["intent"] == "HYPOTHETICAL_SELL"
    assert sell["reason"] == "PROTECTIVE_STOP_TRIGGERED"
    assert sell["execution_posture"] == "PHYSICALLY_UNAVAILABLE"


def test_refuses_production_state(tmp_path: Path):
    try:
        advance(
            telemetry={"decision": "HYPOTHETICAL_BUY", "candidate": {
                "symbol": "ABC", "last_price": 100, "target_qty": 1,
            }}, quotes={}, state_path=tmp_path / "bot_state_v34.json",
        )
        assert False
    except ValueError:
        pass

