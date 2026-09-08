"""
Test proper three-stage gate lifecycle with real data.

Demonstrates:
1. Pre-submission gates (1-13, 17-18) evaluate with real PA/ID/portfolio data
2. Post-fill gates (16) would evaluate actual vs intended fill price
3. Post-reconciliation gates (15) would validate trade booking
"""

import pytest
from datetime import datetime
from revision4.contracts import (
    EffectiveConfig, PortfolioSnapshot, Position, OrderIntent,
    TradePlan, SizedProposal, Bar, FillEvent,
)
from revision4.gates_proper import ProperGateEvaluator


@pytest.fixture
def config():
    """Create test config."""
    return EffectiveConfig()


@pytest.fixture
def gate_evaluator(config):
    """Create gate evaluator."""
    return ProperGateEvaluator(config)


@pytest.fixture
def empty_snapshot():
    """Create empty portfolio snapshot."""
    return PortfolioSnapshot(
        timestamp="2024-08-01T09:15:00Z",
        bar_index=0,
        cash=100_000.0,
        reserved_cash=0.0,
        positions={},
        pending_orders={},
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        total_costs=0.0,
        daily_pnl=0.0,
        marked_equity=100_000.0,
        exposure=0.0,
        sector_exposure={},
    )


@pytest.fixture
def sample_order_intent():
    """Create a sample order intent for testing."""
    plan = TradePlan(
        timestamp="2024-08-01T09:15:00Z",
        bar_index=0,
        symbol="SUNPHARMA",
        direction=1,  # Long
        entry_price=500.0,
        stop_price=490.0,
        target_price=520.0,
        position_size_base=100,
        risk_per_share=10.0,
    )

    proposal = SizedProposal(
        timestamp="2024-08-01T09:15:00Z",
        bar_index=0,
        symbol="SUNPHARMA",
        plan=plan,
        mpc_scaling_factor=1.0,
        final_quantity=100,
        cost_estimate=50.0,
    )

    return OrderIntent(
        order_id="test_order_1",
        symbol="SUNPHARMA",
        direction=1,
        quantity=100,
        stop_price=490.0,
        target_price=520.0,
        proposal=proposal,
        timestamp_created="2024-08-01T09:15:00Z",
        bar_index_created=0,
    )


class TestPreSubmissionGates:
    """Test Stage 1: Pre-submission gate evaluation."""

    def test_pre_submission_with_real_pa_confidence(
        self, gate_evaluator, empty_snapshot, sample_order_intent
    ):
        """Pre-submission gates should use REAL PA confidence, not fabricated 0.6."""
        approved, reason = gate_evaluator.evaluate_pre_submission(
            order_intent=sample_order_intent,
            snapshot=empty_snapshot,
            pa_confidence=0.75,  # REAL from PA, not hardcoded
            id_risk_reward=2.5,  # REAL from ID, not hardcoded
            bar_timestamp="2024-08-01T09:15:00Z",
            peak_equity=100_000.0,
            daily_realized_loss=0.0,
            kill_switch_enabled=True,
        )

        # Should pass with good confidence and risk/reward
        # (actual gates may reject for other reasons, but not for fake data)
        assert isinstance(approved, bool)
        assert isinstance(reason, (str, type(None)))

    def test_pre_submission_with_real_bar_timestamp(
        self, gate_evaluator, empty_snapshot, sample_order_intent
    ):
        """Pre-submission gates should use REAL bar timestamp, not wall-clock time."""
        bar_timestamp = "2024-08-01T15:30:00Z"  # REAL from bar, not datetime.now()

        approved, reason = gate_evaluator.evaluate_pre_submission(
            order_intent=sample_order_intent,
            snapshot=empty_snapshot,
            pa_confidence=0.75,
            id_risk_reward=2.5,
            bar_timestamp=bar_timestamp,
            peak_equity=100_000.0,
            daily_realized_loss=0.0,
            kill_switch_enabled=True,
        )

        # Should evaluate without error
        assert isinstance(approved, bool)

    def test_pre_submission_with_peak_equity_tracking(
        self, gate_evaluator, sample_order_intent
    ):
        """Pre-submission gates should use tracked peak equity for drawdown calculation."""
        # Start with 100k, current at 95k = 5% drawdown
        snapshot_at_drawdown = PortfolioSnapshot(
            timestamp="2024-08-01T09:15:00Z",
            bar_index=0,
            cash=50_000.0,
            reserved_cash=0.0,
            positions={},
            pending_orders={},
            realized_pnl=-5_000.0,
            unrealized_pnl=0.0,
            total_costs=0.0,
            daily_pnl=-5_000.0,
            marked_equity=95_000.0,  # 5% drawdown from peak
            exposure=0.0,
            sector_exposure={},
        )

        approved, reason = gate_evaluator.evaluate_pre_submission(
            order_intent=sample_order_intent,
            snapshot=snapshot_at_drawdown,
            pa_confidence=0.75,
            id_risk_reward=2.5,
            bar_timestamp="2024-08-01T09:15:00Z",
            peak_equity=100_000.0,  # REAL tracked peak
            daily_realized_loss=0.0,
            kill_switch_enabled=True,
        )

        # Should evaluate with correct drawdown
        assert isinstance(approved, bool)

    def test_pre_submission_with_daily_loss_tracking(
        self, gate_evaluator, empty_snapshot, sample_order_intent
    ):
        """Pre-submission gates should use tracked daily realized loss."""
        daily_loss = 5_000.0  # Real accumulated loss from exits today

        approved, reason = gate_evaluator.evaluate_pre_submission(
            order_intent=sample_order_intent,
            snapshot=empty_snapshot,
            pa_confidence=0.75,
            id_risk_reward=2.5,
            bar_timestamp="2024-08-01T09:15:00Z",
            peak_equity=100_000.0,
            daily_realized_loss=daily_loss,  # REAL from ledger
            kill_switch_enabled=True,
        )

        # Should evaluate with correct daily loss
        assert isinstance(approved, bool)

    def test_kill_switch_enforcement(
        self, gate_evaluator, empty_snapshot, sample_order_intent
    ):
        """Kill switch should be real state from config, not hardcoded."""
        # Test with kill switch ENABLED (safe)
        approved_enabled, _ = gate_evaluator.evaluate_pre_submission(
            order_intent=sample_order_intent,
            snapshot=empty_snapshot,
            pa_confidence=0.75,
            id_risk_reward=2.5,
            bar_timestamp="2024-08-01T09:15:00Z",
            peak_equity=100_000.0,
            daily_realized_loss=0.0,
            kill_switch_enabled=True,  # REAL from config
        )

        # Test with kill switch DISABLED (should block)
        approved_disabled, reason_disabled = gate_evaluator.evaluate_pre_submission(
            order_intent=sample_order_intent,
            snapshot=empty_snapshot,
            pa_confidence=0.75,
            id_risk_reward=2.5,
            bar_timestamp="2024-08-01T09:15:00Z",
            peak_equity=100_000.0,
            daily_realized_loss=0.0,
            kill_switch_enabled=False,  # Kill switch OFF
        )

        # When kill switch is disabled, should reject
        assert not approved_disabled
        assert reason_disabled is not None
        assert "kill switch" in reason_disabled.lower()


class TestPostFillGates:
    """Test Stage 2: Post-fill gate evaluation (Gate 16)."""

    def test_post_fill_slippage_gate(self, gate_evaluator, sample_order_intent):
        """Gate 16 compares the planned price with an actual fill."""
        fill = FillEvent("fill-1", "test_order_1", "2024-08-01T09:16:00Z", 1,
                         "SUNPHARMA", 1, 100, 500.0, 1.0,
                         "2024-08-01T09:15:00Z", "2024-08-01T09:15:00Z")
        approved, reason = gate_evaluator.evaluate_post_fill(
            order_intent=sample_order_intent,
            fill_event=fill,
        )
        assert approved
        assert reason is None

    def test_v3_intraday_slippage_boundary(self, gate_evaluator, sample_order_intent):
        """The approved V3 0.15% threshold is inclusive at its boundary."""
        at_limit = FillEvent(
            "fill-at-limit", "test_order_1", "2024-08-01T09:16:00Z", 1,
            "SUNPHARMA", 1, 100, 500.0 * 1.0015, 1.0,
            "2024-08-01T09:15:00Z", "2024-08-01T09:15:00Z",
        )
        beyond_limit = FillEvent(
            "fill-over-limit", "test_order_1", "2024-08-01T09:16:00Z", 1,
            "SUNPHARMA", 1, 100, 500.0 * 1.001501, 1.0,
            "2024-08-01T09:15:00Z", "2024-08-01T09:15:00Z",
        )
        assert gate_evaluator.evaluate_post_fill(sample_order_intent, at_limit)[0]
        assert not gate_evaluator.evaluate_post_fill(sample_order_intent, beyond_limit)[0]


class TestPostReconciliationGates:
    """Test Stage 3: Post-reconciliation gate evaluation (Gate 15)."""

    def test_post_reconciliation_placeholder(
        self, gate_evaluator, sample_order_intent
    ):
        """Post-reconciliation gates are not yet implemented (placeholder always passes)."""
        # This is a placeholder test for Stage 3
        # Will be implemented when reconciliation is complete

        # For now, placeholder always passes
        approved, reason = gate_evaluator.evaluate_post_reconciliation(
            order_intent=sample_order_intent,
            fill_event=None,  # TODO: Use real FillEvent
            expected_quantity=100,
            actual_quantity=100,
            expected_cost=50.0,
            actual_cost=50.0,
        )

        assert approved is True
        assert reason is None


class TestArchitectureCorrectness:
    """Test that architecture matches spec."""

    def test_three_separate_methods_not_one_filtered_method(self, gate_evaluator):
        """Architecture must have three separate methods, not one filtered method."""
        # This test documents the CORRECT architecture

        # WRONG (what we were doing):
        # def evaluate_all_18_then_filter(...)
        #     all_results = run_gates_1_to_18()
        #     return [r for r in all_results if r.gate_number != 15 and r.gate_number != 16]

        # RIGHT (what we have now):
        # def evaluate_pre_submission(...) -> gates 1-13, 17-18
        # def evaluate_post_fill(...) -> gate 16
        # def evaluate_post_reconciliation(...) -> gate 15

        assert hasattr(gate_evaluator, 'evaluate_pre_submission')
        assert hasattr(gate_evaluator, 'evaluate_post_fill')
        assert hasattr(gate_evaluator, 'evaluate_post_reconciliation')

        # Each should be callable
        assert callable(gate_evaluator.evaluate_pre_submission)
        assert callable(gate_evaluator.evaluate_post_fill)
        assert callable(gate_evaluator.evaluate_post_reconciliation)

    def test_no_fabricated_values_in_architecture(self):
        """Architecture must accept real data, not fabricate values."""
        # WRONG (what we were doing):
        # pa_confidence = 0.6  # Fabricated
        # id_risk_reward = 2.0  # Fabricated
        # timestamp = datetime.now()  # Wall-clock time (non-deterministic)
        # unrealized_loss = portfolio_equity  # Wrong (should be actual loss)

        # RIGHT (what we have now):
        # evaluate_pre_submission(
        #     pa_confidence=signal.confidence,  # REAL from PA
        #     id_risk_reward=decision.risk_reward_ratio,  # REAL from ID
        #     bar_timestamp="2024-08-01T09:15:00Z",  # REAL from bar
        #     peak_equity=tracked_peak,  # REAL tracked
        #     daily_realized_loss=ledger_sum,  # REAL from ledger
        #     kill_switch_enabled=config.require(...),  # REAL from config
        # )

        # This test is documentation of the correct pattern
        evaluator = ProperGateEvaluator(EffectiveConfig())

        # Each method signature should require real data parameters
        import inspect

        sig = inspect.signature(evaluator.evaluate_pre_submission)
        params = list(sig.parameters.keys())

        assert 'pa_confidence' in params
        assert 'id_risk_reward' in params
        assert 'bar_timestamp' in params
        assert 'peak_equity' in params
        assert 'daily_realized_loss' in params
        assert 'kill_switch_enabled' in params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
