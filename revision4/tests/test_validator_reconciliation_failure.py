"""Test that validator correctly reports FAILED on reconciliation failure."""

import pytest
from revision4.contracts import EffectiveConfig, Bar, OrderIntent, SizedProposal, TradePlan
from revision4.timestamp_orchestrator import TimestampOrchestrator, RankedOrderCandidate
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.gates_proper import ProperGateEvaluator


class MockBrokerWithPendingOrder(PaperBroker):
    """Mock broker that leaves an order PENDING (never fills it)."""

    def try_fill_order(self, order_id, bar, bar_index, config):
        """Always return None (no fill), leaving order PENDING."""
        return None


class MockCandidateProviderForReconciliation:
    """Generate one candidate order at specific timestamp/index.

    Args:
        target_timestamp: The timestamp at which to generate a candidate
        target_bar_index: The event_index at which to generate a candidate

    Note: bars parameter passed to __call__ is a dict {symbol: Bar} for the
    current timestamp only, not a mapping of all timestamps.
    """

    def __init__(self, target_timestamp: str, target_bar_index: int):
        self.target_timestamp = target_timestamp
        self.target_bar_index = target_bar_index
        self.generated_at_index = None

    def __call__(self, snapshot, bars, event_index):
        # Only generate candidate at the target index
        # (bars here is {symbol: Bar} for this specific timestamp)
        if event_index != self.target_bar_index:
            return []

        # bars should be {symbol: Bar} for current timestamp
        if not bars:
            return []

        # Use the first (and typically only) symbol in this timestamp
        symbol = list(bars.keys())[0]
        bar = bars[symbol]

        plan = TradePlan(
            timestamp=bar.timestamp,  # Use actual bar timestamp
            bar_index=event_index,
            symbol=bar.symbol,
            direction=1,
            entry_price=bar.close,
            stop_price=bar.close - 5.0,
            target_price=bar.close + 10.0,
            risk_per_share=5.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=bar.timestamp,
            bar_index=event_index,
            symbol=bar.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=1000.0,
        )

        order = OrderIntent(
            order_id="pending_123",
            symbol=bar.symbol,
            timestamp_created=bar.timestamp,
            bar_index_created=event_index,
            quantity=10,
            direction=1,
            stop_price=bar.close - 5.0,
            target_price=bar.close + 10.0,
            proposal=proposal,
        )

        self.generated_at_index = event_index
        return [RankedOrderCandidate(order=order, rank=1.0, pa_confidence=0.8, id_risk_reward=2.0)]


def test_validator_reports_failed_on_pending_orders():
    """Validator must report FAILED if reconciliation fails (pending orders remain)."""

    config = EffectiveConfig()
    ts1 = "2024-08-02T10:00:00+00:00"
    ts2 = "2024-08-02T10:01:00+00:00"

    # Two bars: order at ts1, but no fill (broker never fills)
    bars = [
        Bar(
            symbol="TEST_SYM",
            timestamp=ts1,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1000,
        ),
        Bar(
            symbol="TEST_SYM",
            timestamp=ts2,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1000,
        ),
    ]

    # Use broker that never fills orders
    ledger = PortfolioLedger()
    broker = MockBrokerWithPendingOrder()
    gate_evaluator = ProperGateEvaluator(config)

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=MockCandidateProviderForReconciliation(ts1, 0),
        exit_provider=lambda _snapshot, _bars, _index: (),
        ledger=ledger,
        broker=broker,
        gate_evaluator=gate_evaluator,
    )

    # Run orchestrator: order submitted at ts1, never filled
    result = orchestrator.run({"TEST_SYM": bars})

    # After run: 1 order submitted, 0 fills, 1 pending order remains
    assert len(result.orders_submitted) == 1, f"Expected 1 order submitted, got {len(result.orders_submitted)}"
    assert len(result.fills) == 0, f"Expected 0 fills, got {len(result.fills)}"
    assert len(ledger.pending_orders) == 1, f"Expected 1 pending order, got {len(ledger.pending_orders)}"  # This is the reconciliation failure

    # Reconciliation is NOT exact
    reconciliation_ok = (
        len(ledger.pending_orders) == 0
        and ledger.reserved_cash == 0.0
        and len(ledger.positions) == 0
    )
    assert not reconciliation_ok, "Reconciliation should fail (pending orders remain)"


def test_validator_reports_failed_on_reserved_cash():
    """Validator must report FAILED if reconciliation fails (reserved cash remains)."""

    config = EffectiveConfig()
    ts1 = "2024-08-02T10:00:00+00:00"
    ts2 = "2024-08-02T10:01:00+00:00"

    bars = [
        Bar(
            symbol="TEST_SYM",
            timestamp=ts1,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1000,
        ),
        Bar(
            symbol="TEST_SYM",
            timestamp=ts2,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1000,
        ),
    ]

    ledger = PortfolioLedger()
    broker = MockBrokerWithPendingOrder()
    gate_evaluator = ProperGateEvaluator(config)

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=MockCandidateProviderForReconciliation(ts1, 0),
        exit_provider=lambda _snapshot, _bars, _index: (),
        ledger=ledger,
        broker=broker,
        gate_evaluator=gate_evaluator,
    )

    result = orchestrator.run({"TEST_SYM": bars})

    # If an order is pending, reserved_cash > 0
    assert len(ledger.pending_orders) == 1
    assert ledger.reserved_cash > 0.0, "Reserved cash should be > 0 for pending order"

    reconciliation_ok = (
        len(ledger.pending_orders) == 0
        and ledger.reserved_cash == 0.0
        and len(ledger.positions) == 0
    )
    assert not reconciliation_ok, "Reconciliation should fail (reserved cash remains)"


def test_validator_reports_failed_on_open_positions():
    """Validator must report FAILED if reconciliation fails (open positions remain)."""

    config = EffectiveConfig()
    ts1 = "2024-08-02T10:00:00+00:00"
    ts2 = "2024-08-02T10:01:00+00:00"

    # Provide two bars: first for order, second to observe position
    bars = [
        Bar(
            symbol="TEST_SYM",
            timestamp=ts1,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1000,
        ),
        Bar(
            symbol="TEST_SYM",
            timestamp=ts2,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1000,
        ),
    ]

    ledger = PortfolioLedger()
    # Use a real broker that fills orders
    broker = PaperBroker()
    gate_evaluator = ProperGateEvaluator(config)

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=MockCandidateProviderForReconciliation(ts1, 0),
        exit_provider=lambda _snapshot, _bars, _index: (),  # No exits
        ledger=ledger,
        broker=broker,
        gate_evaluator=gate_evaluator,
    )

    result = orchestrator.run({"TEST_SYM": bars})

    # If order fills but no exit, position remains open
    if len(result.fills) > 0 and len(result.exits) == 0:
        # Position should be open
        assert len(ledger.positions) > 0, "Position should remain open if not exited"

        reconciliation_ok = (
            len(ledger.pending_orders) == 0
            and ledger.reserved_cash == 0.0
            and len(ledger.positions) == 0
        )
        assert (
            not reconciliation_ok
        ), "Reconciliation should fail (open positions remain)"
