"""V34-P02 authorizer + reservation registry (P02-E).

Contract, exactly as specified: the authorizer takes (an authoritative
broker/accounting snapshot from P02-D, durable reservation state, a
candidate entry intent) and returns either ALLOW + a durable reservation,
or DECLINE(reason) - structured, not a string the caller has to parse.
It never mutates engine halt state itself for a financial-policy
rejection; `AuthorizationDecision.halt_class` tells the caller which of
the two classes (spec §2) a decline belongs to, and it is the caller's
(the engine's, P02-C) job to act on that - exactly the same separation
`EntryPolicyDeclinedError` already establishes at the engine boundary.

Two-phase reserve/submit, deliberately decoupled:

1. `authorize_entry(...)` runs the full deterministic gate stack and, on
   ALLOW, returns a durable `EntryReservation` with NO fingerprint yet -
   capital/symbol/sector are held, but nothing has been submitted to the
   broker. The caller must persist the returned registry (store-first,
   the same discipline used throughout this project) before treating the
   entry as authorized.
2. If the caller proceeds to submit, `mark_reservation_submitted(...)`
   durably attaches the submission fingerprint BEFORE the broker call is
   made - mirroring exactly how the engine (P02-C) records its own
   fingerprint before ever calling `place_order()`.
3. If an ENTRY_LOCK trips between (1) and (2) - or the caller simply
   decides not to proceed - `release_unsubmitted_reservation(...)`
   releases the reservation, but ONLY when it can prove no fingerprint was
   ever attached; a fingerprinted reservation can only be resolved through
   `reconcile_reservations(...)`, which proves the outcome against real
   broker evidence rather than assuming release is safe.

`reconcile_reservations(...)` is the only place a fingerprinted
reservation's fate is decided, and it never infers execution from the
registry itself - the registry explains intent, broker orders/positions
are the only accepted proof (spec §1/§7). Every ambiguous case raises
`BrokerObservationContractViolation` rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from v34_p02_accounting import PortfolioRiskSnapshot
from v34_p02_state import (
    BrokerObservationContractViolation,
    Config,
    EntryReservation,
    canonical_decimal_string,
    order_matches_entry_fingerprint,
)

DEFAULT_ENTRY_TAG = "V3.4_P02_ENTRY"
TERMINAL_NO_FILL_STATUSES = {"REJECTED", "CANCELLED", "EXPIRED"}


# ---------------------------------------------------------------------------
# Registry / decision / candidate shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthorizerRegistry:
    """Durable authorizer-owned state. `reservations` covers everything
    from "capital just reserved, nothing submitted yet" through "order is
    live at the broker, unresolved" - both are still "occupying" a symbol/
    sector (spec §8's Held ∪ Pending ∪ Reserved). `held_symbols` is
    populated ONLY by `reconcile_reservations` after authoritative
    broker-position confirmation - never inferred, never assumed from an
    order's COMPLETE status alone."""
    reservations: Dict[str, EntryReservation] = field(default_factory=dict)
    held_symbols: Dict[str, str] = field(default_factory=dict)  # symbol -> sector


@dataclass(frozen=True)
class CandidateEntry:
    symbol: str
    sector: str
    quantity: int
    price: Decimal
    entry_tag: str = DEFAULT_ENTRY_TAG


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: Optional[str]
    halt_class: Optional[str]  # "ENGINE_HALT" | "ENTRY_LOCK" | None (None only when allowed)
    reservation: Optional[EntryReservation]
    committed_before: Decimal
    proposed_capital: Decimal
    committed_after: Decimal


def _decline(reason: str, *, halt_class: str, committed_before: Decimal, proposed_capital: Decimal) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=False, reason=reason, halt_class=halt_class, reservation=None,
        committed_before=committed_before, proposed_capital=proposed_capital, committed_after=committed_before,
    )


def _build_entry_fingerprint(candidate: CandidateEntry, *, product: str) -> Dict[str, Any]:
    # Deliberately the same shape the engine (P02-C) builds for the same
    # candidate inputs, via the same shared canonical_decimal_string - two
    # independent constructions of an identical fingerprint, not one
    # shared mutable object, so either side can prove intent from its own
    # state alone.
    return {
        "exchange": "NSE", "tradingsymbol": candidate.symbol, "transaction_type": "BUY",
        "product": product, "order_type": "LIMIT", "quantity": int(candidate.quantity),
        "price": canonical_decimal_string(candidate.price), "tag": candidate.entry_tag,
    }


# ---------------------------------------------------------------------------
# The gate stack (spec-ordered, deterministic)
# ---------------------------------------------------------------------------

def authorize_entry(
    *,
    candidate: CandidateEntry,
    registry: AuthorizerRegistry,
    snapshot: PortfolioRiskSnapshot,
    cfg: Config,
    reconciliation_clean: bool,
    kill_switch_active: bool,
    entries_today: int,
    seconds_since_last_entry: Optional[Decimal],
    turnover_today: Decimal,
) -> AuthorizationDecision:
    """Runs gates 1-10 in order and stops at the first failure. Never
    touches `registry` - returns a decision the caller applies via
    `apply_decision`/`authorize_and_persist`, so a decision can always be
    inspected/logged before anything durable changes."""
    proposed_capital = Decimal(candidate.quantity) * candidate.price
    committed_before = snapshot.committed_capital

    # Gate 1: engine/reconciliation integrity - the only ENGINE_HALT-class
    # decline this function can produce. Everything else is ENTRY_LOCK-class:
    # a business/policy "no," never an integrity problem.
    if not reconciliation_clean:
        return _decline("RECONCILIATION_NOT_CLEAN", halt_class="ENGINE_HALT", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 2: ENTRY_LOCK - computed financial/policy thresholds, or kill switch.
    if kill_switch_active:
        return _decline("KILL_SWITCH", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)
    daily_loss = max(-snapshot.daily_pnl, Decimal("0"))
    if daily_loss >= cfg.trial_capital * cfg.daily_hard_halt_pct:
        return _decline("DAILY_HARD_HALT", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)
    rolling_week_loss = max(-snapshot.rolling_week_pnl, Decimal("0"))
    if rolling_week_loss >= cfg.trial_capital * cfg.rolling_week_halt_pct:
        return _decline("ROLLING_WEEK_HALT", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)
    if snapshot.trial_drawdown >= cfg.trial_drawdown_halt_pct:
        return _decline("TRIAL_DRAWDOWN_HALT", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)
    if daily_loss >= cfg.trial_capital * cfg.daily_entry_lock_pct:
        return _decline("DAILY_ENTRY_LOCK", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 3: symbol already Held ∪ Pending ∪ Reserved - "currently held,"
    # never "bought today," so a symbol held since yesterday is still caught.
    if candidate.symbol in registry.held_symbols or candidate.symbol in registry.reservations:
        return _decline("SYMBOL_ALREADY_HELD", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 4: sector already Held ∪ Pending ∪ Reserved.
    occupied_sectors = set(registry.held_symbols.values()) | {r.sector for r in registry.reservations.values()}
    if candidate.sector in occupied_sectors:
        return _decline("SECTOR_ALREADY_HELD", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 5: daily entry limit.
    if entries_today >= cfg.max_daily_entries:
        return _decline("DAILY_ENTRY_LIMIT", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 6: cooldown / turnover.
    if seconds_since_last_entry is not None and seconds_since_last_entry < cfg.entry_cooldown_seconds:
        return _decline("ENTRY_COOLDOWN", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)
    if turnover_today + proposed_capital > cfg.trial_capital * cfg.daily_turnover_pct:
        return _decline("DAILY_TURNOVER_LIMIT", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 7: per-trade risk limit (sizing convention, spec §6 - see Config.stop_loss_pct).
    proposed_risk = proposed_capital * cfg.stop_loss_pct
    max_trade_risk = cfg.trial_capital * cfg.max_trade_risk_pct
    if proposed_risk > max_trade_risk:
        return _decline("TRADE_RISK_LIMIT", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 8: committed-capital ceiling - the one formula, spec §3/§4.
    committed_after = committed_before + proposed_capital
    if committed_after > cfg.trial_capital:
        return _decline("CAPITAL_CEILING", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 9: simultaneous-position ceiling, counted over Held ∪ Reserved
    # (a reservation already "spoken for" a slot even before broker confirmation).
    occupied_count = len(registry.held_symbols) + len(registry.reservations)
    if occupied_count >= cfg.max_simultaneous_positions:
        return _decline("SIMULTANEOUS_POSITION_LIMIT", halt_class="ENTRY_LOCK", committed_before=committed_before, proposed_capital=proposed_capital)

    # Gate 10: atomic durable reservation. No fingerprint yet - submission
    # is a separate, later step (mark_reservation_submitted).
    reservation = EntryReservation(
        symbol=candidate.symbol, sector=candidate.sector, reserved_capital=proposed_capital,
        reserved_at=datetime.now(timezone.utc).isoformat(), entry_fingerprint=None,
    )
    return AuthorizationDecision(
        allowed=True, reason=None, halt_class=None, reservation=reservation,
        committed_before=committed_before, proposed_capital=proposed_capital, committed_after=committed_after,
    )


def apply_decision(registry: AuthorizerRegistry, decision: AuthorizationDecision) -> AuthorizerRegistry:
    """Pure state transition: fold an ALLOW decision's reservation into a
    new registry. Declines return the registry unchanged - nothing to apply."""
    if not decision.allowed or decision.reservation is None:
        return registry
    if decision.reservation.symbol in registry.reservations or decision.reservation.symbol in registry.held_symbols:
        # Structural guard: authorize_entry's own gates 3/9 should make
        # this unreachable in single-threaded sequential use, but a
        # caller applying a stale decision against a registry that has
        # since changed must still fail closed rather than silently
        # overwrite an existing reservation.
        raise RuntimeError(
            f"REFUSING_DOUBLE_RESERVATION: {decision.reservation.symbol} is already "
            "reserved or held - this decision was computed against a stale registry."
        )
    new_reservations = dict(registry.reservations)
    new_reservations[decision.reservation.symbol] = decision.reservation
    return AuthorizerRegistry(reservations=new_reservations, held_symbols=dict(registry.held_symbols))


def authorize_and_persist(*, store, registry: AuthorizerRegistry, **authorize_kwargs) -> AuthorizationDecision:
    """The store-first wrapper: on ALLOW, the new registry is durably
    saved BEFORE this function returns. A caller that crashes between
    `authorize_entry` computing the decision and `store.save` completing
    never returns ALLOW at all - the reservation simply never existed as
    far as any other observer can tell, so a retry from the un-mutated
    registry is safe and simply re-runs the gates fresh. Once `store.save`
    has returned, the reservation is real: a second `authorize_and_persist`
    call for the same symbol, against the now-updated registry, is
    correctly rejected by gate 3."""
    decision = authorize_entry(registry=registry, **authorize_kwargs)
    if decision.allowed:
        new_registry = apply_decision(registry, decision)
        store.save(new_registry)  # if this raises, the caller never sees ALLOW
    return decision


def mark_reservation_submitted(registry: AuthorizerRegistry, symbol: str, fingerprint: Dict[str, Any]) -> AuthorizerRegistry:
    """Durably attach the submission fingerprint to an existing
    reservation, called BEFORE the broker order call is made - the
    authorizer-side mirror of the engine's own store-first fingerprint
    discipline (P02-C)."""
    reservation = registry.reservations.get(symbol)
    if reservation is None:
        raise RuntimeError(f"CANNOT_MARK_SUBMITTED: no reservation exists for {symbol}.")
    if reservation.entry_fingerprint is not None and reservation.entry_fingerprint != fingerprint:
        raise RuntimeError(f"CANNOT_MARK_SUBMITTED: {symbol} already has a different, immutable submission fingerprint.")
    updated = EntryReservation(
        symbol=reservation.symbol, sector=reservation.sector, reserved_capital=reservation.reserved_capital,
        reserved_at=reservation.reserved_at, entry_fingerprint=fingerprint,
    )
    new_reservations = dict(registry.reservations)
    new_reservations[symbol] = updated
    return AuthorizerRegistry(reservations=new_reservations, held_symbols=dict(registry.held_symbols))


def release_held_symbol(registry: AuthorizerRegistry, symbol: str) -> AuthorizerRegistry:
    """Remove `symbol` from held_symbols - the exit-side mirror of
    reconcile_reservations' PROMOTE_TO_HELD. Called once the engine's own
    reconciliation (P02-C) has independently proven the position is fully
    closed (broker quantity zero after a verified exit) - this function
    does not re-verify that proof itself, since the safety decision
    already happened in the engine's exit flow; it is bookkeeping only,
    so a closed symbol doesn't block re-entry to it or its sector forever."""
    if symbol not in registry.held_symbols:
        return registry
    new_held = dict(registry.held_symbols)
    del new_held[symbol]
    return AuthorizerRegistry(reservations=dict(registry.reservations), held_symbols=new_held)


def release_unsubmitted_reservation(registry: AuthorizerRegistry, symbol: str) -> AuthorizerRegistry:
    """Release a reservation cleanly - but only when provably safe: no
    fingerprint means no submission could ever have reached the broker
    (the engine always records a fingerprint durably before calling
    place_order - P02-C), so releasing here can never orphan a real order.
    A fingerprinted reservation must instead be resolved through
    `reconcile_reservations`, which proves the outcome against broker
    evidence rather than assuming release is safe."""
    reservation = registry.reservations.get(symbol)
    if reservation is None:
        return registry
    if reservation.entry_fingerprint is not None:
        raise RuntimeError(
            f"CANNOT_RELEASE: {symbol} has a submission fingerprint - a broker order may "
            "already exist. Use reconcile_reservations to resolve this with proof, not release()."
        )
    new_reservations = dict(registry.reservations)
    del new_reservations[symbol]
    return AuthorizerRegistry(reservations=new_reservations, held_symbols=dict(registry.held_symbols))


# ---------------------------------------------------------------------------
# Reconciliation - the only place a fingerprinted reservation's fate is
# decided, and only from broker evidence, never inferred from the registry.
# ---------------------------------------------------------------------------

def _reconcile_one_reservation(symbol: str, reservation: EntryReservation, *, orders: List[Dict[str, Any]], positions: List[Dict[str, Any]], product: str) -> str:
    """Returns one of UNSUBMITTED / PENDING_AT_BROKER / PROMOTE_TO_HELD /
    PROVABLY_STALE. Raises BrokerObservationContractViolation on any
    disagreement it cannot prove one way or the other - never guesses."""
    if reservation.entry_fingerprint is None:
        return "UNSUBMITTED"

    matches = [o for o in orders if isinstance(o, dict) and order_matches_entry_fingerprint(o, reservation.entry_fingerprint)]
    if len(matches) > 1:
        raise BrokerObservationContractViolation(
            f"{symbol}: multiple broker orders match this reservation's fingerprint - cannot resolve unambiguously."
        )
    if not matches:
        # Proof, not inference: the broker's own order history (all
        # statuses, not just active ones) shows nothing matching this
        # exact fingerprint anywhere. Only a crash strictly between
        # mark_reservation_submitted and the broker call itself produces
        # this - safe to clear.
        return "PROVABLY_STALE"

    order = matches[0]
    status = order.get("status")
    try:
        filled = int(order.get("filled_quantity", 0) or 0)
        quantity = int(order.get("quantity", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise BrokerObservationContractViolation(f"{symbol}: matching order has malformed quantity/filled_quantity.") from exc

    if status in TERMINAL_NO_FILL_STATUSES and filled == 0:
        return "PROVABLY_STALE"

    if status == "COMPLETE" and filled == quantity and filled > 0:
        matching_pos = [
            p for p in positions
            if isinstance(p, dict) and p.get("tradingsymbol") == symbol
            and p.get("product") == product
            and _safe_int(p.get("quantity")) == filled
        ]
        if len(matching_pos) != 1:
            raise BrokerObservationContractViolation(
                f"{symbol}: entry order COMPLETE but no uniquely matching broker position was "
                "found - refusing to promote to held without authoritative confirmation."
            )
        return "PROMOTE_TO_HELD"

    # Still active, partially filled, or some other non-terminal state:
    # genuinely pending, not stale, not yet promotable.
    return "PENDING_AT_BROKER"


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reconcile_reservations(registry: AuthorizerRegistry, *, orders: List[Dict[str, Any]], positions: List[Dict[str, Any]], product: str) -> AuthorizerRegistry:
    """Resolve every fingerprinted reservation against broker evidence.
    Fails fast (raises) on the first ambiguous case - whole-batch, not
    partial, matching the engine's own reconciliation discipline (P02-C):
    prove state, then persist; if proof fails, stop."""
    new_reservations = dict(registry.reservations)
    new_held = dict(registry.held_symbols)
    for symbol, reservation in registry.reservations.items():
        outcome = _reconcile_one_reservation(symbol, reservation, orders=orders, positions=positions, product=product)
        if outcome == "PROVABLY_STALE":
            del new_reservations[symbol]
        elif outcome == "PROMOTE_TO_HELD":
            del new_reservations[symbol]
            new_held[symbol] = reservation.sector
        # UNSUBMITTED / PENDING_AT_BROKER: left exactly as-is.
    return AuthorizerRegistry(reservations=new_reservations, held_symbols=new_held)
