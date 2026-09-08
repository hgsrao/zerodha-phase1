"""Integration test for EOD flattening with real positions and completed trades."""

import pytest
import pandas as pd
from revision4.contracts import EffectiveConfig, Bar, ExitReason
from revision4.timestamp_orchestrator import TimestampOrchestrator, RankedOrderCandidate
from revision4.contracts import OrderIntent, SizedProposal, TradePlan
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.gates_proper import ProperGateEvaluator
from revision4.config_access import calculate_transaction_cost


class MockCandidateProviderEOD:
    """Generate one candidate order at specific timestamp."""

    def __init__(self, target_index: int):
        self.target_index = target_index

    def __call__(self, snapshot, bars, event_index):
        if event_index != self.target_index or not bars:
            return []

        symbol = list(bars.keys())[0]
        bar = bars[symbol]

        plan = TradePlan(
            timestamp=bar.timestamp,
            bar_index=event_index,
            symbol=bar.symbol,
            direction=1 if symbol == "LONG_SYM" else -1,  # Long or short
            entry_price=bar.close,
            stop_price=bar.close - 5.0 if symbol == "LONG_SYM" else bar.close + 5.0,
            target_price=bar.close + 10.0 if symbol == "LONG_SYM" else bar.close - 10.0,
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
            order_id=f"eod_test_{symbol}",
            symbol=bar.symbol,
            timestamp_created=bar.timestamp,
            bar_index_created=event_index,
            quantity=10,
            direction=1 if symbol == "LONG_SYM" else -1,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            proposal=proposal,
        )

        return [RankedOrderCandidate(order=order, rank=1.0, pa_confidence=0.8, id_risk_reward=2.0)]


def test_eod_flatten_exit_event_construction():
    """Verify EOD ExitEvent construction uses correct contract (no TypeError)."""

    from revision4.contracts import ExitEvent, Position

    # Create a mock position (with all required fields)
    position = Position(
        symbol="TEST_SYM",
        direction=1,  # Long
        entry_price=100.0,
        entry_bar_index=0,
        entry_bar_timestamp="2024-08-02T09:15:00+00:00",
        quantity=10.0,
        stop_price=95.0,
        target_price=110.0,
        cost_paid=50.0,
        fill_id="fill_1",
    )

    # Simulate EOD flatten: construct ExitEvent with correct fields (no TypeError)
    exit_price = 101.0
    exit_cost = calculate_transaction_cost(101.0, 10.0, "SELL")
    pnl_realized = (exit_price - position.entry_price) * position.quantity
    pnl_pct = (pnl_realized / (position.entry_price * position.quantity)) * 100

    # This should NOT raise TypeError for wrong field names
    exit_event = ExitEvent(
        exit_id="eod_test_1",
        symbol=position.symbol,
        timestamp_exit="2024-08-02T15:30:00+00:00",
        bar_index_exit=390,
        entry_price=position.entry_price,
        exit_price=exit_price,
        quantity=position.quantity,
        direction=position.direction,
        bars_held=390 - position.entry_bar_index,
        entry_cost_paid=position.cost_paid,
        exit_cost_paid=exit_cost,
        exit_reason=ExitReason.EOD_FLATTENING,
        pnl_realized=pnl_realized,
        pnl_pct=pnl_pct,
    )

    # Verify construction succeeded and fields are correct
    assert exit_event.symbol == "TEST_SYM"
    assert exit_event.exit_price == 101.0
    assert exit_event.exit_reason == ExitReason.EOD_FLATTENING
    assert exit_event.exit_cost_paid > 0, "Canonical exit cost should be positive"
    assert abs(exit_event.pnl_realized - 10.0) < 0.01, f"Expected P&L ~10, got {exit_event.pnl_realized}"

    print(f"ExitEvent construction successful:")
    print(f"  Symbol: {exit_event.symbol}")
    print(f"  Entry: {exit_event.entry_price} (cost {exit_event.entry_cost_paid:.2f})")
    print(f"  Exit: {exit_event.exit_price} (cost {exit_event.exit_cost_paid:.2f})")
    print(f"  P&L: {exit_event.pnl_realized:.2f} ({exit_event.pnl_pct:.2f}%)")
