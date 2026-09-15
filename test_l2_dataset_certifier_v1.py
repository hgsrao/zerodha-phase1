"""TEST SUITE — L2 Dataset Certifier V1

Comprehensive adversarial testing with synthetic fixtures.
All tests use temporary copies or in-memory data.
Raw source files are NEVER modified.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from l2_dataset_certifier_v1 import (
    AUTHORITATIVE_UNIVERSE,
    FROZEN_INTERVAL_SECONDS,
    SESSION_END_IST,
    SESSION_START_IST,
    CertificationResult,
    DepthLevel,
    SessionAnomalies,
    Snapshot,
    certify_session,
    parse_snapshot,
)


class TestParseSnapshot:
    """Test individual snapshot parsing."""

    def test_valid_snapshot(self):
        """Valid snapshot with full fields."""
        raw = {
            "symbol": "RELIANCE",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "last_price": 2750.50,
            "volume": 1000000,
            "buy_quantity": 500000,
            "sell_quantity": 450000,
            "depth": {
                "buy": [
                    {"price": 2750.25, "quantity": 100, "orders": 5},
                    {"price": 2750.00, "quantity": 200, "orders": 8},
                ],
                "sell": [
                    {"price": 2750.75, "quantity": 150, "orders": 6},
                    {"price": 2751.00, "quantity": 250, "orders": 10},
                ],
            },
        }
        snapshot = parse_snapshot("RELIANCE", raw)
        assert snapshot.valid is True
        assert snapshot.last_price == 2750.50
        assert len(snapshot.depth_buy) == 2
        assert snapshot.best_bid() == 2750.25
        assert snapshot.best_ask() == 2750.75

    def test_malformed_not_mapping(self):
        """Non-dict raw record."""
        snapshot = parse_snapshot("INFY", "not a dict")
        assert snapshot.valid is False
        assert snapshot.reason == "NOT_MAPPING"

    def test_missing_timestamp(self):
        """No exchange timestamp."""
        raw = {
            "symbol": "INFY",
            "depth": {
                "buy": [{"price": 100, "quantity": 10}],
                "sell": [{"price": 101, "quantity": 10}],
            },
        }
        snapshot = parse_snapshot("INFY", raw)
        assert snapshot.valid is False
        assert snapshot.timestamp_utc is None

    def test_missing_depth(self):
        """No depth information."""
        raw = {
            "symbol": "TCS",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "last_price": 4000,
        }
        snapshot = parse_snapshot("TCS", raw)
        assert snapshot.valid is False

    def test_sparse_depth_is_valid(self):
        """Fewer than 5 populated levels is NOT invalid."""
        raw = {
            "symbol": "HDFCBANK",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "last_price": 1500,
            "depth": {
                "buy": [
                    {"price": 1499.50, "quantity": 100, "orders": 2},
                    {"price": 1499.00, "quantity": 200, "orders": 5},
                ],
                "sell": [
                    {"price": 1500.50, "quantity": 150, "orders": 3},
                ],
            },
        }
        snapshot = parse_snapshot("HDFCBANK", raw)
        assert snapshot.valid is True
        assert len(snapshot.depth_buy) == 2
        assert len(snapshot.depth_sell) == 1

    def test_crossed_book_detection(self):
        """Crossed book (bid >= ask) is detected but not rejected."""
        raw = {
            "symbol": "INFY",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "depth": {
                "buy": [{"price": 3500.00, "quantity": 100}],
                "sell": [{"price": 3499.50, "quantity": 150}],
            },
        }
        snapshot = parse_snapshot("INFY", raw)
        assert snapshot.valid is True
        assert snapshot.is_crossed() is True

    def test_locked_book_detection(self):
        """Locked book (bid == ask) is detected."""
        raw = {
            "symbol": "TCS",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "depth": {
                "buy": [{"price": 4000.00, "quantity": 100}],
                "sell": [{"price": 4000.00, "quantity": 150}],
            },
        }
        snapshot = parse_snapshot("TCS", raw)
        assert snapshot.is_locked() is True

    def test_nan_prices_filtered(self):
        """NaN/Infinity prices are filtered out."""
        raw = {
            "symbol": "WIPRO",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "depth": {
                "buy": [{"price": float("nan"), "quantity": 100}],
                "sell": [{"price": float("inf"), "quantity": 150}],
            },
        }
        snapshot = parse_snapshot("WIPRO", raw)
        assert snapshot.valid is False

    def test_negative_quantity_preserved(self):
        """Negative quantities are preserved in raw but flagged."""
        raw = {
            "symbol": "RELIANCE",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "depth": {
                "buy": [{"price": 2750, "quantity": -100}],
                "sell": [{"price": 2751, "quantity": 200}],
            },
        }
        snapshot = parse_snapshot("RELIANCE", raw)
        # Quantity is parsed but negative; snapshot is still valid structurally
        assert snapshot.depth_buy[0].quantity == -100


class TestSessionCertification:
    """Test full session certification."""

    def _write_jsonl(self, path: Path, records: list[dict]) -> None:
        """Helper: write records to JSONL file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def test_valid_complete_session(self):
        """A valid, complete session with all symbols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Create one record per symbol at 10:00 IST
            records = []
            base_dt = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)
            for i, symbol in enumerate(sorted(AUTHORITATIVE_UNIVERSE)[:5]):  # Sample 5
                records.append({
                    "symbol": symbol,
                    "exchange_timestamp": base_dt.isoformat(),
                    "last_price": 1000 + i * 10,
                    "volume": 100000,
                    "depth": {
                        "buy": [{"price": 1000 + i * 10 - 0.5, "quantity": 100, "orders": 2}],
                        "sell": [{"price": 1000 + i * 10 + 0.5, "quantity": 100, "orders": 2}],
                    },
                })

            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert result.snapshot_count == 5
            assert result.valid_snapshots == 5
            assert len(result.anomalies.malformed_json) == 0

    def test_malformed_json_detected(self):
        """Malformed JSON lines are detected and counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"
            session_dir.mkdir(parents=True)

            # Mix valid and malformed lines
            with jsonl_file.open("w", encoding="utf-8") as f:
                f.write('{"symbol": "INFY", "exchange_timestamp": "2026-08-25T10:00:00Z", "depth": {"buy": [], "sell": []}}\n')
                f.write('{"symbol": "TCS", MALFORMED JSON\n')
                f.write('{"symbol": "RELIANCE", "exchange_timestamp": "2026-08-25T10:00:00Z", "depth": {"buy": [], "sell": []}}\n')

            result = certify_session(jsonl_file)
            assert result.status == "FAIL"
            assert len(result.anomalies.malformed_json) == 1

    def test_unknown_symbols_rejected(self):
        """Unknown symbols cause FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "INVALID_SYMBOL",
                    "exchange_timestamp": "2026-08-25T10:00:00+05:30",
                    "depth": {
                        "buy": [{"price": 100, "quantity": 10}],
                        "sell": [{"price": 101, "quantity": 10}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert result.status == "FAIL"
            assert "INVALID_SYMBOL" in result.unknown_symbols

    def test_out_of_session_detected(self):
        """Records outside 09:15-15:15 IST are flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "RELIANCE",
                    "exchange_timestamp": "2026-08-25T08:00:00+05:30",  # Before session
                    "depth": {
                        "buy": [{"price": 2750, "quantity": 100}],
                        "sell": [{"price": 2751, "quantity": 100}],
                    },
                },
                {
                    "symbol": "RELIANCE",
                    "exchange_timestamp": "2026-08-25T16:00:00+05:30",  # After session
                    "depth": {
                        "buy": [{"price": 2750, "quantity": 100}],
                        "sell": [{"price": 2751, "quantity": 100}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert len(result.anomalies.out_of_session) == 2

    def test_reversed_timestamp_detected(self):
        """Timestamps going backward are detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            t1 = datetime(2026, 8, 25, 10, 30, 0, tzinfo=timezone.utc)
            t2 = datetime(2026, 8, 25, 10, 29, 0, tzinfo=timezone.utc)  # Earlier

            records = [
                {
                    "symbol": "INFY",
                    "exchange_timestamp": t1.isoformat(),
                    "depth": {
                        "buy": [{"price": 3500, "quantity": 100}],
                        "sell": [{"price": 3501, "quantity": 100}],
                    },
                },
                {
                    "symbol": "INFY",
                    "exchange_timestamp": t2.isoformat(),  # Goes backward
                    "depth": {
                        "buy": [{"price": 3500, "quantity": 100}],
                        "sell": [{"price": 3501, "quantity": 100}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert len(result.anomalies.reversed_timestamps) == 1

    def test_duplicate_timestamp_detected(self):
        """Identical consecutive timestamps are detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            t = datetime(2026, 8, 25, 10, 30, 0, tzinfo=timezone.utc)

            records = [
                {
                    "symbol": "TCS",
                    "exchange_timestamp": t.isoformat(),
                    "depth": {
                        "buy": [{"price": 4000, "quantity": 100}],
                        "sell": [{"price": 4001, "quantity": 100}],
                    },
                },
                {
                    "symbol": "TCS",
                    "exchange_timestamp": t.isoformat(),  # Same timestamp
                    "depth": {
                        "buy": [{"price": 4000, "quantity": 100}],
                        "sell": [{"price": 4001, "quantity": 100}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert len(result.anomalies.duplicate_timestamps) == 1

    def test_crossed_books_flagged_not_failed(self):
        """Crossed books are flagged; with missing symbols -> HOLD."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Single symbol with crossed book -> missing 47 others -> HOLD
            records = [
                {
                    "symbol": "RELIANCE",
                    "exchange_timestamp": "2026-08-25T10:30:00+05:30",
                    "depth": {
                        "buy": [{"price": 2751.00, "quantity": 100}],  # Bid > Ask
                        "sell": [{"price": 2750.50, "quantity": 100}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert len(result.anomalies.crossed_books) == 1
            # Crossed books + missing symbols -> HOLD (not PASS_WITH_SOURCE_FLAGS)
            assert result.status == "HOLD"

    def test_locked_books_flagged(self):
        """Locked books (bid == ask) are flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "INFY",
                    "exchange_timestamp": "2026-08-25T10:30:00+05:30",
                    "depth": {
                        "buy": [{"price": 3500.00, "quantity": 100}],
                        "sell": [{"price": 3500.00, "quantity": 100}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert len(result.anomalies.locked_books) == 1

    def test_missing_symbols_detected(self):
        """Missing authoritative symbols are reported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Only one symbol
            records = [
                {
                    "symbol": "RELIANCE",
                    "exchange_timestamp": "2026-08-25T10:30:00+05:30",
                    "depth": {
                        "buy": [{"price": 2750, "quantity": 100}],
                        "sell": [{"price": 2751, "quantity": 100}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)
            result = certify_session(jsonl_file)

            assert len(result.missing_symbols) == 47  # 48 - 1
            assert result.status == "HOLD"

    def test_no_modification_of_source(self):
        """Source file size and hash unchanged after certification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-25"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "TCS",
                    "exchange_timestamp": "2026-08-25T10:30:00+05:30",
                    "depth": {
                        "buy": [{"price": 4000, "quantity": 100}],
                        "sell": [{"price": 4001, "quantity": 100}],
                    },
                },
            ]
            self._write_jsonl(jsonl_file, records)

            # Record file stats before
            stat_before = jsonl_file.stat()
            hash_before = (
                __import__("hashlib").sha256(jsonl_file.read_bytes()).hexdigest()
            )

            # Certify
            certify_session(jsonl_file)

            # Verify unchanged
            stat_after = jsonl_file.stat()
            hash_after = (
                __import__("hashlib").sha256(jsonl_file.read_bytes()).hexdigest()
            )

            assert stat_before.st_size == stat_after.st_size
            assert hash_before == hash_after


class TestDepthSchemaValidation:
    """Test depth schema handling (critical: sparse depth != corruption)."""

    def test_single_populated_level_valid(self):
        """One populated level per side is valid."""
        raw = {
            "symbol": "SUNPHARMA",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "depth": {
                "buy": [{"price": 500, "quantity": 100, "orders": 1}],
                "sell": [{"price": 501, "quantity": 150, "orders": 2}],
            },
        }
        snapshot = parse_snapshot("SUNPHARMA", raw)
        assert snapshot.valid is True
        assert len(snapshot.depth_buy) == 1
        assert len(snapshot.depth_sell) == 1

    def test_mixed_populated_and_empty_levels(self):
        """Some populated, some unpopulated levels is valid."""
        raw = {
            "symbol": "HCLTECH",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "depth": {
                "buy": [
                    {"price": 1500, "quantity": 100, "orders": 3},
                    {},  # Empty
                    {"price": 1498, "quantity": 200, "orders": 5},
                ],
                "sell": [
                    {"price": 1501, "quantity": 150, "orders": 4},
                ],
            },
        }
        snapshot = parse_snapshot("HCLTECH", raw)
        assert snapshot.valid is True
        # Empty dict becomes invalid DepthLevel but doesn't break schema
        assert len(snapshot.depth_buy) == 3
        assert snapshot.depth_buy[0].is_populated() is True
        assert snapshot.depth_buy[1].is_populated() is False
        assert snapshot.depth_buy[2].is_populated() is True

    def test_malformed_depth_container(self):
        """Non-list depth is rejected."""
        raw = {
            "symbol": "MARUTI",
            "exchange_timestamp": "2026-08-25T10:30:00+05:30",
            "depth": {
                "buy": "NOT_A_LIST",  # Malformed
                "sell": [{"price": 10000, "quantity": 50}],
            },
        }
        snapshot = parse_snapshot("MARUTI", raw)
        assert snapshot.valid is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
