"""Step 2: Test cross-session pre-submission check integration."""

import pytest
import pandas as pd
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator
from inhouse_validation.cross_session_rejection import CrossSessionRejectionPolicy


def test_cross_session_policy_wired_in_orchestrator():
    """Verify cross-session policy can be wired into orchestrator."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Wire policy
    policy = CrossSessionRejectionPolicy(allow_cross_session=False)
    orchestrator.cross_session_policy = policy

    # Verify policy is wired
    assert orchestrator.cross_session_policy is not None
    assert isinstance(orchestrator.cross_session_policy, CrossSessionRejectionPolicy)


def test_cross_session_check_accepts_same_session_next_bar():
    """Verify same-session orders are accepted by pre-submission check."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Create bars for same session
    bars = {
        "SUNPHARMA": pd.DataFrame({
            "timestamp": ["2024-08-02T09:15:00+05:30", "2024-08-02T09:16:00+05:30"],
            "close": [450.0, 451.0],
        })
    }

    # Check pre-submission for same-session order
    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2024-08-02T09:15:00+05:30",
        current_bar_index=0,
        all_bars=bars,
    )

    assert allowed is True
    assert reason is None


def test_cross_session_check_rejects_cross_session_order():
    """Verify cross-session orders are rejected by default."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Create bars spanning Friday to Monday
    bars = {
        "SUNPHARMA": pd.DataFrame({
            "timestamp": ["2024-08-16T15:14:00+05:30", "2024-08-19T09:15:00+05:30"],
            "close": [450.0, 451.0],
        })
    }

    # Check pre-submission for Friday order (no same-day fill)
    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2024-08-16T15:14:00+05:30",  # Friday
        current_bar_index=0,
        all_bars=bars,
    )

    assert allowed is False
    assert reason == "CROSS_SESSION_REJECTED"


def test_cross_session_check_rejects_no_eligible_bar():
    """Verify orders with no future bar are rejected."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Only one bar (end of month, no next bar)
    bars = {
        "SUNPHARMA": pd.DataFrame({
            "timestamp": ["2024-08-29T15:14:00+05:30"],
            "close": [450.0],
        })
    }

    # Check pre-submission for last bar
    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2024-08-29T15:14:00+05:30",
        current_bar_index=0,
        all_bars=bars,
    )

    assert allowed is False
    assert reason == "NO_ELIGIBLE_FILL_BAR"


def test_cross_session_policy_with_authorization():
    """Verify cross-session orders are allowed when explicitly authorized."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=True)

    # Bars spanning Friday to Monday
    bars = {
        "SUNPHARMA": pd.DataFrame({
            "timestamp": ["2024-08-16T15:14:00+05:30", "2024-08-19T09:15:00+05:30"],
            "close": [450.0, 451.0],
        })
    }

    # Check pre-submission with authorization enabled
    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2024-08-16T15:14:00+05:30",  # Friday
        current_bar_index=0,
        all_bars=bars,
    )

    assert allowed is True
    assert reason is None


def test_orchestrator_emits_order_rejected_on_cross_session():
    """Verify orchestrator emits ORDER_REJECTED event for cross-session rejection."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Wire policy
    orchestrator.cross_session_policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Simulate order rejection event
    orchestrator._emit_event(
        "ORDER_REJECTED", "2024-08-16T15:14:00+05:30",
        symbol="SUNPHARMA",
        order_id="order_1",
        reason="CROSS_SESSION_REJECTED",
    )

    # Verify event was recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "ORDER_REJECTED"]
    assert len(events) == 1
    assert events[0]["payload"]["reason"] == "CROSS_SESSION_REJECTED"
    assert events[0]["payload"]["symbol"] == "SUNPHARMA"


def test_orchestrator_blocks_order_with_no_eligible_bar():
    """Verify orchestrator rejects orders with no eligible fill bar."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Wire policy
    orchestrator.cross_session_policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Simulate order rejection for NO_ELIGIBLE_FILL_BAR
    orchestrator._emit_event(
        "ORDER_REJECTED", "2024-08-29T15:14:00+05:30",
        symbol="SUNPHARMA",
        order_id=None,
        reason="NO_ELIGIBLE_FILL_BAR",
    )

    # Verify event was recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "ORDER_REJECTED"]
    assert len(events) == 1
    assert events[0]["payload"]["reason"] == "NO_ELIGIBLE_FILL_BAR"


def test_step2_complete_lifecycle():
    """Integration test: Full Step 2 cross-session pre-submission lifecycle."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH"],
        starting_equity=100_000.0
    )

    # Wire both policies
    orchestrator.cross_session_policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Test Case 1: Same-session order (should be allowed)
    test1_allowed, test1_reason = orchestrator.cross_session_policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2024-08-02T09:15:00+05:30",
        current_bar_index=0,
        all_bars={"SUNPHARMA": pd.DataFrame({
            "timestamp": ["2024-08-02T09:15:00+05:30", "2024-08-02T09:16:00+05:30"],
        })},
    )
    assert test1_allowed is True

    # Record acceptance
    orchestrator._emit_event("ORDER_SUBMITTED", "2024-08-02T09:15:00+05:30", symbol="SUNPHARMA")

    # Test Case 2: Cross-session order (should be rejected)
    test2_allowed, test2_reason = orchestrator.cross_session_policy.check_pre_submission(
        symbol="MAXHEALTH",
        decision_timestamp="2024-08-16T15:14:00+05:30",  # Friday
        current_bar_index=0,
        all_bars={"MAXHEALTH": pd.DataFrame({
            "timestamp": ["2024-08-16T15:14:00+05:30", "2024-08-19T09:15:00+05:30"],
        })},
    )
    assert test2_allowed is False
    assert test2_reason == "CROSS_SESSION_REJECTED"

    # Record rejection
    orchestrator._emit_event(
        "ORDER_REJECTED", "2024-08-16T15:14:00+05:30",
        symbol="MAXHEALTH",
        reason=test2_reason,
    )

    # Verify event sequence
    events = [e for e in orchestrator.event_ledger if e["event_type"] in ("ORDER_SUBMITTED", "ORDER_REJECTED")]
    assert len(events) == 2
    assert events[0]["event_type"] == "ORDER_SUBMITTED"
    assert events[1]["event_type"] == "ORDER_REJECTED"
    assert events[1]["payload"]["reason"] == "CROSS_SESSION_REJECTED"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
