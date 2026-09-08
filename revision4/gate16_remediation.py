"""Fail-closed replay remediation for a post-fill Gate 16 breach.

The broker fill is already real when Gate 16 runs.  This controller therefore
freezes new entries, cancels pending orders, and schedules adverse next-bar
exits.  It is intentionally a paper-replay component; live broker retries and
cryptographic signatures remain separate production capabilities.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from revision4.config_access import calculate_transaction_cost
from revision4.contracts import ExitEvent, ExitReason, FillEvent, OrderIntent, Bar


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
    """Append-only, hash-linked Gate 16 remediation state for one replay."""

    def __init__(self, config, *, dataset_hash: str, config_hash: str,
                 audit_log_path: Optional[str] = None, run_id: Optional[str] = None):
        self.config = config
        self.run_id = run_id or str(uuid.uuid4())
        self.dataset_hash = dataset_hash
        self.config_hash = config_hash
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self.violations: List[SafetyViolation] = []
        self.quarantine_mode = False
        self.trading_halted = False
        self._scheduled_symbols: set[str] = set()

    @property
    def entry_authorization_enabled(self) -> bool:
        return not self.quarantine_mode and not self.trading_halted

    def _append(self, timestamp: str, order: OrderIntent, fill: FillEvent, reason: str) -> SafetyViolation:
        tolerance = float(self.config.require("slippage_tolerance_percent"))
        intended = order.proposal.plan.entry_price
        measured = abs(fill.fill_price - intended) / intended * 100 if intended else float("inf")
        prior = self.violations[-1].record_hash if self.violations else "GENESIS"
        payload = {"run_id": self.run_id, "dataset_hash": self.dataset_hash, "config_hash": self.config_hash, "timestamp": timestamp, "order_id": order.order_id,
                   "fill_id": fill.fill_id, "symbol": fill.symbol, "measured": measured,
                   "tolerance": tolerance, "prior": prior, "reason": reason}
        record_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        record = SafetyViolation(str(uuid.uuid4()), self.run_id, self.dataset_hash, self.config_hash, timestamp, order.order_id, fill.fill_id,
                                 fill.symbol, measured, tolerance, prior, record_hash)
        self.violations.append(record)
        if self.audit_log_path:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
        return record

    def handle_breach(self, timestamp: str, order: OrderIntent, fill: FillEvent, ledger, broker) -> SafetyViolation:
        record = self._append(timestamp, order, fill, "Gate16Slippage")
        self.quarantine_mode = True
        # Snapshot before cancellation; only pending orders are cancellable.
        pending = sorted(ledger.pending_orders.values(), key=lambda item: (item.timestamp_created, item.order_id))
        for pending_order in pending:
            broker.cancel_order(pending_order.order_id, "QUARANTINE_MODE_GATE16")
            ledger.cancel_order(pending_order.order_id)
        self._scheduled_symbols.update(ledger.positions)
        if len(self.violations) >= 2:
            self.trading_halted = True
        return record

    def flatten_next_bars(self, bars: Dict[str, Bar], event_index: int, ledger) -> List[ExitEvent]:
        """Close scheduled positions only on a later observed bar, adversely."""
        exits: List[ExitEvent] = []
        for symbol in sorted(self._scheduled_symbols, key=lambda s: ledger.positions[s].entry_bar_index if s in ledger.positions else -1):
            position = ledger.positions.get(symbol)
            bar = bars.get(symbol)
            if position is None or bar is None or event_index <= position.entry_bar_index:
                continue
            price = bar.open * (0.999 if position.direction == 1 else 1.001)
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
                raise RuntimeError(f"Gate16 remediation flatten failed for {symbol}: {reason}")
            exits.append(event)
        self._scheduled_symbols.difference_update(event.symbol for event in exits)
        return exits

    def verify_chain(self) -> bool:
        prior = "GENESIS"
        for record in self.violations:
            if record.prior_hash != prior:
                return False
            prior = record.record_hash
        return True

    def approve_manual_recovery(self, approved_by: str, ledger) -> bool:
        if not approved_by or self.trading_halted or not self.verify_chain():
            return False
        if ledger.positions or ledger.pending_orders or ledger.reserved_cash:
            return False
        self.quarantine_mode = False
        return True
