"""Unit tests for cross-session rejection policy."""

import pandas as pd
import pytest

from inhouse_validation.cross_session_rejection import CrossSessionRejectionPolicy


def test_cross_session_same_date_allowed():
    """Test that same-date orders are allowed."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Create mock bars
    bars = {
        "SUNPHARMA": [
            pd.Series({"timestamp": "2026-08-19T09:15:00+05:30", "close": 100.0}),
            pd.Series({"timestamp": "2026-08-19T09:16:00+05:30", "close": 101.0}),
        ]
    }

    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2026-08-19T09:15:00+05:30",
        current_bar_index=0,
        all_bars=bars,
    )

    assert allowed
    assert reason is None


def test_cross_session_different_date_rejected():
    """Test that cross-date orders are rejected by default."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Create mock bars: Friday and Monday
    bars = {
        "SUNPHARMA": [
            pd.Series({"timestamp": "2026-08-16T15:14:00+05:30", "close": 100.0}),  # Friday
            pd.Series({"timestamp": "2026-08-19T09:15:00+05:30", "close": 101.0}),  # Monday
        ]
    }

    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2026-08-16T15:14:00+05:30",  # Friday
        current_bar_index=0,
        all_bars=bars,
    )

    assert not allowed
    assert reason == "CROSS_SESSION_REJECTED"


def test_cross_session_authorized_allowed():
    """Test that authorized cross-session orders are allowed."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=True)

    # Friday -> Monday bars
    bars = {
        "SUNPHARMA": [
            pd.Series({"timestamp": "2026-08-16T15:14:00+05:30", "close": 100.0}),
            pd.Series({"timestamp": "2026-08-19T09:15:00+05:30", "close": 101.0}),
        ]
    }

    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2026-08-16T15:14:00+05:30",
        current_bar_index=0,
        all_bars=bars,
    )

    assert allowed
    assert reason is None


def test_cross_session_no_future_bar_rejected():
    """Test that orders with no future bar are rejected."""
    policy = CrossSessionRejectionPolicy(allow_cross_session=False)

    # Only one bar (last bar of month)
    bars = {
        "SUNPHARMA": [
            pd.Series({"timestamp": "2026-08-29T15:14:00+05:30", "close": 100.0}),
        ]
    }

    allowed, reason = policy.check_pre_submission(
        symbol="SUNPHARMA",
        decision_timestamp="2026-08-29T15:14:00+05:30",
        current_bar_index=0,
        all_bars=bars,
    )

    assert not allowed
    assert reason == "NO_ELIGIBLE_FILL_BAR"


def test_fill_time_check_same_date():
    """Test fill-time validation for same-date fills."""
    policy = CrossSessionRejectionPolicy()

    allowed, reason = policy.check_fill_time(
        order_decision_date="2026-08-19",
        fill_timestamp="2026-08-19T09:16:00+05:30",
        cross_session_authorized=False,
    )

    assert allowed
    assert reason is None


def test_fill_time_check_cross_date_rejected():
    """Test fill-time validation rejects cross-date fills."""
    policy = CrossSessionRejectionPolicy()

    allowed, reason = policy.check_fill_time(
        order_decision_date="2026-08-16",  # Friday
        fill_timestamp="2026-08-19T09:15:00+05:30",  # Monday
        cross_session_authorized=False,
    )

    assert not allowed
    assert reason == "CROSS_SESSION_FILL_REJECTED"


def test_fill_time_check_cross_date_authorized():
    """Test fill-time validation allows authorized cross-session fills."""
    policy = CrossSessionRejectionPolicy()

    allowed, reason = policy.check_fill_time(
        order_decision_date="2026-08-16",
        fill_timestamp="2026-08-19T09:15:00+05:30",
        cross_session_authorized=True,
    )

    assert allowed
    assert reason is None


def test_reject_pending_order():
    """Test rejection of a pending order releases cash."""
    policy = CrossSessionRejectionPolicy()

    result = policy.reject_pending_order(
        order_id="order_1",
        symbol="SUNPHARMA",
        reserved_cash=50000.0,
    )

    assert result["order_id"] == "order_1"
    assert result["status"] == "CANCELLED"
    assert result["reason"] == "CROSS_SESSION_REJECTED"
    assert result["reserved_cash_released"] == 50000.0
