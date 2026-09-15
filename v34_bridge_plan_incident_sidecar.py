"""EA1-R1 step 5 — plan incident sidecar.

Closes the single defect the owner called out as more serious than the
429 itself: `halt_plan(plan, reason)` (v34_bridge_rebalance_transitions.py)
discards its own `reason` argument - `RebalancePlan` has no field to
store it. Terminal A's halt reason survived only because the underlying
ENGINE happened to log a HARD_HALT audit event before the plan halted;
Terminal B's genuinely did not (a bare bridge-side exception, invisible
to the engine's own audit.jsonl) - the exact cause could only ever be
inferred, never proven. That asymmetry is itself the defect: the plan
layer's own reason should never depend on whether the engine layer
happened to also be involved.

DELIBERATELY ADDITIVE, NOT A SCHEMA CHANGE: does not touch RebalancePlan
or RebalancePlanStore at all. A separate, append-only JSONL file,
`plan_events.jsonl`, living in the same PLAN_STORE_DIR as the plan files
themselves, keyed by target_id per record (not one-file-per-plan - an
operator watching one terminal's whole plan history over time is the
common case, not one plan in isolation). Same durable-write discipline as
every other append-only log in this project (open in append mode, write
one JSON line, flush + fsync before returning) - simpler than AuditSink's
own seq-recovery machinery on purpose: this is a secondary diagnostic
record, not the primary safety-critical audit trail (that's still
engine.audit, untouched).

Event vocabulary, deliberately small and closed:
  PLAN_HALTED - halt_plan() was called. Carries the real reason, for the
    first time ever persisted anywhere the plan itself can be found from.
  PLAN_RECONCILIATION_STARTED - an operator (or automatic recovery) began
    attempting to resolve a HALTED plan.
  PLAN_RECONCILIATION_PROVED_SAFE - the reconciliation's own verification
    (a fresh, real broker check) came back clean.
  PLAN_REARMED - the plan was unlatched and resumed (HALTED -> ENTERING).
  PLAN_ABANDONED - the plan was determined stale/no-longer-executable and
    deliberately not resumed (R0 SS7-adjacent staleness gate - see task
    #13, not yet built; this event exists now so the sidecar's vocabulary
    doesn't need another schema change once it is).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class PlanIncidentSidecar:
    """One shared file per PLAN_STORE_DIR (matches AuditSink's own one-
    file-per-runner-dir convention). `clock` is injectable for
    deterministic tests, same pattern as AuditSink."""

    def __init__(self, path: Path | str, *, clock: Optional[Any] = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock

    def _now_iso(self) -> str:
        now = self.clock.now() if self.clock is not None else datetime.now(timezone.utc)
        return now.astimezone(timezone.utc).isoformat()

    def _append(self, event_type: str, *, target_id: str, **fields: Any) -> None:
        record = {
            "event_type": event_type, "target_id": target_id,
            "timestamp_utc": self._now_iso(), "fields": fields,
        }
        line = json.dumps(record, default=str)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def log_halted(self, target_id: str, *, reason: str, **extra: Any) -> None:
        self._append("PLAN_HALTED", target_id=target_id, reason=reason, **extra)

    def log_reconciliation_started(self, target_id: str, *, operator_note: str, **extra: Any) -> None:
        self._append("PLAN_RECONCILIATION_STARTED", target_id=target_id, operator_note=operator_note, **extra)

    def log_reconciliation_proved_safe(self, target_id: str, **extra: Any) -> None:
        self._append("PLAN_RECONCILIATION_PROVED_SAFE", target_id=target_id, **extra)

    def log_rearmed(self, target_id: str, **extra: Any) -> None:
        self._append("PLAN_REARMED", target_id=target_id, **extra)

    def log_abandoned(self, target_id: str, *, reason: str, **extra: Any) -> None:
        self._append("PLAN_ABANDONED", target_id=target_id, reason=reason, **extra)

    def read_all(self) -> list[dict[str, Any]]:
        """Convenience for operators/dashboards - not used by any hot
        path in this project, only for after-the-fact review."""
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def events_for(self, target_id: str) -> list[dict[str, Any]]:
        return [r for r in self.read_all() if r.get("target_id") == target_id]
