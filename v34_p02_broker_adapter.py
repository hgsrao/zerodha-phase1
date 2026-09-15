"""V34-P02 broker adapter wiring (P02-F).

The glue that makes `institutional_engine_v34_p02_multipos_candidate.py`
(P02-C), `v34_p02_accounting.py` (P02-D), and `v34_p02_authorizer.py`
(P02-E) actually operate together end to end, exactly as the engine's
`request_entry()`/`step()` already expect: something the engine calls
`self.broker.place_order(...)` on, which internally runs the full
authorization gate stack before ever reaching a real (or, in every test
here, mocked) broker call.

No new policy is introduced here - every decision is delegated to
`authorize_entry`/`authorize_and_persist` (P02-E) using numbers from
`build_portfolio_snapshot` (P02-D). This module's only job is sequencing:
snapshot -> authorize -> durably mark submitted -> only then call the raw
broker, so the store-first discipline already used throughout P02-C/P02-E
extends across the adapter boundary instead of stopping at it.

`AuthorizationContext` deliberately stays a lightweight, caller-supplied
bundle rather than a new persisted runner-state format - assembling and
durably persisting day-scoped counters, cumulative P&L, and the daily
checkpoint across restarts is runner-state plumbing that belongs to a
later production-wiring pass, not P02-F's scope (prove the lifecycle
wiring, not invent new policy or a new state file).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from kite_request_governor import DEFAULT as GOV_DEFAULT
from kite_request_governor import ORDER as GOV_ORDER
from kite_request_governor import QUOTE as GOV_QUOTE
from kite_request_governor import KiteRequestGovernor
from v34_p02_accounting import DailyAccountingCheckpoint, build_portfolio_snapshot
from v34_p02_authorizer import (
    AuthorizerRegistry,
    CandidateEntry,
    authorize_and_persist,
    mark_reservation_submitted,
)
from v34_p02_state import Config, EntryPolicyDeclinedError


@dataclass(frozen=True)
class AuthorizationContext:
    """Everything `authorize_entry` needs beyond the candidate/registry/cfg,
    supplied fresh by the caller on every BUY attempt."""
    reconciliation_clean: bool
    kill_switch_active: bool
    entries_today: int
    seconds_since_last_entry: Optional[Decimal]
    turnover_today: Decimal
    cumulative_realized_pnl: Decimal
    cumulative_charges: Decimal
    checkpoint: DailyAccountingCheckpoint
    sealed_daily_pnl_series: Dict[str, Decimal]
    sector_lookup: Dict[str, str]


class SymbolNotInUniverseError(RuntimeError):
    pass


class AuthorizerRegistryStore:
    """Minimal durable store for AuthorizerRegistry - same load/save shape
    as the engine's own state store, so both sides share one discipline."""

    def __init__(self, initial: AuthorizerRegistry):
        self.registry = initial

    def load(self) -> AuthorizerRegistry:
        return self.registry

    def save(self, registry: AuthorizerRegistry) -> None:
        self.registry = registry


class KiteBrokerAdapterMultiPos:
    """Wraps a raw Kite-shaped broker client. Every BUY is routed through
    the full P02-E gate stack before the raw client is ever called - the
    sole BUY dispatch path, exactly as the single-position production
    adapter's docstring already establishes for its own engine. Emergency
    exits are never gated (spec §2/§5: entry controls are not liquidation
    controls) - they pass straight through to the raw client, matching
    `P03RiskController.evaluate_emergency_exit`'s existing "no restriction"
    design."""

    def __init__(self, *, raw_broker, authorizer_store: AuthorizerRegistryStore, cfg: Config,
                 context_provider: Callable[[], AuthorizationContext],
                 governor: Optional[KiteRequestGovernor] = None):
        self.raw_broker = raw_broker
        self.authorizer_store = authorizer_store
        self.cfg = cfg
        self.context_provider = context_provider
        # EA1-R1, 2026-08-19: optional, defaults to None so every existing
        # caller/test (none of which pass a governor today) is completely
        # unaffected - see kite_request_governor.py's own module docstring
        # for why this has to be a genuinely shared, cross-process
        # primitive rather than something invented fresh per adapter.
        # None means "no governance" (test/offline use); production
        # wiring always supplies one.
        self.governor = governor

    def _gate(self, endpoint_class: str) -> None:
        if self.governor is not None:
            self.governor.acquire(endpoint_class)

    # -- read-only pass-through -------------------------------------------

    def get_positions(self):
        self._gate(GOV_DEFAULT)
        return self.raw_broker.get_positions()

    def get_orders(self):
        self._gate(GOV_DEFAULT)
        return self.raw_broker.get_orders()

    def get_order_details(self, order_id):
        self._gate(GOV_DEFAULT)
        return self.raw_broker.get_order_details(order_id)

    def ltp(self, symbols):
        self._gate(GOV_QUOTE)
        return self.raw_broker.ltp(symbols)

    def get_tick_size(self, symbol):
        self._gate(GOV_DEFAULT)
        return self.raw_broker.get_tick_size(symbol)

    # -- the sole BUY dispatch path -----------------------------------------

    def place_order(self, *, exchange, tradingsymbol, transaction_type, quantity, product, order_type, price, tag):
        if transaction_type != "BUY":
            raise RuntimeError("KiteBrokerAdapterMultiPos.place_order only handles BUY; use submit_emergency_exit for SELL.")

        ctx = self.context_provider()
        sector = ctx.sector_lookup.get(tradingsymbol)
        if sector is None:
            raise SymbolNotInUniverseError(f"{tradingsymbol} has no sector mapping - refusing to authorize an entry with unknown concentration risk.")

        self._gate(GOV_DEFAULT)
        positions = self.raw_broker.get_positions()
        self._gate(GOV_DEFAULT)
        orders = self.raw_broker.get_orders()
        self._gate(GOV_QUOTE)
        quotes = self.raw_broker.ltp([tradingsymbol])

        snapshot = build_portfolio_snapshot(
            positions=positions, orders=orders, quotes=quotes,
            reservations=self.authorizer_store.registry.reservations, cfg=self.cfg,
            checkpoint=ctx.checkpoint, cumulative_realized_pnl=ctx.cumulative_realized_pnl,
            cumulative_charges=ctx.cumulative_charges, sealed_daily_pnl_series=ctx.sealed_daily_pnl_series,
        )
        candidate = CandidateEntry(symbol=tradingsymbol, sector=sector, quantity=quantity, price=Decimal(str(price)), entry_tag=tag)

        decision = authorize_and_persist(
            store=self.authorizer_store, registry=self.authorizer_store.registry, candidate=candidate,
            snapshot=snapshot, cfg=self.cfg, reconciliation_clean=ctx.reconciliation_clean,
            kill_switch_active=ctx.kill_switch_active, entries_today=ctx.entries_today,
            seconds_since_last_entry=ctx.seconds_since_last_entry, turnover_today=ctx.turnover_today,
        )
        if not decision.allowed:
            if decision.halt_class == "ENTRY_LOCK":
                raise EntryPolicyDeclinedError(decision.reason)
            # ENGINE_HALT-class (reconciliation not clean): a genuine
            # integrity problem, not a policy decline - the engine's
            # generic exception handling in step() correctly treats this
            # as ENGINE_HALT, unchanged from before EntryPolicyDeclinedError existed.
            raise RuntimeError(f"AUTHORIZATION_FAILED: {decision.reason}")

        # Durably mark the reservation submitted BEFORE the raw broker
        # call - if the process crashes between here and the call
        # returning, reconcile_reservations (P02-E) proves the outcome
        # from broker evidence on restart; nothing here assumes success.
        fingerprint = {
            "exchange": exchange, "tradingsymbol": tradingsymbol, "transaction_type": "BUY",
            "product": product, "order_type": order_type, "quantity": int(quantity),
            "price": _canonical(price), "tag": tag,
        }
        updated_registry = mark_reservation_submitted(self.authorizer_store.registry, tradingsymbol, fingerprint)
        self.authorizer_store.save(updated_registry)

        self._gate(GOV_ORDER)
        return self.raw_broker.place_order(
            exchange=exchange, tradingsymbol=tradingsymbol, transaction_type=transaction_type,
            quantity=quantity, product=product, order_type=order_type, price=price, tag=tag,
        )

    def submit_emergency_exit(self, **kwargs):
        # Deliberately NEVER gated by the request governor, same reasoning
        # as this class's own docstring already states for why exits are
        # never authorization-gated: "P02 refuses any doubt whatsoever on
        # the liquidation path." Making an emergency exit wait its turn
        # behind a rate limiter - even briefly, even correctly - would be
        # a real safety regression, not a convenience. If this account
        # genuinely needs a real Kite request-rate fix on the exit path,
        # that's a decision for Kite/the broker relationship, never
        # something this bridge silently imposes on a liquidation.
        return self.raw_broker.submit_emergency_exit(**kwargs)


def _canonical(price) -> str:
    from v34_p02_state import canonical_decimal_string
    return canonical_decimal_string(price)
