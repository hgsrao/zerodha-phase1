"""Gate16 (Slippage) breach remediation for in-house engine.

Adapted from external engine's Gate16Remediator to work with in-house
PaperBrokerAdapter and portfolio orchestrator interfaces.

Lifecycle:
  Fill detected post-submission
  → Gate16 breach check (measured_slippage > tolerance)
  → Immutable violation record
  → Entry quarantine
  → Cancel pending orders + release reservations
  → Next eligible bar adverse flatten
  → Exact reconciliation
  → Manual recovery approval required
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class SafetyViolation:
    """Immutable record of a Gate16 breach (slippage > tolerance)."""
    violation_id: str
    run_id: str
    timestamp_detected: str
    symbol: str
    order_id: str
    fill_id: str
    intended_entry_price: float
    actual_fill_price: float
    measured_slippage_pct: float
    tolerance_pct: float
    prior_hash: str
    record_hash: str
    signature_status: str = "UNSIGNED"


@dataclass(frozen=True)
class AuditEvent:
    """One immutable link in the remediation audit chain."""
    event_id: str
    run_id: str
    timestamp: str
    event_type: str
    payload: Mapping[str, Any]
    prior_hash: str
    record_hash: str
    signature_status: str = "UNSIGNED"


class Gate16Remediator:
    """
    Manages Gate16 (slippage) breaches for the in-house engine.

    First breach triggers quarantine (freeze entries, cancel pending, flatten).
    Second breach triggers full shutdown.
    """

    def __init__(
        self,
        tolerance_pct: float,
        run_id: Optional[str] = None,
        audit_log_path: Optional[str] = None,
    ):
        self.tolerance_pct = tolerance_pct  # Immutable safety value
        self.run_id = run_id or str(uuid.uuid4())
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None

        self.violations: List[SafetyViolation] = []
        self.audit_events: List[AuditEvent] = []
        self.quarantine_mode = False
        self.trading_halted = False
        self._flattened_symbols: set = set()

    @property
    def entry_authorization_enabled(self) -> bool:
        """Returns True only if not in quarantine or shutdown."""
        return not self.quarantine_mode and not self.trading_halted

    @staticmethod
    def _hash_payload(payload: Mapping[str, Any]) -> str:
        """Compute SHA-256 of payload."""
        json_str = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

    def _append_event(self, timestamp: str, event_type: str, payload: Mapping[str, Any]) -> AuditEvent:
        """Append a tamper-detectable audit event."""
        prior = self.audit_events[-1].record_hash if self.audit_events else "GENESIS"
        material = {
            "run_id": self.run_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "payload": dict(payload),
            "prior_hash": prior,
            "signature_status": "UNSIGNED",
        }
        record = AuditEvent(
            event_id=str(uuid.uuid4()),
            run_id=self.run_id,
            timestamp=timestamp,
            event_type=event_type,
            payload=dict(payload),
            prior_hash=prior,
            record_hash=self._hash_payload(material),
        )
        self.audit_events.append(record)

        # Persist to disk if path provided
        if self.audit_log_path:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(asdict(record), sort_keys=True, default=str) + '\n')

        return record

    def detect_breach(
        self,
        timestamp: str,
        symbol: str,
        order_id: str,
        fill_id: str,
        intended_entry_price: float,
        actual_fill_price: float,
    ) -> bool:
        """
        Check if a fill breaches Gate16 slippage tolerance.

        Returns True if breach detected; records violation and triggers remediation.
        """
        measured_slippage_pct = abs(actual_fill_price - intended_entry_price) / intended_entry_price * 100

        if measured_slippage_pct <= self.tolerance_pct:
            return False  # No breach

        # Breach detected: record and escalate
        breach_event = self._append_event(
            timestamp,
            "GATE16_VIOLATION",
            {
                "symbol": symbol,
                "order_id": order_id,
                "fill_id": fill_id,
                "intended_entry_price": intended_entry_price,
                "actual_fill_price": actual_fill_price,
                "measured_slippage_pct": measured_slippage_pct,
                "tolerance_pct": self.tolerance_pct,
            }
        )

        violation = SafetyViolation(
            violation_id=breach_event.event_id,
            run_id=self.run_id,
            timestamp_detected=timestamp,
            symbol=symbol,
            order_id=order_id,
            fill_id=fill_id,
            intended_entry_price=intended_entry_price,
            actual_fill_price=actual_fill_price,
            measured_slippage_pct=measured_slippage_pct,
            tolerance_pct=self.tolerance_pct,
            prior_hash=breach_event.prior_hash,
            record_hash=breach_event.record_hash,
        )
        self.violations.append(violation)

        # Trigger quarantine
        self.quarantine_mode = True
        self._append_event(
            timestamp,
            "QUARANTINE_ACTIVATED",
            {
                "trigger_violation_id": violation.violation_id,
                "breach_count": len(self.violations),
            }
        )

        # Check for second breach → full shutdown
        if len(self.violations) >= 2:
            self.trading_halted = True
            self._append_event(
                timestamp,
                "TRADING_HALTED",
                {
                    "trigger": "SECOND_GATE16_BREACH",
                    "breach_count": len(self.violations),
                }
            )

        return True

    def record_pending_order_cancellation(
        self,
        timestamp: str,
        order_id: str,
        symbol: str,
        reserved_cash: float,
    ) -> None:
        """Record cancellation of a pending order."""
        self._append_event(
            timestamp,
            "PENDING_ORDER_CANCELLED",
            {
                "order_id": order_id,
                "symbol": symbol,
                "reserved_cash_released": reserved_cash,
                "reason": "QUARANTINE_MODE_GATE16",
            }
        )

    def record_adverse_flatten(
        self,
        timestamp: str,
        symbol: str,
        exit_id: str,
        next_bar_open: float,
        adverse_factor: float,
        flatten_price: float,
        position_quantity: float,
        position_direction: int,
        exit_cost: float,
        net_pnl: float,
    ) -> None:
        """Record a remediation flatten (adverse, next-bar-open based)."""
        self._append_event(
            timestamp,
            "ADVERSE_FLATTEN",
            {
                "symbol": symbol,
                "exit_id": exit_id,
                "next_bar_open": next_bar_open,
                "adverse_factor": adverse_factor,
                "flatten_price": flatten_price,
                "position_quantity": position_quantity,
                "position_direction": position_direction,
                "exit_cost": exit_cost,
                "net_pnl": net_pnl,
            }
        )
        self._flattened_symbols.add(symbol)

    def record_reconciliation(
        self,
        timestamp: str,
        pending_orders: int,
        reserved_cash: float,
        open_positions: int,
        realized_pnl: float,
        daily_pnl: Mapping[str, float],
        exact: bool,
    ) -> None:
        """Record final reconciliation state."""
        if not exact:
            self.trading_halted = True

        self._append_event(
            timestamp,
            "RECONCILIATION_RESULT",
            {
                "exact": exact,
                "pending_orders": pending_orders,
                "reserved_cash": reserved_cash,
                "open_positions": open_positions,
                "realized_pnl": realized_pnl,
                "daily_pnl": dict(daily_pnl),
                "daily_pnl_total": sum(daily_pnl.values()),
            }
        )

    def verify_chain(self) -> bool:
        """Verify in-memory audit chain is unbroken."""
        prior = "GENESIS"
        for event in self.audit_events:
            material = {
                "run_id": event.run_id,
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "payload": dict(event.payload),
                "prior_hash": event.prior_hash,
                "signature_status": event.signature_status,
            }
            if self._hash_payload(material) != event.record_hash:
                return False
            if event.prior_hash != prior:
                return False
            prior = event.record_hash
        return True

    def verify_persisted_chain(self) -> bool:
        """Verify persisted audit log is unbroken."""
        if not self.audit_log_path or not self.audit_log_path.exists():
            return False

        prior = "GENESIS"
        try:
            lines = self.audit_log_path.read_text(encoding='utf-8').splitlines()
            for line in lines:
                data = json.loads(line)
                material = {
                    "run_id": data["run_id"],
                    "timestamp": data["timestamp"],
                    "event_type": data["event_type"],
                    "payload": data["payload"],
                    "prior_hash": data["prior_hash"],
                    "signature_status": data["signature_status"],
                }
                if self._hash_payload(material) != data["record_hash"]:
                    return False
                if data["prior_hash"] != prior:
                    return False
                prior = data["record_hash"]
            return True
        except (OSError, json.JSONDecodeError, KeyError):
            return False
