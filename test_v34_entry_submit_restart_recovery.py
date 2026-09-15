from decimal import Decimal

from institutional_engine_v34_p01d_candidate import (
    BotState,
    Config,
    TradeContext,
    TradingEngineV34,
)


class FakeClock:
    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)


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
    def __init__(self, positions=None, orders=None):
        self.positions = positions or []
        self.orders = orders or []
        self.place_order_calls = []

    def get_positions(self):
        return self.positions

    def get_orders(self):
        return self.orders

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        raise AssertionError(
            "SAFETY VIOLATION: reconcile_startup() attempted order submission"
        )


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
    def __init__(self):
        self.halted = False
        self.reason = None

    def halt(self, reason):
        self.halted = True
        self.reason = reason


def make_state():
    return BotState(
        trading_day="2026-08-12",
        status="ENTRY_SUBMIT",
        active_trade=TradeContext(
            symbol="INFY",
            entry_tag="V3.4_ENTRY",
            target_qty=1,
            tranche_qty=1,
            filled_qty=0,
            pending_qty=1,
            entry_order_id=None,
            entry_price=Decimal("100"),
            order_status="PENDING",
        ),
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
        cfg=Config(
            alert_webhook_url="",
            max_daily_loss=Decimal("2000"),
        ),
    )


def test_entry_submit_restart_clean_broker_is_recovered_without_submission():
    state = make_state()
    broker = FakeBroker()

    engine = make_engine(state, broker)

    # __init__ must not destroy the persisted ENTRY_SUBMIT state.
    assert engine.state.status == "ENTRY_SUBMIT"

    engine.reconcile_startup()

    assert engine.state.status == "ENTRY_SUBMIT"
    assert engine.state.active_trade is not None
    assert engine.state.active_trade.entry_order_id is None
    assert broker.place_order_calls == []
    assert engine.terminator.halted is False


def test_entry_submit_restart_with_active_position_halts():
    state = make_state()

    broker = FakeBroker(
        positions=[
            {
                "tradingsymbol": "INFY",
                "exchange": "NSE",
                "product": "MIS",
                "quantity": 1,
                "average_price": 100,
            }
        ]
    )

    engine = make_engine(state, broker)
    engine.reconcile_startup()

    assert engine.terminator.halted is True
    assert engine.state.status == "RECONCILIATION_HALT"
    assert broker.place_order_calls == []


def test_entry_submit_restart_with_active_order_halts():
    state = make_state()

    broker = FakeBroker(
        orders=[
            {
                "order_id": "UNMAPPED-1",
                "tradingsymbol": "INFY",
                "exchange": "NSE",
                "product": "MIS",
                "transaction_type": "BUY",
                "quantity": 1,
                "filled_quantity": 0,
                "price": 100,
                "status": "OPEN",
                "tag": "V3.4_ENTRY",
            }
        ]
    )

    engine = make_engine(state, broker)
    engine.reconcile_startup()

    assert engine.terminator.halted is True
    assert engine.state.status == "RECONCILIATION_HALT"
    assert broker.place_order_calls == []


class AmbiguousSubmitBroker(FakeBroker):
    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        raise TimeoutError("response lost after submission")


def exact_entry_order(order_id="ENTRY-1"):
    return {
        "order_id": order_id,
        "tradingsymbol": "INFY",
        "exchange": "NSE",
        "product": "MIS",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "quantity": 1,
        "filled_quantity": 0,
        "price": 100,
        "status": "OPEN",
        "tag": "V3.4_ENTRY",
    }


def test_ambiguous_entry_submission_persists_unknown_and_never_retries_buy():
    engine = make_engine(make_state(), AmbiguousSubmitBroker())

    assert engine.step() == "STATE_CHANGED"
    assert engine.state.status == "ENTRY_UNKNOWN"
    assert engine.state.active_trade.entry_submission_fingerprint is not None
    assert engine.terminator.halted is False
    assert len(engine.broker.place_order_calls) == 1

    assert engine.step() == "NO_ACTION"
    assert engine.state.status == "ENTRY_UNKNOWN"
    assert len(engine.broker.place_order_calls) == 1


def test_entry_unknown_restart_recovers_exact_order_without_submission():
    state = make_state()
    state.status = "ENTRY_UNKNOWN"
    state.active_trade.entry_submission_fingerprint = {
        "exchange": "NSE", "tradingsymbol": "INFY",
        "transaction_type": "BUY", "product": "MIS",
        "order_type": "LIMIT", "quantity": 1,
        "price": "100", "tag": "V3.4_ENTRY",
    }
    broker = FakeBroker(orders=[exact_entry_order()])
    engine = make_engine(state, broker)

    engine.reconcile_startup()

    assert engine.state.status == "ENTRY_PENDING"
    assert engine.state.active_trade.entry_order_id == "ENTRY-1"
    assert broker.place_order_calls == []
    assert engine.terminator.halted is False


def test_entry_unknown_multiple_exact_orders_halts_without_submission():
    state = make_state()
    state.status = "ENTRY_UNKNOWN"
    state.active_trade.entry_submission_fingerprint = {
        "exchange": "NSE", "tradingsymbol": "INFY",
        "transaction_type": "BUY", "product": "MIS",
        "order_type": "LIMIT", "quantity": 1,
        "price": "100", "tag": "V3.4_ENTRY",
    }
    broker = FakeBroker(
        orders=[exact_entry_order("ENTRY-1"), exact_entry_order("ENTRY-2")]
    )
    engine = make_engine(state, broker)

    engine.reconcile_startup()

    assert engine.state.status == "RECONCILIATION_HALT"
    assert engine.terminator.halted is True
    assert broker.place_order_calls == []

