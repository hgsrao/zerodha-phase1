"""Position lifecycle and ownership contract (Engine A -> Engine B hand-off, phase 1).

Execution-neutral: this module only defines WHO owns an open position and which ownership
changes are legal.  It sends no orders, moves no stops and performs no hand-off; nothing in the
replay path creates a ``B_OPEN`` record yet.

States and legal transitions::

    A_OPEN --request--> TRANSFER_REQUESTED --acknowledge--> B_OPEN --close--> CLOSED
       |                      |  (reject / timeout -> A_OPEN)                    ^
       |                      +--close---------------------------------------->|
       +--close------------------------------------------------------------>---+

* ``A_OPEN`` and ``TRANSFER_REQUESTED``: owner ``ENGINE_A``, product ``MIS``.  Engine A keeps
  ownership (and therefore its intraday square-off duty) until the transfer is acknowledged.
* ``B_OPEN``: owner ``ENGINE_B``, product ``CNC``.  Reachable only by acknowledging a pending
  request; ``A_OPEN -> B_OPEN`` directly is rejected.
* ``TRANSFER_REJECTED`` is an event, not a state: ``reject_transfer`` returns the position to
  ``A_OPEN`` with Engine A still the owner.
* ``CLOSED`` is terminal.

Identity, side, anchor and initial risk never change across a transition; the stop may only
tighten (BUY: rise, SELL: fall).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Dict, FrozenSet

import pandas as pd

ENGINE_A = "ENGINE_A"
ENGINE_B = "ENGINE_B"
PRODUCT_MIS = "MIS"
PRODUCT_CNC = "CNC"

A_OPEN = "A_OPEN"
TRANSFER_REQUESTED = "TRANSFER_REQUESTED"
B_OPEN = "B_OPEN"
CLOSED = "CLOSED"

DIRECTIONS = ("BUY", "SELL")
OWNERS = (ENGINE_A, ENGINE_B)
PRODUCTS = (PRODUCT_MIS, PRODUCT_CNC)
LIFECYCLE_STATES = (A_OPEN, TRANSFER_REQUESTED, B_OPEN, CLOSED)

# (owner, product) each state requires.  CLOSED keeps whatever the position last had.
_STATE_OWNERSHIP = {
    A_OPEN: (ENGINE_A, PRODUCT_MIS),
    TRANSFER_REQUESTED: (ENGINE_A, PRODUCT_MIS),
    B_OPEN: (ENGINE_B, PRODUCT_CNC),
}

LEGAL_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    A_OPEN: frozenset({TRANSFER_REQUESTED, CLOSED}),
    TRANSFER_REQUESTED: frozenset({B_OPEN, A_OPEN, CLOSED}),   # ack / reject-or-timeout / forced close
    B_OPEN: frozenset({CLOSED}),
    CLOSED: frozenset(),
}


class PositionLifecycleError(ValueError):
    """An illegal record or an illegal ownership transition."""


@dataclass(frozen=True)
class PositionLifecycleRecord:
    position_id: str
    symbol: str
    direction: str
    owner_engine: str
    product: str
    lifecycle_state: str
    initial_risk_r: float          # 1R at entry, in price units per share (|anchor - initial stop|)
    anchor_price: float            # entry fill price the risk and R are measured from
    current_stop_price: float
    created_bar_timestamp: pd.Timestamp

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, str) or not self.position_id:
            raise PositionLifecycleError("position_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise PositionLifecycleError("symbol must be a non-empty string")
        if self.direction not in DIRECTIONS:
            raise PositionLifecycleError(f"direction must be one of {DIRECTIONS}, got {self.direction!r}")
        if self.owner_engine not in OWNERS:
            raise PositionLifecycleError(f"owner_engine must be one of {OWNERS}, got {self.owner_engine!r}")
        if self.product not in PRODUCTS:
            raise PositionLifecycleError(f"product must be one of {PRODUCTS}, got {self.product!r}")
        if self.lifecycle_state not in LIFECYCLE_STATES:
            raise PositionLifecycleError(f"lifecycle_state must be one of {LIFECYCLE_STATES}, got {self.lifecycle_state!r}")
        for name in ("initial_risk_r", "anchor_price", "current_stop_price"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise PositionLifecycleError(f"{name} must be a finite number, got {value!r}")
        if self.initial_risk_r <= 0.0:
            raise PositionLifecycleError("initial_risk_r must be positive")
        if self.anchor_price <= 0.0:
            raise PositionLifecycleError("anchor_price must be positive")
        if not isinstance(self.created_bar_timestamp, pd.Timestamp) or pd.isna(self.created_bar_timestamp):
            raise PositionLifecycleError("created_bar_timestamp must be a valid pd.Timestamp")
        expected = _STATE_OWNERSHIP.get(self.lifecycle_state)
        if expected is not None and (self.owner_engine, self.product) != expected:
            raise PositionLifecycleError(
                f"state {self.lifecycle_state} requires owner/product {expected}, "
                f"got {(self.owner_engine, self.product)}")

    @property
    def is_open(self) -> bool:
        return self.lifecycle_state != CLOSED


def open_position(*, position_id: str, symbol: str, direction: str, initial_risk_r: float, anchor_price: float,
                  initial_stop_price: float, created_bar_timestamp: pd.Timestamp) -> PositionLifecycleRecord:
    """A freshly filled position: Engine A, MIS, ``A_OPEN``.  The initial stop must protect (BUY: below, SELL: above the anchor)."""
    if direction not in DIRECTIONS:
        raise PositionLifecycleError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    try:
        protective = initial_stop_price < anchor_price if direction == "BUY" else initial_stop_price > anchor_price
    except TypeError as exc:
        raise PositionLifecycleError(f"initial_stop_price/anchor_price must be numbers: {exc}") from exc
    if not protective:
        raise PositionLifecycleError(
            f"initial stop {initial_stop_price!r} is not on the protective side of anchor {anchor_price!r} for {direction}")
    return PositionLifecycleRecord(
        position_id=position_id, symbol=symbol, direction=direction, owner_engine=ENGINE_A, product=PRODUCT_MIS,
        lifecycle_state=A_OPEN, initial_risk_r=initial_risk_r, anchor_price=anchor_price,
        current_stop_price=initial_stop_price, created_bar_timestamp=created_bar_timestamp)


def transition(record: PositionLifecycleRecord, new_state: str) -> PositionLifecycleRecord:
    """Return the record in ``new_state`` with owner and product set to what that state requires.

    Raises ``PositionLifecycleError`` for any transition not in ``LEGAL_TRANSITIONS``."""
    if new_state not in LIFECYCLE_STATES:
        raise PositionLifecycleError(f"unknown lifecycle state {new_state!r}")
    if new_state not in LEGAL_TRANSITIONS[record.lifecycle_state]:
        hint = (" (a transfer must be requested and acknowledged first)"
                if (record.lifecycle_state, new_state) == (A_OPEN, B_OPEN) else "")
        raise PositionLifecycleError(f"illegal transition {record.lifecycle_state} -> {new_state}{hint}")
    owner, product = _STATE_OWNERSHIP.get(new_state, (record.owner_engine, record.product))
    return replace(record, lifecycle_state=new_state, owner_engine=owner, product=product)


def request_transfer(record: PositionLifecycleRecord) -> PositionLifecycleRecord:
    return transition(record, TRANSFER_REQUESTED)


def acknowledge_transfer(record: PositionLifecycleRecord) -> PositionLifecycleRecord:
    """Engine B accepted: ownership flips to ENGINE_B / CNC.  Only valid from ``TRANSFER_REQUESTED``."""
    return transition(record, B_OPEN)


def reject_transfer(record: PositionLifecycleRecord) -> PositionLifecycleRecord:
    """Engine B declined or the request timed out: the position stays with Engine A as ``A_OPEN``."""
    if record.lifecycle_state != TRANSFER_REQUESTED:
        raise PositionLifecycleError(f"only a TRANSFER_REQUESTED position can be rejected, state is {record.lifecycle_state}")
    return transition(record, A_OPEN)


def close_position(record: PositionLifecycleRecord) -> PositionLifecycleRecord:
    return transition(record, CLOSED)


def tighten_stop(record: PositionLifecycleRecord, new_stop_price: float) -> PositionLifecycleRecord:
    """Move the protective stop one way only (BUY up, SELL down); loosening raises."""
    if not record.is_open:
        raise PositionLifecycleError("cannot move the stop of a CLOSED position")
    if isinstance(new_stop_price, bool) or not isinstance(new_stop_price, (int, float)) or not isfinite(new_stop_price):
        raise PositionLifecycleError(f"new stop must be a finite number, got {new_stop_price!r}")
    loosens = (new_stop_price < record.current_stop_price if record.direction == "BUY"
               else new_stop_price > record.current_stop_price)
    if loosens:
        raise PositionLifecycleError(
            f"stop may only tighten: {record.direction} stop {record.current_stop_price!r} -> {new_stop_price!r} loosens it")
    return replace(record, current_stop_price=float(new_stop_price))
