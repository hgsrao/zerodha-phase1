"""R6 active-paper decision gate.

No broker API. No governor modifications. Native execution must
explicitly honor this gate's result before placing orders.
"""

from math import ceil, floor, isfinite

from r6_avr_core import (
    Action,
    CausalID,
    ExposureAVR,
    ExposureConfig,
    ExposureSample,
    Vote,
    SafetyPermit,
    UnitMaster,
)


class R6GateError(ValueError):
    pass


class ActiveBayGate:

    def __init__(self, bay_id, max_utilization, target_utilization):
        self.bay = bay_id
        self.avr = ExposureAVR(
            ExposureConfig(
                bay_id,
                max_utilization,
                target_utilization,
            )
        )
        self.current = None
        self.allowed = 0.0
        self.reserved = {}
        self.seen = set()

    def begin_bar(self, snapshot, bar_index):
        if self.reserved:
            raise R6GateError(
                "UNSETTLED_NATIVE_ORDER_RESERVATIONS"
            )

        if snapshot.bay_id != self.bay or not isinstance(
            bar_index, int
        ):
            raise R6GateError("BAY_IDENTITY_MISMATCH")

        ident = CausalID(
            bar_index,
            snapshot.bar_timestamp,
            self.bay,
            "__BAY_FEEDBACK__",
        )

        vote = self.avr.step(
            ExposureSample(
                ident,
                snapshot.bay_capital,
                snapshot.filled_notional,
                snapshot.acknowledged_pending_entry_notional,
                0,
                1.0,
            )
        )

        if "allowed_util" not in vote.telemetry:
            raise R6GateError("INVALID_EXPOSURE_FEEDBACK")

        self.allowed = (
            vote.telemetry["allowed_util"]
            * snapshot.bay_capital
        )

        self.current = (snapshot, bar_index)
        self.seen.clear()

    def _context(self, identity):
        if self.current is None:
            raise R6GateError("NO_BAY_FEEDBACK")

        snapshot, bar = self.current

        if (
            identity.bay_id != self.bay
            or identity.bar_index != bar
            or identity.timestamp != snapshot.bar_timestamp
            or identity.symbol not in snapshot.quantity_by_symbol
        ):
            raise R6GateError("CAUSAL_IDENTITY_MISMATCH")

        return snapshot

    def entry(
        self,
        *,
        identity,
        native_governor,
        final_native_quantity,
        fsr_already_applied,
        safety,
        price,
        minimum_order_notional,
        legacy_plant_admitted,
    ):
        snap = self._context(identity)

        if (
            identity.position_id != "FLAT"
            or snap.quantity_by_symbol[identity.symbol] != 0
        ):
            raise R6GateError("ENTRY_TO_HELD_SYMBOL")

        if identity.symbol in self.seen:
            raise R6GateError("DUPLICATE_SYMBOL_CANDIDATE")

        if not fsr_already_applied:
            raise R6GateError("UNVERIFIED_FSR_PROVENANCE")

        if (
            type(final_native_quantity) is not int
            or final_native_quantity < 0
            or not isinstance(safety, SafetyPermit)
            or type(legacy_plant_admitted) is not bool
        ):
            raise R6GateError("INVALID_NATIVE_AUTHORITIES")

        if not (
            isinstance(price, (int, float))
            and not isinstance(price, bool)
            and isfinite(price)
            and price > 0
        ):
            raise R6GateError("INVALID_MARK")

        if not (
            isinstance(minimum_order_notional, (int, float))
            and not isinstance(minimum_order_notional, bool)
            and isfinite(minimum_order_notional)
            and minimum_order_notional >= 0
        ):
            raise R6GateError("INVALID_COMMERCIAL_MINIMUM")

        mode = native_governor.get("action")
        fsr = native_governor.get("size_multiplier")

        if (
            mode not in ("ENTRY", "NO_ACTION")
            or not isinstance(native_governor.get("reason"), str)
            or not isinstance(fsr, (float, int))
            or isinstance(fsr, bool)
            or not isfinite(fsr)
            or not 0 <= fsr <= 1
        ):
            raise R6GateError(
                "NOT_FULL_GOVERNOR_AUTHORITY"
            )

        # One shared bay capacity for all candidate symbols.
        reserve = sum(self.reserved.values())

        available = max(
            0.0,
            self.allowed
            - snap.filled_notional
            - snap.acknowledged_pending_entry_notional
            - reserve,
        )

        avr_cap = floor(available / price)

        # Governor quantity is ALREADY FSR derated.
        # Never multiply by FSR again.
        gov_vote = Vote(
            identity,
            "GOVERNOR",
            Action.ENTRY if mode == "ENTRY" else Action.BLOCK,
            final_native_quantity if mode == "ENTRY" else 0,
            native_governor["reason"],
        )

        avr_vote = Vote(
            identity,
            "AVR",
            Action.ENTRY if avr_cap else Action.BLOCK,
            avr_cap,
            "RECONCILED_BAY_HEADROOM",
        )

        # Existing protection and plant admission still prevail.
        native_safe = SafetyPermit(
            safety.entry_allowed and legacy_plant_admitted,
            safety.max_total_quantity
            if legacy_plant_admitted else 0,
            safety.mandatory_trip_reason,
        )

        intent = UnitMaster.decide(
            governor=gov_vote,
            avr=avr_vote,
            safety=native_safe,
            held_quantity=0,
            requested_entry_quantity=final_native_quantity,
            market_price=price,
            minimum_order_notional=minimum_order_notional,
            outstanding_order=(
                identity.symbol in snap.outstanding_symbols
            ),
        )

        self.seen.add(identity.symbol)

        # Reserve immediately to prevent simultaneous symbols
        # exceeding their shared turbine-bay allowance.
        if intent.action == Action.ENTRY:
            self.reserved[intent.event_id] = (
                intent.order_quantity * price
            )

        return intent

    def position(
        self,
        *,
        identity,
        native_position,
        safety,
        price,
    ):
        snap = self._context(identity)
        qty = abs(snap.quantity_by_symbol[identity.symbol])

        if qty == 0 or not isinstance(
            safety, SafetyPermit
        ):
            raise R6GateError("INVALID_HELD_POSITION")

        action = native_position.get("action")

        if action not in ("HOLD", "EXIT"):
            action = "EXIT"

        gov = Vote(
            identity,
            "GOVERNOR",
            Action.EXIT if action == "EXIT" else Action.HOLD,
            0 if action == "EXIT" else qty,
            str(
                native_position.get("reason")
                or "UNKNOWN_GOVERNOR_EXIT"
            ),
        )

        if (
            not isinstance(price, (int, float))
            or not isfinite(price)
            or price <= 0
        ):
            raise R6GateError("INVALID_MARK")

        excess = max(
            0.0,
            snap.filled_notional - self.allowed,
        )

        to_close = min(qty, ceil(excess / price))

        avr = Vote(
            identity,
            "AVR",
            Action.REDUCE if to_close else Action.HOLD,
            qty - to_close,
            "BAY_CAPACITY_FEEDBACK",
        )

        return UnitMaster.decide(
            governor=gov,
            avr=avr,
            safety=safety,
            held_quantity=qty,
            requested_entry_quantity=0,
            market_price=price,
            minimum_order_notional=0,
            outstanding_order=(
                identity.symbol in snap.outstanding_symbols
            ),
        )

    def settle(
        self,
        event_id,
        *,
        native_ack_or_terminal,
    ):
        if (
            not native_ack_or_terminal
            or event_id not in self.reserved
        ):
            raise R6GateError(
                "NATIVE_ACK_OR_TERMINAL_EVIDENCE_REQUIRED"
            )

        del self.reserved[event_id]
