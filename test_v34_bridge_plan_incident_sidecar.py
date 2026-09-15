"""Tests for v34_bridge_plan_incident_sidecar.py (EA1-R1 step 5)."""
import json
from datetime import datetime, timezone

from v34_bridge_plan_incident_sidecar import PlanIncidentSidecar


class _FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now


NOW = datetime(2026, 8, 19, 5, 0, 0, tzinfo=timezone.utc)


def test_log_halted_persists_the_real_reason(tmp_path):
    sidecar = PlanIncidentSidecar(tmp_path / "plan_events.jsonl", clock=_FakeClock(NOW))
    sidecar.log_halted("V11_2026-08-17_ABC", reason="CRITICAL P0: SBIN entry submission rejected: Too many requests")

    events = sidecar.read_all()
    assert len(events) == 1
    assert events[0]["event_type"] == "PLAN_HALTED"
    assert events[0]["target_id"] == "V11_2026-08-17_ABC"
    assert events[0]["fields"]["reason"] == "CRITICAL P0: SBIN entry submission rejected: Too many requests"
    assert events[0]["timestamp_utc"] == NOW.isoformat()


def test_full_lifecycle_sequence_for_one_plan(tmp_path):
    sidecar = PlanIncidentSidecar(tmp_path / "plan_events.jsonl", clock=_FakeClock(NOW))
    target_id = "V11_2026-08-17_XYZ"

    sidecar.log_halted(target_id, reason="429 during submission", exception_class="NetworkException")
    sidecar.log_reconciliation_started(target_id, operator_note="verified zero live orders")
    sidecar.log_reconciliation_proved_safe(target_id, live_orders_checked=0)
    sidecar.log_rearmed(target_id)

    events = sidecar.events_for(target_id)
    assert [e["event_type"] for e in events] == [
        "PLAN_HALTED", "PLAN_RECONCILIATION_STARTED", "PLAN_RECONCILIATION_PROVED_SAFE", "PLAN_REARMED",
    ]
    assert events[0]["fields"]["exception_class"] == "NetworkException"
    assert events[1]["fields"]["operator_note"] == "verified zero live orders"
    assert events[2]["fields"]["live_orders_checked"] == 0


def test_abandoned_carries_a_reason_too(tmp_path):
    sidecar = PlanIncidentSidecar(tmp_path / "plan_events.jsonl", clock=_FakeClock(NOW))
    sidecar.log_abandoned("V11_2026-08-17_STALE", reason="signal_date stale relative to current monthly cycle")
    events = sidecar.read_all()
    assert events[0]["event_type"] == "PLAN_ABANDONED"
    assert "stale" in events[0]["fields"]["reason"]


def test_events_for_filters_to_one_target_id_only(tmp_path):
    sidecar = PlanIncidentSidecar(tmp_path / "plan_events.jsonl", clock=_FakeClock(NOW))
    sidecar.log_halted("PLAN_A", reason="x")
    sidecar.log_halted("PLAN_B", reason="y")
    sidecar.log_rearmed("PLAN_A")

    assert len(sidecar.events_for("PLAN_A")) == 2
    assert len(sidecar.events_for("PLAN_B")) == 1
    assert len(sidecar.read_all()) == 3


def test_read_all_on_a_missing_file_returns_empty_not_an_error(tmp_path):
    sidecar = PlanIncidentSidecar(tmp_path / "does_not_exist.jsonl")
    assert sidecar.read_all() == []


def test_appends_are_durable_across_separate_sidecar_instances(tmp_path):
    path = tmp_path / "plan_events.jsonl"
    PlanIncidentSidecar(path, clock=_FakeClock(NOW)).log_halted("PLAN_A", reason="x")
    # A second, independent instance (simulating a fresh process) sees it.
    second = PlanIncidentSidecar(path, clock=_FakeClock(NOW))
    assert len(second.read_all()) == 1


def test_real_wall_clock_is_used_when_no_clock_is_injected(tmp_path):
    sidecar = PlanIncidentSidecar(tmp_path / "plan_events.jsonl")
    sidecar.log_halted("PLAN_A", reason="x")
    events = sidecar.read_all()
    # Just proves it's a real, parseable, timezone-aware UTC ISO timestamp
    # - not asserting an exact value against real wall-clock time.
    parsed = datetime.fromisoformat(events[0]["timestamp_utc"])
    assert parsed.tzinfo is not None
