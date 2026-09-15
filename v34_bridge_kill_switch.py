"""Phase 3.6 — kill switch.

New module. `AuthorizationContext.kill_switch_active` is a field the
frozen authorizer (`v34_p02_authorizer.authorize_entry`, gate 2) already
consumes as an opaque boolean - traced: the frozen engine/authorizer has
no kill-switch concept or mechanism of its own anywhere; it is entirely
an external operator control this project must supply.

Deliberately the simplest durable primitive that could work: a sentinel
file's existence. No new class, no daemon, no polling process - an
operator (or an ops script) creates the file to halt new entries and
deletes it to resume, and this module's only job is a safe, honest
existence check. This mirrors the same "an OS-level primitive over
inventing a database" discipline v34_bridge_runner_lock.py already
established for a different concern.

Deliberately does NOT block/limit anything beyond what
`kill_switch_active` already means to the authorizer: it only ever
affects new-entry authorization (gate 2, ENTRY_LOCK-class) - it must
never be treated as an engine-level halt. A kill switch is a business/
policy "no," not a safety integrity problem; conflating the two would
misclassify it exactly the way `authorize_entry`'s own halt_class
separation (ENGINE_HALT vs ENTRY_LOCK) already refuses to.
"""

from __future__ import annotations

from pathlib import Path


def is_kill_switch_active(path: Path | str) -> bool:
    """True iff the sentinel file exists. No content is read or
    validated - existence alone is the signal, exactly like a PID file's
    existence would be for a different (rejected, see v34_bridge_runner_
    lock.py) purpose. Deliberately tolerant of the file being a directory
    or otherwise unusual - `Path.exists()` only, no attempt to open or
    parse it, so a kill switch can never itself become a new source of
    fail-open ambiguity."""
    return Path(path).exists()
