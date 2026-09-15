"""Phase 3.1 — durable AuthorizerRegistry store.

P02-E's `AuthorizerRegistry` (v34_p02_authorizer.py, frozen) is genuinely
crash-sensitive control state, not a cache: `authorize_and_persist()` is
store-first (a reservation is durably recorded *before* any broker call
is made), so losing it across a crash doesn't just lose bookkeeping - it
silently reopens a symbol/sector/capital slot that a real, in-flight or
already-submitted order may still be occupying. Until this module, the
only implementation of the store this project had was
`v34_p02_broker_adapter.AuthorizerRegistryStore` (also frozen) - an
in-memory placeholder that loses every reservation on process exit.

This is a NEW module, not an edit to either frozen file
(`v34_p02_authorizer.py` defines `AuthorizerRegistry`;
`v34_p02_broker_adapter.py` defines the in-memory placeholder) - both are
on the frozen-file list this whole project has respected throughout.
`AuthorizerRegistry` has no `__post_init__` slot this module could hook
into even if editing were allowed, so every invariant check below is
enforced by this module's own (de)serialization code, not by the frozen
dataclass itself.

`EntryReservation` (v34_p02_state.py, also frozen) already provides
`to_dict()`/`from_dict()` - fail-closed via `StateIntegrityError`, the
same convention `TradeContext`/`BotState` use. This module reuses that
directly rather than reimplementing per-reservation serialization; it
only adds the registry-level (dict-of-reservations, dict-of-held-symbols)
wrapping and the durable file mechanics.

CALLER CONTRACT, stated here because nothing enforces it automatically:
loading the registry is NOT sufficient by itself to make it safe to use.
`reconcile_reservations()` (v34_p02_authorizer.py) is the only function
that resolves a fingerprinted reservation's fate, and it needs a FRESH
broker orders/positions snapshot to do it - a reservation fingerprinted
right before a crash, with the actual `place_order()` call never having
happened, must be proven PROVABLY_STALE against real broker evidence, not
assumed either way. Whatever eventually drives engine startup MUST call
`reconcile_reservations()` against a live broker snapshot immediately
after `AuthorizerRegistryStore.load()`, before treating any fingerprinted
reservation as trustworthy. This module does not do that itself - it
depends on a broker snapshot fetch that is downstream of this store, and
wiring that sequencing is later Phase 3 work, not this subcomponent's.

Durable-write discipline matches every other store in this project:
temp-file + fsync + os.replace, so a crash mid-write can never leave a
half-written registry file. Single-file, not one-per-ID like
RebalancePlanStore - there is exactly one AuthorizerRegistry per running
engine, not many keyed by identity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_state import EntryReservation, StateIntegrityError


class AuthorizerRegistryStateError(RuntimeError):
    """Persisted AuthorizerRegistry state is missing, malformed, or
    violates an invariant. Wraps every failure mode uniformly - malformed
    JSON, wrong container types, a StateIntegrityError from a bad
    EntryReservation, or the reservations/held_symbols overlap structural
    invariant apply_decision() already enforces in memory - so a caller
    only ever needs to catch this one type for "the registry file cannot
    be trusted." Always fail closed: raise, never silently drop a
    reservation or default a missing field."""


def _registry_to_dict(registry: AuthorizerRegistry) -> Dict[str, Any]:
    return {
        "reservations": {symbol: r.to_dict() for symbol, r in registry.reservations.items()},
        "held_symbols": dict(registry.held_symbols),
    }


def _registry_from_dict(raw: Any) -> AuthorizerRegistry:
    if not isinstance(raw, dict):
        raise AuthorizerRegistryStateError(f"AuthorizerRegistry: expected an object, got {type(raw).__name__}.")
    required = {"reservations", "held_symbols"}
    missing = required.difference(raw)
    if missing:
        raise AuthorizerRegistryStateError(f"AuthorizerRegistry: missing required field(s) {sorted(missing)}.")

    reservations_raw = raw["reservations"]
    if not isinstance(reservations_raw, dict):
        raise AuthorizerRegistryStateError(f"AuthorizerRegistry.reservations: expected an object, got {type(reservations_raw).__name__}.")
    reservations: Dict[str, EntryReservation] = {}
    for symbol, r_raw in reservations_raw.items():
        try:
            reservation = EntryReservation.from_dict(r_raw)
        except StateIntegrityError as exc:
            raise AuthorizerRegistryStateError(f"AuthorizerRegistry.reservations[{symbol!r}]: {exc}") from exc
        if reservation.symbol != symbol:
            raise AuthorizerRegistryStateError(
                f"AuthorizerRegistry.reservations: key {symbol!r} disagrees with the "
                f"reservation's own symbol {reservation.symbol!r}."
            )
        reservations[symbol] = reservation

    held_symbols_raw = raw["held_symbols"]
    if not isinstance(held_symbols_raw, dict):
        raise AuthorizerRegistryStateError(f"AuthorizerRegistry.held_symbols: expected an object, got {type(held_symbols_raw).__name__}.")
    for symbol, sector in held_symbols_raw.items():
        if not isinstance(sector, str) or not sector.strip():
            raise AuthorizerRegistryStateError(f"AuthorizerRegistry.held_symbols[{symbol!r}]: expected a non-empty sector string, got {sector!r}.")
    held_symbols = dict(held_symbols_raw)

    # Structural invariant apply_decision() already enforces in memory
    # (REFUSING_DOUBLE_RESERVATION) - a corrupted or hand-edited file with
    # a symbol in both dicts must be caught here, not discovered later
    # when authorize_entry()'s gates 3/4/9 start behaving inconsistently.
    overlap = set(reservations) & set(held_symbols)
    if overlap:
        raise AuthorizerRegistryStateError(
            f"AuthorizerRegistry: symbol(s) {sorted(overlap)} present in both reservations "
            "and held_symbols - structurally impossible (mirrors apply_decision's own "
            "REFUSING_DOUBLE_RESERVATION guard)."
        )

    return AuthorizerRegistry(reservations=reservations, held_symbols=held_symbols)


class AuthorizerRegistryStore:
    """Durable, single-file store for AuthorizerRegistry. See module
    docstring for the caller contract this store does NOT itself
    enforce (reconcile_reservations() must run against a fresh broker
    snapshot immediately after load(), before the registry is trusted)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[AuthorizerRegistry]:
        """None means no prior state - the caller (not this store)
        decides that means starting from a fresh, empty
        AuthorizerRegistry(). A file that exists but cannot be parsed or
        validated always raises AuthorizerRegistryStateError; it is never
        treated as "missing"."""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Deliberately wrapped here (unlike some other stores in this
            # project, which let json.JSONDecodeError propagate raw) -
            # this file gates whether the engine can safely authorize any
            # entry at all, a wider blast radius than a single plan file,
            # and a clear, named exception is worth the small deviation
            # from that precedent.
            raise AuthorizerRegistryStateError(f"AuthorizerRegistry: corrupt JSON in {self.path}: {exc}") from exc
        return _registry_from_dict(raw)

    def save(self, registry: AuthorizerRegistry) -> None:
        # Defensive re-check before every write, even though every frozen
        # mutator (apply_decision, mark_reservation_submitted,
        # release_held_symbol, release_unsubmitted_reservation,
        # reconcile_reservations) already preserves this invariant by
        # construction - cheap, and it means a future bug on either side
        # of this boundary is caught before it is durably written, not
        # after it is loaded back and silently corrupts authorization.
        overlap = set(registry.reservations) & set(registry.held_symbols)
        if overlap:
            raise AuthorizerRegistryStateError(
                f"Refusing to save: symbol(s) {sorted(overlap)} present in both reservations "
                "and held_symbols - this registry object is already invalid in memory."
            )
        data = _registry_to_dict(registry)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.path)
