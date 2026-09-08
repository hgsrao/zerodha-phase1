"""Step 1: Test lifecycle dependencies initialization."""

import pytest
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


def test_lifecycle_dependencies_initialized():
    """Verify safety control lifecycle dependencies are initialized."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH"],
        starting_equity=100_000.0
    )

    # Check all lifecycle attributes exist
    assert hasattr(orchestrator, "cross_session_policy")
    assert hasattr(orchestrator, "gate16_remediator")
    assert hasattr(orchestrator, "event_ledger")
    assert hasattr(orchestrator, "quarantine_mode")
    assert hasattr(orchestrator, "trading_halted")
    assert hasattr(orchestrator, "_scheduled_flattens")

    # Check initial values
    assert orchestrator.cross_session_policy is None  # Not wired yet
    assert orchestrator.gate16_remediator is None     # Not wired yet
    assert orchestrator.event_ledger == []            # Empty at start
    assert orchestrator.quarantine_mode is False      # Not in quarantine
    assert orchestrator.trading_halted is False       # Not halted
    assert orchestrator._scheduled_flattens == {}     # No flattens scheduled


def test_event_ledger_is_list():
    """Verify event_ledger is a list for append-only behavior."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Should be empty and appendable
    assert isinstance(orchestrator.event_ledger, list)
    assert len(orchestrator.event_ledger) == 0

    # Should support append
    orchestrator.event_ledger.append({"event_type": "TEST", "timestamp": "2026-09-08T10:00:00"})
    assert len(orchestrator.event_ledger) == 1


def test_quarantine_mode_flag():
    """Verify quarantine_mode flag works correctly."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    assert orchestrator.quarantine_mode is False
    orchestrator.quarantine_mode = True
    assert orchestrator.quarantine_mode is True


def test_scheduled_flattens_dict():
    """Verify _scheduled_flattens tracks positions for next-bar flatten."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Should be empty initially
    assert isinstance(orchestrator._scheduled_flattens, dict)
    assert len(orchestrator._scheduled_flattens) == 0

    # Should support assignment
    orchestrator._scheduled_flattens["SUNPHARMA"] = {
        "entry_price": 100.0,
        "entry_bar_idx": 0,
        "side": "BUY",
        "quantity": 100,
    }
    assert "SUNPHARMA" in orchestrator._scheduled_flattens


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
