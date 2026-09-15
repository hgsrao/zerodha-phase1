"""P0-3B-E fake-broker crash/restart matrix.

This module is intentionally hermetic: it imports no production runner or
broker SDK and every submission/observation is handled by an in-memory fake.
"""

from test_v34_entry_submit_restart_recovery import (
    AmbiguousSubmitBroker,
    FakeBroker,
    exact_entry_order,
    make_engine,
    make_state,
)


def fingerprint():
    return {
        "exchange": "NSE",
        "tradingsymbol": "INFY",
        "transaction_type": "BUY",
        "product": "MIS",
        "order_type": "LIMIT",
        "quantity": 1,
        "price": "100",
        "tag": "V3.4_ENTRY",
    }


def unknown_state():
    state = make_state()
    state.status = "ENTRY_UNKNOWN"
    state.active_trade.entry_submission_fingerprint = fingerprint()
    return state


class AcceptedButResponseLostBroker(AmbiguousSubmitBroker):
    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        self.orders = [exact_entry_order("ACCEPTED-1")]
        raise TimeoutError("broker accepted order; response was lost")


class InspectDurabilityBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.engine = None

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        assert self.engine.store.saved[-1] == "ENTRY_SUBMITTING"
        assert self.engine.state.active_trade.entry_submission_fingerprint == fingerprint()
        return "DURABLE-1"


class InvalidResponseBroker(FakeBroker):
    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        return None


def test_matrix_01_timeout_before_acceptance_never_resubmits():
    broker = AmbiguousSubmitBroker()
    engine = make_engine(make_state(), broker)

    assert engine.step() == "STATE_CHANGED"
    assert engine.state.status == "ENTRY_UNKNOWN"
    for _ in range(3):
        assert engine.step() == "NO_ACTION"

    assert len(broker.place_order_calls) == 1
    assert engine.terminator.halted is False


def test_matrix_02_timeout_after_acceptance_recovers_broker_order():
    broker = AcceptedButResponseLostBroker()
    engine = make_engine(make_state(), broker)

    assert engine.step() == "STATE_CHANGED"
    assert engine.state.status == "ENTRY_UNKNOWN"
    assert engine.step() == "STATE_CHANGED"
    assert engine.state.status == "ENTRY_PENDING"
    assert engine.state.active_trade.entry_order_id == "ACCEPTED-1"
    assert len(broker.place_order_calls) == 1


def test_matrix_03_delayed_order_visibility_recovers_without_resubmission():
    broker = AmbiguousSubmitBroker()
    engine = make_engine(make_state(), broker)

    assert engine.step() == "STATE_CHANGED"
    assert engine.step() == "NO_ACTION"
    broker.orders = [exact_entry_order("DELAYED-1")]
    assert engine.step() == "STATE_CHANGED"

    assert engine.state.status == "ENTRY_PENDING"
    assert engine.state.active_trade.entry_order_id == "DELAYED-1"
    assert len(broker.place_order_calls) == 1


def test_matrix_04_duplicate_exact_orders_fail_closed():
    broker = FakeBroker(
        orders=[exact_entry_order("DUP-1"), exact_entry_order("DUP-2")]
    )
    engine = make_engine(unknown_state(), broker)

    assert engine.step() == "HALTED"
    assert engine.state.status == "RECONCILIATION_HALT"
    assert engine.terminator.halted is True
    assert broker.place_order_calls == []


def test_matrix_05_malformed_broker_observation_fails_closed():
    broker = FakeBroker(orders=[{"order_id": "MALFORMED"}])
    engine = make_engine(unknown_state(), broker)

    assert engine.step() == "HALTED"
    assert engine.state.status == "RECONCILIATION_HALT"
    assert engine.terminator.halted is True
    assert broker.place_order_calls == []


def test_matrix_06_reconciliation_budget_exhaustion_halts_no_resubmit():
    broker = FakeBroker()
    engine = make_engine(unknown_state(), broker)

    for _ in range(3):
        assert engine.step() == "NO_ACTION"
    assert engine.step() == "HALTED"

    assert engine.state.status == "RECONCILIATION_HALT"
    assert engine.terminator.halted is True
    assert broker.place_order_calls == []


def test_matrix_07_entry_submitting_is_durable_before_buy_call():
    broker = InspectDurabilityBroker()
    engine = make_engine(make_state(), broker)
    broker.engine = engine

    assert engine.step() == "STATE_CHANGED"
    assert engine.state.status == "ENTRY_PENDING"
    assert engine.state.active_trade.entry_order_id == "DURABLE-1"
    assert len(broker.place_order_calls) == 1


def test_matrix_08_invalid_submission_response_becomes_unknown_no_resubmit():
    broker = InvalidResponseBroker()
    engine = make_engine(make_state(), broker)

    assert engine.step() == "STATE_CHANGED"
    assert engine.state.status == "ENTRY_UNKNOWN"
    assert engine.step() == "NO_ACTION"
    assert len(broker.place_order_calls) == 1
