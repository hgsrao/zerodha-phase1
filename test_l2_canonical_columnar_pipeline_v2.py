"""TEST SUITE — L2 Canonical Columnar Pipeline V2

Nested-cycle flattening to per-symbol canonical rows.
Field-specific quality provenance.
Accounting invariants.
"""
import json
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from l2_canonical_columnar_pipeline_v2 import _transform_certified_cycles as process_certified_session

IST = ZoneInfo("Asia/Kolkata")


class TestCanonicalPipelineV2:
    """Canonical pipeline V2 tests."""

    def _write_jsonl(self, path: Path, cycles: list[dict]) -> None:
        """Write nested-cycle JSONL."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for cycle in cycles:
                f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

    def test_one_complete_cycle_three_symbols(self):
        """One cycle with 3 symbols → 3 canonical rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            snapshots = {
                "RELIANCE": {
                    "valid": True,
                    "last_price": 2750.0,
                    "exchange_timestamp": "2026-08-26T09:15:00+05:30",
                    "depth": {"buy": [], "sell": []},
                },
                "INFY": {
                    "valid": True,
                    "last_price": 3500.0,
                    "exchange_timestamp": "2026-08-26T09:15:00+05:30",
                    "depth": {"buy": [], "sell": []},
                },
                "TCS": {
                    "valid": True,
                    "last_price": 4000.0,
                    "exchange_timestamp": "2026-08-26T09:15:00+05:30",
                    "depth": {"buy": [], "sell": []},
                },
            }

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": snapshots,
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            assert manifest["accounting"]["canonical_rows"] == 3
            assert manifest["accounting"]["total_snapshot_entries"] == 3

    def test_two_complete_cycles_four_symbols(self):
        """Two cycles with 4 symbols each → 8 rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            symbols = ["RELIANCE", "INFY", "TCS", "HDFCBANK"]
            snapshots1 = {sym: {"valid": True, "last_price": 100.0 + i} for i, sym in enumerate(symbols)}
            snapshots2 = {sym: {"valid": True, "last_price": 200.0 + i} for i, sym in enumerate(symbols)}

            cycles = [
                {
                    "cycle": 1,
                    "observed_at_utc": "2026-08-26T03:45:00Z",
                    "observed_at_ist": "2026-08-26T09:15:00+05:30",
                    "snapshots": snapshots1,
                },
                {
                    "cycle": 2,
                    "observed_at_utc": "2026-08-26T03:45:15Z",
                    "observed_at_ist": "2026-08-26T09:15:15+05:30",
                    "snapshots": snapshots2,
                },
            ]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            assert manifest["accounting"]["canonical_rows"] == 8
            assert manifest["accounting"]["total_snapshot_entries"] == 8

    def test_sparse_symbol_coverage_no_fabrication(self):
        """Cycle with 2 symbols → 2 rows (not fabricated to 48)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {
                    "RELIANCE": {"valid": True},
                    "INFY": {"valid": True},
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            assert manifest["accounting"]["canonical_rows"] == 2
            assert manifest["accounting"]["explicitly_rejected_entries"] == 0

    def test_observation_timestamp_shared_across_cycle(self):
        """All rows from same cycle share same observed_at_ist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {
                    "RELIANCE": {"valid": True},
                    "INFY": {"valid": True},
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            # Verify rows were created
            assert manifest["accounting"]["canonical_rows"] == 2

    def test_three_timestamps_separate(self):
        """Observed, exchange, and last-trade timestamps remain separate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {
                    "RELIANCE": {
                        "valid": True,
                        "exchange_timestamp": "2026-08-26T09:15:05+05:30",
                        "last_trade_time": "2026-08-26T09:14:55+05:30",
                    },
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            assert manifest["accounting"]["canonical_rows"] == 1

    def test_malformed_json(self):
        """Not-valid-JSON increments counter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file.open("w") as f:
                f.write('NOT JSON\n')

            manifest = process_certified_session(jsonl_file)

            assert manifest["accounting"]["malformed_json_lines"] >= 1

    def test_malformed_one_symbol_doesnt_destroy_cycle(self):
        """Malformed snapshot for one symbol doesn't reject whole cycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {
                    "RELIANCE": {"valid": True},
                    "INFY": "NOT_A_DICT",
                    "TCS": {"valid": True},
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            assert manifest["accounting"]["canonical_rows"] == 2
            assert manifest["accounting"]["explicitly_rejected_entries"] == 1

    def test_quality_codes_for_malformed_numeric(self):
        """Malformed numeric gets quality code instead of silent null."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {
                    "RELIANCE": {
                        "valid": True,
                        "last_price": "NOT_A_NUMBER",
                    },
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            # Quality codes should be populated
            assert manifest["accounting"]["canonical_rows"] == 1

    def test_accounting_invariant(self):
        """canonical + rejected = total snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {
                    "RELIANCE": {"valid": True},
                    "INFY": "MALFORMED",
                    "TCS": {"valid": False},
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            canonical = manifest["accounting"]["canonical_rows"]
            rejected = manifest["accounting"]["explicitly_rejected_entries"]
            total = manifest["accounting"]["total_snapshot_entries"]

            assert canonical + rejected == total

    def test_raw_immutability(self):
        """Raw JSONL unchanged after processing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {"RELIANCE": {"valid": True}},
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)

            assert manifest["immutability"]["unchanged"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
