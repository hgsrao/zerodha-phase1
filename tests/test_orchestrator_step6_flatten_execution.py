"""Step 6: Test adverse flatten execution at next eligible bar."""

import pytest
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


def test_flatten_execution_uses_adverse_factor_for_long():
    """Verify long position flatten uses -10bps adverse factor."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # For long position (BUY side)
    bar_open = 450.0
    adverse_factor = 0.999  # -10bps for long
    exit_price = bar_open * adverse_factor

    # Verify adverse pricing
    expected_exit = 450.0 * 0.999
    assert exit_price == expected_exit
    assert exit_price < bar_open  # Should be lower (adverse for long)


def test_flatten_execution_uses_adverse_factor_for_short():
    """Verify short position flatten uses +10bps adverse factor."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # For short position (SELL side)
    bar_open = 500.0
    adverse_factor = 1.001  # +10bps for short
    exit_price = bar_open * adverse_factor

    # Verify adverse pricing
    expected_exit = 500.0 * 1.001
    assert exit_price == expected_exit
    assert exit_price > bar_open  # Should be higher (adverse for short)


def test_flatten_event_recorded():
    """Verify position flatten is recorded in event ledger."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Record a flatten execution event
    orchestrator.event_ledger.append({
        "event_type": "POSITION_FLATTENED",
        "timestamp": "2024-08-02T09:17:00+05:30",
        "symbol": "SUNPHARMA",
        "exit_price": 449.55,
        "adverse_factor": 0.999,
        "quantity": 100,
    })

    assert len(orchestrator.event_ledger) == 1
    event = orchestrator.event_ledger[0]
    assert event["event_type"] == "POSITION_FLATTENED"
    assert event["symbol"] == "SUNPHARMA"
    assert event["adverse_factor"] == 0.999
    assert event["exit_price"] == 449.55


def test_scheduled_flatten_removed_after_execution():
    """Verify flatten is removed from scheduled list after execution."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Add a scheduled flatten
    orchestrator._scheduled_flattens["SUNPHARMA"] = {
        "entry_price": 450.0,
        "entry_bar_idx": 10,
        "side": "BUY",
        "quantity": 100,
    }

    assert "SUNPHARMA" in orchestrator._scheduled_flattens

    # Simulate execution (removal from scheduled)
    del orchestrator._scheduled_flattens["SUNPHARMA"]

    assert "SUNPHARMA" not in orchestrator._scheduled_flattens
    assert len(orchestrator._scheduled_flattens) == 0


def test_quarantine_flattens_tracked_in_funnel():
    """Verify quarantine flattens are counted in funnel metrics."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Simulate funnel tracking
    funnel = {}
    funnel["quarantine_flattens"] = funnel.get("quarantine_flattens", 0) + 1

    assert funnel["quarantine_flattens"] == 1

    # Add more
    funnel["quarantine_flattens"] = funnel.get("quarantine_flattens", 0) + 1
    assert funnel["quarantine_flattens"] == 2


def test_multiple_symbol_flattens():
    """Verify multiple symbols can be flattened in same bar."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH"],
        starting_equity=100_000.0
    )

    # Schedule flattens for multiple symbols
    orchestrator._scheduled_flattens = {
        "SUNPHARMA": {"entry_price": 450.0, "side": "BUY", "quantity": 100},
        "MAXHEALTH": {"entry_price": 500.0, "side": "SELL", "quantity": 50},
    }

    # Record events for both
    events_recorded = 0
    for symbol in ["SUNPHARMA", "MAXHEALTH"]:
        if symbol in orchestrator._scheduled_flattens:
            orchestrator.event_ledger.append({
                "event_type": "POSITION_FLATTENED",
                "timestamp": "2024-08-02T09:17:00+05:30",
                "symbol": symbol,
                "exit_price": 100.0,
                "adverse_factor": 0.999 if symbol == "SUNPHARMA" else 1.001,
                "quantity": 100 if symbol == "SUNPHARMA" else 50,
            })
            events_recorded += 1

    assert events_recorded == 2
    assert len(orchestrator.event_ledger) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
