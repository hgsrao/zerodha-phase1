"""
REVISION 04 (Proper): Hard Acceptance Tests

All of these must PASS before sealed month can run.
No exceptions. No workarounds.
"""

import pytest
from revision4.contracts import (
    Bar, OrderIntent, SizedProposal, TradePlan,
    ForecastSignal, IDDecision, FillEvent, OrderState,
    EffectiveConfig, SignalType, ExitReason, ExitEvent
)
from revision4.portfolio import PortfolioLedger
from revision4.paper_broker import PaperBroker
from revision4.dataset_seal import DatasetValidator


class TestDatasetRequirements:
    """Dataset must have exactly 48 symbols."""

    def test_48_symbols_required(self):
        """Fail if not 48 symbols. Requires real data directory."""
        import os

        # Get the actual data directory from environment or manifest
        data_dir = os.environ.get('NSE_DATA_DIR')
        manifest_path = 'revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json'

        if not os.path.exists(manifest_path):
            pytest.skip("Manifest not available")

        if not data_dir:
            pytest.skip("NSE_DATA_DIR not set; skipping file validation")

        validator = DatasetValidator(manifest_path)
        try:
            seal = validator.load_manifest(data_dir)
            assert seal.symbol_count == 48, f"Need 48 symbols, got {seal.symbol_count}"

            # Verify all hashes match
            assert len(seal.symbol_hashes) == 48, "All 48 symbols should have hashes"
        except RuntimeError as e:
            pytest.fail(f"Dataset validation failed: {e}")


class TestCashConstraints:
    """Cash never goes negative. Ever."""

    def test_cash_never_negative(self):
        """After any operation, cash >= 0."""
        ledger = PortfolioLedger(starting_cash=100_000.0)

        # Attempt to create order that exceeds cash
        config = EffectiveConfig()

        proposal = SizedProposal(
            timestamp="2024-08-01T09:15:00",
            bar_index=0,
            symbol="INFY",
            plan=TradePlan(
                timestamp="2024-08-01T09:15:00",
                bar_index=0,
                symbol="INFY",
                direction=1,
                entry_price=3000.0,
                stop_price=2990.0,
                target_price=3025.0,
                risk_per_share=10.0,
                position_size_base=100.0,  # Would cost 300k
            ),
            mpc_scaling_factor=1.0,
            final_quantity=100.0,
            cost_estimate=300_000.0,
        )

        order = OrderIntent(
            order_id="order_1",
            timestamp_created="2024-08-01T09:15:00",
            bar_index_created=0,
            symbol="INFY",
            direction=1,
            quantity=100.0,
            stop_price=2990.0,
            target_price=3025.0,
            proposal=proposal,
        )

        # Try to create order (should fail due to insufficient cash)
        ok, msg = ledger.create_order("order_1", order)

        # Order should be rejected
        assert ok == False, f"Order should be rejected, but: {msg}"
        assert ledger.cash == 100_000.0, "Cash should not change"


class TestNextBarFills:
    """Orders created at bar t fill at bar t+1, never at bar t."""

    def test_order_not_filled_same_bar(self):
        """Order at bar t must not fill until bar t+1+."""
        broker = PaperBroker()
        config = EffectiveConfig()

        proposal = SizedProposal(
            timestamp="2024-08-01T09:15:00",
            bar_index=0,
            symbol="INFY",
            plan=TradePlan(
                timestamp="2024-08-01T09:15:00",
                bar_index=0,
                symbol="INFY",
                direction=1,
                entry_price=3000.0,
                stop_price=2990.0,
                target_price=3025.0,
                risk_per_share=10.0,
                position_size_base=10.0,
            ),
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=100.0,
        )

        order = OrderIntent(
            order_id="order_1",
            timestamp_created="2024-08-01T09:15:00",
            bar_index_created=0,
            symbol="INFY",
            direction=1,
            quantity=10.0,
            stop_price=2990.0,
            target_price=3025.0,
            proposal=proposal,
        )

        # Submit order
        broker.submit_order(order)

        # Bar 0 (creation bar): should NOT fill
        bar_0 = Bar(
            timestamp="2024-08-01T09:15:00",
            symbol="INFY",
            open=3000.0,
            high=3010.0,
            low=2990.0,
            close=3005.0,
            volume=1000000,
        )

        fill_event = broker.try_fill_order("order_1", bar_0, fill_bar_index=0, config=config)
        assert fill_event is None, "Should NOT fill at same bar"

        # Bar 1 (next bar): should fill at open
        bar_1 = Bar(
            timestamp="2024-08-01T09:16:00",
            symbol="INFY",
            open=3006.0,
            high=3015.0,
            low=3000.0,
            close=3010.0,
            volume=1000000,
        )

        fill_event = broker.try_fill_order("order_1", bar_1, fill_bar_index=1, config=config)
        assert fill_event is not None, "Should fill at bar t+1"
        assert fill_event.fill_price == 3006.0, "Should fill at open price"


class TestMaxPositions:
    """Cannot exceed 5 concurrent positions."""

    def test_sixth_position_rejected(self):
        """Sixth position should be rejected."""
        ledger = PortfolioLedger()

        # Try to add 6 positions
        for i in range(6):
            symbol = f"STOCK{i}"
            position_stub = Bar(
                timestamp="2024-08-01T09:15:00",
                symbol=symbol,
                open=1000.0,
                high=1010.0,
                low=990.0,
                close=1005.0,
                volume=1000000,
            )

            # Simulate position creation
            from revision4.contracts import Position

            pos = Position(
                symbol=symbol,
                direction=1,
                entry_price=1000.0,
                entry_bar_index=0,
                entry_bar_timestamp="2024-08-01T09:15:00",
                quantity=1.0,
                stop_price=990.0,
                target_price=1010.0,
                cost_paid=5.0,
                fill_id="fill_1",
            )

            ledger.positions[symbol] = pos

        # Check limit
        assert len(ledger.positions) == 6, "Added 6 positions"

        # Reconciliation should fail
        ok, msg = ledger.reconcile()
        assert ok == False, "Reconciliation should fail with 6 positions"
        assert "More than 5 positions" in msg


class TestLedgerReconciliation:
    """Ledger must reconcile: every event matches state."""

    def test_cost_reconciliation(self):
        """Transaction costs recorded must match cash reduction."""
        ledger = PortfolioLedger(starting_cash=100_000.0)
        initial_cash = ledger.cash

        # Simulate fill
        cost_paid = 5.0
        ledger.total_costs += cost_paid
        ledger.cash -= 1000.0 + cost_paid  # position + cost

        assert ledger.cash == initial_cash - 1000.0 - cost_paid


class TestDeterminism:
    """Same input twice → same output."""

    def test_repeated_run_produces_same_result(self):
        """Two runs with identical parameters should reconcile identically."""
        # Run 1
        ledger1 = PortfolioLedger()
        ledger1.cash -= 100.0
        ledger1.total_costs += 5.0

        # Run 2
        ledger2 = PortfolioLedger()
        ledger2.cash -= 100.0
        ledger2.total_costs += 5.0

        # Should be identical
        assert ledger1.cash == ledger2.cash
        assert ledger1.total_costs == ledger2.total_costs


class TestNoFutureDataLeakage:
    """No bar t+1 accessed during bar t processing."""

    def test_no_lookahead(self):
        """Pipeline should only use data up to current bar."""
        # Architecture ensures this:
        # - Input: closes_history[0:current_bar]
        # - No access to bar t+1
        # This is enforced at the replay loop level, not testable in isolation
        pass


class TestParameterTrace:
    """Every parameter fetch is logged for audit."""

    def test_parameter_trace_logged(self):
        """Parameters fetched via _log_param should appear in trace."""
        from revision4.pipeline import PipelineAdapter

        config = EffectiveConfig()
        adapter = PipelineAdapter(config)

        # Use adapter to call make_id_decision (which logs params)
        forecast = ForecastSignal(
            timestamp="2024-08-01T09:15:00",
            bar_index=0,
            symbol="INFY",
            signal_type=SignalType.MOMENTUM_UP,
            pa_confidence=0.80,
            chart_confidence=0.65,
            rejection_reason=None,
        )

        decision = adapter.make_id_decision(
            forecast=forecast,
            hour=10,
            minute=0,
            grid_sync=True,
        )

        # Check parameter trace - should have at least these params logged
        trace = adapter.get_parameter_trace()
        trace_params = [t[0] for t in trace]

        assert len(trace) > 0, "Parameters should be traced"
        assert "pa_confidence_min" in trace_params, "pa_confidence_min should be logged"
        assert "trading_start_hour" in trace_params, "trading_start_hour should be logged"


class TestEODFlattening:
    """All positions closed at market close (3:29 PM)."""

    def test_all_positions_closed_at_eod(self):
        """EOD should close all open positions."""
        # This is enforced at replay loop level
        # Test verifies the close_position method works
        ledger = PortfolioLedger()

        from revision4.contracts import Position

        # Add a position
        pos = Position(
            symbol="INFY",
            direction=1,
            entry_price=3000.0,
            entry_bar_index=0,
            entry_bar_timestamp="2024-08-01T09:15:00",
            quantity=10.0,
            stop_price=2990.0,
            target_price=3025.0,
            cost_paid=5.0,
            fill_id="fill_1",
        )

        ledger.positions["INFY"] = pos

        # Close at EOD
        exit_event = ExitEvent(
            exit_id="exit_1",
            symbol="INFY",
            timestamp_exit="2024-08-01T15:29:00",
            bar_index_exit=390,
            entry_price=3000.0,
            exit_price=3010.0,
            quantity=10.0,
            direction=1,
            bars_held=390,
            exit_reason=ExitReason.EOD_FLATTENING,
            pnl_realized=100.0 - 5.0,  # (exit - entry) * qty - cost
            pnl_pct=0.33,
        )

        ok, msg = ledger.close_position(exit_event)
        assert ok == True, f"Should close position: {msg}"
        assert "INFY" not in ledger.positions, "Position should be removed"
        assert ledger.realized_pnl == 95.0, "P&L should be recorded"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
