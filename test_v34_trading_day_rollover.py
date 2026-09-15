from decimal import Decimal

from institutional_engine_v34_p01d_candidate import BotState, Config, TradingEngineV34
from test_v34_entry_submit_restart_recovery import (
    FakeAlert,
    FakeAudit,
    FakeBroker,
    FakeClock,
    FakeLock,
    FakeStore,
    FakeTerminator,
)


def make_engine(state, broker):
    return TradingEngineV34(
        broker=broker,
        clock=FakeClock(),
        sleeper=lambda *_: None,
        store=FakeStore(state),
        audit=FakeAudit(),
        alert=FakeAlert(),
        lock=FakeLock(),
        terminator=FakeTerminator(),
        cfg=Config(alert_webhook_url="", max_daily_loss=Decimal("2000")),
    )


def test_clean_flat_startup_rolls_trading_day_after_broker_reconciliation():
    state = BotState(trading_day="2026-08-11", status="STARTUP")
    engine = make_engine(state, FakeBroker())

    engine.reconcile_startup()

    assert engine.state.status == "FLAT"
    assert engine.state.trading_day == "2026-08-12"
    assert engine.terminator.halted is False
    assert any(
        event == "TRADING_DAY_ROLLED_FORWARD"
        and details["previous_trading_day"] == "2026-08-11"
        and details["trading_day"] == "2026-08-12"
        for event, details in engine.audit.events
    )


def test_broker_exposure_blocks_rollover_and_halts():
    state = BotState(trading_day="2026-08-11", status="STARTUP")
    broker = FakeBroker(
        positions=[{
            "tradingsymbol": "INFY",
            "exchange": "NSE",
            "product": "MIS",
            "quantity": 1,
            "average_price": 100,
        }]
    )
    engine = make_engine(state, broker)

    engine.reconcile_startup()

    assert engine.state.status == "RECONCILIATION_HALT"
    assert engine.state.trading_day == "2026-08-11"
    assert engine.terminator.halted is True

