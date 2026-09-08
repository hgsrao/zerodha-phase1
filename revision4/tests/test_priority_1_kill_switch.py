"""
REVISION 04: Priority 1 - Kill Switch Enforcement + Broker Cleanup

Verify that:
1. Kill switch blocks order submission when disabled
2. Kill switch blocks order fill when disabled
3. Filled orders are properly retired from active_orders
"""

import pytest
from revision4.contracts import (
    EffectiveConfig, Bar, OrderIntent, SizedProposal, TradePlan,
    ForecastSignal, IDDecision, OrderState, SignalType
)
from revision4.paper_broker import PaperBroker


class TestKillSwitchBlocksSubmission:
    """Verify kill switch blocks order submission."""

    def test_submit_order_succeeds_when_kill_switch_enabled(self):
        """Order submission succeeds when kill switch is enabled."""
        broker = PaperBroker()
        config = EffectiveConfig()

        # Create a minimal order
        forecast = ForecastSignal(
            timestamp="2024-08-01T09:15:00Z",
            bar_index=1,
            symbol="INFY",
            signal_type=SignalType.MOMENTUM_UP,
            pa_confidence=0.8,
            chart_confidence=0.7,
            rejection_reason=None,
        )

        decision = IDDecision(
            timestamp=forecast.timestamp,
            bar_index=forecast.bar_index,
            symbol=forecast.symbol,
            forecast=forecast,
            entry_valid=True,
            entry_reason="Test",
            current_hour=9,
            current_minute=15,
            grid_sync=True,
            grid_reason="Test",
        )

        plan = TradePlan(
            timestamp=decision.timestamp,
            bar_index=decision.bar_index,
            symbol=decision.symbol,
            direction=1,
            entry_price=3000.0,
            stop_price=2990.0,
            target_price=3010.0,
            risk_per_share=10.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=plan.timestamp,
            bar_index=plan.bar_index,
            symbol=plan.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=30.0,
        )

        order = OrderIntent(
            order_id="order_1",
            timestamp_created=forecast.timestamp,
            bar_index_created=forecast.bar_index,
            symbol=forecast.symbol,
            direction=1,
            quantity=10.0,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            proposal=proposal,
            state=OrderState.PENDING,
            rejection_reason=None,
        )

        # Kill switch should be enabled by default
        success, reason = broker.submit_order(order, config)
        assert success, f"Expected successful submission, got: {reason}"

    def test_submit_order_fails_when_kill_switch_disabled(self):
        """Order submission fails when kill switch is disabled."""
        broker = PaperBroker()

        # Create a config with kill switch disabled
        # (We'll need to modify config to support this)
        class DisabledKillSwitchConfig:
            def require(self, param: str):
                if param == "kill_switch_enabled":
                    return False
                return None

        config = DisabledKillSwitchConfig()

        # Create a minimal order
        forecast = ForecastSignal(
            timestamp="2024-08-01T09:15:00Z",
            bar_index=1,
            symbol="INFY",
            signal_type=SignalType.MOMENTUM_UP,
            pa_confidence=0.8,
            chart_confidence=0.7,
            rejection_reason=None,
        )

        decision = IDDecision(
            timestamp=forecast.timestamp,
            bar_index=forecast.bar_index,
            symbol=forecast.symbol,
            forecast=forecast,
            entry_valid=True,
            entry_reason="Test",
            current_hour=9,
            current_minute=15,
            grid_sync=True,
            grid_reason="Test",
        )

        plan = TradePlan(
            timestamp=decision.timestamp,
            bar_index=decision.bar_index,
            symbol=decision.symbol,
            direction=1,
            entry_price=3000.0,
            stop_price=2990.0,
            target_price=3010.0,
            risk_per_share=10.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=plan.timestamp,
            bar_index=plan.bar_index,
            symbol=plan.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=30.0,
        )

        order = OrderIntent(
            order_id="order_1",
            timestamp_created=forecast.timestamp,
            bar_index_created=forecast.bar_index,
            symbol=forecast.symbol,
            direction=1,
            quantity=10.0,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            proposal=proposal,
            state=OrderState.PENDING,
            rejection_reason=None,
        )

        success, reason = broker.submit_order(order, config)
        assert not success, "Expected rejection with kill switch disabled"
        assert "kill switch" in reason.lower(), f"Expected kill switch message, got: {reason}"


class TestKillSwitchBlocksFill:
    """Verify kill switch blocks order fill."""

    def test_try_fill_order_fails_when_kill_switch_disabled(self):
        """Order fill fails when kill switch is disabled."""
        broker = PaperBroker()

        # Create a config with kill switch disabled
        class DisabledKillSwitchConfig:
            def require(self, param: str):
                if param == "kill_switch_enabled":
                    return False
                return None

        config = DisabledKillSwitchConfig()

        # Create and submit an order (without config, to bypass submission check)
        forecast = ForecastSignal(
            timestamp="2024-08-01T09:15:00Z",
            bar_index=1,
            symbol="INFY",
            signal_type=SignalType.MOMENTUM_UP,
            pa_confidence=0.8,
            chart_confidence=0.7,
            rejection_reason=None,
        )

        decision = IDDecision(
            timestamp=forecast.timestamp,
            bar_index=forecast.bar_index,
            symbol=forecast.symbol,
            forecast=forecast,
            entry_valid=True,
            entry_reason="Test",
            current_hour=9,
            current_minute=15,
            grid_sync=True,
            grid_reason="Test",
        )

        plan = TradePlan(
            timestamp=decision.timestamp,
            bar_index=decision.bar_index,
            symbol=decision.symbol,
            direction=1,
            entry_price=3000.0,
            stop_price=2990.0,
            target_price=3010.0,
            risk_per_share=10.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=plan.timestamp,
            bar_index=plan.bar_index,
            symbol=plan.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=30.0,
        )

        order = OrderIntent(
            order_id="order_1",
            timestamp_created=forecast.timestamp,
            bar_index_created=forecast.bar_index,
            symbol=forecast.symbol,
            direction=1,
            quantity=10.0,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            proposal=proposal,
            state=OrderState.PENDING,
            rejection_reason=None,
        )

        # Submit without config check
        broker.submit_order(order, config=None)

        # Try to fill with kill switch disabled
        bar = Bar(
            timestamp="2024-08-01T09:16:00Z",
            symbol="INFY",
            open=3010.0,
            high=3015.0,
            low=3005.0,
            close=3012.0,
            volume=1000,
        )

        fill = broker.try_fill_order("order_1", bar, 2, config)
        assert fill is None, "Expected fill rejection when kill switch is disabled"


class TestBrokerOrderCleanup:
    """Verify filled orders are properly retired."""

    def test_retire_filled_orders_removes_from_active(self):
        """retire_filled_orders() removes filled orders from active_orders."""
        broker = PaperBroker()
        config = EffectiveConfig()

        # Create and submit an order
        forecast = ForecastSignal(
            timestamp="2024-08-01T09:15:00Z",
            bar_index=1,
            symbol="INFY",
            signal_type=SignalType.MOMENTUM_UP,
            pa_confidence=0.8,
            chart_confidence=0.7,
            rejection_reason=None,
        )

        decision = IDDecision(
            timestamp=forecast.timestamp,
            bar_index=forecast.bar_index,
            symbol=forecast.symbol,
            forecast=forecast,
            entry_valid=True,
            entry_reason="Test",
            current_hour=9,
            current_minute=15,
            grid_sync=True,
            grid_reason="Test",
        )

        plan = TradePlan(
            timestamp=decision.timestamp,
            bar_index=decision.bar_index,
            symbol=decision.symbol,
            direction=1,
            entry_price=3000.0,
            stop_price=2990.0,
            target_price=3010.0,
            risk_per_share=10.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=plan.timestamp,
            bar_index=plan.bar_index,
            symbol=plan.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=30.0,
        )

        order = OrderIntent(
            order_id="order_1",
            timestamp_created=forecast.timestamp,
            bar_index_created=forecast.bar_index,
            symbol=forecast.symbol,
            direction=1,
            quantity=10.0,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            proposal=proposal,
            state=OrderState.PENDING,
            rejection_reason=None,
        )

        broker.submit_order(order, config)
        assert "order_1" in broker.active_orders, "Order should be in active_orders after submit"

        # Fill the order
        bar = Bar(
            timestamp="2024-08-01T09:16:00Z",
            symbol="INFY",
            open=3010.0,
            high=3015.0,
            low=3005.0,
            close=3012.0,
            volume=1000,
        )

        fill = broker.try_fill_order("order_1", bar, 2, config)
        assert fill is not None, "Expected successful fill"
        assert "order_1" in broker.active_orders, "Filled order should still be in active_orders before cleanup"

        # Retire filled orders
        count = broker.retire_filled_orders()
        assert count == 1, f"Expected 1 order retired, got {count}"
        assert "order_1" not in broker.active_orders, "Filled order should be removed from active_orders"
        assert "order_1" in broker.order_history, "Filled order should still be in order_history"

    def test_retire_filled_orders_preserves_pending(self):
        """retire_filled_orders() only removes filled orders, not pending ones."""
        broker = PaperBroker()
        config = EffectiveConfig()

        # Create two orders
        forecast = ForecastSignal(
            timestamp="2024-08-01T09:15:00Z",
            bar_index=1,
            symbol="INFY",
            signal_type=SignalType.MOMENTUM_UP,
            pa_confidence=0.8,
            chart_confidence=0.7,
            rejection_reason=None,
        )

        decision = IDDecision(
            timestamp=forecast.timestamp,
            bar_index=forecast.bar_index,
            symbol=forecast.symbol,
            forecast=forecast,
            entry_valid=True,
            entry_reason="Test",
            current_hour=9,
            current_minute=15,
            grid_sync=True,
            grid_reason="Test",
        )

        plan = TradePlan(
            timestamp=decision.timestamp,
            bar_index=decision.bar_index,
            symbol=decision.symbol,
            direction=1,
            entry_price=3000.0,
            stop_price=2990.0,
            target_price=3010.0,
            risk_per_share=10.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=plan.timestamp,
            bar_index=plan.bar_index,
            symbol=plan.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=30.0,
        )

        # Order 1
        order1 = OrderIntent(
            order_id="order_1",
            timestamp_created=forecast.timestamp,
            bar_index_created=forecast.bar_index,
            symbol="INFY",
            direction=1,
            quantity=10.0,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            proposal=proposal,
            state=OrderState.PENDING,
            rejection_reason=None,
        )

        # Order 2 (different symbol, so can be pending simultaneously)
        order2 = OrderIntent(
            order_id="order_2",
            timestamp_created=forecast.timestamp,
            bar_index_created=forecast.bar_index,
            symbol="TCS",
            direction=1,
            quantity=5.0,
            stop_price=4000.0,
            target_price=4010.0,
            proposal=proposal,
            state=OrderState.PENDING,
            rejection_reason=None,
        )

        broker.submit_order(order1, config)
        broker.submit_order(order2, config)
        assert len(broker.active_orders) == 2, "Should have 2 orders in active_orders"

        # Fill only order 1
        bar = Bar(
            timestamp="2024-08-01T09:16:00Z",
            symbol="INFY",
            open=3010.0,
            high=3015.0,
            low=3005.0,
            close=3012.0,
            volume=1000,
        )

        fill = broker.try_fill_order("order_1", bar, 2, config)
        assert fill is not None, "Expected successful fill of order_1"

        # Retire filled orders
        count = broker.retire_filled_orders()
        assert count == 1, f"Expected 1 order retired, got {count}"

        # Check final state
        assert "order_1" not in broker.active_orders, "Filled order_1 should be removed"
        assert "order_2" in broker.active_orders, "Pending order_2 should remain"
        assert broker.active_orders["order_2"].state == OrderState.PENDING


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
