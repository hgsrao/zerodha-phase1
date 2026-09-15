"""DECISION PRECEDENCE & CERTIFICATION BINDING TESTS

Critical tests for:
1. Deterministic decision-state precedence (FAIL → HOLD → PASS_WITH_SOURCE_FLAGS → PASS)
2. Certification artifact binding and integrity enforcement
"""
import json
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from l2_dataset_certifier_v2 import certify_session, AUTHORITATIVE_UNIVERSE, FULL_SESSION_CYCLE_SLOTS
from l2_canonical_columnar_pipeline_v2 import _transform_certified_cycles as process_certified_session

IST = ZoneInfo("Asia/Kolkata")


class TestDecisionPrecedence:
    """Tests A–J: Deterministic decision-state order."""

    def _write_jsonl(self, path: Path, cycles: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for cycle in cycles:
                f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

    def test_a_complete_clean_session_pass(self):
        """A. Complete 1,440-cycle clean session → PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Generate exactly 1,440 cycles within session bounds (09:15-15:15 IST)
            from datetime import datetime, timedelta, timezone
            start_ist = datetime(2026, 8, 26, 9, 15, 0, tzinfo=IST)
            cycles = [
                {
                    "cycle": i + 1,
                    "observed_at_utc": (start_ist + timedelta(seconds=i*15)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "observed_at_ist": (start_ist + timedelta(seconds=i*15)).isoformat(),
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
                }
                for i in range(1440)
            ]
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "PASS", f"Expected PASS, got {result.status}"
            assert result.parseable_cycles == 1440

    def test_b_complete_with_source_anomalies_pass_with_flags(self):
        """B. Complete 1,440-cycle session + reversed cycles → PASS_WITH_SOURCE_FLAGS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Cycles 2-1440 in order, then cycle 1 at end (out of order)
            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T03:{45+(i*15)//60:02d}:{(i*15)%60:02d}Z",
                    "observed_at_ist": f"2026-08-26T09:{15+(i*15)//60:02d}:{(i*15)%60:02d}+05:30",
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE},
                }
                for i in range(2, 1441)
            ]
            # Append cycle 1 at the end (reversed)
            cycles.append({
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE},
            })
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "PASS_WITH_SOURCE_FLAGS"

    def test_c_one_cycle_only_hold(self):
        """C. 1 cycle only → HOLD (incomplete session)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [{
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE},
            }]
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "HOLD", f"Expected HOLD for 1 cycle, got {result.status}"
            assert result.parseable_cycles == 1

    def test_d_late_start_hold(self):
        """D. Late start (missing first 100 cycles) → HOLD."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Skip first 100 cycles (16 minutes); only cycles 101-1440
            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T04:{1+i//60:02d}:{i%60:02d}Z",
                    "observed_at_ist": f"2026-08-26T09:{31+i//60:02d}:{i%60:02d}+05:30",
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
                }
                for i in range(101, 1440)
            ]
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "HOLD"
            assert result.parseable_cycles < int(FULL_SESSION_CYCLE_SLOTS * 0.95)

    def test_e_early_stop_hold(self):
        """E. Early stop (missing last 100 cycles) → HOLD."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Only cycles 1-1340 (missing last 100)
            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T03:{45+i//60:02d}:{i%60:02d}Z",
                    "observed_at_ist": f"2026-08-26T09:{15+i//60:02d}:{i%60:02d}+05:30",
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
                }
                for i in range(1, 1341)
            ]
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "HOLD"

    def test_f_missing_first_100_cycles_hold(self):
        """F. Missing first 100 cycles → HOLD."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T04:{1+i//60:02d}:{i%60:02d}Z",
                    "observed_at_ist": f"2026-08-26T09:{31+i//60:02d}:{i%60:02d}+05:30",
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
                }
                for i in range(101, 1441)
            ]
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "HOLD"
            assert result.parseable_cycles == 1340

    def test_g_missing_last_100_cycles_hold(self):
        """G. Missing last 100 cycles → HOLD."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T03:{45+i//60:02d}:{i%60:02d}Z",
                    "observed_at_ist": f"2026-08-26T09:{15+i//60:02d}:{i%60:02d}+05:30",
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
                }
                for i in range(1, 1341)  # Stop at cycle 1340, missing last 100
            ]
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "HOLD"
            assert result.parseable_cycles == 1340

    def test_h_internal_cycle_gap_hold(self):
        """H. Internal cycle gap (cycles 1–500, then 600–1440) → HOLD."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # First half
            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T03:{45+i//60:02d}:{i%60:02d}Z",
                    "observed_at_ist": f"2026-08-26T09:{15+i//60:02d}:{i%60:02d}+05:30",
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
                }
                for i in range(1, 501)
            ]
            # Second half (gap from 501–599)
            cycles.extend([
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T03:{45+i//60:02d}:{i%60:02d}Z",
                    "observed_at_ist": f"2026-08-26T09:{15+i//60:02d}:{i%60:02d}+05:30",
                    "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
                }
                for i in range(600, 1441)
            ])
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "HOLD"

    def test_i_unrecoverable_corruption_fail(self):
        """I. Unrecoverable outer structure corruption → FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file.open("w") as f:
                f.write("INVALID_JSON\n")

            result = certify_session(jsonl_file)
            assert result.status == "FAIL"

    def test_j_incomplete_with_anomalies_holds_not_flags(self):
        """J. Incomplete session with anomalies → HOLD (not PASS_WITH_SOURCE_FLAGS)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Only 100 cycles (incomplete) with sparse depth (anomaly)
            cycles = [
                {
                    "cycle": i,
                    "observed_at_utc": f"2026-08-26T03:45:00Z",
                    "observed_at_ist": f"2026-08-26T09:15:00+05:30",
                    "snapshots": {
                        sym: {
                            "valid": True,
                            "depth": {"buy": [], "sell": []},  # Legitimate sparse
                        }
                        for sym in AUTHORITATIVE_UNIVERSE
                    },
                }
                for i in range(100)
            ]
            self._write_jsonl(jsonl_file, cycles)
            result = certify_session(jsonl_file)
            assert result.status == "HOLD", f"Incomplete + anomalies should be HOLD, got {result.status}"


class TestCertificationBinding:
    """Tests: explicit certification artifact binding enforcement."""

    def _write_jsonl(self, path: Path, cycles: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for cycle in cycles:
                f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

    def test_cert_artifact_exists_and_contains_raw_sha(self):
        """Certification artifact exists and holds raw JSONL SHA."""
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

            cert = certify_session(jsonl_file)
            # Certification artifact should record the raw file SHA
            assert cert.file_sha256 is not None
            assert len(cert.file_sha256) == 64  # SHA256 hex

    def test_pipeline_accepts_pass_certification(self):
        """Pipeline accepts PASS certification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            from datetime import datetime, timedelta, timezone
            start_ist = datetime(2026, 8, 26, 9, 15, 0, tzinfo=IST)
            cycles = [{
                "cycle": i + 1,
                "observed_at_utc": (start_ist + timedelta(seconds=i*15)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "observed_at_ist": (start_ist + timedelta(seconds=i*15)).isoformat(),
                "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
            } for i in range(1440)]
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)
            assert cert.status == "PASS"

            # Pipeline should accept and process
            manifest = process_certified_session(jsonl_file)
            assert manifest["accounting"]["canonical_rows"] > 0
            assert manifest["accounting"]["canonical_rows"] == 1440 * 48

    def test_pipeline_accepts_pass_with_source_flags_certification(self):
        """Pipeline accepts PASS_WITH_SOURCE_FLAGS certification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Create 1440 cycles with out-of-order at end (reversed) to trigger PASS_WITH_SOURCE_FLAGS
            cycles = [{
                "cycle": i + 2,
                "observed_at_utc": f"2026-08-26T03:{45+(i*15)//60:02d}:{(i*15)%60:02d}Z",
                "observed_at_ist": f"2026-08-26T09:{15+(i*15)//60:02d}:{(i*15)%60:02d}+05:30",
                "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
            } for i in range(1439)]
            # Add cycle 1 at the end (reversed)
            cycles.append({
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {sym: {"valid": True} for sym in AUTHORITATIVE_UNIVERSE}
            })
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)
            assert cert.status == "PASS_WITH_SOURCE_FLAGS"

            # Pipeline should accept
            manifest = process_certified_session(jsonl_file)
            assert manifest is not None
            assert manifest["accounting"]["canonical_rows"] > 0

    def test_certifier_identity_sha256_preserved(self):
        """Certifier V2 SHA256 is immutable and comparable."""
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

            result1 = certify_session(jsonl_file)
            result2 = certify_session(jsonl_file)

            # Certifier identity must remain constant
            assert result1.certifier_sha256 == result2.certifier_sha256


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
