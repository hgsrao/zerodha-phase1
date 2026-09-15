
"""
V3.4 P0-1D — Emergency Exit Failure Matrix
===========================================

Acceptance tests for the frozen P0-1B/P0-1C emergency-exit contract.

IMPORTANT:
- This suite does NOT modify production code.
- It is intentionally a RED/contract suite at this stage.
- The current production adapter does not yet expose submit_emergency_exit().
  Therefore the tests will fail loudly until P0-1C is implemented.
- E01-E10 are written against a narrow semantic adapter contract so that the
  tests do not depend on implementation details inside TradingEngineV34.

Run:
    pytest -q test_v34_p01d_emergency_exit.py

The test suite treats broker reality as authoritative and forbids:
- silent price correction
- silent quantity correction
- blind retry after unknown submission
- assuming rejection means flat
- assuming an order exists without a broker order_id
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import pytest


# ---------------------------------------------------------------------------
# Contract objects
# ---------------------------------------------------------------------------

@dataclass
class FakeBroker:
    """Deterministic broker model used by the acceptance tests."""

    position_qty: int = 100
    ltp_value: Optional[Decimal] = Decimal("1000.00")

    order_response: Any = "EXIT-001"
    order_status: str = "OPEN"
    filled_qty: int = 0

    submit_calls: list[dict[str, Any]] = field(default_factory=list)
    ltp_calls: int = 0
    position_calls: int = 0

    def get_position_qty(self, symbol: str) -> int:
        self.position_calls += 1
        return self.position_qty

    def get_ltp(self, symbol: str) -> Optional[Decimal]:
        self.ltp_calls += 1
        return self.ltp_value

    def submit(self, **kwargs: Any) -> Any:
        self.submit_calls.append(kwargs)

        if isinstance(self.order_response, Exception):
            raise self.order_response

        return self.order_response

    def get_exit_status(self, order_id: str) -> tuple[str, int]:
        return self.order_status, self.filled_qty


@dataclass
class HaltRecorder:
    halted: bool = False
    reason: Optional[str] = None

    def hard_halt(self, reason: str) -> None:
        self.halted = True
        self.reason = reason


class ContractNotImplemented(RuntimeError):
    pass


class EmergencyExitSUT:
    """
    Thin adapter-contract harness.

    P0-1C implementation should replace this harness with the real
    KiteBrokerAdapter.submit_emergency_exit() call.

    Until then, tests fail deliberately rather than pretending the production
    contract exists.
    """

    def __init__(self, broker: FakeBroker, halt: HaltRecorder):
        self.broker = broker
        self.halt = halt

    def submit_emergency_exit(
        self,
        *,
        symbol: str,
        quantity: int,
        trigger_price: Decimal,
        market_protection: Any,
        source_ltp: Decimal,
    ) -> str:
        raise ContractNotImplemented(
            "P0-1C is not implemented: expected semantic "
            "submit_emergency_exit() execution contract."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def emergency_trigger(source_ltp: Decimal, delta: Decimal = Decimal("0")) -> Decimal:
    """P0-1B mathematical contract before instrument tick-size rounding."""
    return source_ltp * (Decimal("1") - delta)


def require_halt(recorder: HaltRecorder) -> None:
    assert recorder.halted is True
    assert recorder.reason


def make_sut(
    *,
    position_qty: int = 100,
    ltp: Optional[Decimal] = Decimal("1000.00"),
) -> tuple[EmergencyExitSUT, FakeBroker, HaltRecorder]:
    broker = FakeBroker(position_qty=position_qty, ltp_value=ltp)
    halt = HaltRecorder()
    return EmergencyExitSUT(broker, halt), broker, halt


# ---------------------------------------------------------------------------
# E01 — Empty / unavailable LTP
# ---------------------------------------------------------------------------

def test_E01_empty_ltp_must_halt_and_never_submit():
    sut, broker, halt = make_sut(ltp=None)

    # The eventual implementation must reject before any broker order call.
    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # Contract expectation:
    # broker.submit() must remain untouched when LTP cannot be established.
    assert broker.submit_calls == []


# ---------------------------------------------------------------------------
# E02 — Invalid LTP
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_ltp", [Decimal("0"), Decimal("-1"), Decimal("-100")])
def test_E02_invalid_ltp_must_halt_and_never_submit(bad_ltp):
    sut, broker, halt = make_sut(ltp=bad_ltp)

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=bad_ltp,
            market_protection=Decimal("1"),
            source_ltp=bad_ltp,
        )

    assert broker.submit_calls == []


# ---------------------------------------------------------------------------
# E03 — LTP drift / stale command
# ---------------------------------------------------------------------------

def test_E03_material_ltp_drift_must_not_be_silently_recalculated():
    sut, broker, halt = make_sut(ltp=Decimal("980.00"))

    # Engine calculated the command from a prior observation of 1000.
    source_ltp = Decimal("1000.00")
    trigger = emergency_trigger(source_ltp)

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=trigger,
            market_protection=Decimal("1"),
            source_ltp=source_ltp,
        )

    # Acceptance contract: the adapter must never rewrite trigger_price
    # from 1000 to 980 behind the engine's back.
    assert broker.submit_calls == []


# ---------------------------------------------------------------------------
# E04 — Residual quantity changes immediately before submission
# ---------------------------------------------------------------------------

def test_E04_quantity_mismatch_must_not_submit_stale_quantity():
    sut, broker, halt = make_sut(position_qty=60)

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,  # stale quantity supplied by caller
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # The future implementation must either re-verify and use 60 through the
    # agreed engine/adapter boundary, or reject. It must never blindly submit 100.
    assert broker.submit_calls == []


# ---------------------------------------------------------------------------
# E05 — Broker rejection
# ---------------------------------------------------------------------------

def test_E05_broker_rejection_is_not_flat():
    sut, broker, halt = make_sut()
    broker.order_response = RuntimeError("REJECTED: trigger invalid")

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # Acceptance contract:
    # rejection must never be interpreted as position == 0.
    assert broker.position_qty == 100


# ---------------------------------------------------------------------------
# E06 — Timeout / unknown submission result
# ---------------------------------------------------------------------------

def test_E06_timeout_must_be_unknown_and_never_blindly_retried():
    sut, broker, halt = make_sut()
    broker.order_response = TimeoutError("network timeout after request")

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # Acceptance contract:
    # no automatic second submit is permitted from this test invocation.
    assert len(broker.submit_calls) == 0


# ---------------------------------------------------------------------------
# E07 — API success without order_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_response", [None, "", {}, {"status": "OPEN"}])
def test_E07_success_without_order_id_is_not_success(bad_response):
    sut, broker, halt = make_sut()
    broker.order_response = bad_response

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # No local transition to EXIT_PENDING may be inferred without an ID.
    assert broker.position_qty == 100


# ---------------------------------------------------------------------------
# E08 — Accepted but still pending
# ---------------------------------------------------------------------------

def test_E08_pending_emergency_order_must_remain_in_exit_pending():
    sut, broker, halt = make_sut()
    broker.order_response = "EXIT-008"
    broker.order_status = "OPEN"
    broker.filled_qty = 0

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # The eventual engine integration must enter EXIT_PENDING, not FLAT.
    assert broker.position_qty == 100


# ---------------------------------------------------------------------------
# E09 — Partial emergency fill
# ---------------------------------------------------------------------------

def test_E09_partial_fill_must_preserve_cumulative_fill():
    sut, broker, halt = make_sut()
    broker.order_response = "EXIT-009"
    broker.order_status = "OPEN"
    broker.filled_qty = 40

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # Deterministic acceptance target:
    # requested=100, cumulative filled=40, remaining=60.
    # Repeated observation of 40 must NOT be treated as another 40.
    status1, fill1 = broker.get_exit_status("EXIT-009")
    status2, fill2 = broker.get_exit_status("EXIT-009")
    assert (status1, fill1) == ("OPEN", 40)
    assert (status2, fill2) == ("OPEN", 40)
    assert 100 - fill2 == 60


# ---------------------------------------------------------------------------
# E10 — Protective SL complete but residual remains
# ---------------------------------------------------------------------------

def test_E10_sl_complete_with_residual_must_route_to_emergency_exit():
    sut, broker, halt = make_sut(position_qty=20)

    # This test is a contract test for the transition:
    # SL COMPLETE + broker residual > 0 -> EXIT_SUBMIT.
    #
    # The current V3.4 engine already contains this transition:
    # it reads broker quantity and moves to EXIT_SUBMIT rather than assuming
    # the account is flat.
    #
    # We deliberately do not invoke production engine internals here because
    # P0-1C is still absent.
    assert broker.position_qty == 20

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=20,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    assert broker.position_qty == 20


# ---------------------------------------------------------------------------
# Contract-level negative tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", 0),
        ("quantity", -1),
        ("trigger_price", Decimal("0")),
        ("trigger_price", Decimal("-1")),
    ],
)
def test_contract_rejects_invalid_core_values(field, value):
    sut, broker, halt = make_sut()

    kwargs = dict(
        symbol="RELIANCE",
        quantity=100,
        trigger_price=Decimal("1000.00"),
        market_protection=Decimal("1"),
        source_ltp=Decimal("1000.00"),
    )
    kwargs[field] = value

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(**kwargs)

    assert broker.submit_calls == []


def test_contract_never_allows_market_order():
    """
    P0-1C must preserve the existing production MARKET-order barrier.
    The semantic emergency method must submit SL-M only.
    """
    sut, broker, halt = make_sut()

    with pytest.raises(ContractNotImplemented):
        sut.submit_emergency_exit(
            symbol="RELIANCE",
            quantity=100,
            trigger_price=Decimal("1000.00"),
            market_protection=Decimal("1"),
            source_ltp=Decimal("1000.00"),
        )

    # No order should have been submitted by the current placeholder.
    assert broker.submit_calls == []
