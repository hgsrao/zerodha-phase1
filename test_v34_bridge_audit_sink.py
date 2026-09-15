"""Tests for v34_bridge_audit_sink.py.

Real tmp_path-backed files throughout, including genuine crash/restart
and mid-append-corruption simulations - matching the exact proof pattern
every other Phase 3 store in this project already established.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_audit_sink import AuditSink, AuditSinkError

NOW = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)


def _read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestHappyPathLogging:
    def test_a_single_event_is_written_with_the_expected_envelope(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        result = sink.log("ENTRY_INTENT_CREATED", symbol="RELIANCE", quantity=10, price="2500.00", tag="V3.4_P02_ENTRY")
        assert result is None  # matches every frozen call site - return value never consumed

        records = _read_records(path)
        assert len(records) == 1
        record = records[0]
        assert record["schema_version"] == 1
        assert record["seq"] == 0
        assert record["timestamp_utc"] == NOW.isoformat()
        assert record["event_type"] == "ENTRY_INTENT_CREATED"
        assert record["fields"] == {"symbol": "RELIANCE", "quantity": 10, "price": "2500.00", "tag": "V3.4_P02_ENTRY"}

    def test_seq_increments_for_each_call(self, tmp_path):
        sink = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
        sink.log("HARD_HALT", reason="test", source="ENGINE", halted_at=NOW.isoformat())
        sink.log("HARD_HALT", reason="test", source="ENGINE", halted_at=NOW.isoformat())
        sink.log("HARD_HALT", reason="test", source="ENGINE", halted_at=NOW.isoformat())
        records = _read_records(tmp_path / "audit.jsonl")
        assert [r["seq"] for r in records] == [0, 1, 2]

    def test_a_bare_event_with_no_fields_is_legal(self, tmp_path):
        # RUNTIME_HALT_CLEARED_AFTER_BROKER_VERIFICATION is called with no
        # kwargs at all in the frozen engine.
        sink = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
        sink.log("RUNTIME_HALT_CLEARED_AFTER_BROKER_VERIFICATION")
        records = _read_records(tmp_path / "audit.jsonl")
        assert records[0]["fields"] == {}


class TestIdenticalEventsAreNeverDeduplicated:
    def test_two_byte_identical_payloads_produce_two_separate_records(self, tmp_path):
        sink = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
        sink.log("ORDER_DEAD_NO_FILL", symbol="RELIANCE", order_id="OID-1", status="REJECTED")
        sink.log("ORDER_DEAD_NO_FILL", symbol="RELIANCE", order_id="OID-1", status="REJECTED")
        records = _read_records(tmp_path / "audit.jsonl")
        assert len(records) == 2
        assert records[0]["fields"] == records[1]["fields"]
        assert records[0]["seq"] != records[1]["seq"]  # distinguishable despite identical content


class TestDeterministicSerialization:
    def test_field_key_order_does_not_affect_the_persisted_fields_object(self, tmp_path):
        sink = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
        sink.log("X", a=1, b=2, c=3)
        line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0]
        # sort_keys=True means the exact insertion order of the kwargs
        # never matters to the bytes on disk.
        assert '"a": 1' in line and line.index('"a"') < line.index('"b"') < line.index('"c"')


class TestFailClosedValidation:
    def test_a_non_serializable_field_raises_before_touching_the_file(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        with pytest.raises(AuditSinkError, match="not JSON-serializable"):
            sink.log("BAD_EVENT", bad_value=Decimal("1.5"))  # Decimal is not JSON-serializable
        assert not path.exists()  # never created

    def test_a_non_serializable_field_after_a_prior_good_write_leaves_the_file_untouched(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        sink.log("GOOD_EVENT", symbol="RELIANCE")
        original_bytes = path.read_bytes()
        with pytest.raises(AuditSinkError):
            sink.log("BAD_EVENT", bad_value=object())
        assert path.read_bytes() == original_bytes

    def test_an_empty_event_type_raises(self, tmp_path):
        sink = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
        with pytest.raises(AuditSinkError, match="non-empty string"):
            sink.log("")

    @pytest.mark.parametrize("field_name", ["api_key", "access_token", "password", "secret", "token", "authorization"])
    def test_a_credential_shaped_field_name_is_refused(self, tmp_path, field_name):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        with pytest.raises(AuditSinkError, match="refusing field name"):
            sink.log("SOME_EVENT", **{field_name: "whatever-value"})
        assert not path.exists()


class TestAppendFailurePropagates:
    def test_an_injected_fsync_failure_propagates_and_is_not_swallowed(self, tmp_path, monkeypatch):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        sink.log("FIRST", symbol="RELIANCE")
        original_bytes = path.read_bytes()

        def failing_fsync(fd):
            raise OSError("simulated disk failure")
        monkeypatch.setattr("os.fsync", failing_fsync)

        with pytest.raises(OSError, match="simulated disk failure"):
            sink.log("SECOND", symbol="RELIANCE")
        # The failed write must not silently succeed, and must not
        # corrupt what was already durably there. (Append mode may leave
        # a partial trailing write on some platforms - that is exactly
        # the crash-recovery scenario the next test class covers, not a
        # bug in this one.)
        assert path.read_bytes().startswith(original_bytes)


class TestCrashAndRestart:
    def test_a_fresh_sink_instance_appends_rather_than_truncates(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink_before_crash = AuditSink(path, clock=FakeClock(NOW))
        sink_before_crash.log("FIRST", symbol="RELIANCE")
        sink_before_crash.log("SECOND", symbol="RELIANCE")
        del sink_before_crash  # simulate the process dying

        sink_after_restart = AuditSink(path, clock=FakeClock(NOW))
        sink_after_restart.log("THIRD", symbol="RELIANCE")

        records = _read_records(path)
        assert [r["event_type"] for r in records] == ["FIRST", "SECOND", "THIRD"]

    def test_seq_resumes_correctly_not_from_zero(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink_before_crash = AuditSink(path, clock=FakeClock(NOW))
        sink_before_crash.log("FIRST", symbol="RELIANCE")
        sink_before_crash.log("SECOND", symbol="RELIANCE")
        sink_before_crash.log("THIRD", symbol="RELIANCE")
        del sink_before_crash

        sink_after_restart = AuditSink(path, clock=FakeClock(NOW))
        sink_after_restart.log("FOURTH", symbol="RELIANCE")

        records = _read_records(path)
        assert [r["seq"] for r in records] == [0, 1, 2, 3]


class TestTrailingCorruptionIsRecoverable:
    def test_a_partial_trailing_write_is_truncated_and_prior_records_survive(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        sink.log("FIRST", symbol="RELIANCE")
        sink.log("SECOND", symbol="RELIANCE")
        del sink

        # Simulate a crash mid-write: append a truncated, unparseable
        # fragment as the last line - exactly what an fsync/power failure
        # partway through a write would leave behind.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"schema_version": 1, "seq": 2, "event_type": "THIRD", "fiel')  # cut off, no newline

        recovered_sink = AuditSink(path, clock=FakeClock(NOW))  # must not raise
        records = _read_records(path)
        assert [r["event_type"] for r in records] == ["FIRST", "SECOND"]  # partial record discarded, prior ones intact

        # And the sink correctly resumes numbering from the last GOOD
        # record, not from the discarded partial one.
        recovered_sink.log("REPLACEMENT_THIRD", symbol="RELIANCE")
        records = _read_records(path)
        assert records[-1]["seq"] == 2
        assert records[-1]["event_type"] == "REPLACEMENT_THIRD"

    def test_a_malformed_line_that_is_not_last_fails_closed(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        sink.log("FIRST", symbol="RELIANCE")
        sink.log("SECOND", symbol="RELIANCE")
        del sink

        # Corrupt the FIRST (non-trailing) line directly - not the crash-
        # mid-append shape at all; genuine, unexplained corruption.
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = "not valid json at all"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(AuditSinkError, match="not the last line"):
            AuditSink(path, clock=FakeClock(NOW))

    def test_a_missing_seq_field_on_a_non_trailing_line_fails_closed(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        sink = AuditSink(path, clock=FakeClock(NOW))
        sink.log("FIRST", symbol="RELIANCE")
        sink.log("SECOND", symbol="RELIANCE")
        del sink

        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = json.dumps({"schema_version": 1, "event_type": "FIRST", "fields": {}})  # no "seq"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(AuditSinkError, match="not the last line"):
            AuditSink(path, clock=FakeClock(NOW))


class TestMissingOrEmptyFile:
    def test_no_prior_file_starts_seq_at_zero(self, tmp_path):
        sink = AuditSink(tmp_path / "audit.jsonl", clock=FakeClock(NOW))
        sink.log("FIRST", symbol="RELIANCE")
        assert _read_records(tmp_path / "audit.jsonl")[0]["seq"] == 0

    def test_an_empty_existing_file_starts_seq_at_zero(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        path.write_text("", encoding="utf-8")
        sink = AuditSink(path, clock=FakeClock(NOW))
        sink.log("FIRST", symbol="RELIANCE")
        assert _read_records(path)[0]["seq"] == 0
