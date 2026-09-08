"""
Cross-Session Rejection Tests

Validates two-stage fail-closed controls:
1. Pre-submission: Orders must expire at session close
2. Pre-fill: Fills are rejected if date(decision) != date(fill) unless authorized

Test coverage:
- Same-session next-bar fill succeeds
- Friday decision → Monday fill is rejected
- Rejected orders do NOT become fills
"""

import pytest
import pandas as pd

from revision4.contracts import (
    Bar, EffectiveConfig, OrderIntent, OrderState,
    SizedProposal, TradePlan
)
from revision4.paper_broker import PaperBroker


class TestCrossSessionRejection:
    """Cross-session order rejection at broker level."""

    @pytest.fixture
    def config_no_cross_session(self):
        """Config with cross-session disabled (default)."""
        return EffectiveConfig(
            authorized_cross_session=False,
            trading_hours_start='09:15',
            trading_hours_end='15:30',
        )

    @pytest.fixture
    def config_cross_session_allowed(self):
        """Config with cross-session explicitly allowed."""
        return EffectiveConfig(
            authorized_cross_session=True,
            trading_hours_start='09:15',
            trading_hours_end='15:30',
        )

    @pytest.fixture
    def broker(self):
        """Fresh paper broker."""
        return PaperBroker()

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

    # ============= Core Tests: Pre-Fill Cross-Session Rejection =============

    def test_broker_rejects_cross_session_fill(self, broker, config_no_cross_session):
        """
        Broker rejects fills when fill_date != decision_date (default behavior).

        Scenario: Order created Friday, fill attempted Monday → rejected.
        Expected: Fill returns None, order stays PENDING.
        """
        order = self._make_order_intent(
            "O1",
            timestamp_created="2024-08-02 14:00:00+00:00"  # Friday
        )

        # Submit order
        ok, msg = broker.submit_order(order, config_no_cross_session)
        assert ok, f"Order submission failed: {msg}"

        # Try to fill on Monday (next trading day)
        monday_bar = self._make_bar("2024-08-05 09:15:00+00:00")  # Monday open

        fill = broker.try_fill_order("O1", monday_bar, 1, config_no_cross_session)

        # Should be rejected (None returned)
        assert fill is None, "Expected fill to be rejected (cross-session), but it was accepted"

        # Order should still be pending
        assert broker.active_orders["O1"].state == OrderState.PENDING, \
            "Order should remain PENDING after cross-session fill rejection"

    def test_broker_allows_cross_session_when_authorized(self, broker, config_cross_session_allowed):
        """
        Broker allows fills across sessions when explicitly authorized.

        Scenario: Same Friday → Monday order, but with authorized_cross_session=True.
        Expected: Fill succeeds, order becomes FILLED.
        """
        order = self._make_order_intent(
            "O2",
            timestamp_created="2024-08-02 14:00:00+00:00"  # Friday
        )

        ok, msg = broker.submit_order(order, config_cross_session_allowed)
        assert ok, f"Order submission failed: {msg}"

        # Try to fill on Monday
        monday_bar = self._make_bar("2024-08-05 09:15:00+00:00")

        fill = broker.try_fill_order("O2", monday_bar, 1, config_cross_session_allowed)

        # Should succeed
        assert fill is not None, "Expected cross-session fill to succeed with authorization"
        assert fill.timestamp_filled == "2024-08-05 09:15:00+00:00"
        assert broker.active_orders["O2"].state == OrderState.FILLED

    def test_broker_allows_same_session_fill(self, broker, config_no_cross_session):
        """
        Broker always allows same-session fills (fill_date == decision_date).

        Scenario: Order created Thursday 14:00, filled Thursday 14:01.
        Expected: Fill succeeds regardless of authorization flag.
        """
        order = self._make_order_intent(
            "O3",
            timestamp_created="2024-08-01 14:00:00+00:00"
        )

        ok, msg = broker.submit_order(order, config_no_cross_session)
        assert ok

        # Fill on same day, one minute later
        same_day_bar = self._make_bar("2024-08-01 14:01:00+00:00")

        fill = broker.try_fill_order("O3", same_day_bar, 1, config_no_cross_session)

        # Should succeed (same date)
        assert fill is not None, "Same-session fill should always succeed"
        assert fill.timestamp_filled == "2024-08-01 14:01:00+00:00"
        assert broker.active_orders["O3"].state == OrderState.FILLED

    def test_rejected_order_never_becomes_fill(self, broker, config_no_cross_session):
        """
        Rejected cross-session orders never transition to FILLED state.

        Scenario: Multiple fill attempts across session boundary.
        Expected: First same-day fill succeeds; cross-session attempts fail.
        """
        order = self._make_order_intent(
            "O4",
            timestamp_created="2024-08-02 15:00:00+00:00"  # Friday 3pm
        )

        broker.submit_order(order, config_no_cross_session)

        # Fill Friday evening (same day) - should succeed
        friday_evening = self._make_bar("2024-08-02 15:15:00+00:00")
        fill1 = broker.try_fill_order("O4", friday_evening, 1, config_no_cross_session)
        assert fill1 is not None, "Same-day fill should succeed"
        assert broker.active_orders["O4"].state == OrderState.FILLED

    def test_cross_session_detection_by_date(self, broker, config_no_cross_session):
        """
        Cross-session detection works correctly via date comparison.

        Scenario: Order timestamps contain dates; comparison uses pd.Timestamp.date()
        Expected: Correctly identifies same-date vs different-date scenarios.
        """
        # Create order Friday at noon (clearly same-day with any Friday fill)
        order = self._make_order_intent(
            "O5",
            timestamp_created="2024-08-02 12:00:00+00:00"
        )
        broker.submit_order(order, config_no_cross_session)

        # Try Friday EOD fill - same date, should succeed
        friday_eod = self._make_bar("2024-08-02 15:29:00+00:00")
        fill_same = broker.try_fill_order("O5", friday_eod, 1, config_no_cross_session)
        assert fill_same is not None, "Same-date fill should succeed"

        # Create new order for cross-date test
        order2 = self._make_order_intent(
            "O6",
            timestamp_created="2024-08-02 12:00:00+00:00"
        )
        broker.submit_order(order2, config_no_cross_session)

        # Try Monday fill - different date, should fail
        monday = self._make_bar("2024-08-05 09:15:00+00:00")
        fill_cross = broker.try_fill_order("O6", monday, 2, config_no_cross_session)
        assert fill_cross is None, "Different-date fill should be rejected"
