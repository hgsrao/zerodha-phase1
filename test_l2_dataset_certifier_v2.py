"""TEST SUITE — L2 Dataset Certifier V2

Nested-cycle structure: one JSONL line = one cycle with per-symbol snapshots dict.
"""
import json
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from l2_dataset_certifier_v2 import certify_session

IST = ZoneInfo("Asia/Kolkata")


class TestCertifierV2:
    """Certifier V2 nested-cycle tests."""

    def _write_jsonl(self, path: Path, cycles: list[dict]) -> None:
        """Write test JSONL with nested cycles."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for cycle in cycles:
                f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

    def test_valid_cycle_with_snapshots(self):
        """Valid cycle with multiple snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            snapshots = {
                "RELIANCE": {"symbol": "RELIANCE", "valid": True, "last_price": 2750.0},
                "INFY": {"symbol": "INFY", "valid": True, "last_price": 3500.0},
            }

            cycles = [{
                "observed_at_utc": "2026-08-26T09:15:00Z",
                "observed_at_ist": "2026-08-26T14:45:00+05:30",
                "snapshots": snapshots,
            }]

            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)

            assert result.parseable_cycles == 1
            assert result.total_snapshot_entries == 2
            assert result.canonical_entries == 2

    def test_malformed_json(self):
        """Malformed JSON line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file.open("w") as f:
                f.write('{"valid": true}\n')
                f.write('NOT JSON\n')

            result = certify_session(jsonl_file)

            assert result.malformed_json_lines == 1
            assert result.parseable_cycles == 1

    def test_missing_snapshots_dict(self):
        """Cycle missing snapshots container."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "observed_at_utc": "2026-08-26T09:15:00Z",
                "observed_at_ist": "2026-08-26T14:45:00+05:30",
            }]

            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)

            assert result.malformed_json_lines == 1

    def test_accounting_invariant(self):
        """canonical + rejected = total snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            snapshots = {
                "RELIANCE": {"valid": True},
                "INFY": {"valid": False},
            }

            cycles = [{
                "observed_at_utc": "2026-08-26T09:15:00Z",
                "observed_at_ist": "2026-08-26T14:45:00+05:30",
                "snapshots": snapshots,
            }]

            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)

            assert result.canonical_entries + result.rejected_snapshot_entries == 2
            assert result.total_snapshot_entries == 2

    def test_file_accounting(self):
        """parseable + malformed = nonempty lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file.open("w") as f:
                f.write('{"observed_at_utc":"2026-08-26T09:15:00Z","observed_at_ist":"2026-08-26T14:45:00+05:30","snapshots":{}}\n')
                f.write('BAD\n')
                f.write('{"observed_at_utc":"2026-08-26T09:30:00Z","observed_at_ist":"2026-08-26T15:00:00+05:30","snapshots":{}}\n')

            result = certify_session(jsonl_file)

            assert result.parseable_cycles + result.malformed_json_lines == result.nonempty_raw_lines

    def test_unknown_symbol(self):
        """Snapshots contains unknown symbol."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            snapshots = {
                "RELIANCE": {"valid": True},
                "UNKNOWN_SYM": {"valid": True},
            }

            cycles = [{
                "observed_at_utc": "2026-08-26T09:15:00Z",
                "observed_at_ist": "2026-08-26T14:45:00+05:30",
                "snapshots": snapshots,
            }]

            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)

            assert len(result.cycle_details) > 0
            assert "UNKNOWN_SYM" in result.cycle_details[0].unknown_symbols


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
