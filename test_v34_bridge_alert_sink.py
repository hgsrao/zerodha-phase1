"""Tests for v34_bridge_alert_sink.py.

The frozen engine's entire alerting surface is one call:
self.alert.send("CRITICAL", reason). These tests exercise exactly that
shape, plus the fail-closed/durability properties every other Phase 3
store in this project already established.
"""

import json
from datetime import datetime, timezone

import pytest

from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_alert_sink import AlertSink, AlertSinkError

NOW = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)


def _read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestHappyPath:
    def test_matches_the_frozen_engines_exact_call_shape(self, tmp_path):
        path = tmp_path / "alerts.jsonl"
        sink = AlertSink(path, clock=FakeClock(NOW))
        result = sink.send("CRITICAL", "Reconciliation Failure: RELIANCE broker position ambiguous")
        assert result is None  # never consumed by the frozen engine

        records = _read_records(path)
        assert len(records) == 1
        assert records[0]["level"] == "CRITICAL"
        assert records[0]["message"] == "Reconciliation Failure: RELIANCE broker position ambiguous"
        assert records[0]["timestamp_utc"] == NOW.isoformat()
        assert records[0]["schema_version"] == 1

    def test_repeated_alerts_are_never_deduplicated(self, tmp_path):
        # Nothing in the frozen engine assumes deduplication - if
        # trigger_hard_halt somehow fired twice, it would alert twice.
        path = tmp_path / "alerts.jsonl"
        sink = AlertSink(path, clock=FakeClock(NOW))
        sink.send("CRITICAL", "same reason")
        sink.send("CRITICAL", "same reason")
        records = _read_records(path)
        assert len(records) == 2
        assert records[0] == records[1]  # identical content, both kept

    def test_a_fresh_sink_instance_appends_rather_than_truncates(self, tmp_path):
        path = tmp_path / "alerts.jsonl"
        sink_before_crash = AlertSink(path, clock=FakeClock(NOW))
        sink_before_crash.send("CRITICAL", "first")
        del sink_before_crash

        sink_after_restart = AlertSink(path, clock=FakeClock(NOW))
        sink_after_restart.send("CRITICAL", "second")

        records = _read_records(path)
        assert [r["message"] for r in records] == ["first", "second"]


class TestFailClosedValidation:
    def test_empty_level_raises_before_touching_the_file(self, tmp_path):
        path = tmp_path / "alerts.jsonl"
        sink = AlertSink(path, clock=FakeClock(NOW))
        with pytest.raises(AlertSinkError, match="non-empty string"):
            sink.send("", "message")
        assert not path.exists()

    def test_non_string_message_raises(self, tmp_path):
        sink = AlertSink(tmp_path / "alerts.jsonl", clock=FakeClock(NOW))
        with pytest.raises(AlertSinkError, match="must be a string"):
            sink.send("CRITICAL", 12345)


class TestSendFailurePropagatesUncaught:
    def test_an_injected_fsync_failure_propagates_and_is_not_swallowed(self, tmp_path, monkeypatch):
        # This sink must NOT swallow its own failure - trigger_hard_halt()
        # already does that at the one call site that needs it. If this
        # sink also swallowed, a caller with no such shield (a future
        # call site, or Phase 3.6's own wiring) would never find out.
        path = tmp_path / "alerts.jsonl"
        sink = AlertSink(path, clock=FakeClock(NOW))
        sink.send("CRITICAL", "first")
        original_bytes = path.read_bytes()

        def failing_fsync(fd):
            raise OSError("simulated disk failure")
        monkeypatch.setattr("os.fsync", failing_fsync)

        with pytest.raises(OSError, match="simulated disk failure"):
            sink.send("CRITICAL", "second")
        assert path.read_bytes().startswith(original_bytes)
