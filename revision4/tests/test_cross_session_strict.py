"""
Strict Cross-Session Rejection Integration Test

End-to-end Friday→Monday scenario proving:
1. Friday order submitted (passes pre-submission)
2. Monday bar → fill attempted → cross-session detected
3. Orchestrator cancels order atomically
4. Ledger reservation released
5. Reconciliation succeeds with no dangling orders
"""

import pytest
import pandas as pd

from revision4.contracts import (
    Bar, EffectiveConfig, OrderIntent, OrderState,
    SizedProposal, TradePlan
)
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger


class TestCrossSessionStrict:
    """Strict end-to-end cross-session lifecycle tests."""

    @pytest.fixture
    def config(self):
        """Default config (authorized_cross_session=False)."""
        return EffectiveConfig(
            authorized_cross_session=False,
            trading_hours_start='09:15',
            trading_hours_end='15:30',
        )

    def _make_bar(self, timestamp: str, symbol: str = "TEST") -> Bar:
        """Create a test bar."""
        return Bar(
            timestamp=timestamp,
            symbol=symbol,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1000,
        )

    def _make_order_intent(self, order_id: str, symbol: str = "TEST",
                          timestamp_created: str = "2024-08-01 09:15:00+00:00") -> OrderIntent:
        """Create a test order intent."""
        plan = TradePlan(
            timestamp=timestamp_created,
            bar_index=0,
            symbol=symbol,
            direction=1,
            entry_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            risk_per_share=5.0,
            position_size_base=1.0,
        )
        proposal = SizedProposal(
            timestamp=timestamp_created,
            bar_index=0,
            symbol=symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=1.0,
            cost_estimate=100.0,
        )

        return OrderIntent(
            order_id=order_id,
            timestamp_created=timestamp_created,
            bar_index_created=0,
            symbol=symbol,
            direction=1,
            quantity=1,
            stop_price=95.0,
            target_price=110.0,
            proposal=proposal,
            state=OrderState.PENDING,
        )

    def test_monday_orchestrator_cancels_friday_order(self, config):
        """
        Orchestrator detects cross-session and atomically cancels Friday order.

        Scenario:
        1. Friday bar + submit Friday order
        2. Monday bar arrives → try_fill_order returns None
        3. Orchestrator detects date mismatch → cancels order
        4. Ledger reservation released
        5. Reconciliation succeeds

        Expected:
        - Order transitions from PENDING to CANCELLED
        - Cash reservation released
        - No fill recorded
        - Ledger reconciles
        """
        # Setup
        ledger = PortfolioLedger()
        broker = PaperBroker()

        # Friday order
        friday_order = self._make_order_intent(
            "F2",
            timestamp_created="2024-08-02 15:00:00+00:00"
        )

        # Manually create and submit (simulate pre-submission passed)
        ok, msg = ledger.create_order("F2", friday_order)
        assert ok, f"Order creation failed: {msg}"

        ok, msg = broker.submit_order(friday_order, config)
        assert ok, f"Broker submission failed: {msg}"

        # Verify order is pending and reservation made
        assert "F2" in broker.active_orders
        assert broker.active_orders["F2"].state == OrderState.PENDING
        initial_reserved = ledger.reserved_cash
        assert initial_reserved > 0, "Reservation should be made"

        # Monday bar arrives
        monday_bar = self._make_bar("2024-08-05 09:15:00+00:00")

        # Try to fill (should return None due to cross-session)
        fill = broker.try_fill_order("F2", monday_bar, 1, config)
        assert fill is None, "Cross-session fill should be rejected"

        # Order still pending in broker
        assert broker.active_orders["F2"].state == OrderState.PENDING

        # Simulate orchestrator detection and cancellation
        friday_date = pd.Timestamp(friday_order.timestamp_created).date().isoformat()
        monday_date = pd.Timestamp(monday_bar.timestamp).date().isoformat()

        if friday_date != monday_date:
            # Cancel at broker
            ok, msg = broker.cancel_order("F2", "CROSS_SESSION_PROHIBITED")
            assert ok, f"Broker cancel failed: {msg}"

            # Cancel at ledger (release reservation)
            ok, msg = ledger.cancel_order("F2")
            assert ok, f"Ledger cancel failed: {msg}"

        # Verify state after cancellation
        # Order should be out of active_orders
        assert "F2" not in broker.active_orders, "Cancelled order should be retired"

        # Order should be in history as CANCELLED
        assert broker.order_history["F2"].state == OrderState.CANCELLED
        assert "CROSS_SESSION" in broker.order_history["F2"].rejection_reason

        # Reservation should be released
        assert ledger.reserved_cash == 0, "Reservation should be released"

        # Cash should be unreserved
        assert ledger.cash == 100000.0, "Cash should be restored"

    def test_reconciliation_after_cross_session_cancellation(self, config):
        """
        Ledger reconciliation succeeds after cross-session cancellation.

        Proves: No dangling orders, no cash leaks, state is consistent.
        """
        ledger = PortfolioLedger()
        broker = PaperBroker()

        # Create and submit Friday order
        friday_order = self._make_order_intent(
            "F3",
            timestamp_created="2024-08-02 14:00:00+00:00"
        )

        ok, _ = ledger.create_order("F3", friday_order)
        assert ok

        ok, _ = broker.submit_order(friday_order, config)
        assert ok

        # Cancel it (cross-session scenario)
        ok, _ = broker.cancel_order("F3", "CROSS_SESSION_PROHIBITED")
        assert ok

        ok, _ = ledger.cancel_order("F3")
        assert ok

        # Reconcile with Monday bar
        monday_bar = self._make_bar("2024-08-05 09:15:00+00:00")
        ok, msg = ledger.reconcile({"TEST": monday_bar})

        # Should succeed (no dangling orders)
        assert ok, f"Reconciliation failed: {msg}"

        # Verify state
        assert len(ledger.pending_orders) == 0, "No pending orders should remain"
        assert ledger.reserved_cash == 0, "No cash should remain reserved"
        assert len(ledger.positions) == 0, "No positions should be open"

    def test_multiple_symbols_friday_to_monday_selective_cancellation(self, config):
        """
        Multiple symbols: one order same-day fill, one cross-session (cancelled).

        Scenario:
        - Friday order submitted, fills Friday (same-day)
        - Friday order 2 submitted, cannot fill until Monday (cross-session cancelled)

        Expected: First fills, second cancels; reconciliation clean.
        """
        ledger = PortfolioLedger()
        broker = PaperBroker()

        # Friday order for SYM1 (will fill same day)
        friday_order_1 = self._make_order_intent(
            "F1",
            symbol="SYM1",
            timestamp_created="2024-08-02 10:00:00+00:00"
        )

        # Friday order for SYM2 (will not fill until Monday = cross-session)
        friday_order_2 = self._make_order_intent(
            "F2",
            symbol="SYM2",
            timestamp_created="2024-08-02 14:00:00+00:00"
        )

        # Create and submit both
        ok, _ = ledger.create_order("F1", friday_order_1)
        assert ok

        ok, _ = ledger.create_order("F2", friday_order_2)
        assert ok

        ok, _ = broker.submit_order(friday_order_1, config)
        assert ok

        ok, _ = broker.submit_order(friday_order_2, config)
        assert ok

        # Friday bar for SYM1 (same day) → should fill
        friday_bar_sym1 = self._make_bar("2024-08-02 11:00:00+00:00", symbol="SYM1")
        fill_f1 = broker.try_fill_order("F1", friday_bar_sym1, 1, config)

        assert fill_f1 is not None, f"Same-day fill should succeed, got: {fill_f1}"

        ok, _ = ledger.fill_order("F1", fill_f1)
        assert ok

        # Monday bar for SYM2 → cross-session rejected
        monday_bar_sym2 = self._make_bar("2024-08-05 09:15:00+00:00", symbol="SYM2")
        fill_f2 = broker.try_fill_order("F2", monday_bar_sym2, 100, config)

        assert fill_f2 is None, "Cross-session fill should return None"

        # Cancel F2
        ok, _ = broker.cancel_order("F2", "CROSS_SESSION_PROHIBITED")
        assert ok

        ok, _ = ledger.cancel_order("F2")
        assert ok

        # Retire filled orders
        broker.retire_filled_orders()

        # Reconcile
        ok, msg = ledger.reconcile({
            "SYM1": friday_bar_sym1,
            "SYM2": monday_bar_sym2,
        })

        assert ok, f"Reconciliation failed: {msg}"

        # Verify state
        assert len(ledger.pending_orders) == 0
        assert len(broker.active_orders) == 0, f"Active orders should be empty, got: {broker.active_orders.keys()}"
        # One position should remain (F1 filled)
        assert len(ledger.positions) == 1, f"Expected 1 position, got {len(ledger.positions)}"
        assert "SYM1" in ledger.positions
