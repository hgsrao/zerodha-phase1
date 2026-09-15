"""Phase 3.2 — durable BotState store.

New module, not an edit to institutional_engine_v34_p02_multipos_
candidate.py or v34_p02_state.py (both frozen). `BotState`/`TradeContext`
already have `to_dict()`/`from_dict()` in the frozen v34_p02_state.py,
fail-closed via `StateIntegrityError` - this module wraps that in a
durable, atomic, versioned file envelope; it does not re-derive
per-field parsing.

TWO FINDINGS FROM TRACING THE FROZEN ENGINE, both load-bearing for how
this store must be used - not optional reading:

1. A SUCCESSFULLY LOADED BotState IS SYNTACTICALLY TRUSTWORTHY, NOT
   OPERATIONALLY SAFE. reconcile_startup() (institutional_engine_v34_p02_
   multipos_candidate.py) comprehensively handles every PositionStatus
   value a restart could find mid-flight (ENTRY_SUBMITTING, ENTRY_UNKNOWN,
   EXIT_SUBMITTING, EXIT_UNKNOWN, and every other legal value) - but it
   only runs automatically when step() observes state.status == STARTUP.
   clear_halt_and_reconcile() proves the intended pattern: it explicitly
   forces status back to STARTUP before returning, specifically so the
   next step() re-proves everything rather than trusting the clearance.
   But a plain process crash while status == RUNNING (no halt was ever
   triggered - an OOM-kill, a power loss) persists exactly that: RUNNING.
   __init__ only re-halts on an already-RECONCILIATION_HALT or
   clearance_required snapshot; a clean-looking RUNNING snapshot sails
   through unreconciled. THE CALLER THAT CONSTRUCTS THE ENGINE AFTER A
   GENUINE PROCESS RESTART MUST EXPLICITLY SET
   `engine.state.status = EngineStatus.STARTUP` (unless the engine has
   already halted itself in __init__) BEFORE THE FIRST step() CALL. This
   store does not do that itself - it is honest, mechanical persistence,
   nothing more - and forcing it here would hide a real orchestration
   requirement inside what looks like "just reading a file." This is
   unwired, real work for later Phase 3 (step 6, completing
   _build_engine()), named here so it cannot be silently forgotten.

2. A SAVE FAILURE CAN LEAVE THE IN-MEMORY ENGINE AHEAD OF DURABLE STATE,
   AND THAT IS CORRECT TO LEAVE UNCAUGHT, NOT A BUG TO PAPER OVER. Every
   frozen call site that mutates state calls self.store.save(self.state)
   with no fallback if it raises - including trigger_hard_halt() itself,
   the engine's own last line of defense. This module's save() must NOT
   add a try/except that swallows an underlying I/O failure: doing so
   would silently let the caller believe a mutation was durably recorded
   when it wasn't. Left to propagate raw, a save() failure crashes
   step(), which crashes the runner daemon (v34_bridge_runner_
   entrypoint.run_forever() only catches the one specific rate-limit
   exception shape and re-raises everything else) - and combined with
   finding (1) above, that is the actual safety mechanism: crash loudly,
   restart, force STARTUP, re-reconcile against fresh broker truth. A
   save() that quietly swallowed its own failure would be strictly worse
   than one that crashes the process.

Two candidate cross-field invariants were checked against the frozen
engine's own behavior and deliberately NOT enforced here, because there
is no direct evidence either is actually required:
- `clearance_required` and `status == RECONCILIATION_HALT` are
  deliberately independent halt triggers (__init__ checks either one
  alone) - not required to agree.
- An in-flight per-position status (ENTRY_SUBMITTING/ENTRY_UNKNOWN/
  EXIT_SUBMITTING/EXIT_UNKNOWN) is NOT restricted to EngineStatus.STARTUP
  - _step_entry_submit sets these during completely ordinary
  status == RUNNING operation too, not just at boot. A rule assuming
  otherwise would reject perfectly normal, mid-operation snapshots.

Schema versioning: BotState itself has no version field, and none can be
added to the frozen dataclass. Versioning instead lives in this store's
own file envelope: {"store_schema_version": 1, "state": {...BotState.
to_dict()...}} - checked strictly on load, separate from BotState's own
field-level validation.

EXCEPTION TAXONOMY - two types propagate from load(), deliberately not
one: BotStateStoreError for envelope-level problems (malformed JSON,
wrong container type, missing/unsupported store_schema_version, a
missing "state" key) - all of this module's own construction, nothing
the frozen engine defines. A StateIntegrityError raised by
BotState.from_dict()/TradeContext.from_dict() for a field-level problem
propagates UNCHANGED, not wrapped - it already identifies semantic
corruption precisely (which field, what was expected, what was found),
and wrapping it would only blur that precision without adding
information. This is a deliberate difference from
v34_bridge_authorizer_registry_store.py, which wraps everything under
one type; there is no strong enough project-wide reason to force the two
stores to agree on this, and StateIntegrityError's own specificity is
worth preserving here.

Durable-write discipline matches every other store in this project:
temp-file + fsync + os.replace. Malformed JSON is wrapped in a dedicated
BotStateStoreError (a deliberate deviation from letting
json.JSONDecodeError propagate raw, the same reasoning
v34_bridge_authorizer_registry_store.py already used, and arguably even
more justified here - a corrupt BotState file blocks the entire engine,
not one authorization decision). Directory-fsync (flushing the parent
directory's own entry after os.replace, a further POSIX durability
technique) was considered and deliberately not added: no other store in
this project does it, and this project runs on Windows (see environment
context), where os.open()/os.fsync() on a directory doesn't have the
same well-defined, portable meaning POSIX gives it - adding a platform-
conditional durability technique that might not even work correctly
here would be worse than not claiming it.

Before ever touching the temp file, save() re-parses and re-validates
its own just-built JSON string through the exact same _state_from_envelope()
path load() uses - proving the bytes about to be committed are actually
loadable before the old, last-known-good file is ever replaced. A
serialization or validation failure at this stage raises before any
filesystem write happens at all, so the previous durable file is left
completely untouched - not just "usually," structurally.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# StateIntegrityError is re-exported (not just imported for internal use)
# so a caller only needs one import line to catch both exception types
# this store's load() can raise - see the module docstring's exception
# taxonomy for why the two are kept distinct rather than collapsed.
from v34_p02_state import BotState, StateIntegrityError  # noqa: F401

STORE_SCHEMA_VERSION = 1


class BotStateStoreError(RuntimeError):
    """Persisted BotState *envelope* is missing, malformed, corrupt, or at
    a schema version this store doesn't understand - the failure modes
    this module's own file-format wrapping owns. A field-level problem
    inside the state itself raises StateIntegrityError instead (re-
    exported from v34_p02_state by this module - see the module
    docstring's exception taxonomy for why the two are kept separate, not
    collapsed into one). Always fail closed: raise, never silently
    default or drop a field."""


class BotStateStore:
    """Durable, single-file store for BotState. See module docstring for
    the two caller-contract findings this store does NOT itself enforce:
    forcing status back to STARTUP on a genuine restart, and never
    swallowing a save() failure."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[BotState]:
        """None means no prior state - legitimate first boot, exactly
        like AuthorizerRegistryStore.load(). The caller decides what a
        fresh BotState looks like (trading_day, etc.); this store has no
        opinion about that. A file that exists but cannot be parsed or
        validated always raises BotStateStoreError; it is never treated
        as "missing.\""""
        if not self.path.exists():
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BotStateStoreError(f"BotState: corrupt JSON in {self.path}: {exc}") from exc
        return _state_from_envelope(envelope)

    def save(self, state: BotState) -> None:
        """No try/except around the write itself - see module docstring
        finding (2). A caller whose save() call raises must treat that as
        a hard failure, not retry-and-continue.

        Validates its own output before ever touching the filesystem:
        builds the envelope, serializes it, then re-parses and
        re-validates that exact string through _state_from_envelope() -
        the same function load() uses - before opening the temp file.
        Anything wrong at this stage (a value that doesn't round-trip, a
        non-JSON-serializable field) raises here, with the previous
        durable file completely untouched, not merely "probably fine.\""""
        envelope = {"store_schema_version": STORE_SCHEMA_VERSION, "state": state.to_dict()}
        encoded = json.dumps(envelope, indent=2, sort_keys=True)
        _state_from_envelope(json.loads(encoded))  # prove it's loadable before committing it

        tmp_path = self.path.with_name(self.path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.path)


def _state_from_envelope(envelope: Any) -> BotState:
    if not isinstance(envelope, dict):
        raise BotStateStoreError(f"BotState envelope: expected an object, got {type(envelope).__name__}.")
    required = {"store_schema_version", "state"}
    missing = required.difference(envelope)
    if missing:
        raise BotStateStoreError(f"BotState envelope: missing required field(s) {sorted(missing)}.")

    version = envelope["store_schema_version"]
    if version != STORE_SCHEMA_VERSION:
        raise BotStateStoreError(
            f"BotState envelope: store_schema_version {version!r} is not supported "
            f"(this store understands version {STORE_SCHEMA_VERSION} only)."
        )

    # Deliberately NOT caught/wrapped here - see module docstring's
    # exception taxonomy. BotState.from_dict() already fails closed on a
    # non-dict "state" value (via its own _require_keys) with a precise
    # StateIntegrityError - this function adds nothing by re-checking
    # that itself, only risks disagreeing with BotState's own judgment
    # of what counts as valid.
    return BotState.from_dict(envelope["state"])
