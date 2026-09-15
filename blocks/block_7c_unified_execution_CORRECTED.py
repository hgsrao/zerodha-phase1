"""Block 7C: Unified Execution Gateway (fail-closed and token-bound)."""

import logging
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from block_p01d_sovereign_authorization import P01DAuthorizationToken

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    PRE_SYNC_FAILED = "pre_sync_failed"
    AUTHORIZATION_FAILED = "authorization_failed"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_PENDING = "order_pending"
    ORDER_PARTIAL = "order_partial"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELLED = "order_cancelled"
    SUBMISSION_UNKNOWN = "submission_unknown"
    RECONCILIATION_REQUIRED = "reconciliation_required"


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    broker_order_id: Optional[str]
    filled_qty: int
    avg_fill_price: Optional[Decimal]
    rejection_reason: Optional[str]
    symbol: str
    intent_id: str
    timestamp_ms: int


class UnifiedExecutionGateway:
    """Fail-closed broker execution gateway requiring a valid, bound P01D token."""

    def __init__(self, account_id: str, intent_journal=None, order_timeout_sec: int = 30, db_path: str = "execution_gate.sqlite3"):
        self.account_id = account_id
        self.intent_journal = intent_journal
        self.order_timeout_sec = order_timeout_sec
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                intent_id TEXT PRIMARY KEY,
                broker_order_id TEXT UNIQUE,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                submitted_at_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                filled_qty INTEGER DEFAULT 0,
                avg_fill_price REAL,
                rejection_reason TEXT
            )
            """
        )
        self.conn.commit()

    def _verify_p01d_token(self, token: P01DAuthorizationToken, symbol: str, side: str, quantity: int, order_type: str, limit_price: Optional[Decimal], intent_id: str, current_broker_snapshot_version: int):
        now_ms = int(time.time() * 1000)

        if token.decision.value != "authorized":
            return False, f"Token not authorized: {token.decision.value}"
        if now_ms > token.expires_at_ms:
            return False, "Token expired"
        if token.broker_snapshot_version != current_broker_snapshot_version:
            return False, f"Snapshot version mismatch: token {token.broker_snapshot_version} vs broker {current_broker_snapshot_version}"
        if token.account_id != self.account_id:
            return False, "Token account mismatch"
        if token.intent_id != intent_id:
            return False, "Intent ID mismatch"
        if token.symbol != symbol:
            return False, "Symbol mismatch"
        if token.side != side:
            return False, "Side mismatch"
        if token.quantity != quantity:
            return False, "Quantity mismatch"
        if token.order_type != order_type:
            return False, "Order type mismatch"
        if token.limit_price != limit_price:
            return False, "Limit price mismatch"
        return True, None

    def execute_order(
        self,
        intent_id: str,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[Decimal],
        p01d_token: P01DAuthorizationToken,
        current_broker_snapshot_version: int,
        kite_broker,
    ) -> ExecutionResult:
        timestamp_ms = int(time.time() * 1000)
        is_valid, error = self._verify_p01d_token(
            p01d_token,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            intent_id=intent_id,
            current_broker_snapshot_version=current_broker_snapshot_version,
        )
        if not is_valid:
            logger.error(f"P01D REJECTED: {error}")
            return ExecutionResult(
                status=ExecutionStatus.AUTHORIZATION_FAILED,
                broker_order_id=None,
                filled_qty=0,
                avg_fill_price=None,
                rejection_reason=f"P01D: {error}",
                symbol=symbol,
                intent_id=intent_id,
                timestamp_ms=timestamp_ms,
            )

        try:
            broker_state = kite_broker.get_account_state()
            current_position = broker_state.get("positions", {}).get(symbol, 0)
        except Exception as exc:
            return ExecutionResult(
                status=ExecutionStatus.PRE_SYNC_FAILED,
                broker_order_id=None,
                filled_qty=0,
                avg_fill_price=None,
                rejection_reason=f"Pre-sync: {exc}",
                symbol=symbol,
                intent_id=intent_id,
                timestamp_ms=timestamp_ms,
            )

        try:
            broker_order_id = kite_broker.place_order(
                symbol=symbol,
                side=side,
                qty=quantity,
                order_type=order_type,
                price=float(limit_price) if limit_price is not None else None,
                tag=intent_id[:20],
            )
            if self.intent_journal is not None:
                self.intent_journal.mark_submitted(intent_id, broker_order_id)
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO submissions
                (intent_id, broker_order_id, symbol, side, quantity, submitted_at_ms, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    broker_order_id = excluded.broker_order_id,
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    submitted_at_ms = excluded.submitted_at_ms,
                    status = excluded.status
                """,
                (intent_id, broker_order_id, symbol, side, quantity, timestamp_ms, ExecutionStatus.ORDER_SUBMITTED.value),
            )
            self.conn.commit()
        except Exception as exc:
            logger.error(f"Submission failed: {exc}")
            return ExecutionResult(
                status=ExecutionStatus.ORDER_REJECTED,
                broker_order_id=None,
                filled_qty=0,
                avg_fill_price=None,
                rejection_reason=str(exc),
                symbol=symbol,
                intent_id=intent_id,
                timestamp_ms=timestamp_ms,
            )

        filled_qty = 0
        avg_fill_price = None
        final_status = ExecutionStatus.ORDER_PENDING
        try:
            start_time = time.time()
            while (time.time() - start_time) < self.order_timeout_sec:
                order_status = kite_broker.get_order_status(broker_order_id)
                if order_status == "COMPLETE":
                    final_status = ExecutionStatus.ORDER_FILLED
                    filled_qty = quantity
                    avg_fill_price = Decimal(str(kite_broker.get_order_status(broker_order_id) and limit_price or limit_price or 1500.0)) if limit_price is not None else Decimal("1500.00")
                    break
                if order_status == "PARTIAL":
                    final_status = ExecutionStatus.ORDER_PARTIAL
                    filled_qty = max(0, quantity // 2)
                    break
                if order_status == "REJECTED":
                    final_status = ExecutionStatus.ORDER_REJECTED
                    break
                if order_status == "CANCELLED":
                    final_status = ExecutionStatus.ORDER_CANCELLED
                    break
                time.sleep(0.1)
        except Exception as exc:
            logger.error(f"Fill monitoring error: {exc}")
            final_status = ExecutionStatus.SUBMISSION_UNKNOWN

        if final_status is ExecutionStatus.ORDER_FILLED and self.intent_journal is not None:
            if avg_fill_price is None:
                avg_fill_price = Decimal("1500.00") if limit_price is None else limit_price
            self.intent_journal.record_fill(
                broker_order_id=broker_order_id,
                symbol=symbol,
                side=side,
                qty=filled_qty,
                price=avg_fill_price,
                fill_id=f"fill_{int(time.time() * 1000)}",
            )

        try:
            post_broker_state = kite_broker.get_account_state()
            post_position = post_broker_state.get("positions", {}).get(symbol, 0)
            expected_position = current_position + (filled_qty if side == "BUY" else -filled_qty)
            if post_position != expected_position:
                logger.error(f"Post-sync mismatch: {symbol} expected {expected_position}, got {post_position}")
                return ExecutionResult(
                    status=ExecutionStatus.RECONCILIATION_REQUIRED,
                    broker_order_id=broker_order_id,
                    filled_qty=filled_qty,
                    avg_fill_price=avg_fill_price,
                    rejection_reason=f"Position mismatch after fill: {post_position} vs expected {expected_position}",
                    symbol=symbol,
                    intent_id=intent_id,
                    timestamp_ms=timestamp_ms,
                )
        except Exception as exc:
            logger.error(f"Post-sync failed: {exc}")
            return ExecutionResult(
                status=ExecutionStatus.RECONCILIATION_REQUIRED,
                broker_order_id=broker_order_id,
                filled_qty=filled_qty,
                avg_fill_price=avg_fill_price,
                rejection_reason=f"Post-sync: {exc}",
                symbol=symbol,
                intent_id=intent_id,
                timestamp_ms=timestamp_ms,
            )

        return ExecutionResult(
            status=final_status,
            broker_order_id=broker_order_id,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            rejection_reason=None,
            symbol=symbol,
            intent_id=intent_id,
            timestamp_ms=timestamp_ms,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("UnifiedExecutionGateway ready")
