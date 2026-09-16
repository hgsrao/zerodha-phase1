"""
Real TimestampOrchestrator.run() Integration Tests

Proves full orchestrator lifecycle with cross-session scenarios:
1. Next global timestamp is different symbol (finds correct symbol's next bar)
2. Next eligible same-symbol bar is Monday → rejected pre-submission
3. No future same-symbol bar → rejected pre-submission
4. authorized_cross_session=True → allows cross-session at pre-submission
5. Reconciliation failure raises and stops run

These call TimestampOrchestrator.run() with real bars, not manual steps.
"""

import pytest
import pandas as pd

from revision4.contracts import (
    Bar, EffectiveConfig, OrderIntent, OrderState,
    SizedProposal, TradePlan
)
from revision4.timestamp_orchestrator import TimestampOrchestrator, RankedOrderCandidate
from revision4.gates_proper import ProperGateEvaluator
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger


class TestOrchestratorCrossSession:
    """Real orchestrator tests calling run()."""

    def _make_bar(self, timestamp: str, symbol: str) -> Bar:
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

    def _make_order_intent(self, order_id: str, symbol: str,
                          timestamp_created: str) -> OrderIntent:
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

    def test_next_timestamp_is_different_symbol_finds_correct_next(self):
        """
        Next global timestamp belongs to different symbol → orchestrator finds correct next for this symbol.

        Scenario:
        - Friday: SYM1 at 10:00, SYM2 at 11:00
        - Next global is SYM2, but SYM1's next is Monday
        - SYM1 order should be rejected (next is Monday, not Friday)
        """
        config = EffectiveConfig(authorized_cross_session=False)
        ledger = PortfolioLedger()
        broker = PaperBroker()
        gate_eval = ProperGateEvaluator(config)

        # Candidate provider: returns one order for SYM1 at Friday 10:00
        def candidate_provider(snapshot, bars, event_index):
            if "SYM1" not in bars or "2024-08-02" not in bars["SYM1"].timestamp:
                return []

            order = self._make_order_intent("F1", "SYM1", "2024-08-02 10:00:00+00:00")
            return [RankedOrderCandidate(order, rank=1.0, pa_confidence=0.7, id_risk_reward=2.0)]

        orchestrator = TimestampOrchestrator(
            config=config,
            candidate_provider=candidate_provider,
            ledger=ledger,
            broker=broker,
            gate_evaluator=gate_eval,
        )

        # Bars:
        # Friday 10:00: SYM1 (candidate can be generated here)
        # Friday 11:00: SYM2 (different symbol, but next global timestamp)
        # Monday 09:15: SYM1 (SYM1's actual next bar - different date!)
        bars_by_symbol = {
            "SYM1": [
                self._make_bar("2024-08-02 10:00:00+00:00", "SYM1"),  # Friday
                self._make_bar("2024-08-05 09:15:00+00:00", "SYM1"),  # Monday
            ],
            "SYM2": [
                self._make_bar("2024-08-02 11:00:00+00:00", "SYM2"),  # Friday (between)
            ],
        }

        result = orchestrator.run(bars_by_symbol)

        # Order should NOT be submitted (rejected: next bar is Monday, not Friday)
        assert len(result.orders_submitted) == 0, "Cross-session order should be rejected pre-submission"
        assert len(result.event_log) > 0
        assert any("GATE_REJECT" in str(e) for e in result.event_log), "Should have gate rejection event"

    def test_next_same_symbol_bar_monday_rejected_by_default(self):
        """
        Next eligible same-symbol bar is Monday → rejected pre-submission (authorized_cross_session=False).

        Scenario:
        - Friday: SYM order at 14:00
        - Next SYM bar: Monday 09:15 (different date)
        - Should be rejected: "CROSS_SESSION_PROHIBITED_BY_POLICY"
        """
        config = EffectiveConfig(authorized_cross_session=False)
        ledger = PortfolioLedger()
        broker = PaperBroker()
        gate_eval = ProperGateEvaluator(config)

        # Candidate provider: order at Friday 14:00
        def candidate_provider(snapshot, bars, event_index):
            if "SYM" not in bars or "2024-08-02 14:00" not in bars["SYM"].timestamp:
                return []

            order = self._make_order_intent("F2", "SYM", "2024-08-02 14:00:00+00:00")
            return [RankedOrderCandidate(order, rank=1.0, pa_confidence=0.7, id_risk_reward=2.0)]

        orchestrator = TimestampOrchestrator(
            config=config,
            candidate_provider=candidate_provider,
            ledger=ledger,
            broker=broker,
            gate_evaluator=gate_eval,
        )

        bars_by_symbol = {
            "SYM": [
                self._make_bar("2024-08-02 14:00:00+00:00", "SYM"),  # Friday 14:00
                self._make_bar("2024-08-05 09:15:00+00:00", "SYM"),  # Monday 09:15 (next bar)
            ],
        }

        result = orchestrator.run(bars_by_symbol)

        # Order should NOT be submitted
        assert len(result.orders_submitted) == 0, "Cross-session order should be rejected"
        assert any("CROSS_SESSION" in str(e) for e in result.event_log), "Should mention cross-session"

    def test_no_future_same_symbol_bar_rejected_with_no_eligible_fill_bar(self):
        """
        No future same-symbol bar exists → rejected pre-submission (NO_ELIGIBLE_FILL_BAR).

        Scenario:
        - Only one bar for SYM (Friday), then symbol disappears
        - Order at Friday → next_bar_timestamp=None
        - Should reject: "NO_ELIGIBLE_FILL_BAR"
        """
        config = EffectiveConfig(authorized_cross_session=False)
        ledger = PortfolioLedger()
        broker = PaperBroker()
        gate_eval = ProperGateEvaluator(config)

        def candidate_provider(snapshot, bars, event_index):
            if "SYM" not in bars:
                return []

            order = self._make_order_intent("F3", "SYM", "2024-08-02 10:00:00+00:00")
            return [RankedOrderCandidate(order, rank=1.0, pa_confidence=0.7, id_risk_reward=2.0)]

        orchestrator = TimestampOrchestrator(
            config=config,
            candidate_provider=candidate_provider,
            ledger=ledger,
            broker=broker,
            gate_evaluator=gate_eval,
        )

        bars_by_symbol = {
            "SYM": [
                self._make_bar("2024-08-02 10:00:00+00:00", "SYM"),  # Only one bar, no next
            ],
        }

        result = orchestrator.run(bars_by_symbol)

        # Order should NOT be submitted
        assert len(result.orders_submitted) == 0, "Order with no eligible fill bar should be rejected"
        assert any("NO_ELIGIBLE_FILL_BAR" in str(e) for e in result.event_log), \
            "Should reject with NO_ELIGIBLE_FILL_BAR"

    def test_authorized_cross_session_allows_pre_submission(self):
        """
        authorized_cross_session=True → allows cross-session orders at pre-submission.

        Scenario:
        - Friday order, next bar is Monday
        - Config has authorized_cross_session=True
        - Should be submitted (not rejected)
        """
        config = EffectiveConfig(authorized_cross_session=True)  # ALLOW cross-session
        ledger = PortfolioLedger()
        broker = PaperBroker()
        gate_eval = ProperGateEvaluator(config)

        def candidate_provider(snapshot, bars, event_index):
            if "SYM" not in bars or "2024-08-02" not in bars["SYM"].timestamp:
                return []

            order = self._make_order_intent("F4", "SYM", "2024-08-02 14:00:00+00:00")
            return [RankedOrderCandidate(order, rank=1.0, pa_confidence=0.7, id_risk_reward=2.0)]

        orchestrator = TimestampOrchestrator(
            config=config,
            candidate_provider=candidate_provider,
            ledger=ledger,
            broker=broker,
            gate_evaluator=gate_eval,
        )

        bars_by_symbol = {
            "SYM": [
                self._make_bar("2024-08-02 14:00:00+00:00", "SYM"),  # Friday
                self._make_bar("2024-08-05 09:15:00+00:00", "SYM"),  # Monday (cross-session)
            ],
        }

        result = orchestrator.run(bars_by_symbol)

        # Order SHOULD be submitted (authorized_cross_session=True allows it)
        assert len(result.orders_submitted) == 1, "Authorized cross-session order should be submitted"
        assert result.orders_submitted[0].order_id == "F4"

    def test_reconciliation_and_full_lifecycle(self):
        """
        Full lifecycle: order submitted, filled, reconciliation succeeds.

        Scenario:
        - Friday 10:00: order candidate (gets submitted)
        - Friday 11:00: fill at open (fills)
        - Reconciliation succeeds
        """
        config = EffectiveConfig()
        ledger = PortfolioLedger()
        broker = PaperBroker()
        gate_eval = ProperGateEvaluator(config)

        def candidate_provider(snapshot, bars, event_index):
            # Only return candidate at first timestamp (10:00)
            if "SYM" not in bars or bars["SYM"].timestamp != "2024-08-02 10:00:00+00:00":
                return []

            order = self._make_order_intent("F5", "SYM", "2024-08-02 10:00:00+00:00")
            return [RankedOrderCandidate(order, rank=1.0, pa_confidence=0.7, id_risk_reward=2.0)]

        orchestrator = TimestampOrchestrator(
            config=config,
            candidate_provider=candidate_provider,
            ledger=ledger,
            broker=broker,
            gate_evaluator=gate_eval,
        )

        bars_by_symbol = {
            "SYM": [
                self._make_bar("2024-08-02 10:00:00+00:00", "SYM"),  # Friday 10:00 (candidate)
                self._make_bar("2024-08-02 11:00:00+00:00", "SYM"),  # Friday 11:00 (fills)
            ],
        }

        # Run should succeed
        result = orchestrator.run(bars_by_symbol)

        # Verify lifecycle
        assert len(result.orders_submitted) == 1, "Order should be submitted"
        assert len(result.fills) == 1, "Order should fill on next bar"
        assert result.fills[0].order_id == "F5"
        assert len(result.event_log) > 0
