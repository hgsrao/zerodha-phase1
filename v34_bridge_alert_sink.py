"""Phase 3.4A — alert sink.

New module. The frozen engine's entire alerting surface is ONE call
site: trigger_hard_halt()'s own

    try:
        self.alert.send("CRITICAL", reason)
    except Exception:
        pass

- the same local try/except Exception: pass discipline the audit call
right next to it uses (Phase 3.3), for the identical reason: alert
delivery must never be allowed to block or undo the halt itself. There
is no second call site anywhere in the frozen engine - no other
severity, no other payload shape, no deduplication logic (if
trigger_hard_halt somehow fired twice, the engine would just alert
twice - nothing in the frozen code assumes otherwise), no return value
ever consumed, no secrets in the payload (reason is a string the engine
already constructs itself from its own halt logic).

CONCLUSION, matching 3.1/3.2/3.3's own established discipline: this sink
must raise honestly on send() failure, never swallow it itself - the one
place that cannot tolerate a raised exception (trigger_hard_halt) already
shields itself. Duplicating that swallow here would be redundant at
best, and would silently override the engine's own considered choice
that every OTHER caller of alert.send() (there are none today, but
Phase 3.6's wiring or future call sites are not guaranteed the same
tolerance) gets to see a real failure.

DELIVERY MECHANISM: deterministic, local, file-backed (append-only
JSONL, matching v34_bridge_audit_sink.py's own envelope discipline and
durable-write mechanics exactly - same reasoning: an alert is meant to
survive a crash immediately after it fires, since it may be the last
thing this process ever records). No SMTP/Telegram/Slack/webhook/network
call anywhere in this module - the frozen bridge has never required one,
and introducing one now would be exactly the "generic notification
framework" this phase was explicitly told not to build. A real network-
backed delivery channel (if ever wanted) is a separate, later decision,
not something to smuggle into the primitive Phase 3.6 will wire.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 1


class AlertSinkError(RuntimeError):
    """The alert sink refused to record an alert, or refused to open an
    existing file. Always fail closed: raise, never silently drop or
    report success when the write didn't durably happen."""


class _Clock(Protocol):
    def now(self) -> datetime: ...


class AlertSink:
    """Durable, append-only JSONL alert log. send(level, message) matches
    the frozen engine's exact call convention
    (self.alert.send("CRITICAL", reason)) - see module docstring for why
    this sink deliberately does not swallow a delivery failure itself."""

    def __init__(self, path: Path | str, *, clock: _Clock):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock

    def send(self, level: str, message: str) -> None:
        if not isinstance(level, str) or not level.strip():
            raise AlertSinkError(f"AlertSink.send(): level must be a non-empty string, got {level!r}.")
        if not isinstance(message, str):
            raise AlertSinkError(f"AlertSink.send(): message must be a string, got {message!r}.")

        record = {
            "schema_version": SCHEMA_VERSION,
            "timestamp_utc": self.clock.now().astimezone(timezone.utc).isoformat(),
            "level": level,
            "message": message,
        }
        try:
            encoded = json.dumps(record, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise AlertSinkError(f"AlertSink.send(): payload is not JSON-serializable: {exc}") from exc

        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(encoded + "\n")
            fh.flush()
            os.fsync(fh.fileno())
