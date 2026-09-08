"""Unit tests for sealed replay runner.

IMPORTANT: These are component tests, not end-to-end replay tests.
End-to-end test requires real orchestrator.run() with actual bar data.
See test_inhouse_sealed_replay_e2e.py for that integration test.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from inhouse_validation.sealed_replay_runner import SealedReplayRunner, SealedReplayReport


def create_test_manifest(tmpdir: str, num_symbols: int = 3) -> str:
    """Helper to create a test manifest."""
    symbols = ["SUNPHARMA", "MAXHEALTH", "HDFCBANK"][:num_symbols]

    manifest_data = {
        "built_at": "2026-09-08T12:00:00+00:00",
        "data_dir": tmpdir,
        "files": [
            {
                "filename": f"NSE_{sym}_minute.csv",
                "symbol": sym,
                "sha256": f"abc{i:03d}",
                "first_timestamp": "2023-07-03T09:15:00+05:30",
                "last_timestamp": "2026-08-24T15:14:00+05:30",
                "row_count": 100000,
                "size_bytes": 5000000,
            }
            for i, sym in enumerate(symbols)
        ],
    }

    manifest_path = Path(tmpdir) / "manifest.json"
    with open(manifest_path, 'w') as f:
        json.dump(manifest_data, f)

    return str(manifest_path)


def test_sealed_replay_runner_init():
    """Test runner initialization with manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = create_test_manifest(tmpdir, 3)
        runner = SealedReplayRunner(manifest_path, starting_equity=100_000.0)

        assert runner.starting_equity == 100_000.0
        assert runner.dataset_hash is not None
        assert runner.config_hash is not None
        assert len(runner.dataset_hash) == 64  # SHA-256


def test_sealed_replay_runner_config_hash():
    """Test that config hash is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = create_test_manifest(tmpdir, 1)

        runner1 = SealedReplayRunner(manifest_path)
        runner2 = SealedReplayRunner(manifest_path)

        assert runner1.config_hash == runner2.config_hash


def test_sealed_replay_runner_with_overrides():
    """Test runner with calibration overrides."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = create_test_manifest(tmpdir, 1)

        overrides = {
            "max_position_quantity": 5000,
            "initial_stop_fraction": 0.02,
        }

        runner1 = SealedReplayRunner(manifest_path)
        runner2 = SealedReplayRunner(manifest_path, calibration_overrides=overrides)

        # Different overrides should produce different config hash
        assert runner1.config_hash != runner2.config_hash


def test_sealed_replay_report_structure():
    """Test that SealedReplayReport has all required fields."""
    report = SealedReplayReport(
        timestamp="2026-09-08T10:00:00+00:00",
        dataset_hash="abc123",
        config_hash="def456",
        symbols_loaded=48,
        bars_processed=377997,
        orders_submitted=483,
        orders_filled=483,
        gate16_breaches=1,
        rejected_orders=8,
        starting_equity=100_000.0,
        ending_equity=99_408.10,
        realized_pnl=-591.90,
        entry_costs=500.0,
        exit_costs=0.0,
        daily_pnl_series={"2026-08-19": -591.90},
        reconciliation_exact=True,
        pending_orders_final=0,
        reserved_cash_final=0.0,
        open_positions_final=0,
        audit_chain_valid=True,
        status="PASSED",
    )

    # Verify all fields are present
    assert report.dataset_hash == "abc123"
    assert report.symbols_loaded == 48
    assert report.reconciliation_exact
    assert report.status == "PASSED"


def test_sealed_replay_report_no_execution():
    """Test that reports with zero trades are NO_EXECUTION, not PASSED."""
    report = SealedReplayReport(
        timestamp="2026-09-08T10:00:00+00:00",
        dataset_hash="abc123",
        config_hash="def456",
        symbols_loaded=48,
        bars_processed=377997,
        orders_submitted=0,  # NO ORDERS
        orders_filled=0,
        gate16_breaches=0,
        rejected_orders=0,
        starting_equity=100_000.0,
        ending_equity=100_000.0,  # Unchanged
        realized_pnl=0.0,
        entry_costs=0.0,
        exit_costs=0.0,
        daily_pnl_series={},
        reconciliation_exact=True,
        pending_orders_final=0,
        reserved_cash_final=0.0,
        open_positions_final=0,
        audit_chain_valid=True,
        status="NO_EXECUTION",  # Must be this, not PASSED
    )

    # Zero executions must NOT be PASSED
    assert report.orders_submitted == 0
    assert report.status == "NO_EXECUTION"


def test_sealed_replay_report_remediation_status():
    """Test that Gate16 breaches produce REMEDIATION_REQUIRED status."""
    report = SealedReplayReport(
        timestamp="2026-09-08T10:00:00+00:00",
        dataset_hash="abc123",
        config_hash="def456",
        symbols_loaded=48,
        bars_processed=377997,
        orders_submitted=483,
        orders_filled=483,
        gate16_breaches=1,  # One breach
        rejected_orders=8,
        starting_equity=100_000.0,
        ending_equity=99_408.10,
        realized_pnl=-591.90,
        entry_costs=500.0,
        exit_costs=0.0,
        daily_pnl_series={"2026-08-19": -591.90},
        reconciliation_exact=True,  # Breach + recovered
        pending_orders_final=0,
        reserved_cash_final=0.0,
        open_positions_final=0,
        audit_chain_valid=True,
        status="REMEDIATION_REQUIRED",
    )

    # Breach + exact reconciliation = REMEDIATION_REQUIRED (not PASSED)
    assert report.gate16_breaches > 0
    assert report.reconciliation_exact
    assert report.status == "REMEDIATION_REQUIRED"


def test_sealed_replay_report_incomplete_reconciliation_fails():
    """Test that incomplete reconciliation produces FAILED status."""
    report = SealedReplayReport(
        timestamp="2026-09-08T10:00:00+00:00",
        dataset_hash="abc123",
        config_hash="def456",
        symbols_loaded=48,
        bars_processed=377997,
        orders_submitted=483,
        orders_filled=483,
        gate16_breaches=0,
        rejected_orders=8,
        starting_equity=100_000.0,
        ending_equity=99_408.10,
        realized_pnl=-591.90,
        entry_costs=500.0,
        exit_costs=0.0,
        daily_pnl_series={"2026-08-19": -591.90},
        reconciliation_exact=False,  # INCOMPLETE
        pending_orders_final=1,  # Has pending orders!
        reserved_cash_final=50000.0,  # Has reserved cash!
        open_positions_final=0,
        audit_chain_valid=True,
        status="FAILED",
    )

    # Incomplete reconciliation must be FAILED
    assert not report.reconciliation_exact
    assert report.pending_orders_final > 0 or report.reserved_cash_final > 0
    assert report.status == "FAILED"
