"""Phase 3.4B — runner lock.

New module. Traced the frozen engine's lock usage before writing this:
exactly one call site, `self._lock_ref = self.lock_provider.acquire()`
in __init__, called once, the return value stored but never read again
anywhere. `.release()` is never called by the frozen engine at all - lock
release is entirely the runner's own responsibility (or purely automatic
on process death), not something P02 itself manages.

GOVERNING PRINCIPLE (the user's own framing, carried verbatim because it
is the actual design driver): there must be exactly one production
runner instance with authority over the same durable state, and
termination must never create the appearance that execution stopped
safely when it did not.

WHY THIS IS A REAL OS-LEVEL LOCK, NOT A PID FILE: a PID-existence check
is exactly the fragile design this module avoids - a recycled PID can
produce a false positive ("looks free, isn't"), and deciding a lock is
"stale" by inspecting whether some PID is still running is inherently
racy (the process could die between the check and the decision). A real
OS-level exclusive lock (fcntl.flock on POSIX, msvcrt.locking on
Windows - both stdlib, no new dependency) sidesteps the entire question:
the kernel already knows, atomically and correctly, whether the lock is
still held, because it releases the lock automatically when the holding
process's file descriptors close for ANY reason - clean exit, an
uncaught exception, SIGKILL, an OOM-kill. There is no "is it stale"
question left to answer once the OS is the one answering it.

TWO SEPARATE FILES, ON PURPOSE - `<path>` (the OS-lock file) and
`<path>.meta.json` (the diagnostic metadata file). This was not the
original design; it replaced a single-file layout after empirical
testing on Windows showed msvcrt.locking() blocks read/write access to
the ENTIRE file from any other handle - including a second handle
opened by a different process for a plain read - once even one byte is
locked, not just the specific byte range requested. That made it
impossible for a refused acquire() to read "who holds it" out of the
same file the OS lock lives in. Splitting the concerns into two files
sidesteps the problem entirely and, on reflection, matches the user's
own framing more literally anyway: "Metadata can say who owns it; the
OS lock proves whether ownership is still active" reads as two
responsibilities, not one file serving both. The metadata file is never
locked, so it is always plainly readable; the lock file is never parsed
for content, so what's "in" it is irrelevant - only whether it can be
OS-locked matters.

METADATA IS DIAGNOSTIC ONLY, NEVER THE DECISION. The metadata file (pid,
instance_id, acquired_at) is written only after the OS lock is actually
held, purely so a refused acquisition can report *who* appears to hold
it. It is never consulted to decide whether acquisition succeeds - that
decision belongs to the OS lock alone, checked first, every time.
Corrupted or missing metadata therefore degrades diagnostics only (a
vaguer error message on refusal), never the accept/reject decision
itself.

PID ALONE IS NOT AN IDENTITY (the user's own explicit requirement): a
fresh, random UUID is generated per RunnerLock instance (not derived from
anything reusable) and recorded alongside the PID, so a recycled PID from
an unrelated process can never be mistaken for the same runner instance
that held the lock before - the UUID is the actual identity; PID is only
included because it's useful to a human reading the diagnostic.

NO STALE-LOCK DETECTION OR CLEANUP LOGIC EXISTS IN THIS MODULE, ON
PURPOSE. That is not a missing feature - it is the direct consequence of
using a real OS lock instead of a PID file. There is nothing to detect:
either the OS grants the lock (nothing else legitimately holds it) or it
doesn't (something still does). The metadata file is likewise never
proactively deleted on release() - it's stale-but-harmless diagnostic
residue, overwritten by whichever process next successfully acquires,
not a signal anything reads to make a decision.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import fcntl  # POSIX
    _PLATFORM = "posix"
except ImportError:
    import msvcrt  # Windows
    _PLATFORM = "windows"


class RunnerLockHeldError(RuntimeError):
    """acquire() was refused because the OS lock is already held by
    another process. Includes whatever diagnostic metadata could be read
    from the metadata file - degrades to a vaguer message if that file is
    missing or corrupt, but the refusal itself is never in doubt: it
    reflects the OS's own decision, not this module's interpretation of
    the metadata."""


class RunnerLockStateError(RuntimeError):
    """acquire()/release() was used incorrectly (e.g. release() before a
    successful acquire(), or a double acquire() on the same instance) -
    a programming-contract violation, not a lock-contention outcome."""


def _lock_exclusive_nonblocking(fh) -> None:
    if _PLATFORM == "posix":
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RunnerLockHeldError(str(exc)) from exc
    else:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RunnerLockHeldError(str(exc)) from exc


def _unlock(fh) -> None:
    if _PLATFORM == "posix":
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    else:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def _read_diagnostic_metadata(metadata_path: Path) -> Optional[Dict[str, Any]]:
    """Best-effort only - see module docstring. Never raises; a missing
    or corrupt file just means a vaguer diagnostic message, never a
    change in whether acquisition succeeds. Plain read - this file is
    never OS-locked, so no platform-specific access quirks apply."""
    try:
        raw = metadata_path.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


class RunnerLock:
    """The object passed to TradingEngineV34P02(lock=...). acquire() is
    what the frozen engine calls, exactly once, at construction - see
    module docstring for why its return value (self) only needs to stay
    referenced, not inspected. release() is never called by the frozen
    engine; it exists for a well-behaved runner's own clean-shutdown path
    (Phase 3.6), and for tests."""

    def __init__(self, path: Path | str, *, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.path.with_name(self.path.name + ".meta.json")
        self.instance_id = str(uuid.uuid4())
        self.pid = os.getpid()
        self._clock = clock
        self._fh = None

    def _now_iso(self) -> str:
        now = self._clock.now() if self._clock is not None else datetime.now(timezone.utc)
        return now.astimezone(timezone.utc).isoformat()

    def acquire(self) -> "RunnerLock":
        if self._fh is not None:
            raise RunnerLockStateError(f"RunnerLock({self.path}): already acquired by this same instance.")

        # Binary mode, content-agnostic - this file's only job is to be
        # something the OS can hold an exclusive lock on; nothing is ever
        # parsed out of it. Diagnostic identity lives in metadata_path
        # instead - see module docstring for why they're split.
        fh = open(self.path, "a+b")
        if os.fstat(fh.fileno()).st_size == 0:
            # msvcrt.locking() needs at least one byte to lock on Windows;
            # content is never read back, so any single byte will do.
            fh.write(b"\x00")
            fh.flush()

        try:
            _lock_exclusive_nonblocking(fh)
        except RunnerLockHeldError:
            fh.close()
            metadata = _read_diagnostic_metadata(self.metadata_path)
            if metadata:
                raise RunnerLockHeldError(
                    f"RunnerLock({self.path}): already held by pid={metadata.get('pid')!r} "
                    f"instance_id={metadata.get('instance_id')!r} acquired_at={metadata.get('acquired_at')!r}."
                ) from None
            raise RunnerLockHeldError(
                f"RunnerLock({self.path}): already held by another process "
                "(diagnostic metadata unavailable or unreadable)."
            ) from None

        # Lock is genuinely held now - safe to publish our own identity.
        # Written only after the OS lock succeeds, never before: a failed
        # acquire() must never overwrite the real holder's metadata.
        metadata = {"pid": self.pid, "instance_id": self.instance_id, "acquired_at": self._now_iso()}
        self.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        self._fh = fh
        return self  # matches `self._lock_ref = self.lock_provider.acquire()` - held alive by the caller's reference

    def release(self) -> None:
        """Never called by the frozen engine. Idempotent: releasing an
        already-released (or never-acquired) lock is a no-op, not an
        error - a clean-shutdown path shouldn't have to track whether it
        already released."""
        if self._fh is None:
            return
        _unlock(self._fh)
        self._fh.close()
        self._fh = None

    def read_metadata(self) -> Optional[Dict[str, Any]]:
        """Diagnostic only - see module docstring."""
        return _read_diagnostic_metadata(self.metadata_path)
