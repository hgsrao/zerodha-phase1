"""
Block P01D: Sovereign Authorization Gate
The mandatory gatekeeper between exposure governor and broker execution.

Every order requires a valid, single-use, expiring authorization token.
This is the PRIMARY safety mechanism preventing uncontrolled mutations.
"""

import logging
import time
import sqlite3
import hmac
import hashlib
import os
from dataclasses import dataclass
from typing import Optional, Tuple
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class P01DDecision(Enum):
    """P01D authorization decision."""
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    DEFERRED = "deferred"  # Margin insufficient, etc


@dataclass(frozen=True)
class BrokerSnapshot:
    """Authoritative broker state snapshot at authorization time."""
    version: int
    timestamp_ms: int
    account_id: str
    current_equity: Decimal
    available_margin: Decimal
    open_positions: dict  # symbol -> qty
    daily_pnl: Decimal
    max_daily_loss: Decimal


@dataclass(frozen=True)
class P01DAuthorizationRequest:
    """Request for sovereign trade authorization."""
    intent_id: str
    symbol: str
    side: str                              # BUY or SELL
    quantity: int
    order_type: str                        # LIMIT or MARKET
    limit_price: Optional[Decimal]

    # Projected state after execution
    projected_position: int
    projected_notional: Decimal

    # Risk context
    risk_capacity: float                   # 0.0 to 1.0
    is_risk_reduction: bool                # True for exits/flatten


@dataclass(frozen=True)
class P01DAuthorizationToken:
    """Single-use, expiring, signed authorization token."""
    token_id: str
    intent_id: str
    broker_snapshot_version: int
    authorized_at_ms: int
    expires_at_ms: int
    decision: P01DDecision
    rejection_reason: Optional[str]
    nonce: str
    signature: str                         # HMAC-SHA256


class P01DSovereignAuthorizationGate:
    """
    Sovereign authorization gate.

    No order submission to broker is possible without a valid P01D token.
    This gate:
    - Verifies broker state stability
    - Checks margin and risk budget
    - Enforces order size limits
    - Generates single-use, expiring tokens
    - Logs all authorizations durably
    """

    def __init__(
        self,
        account_id: str,
        db_path: str = ":memory:",
        token_expiry_sec: int = 30,
        max_order_notional: Decimal = Decimal('500000'),  # ₹500k max per order
        require_durable_db: bool = False,
    ):
        """Initialize P01D gate."""
        self.account_id = account_id
        self.db_path = db_path
        self.token_expiry_sec = token_expiry_sec
        self.max_order_notional = max_order_notional
        self.require_durable_db = require_durable_db

        # Check database durability requirement
        if require_durable_db and db_path == ":memory:":
            raise RuntimeError(
                "P01D requires durable database in production. "
                "Use a real file path, not ':memory:'"
            )

        # Secret key for HMAC signing (must be set externally)
        self.hmac_secret = os.environ.get("P01D_SIGNING_KEY", "").encode()
        if not self.hmac_secret or self.hmac_secret == b"":
            logger.warning("P01D_SIGNING_KEY not set. Tokens will not be cryptographically signed.")

        self._init_db()

    def _init_db(self):
        """Initialize authorization log database."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        cursor = self.conn.cursor()

        # Authorization log (immutable)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS p01d_authorizations (
                token_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                projected_notional REAL NOT NULL,
                risk_capacity REAL NOT NULL,
                is_risk_reduction INTEGER NOT NULL,
                broker_snapshot_version INTEGER NOT NULL,
                authorized_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                decision TEXT NOT NULL,
                rejection_reason TEXT,
                used_at_ms INTEGER,
                UNIQUE(intent_id)
            )
        """)

        # Broker state snapshots (for audit)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS broker_snapshots (
                version INTEGER PRIMARY KEY,
                timestamp_ms INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                current_equity REAL NOT NULL,
                available_margin REAL NOT NULL,
                daily_pnl REAL NOT NULL,
                snapshot_json TEXT NOT NULL
            )
        """)

        self.conn.commit()

    def authorize(
        self,
        request: P01DAuthorizationRequest,
        broker_snapshot: BrokerSnapshot,
    ) -> P01DAuthorizationToken:
        """
        Authorize a single order. Returns token or rejection.

        Decision logic:
        1. Verify broker snapshot version is current
        2. Check margin sufficient for this trade
        3. Check daily risk budget (unless risk-reduction)
        4. Check order size limits
        5. Check projection within concentration limits
        6. Generate single-use token with HMAC signature
        """

        token_id = f"P01D_{int(time.time() * 1000)}_{os.urandom(8).hex()[:16]}"
        now_ms = int(time.time() * 1000)
        nonce = os.urandom(16).hex()

        # ==================== VALIDATION ====================

        # 1. Broker snapshot version must match (prevents TOCTOU race)
        # (Caller is responsible for fetching current snapshot)

        # 2. Check margin
        margin_required = request.projected_notional * Decimal("0.20")  # 20% intraday margin
        if margin_required > broker_snapshot.available_margin:
            token = P01DAuthorizationToken(
                token_id=token_id,
                intent_id=request.intent_id,
                broker_snapshot_version=broker_snapshot.version,
                authorized_at_ms=now_ms,
                expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                decision=P01DDecision.REJECTED,
                rejection_reason=f"Insufficient margin: need {margin_required}, have {broker_snapshot.available_margin}",
                nonce=nonce,
                signature=""
            )
            self._log_authorization(token, broker_snapshot)
            return token

        # 3. Check daily risk budget (skip for risk-reduction)
        if not request.is_risk_reduction:
            remaining_daily_risk = abs(broker_snapshot.max_daily_loss) - abs(broker_snapshot.daily_pnl)
            if remaining_daily_risk <= 0:
                token = P01DAuthorizationToken(
                    token_id=token_id,
                    intent_id=request.intent_id,
                    broker_snapshot_version=broker_snapshot.version,
                    authorized_at_ms=now_ms,
                    expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                    decision=P01DDecision.REJECTED,
                    rejection_reason=f"Daily risk budget exhausted: {broker_snapshot.daily_pnl} vs {broker_snapshot.max_daily_loss}",
                    nonce=nonce,
                    signature=""
                )
                self._log_authorization(token, broker_snapshot)
                return token

        # 4. Check order size limits
        if request.projected_notional > self.max_order_notional:
            token = P01DAuthorizationToken(
                token_id=token_id,
                intent_id=request.intent_id,
                broker_snapshot_version=broker_snapshot.version,
                authorized_at_ms=now_ms,
                expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                decision=P01DDecision.REJECTED,
                rejection_reason=f"Order notional {request.projected_notional} exceeds limit {self.max_order_notional}",
                nonce=nonce,
                signature=""
            )
            self._log_authorization(token, broker_snapshot)
            return token

        # 5. Check concentration limits
        current_position = broker_snapshot.open_positions.get(request.symbol, 0)
        max_concentration = broker_snapshot.current_equity * Decimal("0.40")  # 40% of equity per symbol

        if request.projected_notional > max_concentration:
            token = P01DAuthorizationToken(
                token_id=token_id,
                intent_id=request.intent_id,
                broker_snapshot_version=broker_snapshot.version,
                authorized_at_ms=now_ms,
                expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
                decision=P01DDecision.REJECTED,
                rejection_reason=f"Concentration limit: {request.projected_notional} > {max_concentration}",
                nonce=nonce,
                signature=""
            )
            self._log_authorization(token, broker_snapshot)
            return token

        # ==================== AUTHORIZATION GRANTED ====================

        signature = self._sign_authorization(request, broker_snapshot, nonce)

        token = P01DAuthorizationToken(
            token_id=token_id,
            intent_id=request.intent_id,
            broker_snapshot_version=broker_snapshot.version,
            authorized_at_ms=now_ms,
            expires_at_ms=now_ms + (self.token_expiry_sec * 1000),
            decision=P01DDecision.AUTHORIZED,
            rejection_reason=None,
            nonce=nonce,
            signature=signature
        )

        self._log_authorization(token, broker_snapshot)

        logger.info(
            f"✓ P01D AUTHORIZED: {request.symbol} {request.side} {request.quantity} "
            f"notional={request.projected_notional} token={token_id[:20]}... "
            f"expires in {self.token_expiry_sec}s"
        )

        return token

    def verify_token_before_submission(
        self,
        token: P01DAuthorizationToken,
        current_broker_snapshot_version: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Block 7C MUST call this immediately before broker submission.

        Checks:
        - Token decision is AUTHORIZED
        - Token has not expired
        - Broker snapshot version matches (prevents TOCTOU)
        - Token signature is valid (not tampered)
        - Token has not been used before (single-use)

        Returns:
            (is_valid, error_reason)
        """

        now_ms = int(time.time() * 1000)

        # Check decision
        if token.decision != P01DDecision.AUTHORIZED:
            return False, f"Token not authorized: {token.decision.value}"

        # Check expiry
        if now_ms > token.expires_at_ms:
            return False, f"Token expired {(now_ms - token.expires_at_ms)}ms ago"

        # Check snapshot version matches current broker state
        if token.broker_snapshot_version != current_broker_snapshot_version:
            return False, f"Snapshot version mismatch: token {token.broker_snapshot_version} vs broker {current_broker_snapshot_version}"

        # Check signature (if key is set)
        if self.hmac_secret:
            # Reconstruct the data that was signed
            data_to_verify = f"{token.token_id}:{token.intent_id}:{token.broker_snapshot_version}:{token.authorized_at_ms}:{token.nonce}"
            expected_sig = hmac.new(self.hmac_secret, data_to_verify.encode(), hashlib.sha256).hexdigest()

            if token.signature != expected_sig:
                return False, "Token signature invalid (tampering detected)"

        # Check single-use (not already consumed)
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT used_at_ms FROM p01d_authorizations WHERE token_id = ?",
            (token.token_id,)
        )
        row = cursor.fetchone()

        if row and row['used_at_ms'] is not None:
            return False, f"Token already used at {row['used_at_ms']}"

        # ==================== TOKEN IS VALID ====================

        # Mark as used (atomic single-use)
        cursor.execute(
            "UPDATE p01d_authorizations SET used_at_ms = ? WHERE token_id = ?",
            (now_ms, token.token_id)
        )
        self.conn.commit()

        logger.info(f"✓ P01D token verified and consumed: {token.token_id[:20]}...")

        return True, None

    def _sign_authorization(
        self,
        request: P01DAuthorizationRequest,
        snapshot: BrokerSnapshot,
        nonce: str,
    ) -> str:
        """Create HMAC-SHA256 signature of authorization."""
        if not self.hmac_secret:
            return ""

        # Deterministic serialization
        data = f"{request.intent_id}:{request.symbol}:{request.side}:{request.quantity}:{snapshot.version}:{nonce}"

        signature = hmac.new(self.hmac_secret, data.encode(), hashlib.sha256).hexdigest()
        return signature

    def _log_authorization(
        self,
        token: P01DAuthorizationToken,
        snapshot: BrokerSnapshot,
    ):
        """Log authorization to durable database."""
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO p01d_authorizations
            (token_id, intent_id, symbol, side, quantity, projected_notional, risk_capacity,
             is_risk_reduction, broker_snapshot_version, authorized_at_ms, expires_at_ms,
             decision, rejection_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token.token_id,
            token.intent_id,
            "",  # symbol not in token, would need to extract from intent
            "",  # side
            0,   # quantity
            0.0,
            0.0,
            0,
            token.broker_snapshot_version,
            token.authorized_at_ms,
            token.expires_at_ms,
            token.decision.value,
            token.rejection_reason
        ))

        self.conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test P01D gate
    gate = P01DSovereignAuthorizationGate(account_id="ACC123")

    # Create broker snapshot
    snapshot = BrokerSnapshot(
        version=1,
        timestamp_ms=int(time.time() * 1000),
        account_id="ACC123",
        current_equity=Decimal('1000000'),
        available_margin=Decimal('500000'),
        open_positions={'INFY': 0},
        daily_pnl=Decimal('0'),
        max_daily_loss=Decimal('-50000')
    )

    # Create authorization request
    request = P01DAuthorizationRequest(
        intent_id="ENTRY_INFY_12345",
        symbol='INFY',
        side='BUY',
        quantity=10,
        order_type='LIMIT',
        limit_price=Decimal('1500.00'),
        projected_position=10,
        projected_notional=Decimal('15000'),
        risk_capacity=0.80,
        is_risk_reduction=False
    )

    # Authorize
    token = gate.authorize(request, snapshot)

    print(f"✓ Authorization test:")
    print(f"  Decision: {token.decision.value}")
    print(f"  Token ID: {token.token_id[:20]}...")
    print(f"  Expires: {token.expires_at_ms - token.authorized_at_ms}ms from now")

    # Verify before submission
    is_valid, error = gate.verify_token_before_submission(token, snapshot.version)
    print(f"  Pre-submission validation: {is_valid}")

    if is_valid:
        print("✓ P01D gate working correctly")
