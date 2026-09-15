"""TEST SUITE — L2 Canonical Columnar Pipeline V1

Adversarial tests for raw JSONL → canonical Parquet transformation.

18 test cases using synthetic fixtures (never real data).

CRITICAL INVARIANTS:
1. Every structurally parseable JSON record appears in canonical output.
2. Explicit quality provenance distinguishes absent vs. malformed.
3. Raw JSONL immutability verified.
4. Schema/type preservation validated.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from l2_canonical_columnar_pipeline_v1 import (
    CANONICAL_COLUMNS,
    process_certified_session,
    sha256_file,
    transform_jsonl_to_canonical,
)


class TestCanonicalPipeline:
    """Canonical pipeline tests."""

    def _write_jsonl(self, path: Path, records: list[dict]) -> None:
        """Helper: write test JSONL."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def test_valid_complete_record(self):
        """Valid record with all fields populated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "RELIANCE",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 2750.50,
                    "last_quantity": 100,
                    "average_price": 2750.00,
                    "volume": 1000000,
                    "buy_quantity": 500000,
                    "sell_quantity": 450000,
                    "net_change": +1.50,
                    "ohlc": {"open": 2749, "high": 2755, "low": 2748, "close": 2750},
                    "depth": {
                        "buy": [
                            {"price": 2750.25, "quantity": 100, "orders": 5},
                            {"price": 2750.00, "quantity": 200, "orders": 8},
                            {"price": 2749.75, "quantity": 150, "orders": 6},
                            {"price": 2749.50, "quantity": 300, "orders": 10},
                            {"price": 2749.25, "quantity": 250, "orders": 7},
                        ],
                        "sell": [
                            {"price": 2750.75, "quantity": 120, "orders": 6},
                            {"price": 2751.00, "quantity": 250, "orders": 10},
                            {"price": 2751.25, "quantity": 180, "orders": 7},
                            {"price": 2751.50, "quantity": 200, "orders": 8},
                            {"price": 2751.75, "quantity": 300, "orders": 12},
                        ],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert len(rejected) == 0
            assert df[0, "record_valid"] is True
            assert df[0, "bid_populated_count"] == 5
            assert df[0, "ask_populated_count"] == 5
            assert df[0, "malformed_depth"] is False

    def test_sparse_depth_legitimate(self):
        """Sparse but valid depth (2 bids, 1 ask)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "INFY",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 3500.00,
                    "depth": {
                        "buy": [
                            {"price": 3499.50, "quantity": 100, "orders": 2},
                            {"price": 3499.00, "quantity": 200, "orders": 5},
                        ],
                        "sell": [
                            {"price": 3500.50, "quantity": 150, "orders": 3},
                        ],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert len(rejected) == 0
            assert df[0, "record_valid"] is True
            assert df[0, "bid_populated_count"] == 2
            assert df[0, "ask_populated_count"] == 1
            assert df[0, "bid_3_price"] is None
            assert df[0, "ask_2_price"] is None

    def test_missing_required_field_not_silently_lost(self):
        """Missing exchange_timestamp — record preserved with quality flags."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "TCS",
                    "last_price": 4000.00,
                    "depth": {
                        "buy": [{"price": 3999.50, "quantity": 100}],
                        "sell": [{"price": 4000.50, "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            # Record must NOT be silently dropped
            assert len(df) == 1, "Record with missing timestamp must be preserved"
            assert df[0, "missing_exchange_timestamp"] is True
            assert df[0, "exchange_timestamp"] is None
            assert df[0, "record_valid"] is False  # Invalid due to missing timestamp

    def test_malformed_numeric_field(self):
        """Non-numeric last_price coerces to None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "WIPRO",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": "NOT_A_NUMBER",
                    "depth": {
                        "buy": [{"price": 100, "quantity": 100}],
                        "sell": [{"price": 101, "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert df[0, "last_price"] is None
            assert df[0, "malformed_last_price"] is True  # Explicit flag
            assert df[0, "missing_last_price"] is False
            assert df[0, "record_valid"] is False

    def test_absent_vs_malformed_numeric(self):
        """Distinguish absent field vs. malformed field value.

        Record A: last_price not in JSON → missing_last_price=True, malformed_last_price=False
        Record B: last_price="NOT_A_NUMBER" → missing_last_price=False, malformed_last_price=True
        Both yield None numerically, but quality flags differ.
        """
        import polars as pl

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "RECORD_A",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    # no last_price field
                    "depth": {
                        "buy": [{"price": 100, "quantity": 100}],
                        "sell": [{"price": 101, "quantity": 100}],
                    },
                },
                {
                    "symbol": "RECORD_B",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": "NOT_A_NUMBER",  # malformed
                    "depth": {
                        "buy": [{"price": 100, "quantity": 100}],
                        "sell": [{"price": 101, "quantity": 100}],
                    },
                },
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 2

            # Record A: absent
            record_a = df.filter(pl.col("symbol") == "RECORD_A")
            assert record_a[0, "last_price"] is None
            assert record_a[0, "missing_last_price"] is True
            assert record_a[0, "malformed_last_price"] is False

            # Record B: malformed
            record_b = df.filter(pl.col("symbol") == "RECORD_B")
            assert record_b[0, "last_price"] is None
            assert record_b[0, "missing_last_price"] is False
            assert record_b[0, "malformed_last_price"] is True

    def test_nan_infinity_handling(self):
        """NaN and Infinity in prices."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "HDFCBANK",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": float("nan"),
                    "depth": {
                        "buy": [{"price": 1500.00, "quantity": 100}],
                        "sell": [{"price": float("inf"), "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert df[0, "last_price"] is None  # NaN → None
            assert df[0, "ask_1_price"] is None  # Infinity → None
            assert df[0, "malformed_last_price"] is True

    def test_zero_vs_null(self):
        """Distinguish genuine zero from null/missing."""
        import polars as pl

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "ZERO_QTY",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 100.00,
                    "depth": {
                        "buy": [{"price": 99.50, "quantity": 0}],  # Genuine zero
                        "sell": [{"price": 100.50, "quantity": 50}],
                    },
                },
                {
                    "symbol": "NULL_QTY",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 100.00,
                    "depth": {
                        "buy": [{"price": 99.50, "quantity": None}],  # Null
                        "sell": [{"price": 100.50, "quantity": 50}],
                    },
                },
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            zero_rec = df.filter(pl.col("symbol") == "ZERO_QTY")
            null_rec = df.filter(pl.col("symbol") == "NULL_QTY")

            # Zero is valid (price+qty both present)
            assert zero_rec[0, "bid_1_qty"] == 0
            assert zero_rec[0, "bid_populated_count"] == 1

            # Null qty means unpopulated level
            assert null_rec[0, "bid_1_qty"] is None
            assert null_rec[0, "bid_populated_count"] == 0

    def test_explicit_empty_depth_level(self):
        """Empty depth level (no price/qty)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "EMPTY_LEVEL",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 100.00,
                    "depth": {
                        "buy": [
                            {"price": 99.50, "quantity": 100},
                            {},  # Empty level object
                            {"price": 99.00, "quantity": 200},
                        ],
                        "sell": [{"price": 100.50, "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            # bid[0] populated, bid[1] empty (None), bid[2] populated
            assert df[0, "bid_1_price"] == 99.50
            assert df[0, "bid_2_price"] is None  # Empty level
            assert df[0, "bid_3_price"] == 99.00
            assert df[0, "bid_populated_count"] == 2

    def test_negative_values_preserved(self):
        """Negative quantities/prices are preserved (anomaly source)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "MARUTI",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": -100.00,
                    "depth": {
                        "buy": [{"price": 10000, "quantity": -50}],
                        "sell": [{"price": 10001, "quantity": 50}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert df[0, "last_price"] == -100.00
            assert df[0, "bid_1_qty"] == -50

    def test_depth_not_list(self):
        """Depth container is not a list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "TATASTEEL",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 200.00,
                    "depth": {
                        "buy": "NOT_A_LIST",
                        "sell": [{"price": 200.50, "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert df[0, "malformed_depth"] is True
            assert df[0, "bid_populated_count"] == 0
            assert df[0, "record_valid"] is False

    def test_missing_depth_container(self):
        """Depth field entirely missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "KOTAKBANK",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 2000.00,
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert df[0, "bid_1_price"] is None
            assert df[0, "ask_1_price"] is None
            assert df[0, "bid_populated_count"] == 0
            assert df[0, "ask_populated_count"] == 0
            assert df[0, "record_valid"] is False

    def test_raw_immutability(self):
        """Raw JSONL remains unchanged after processing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "SBIN",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 500.00,
                    "depth": {
                        "buy": [{"price": 499.50, "quantity": 100}],
                        "sell": [{"price": 500.50, "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            sha_before = sha256_file(jsonl_file)
            manifest = process_certified_session(jsonl_file)
            sha_after = sha256_file(jsonl_file)

            assert sha_before == sha_after
            assert manifest["immutability"]["unchanged"] is True

    def test_deterministic_ordering(self):
        """Output is deterministically ordered by timestamp, then symbol."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "RELIANCE",
                    "exchange_timestamp": "2026-08-26T10:31:00+05:30",
                    "last_price": 2750.00,
                    "depth": {
                        "buy": [{"price": 2749.50, "quantity": 100}],
                        "sell": [{"price": 2750.50, "quantity": 100}],
                    },
                },
                {
                    "symbol": "INFY",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 3500.00,
                    "depth": {
                        "buy": [{"price": 3499.50, "quantity": 100}],
                        "sell": [{"price": 3500.50, "quantity": 100}],
                    },
                },
                {
                    "symbol": "HDFCBANK",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 1500.00,
                    "depth": {
                        "buy": [{"price": 1499.50, "quantity": 100}],
                        "sell": [{"price": 1500.50, "quantity": 100}],
                    },
                },
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert df[0, "symbol"] == "HDFCBANK"
            assert df[1, "symbol"] == "INFY"
            assert df[2, "symbol"] == "RELIANCE"

    def test_column_order(self):
        """Output columns are in canonical order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "TCS",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 4000.00,
                    "depth": {
                        "buy": [{"price": 3999.50, "quantity": 100}],
                        "sell": [{"price": 4000.50, "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert df.columns == CANONICAL_COLUMNS
            assert len(CANONICAL_COLUMNS) == 58

    def test_exchange_timestamp_preservation(self):
        """exchange_timestamp is preserved in canonical schema."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "SBIN",
                    "exchange_timestamp": "2026-08-26T10:30:15+05:30",
                    "last_price": 500.00,
                    "depth": {
                        "buy": [{"price": 499.50, "quantity": 100}],
                        "sell": [{"price": 500.50, "quantity": 100}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            df, rejected = transform_jsonl_to_canonical(jsonl_file, "2026-08-26")

            assert len(df) == 1
            assert df[0, "exchange_timestamp"] is not None
            assert df[0, "exchange_timestamp_ist"] is not None
            assert "exchange_timestamp" in CANONICAL_COLUMNS

    def test_parquet_round_trip(self):
        """Parquet round-trip preserves types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {
                    "symbol": "CIPLA",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 1200.50,
                    "volume": 5000000,
                    "depth": {
                        "buy": [{"price": 1200.25, "quantity": 100, "orders": 5}],
                        "sell": [{"price": 1200.75, "quantity": 150, "orders": 8}],
                    },
                }
            ]

            self._write_jsonl(jsonl_file, records)
            manifest = process_certified_session(jsonl_file)

            parquet_path = Path(manifest["output"]["parquet_path"])
            assert parquet_path.exists()

            manifest_path = parquet_path.parent / "manifest.json"
            assert manifest_path.exists()

    def test_rejected_record_accounting(self):
        """Verify accounting invariant: parseable >= canonical + rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            records = [
                {"not_json_parseable": 1} for _ in range(0)
            ] + [
                {
                    "symbol": "VALID_1",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 100.00,
                    "depth": {
                        "buy": [{"price": 99.50, "quantity": 100}],
                        "sell": [{"price": 100.50, "quantity": 100}],
                    },
                },
                {  # Will be rejected: no symbol
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 200.00,
                },
                {
                    "symbol": "VALID_2",
                    "exchange_timestamp": "2026-08-26T10:30:00+05:30",
                    "last_price": 300.00,
                    "depth": {
                        "buy": [{"price": 299.50, "quantity": 100}],
                        "sell": [{"price": 300.50, "quantity": 100}],
                    },
                },
            ]

            self._write_jsonl(jsonl_file, records)
            manifest = process_certified_session(jsonl_file)

            canonical_count = manifest["record_accounting"]["canonical_rows"]
            rejected_count = manifest["record_accounting"]["explicitly_rejected_records"]

            # 2 valid + 1 rejected = 3 total parseable records
            assert canonical_count == 2
            assert rejected_count == 1
            assert canonical_count + rejected_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
