"""
Block P01D: Sovereign Authorization Gate
The mandatory gatekeeper between exposure governor and broker execution.

Every order requires a valid, single-use, expiring authorization token.
This is the PRIMARY safety mechanism preventing uncontrolled mutations.
"""

import hashlib
import hmac
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class P01DDecision(Enum):
    """P01D authorization decision."""

    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class BrokerSnapshot:
    """Authoritative broker state snapshot at authorization time."""

    version: int
    timestamp_ms: int
    account_id: str
    current_equity: Decimal
    available_margin: Decimal
    open_positions: dict
    daily_pnl: Decimal
    max_daily_loss: Decimal


@dataclass(frozen=True)
class P01DAuthorizationRequest:
    """Request for sovereign trade authorization."""

    intent_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[Decimal]
    projected_position: int
    projected_notional: Decimal
    risk_capacity: float
    is_risk_reduction: bool


@dataclass(frozen=True)
class P01DAuthorizationToken:
    """Single-use, expiring, signed authorization token."""

    token_id: str
    intent_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[Decimal]
    projected_notional: Decimal
    risk_capacity: float
    is_risk_reduction: bool
    account_id: str
    broker_snapshot_version: int
    authorized_at_ms: int
    expires_at_ms: int
    decision: P01DDecision
    rejection_reason: Optional[str]
    nonce: str
    signature: str

    def _replace(self, **kwargs):
        return replace(self, **kwargs)

    def __replace__(self, **kwargs):
        return replace(self, **kwargs)


class P01DSovereignAuthorizationGate:
    """Fail-closed sovereign authorization gate."""

    def __init__(
        self,
        account_id: str,
        db_path: str = ":memory:",
        token_expiry_sec: int = 30,
        max_order_notional: Decimal = Decimal("500000"),
        require_durable_db: bool = False,
    ):
        self.account_id = account_id
        self.db_path = db_path
        self.token_expiry_sec = token_expiry_sec
        self.max_order_notional = max_order_notional
        self.require_durable_db = require_durable_db

        if require_durable_db and db_path == ":memory:":
            raise RuntimeError(
                "P01D requires durable database in production. Use a real file path, not ':memory:'"
            )

        self.hmac_secret = os.environ.get("P01D_SIGNING_KEY", "dev-secret").encode()
        self._init_db()

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS p01d_authorizations (
                token_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                projected_notional REAL NOT NULL,
                risk_capacity REAL NOT NULL,
                is_risk_reduction INTEGER NOT NULL,
                order_type TEXT NOT NULL,
                limit_price REAL,
                account_id TEXT NOT NULL,
                broker_snapshot_version INTEGER NOT NULL,
                authorized_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                decision TEXT NOT NULL,
                rejection_reason TEXT,
                used_at_ms INTEGER,
                UNIQUE(intent_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_snapshots (
                version INTEGER PRIMARY KEY,
                timestamp_ms INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                current_equity REAL NOT NULL,
                available_margin REAL NOT NULL,
                daily_pnl REAL NOT NULL,
                snapshot_json TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def _canonical_payload(self, request: P01DAuthorizationRequest, snapshot: BrokerSnapshot, nonce: str) -> str:
        limit_value = "" if request.limit_price is None else str(request.limit_price)
        return (
            f"{request.intent_id}:{request.symbol}:{request.side}:{request.quantity}:"
            f"{request.order_type}:{limit_value}:{request.projected_position}:"
            f"{request.projected_notional}:{request.risk_capacity}:{request.is_risk_reduction}:"
            f"{snapshot.account_id}:{snapshot.version}:{snapshot.timestamp_ms}:{nonce}"
        )

    def authorize(
        self,
        request: P01DAuthorizationRequest,
        broker_snapshot: BrokerSnapshot,
    ) -> P01DAuthorizationToken:
        token_id = f"P01D_{int(time.time() * 1000)}_{os.urandom(8).hex()[:16]}"
        now_ms = int(time.time() * 1000)
        nonce = os.urandom(16).hex()
        signing_snapshot = BrokerSnapshot(
            version=broker_snapshot.version,
            timestamp_ms=now_ms,
            account_id=broker_snapshot.account_id,
            current_equity=broker_snapshot.current_equity,
            available_margin=broker_snapshot.available_margin,
            open_positions=broker_snapshot.open_positions,
            daily_pnl=broker_snapshot.daily_pnl,
            max_daily_loss=broker_snapshot.max_daily_loss,
        )

        if request.quantity <= 0:
            token = P01DAuthorizationToken(
                token_id=token_id,
                intent_id=request.intent_id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                projected_notional=request.projected_notional,
                risk_capacity=request.risk_capacity,
                is_risk_reduction=request.is_risk_reduction,
                account_id=broker_snapshot.account_id,
                broker_snapshot_version=broker_snapshot.version,
                authorized_at_ms=now_ms,
                expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                decision=P01DDecision.REJECTED,
                rejection_reason="Quantity must be positive",
                nonce=nonce,
                signature="",
            )
            self._log_authorization(token, broker_snapshot)
            return token

        margin_required = request.projected_notional * Decimal("0.20")
        if margin_required > broker_snapshot.available_margin:
            token = P01DAuthorizationToken(
                token_id=token_id,
                intent_id=request.intent_id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                projected_notional=request.projected_notional,
                risk_capacity=request.risk_capacity,
                is_risk_reduction=request.is_risk_reduction,
                account_id=broker_snapshot.account_id,
                broker_snapshot_version=broker_snapshot.version,
                authorized_at_ms=now_ms,
                expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                decision=P01DDecision.REJECTED,
                rejection_reason=f"Insufficient margin: need {margin_required}, have {broker_snapshot.available_margin}",
                nonce=nonce,
                signature="",
            )
            self._log_authorization(token, broker_snapshot)
            return token

        if not request.is_risk_reduction:
            remaining_daily_risk = abs(broker_snapshot.max_daily_loss) - abs(broker_snapshot.daily_pnl)
            if remaining_daily_risk <= 0:
                token = P01DAuthorizationToken(
                    token_id=token_id,
                    intent_id=request.intent_id,
                    symbol=request.symbol,
                    side=request.side,
                    quantity=request.quantity,
                    order_type=request.order_type,
                    limit_price=request.limit_price,
                    projected_notional=request.projected_notional,
                    risk_capacity=request.risk_capacity,
                    is_risk_reduction=request.is_risk_reduction,
                    account_id=broker_snapshot.account_id,
                    broker_snapshot_version=broker_snapshot.version,
                    authorized_at_ms=now_ms,
                    expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                    decision=P01DDecision.REJECTED,
                    rejection_reason=f"Daily risk budget exhausted: {broker_snapshot.daily_pnl} vs {broker_snapshot.max_daily_loss}",
                    nonce=nonce,
                    signature="",
                )
                self._log_authorization(token, broker_snapshot)
                return token

        if request.projected_notional > self.max_order_notional:
            token = P01DAuthorizationToken(
                token_id=token_id,
                intent_id=request.intent_id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                projected_notional=request.projected_notional,
                risk_capacity=request.risk_capacity,
                is_risk_reduction=request.is_risk_reduction,
                account_id=broker_snapshot.account_id,
                broker_snapshot_version=broker_snapshot.version,
                authorized_at_ms=now_ms,
                expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                decision=P01DDecision.REJECTED,
                rejection_reason=f"Order notional {request.projected_notional} exceeds limit {self.max_order_notional}",
                nonce=nonce,
                signature="",
            )
            self._log_authorization(token, broker_snapshot)
            return token

        signature = self._sign_from_token_fields(
            intent_id=request.intent_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            limit_price=request.limit_price,
            projected_notional=request.projected_notional,
            risk_capacity=request.risk_capacity,
            is_risk_reduction=request.is_risk_reduction,
            account_id=broker_snapshot.account_id,
            broker_snapshot_version=broker_snapshot.version,
            authorized_at_ms=now_ms,
            nonce=nonce,
        )
        token = P01DAuthorizationToken(
            token_id=token_id,
            intent_id=request.intent_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            limit_price=request.limit_price,
            projected_notional=request.projected_notional,
            risk_capacity=request.risk_capacity,
            is_risk_reduction=request.is_risk_reduction,
            account_id=broker_snapshot.account_id,
            broker_snapshot_version=broker_snapshot.version,
            authorized_at_ms=now_ms,
            expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
            decision=P01DDecision.AUTHORIZED,
            rejection_reason=None,
            nonce=nonce,
            signature=signature,
        )

        self._log_authorization(token, broker_snapshot)
        logger.info(
            f"✓ P01D AUTHORIZED: {request.symbol} {request.side} {request.quantity} "
            f"notional={request.projected_notional} token={token_id[:20]}... expires in {self.token_expiry_sec}s"
        )
        return token

    def verify_token_before_submission(
        self,
        token: P01DAuthorizationToken,
        current_broker_snapshot_version: int,
    ) -> Tuple[bool, Optional[str]]:
        now_ms = int(time.time() * 1000)

        if token.decision != P01DDecision.AUTHORIZED:
            return False, f"Token not authorized: {token.decision.value}"
        if now_ms > token.expires_at_ms:
            return False, "Token expired"
        if token.broker_snapshot_version != current_broker_snapshot_version:
            return False, f"Snapshot version mismatch: token {token.broker_snapshot_version} vs broker {current_broker_snapshot_version}"

        expected_sig = self._sign_authorization_for_token(token)
        if token.signature != expected_sig:
            return False, "Token signature invalid (tampering detected)"

        cursor = self.conn.cursor()
        cursor.execute("SELECT used_at_ms FROM p01d_authorizations WHERE token_id = ?", (token.token_id,))
        row = cursor.fetchone()
        if row and row["used_at_ms"] is not None:
            return False, f"Token already used at {row['used_at_ms']}"

        cursor.execute("UPDATE p01d_authorizations SET used_at_ms = ? WHERE token_id = ?", (now_ms, token.token_id))
        self.conn.commit()
        logger.info(f"✓ P01D token verified and consumed: {token.token_id[:20]}...")
        return True, None

    def _sign_from_token_fields(
        self,
        *,
        intent_id: str,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[Decimal],
        projected_notional: Decimal,
        risk_capacity: float,
        is_risk_reduction: bool,
        account_id: str,
        broker_snapshot_version: int,
        authorized_at_ms: int,
        nonce: str,
    ) -> str:
        if not self.hmac_secret:
            return ""
        limit_value = "" if limit_price is None else str(limit_price)
        data = (
            f"{intent_id}:{symbol}:{side}:{quantity}:{order_type}:{limit_value}:"
            f"{projected_notional}:{risk_capacity}:{is_risk_reduction}:{account_id}:"
            f"{broker_snapshot_version}:{authorized_at_ms}:{nonce}"
        )
        return hmac.new(self.hmac_secret, data.encode(), hashlib.sha256).hexdigest()

    def _sign_authorization(
        self,
        request: P01DAuthorizationRequest,
        snapshot: BrokerSnapshot,
        nonce: str,
    ) -> str:
        return self._sign_from_token_fields(
            intent_id=request.intent_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            limit_price=request.limit_price,
            projected_notional=request.projected_notional,
            risk_capacity=request.risk_capacity,
            is_risk_reduction=request.is_risk_reduction,
            account_id=snapshot.account_id,
            broker_snapshot_version=snapshot.version,
            authorized_at_ms=snapshot.timestamp_ms,
            nonce=nonce,
        )

    def _sign_authorization_for_token(self, token: P01DAuthorizationToken) -> str:
        return self._sign_from_token_fields(
            intent_id=token.intent_id,
            symbol=token.symbol,
            side=token.side,
            quantity=token.quantity,
            order_type=token.order_type,
            limit_price=token.limit_price,
            projected_notional=token.projected_notional,
            risk_capacity=token.risk_capacity,
            is_risk_reduction=token.is_risk_reduction,
            account_id=token.account_id,
            broker_snapshot_version=token.broker_snapshot_version,
            authorized_at_ms=token.authorized_at_ms,
            nonce=token.nonce,
        )

    def _log_authorization(self, token: P01DAuthorizationToken, snapshot: BrokerSnapshot):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO p01d_authorizations
            (token_id, intent_id, symbol, side, quantity, projected_notional, risk_capacity,
             is_risk_reduction, order_type, limit_price, account_id, broker_snapshot_version,
             authorized_at_ms, expires_at_ms, decision, rejection_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token.token_id,
                token.intent_id,
                token.symbol,
                token.side,
                token.quantity,
                float(token.projected_notional),
                token.risk_capacity,
                int(token.is_risk_reduction),
                token.order_type,
                None if token.limit_price is None else float(token.limit_price),
                token.account_id,
                token.broker_snapshot_version,
                token.authorized_at_ms,
                token.expires_at_ms,
                token.decision.value,
                token.rejection_reason,
            ),
        )
        self.conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    os.environ.setdefault("P01D_SIGNING_KEY", "dev-secret")
    gate = P01DSovereignAuthorizationGate(account_id="ACC123", db_path="p01d_test.sqlite3")
    snapshot = BrokerSnapshot(
        version=1,
        timestamp_ms=int(time.time() * 1000),
        account_id="ACC123",
        current_equity=Decimal("1000000"),
        available_margin=Decimal("500000"),
        open_positions={"INFY": 0},
        daily_pnl=Decimal("0"),
        max_daily_loss=Decimal("-50000"),
    )
    request = P01DAuthorizationRequest(
        intent_id="ENTRY_INFY_12345",
        symbol="INFY",
        side="BUY",
        quantity=10,
        order_type="LIMIT",
        limit_price=Decimal("1500.00"),
        projected_position=10,
        projected_notional=Decimal("15000"),
        risk_capacity=0.80,
        is_risk_reduction=False,
    )
    token = gate.authorize(request, snapshot)
    print(f"Decision: {token.decision.value}")
    print(f"Valid: {gate.verify_token_before_submission(token, snapshot.version)[0]}")
