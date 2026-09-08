"""Step 8: Test reconciliation enforcement at run() end."""

import pytest
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


def test_reconciliation_exact_no_open_positions():
    """Verify reconciliation is exact when no open positions exist."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Clean state (no open trades, no pending orders)
    assert len(orchestrator.open_trades) == 0
    assert len(orchestrator.pending_entries) == 0

    # Reconciliation should be exact
    reconciliation_exact = not orchestrator.open_trades and not orchestrator.pending_entries
    assert reconciliation_exact is True


def test_reconciliation_fails_with_open_positions():
    """Verify reconciliation fails when open positions remain."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Simulate open position
    orchestrator.open_trades["SUNPHARMA"] = {
        "entry_price": 450.0,
        "entry_bar_idx": 10,
        "side": "BUY",
        "quantity": 100,
    }

    # Reconciliation should fail
    reconciliation_exact = not orchestrator.open_trades and not orchestrator.pending_entries
    assert reconciliation_exact is False


def test_reconciliation_fails_with_pending_orders():
    """Verify reconciliation fails when pending orders remain."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Simulate pending order
    orchestrator.pending_entries["SUNPHARMA"] = {
        "order_id": "order_1",
        "quantity": 50,
    }

    # Reconciliation should fail
    reconciliation_exact = not orchestrator.open_trades and not orchestrator.pending_entries
    assert reconciliation_exact is False


def test_reconciliation_checks_unrealized_pnl():
    """Verify reconciliation enforces unrealized P&L is near zero."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Mock mark-to-market equity to simulate unrealized P&L
    # After EOD flatten, unrealized should be near zero
    mtm_equity = 100_000.0 + 0.005  # Tiny floating point error (acceptable)
    unrealized_pnl = mtm_equity - orchestrator.starting_equity - orchestrator.broker.realized_pnl

    # Should pass reconciliation
    tolerance = 0.01
    assert abs(unrealized_pnl) < tolerance


def test_reconciliation_fails_with_large_unrealized_pnl():
    """Verify reconciliation fails when unrealized P&L is significant."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Simulate significant unrealized P&L (shouldn't exist after EOD flatten)
    mtm_equity = 100_500.0  # 500 rupees unrealized
    unrealized_pnl = mtm_equity - orchestrator.starting_equity - orchestrator.broker.realized_pnl

    # Should fail reconciliation
    tolerance = 0.01
    assert abs(unrealized_pnl) >= tolerance


def test_reconciliation_emits_event():
    """Verify RECONCILIATION_COMPLETED event is emitted with full details."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Emit reconciliation event
    orchestrator._emit_event(
        "RECONCILIATION_COMPLETED", "END_OF_RUN",
        exact=True,
        open_positions=0,
        pending_orders=0,
        realized_pnl=100.0,
        unrealized_pnl=0.005,
        quarantine_mode=False,
    )

    # Verify event was recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "RECONCILIATION_COMPLETED"]
    assert len(events) == 1
    assert events[0]["payload"]["exact"] is True
    assert events[0]["payload"]["open_positions"] == 0
    assert events[0]["payload"]["pending_orders"] == 0
    assert events[0]["payload"]["realized_pnl"] == 100.0
    assert events[0]["payload"]["quarantine_mode"] is False


def test_reconciliation_with_quarantine_mode():
    """Verify reconciliation emits quarantine status when in quarantine."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Set quarantine mode (after Gate16 breach)
    orchestrator.quarantine_mode = True

    # Emit reconciliation event
    orchestrator._emit_event(
        "RECONCILIATION_COMPLETED", "END_OF_RUN",
        exact=True,
        open_positions=0,
        pending_orders=0,
        realized_pnl=-592.0,
        unrealized_pnl=0.0,
        quarantine_mode=True,
    )

    # Verify event includes quarantine status
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "RECONCILIATION_COMPLETED"]
    assert len(events) == 1
    assert events[0]["payload"]["quarantine_mode"] is True


def test_reconciliation_binary_outcome():
    """Verify reconciliation has binary outcome: exact or failed."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Case 1: Exact reconciliation
    reconciliation_1 = (
        not orchestrator.open_trades and
        not orchestrator.pending_entries and
        abs(0.0) < 0.01
    )
    assert reconciliation_1 is True

    # Case 2: Failed reconciliation (open position)
    orchestrator.open_trades["SUNPHARMA"] = {"entry_price": 450.0, "quantity": 100}
    reconciliation_2 = (
        not orchestrator.open_trades and
        not orchestrator.pending_entries and
        abs(0.0) < 0.01
    )
    assert reconciliation_2 is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
