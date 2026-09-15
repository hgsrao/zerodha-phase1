"""EA-1 — Online Shadow Execution Validation: the shadow-mode broker wrapper.

New module. Wraps a real, already-constructed `KiteBrokerClient` (Phase 2,
sealed - NOT modified anywhere by this module, not even by one line) to
answer exactly one question every real trading session needs answered
before real capital is ever authorized: does the complete decision chain
- real market data, real reconciliation, real accounting, real
authorization gates - actually reach a genuine "this would be submitted"
moment, and can that moment be captured as durable, forensic-quality
evidence without ever touching the real write path?

GOVERNING PRINCIPLE, restated from the user's own framing: broker
connectivity ON, live observation ON, execution authority OFF. This
module is the ONE place that distinction is drawn for the entry path -
everywhere else in the stack behaves exactly as it would in a real run.

WHY A SEPARATE WRAPPER CLASS, NOT A NEW BRANCH INSIDE KiteBrokerClient:
that class's own docstring states its governing rule explicitly - "do
nothing this module hasn't been explicitly told to do... no try/except...
no validation" - a deliberately minimal, single-purpose write adapter.
Adding shadow-mode branching directly into it would violate that stated
minimalism and, worse, would mean EVERY caller of KiteBrokerClient -
including a genuine future live deployment - carries shadow-mode code
paths it never needs. Composition keeps KiteBrokerClient exactly as
sealed as every other Phase 2 component; this wrapper is purely additive,
opt-in only via `v34_bridge_runner_startup.build_production_engine
(shadow_mode=True)`, itself defaulted to `False` everywhere.

WHY EntryPolicyDeclinedError, TRACED FROM THE FROZEN ENGINE, NOT
IMPROVISED: `_step_entry_submit()` (institutional_engine_v34_p02_
multipos_candidate.py, frozen) already has exactly the behavior EA-1
needs, built for a different reason but structurally identical: catch
this one exception type, abandon the single pending entry cleanly (`del
self.state.active_trades[symbol]`, durable save, `ENTRY_ABANDONED_
POLICY_HALT` audit log with the exact reason) - WITHOUT touching
`terminator.halted`/`RECONCILIATION_HALT`, so the engine keeps running
and can evaluate the next candidate normally. `EntryPolicyDeclinedError`
itself is documented as raised "by a broker adapter's place_order()"
for "an ENTRY_LOCK-class condition... why a BUY cannot proceed right
now" - shadow mode is honestly exactly that: a policy condition (this
process holds no execution authority) deciding a BUY cannot proceed,
not a broker integrity problem. This is a considered, third use of an
existing, frozen, already-tested exception class - not a new exception
family, and not a repurposing of `KiteBrokerClient`'s own SAFETY_HALT
(a bare, unchained `RuntimeError`, deliberately ENGINE_HALT-class,
deliberately UNCHANGED here - see below).

WHY submit_emergency_exit() IS DELIBERATELY NOT INTERCEPTED, THE MOST
IMPORTANT SAFETY DECISION IN THIS MODULE: exits stay ENGINE_HALT-class,
full stop, by the frozen engine's own explicit design ("P02 refuses any
doubt whatsoever on the liquidation path"). If this account happens to
hold a genuine pre-existing real position when a shadow session starts,
and the engine's own MANAGING logic ever decides that position needs an
emergency exit, the CORRECT behavior is a loud, immediate hard halt -
exactly what `KiteBrokerClient.submit_emergency_exit()` already does
when `live_trading_enabled=False`. Softening that into a quiet decline,
matching the entry-side treatment, would mean this wrapper could
silently prevent a REAL position from being protected while pretending
to observe passively - a genuine safety regression, not a convenience.
This wrapper's `submit_emergency_exit()` is therefore a pure pass-
through to the real `KiteBrokerClient`, completely unmodified. A halt
from this path during a shadow session is a correct, informative signal
worth investigating (why does this account already hold a position?),
not a bug in this module.

THE RESERVATION-CLEANUP FINDING - discovered by tracing, not assumed:
`KiteBrokerAdapterMultiPos.place_order()` (frozen) durably fingerprints
the `EntryReservation` via `mark_reservation_submitted()` BEFORE ever
calling the raw broker - store-first, unconditional, no hook point to
skip it. Once this wrapper declines the call, that reservation is
already fingerprinted but will never have a matching broker order (none
was ever submitted) - `release_unsubmitted_reservation()` (P02-E,
frozen) explicitly REFUSES to release anything already fingerprinted
("a broker order may already exist... use reconcile_reservations to
resolve this with proof"). Left alone, that stale reservation would
permanently occupy the symbol/sector slot for the rest of the session
(`authorize_entry`'s own gate 3, SYMBOL_ALREADY_HELD) - capping a whole
day's shadow evidence at one WOULD_SUBMIT per symbol, ever, rather than
one per genuine signal. This wrapper therefore calls `reconcile_
reservations()` (P02-E, frozen, pure) immediately after logging
WOULD_SUBMIT and before raising - proving, from fresh broker evidence
(no matching order exists, because none was ever submitted), that the
reservation is PROVABLY_STALE and clearing it - the exact same
resolution a genuine crash-between-fingerprint-and-broker-call would
receive on the next startup reconciliation, just applied immediately
instead of waiting for a restart. Persisted through the same adapter
(persist-first) every other component in this project already uses.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from kite_request_governor import DEFAULT as GOV_DEFAULT
from kite_request_governor import QUOTE as GOV_QUOTE
from kite_request_governor import KiteRequestGovernor
from v34_bridge_daily_accounting import compute_daily_entry_stats
from v34_bridge_engine_adapters import EngineAuthorizerRegistryAdapter
from v34_p02_accounting import build_portfolio_snapshot
from v34_p02_authorizer import reconcile_reservations
from v34_p02_state import Config, EntryPolicyDeclinedError


class ShadowModeBrokerClient:
    """Wraps a real KiteBrokerClient. Read methods pass straight through
    unmodified. `place_order()` logs a durable WOULD_SUBMIT audit record
    (best-effort context: fresh quote, fresh portfolio snapshot, fresh
    daily entry stats - each independently fetched, each gracefully
    degraded on its own failure so an observability hiccup never blocks
    the decline itself) and raises EntryPolicyDeclinedError instead of
    ever reaching the real broker. `submit_emergency_exit()` is a pure,
    unmodified pass-through - see module docstring for why."""

    def __init__(
        self, real_broker: Any, *, audit: Any, clock: Any, cfg: Config,
        authreg_adapter: EngineAuthorizerRegistryAdapter, accounting_state_holder: Dict[str, Any],
        governor: Optional[KiteRequestGovernor] = None,
    ):
        if getattr(real_broker, "live_trading_enabled", None) is not False:
            raise RuntimeError(
                "ShadowModeBrokerClient refuses to wrap a broker whose own live_trading_enabled "
                "is not exactly False - shadow mode must never be layered on top of real execution authority."
            )
        self._real_broker = real_broker
        self._audit = audit
        self._clock = clock
        self._cfg = cfg
        self._authreg_adapter = authreg_adapter
        self._accounting_state_holder = accounting_state_holder
        # EA1-R1, 2026-08-19: optional, defaults to None. Load-bearing
        # reason this exists at all, not just in the outer adapter: every
        # WOULD_SUBMIT decline this class logs makes up to 5 of its OWN
        # additional real Kite calls internally (_best_effort_live_quote,
        # _best_effort_portfolio_snapshot x2, _best_effort_daily_entry_
        # stats, _clear_now_provably_stale_reservation x2) that the
        # OUTER KiteBrokerAdapterMultiPos gate (one acquire() before
        # calling place_order() at all) never sees or governs - found by
        # tracing this class's own place_order() body, not assumed.
        self._governor = governor

    def _gate(self, endpoint_class: str) -> None:
        if self._governor is not None:
            self._governor.acquire(endpoint_class)

    # -- read delegation - unmodified pass-through --------------------------

    def get_positions(self):
        return self._real_broker.get_positions()

    def get_orders(self):
        return self._real_broker.get_orders()

    def get_order_details(self, order_id: str):
        return self._real_broker.get_order_details(order_id)

    def ltp(self, symbols: List[str]):
        return self._real_broker.ltp(symbols)

    def get_tick_size(self, symbol: str):
        return self._real_broker.get_tick_size(symbol)

    # -- the one intercepted write path --------------------------------------

    def place_order(
        self, *, exchange: str, tradingsymbol: str, transaction_type: str,
        quantity: int, product: str, order_type: str, price: float, tag: str,
    ) -> Any:
        self._log_would_submit(
            exchange=exchange, tradingsymbol=tradingsymbol, transaction_type=transaction_type,
            quantity=quantity, product=product, order_type=order_type, price=price, tag=tag,
        )
        self._clear_now_provably_stale_reservation(tradingsymbol)
        raise EntryPolicyDeclinedError(
            "EA1_SHADOW_MODE: execution authority disabled for this session - this order would "
            "have been submitted for real; see the preceding WOULD_SUBMIT audit record."
        )

    def submit_emergency_exit(self, **kwargs) -> Any:
        # Deliberately unmodified - see module docstring's own safety note.
        return self._real_broker.submit_emergency_exit(**kwargs)

    # -- internals ------------------------------------------------------------

    def _log_would_submit(self, *, exchange, tradingsymbol, transaction_type, quantity, product, order_type, price, tag) -> None:
        fields: Dict[str, Any] = {
            "symbol": tradingsymbol, "side": transaction_type, "quantity": int(quantity),
            "reference_price": str(price), "product": product, "order_type": order_type, "tag": tag,
            "exchange": exchange,
            # Sizing convention, not an actual placed stop - this engine's
            # own no-mandatory-SL policy (traced, P02-C) means no
            # protective order is guaranteed to exist at entry time.
            "configured_stop_loss_pct": str(self._cfg.stop_loss_pct),
            "configured_target_risk_pct": str(self._cfg.target_risk_pct),
        }
        fields.update(self._best_effort_live_quote(tradingsymbol))
        fields.update(self._best_effort_portfolio_snapshot())
        fields.update(self._best_effort_daily_entry_stats())
        self._audit.log("WOULD_SUBMIT", **fields)

    def _best_effort_live_quote(self, symbol: str) -> Dict[str, Any]:
        try:
            self._gate(GOV_QUOTE)
            quote = self._real_broker.ltp([symbol])
            last_price = quote.get(f"NSE:{symbol}", quote.get(symbol, {})).get("last_price")
            return {"live_quote": str(last_price) if last_price is not None else None}
        except Exception as exc:
            return {"live_quote": None, "live_quote_error": type(exc).__name__}

    def _best_effort_portfolio_snapshot(self) -> Dict[str, Any]:
        try:
            self._gate(GOV_DEFAULT)
            positions = self._real_broker.get_positions()
            self._gate(GOV_DEFAULT)
            orders = self._real_broker.get_orders()
            quotes: Dict[str, Any] = {}
            state = self._accounting_state_holder["state"]
            snapshot = build_portfolio_snapshot(
                positions=positions, orders=orders, quotes=quotes,
                reservations=self._authreg_adapter.registry.reservations, cfg=self._cfg,
                checkpoint=state.checkpoint, cumulative_realized_pnl=state.cumulative_realized_pnl,
                cumulative_charges=state.cumulative_charges, sealed_daily_pnl_series=state.sealed_daily_pnl_series,
            )
            return {
                "committed_capital": str(snapshot.committed_capital),
                "deployed_capital": str(snapshot.deployed_capital),
                # equity/daily_pnl/trial_drawdown need quotes for open
                # positions (mark-dependent, P02-D) - deliberately not
                # fetched here (a second, avoidable quote burst); the
                # cost-basis figures above never need a mark and are
                # always safe to report.
            }
        except Exception as exc:
            return {"portfolio_snapshot_error": type(exc).__name__}

    def _best_effort_daily_entry_stats(self) -> Dict[str, Any]:
        try:
            self._gate(GOV_DEFAULT)
            orders = self._real_broker.get_orders()
            trades: List[Any] = []  # trades() is not on KiteBrokerClient's own protocol - entries_today/turnover_today from orders() alone is still meaningful, just not trade-level exact
            entries_today, turnover_today, seconds_since_last_entry = compute_daily_entry_stats(
                orders=orders, trades=trades, cfg=self._cfg, now=self._clock.now(),
            )
            return {
                "entries_today": entries_today, "turnover_today": str(turnover_today),
                "seconds_since_last_entry": (str(seconds_since_last_entry) if seconds_since_last_entry is not None else None),
            }
        except Exception as exc:
            return {"daily_entry_stats_error": type(exc).__name__}

    def _clear_now_provably_stale_reservation(self, symbol: str) -> None:
        """See module docstring's own "RESERVATION-CLEANUP FINDING"
        section for why this is necessary, not optional cleanup."""
        self._gate(GOV_DEFAULT)
        orders = self._real_broker.get_orders()
        self._gate(GOV_DEFAULT)
        positions = self._real_broker.get_positions()
        reconciled = reconcile_reservations(self._authreg_adapter.registry, orders=orders, positions=positions, product=self._cfg.product)
        self._authreg_adapter.save(reconciled)
