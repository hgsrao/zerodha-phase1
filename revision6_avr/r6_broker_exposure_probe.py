"""Offline-only R6 Engine B/C broker exposure snapshot.

Broker net positions include partial fills. Never count an
unbooked partial fill a second time. Only ACKNOWLEDGED,
unfilled entry quantities become committed capacity.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Mapping


class LedgerProbeError(ValueError):
    pass


@dataclass(frozen=True)
class BaySnapshot:
    engine: str
    bay_id: str
    bar_timestamp: str
    filled_notional: float
    acknowledged_pending_entry_notional: float
    bay_capital: float
    quantity_by_symbol: dict
    outstanding_symbols: frozenset


def integer(x, name, signed=False):
    if (
        isinstance(x, bool)
        or not isinstance(x, (int, float))
        or not isfinite(x)
        or int(x) != x
        or (not signed and x < 0)
    ):
        raise LedgerProbeError(f"{name}: invalid integer")
    return int(x)


def positive(x, name):
    if (
        isinstance(x, bool)
        or not isinstance(x, (int, float))
        or not isfinite(x)
        or x <= 0
    ):
        raise LedgerProbeError(f"{name}: invalid positive value")
    return float(x)


def snapshot(
    *,
    engine,
    variant,
    bay_id,
    symbol_to_bay,
    completed_bar_marks,
    bar_timestamp,
):
    if variant not in ("B", "C"):
        raise LedgerProbeError("Unknown execution engine")

    if not isinstance(bar_timestamp, str) or not bar_timestamp:
        raise LedgerProbeError("Missing bar timestamp")

    if getattr(engine, "_execution_halted", False):
        raise LedgerProbeError("Execution halted")

    if variant == "B" and getattr(
        engine, "reconciliation_breaks", None
    ):
        raise LedgerProbeError("Engine B reconciliation failure")

    if variant == "C":
        reconciler = getattr(engine, "reconciler", None)
        if reconciler is None:
            raise LedgerProbeError("Engine C reconciler missing")
        if getattr(reconciler, "breaks", None):
            raise LedgerProbeError("Engine C reconciliation failure")

    members = sorted(
        symbol
        for symbol, bay in symbol_to_bay.items()
        if bay == bay_id
    )

    if not members:
        raise LedgerProbeError("No symbols in turbine bay")

    plant = getattr(engine, "real_plant_dcs", None)
    bays = getattr(plant, "bays", {}) if plant else {}
    bay = bays.get(bay_id)

    if bay is None:
        raise LedgerProbeError("Native turbine bay unavailable")

    capital = positive(
        getattr(bay, "effective_allocation", None),
        "Bay effective allocation",
    )

    port = getattr(engine, "port", None)
    if port is None or not callable(
        getattr(port, "net_position", None)
    ):
        raise LedgerProbeError("Broker position interface missing")

    method = (
        "_expected_position"
        if variant == "B"
        else "expected_position"
    )

    expected_position = getattr(engine, method, None)

    if not callable(expected_position):
        raise LedgerProbeError("Reconciliation function missing")

    positions = {}
    filled_notional = 0.0

    for symbol in members:
        actual = integer(
            port.net_position(symbol),
            "Broker position",
            signed=True,
        )
        expected = integer(
            expected_position(symbol),
            "Expected position",
            signed=True,
        )

        if actual != expected:
            raise LedgerProbeError(
                f"{symbol}: broker/native position mismatch"
            )

        positions[symbol] = actual

        if actual:
            if symbol not in completed_bar_marks:
                raise LedgerProbeError(
                    f"{symbol}: missing causal market mark"
                )

            mark = positive(
                completed_bar_marks[symbol],
                "Market mark",
            )

            filled_notional += abs(actual) * mark

    orders = getattr(
        engine,
        "_inflight" if variant == "B" else "_working",
        None,
    )

    if not isinstance(orders, Mapping):
        raise LedgerProbeError("Working-order ledger missing")

    # Build acknowledgement evidence from native order events.
    acknowledged = set()

    for event in getattr(engine, "order_audit", ()):
        if not isinstance(event, Mapping):
            raise LedgerProbeError("Malformed order event")

        identifier = event.get(
            "client_order_id" if variant == "B" else "bt_ref"
        )

        state = event.get("event")

        if identifier is None or not isinstance(state, str):
            raise LedgerProbeError("Malformed audit identity")

        if state.upper() == "ACCEPTED":
            acknowledged.add(str(identifier))

    pending_notional = 0.0
    outstanding = set()
    active_entry_symbols = set()

    for order_id, record in orders.items():
        if not isinstance(record, Mapping):
            raise LedgerProbeError("Malformed working order")

        symbol = record.get("symbol")

        if symbol not in members:
            continue

        outstanding.add(symbol)
        kind = record.get("kind")

        if kind not in ("entry", "exit"):
            raise LedgerProbeError("Unknown order kind")

        # Exits are monitored but are never treated as
        # additional entry commitments.
        if kind == "exit" or record.get("booked", False):
            continue

        if symbol in active_entry_symbols:
            raise LedgerProbeError(
                f"{symbol}: duplicate active entry order"
            )

        active_entry_symbols.add(symbol)

        requested = integer(
            record.get("requested"), "Requested quantity"
        )
        already_filled = integer(
            record.get("filled"), "Filled quantity"
        )

        if already_filled > requested:
            raise LedgerProbeError("Order overfilled")

        if str(order_id) not in acknowledged:
            raise LedgerProbeError(
                f"{symbol}: working entry not acknowledged"
            )

        remaining = requested - already_filled

        if remaining:
            intent = record.get("intent")
            plan = (
                intent.get("plan")
                if isinstance(intent, Mapping)
                else None
            )

            reference = positive(
                getattr(plan, "entry_price", None),
                "Planned reference price",
            )

            mark = positive(
                completed_bar_marks.get(symbol),
                "Completed-bar market price",
            )

            # Conservative valuation of outstanding entry.
            pending_notional += remaining * max(
                reference, mark
            )

    symbol_orders = getattr(
        engine,
        "_symbol_inflight"
        if variant == "B"
        else "_symbol_working",
        None,
    )

    if not isinstance(symbol_orders, Mapping):
        raise LedgerProbeError("Working-symbol map missing")

    outstanding.update(
        symbol for symbol in symbol_orders if symbol in members
    )

    if variant == "B":
        parked_exits = getattr(engine, "_working_exits", None)

        if not isinstance(parked_exits, Mapping):
            raise LedgerProbeError("Parked-exit ledger missing")

        outstanding.update(
            symbol for symbol in parked_exits if symbol in members
        )

    return BaySnapshot(
        engine=variant,
        bay_id=bay_id,
        bar_timestamp=bar_timestamp,
        filled_notional=filled_notional,
        acknowledged_pending_entry_notional=pending_notional,
        bay_capital=capital,
        quantity_by_symbol=positions,
        outstanding_symbols=frozenset(outstanding),
    )
