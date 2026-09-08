"""Fail-closed, auditable replay remediation for a post-fill Gate 16 breach.

This is intentionally a historical-paper replay component. It captures every
completed remediation action in an append-only hash-linked audit chain; it
does not claim live broker persistence or cryptographic signing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from revision4.config_access import calculate_transaction_cost
from revision4.contracts import Bar, ExitEvent, ExitReason, FillEvent, OrderIntent


@dataclass(frozen=True)
class AuditEvent:
    """One immutable link in the Gate 16 remediation audit chain."""

    event_id: str
    run_id: str
    dataset_hash: str
    config_hash: str
    timestamp: str
    event_type: str
    payload: Mapping[str, Any]
    prior_hash: str
    record_hash: str
    signature_status: str = "UNSIGNED"


@dataclass(frozen=True)
class SafetyViolation:
    violation_id: str
    run_id: str
    dataset_hash: str
    config_hash: str
    timestamp_detected: str
    order_id: str
    fill_id: str
    symbol: str
    measured_slippage_pct: float
    tolerance_pct: float
    prior_hash: str
    record_hash: str
    signature_status: str = "UNSIGNED"


class Gate16Remediator:
    """First breach quarantines; a second breach halts the replay."""

    def __init__(self, config, *, dataset_hash: str, config_hash: str,
                 audit_log_path: Optional[str] = None, run_id: Optional[str] = None):
        self.config = config
        self.run_id = run_id or str(uuid.uuid4())
        self.dataset_hash = dataset_hash
        self.config_hash = config_hash
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self.violations: List[SafetyViolation] = []
        self.audit_events: List[AuditEvent] = []
        self.quarantine_mode = False
        self.trading_halted = False
        self._scheduled_symbols: set[str] = set()

    @property
    def entry_authorization_enabled(self) -> bool:
        return not self.quarantine_mode and not self.trading_halted

    @staticmethod
    def _hash_payload(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()

    def _append_event(self, timestamp: str, event_type: str, payload: Mapping[str, Any]) -> AuditEvent:
        """Persist an event only after the corresponding action succeeds."""
        prior = self.audit_events[-1].record_hash if self.audit_events else "GENESIS"
        material = {
            "run_id": self.run_id,
            "dataset_hash": self.dataset_hash,
            "config_hash": self.config_hash,
            "timestamp": timestamp,
            "event_type": event_type,
            "payload": dict(payload),
            "prior_hash": prior,
            "signature_status": "UNSIGNED",
        }
        record = AuditEvent(
            event_id=str(uuid.uuid4()), run_id=self.run_id,
            dataset_hash=self.dataset_hash, config_hash=self.config_hash,
            timestamp=timestamp, event_type=event_type, payload=dict(payload),
            prior_hash=prior, record_hash=self._hash_payload(material),
        )
        self.audit_events.append(record)
        if self.audit_log_path:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record), sort_keys=True, default=str) + "\n")
        return record

    def handle_breach(self, timestamp: str, order: OrderIntent, fill: FillEvent, ledger, broker) -> SafetyViolation:
        tolerance = float(self.config.require("slippage_tolerance_percent"))
        intended = order.proposal.plan.entry_price
        measured = abs(fill.fill_price - intended) / intended * 100 if intended else float("inf")
        breach = self._append_event(timestamp, "GATE16_VIOLATION", {
            "order_id": order.order_id, "fill_id": fill.fill_id, "symbol": fill.symbol,
            "measured_slippage_pct": measured, "tolerance_pct": tolerance,
        })
        violation = SafetyViolation(
            violation_id=breach.event_id, run_id=self.run_id,
            dataset_hash=self.dataset_hash, config_hash=self.config_hash,
            timestamp_detected=timestamp, order_id=order.order_id, fill_id=fill.fill_id,
            symbol=fill.symbol, measured_slippage_pct=measured, tolerance_pct=tolerance,
            prior_hash=breach.prior_hash, record_hash=breach.record_hash,
        )
        self.violations.append(violation)
        self.quarantine_mode = True
        self._append_event(timestamp, "QUARANTINE_ACTIVATED", {
            "trigger_violation_id": violation.violation_id,
            "breach_count": len(self.violations), "entry_authorization_enabled": False,
        })

        pending = sorted(ledger.pending_orders.values(), key=lambda item: (item.timestamp_created, item.order_id))
        for pending_order in pending:
            reservation = pending_order.quantity * pending_order.proposal.plan.entry_price
            broker_ok, broker_reason = broker.cancel_order(pending_order.order_id, "QUARANTINE_MODE_GATE16")
            ledger_ok, ledger_reason = ledger.cancel_order(pending_order.order_id)
            if not broker_ok or not ledger_ok:
                self.trading_halted = True
                self._append_event(timestamp, "CANCELLATION_FAILED", {
                    "order_id": pending_order.order_id, "broker_ok": broker_ok,
                    "broker_reason": broker_reason, "ledger_ok": ledger_ok,
                    "ledger_reason": ledger_reason,
                })
                raise RuntimeError(f"Gate16 remediation cancellation failed for {pending_order.order_id}")
            self._append_event(timestamp, "PENDING_ORDER_CANCELLED", {
                "order_id": pending_order.order_id, "symbol": pending_order.symbol,
                "reservation_released": reservation, "reason": "QUARANTINE_MODE_GATE16",
            })

        self._scheduled_symbols.update(ledger.positions)
        if len(self.violations) >= 2:
            self.trading_halted = True
            self._append_event(timestamp, "TRADING_HALTED", {
                "trigger": "SECOND_GATE16_BREACH", "breach_count": len(self.violations),
            })
        return violation

    def flatten_next_bars(self, bars: Dict[str, Bar], event_index: int, ledger) -> List[ExitEvent]:
        """Close scheduled positions only on a later observed bar, adversely."""
        exits: List[ExitEvent] = []
        symbols = sorted(
            self._scheduled_symbols,
            key=lambda symbol: ledger.positions[symbol].entry_bar_index if symbol in ledger.positions else -1,
        )
        for symbol in symbols:
            position = ledger.positions.get(symbol)
            bar = bars.get(symbol)
            if position is None or bar is None or event_index <= position.entry_bar_index:
                continue
            adverse_factor = 0.999 if position.direction == 1 else 1.001
            price = bar.open * adverse_factor
            side = "SELL" if position.direction == 1 else "BUY"
            exit_cost = calculate_transaction_cost(price, position.quantity, side)
            gross = ((price - position.entry_price) if position.direction == 1 else (position.entry_price - price)) * position.quantity
            net = gross - position.cost_paid - exit_cost
            event = ExitEvent(
                exit_id=f"gate16_flatten_{symbol}_{event_index}", symbol=symbol,
                timestamp_exit=bar.timestamp, bar_index_exit=event_index,
                entry_price=position.entry_price, exit_price=price, quantity=position.quantity,
                direction=position.direction, bars_held=position.bars_held(event_index),
                entry_cost_paid=position.cost_paid, exit_cost_paid=exit_cost,
                exit_reason=ExitReason.QUARANTINE_FLATTEN, pnl_realized=net,
                pnl_pct=(gross / (position.entry_price * position.quantity) * 100) if position.entry_price else 0.0,
            )
            ok, reason = ledger.close_position(event, self.config)
            if not ok:
                self.trading_halted = True
                self._append_event(bar.timestamp, "FLATTEN_FAILED", {
                    "symbol": symbol, "exit_id": event.exit_id, "reason": reason,
                })
                raise RuntimeError(f"Gate16 remediation flatten failed for {symbol}: {reason}")
            self._append_event(bar.timestamp, "ADVERSE_FLATTEN", {
                "exit_id": event.exit_id, "position_fill_id": position.fill_id,
                "symbol": symbol, "direction": position.direction,
                "next_bar_open_reference": bar.open, "adverse_factor": adverse_factor,
                "flatten_price": price, "exit_cost": exit_cost, "net_pnl": net,
            })
            exits.append(event)
        self._scheduled_symbols.difference_update(event.symbol for event in exits)
        return exits

    def record_reconciliation(self, timestamp: str, *, pending_orders: int, reserved_cash: float,
                              open_positions: int, realized_pnl: float, daily_pnl: Mapping[str, float],
                              daily_pnl_matches_realized: bool, exact: bool) -> AuditEvent:
        """Record the sealed final ledger result; a failed result halts recovery."""
        if not exact:
            self.trading_halted = True
        return self._append_event(timestamp, "RECONCILIATION_RESULT", {
            "exact": exact, "pending_orders": pending_orders, "reserved_cash": reserved_cash,
            "open_positions": open_positions, "realized_pnl": realized_pnl,
            "daily_pnl": dict(daily_pnl),
            "daily_pnl_total": sum(daily_pnl.values()),
            "daily_pnl_matches_realized": daily_pnl_matches_realized,
        })

    @classmethod
    def _event_is_valid(cls, event: Mapping[str, Any], prior: str, *, run_id: Optional[str] = None) -> bool:
        required = {"event_id", "run_id", "dataset_hash", "config_hash", "timestamp", "event_type", "payload", "prior_hash", "record_hash", "signature_status"}
        if not required.issubset(event) or event["prior_hash"] != prior:
            return False
        if run_id is not None and event["run_id"] != run_id:
            return False
        material = {key: event[key] for key in (
            "run_id", "dataset_hash", "config_hash", "timestamp", "event_type", "payload", "prior_hash", "signature_status"
        )}
        return cls._hash_payload(material) == event["record_hash"]

    def verify_chain(self) -> bool:
        prior = "GENESIS"
        for event in self.audit_events:
            data = asdict(event)
            if not self._event_is_valid(data, prior, run_id=self.run_id):
                return False
            prior = event.record_hash
        return True

    def verify_persisted_chain(self) -> bool:
        if self.audit_log_path is None or not self.audit_log_path.exists():
            return False
        prior = "GENESIS"
        try:
            lines = self.audit_log_path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return False
            for line in lines:
                data = json.loads(line)
                if not self._event_is_valid(data, prior, run_id=self.run_id):
                    return False
                prior = data["record_hash"]
        except (OSError, ValueError, TypeError):
            return False
        return True

    @classmethod
    def from_persisted_audit(cls, config, audit_log_path: str) -> "Gate16Remediator":
        """Load a verified completed run before appending a human decision."""
        path = Path(audit_log_path)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if not rows:
            raise ValueError("recovery audit is empty")
        first = rows[0]
        remediator = cls(
            config, dataset_hash=first["dataset_hash"], config_hash=first["config_hash"],
            audit_log_path=str(path), run_id=first["run_id"],
        )
        remediator.audit_events = [
            AuditEvent(
                event_id=row["event_id"], run_id=row["run_id"],
                dataset_hash=row["dataset_hash"], config_hash=row["config_hash"],
                timestamp=row["timestamp"], event_type=row["event_type"],
                payload=row["payload"], prior_hash=row["prior_hash"],
                record_hash=row["record_hash"], signature_status=row["signature_status"],
            )
            for row in rows
        ]
        if not remediator.verify_chain() or not remediator.verify_persisted_chain():
            raise ValueError("recovery audit chain is invalid")
        return remediator

    def record_persisted_manual_recovery(self, approved_by: str, rationale: str, timestamp: str) -> AuditEvent:
        """Append the human approval only after a clean terminal reconciliation."""
        if not approved_by or not rationale or not timestamp or self.trading_halted:
            raise ValueError("manual recovery requires approver, rationale, timestamp, and a non-halted run")
        if not self.audit_events or self.audit_events[-1].event_type != "RECONCILIATION_RESULT":
            raise ValueError("manual recovery requires a terminal reconciliation event")
        terminal = self.audit_events[-1].payload
        if not terminal.get("exact") or terminal.get("pending_orders") != 0 or terminal.get("reserved_cash") != 0 or terminal.get("open_positions") != 0:
            raise ValueError("manual recovery requires exact zero-state reconciliation")
        return self._append_event(timestamp, "MANUAL_RECOVERY_APPROVED", {
            "approved_by": approved_by, "rationale": rationale,
            "approval_scope": "ONE_CONTROLLED_SUNPHARMA_REPLAY_ONLY",
            "signature_status": "UNSIGNED",
        })

    def approve_manual_recovery(self, approved_by: str, rationale: str, timestamp: str, ledger) -> bool:
        """Record a named limited approval; never grant it automatically."""
        if (not approved_by or not rationale or not timestamp or self.trading_halted
                or not self.verify_chain() or not self.verify_persisted_chain()
                or ledger.positions or ledger.pending_orders or ledger.reserved_cash):
            return False
        self._append_event(timestamp, "MANUAL_RECOVERY_APPROVED", {
            "approved_by": approved_by, "rationale": rationale,
            "approval_scope": "CONTROLLED_SUNPHARMA_REPLAY_ONLY",
            "signature_status": "UNSIGNED",
        })
        self.quarantine_mode = False
        return True
