"""Phase 3.5 — durable DailyAccountingState store.

Same shape and discipline as v34_bridge_botstate_store.py /
v34_bridge_authorizer_registry_store.py: temp-file + fsync + os.replace,
save() re-validates its own encoded output through the same
_state_from_envelope() path load() uses before ever touching the
filesystem, no try/except swallowing an I/O failure - a save() failure
must propagate uncaught, exactly like every other durable store in this
project, so an accounting update that looked like it succeeded never
silently wasn't recorded.

Envelope-level problems (malformed JSON, wrong container type, missing/
unsupported store_schema_version) raise DailyAccountingStoreError.
Field-level problems raise DailyAccountingIntegrityError (re-exported
from v34_bridge_daily_accounting_state, same two-exception-type pattern
BotStateStore uses) - kept separate rather than collapsed into one, for
the same reason: a field-level error already identifies precisely which
value was wrong, and wrapping it would blur that.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from v34_bridge_daily_accounting_state import (  # noqa: F401 (re-exported)
    DailyAccountingIntegrityError,
    DailyAccountingState,
)

STORE_SCHEMA_VERSION = 1


class DailyAccountingStoreError(RuntimeError):
    """Persisted DailyAccountingState *envelope* is missing, malformed,
    corrupt, or at an unsupported schema version - this module's own
    file-format problems, not a field-level one (see module docstring).
    Always fail closed: raise, never silently default a missing daily-
    accounting record to zero."""


class DailyAccountingStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[DailyAccountingState]:
        """None means no prior state - legitimate only on a genuine
        first-ever boot of the trial, before initial_daily_accounting_
        state() has ever been saved. A file that exists but cannot be
        parsed or validated always raises, never treated as "missing" -
        silently defaulting a corrupt accounting record to a fresh zero
        state would erase real, already-realized P&L history."""
        if not self.path.exists():
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DailyAccountingStoreError(f"DailyAccountingState: corrupt JSON in {self.path}: {exc}") from exc
        return _state_from_envelope(envelope)

    def save(self, state: DailyAccountingState) -> None:
        envelope = {"store_schema_version": STORE_SCHEMA_VERSION, "state": state.to_dict()}
        encoded = json.dumps(envelope, indent=2, sort_keys=True)
        _state_from_envelope(json.loads(encoded))  # prove it's loadable before committing it

        tmp_path = self.path.with_name(self.path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.path)


def _state_from_envelope(envelope: Any) -> DailyAccountingState:
    if not isinstance(envelope, dict):
        raise DailyAccountingStoreError(f"DailyAccountingState envelope: expected an object, got {type(envelope).__name__}.")
    required = {"store_schema_version", "state"}
    missing = required.difference(envelope)
    if missing:
        raise DailyAccountingStoreError(f"DailyAccountingState envelope: missing required field(s) {sorted(missing)}.")

    version = envelope["store_schema_version"]
    if version != STORE_SCHEMA_VERSION:
        raise DailyAccountingStoreError(
            f"DailyAccountingState envelope: store_schema_version {version!r} is not supported "
            f"(this store understands version {STORE_SCHEMA_VERSION} only)."
        )

    return DailyAccountingState.from_dict(envelope["state"])
