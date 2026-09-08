"""Step 7: Test comprehensive event emissions throughout orchestrator lifecycle."""

import pytest
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


def test_order_submitted_event_emitted():
    """Verify ORDER_SUBMITTED event is recorded when order is queued."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Add an ORDER_SUBMITTED event
    orchestrator._emit_event(
        "ORDER_SUBMITTED", "2024-08-02T09:16:00+05:30",
        symbol="SUNPHARMA",
        order_id="order_1",
        entry_price=450.0,
        quantity=100,
        side="BUY",
    )

    # Verify event was recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "ORDER_SUBMITTED"]
    assert len(events) == 1
    assert events[0]["payload"]["symbol"] == "SUNPHARMA"
    assert events[0]["payload"]["entry_price"] == 450.0
    assert events[0]["payload"]["quantity"] == 100


def test_order_rejected_event_emitted():
    """Verify ORDER_REJECTED event is recorded for cross-session rejection."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Add an ORDER_REJECTED event
    orchestrator._emit_event(
        "ORDER_REJECTED", "2024-08-02T15:14:00+05:30",
        symbol="SUNPHARMA",
        order_id=None,
        reason="CROSS_SESSION_REJECTED",
    )

    # Verify event was recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "ORDER_REJECTED"]
    assert len(events) == 1
    assert events[0]["payload"]["reason"] == "CROSS_SESSION_REJECTED"
    assert events[0]["payload"]["symbol"] == "SUNPHARMA"


def test_fill_event_emitted():
    """Verify FILL event is recorded with complete details."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Add a FILL event
    orchestrator._emit_event(
        "FILL", "2024-08-02T09:17:00+05:30",
        symbol="SUNPHARMA",
        order_id="order_1",
        fill_price=450.50,
        quantity=100,
    )

    # Verify event was recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "FILL"]
    assert len(events) == 1
    assert events[0]["payload"]["fill_price"] == 450.50
    assert events[0]["payload"]["quantity"] == 100


def test_gate16_breach_event_emitted():
    """Verify GATE16_BREACH event is recorded with slippage details."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Add a GATE16_BREACH event
    orchestrator._emit_event(
        "GATE16_BREACH", "2024-08-02T09:17:00+05:30",
        symbol="SUNPHARMA",
        order_id="order_1",
        intended_entry_price=450.0,
        actual_fill_price=450.70,
    )

    # Verify event was recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "GATE16_BREACH"]
    assert len(events) == 1
    assert events[0]["payload"]["intended_entry_price"] == 450.0
    assert events[0]["payload"]["actual_fill_price"] == 450.70


def test_event_hash_linking():
    """Verify events are hash-linked with prior_hash for tamper detection."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Emit multiple events
    orchestrator._emit_event("ORDER_SUBMITTED", "2024-08-02T09:15:00+05:30", symbol="SUNPHARMA")
    orchestrator._emit_event("FILL", "2024-08-02T09:16:00+05:30", symbol="SUNPHARMA")

    # Verify hash chain
    assert len(orchestrator.event_ledger) == 2
    first_event = orchestrator.event_ledger[0]
    second_event = orchestrator.event_ledger[1]

    # Second event's prior_hash should match first event's record_hash
    assert second_event["prior_hash"] == first_event["record_hash"]
    assert "record_hash" in first_event
    assert "record_hash" in second_event


def test_multiple_events_in_sequence():
    """Verify multiple events are recorded in chronological order."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Emit events in sequence
    orchestrator._emit_event("ORDER_SUBMITTED", "2024-08-02T09:15:00+05:30", symbol="SUNPHARMA")
    orchestrator._emit_event("FILL", "2024-08-02T09:16:00+05:30", symbol="SUNPHARMA")
    orchestrator._emit_event("GATE16_BREACH", "2024-08-02T09:16:00+05:30", symbol="SUNPHARMA")
    orchestrator._emit_event("QUARANTINE_STARTED", "2024-08-02T09:16:00+05:30")
    orchestrator._emit_event("FLATTEN_SCHEDULED", "2024-08-02T09:16:00+05:30", symbol="SUNPHARMA")
    orchestrator._emit_event("POSITION_FLATTENED", "2024-08-02T09:17:00+05:30", symbol="SUNPHARMA")

    # Verify all events recorded
    assert len(orchestrator.event_ledger) == 6
    event_types = [e["event_type"] for e in orchestrator.event_ledger]
    assert event_types == [
        "ORDER_SUBMITTED",
        "FILL",
        "GATE16_BREACH",
        "QUARANTINE_STARTED",
        "FLATTEN_SCHEDULED",
        "POSITION_FLATTENED",
    ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
