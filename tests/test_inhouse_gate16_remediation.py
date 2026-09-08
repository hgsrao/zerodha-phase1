"""Unit tests for Gate16 remediation."""

import tempfile
from pathlib import Path

import pytest

from inhouse_validation.gate16_remediation import Gate16Remediator


def test_gate16_no_breach():
    """Test that slippage within tolerance doesn't trigger breach."""
    remediator = Gate16Remediator(tolerance_pct=0.15)

    # Fill with 0.10% slippage (within 0.15% tolerance)
    is_breach = remediator.detect_breach(
        timestamp="2026-08-19T03:45:00+00:00",
        symbol="MAXHEALTH",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=100.0,
        actual_fill_price=100.10,  # 0.10% slippage
    )

    assert not is_breach
    assert len(remediator.violations) == 0
    assert not remediator.quarantine_mode


def test_gate16_first_breach_triggers_quarantine():
    """Test that first breach triggers quarantine (not shutdown)."""
    remediator = Gate16Remediator(tolerance_pct=0.15)

    # Fill with 0.21% slippage (exceeds 0.15% tolerance)
    is_breach = remediator.detect_breach(
        timestamp="2026-08-19T03:45:00+00:00",
        symbol="MAXHEALTH",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=100.0,
        actual_fill_price=100.21,  # 0.21% slippage
    )

    assert is_breach
    assert len(remediator.violations) == 1
    assert remediator.quarantine_mode
    assert not remediator.trading_halted
    assert not remediator.entry_authorization_enabled


def test_gate16_second_breach_triggers_shutdown():
    """Test that second breach triggers full shutdown."""
    remediator = Gate16Remediator(tolerance_pct=0.15)

    # First breach
    remediator.detect_breach(
        timestamp="2026-08-19T03:45:00+00:00",
        symbol="MAXHEALTH",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=100.0,
        actual_fill_price=100.21,
    )
    assert remediator.quarantine_mode
    assert not remediator.trading_halted

    # Second breach
    remediator.detect_breach(
        timestamp="2026-08-19T04:00:00+00:00",
        symbol="HDFCBANK",
        order_id="order_2",
        fill_id="fill_2",
        intended_entry_price=1500.0,
        actual_fill_price=1503.0,  # 0.20% slippage
    )

    assert remediator.quarantine_mode
    assert remediator.trading_halted
    assert not remediator.entry_authorization_enabled


def test_gate16_audit_chain_verification():
    """Test that audit chain is tamper-detectable."""
    remediator = Gate16Remediator(tolerance_pct=0.15)

    # Record breach
    remediator.detect_breach(
        timestamp="2026-08-19T03:45:00+00:00",
        symbol="MAXHEALTH",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=100.0,
        actual_fill_price=100.21,
    )

    # Record cancellation
    remediator.record_pending_order_cancellation(
        timestamp="2026-08-19T03:46:00+00:00",
        order_id="order_pending",
        symbol="SUNPHARMA",
        reserved_cash=50000.0,
    )

    # Verify chain
    assert remediator.verify_chain()

    # Tamper with audit event
    if remediator.audit_events:
        remediator.audit_events[0].payload["tampered"] = True
        assert not remediator.verify_chain()


def test_gate16_persisted_audit_chain():
    """Test that persisted audit chain can be verified."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.jsonl"
        remediator = Gate16Remediator(
            tolerance_pct=0.15,
            audit_log_path=str(log_path),
        )

        # Record breach
        remediator.detect_breach(
            timestamp="2026-08-19T03:45:00+00:00",
            symbol="MAXHEALTH",
            order_id="order_1",
            fill_id="fill_1",
            intended_entry_price=100.0,
            actual_fill_price=100.21,
        )

        # Verify persisted chain
        assert log_path.exists()
        assert remediator.verify_persisted_chain()


def test_gate16_reconciliation_success():
    """Test recording successful reconciliation."""
    remediator = Gate16Remediator(tolerance_pct=0.15)

    # Trigger breach first
    remediator.detect_breach(
        timestamp="2026-08-19T03:45:00+00:00",
        symbol="MAXHEALTH",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=100.0,
        actual_fill_price=100.21,
    )

    # Record successful reconciliation
    remediator.record_reconciliation(
        timestamp="2026-08-19T15:30:00+00:00",
        pending_orders=0,
        reserved_cash=0.0,
        open_positions=0,
        realized_pnl=-500.0,
        daily_pnl={"2026-08-19": -500.0},
        exact=True,
    )

    # Should not trigger shutdown on success
    assert not remediator.trading_halted


def test_gate16_reconciliation_failure_triggers_shutdown():
    """Test that reconciliation failure triggers shutdown."""
    remediator = Gate16Remediator(tolerance_pct=0.15)

    # Trigger breach
    remediator.detect_breach(
        timestamp="2026-08-19T03:45:00+00:00",
        symbol="MAXHEALTH",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=100.0,
        actual_fill_price=100.21,
    )

    # Record failed reconciliation (open positions remaining)
    remediator.record_reconciliation(
        timestamp="2026-08-19T15:30:00+00:00",
        pending_orders=0,
        reserved_cash=0.0,
        open_positions=1,  # STILL HAS OPEN POSITION
        realized_pnl=-500.0,
        daily_pnl={"2026-08-19": -500.0},
        exact=False,
    )

    # Should trigger shutdown
    assert remediator.trading_halted
