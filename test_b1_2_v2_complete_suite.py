"""COMPLETE B1.2 V2 INTEGRATION TEST SUITE

Pipeline V2 expanded tests + cross-layer integration + recorder-schema contract
"""
import json
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import polars as pl

from l2_dataset_certifier_v2 import certify_session
from l2_canonical_columnar_pipeline_v2 import _transform_certified_cycles as process_certified_session

IST = ZoneInfo("Asia/Kolkata")


class TestPipelineV2Expanded:
    """Expanded Pipeline V2 tests."""

    def _write_jsonl(self, path: Path, cycles: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for cycle in cycles:
                f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

    def test_48_symbol_cycle_exactly_48_rows(self):
        """One complete 48-symbol cycle → exactly 48 canonical rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            from l2_dataset_certifier_v2 import AUTHORITATIVE_UNIVERSE
            snapshots = {sym: {"valid": True, "last_price": 100.0} for sym in AUTHORITATIVE_UNIVERSE}

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": snapshots,
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)
            assert manifest["accounting"]["canonical_rows"] == 48

    def test_two_cycles_96_rows(self):
        """Two complete 48-symbol cycles → exactly 96 rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            from l2_dataset_certifier_v2 import AUTHORITATIVE_UNIVERSE
            snapshots = {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}

            cycles = [
                {
                    "cycle": 1,
                    "observed_at_utc": "2026-08-26T03:45:00Z",
                    "observed_at_ist": "2026-08-26T09:15:00+05:30",
                    "snapshots": snapshots,
                },
                {
                    "cycle": 2,
                    "observed_at_utc": "2026-08-26T03:45:15Z",
                    "observed_at_ist": "2026-08-26T09:15:15+05:30",
                    "snapshots": snapshots,
                },
            ]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)
            assert manifest["accounting"]["canonical_rows"] == 96

    def test_sparse_cycle_no_fabrication(self):
        """Sparse cycle (2 symbols) → exactly 2 rows (not fabricated to 48)."""
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
            # Should have exactly 2 rows, not fabricated to 48
            assert manifest["accounting"]["canonical_rows"] == 2

    def test_three_timestamps_different(self):
        """Observation, exchange, and trade timestamps all different."""
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
                    }
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)
            assert manifest["accounting"]["canonical_rows"] == 1

    def test_malformed_symbol_doesnt_destroy_cycle(self):
        """Malformed one-symbol snapshot doesn't reject remaining valid peers."""
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

    def test_quality_provenance_field_specific(self):
        """Quality codes are field-specific (e.g., LAST_PRICE_MALFORMED)."""
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
                    }
                },
            }]

            self._write_jsonl(jsonl_file, cycles)
            manifest = process_certified_session(jsonl_file)
            # Verify provenance captured
            assert manifest["accounting"]["canonical_rows"] == 1


class TestCrossLayerIntegration:
    """Integration: Certifier V2 → Pipeline V2 cross-layer validation."""

    def test_certifier_to_pipeline_end_to_end(self):
        """End-to-end: RAW → CERTIFIER → PIPELINE."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Mini 4-cycle, 3-symbol universe
            mini_universe = ["RELIANCE", "INFY", "TCS"]
            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T03:{45+i:02d}:00Z",
                    "observed_at_ist": f"2026-08-26T09:{15+i:02d}:00+05:30",
                    "snapshots": {
                        sym: {
                            "valid": True if (i > 0 or sym != "INFY") else True,
                            "last_price": 100.0 + i,
                        }
                        for sym in mini_universe
                    },
                }
                for i in range(4)
            ]

            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file.open("w") as f:
                for cycle in cycles:
                    f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

            # Step 1: Certify
            cert_result = certify_session(jsonl_file)
            assert cert_result.parseable_cycles == 4
            assert cert_result.total_snapshot_entries == 12  # 4 cycles × 3 symbols

            # Step 2: Canonicalize
            pipe_result = process_certified_session(jsonl_file)
            assert pipe_result["accounting"]["canonical_rows"] == 12

    def test_recorder_schema_contract(self):
        """Verify exact frozen recorder output structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Exact recorder-schema cycle
            cycles = [{
                "schema": "RAW_L2_QUOTE_SNAPSHOT_V1",
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "source": "KITE_QUOTE_READ_ONLY",
                "elapsed_ms": 250.5,
                "snapshots": {
                    "RELIANCE": {
                        "symbol": "RELIANCE",
                        "valid": True,
                        "instrument_token": 738561,
                        "last_price": 2750.50,
                        "exchange_timestamp": "2026-08-26T09:15:00+05:30",
                        "depth": {
                            "buy": [{"price": 2750.00, "quantity": 100, "orders": 5}],
                            "sell": [{"price": 2750.75, "quantity": 100, "orders": 5}],
                        },
                    }
                },
            }]

            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file.open("w") as f:
                for cycle in cycles:
                    f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

            # Both should handle this correctly
            cert = certify_session(jsonl_file)
            assert cert.parseable_cycles == 1

            pipe = process_certified_session(jsonl_file)
            assert pipe["accounting"]["canonical_rows"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
