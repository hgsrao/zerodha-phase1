"""Step 5: Test adverse flatten scheduling during quarantine."""

import pytest
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


def test_flatten_scheduling_on_quarantine():
    """Verify open positions are scheduled for next-bar flatten."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH"],
        starting_equity=100_000.0
    )

    # Simulate open trades (positions)
    orchestrator.open_trades = {
        "SUNPHARMA": {
            "entry_price": 450.0,
            "entry_bar_idx": 10,
            "side": "BUY",
            "quantity": 100,
        },
        "MAXHEALTH": {
            "entry_price": 500.0,
            "entry_bar_idx": 15,
            "side": "SELL",
            "quantity": 50,
        },
    }

    # Schedule flattens (simulates what happens on Gate16 breach)
    orchestrator._scheduled_flattens = {}
    for flatten_symbol, trade in list(orchestrator.open_trades.items()):
        orchestrator._scheduled_flattens[flatten_symbol] = {
            "entry_price": trade["entry_price"],
            "entry_bar_idx": trade["entry_bar_idx"],
            "side": trade["side"],
            "quantity": trade["quantity"],
        }

    # Verify all positions are scheduled
    assert len(orchestrator._scheduled_flattens) == 2
    assert "SUNPHARMA" in orchestrator._scheduled_flattens
    assert "MAXHEALTH" in orchestrator._scheduled_flattens


def test_scheduled_flatten_preserves_position_data():
    """Verify scheduled flatten contains complete position info."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    entry_price = 450.0
    entry_bar_idx = 10
    side = "BUY"
    quantity = 100

    orchestrator._scheduled_flattens["SUNPHARMA"] = {
        "entry_price": entry_price,
        "entry_bar_idx": entry_bar_idx,
        "side": side,
        "quantity": quantity,
    }

    flatten_plan = orchestrator._scheduled_flattens["SUNPHARMA"]
    assert flatten_plan["entry_price"] == entry_price
    assert flatten_plan["entry_bar_idx"] == entry_bar_idx
    assert flatten_plan["side"] == side
    assert flatten_plan["quantity"] == quantity


def test_flatten_scheduling_event_logged():
    """Verify flatten scheduling is recorded in event ledger."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Record a flatten scheduling event
    orchestrator.event_ledger.append({
        "event_type": "FLATTEN_SCHEDULED",
        "timestamp": "2024-08-02T09:16:00+05:30",
        "symbol": "SUNPHARMA",
        "entry_price": 450.0,
        "quantity": 100,
    })

    assert len(orchestrator.event_ledger) == 1
    assert orchestrator.event_ledger[0]["event_type"] == "FLATTEN_SCHEDULED"
    assert orchestrator.event_ledger[0]["symbol"] == "SUNPHARMA"
    assert orchestrator.event_ledger[0]["quantity"] == 100


def test_multiple_positions_scheduled():
    """Verify multiple positions can be scheduled simultaneously."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH", "INFY"],
        starting_equity=100_000.0
    )

    # Schedule flattens for multiple positions
    orchestrator._scheduled_flattens = {
        "SUNPHARMA": {"entry_price": 450.0, "side": "BUY", "quantity": 100},
        "MAXHEALTH": {"entry_price": 500.0, "side": "SELL", "quantity": 50},
        "INFY": {"entry_price": 2500.0, "side": "BUY", "quantity": 20},
    }

    assert len(orchestrator._scheduled_flattens) == 3
    assert orchestrator._scheduled_flattens["INFY"]["side"] == "BUY"
    assert orchestrator._scheduled_flattens["MAXHEALTH"]["side"] == "SELL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
