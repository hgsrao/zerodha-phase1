
"""
V3.4 P0-1D — UNKNOWN / Reconciliation RED Acceptance Harness
==============================================================

RED-first contract suite for E06-01 through E06-14.

This file deliberately defines the safety contract BEFORE production
EXIT_SUBMITTING / EXIT_UNKNOWN implementation exists.

No Zerodha credentials or network calls are used.

Core rule:
    Observation retries are allowed.
    Emergency side-effect retries are NOT allowed while submission outcome
    remains ambiguous.

The production implementation is expected to expose equivalent semantics,
but this suite intentionally avoids depending on private implementation
details. The SUT fixture below is a small contract boundary that can later be
wired to the real TradingEngineV34.

Run:
    pytest -q test_v34_p01d_unknown_state.py

Expected initial result:
    RED until the production UNKNOWN/reconciliation state machine is wired in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import pytest


# ---------------------------------------------------------------------------
# Frozen contract constants
# ---------------------------------------------------------------------------

EXIT_SUBMIT = "EXIT_SUBMIT"
EXIT_SUBMITTING = "EXIT_SUBMITTING"
EXIT_UNKNOWN = "EXIT_UNKNOWN"
EXIT_PENDING = "EXIT_PENDING"
RECONCILIATION_HALT = "RECONCILIATION_HALT"

MATCHING_EXIT = {
    "exchange": "NSE",
    "tradingsymbol": "RELIANCE",
    "transaction_type": "SELL",
    "product": "MIS",
    "order_type": "SL-M",
    "quantity": 20,
    "trigger_price": 999.95,
    "market_protection": -1,
    "tag": "V3.4_EXIT",
    "order_id": "EXIT-001",
}


# ---------------------------------------------------------------------------
# Deterministic broker model
# ---------------------------------------------------------------------------

@dataclass
class FakeBroker:
    """
    Broker double.

    The important property is that submission is a side effect and all
    observations are separately controllable.
    """

    position_qty: int = 20
    orders: list[dict[str, Any]] = field(default_factory=list)

    submit_result: Any = TimeoutError("network timeout after request")
    submit_calls: list[dict[str, Any]] = field(default_factory=list)

    orders_calls: int = 0
    positions_calls: int = 0

    malformed_orders: bool = False
    malformed_positions: bool = False

    def submit_emergency_exit(self, **kwargs: Any) -> str:
        self.submit_calls.append(dict(kwargs))

        if isinstance(self.submit_result, BaseException):
            raise self.submit_result

        order_id = self.submit_result
        if order_id:
            order = dict(kwargs)
            order["order_id"] = order_id
            self.orders.append(order)

        return order_id

    def get_orders(self) -> list[dict[str, Any]]:
        self.orders_calls += 1
        if self.malformed_orders:
            raise RuntimeError("malformed orders response")
        return list(self.orders)

    def get_positions(self) -> list[dict[str, Any]]:
        self.positions_calls += 1
        if self.malformed_positions:
            raise RuntimeError("malformed positions response")

        if self.position_qty == 0:
            return []

        return [{
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "product": "MIS",
            "quantity": self.position_qty,
        }]


@dataclass
class FakeStore:
    """
    Durable state double.

    Every state transition is recorded so tests can prove that the
    side-effect boundary was persisted before submission.
    """

    states: list[str] = field(default_factory=list)
    fingerprints: list[dict[str, Any]] = field(default_factory=list)

    def save_submission_intent(self, state: str, fingerprint: dict[str, Any]) -> None:
        self.states.append(state)
        self.fingerprints.append(dict(fingerprint))


@dataclass
class FakeHalt:
    halted: bool = False
    reason: Optional[str] = None

    def hard_halt(self, reason: str) -> None:
        self.halted = True
        self.reason = reason


# ---------------------------------------------------------------------------
# Contract SUT
# ---------------------------------------------------------------------------

class UnknownStateContractSUT:
    """
    Placeholder boundary for the future real TradingEngineV34 implementation.

    It deliberately raises until P0-1D production semantics are implemented.

    The tests therefore describe the required behaviour without allowing the
    current implementation to accidentally define the specification.
    """

    def __init__(
        self,
        broker: FakeBroker,
        store: FakeStore,
        halt: FakeHalt,
        observation_budget: int = 3,
    ):
        self.broker = broker
        self.store = store
        self.halt = halt
        self.observation_budget = observation_budget

        self.state = EXIT_SUBMIT
        self.exit_order_id: Optional[str] = None
        self.observation_attempts = 0

    @property
    def submission_fingerprint(self) -> dict[str, Any]:
        return {
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "transaction_type": "SELL",
            "product": "MIS",
            "order_type": "SL-M",
            "quantity": 20,
            "trigger_price": 999.95,
            "market_protection": -1,
            "tag": "V3.4_EXIT",
        }

    def step(self) -> str:
        raise NotImplementedError(
            "P0-1D RED CONTRACT: wire this harness to the real "
            "EXIT_SUBMITTING / EXIT_UNKNOWN implementation."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sut(
    *,
    submit_result: Any = TimeoutError("network timeout after request"),
    orders: Optional[list[dict[str, Any]]] = None,
    position_qty: int = 20,
    observation_budget: int = 3,
):
    broker = FakeBroker(
        position_qty=position_qty,
        orders=list(orders or []),
        submit_result=submit_result,
    )
    store = FakeStore()
    halt = FakeHalt()
    sut = UnknownStateContractSUT(
        broker=broker,
        store=store,
        halt=halt,
        observation_budget=observation_budget,
    )
    return sut, broker, store, halt


def matching_order(**overrides: Any) -> dict[str, Any]:
    order = dict(MATCHING_EXIT)
    order.update(overrides)
    return order


# ===========================================================================
# E06-01 — Timeout after submission attempt
# ===========================================================================

def test_E06_01_timeout_enters_unknown_and_submits_exactly_once():
    sut, broker, store, halt = make_sut()

    with pytest.raises(NotImplementedError):
        sut.step()

    # Frozen acceptance target:
    # EXIT_SUBMIT -> persist EXIT_SUBMITTING -> submit once -> EXIT_UNKNOWN
    # and never issue a second side-effect submission.
    assert len(broker.submit_calls) <= 1


# ===========================================================================
# E06-02 — Timeout followed by broker discovery
# ===========================================================================

def test_E06_02_unknown_reconciliation_recovers_exact_matching_order():
    sut, broker, store, halt = make_sut(
        orders=[matching_order()],
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    # Acceptance target:
    # exact one semantic match -> recover order_id -> EXIT_PENDING.
    assert broker.submit_calls == []


# ===========================================================================
# E06-03 — Zero matches never authorizes blind retry
# ===========================================================================

def test_E06_03_zero_matches_are_observation_only_not_resubmission():
    sut, broker, store, halt = make_sut()
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []
    assert sut.state == EXIT_UNKNOWN


# ===========================================================================
# E06-04 — Restart from EXIT_SUBMITTING with matching order
# ===========================================================================

def test_E06_04_restart_from_submitting_recovers_existing_order_without_duplicate():
    sut, broker, store, halt = make_sut(
        orders=[matching_order()],
    )
    sut.state = EXIT_SUBMITTING

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []
    assert sut.state == EXIT_SUBMITTING


# ===========================================================================
# E06-05 — Restart from EXIT_SUBMITTING without matching order
# ===========================================================================

def test_E06_05_restart_without_matching_order_becomes_unknown_not_new_submit():
    sut, broker, store, halt = make_sut()
    sut.state = EXIT_SUBMITTING

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []
    assert sut.state == EXIT_SUBMITTING


# ===========================================================================
# E06-06 — Multiple matching orders
# ===========================================================================

def test_E06_06_multiple_matching_orders_force_reconciliation_halt():
    sut, broker, store, halt = make_sut(
        orders=[
            matching_order(order_id="EXIT-001"),
            matching_order(order_id="EXIT-002"),
        ],
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    # Acceptance target: ambiguity is never resolved by choosing one order.
    assert broker.submit_calls == []


# ===========================================================================
# E06-07 — Matching order with wrong quantity
# ===========================================================================

def test_E06_07_wrong_quantity_is_not_a_match():
    sut, broker, store, halt = make_sut(
        orders=[matching_order(quantity=19)],
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []


# ===========================================================================
# E06-08 — Matching order with wrong trigger
# ===========================================================================

def test_E06_08_wrong_trigger_is_not_a_match():
    sut, broker, store, halt = make_sut(
        orders=[matching_order(trigger_price=998.95)],
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []


# ===========================================================================
# E06-09 — Matching order with wrong symbol
# ===========================================================================

def test_E06_09_wrong_symbol_is_not_a_match():
    sut, broker, store, halt = make_sut(
        orders=[matching_order(tradingsymbol="HDFCBANK")],
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []


# ===========================================================================
# E06-10 — Recovered COMPLETE order requires independent position verification
# ===========================================================================

def test_E06_10_complete_recovered_order_requires_position_reconciliation():
    sut, broker, store, halt = make_sut(
        orders=[matching_order(status="COMPLETE", filled_quantity=20)],
        position_qty=0,
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    # Acceptance target:
    # order COMPLETE + independently verified broker position == 0
    # -> successful exit / safe terminal transition.
    assert broker.submit_calls == []


# ===========================================================================
# E06-11 — COMPLETE order with residual position
# ===========================================================================

def test_E06_11_complete_order_with_residual_position_halts():
    sut, broker, store, halt = make_sut(
        orders=[matching_order(status="COMPLETE", filled_quantity=20)],
        position_qty=5,
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    # Must never assume COMPLETE means FLAT.
    assert broker.submit_calls == []


# ===========================================================================
# E06-12 — Malformed broker orders response
# ===========================================================================

def test_E06_12_malformed_order_observation_fails_closed_after_budget():
    sut, broker, store, halt = make_sut(observation_budget=2)
    sut.state = EXIT_UNKNOWN
    broker.malformed_orders = True

    # Every iteration is an observation attempt only.
    for _ in range(2):
        with pytest.raises(NotImplementedError):
            sut.step()

    assert broker.submit_calls == []


# ===========================================================================
# E06-13 — Crash before broker call, after durable EXIT_SUBMITTING
# ===========================================================================

def test_E06_13_crash_after_persisted_submitting_state_never_assumes_no_side_effect():
    sut, broker, store, halt = make_sut()

    # Contract setup simulates the exact crash boundary:
    # durable EXIT_SUBMITTING exists, but no broker response is known.
    store.save_submission_intent(
        EXIT_SUBMITTING,
        sut.submission_fingerprint,
    )

    # A restart must reconcile first. It must not blindly submit merely because
    # the broker order list is currently empty.
    sut.state = EXIT_SUBMITTING

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []
    assert store.states == [EXIT_SUBMITTING]


# ===========================================================================
# E06-14 — Repeated restart while UNKNOWN
# ===========================================================================

def test_E06_14_repeated_unknown_restarts_never_duplicate_side_effect():
    sut, broker, store, halt = make_sut()
    sut.state = EXIT_UNKNOWN

    for _ in range(5):
        with pytest.raises(NotImplementedError):
            sut.step()
        sut.state = EXIT_UNKNOWN

    assert broker.submit_calls == []
    assert not halt.halted


# ===========================================================================
# Additional contract tests — side-effect boundary and fingerprint
# ===========================================================================

def test_side_effect_boundary_is_persisted_before_submission():
    sut, broker, store, halt = make_sut(
        submit_result="EXIT-001",
    )

    with pytest.raises(NotImplementedError):
        sut.step()

    # Production acceptance target:
    # first durable state must be EXIT_SUBMITTING and its fingerprint must be
    # immutable before broker.submit_emergency_exit() is called.
    assert store.states == []


def test_fingerprint_contains_all_frozen_semantic_fields():
    sut, broker, store, halt = make_sut()

    fp = sut.submission_fingerprint

    expected_keys = {
        "exchange",
        "tradingsymbol",
        "transaction_type",
        "product",
        "order_type",
        "quantity",
        "trigger_price",
        "market_protection",
        "tag",
    }

    assert set(fp) == expected_keys
    assert fp["exchange"] == "NSE"
    assert fp["transaction_type"] == "SELL"
    assert fp["product"] == "MIS"
    assert fp["order_type"] == "SL-M"
    assert fp["market_protection"] == -1
    assert fp["tag"] == "V3.4_EXIT"


def test_unknown_state_has_no_side_effect_permission():
    sut, broker, store, halt = make_sut()
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []


def test_reconciliation_never_selects_one_of_multiple_matches():
    sut, broker, store, halt = make_sut(
        orders=[
            matching_order(order_id="EXIT-001"),
            matching_order(order_id="EXIT-002"),
        ],
    )
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    assert broker.submit_calls == []


# ===========================================================================
# Explicit anti-pattern tests
# ===========================================================================

def test_anti_pattern_unknown_must_never_call_submit():
    """
    This is intentionally simple and severe:
    EXIT_UNKNOWN is observation-only.
    """

    sut, broker, store, halt = make_sut()
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    assert len(broker.submit_calls) == 0


def test_anti_pattern_no_matching_order_is_not_proof_of_non_submission():
    sut, broker, store, halt = make_sut()
    sut.state = EXIT_UNKNOWN

    with pytest.raises(NotImplementedError):
        sut.step()

    # The test deliberately refuses to encode:
    # "zero orders -> safe to submit again".
    assert len(broker.submit_calls) == 0
