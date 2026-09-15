"""Regression coverage for the NSE trading-window gate on request_entry().

This gate is defense-in-depth: it refuses to create a *new* entry intent
outside the NSE regular session or on a configured holiday. It must never
block management/exit of an already active trade, and it must never touch
the broker (it is a pure local clock/calendar check).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from institutional_engine_v34_p01d_candidate import (
    BotState,
    Config,
    NSE_HOLIDAYS_2026,
    TradeContext,
    TradingEngineV34,
    describe_nse_trading_window,
)

IST_OFFSET = timezone.utc  # inputs below are constructed already-offset to IST wall time


class FakeStore:
    def __init__(self, state):
        self.state = state
        self.saved = []

    def load(self, *args):
        return self.state

    def save(self, state):
        self.state = state
        self.saved.append(state.status)


class FakeBroker:
    def __init__(self):
        self.calls = []

    def get_positions(self):
        self.calls.append("get_positions")
        return []

    def get_orders(self):
        self.calls.append("get_orders")
        return []

    def place_order(self, **kwargs):
        self.calls.append(("place_order", kwargs))
        raise AssertionError("SAFETY VIOLATION: request_entry() must never place an order.")


class FakeAudit:
    def __init__(self):
        self.events = []

    def log(self, event, **kwargs):
        self.events.append((event, kwargs))


class FakeAlert:
    def send(self, *args, **kwargs):
        pass


class FakeLock:
    def acquire(self):
        return object()


class FakeTerminator:
    halted = False


class FakeClockAt:
    """Returns a fixed, timezone-aware IST wall-clock time."""

    def __init__(self, ist_wall_time: datetime):
        assert ist_wall_time.tzinfo is not None
        self._now = ist_wall_time

    def now(self):
        return self._now


def _ist(y, mo, d, h, mi):
    from institutional_engine_v34_p01d_candidate import IST
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def make_flat_engine(clock):
    engine = TradingEngineV34(
        broker=FakeBroker(),
        clock=clock,
        sleeper=lambda *_: None,
        store=FakeStore(BotState(trading_day="2026-08-14", status="FLAT")),
        audit=FakeAudit(),
        alert=FakeAlert(),
        lock=FakeLock(),
        terminator=FakeTerminator(),
        cfg=Config(
            alert_webhook_url="",
            max_daily_loss=Decimal("2000"),
            holidays=NSE_HOLIDAYS_2026,
        ),
    )
    # __init__ never trusts a persisted FLAT state - it forces STARTUP and
    # requires reconciliation, exactly like a real process boot. The fake
    # broker reports a clean, empty account, so this settles back to FLAT.
    engine.reconcile_startup()
    assert engine.state.status == "FLAT"
    engine.broker.calls.clear()
    return engine


class TestDescribeNseTradingWindow:
    def test_open_during_regular_session(self):
        is_open, reason = describe_nse_trading_window(
            _ist(2026, 8, 14, 11, 0),
            holidays=NSE_HOLIDAYS_2026,
            market_open_time=Config.__dataclass_fields__["market_open_time"].default,
            market_close_time=Config.__dataclass_fields__["market_close_time"].default,
        )
        assert is_open is True
        assert reason == "MARKET_OPEN"

    def test_closed_before_open(self):
        is_open, reason = describe_nse_trading_window(
            _ist(2026, 8, 14, 9, 0),
            holidays=NSE_HOLIDAYS_2026,
            market_open_time=Config.__dataclass_fields__["market_open_time"].default,
            market_close_time=Config.__dataclass_fields__["market_close_time"].default,
        )
        assert is_open is False
        assert reason.startswith("MARKET_CLOSED_OUTSIDE_SESSION")

    def test_closed_at_close_boundary(self):
        # 15:30 is exclusive - the session is [09:15, 15:30).
        is_open, reason = describe_nse_trading_window(
            _ist(2026, 8, 14, 15, 30),
            holidays=NSE_HOLIDAYS_2026,
            market_open_time=Config.__dataclass_fields__["market_open_time"].default,
            market_close_time=Config.__dataclass_fields__["market_close_time"].default,
        )
        assert is_open is False

    def test_closed_on_weekend(self):
        # 2026-08-16 is a Sunday.
        is_open, reason = describe_nse_trading_window(
            _ist(2026, 8, 16, 11, 0),
            holidays=NSE_HOLIDAYS_2026,
            market_open_time=Config.__dataclass_fields__["market_open_time"].default,
            market_close_time=Config.__dataclass_fields__["market_close_time"].default,
        )
        assert is_open is False
        assert reason.startswith("MARKET_CLOSED_WEEKEND")

    def test_closed_on_holiday(self):
        # 2026-10-02 (Gandhi Jayanti) is a Friday, would otherwise be open.
        is_open, reason = describe_nse_trading_window(
            _ist(2026, 10, 2, 11, 0),
            holidays=NSE_HOLIDAYS_2026,
            market_open_time=Config.__dataclass_fields__["market_open_time"].default,
            market_close_time=Config.__dataclass_fields__["market_close_time"].default,
        )
        assert is_open is False
        assert reason.startswith("MARKET_CLOSED_HOLIDAY")

    def test_rejects_naive_datetime(self):
        with pytest.raises(ValueError):
            describe_nse_trading_window(
                datetime(2026, 8, 14, 11, 0),
                holidays=NSE_HOLIDAYS_2026,
                market_open_time=Config.__dataclass_fields__["market_open_time"].default,
                market_close_time=Config.__dataclass_fields__["market_close_time"].default,
            )


class TestRequestEntryTradingWindowGate:
    def test_request_entry_rejected_before_market_open(self):
        engine = make_flat_engine(FakeClockAt(_ist(2026, 8, 14, 8, 0)))
        with pytest.raises(RuntimeError, match="MARKET_CLOSED_OUTSIDE_SESSION"):
            engine.request_entry(symbol="INFY", quantity=1, price=Decimal("100"))
        assert engine.state.status == "FLAT"
        assert engine.broker.calls == []

    def test_request_entry_rejected_after_market_close(self):
        engine = make_flat_engine(FakeClockAt(_ist(2026, 8, 14, 16, 0)))
        with pytest.raises(RuntimeError, match="MARKET_CLOSED_OUTSIDE_SESSION"):
            engine.request_entry(symbol="INFY", quantity=1, price=Decimal("100"))
        assert engine.state.status == "FLAT"
        assert engine.broker.calls == []

    def test_request_entry_rejected_on_weekend(self):
        engine = make_flat_engine(FakeClockAt(_ist(2026, 8, 16, 11, 0)))
        with pytest.raises(RuntimeError, match="MARKET_CLOSED_WEEKEND"):
            engine.request_entry(symbol="INFY", quantity=1, price=Decimal("100"))
        assert engine.broker.calls == []

    def test_request_entry_rejected_on_holiday(self):
        engine = make_flat_engine(FakeClockAt(_ist(2026, 10, 2, 11, 0)))
        with pytest.raises(RuntimeError, match="MARKET_CLOSED_HOLIDAY"):
            engine.request_entry(symbol="INFY", quantity=1, price=Decimal("100"))
        assert engine.broker.calls == []

    def test_request_entry_accepted_during_regular_session(self):
        engine = make_flat_engine(FakeClockAt(_ist(2026, 8, 14, 11, 0)))
        result = engine.request_entry(symbol="INFY", quantity=1, price=Decimal("100"))
        assert result == "STATE_CHANGED"
        assert engine.state.status == "ENTRY_SUBMIT"
        assert engine.state.active_trade.symbol == "INFY"
        # The gate is local-only: it must not have consulted the broker.
        assert engine.broker.calls == []
