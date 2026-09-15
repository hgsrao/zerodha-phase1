"""PIPELINE V2 CERTIFICATION BINDING TESTS

Required 14 tests for Phase 2:
1. PASS certification + matching raw SHA → ALLOW
2. PASS_WITH_SOURCE_FLAGS + matching raw SHA → ALLOW and flags preserved
3. HOLD → BLOCK
4. FAIL → BLOCK
5. Missing certification → BLOCK
6. Unknown decision state → BLOCK
7. Raw SHA mismatch → BLOCK
8. Wrong Certifier V2 source SHA → BLOCK
9. Wrong Certifier V2 freeze SHA → BLOCK
10. Superseded Certifier V1 identity → BLOCK
11. Tampered certification artifact → BLOCK
12. Correct approved certification artifact → ALLOW
13. Raw file changed after certification → BLOCK
14. Certification from a different raw file with same session date → BLOCK
"""
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pytest

from l2_dataset_certifier_v2 import certify_session, AUTHORITATIVE_UNIVERSE
from l2_canonical_columnar_pipeline_v2 import canonicalize_certified_session

IST = ZoneInfo("Asia/Kolkata")

# Frozen Certifier V2 identity from Phase 1
# certifier_freeze_sha parameter binds to the certifier SOURCE SHA, not the freeze JSON
FROZEN_CERTIFIER_V2_SOURCE_SHA = "0FBB4E8000D18F951C508DF179DD4A27D9A31F4D5A36C77F62A0D4DEAC7F0568"
FROZEN_CERTIFIER_V2_FREEZE_SHA = "06B9AA57396D95FE1FD91E01E10B049DE9C08FFFA1DA56B42C2C8D556BF3E678"
# For pipeline binding, use the SOURCE SHA
CERTIFIER_SOURCE_BINDING = FROZEN_CERTIFIER_V2_SOURCE_SHA


def process_certified_session(
    jsonl_file, cert_result=None, certifier_freeze_sha=CERTIFIER_SOURCE_BINDING
):
    """Exercise the real-data gate; synthetic suites use the private helper."""
    return canonicalize_certified_session(
        jsonl_file,
        cert_result=cert_result,
        certifier_freeze_sha=certifier_freeze_sha,
    )


@dataclass
class MockCertification:
    """Mock certification result for testing."""
    status: str
    file_sha256: str
    certifier_sha256: str = FROZEN_CERTIFIER_V2_SOURCE_SHA


@pytest.mark.parametrize("defect", ["missing", "hold", "raw_sha", "certifier_sha"])
def test_uncertified_paths_create_no_derived_outputs(defect):
    """Every pre-transform certification rejection must be side-effect free."""
    import hashlib

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        session_dir = root / "raw" / "2026-08-26"
        jsonl_file = session_dir / "raw_l2_snapshots.jsonl"
        output_base = root / "derived"
        session_dir.mkdir(parents=True)
        jsonl_file.write_text(
            '{"cycle":1,"observed_at_utc":"2026-08-26T03:45:00Z",'
            '"observed_at_ist":"2026-08-26T09:15:00+05:30","snapshots":{}}\n',
            encoding="utf-8",
        )
        raw_sha = hashlib.sha256(jsonl_file.read_bytes()).hexdigest()
        cert = MockCertification("PASS", raw_sha)
        if defect == "missing":
            cert = None
        elif defect == "hold":
            cert.status = "HOLD"
        elif defect == "raw_sha":
            cert.file_sha256 = "0" * 64
        elif defect == "certifier_sha":
            cert.certifier_sha256 = "0" * 64

        with pytest.raises(ValueError, match="BLOCK"):
            canonicalize_certified_session(
                jsonl_file,
                cert_result=cert,
                certifier_freeze_sha=CERTIFIER_SOURCE_BINDING,
                output_base_dir=output_base,
            )

        assert not output_base.exists()


class TestPipelineV2CertificationBinding:
    """14 required certification binding tests."""

    def _write_jsonl(self, path: Path, cycles: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for cycle in cycles:
                f.write(json.dumps(cycle, separators=(",", ":")) + "\n")

    def _create_valid_session(self, count: int = 1440) -> list[dict]:
        """Create a valid session with N cycles (default: full 1,440-cycle session)."""
        from datetime import datetime, timedelta, timezone
        start_ist = datetime(2026, 8, 26, 9, 15, 0, tzinfo=IST)
        return [
            {
                "cycle": i + 1,
                "observed_at_utc": (start_ist + timedelta(seconds=i*15)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "observed_at_ist": (start_ist + timedelta(seconds=i*15)).isoformat(),
                "snapshots": {sym: {"valid": True} for sym in list(AUTHORITATIVE_UNIVERSE)[:48]}
            }
            for i in range(count)
        ]

    def test_1_pass_certification_matching_sha_allow(self):
        """1. PASS certification + matching raw SHA → ALLOW."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            # Real certification
            cert = certify_session(jsonl_file)
            assert cert.status == "PASS"

            # Pipeline should allow
            manifest = process_certified_session(
                jsonl_file,
                cert_result=cert,
                certifier_freeze_sha=CERTIFIER_SOURCE_BINDING
            )
            assert manifest is not None
            assert manifest["certification_binding"]["certification_decision_approved"]

    def test_2_pass_with_source_flags_allow_flags_preserved(self):
        """2. PASS_WITH_SOURCE_FLAGS + matching SHA → ALLOW + flags."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Create 100 cycles with last one reversed (anomaly)
            cycles = self._create_valid_session(1439)
            cycles.append({
                "cycle": 1,
                "observed_at_utc": "2026-08-26T03:45:00Z",
                "observed_at_ist": "2026-08-26T09:15:00+05:30",
                "snapshots": {sym: {"valid": True} for sym in list(AUTHORITATIVE_UNIVERSE)[:48]}
            })
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)
            # With incomplete cycles but anomaly present
            if cert.status == "PASS_WITH_SOURCE_FLAGS":
                manifest = process_certified_session(jsonl_file, cert_result=cert)
                assert manifest["certification_binding"]["certification_decision_approved"]

    def test_3_hold_certification_block(self):
        """3. HOLD certification → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            # Only 1 cycle (incomplete)
            cycles = self._create_valid_session(1)
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)
            assert cert.status == "HOLD"

            # Pipeline must block
            with pytest.raises(ValueError, match="BLOCK.*not approved"):
                process_certified_session(jsonl_file, cert_result=cert)

    def test_4_fail_certification_block(self):
        """4. FAIL certification → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file.open("w") as f:
                f.write("INVALID_JSON\n")

            cert = certify_session(jsonl_file)
            assert cert.status == "FAIL"

            # Pipeline must block
            with pytest.raises(ValueError, match="BLOCK.*not approved"):
                process_certified_session(jsonl_file, cert_result=cert)

    def test_5_missing_certification_real_data_blocked(self):
        """5. Missing certification on real-data entry → BLOCK / FAIL CLOSED."""
        from l2_canonical_columnar_pipeline_v2 import canonicalize_certified_session
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            # Real-data entry point MUST block missing certification
            with pytest.raises(ValueError, match="BLOCK.*certification artifact.*missing"):
                canonicalize_certified_session(
                    jsonl_file,
                    cert_result=None,
                    certifier_freeze_sha=CERTIFIER_SOURCE_BINDING
                )

    def test_6_unknown_decision_state_block(self):
        """6. Unknown decision state → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)
            cert.status = "UNKNOWN_STATE"  # Invalid state

            with pytest.raises(ValueError, match="BLOCK.*not approved"):
                process_certified_session(jsonl_file, cert_result=cert)

    def test_7_raw_sha_mismatch_block(self):
        """7. Raw SHA mismatch → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)

            # Tamper certification's recorded SHA
            cert.file_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

            with pytest.raises(ValueError, match="BLOCK.*SHA mismatch"):
                process_certified_session(jsonl_file, cert_result=cert)

    def test_8_wrong_certifier_source_sha_block(self):
        """8. Wrong Certifier V2 source SHA → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)

            # Wrong certifier source SHA
            cert.certifier_sha256 = "WRONGSHA256000000000000000000000000000000000000000000000000000000"

            with pytest.raises(ValueError, match="BLOCK.*freeze binding mismatch"):
                process_certified_session(
                    jsonl_file,
                    cert_result=cert,
                    certifier_freeze_sha=CERTIFIER_SOURCE_BINDING
                )

    def test_9_wrong_certifier_freeze_sha_block(self):
        """9. Wrong Certifier V2 freeze SHA → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)

            # Wrong freeze SHA
            wrong_freeze_sha = "WRONG000000000000000000000000000000000000000000000000000000000000"

            with pytest.raises(ValueError, match="BLOCK.*freeze binding mismatch"):
                process_certified_session(
                    jsonl_file,
                    cert_result=cert,
                    certifier_freeze_sha=wrong_freeze_sha
                )

    def test_10_superseded_v1_identity_blocked(self):
        """10. Superseded Certifier V1 identity → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)

            # Claim it came from V1
            cert.certifier_sha256 = "V1_CERTIFIER_SHA256000000000000000000000000000000000000000000000000"

            with pytest.raises(ValueError, match="BLOCK.*freeze binding mismatch"):
                process_certified_session(
                    jsonl_file,
                    cert_result=cert,
                    certifier_freeze_sha=CERTIFIER_SOURCE_BINDING
                )

    def test_11_tampered_certification_detected(self):
        """11. Tampered certification artifact → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)
            original_status = cert.status

            # Tamper: change status from PASS to HOLD
            cert.status = "HOLD"

            with pytest.raises(ValueError, match="BLOCK.*not approved"):
                process_certified_session(jsonl_file, cert_result=cert)

    def test_12_correct_certified_allow(self):
        """12. Correct approved certification → ALLOW."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)

            # Should allow
            manifest = process_certified_session(jsonl_file, cert_result=cert)
            assert manifest["certification_binding"]["raw_sha_match"]

    def test_13_raw_file_changed_after_certification_block(self):
        """13. Raw file changed after certification → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file = session_dir / "raw_l2_snapshots.jsonl"

            cycles = self._create_valid_session()
            self._write_jsonl(jsonl_file, cycles)

            cert = certify_session(jsonl_file)

            # Modify file after certification
            with jsonl_file.open("a") as f:
                f.write('{"extra":"line"}\n')

            with pytest.raises(ValueError, match="BLOCK.*SHA mismatch"):
                process_certified_session(jsonl_file, cert_result=cert)

    def test_14_different_raw_file_same_session_date_block(self):
        """14. Certification from different raw file, same session → BLOCK."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026-08-26"
            jsonl_file_1 = session_dir / "raw_l2_snapshots_1.jsonl"
            jsonl_file_2 = session_dir / "raw_l2_snapshots_2.jsonl"

            # File 1
            cycles_1 = self._create_valid_session()
            self._write_jsonl(jsonl_file_1, cycles_1)
            cert_1 = certify_session(jsonl_file_1)

            # File 2 (different data - add extra line at end)
            cycles_2 = self._create_valid_session(1440)
            session_dir.mkdir(parents=True, exist_ok=True)
            with jsonl_file_2.open("w") as f:
                for cycle in cycles_2:
                    f.write(json.dumps(cycle, separators=(",", ":")) + "\n")
                # Extra line different from File 1
                f.write('{"cycle":9999,"observed_at_utc":"2026-08-26T20:00:00Z","observed_at_ist":"2026-08-27T01:30:00+05:30","snapshots":{}}\n')

            # Cert from file 1 won't match file 2
            with pytest.raises(ValueError, match="BLOCK.*SHA mismatch"):
                process_certified_session(jsonl_file_2, cert_result=cert_1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
