"""Phase 3.3 — append-only JSONL audit sink.

New module. The frozen engine's audit contract isn't defined in any
frozen file to trace beyond its 23 call sites in institutional_engine_
v34_p02_multipos_candidate.py, all individually read before writing this
module - not sampled. See the findings below; they are the actual basis
for every design choice here, not general audit-logging best practice.

GOVERNING RULE: an audit record is durable evidence of a state
transition or safety event. It must never silently disappear, mutate, or
be reported as written when persistence failed.

FINDINGS FROM TRACING ALL 23 CALL SITES:

1. log() is called synchronously as a bare statement everywhere - no
   call site ever consumes a return value. This sink's log() returns
   None.

2. Exceptions ARE expected to propagate, as the general rule - but the
   frozen engine already decided, call site by call site, exactly what
   happens if a given log() call fails, and this sink must not second-
   guess any of it:
   - Two sites (_observe()'s OBSERVATION_FAILURE, trigger_hard_halt()'s
     own HARD_HALT) are wrapped in a LOCAL try/except Exception: pass -
     deliberately, so an audit failure can never block the halt/retry
     decision itself from completing. This sink does not need its own
     swallow-on-failure logic; it already exists exactly where needed.
   - Two sites (ENTRY_ORDER_RECOVERED/EMERGENCY_EXIT_ORDER_RECOVERED,
     inside _reconcile_unknown_entry/_exit) sit inside a try/except that
     treats any non-FAIL_CLOSED exception - including an audit failure -
     as an ordinary reconciliation-observation failure, consuming one
     bounded retry rather than halting immediately.
   - The remaining ~19 sites either escalate to a whole-engine
     RECONCILIATION_HALT via step()'s or reconcile_startup()'s own outer
     exception handler, or (request_entry()/request_exit()/
     clear_halt_and_reconcile(), none of which are called from inside
     step()) propagate fully uncaught out of the engine's public API to
     whatever called it - in this bridge, v34_bridge_runner_core.py,
     which already treats any exception from request_entry()/
     request_exit() as grounds to halt_plan().
   Conclusion: this sink's only correct job is to raise honestly on
   failure - never swallow, never retry, never report success when the
   write didn't durably happen. Every consequence of that raise is
   already handled correctly, per call site, by code already tested for
   exactly this.

3. log() genuinely can be called from inside a hard-halt path
   (trigger_hard_halt() itself calls it) - the recursion risk that
   creates is already neutralized at that exact call site by finding 2
   above, not something this sink needs to guard against itself.

4. Payload shapes, across all 23 call sites: every value is str, int, or
   None. No raw Decimal (already pre-stringified everywhere - e.g.
   price=str(ctx.entry_price)), no nested broker-order dicts. VERIFIED:
   no call site passes anything credential-shaped. One honest exception,
   not swept under the rug: OBSERVATION_FAILURE includes
   exception_message=str(exc) - text from a third-party SDK exception
   this module cannot prove is always credential-free the way the
   engine's own explicit fields can be. Per instruction, this sink does
   NOT implement content redaction (which risks destroying forensic
   evidence) - it implements a small, explicit denylist of field NAMES
   that look credential-shaped (_PROHIBITED_FIELD_NAMES), refused
   outright if ever present as a top-level key - a structural backstop
   for a channel that is narrow but not fully provable, not a broad
   scanner.

STORAGE FORMAT: append-only JSONL, one record per line. Each line is a
sink-generated envelope around the untouched engine payload, never
modifying the event itself:
    {"schema_version": 1, "seq": N, "timestamp_utc": "...",
     "event_type": "...", "fields": {...original kwargs, unmodified...}}
Deterministic per-line JSON (sort_keys=True).

`seq` is durable and monotonic ACROSS RESTARTS, not per-process - it
resumes from the existing file's last valid record when the sink is
constructed, never from 0 on an existing file. This is what makes two of
the required adversarial properties provable rather than assumed: two
log() calls with byte-identical fields still get distinct seq values
(never deduplicated), and a gap or out-of-order seq is itself evidence
something is wrong.

CRASH RECOVERY ON OPEN: a malformed trailing line is treated as the
expected signature of a crash mid-append - recoverable. It is truncated
(every complete record before it is preserved byte-for-byte) and the
sink resumes appending from there. A malformed line anywhere else in the
file is NOT treated this way - unexpected, unrecoverable without
operator intervention, and this sink refuses to construct at all rather
than silently continue past unexplained corruption.

No threading lock: nothing in this project is multi-threaded (every
engine/runner in this bridge is single-process, synchronous, poll-loop
based) - adding one would be inventing a concern this architecture
doesn't have.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Protocol

SCHEMA_VERSION = 1

_PROHIBITED_FIELD_NAMES = frozenset({
    "api_key", "access_token", "password", "secret", "token", "authorization",
})


class AuditSinkError(RuntimeError):
    """The audit sink refused to log an event, or refused to open an
    existing file: a malformed/non-serializable payload, a prohibited
    field name, or unexplained corruption found anywhere but the last
    line of an existing audit file. Always fail closed: raise, never
    silently drop, mutate, or truncate a complete record."""


class _Clock(Protocol):
    def now(self) -> datetime: ...


class AuditSink:
    """Durable, append-only JSONL audit log. log(event_type, **fields)
    matches the frozen engine's exact call convention
    (self.audit.log("EVENT", key=val, ...)) - see module docstring for
    why this sink deliberately does not swallow, retry, or reclassify a
    write failure; every frozen call site already has its own considered
    policy for that, one layer up."""

    def __init__(self, path: Path | str, *, clock: _Clock):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._next_seq = self._recover_and_determine_next_seq()

    def _recover_and_determine_next_seq(self) -> int:
        if not self.path.exists():
            return 0
        text = self.path.read_text(encoding="utf-8")
        if not text:
            return 0
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]  # drop the artifact of a trailing newline
        if not lines:
            return 0

        last_good_seq: Optional[int] = None
        last_good_count = 0
        for index, line in enumerate(lines):
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or "seq" not in record:
                    raise ValueError("record is missing 'seq'")
                seq = record["seq"]
                if not isinstance(seq, int) or isinstance(seq, bool):
                    raise ValueError(f"seq must be an integer, got {seq!r}")
            except Exception as exc:
                if index == len(lines) - 1:
                    # Crash mid-append - recoverable. Keep every complete
                    # record before this one, discard the partial tail.
                    self._truncate_to(lines, last_good_count)
                    break
                raise AuditSinkError(
                    f"AuditSink: unrecoverable corruption in {self.path} at line "
                    f"{index + 1} of {len(lines)} (not the last line - refusing to open): {exc}"
                ) from exc
            else:
                last_good_seq = seq
                last_good_count = index + 1

        return 0 if last_good_seq is None else last_good_seq + 1

    def _truncate_to(self, lines: List[str], count: int) -> None:
        content = "".join(line + "\n" for line in lines[:count])
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.path)

    def log(self, event_type: str, **fields: Any) -> None:
        if not isinstance(event_type, str) or not event_type.strip():
            raise AuditSinkError(f"AuditSink.log(): event_type must be a non-empty string, got {event_type!r}.")
        prohibited = set(fields) & _PROHIBITED_FIELD_NAMES
        if prohibited:
            raise AuditSinkError(
                f"AuditSink.log({event_type!r}): refusing field name(s) {sorted(prohibited)} - "
                "credential-shaped field names are never permitted in an audit payload."
            )

        record = {
            "schema_version": SCHEMA_VERSION,
            "seq": self._next_seq,
            "timestamp_utc": self.clock.now().astimezone(timezone.utc).isoformat(),
            "event_type": event_type,
            "fields": fields,
        }
        try:
            encoded = json.dumps(record, sort_keys=True)
        except (TypeError, ValueError) as exc:
            # Validated in memory before any file I/O - matching
            # v34_bridge_botstate_store.py's own validate-before-write
            # discipline. A non-serializable field must never leave a
            # half-written line on disk.
            raise AuditSinkError(f"AuditSink.log({event_type!r}): fields are not JSON-serializable: {exc}") from exc

        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(encoded + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        self._next_seq += 1
