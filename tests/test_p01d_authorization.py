"""
P01D Sovereign Authorization Gate Tests
"""

import pytest
import time
from dataclasses import replace
from decimal import Decimal
from unittest.mock import MagicMock

from blocks.block_p01d_sovereign_authorization import (
    P01DSovereignAuthorizationGate,
    P01DAuthorizationRequest,
    BrokerSnapshot,
    P01DDecision
)


@pytest.fixture
def p01d_gate():
    """Create P01D gate with mock setup."""
    gate = P01DSovereignAuthorizationGate(account_id="TEST_ACC", db_path=":memory:")
    return gate


@pytest.fixture
def broker_snapshot():
    """Create healthy broker snapshot."""
    return BrokerSnapshot(
        version=1,
        timestamp_ms=int(time.time() * 1000),
        account_id="TEST_ACC",
        current_equity=Decimal('1000000'),
        available_margin=Decimal('500000'),
        open_positions={'INFY': 0},
        daily_pnl=Decimal('0'),
        max_daily_loss=Decimal('-50000')
    )


class TestP01DAuthorization:
    """Test P01D authorization gate."""

    def test_authorization_approved_valid_request(self, p01d_gate, broker_snapshot):
        """Valid request should be approved."""
        request = P01DAuthorizationRequest(
            intent_id="ENTRY_INFY_001",
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

        token = p01d_gate.authorize(request, broker_snapshot)

        assert token.decision == P01DDecision.AUTHORIZED
        assert token.rejection_reason is None
        assert token.intent_id == "ENTRY_INFY_001"

    def test_authorization_rejected_insufficient_margin(self, p01d_gate, broker_snapshot):
        """Request with insufficient margin should be rejected."""
        request = P01DAuthorizationRequest(
            intent_id="ENTRY_INFY_002",
            symbol='TCS',
            side='BUY',
            quantity=1000,  # Huge quantity
            order_type='LIMIT',
            limit_price=Decimal('3500.00'),
            projected_position=1000,
            projected_notional=Decimal('3500000'),  # ₹35 lakh
            risk_capacity=0.80,
            is_risk_reduction=False
        )

        token = p01d_gate.authorize(request, broker_snapshot)

        assert token.decision == P01DDecision.REJECTED
        assert "margin" in token.rejection_reason.lower()

    def test_authorization_rejected_daily_risk_exceeded(self, p01d_gate, broker_snapshot):
        """Request when daily risk exhausted should be rejected."""
        # Setup: already lost ₹50k today
        snapshot_with_loss = BrokerSnapshot(
            version=1,
            timestamp_ms=int(time.time() * 1000),
            account_id="TEST_ACC",
            current_equity=Decimal('950000'),  # ₹50k down
            available_margin=Decimal('400000'),
            open_positions={},
            daily_pnl=Decimal('-50000'),  # Exactly at limit
            max_daily_loss=Decimal('-50000')
        )

        request = P01DAuthorizationRequest(
            intent_id="ENTRY_INFY_003",
            symbol='INFY',
            side='BUY',
            quantity=10,
            order_type='LIMIT',
            limit_price=Decimal('1500.00'),
            projected_position=10,
            projected_notional=Decimal('15000'),
            risk_capacity=0.80,
            is_risk_reduction=False  # NOT risk-reduction
        )

        token = p01d_gate.authorize(request, snapshot_with_loss)

        assert token.decision == P01DDecision.REJECTED
        assert "risk budget" in token.rejection_reason.lower()

    def test_authorization_allowed_for_risk_reduction(self, p01d_gate, broker_snapshot):
        """Risk-reduction orders bypass daily risk check."""
        snapshot_with_loss = BrokerSnapshot(
            version=1,
            timestamp_ms=int(time.time() * 1000),
            account_id="TEST_ACC",
            current_equity=Decimal('950000'),
            available_margin=Decimal('400000'),
            open_positions={'INFY': 10},
            daily_pnl=Decimal('-50000'),
            max_daily_loss=Decimal('-50000')
        )

        request = P01DAuthorizationRequest(
            intent_id="EXIT_INFY_001",
            symbol='INFY',
            side='SELL',
            quantity=10,
            order_type='MARKET',
            limit_price=None,
            projected_position=0,
            projected_notional=Decimal('15000'),
            risk_capacity=0.80,
            is_risk_reduction=True  # EXIT = risk-reduction
        )

        token = p01d_gate.authorize(request, snapshot_with_loss)

        assert token.decision == P01DDecision.AUTHORIZED

    def test_token_verification_succeeds_valid_token(self, p01d_gate, broker_snapshot):
        """Valid token should pass verification."""
        request = P01DAuthorizationRequest(
            intent_id="ENTRY_001",
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

        token = p01d_gate.authorize(request, broker_snapshot)
        is_valid, error = p01d_gate.verify_token_before_submission(token, broker_snapshot.version)

        assert is_valid is True
        assert error is None

    def test_token_verification_fails_expired_token(self, p01d_gate, broker_snapshot):
        """Expired token should fail verification."""
        request = P01DAuthorizationRequest(
            intent_id="ENTRY_002",
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

        token = p01d_gate.authorize(request, broker_snapshot)

        # Simulate expiry (set expires_at to past)
        expired_token = replace(token, expires_at_ms=int(time.time() * 1000) - 1000)

        is_valid, error = p01d_gate.verify_token_before_submission(expired_token, broker_snapshot.version)

        assert is_valid is False
        assert "expired" in error.lower()

    def test_token_verification_fails_version_mismatch(self, p01d_gate, broker_snapshot):
        """Snapshot version mismatch should fail verification."""
        request = P01DAuthorizationRequest(
            intent_id="ENTRY_003",
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

        token = p01d_gate.authorize(request, broker_snapshot)

        # Broker state changed (version incremented)
        is_valid, error = p01d_gate.verify_token_before_submission(token, current_broker_snapshot_version=2)

        assert is_valid is False
        assert "version" in error.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
