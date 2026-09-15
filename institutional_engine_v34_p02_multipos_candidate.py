"""V34-P02 multi-position CNC candidate engine (P02-C).

State machine + reconciliation, generalized from the single-position MIS
engine (`institutional_engine_v34_p01d_candidate.py`) to hold up to
`Config.max_simultaneous_positions` concurrent CNC positions, per
`V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` (frozen) and the V17 rewrite plan.

Completely isolated from the production engine/runner: no import from
`institutional_engine_v34_p01d_candidate.py` or
`run_production_p01d_candidate.py`, own state file, own class name
(`TradingEngineV34P02`, not `TradingEngineV34`). Nothing in this module can
place, modify, or cancel a broker order beyond what a caller's mocked
broker adapter chooses to do - LIVE_TRADING_ENABLED lives in the runner
(P02-E), not here, and no live signal source calls request_entry()/
request_exit() from this module.

Two deliberate policy departures from the ported single-position logic,
both documented in the frozen spec, not quiet diffs:

1. **No mandatory protective stop.** `PROTECTION` no longer hard-halts on
   zero matching SL orders - zero is the expected default for a strategy
   V12 already proved gets worse with an ad hoc intraday-calibrated stop
   bolted on (3/17 vs 11/17 profitable folds). `MANAGING` never requires
   `ctx.stop_order_id`. If an operator manually places a discretionary SL,
   adoption still works exactly as before. Capital protection in this
   engine version is the runner's portfolio-level percentage halts
   (P02-D/E), not a per-position stop.
2. **Three control classes, not one boolean** (spec §2). `ENTRY_LOCK`
   (financial/policy) never touches `terminator.halted` or
   `RECONCILIATION_HALT` - a broker adapter signals it by raising
   `EntryPolicyDeclinedError`, which `step()`'s `ENTRY_SUBMIT` branch
   catches specifically and resolves by abandoning that one pending entry
   (removed from `active_trades`, audit-logged), never by halting the
   engine. Every other exception from `place_order()`/
   `submit_emergency_exit()` remains `ENGINE_HALT`-class, unchanged from
   the original engine's fail-closed posture.

Whole-engine fail-closed semantics are otherwise preserved exactly: any
single position's unresolved ambiguity halts the entire engine (no
per-symbol quarantine), reconciliation always proves state before
persisting it and never repairs-then-justifies, and `clear_halt_and_reconcile()`
requires an operator note and defers to a fresh `reconcile_startup()` pass
to actually prove the broker is reconcilable - it does not itself decide
"clean," it only clears the durable latch so the next cycle can try.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from decimal import ROUND_DOWN, Decimal
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional
import math

from broker_exposure_scope_p02_multipos import classify_positions
from v34_p02_state import (
    BotState,
    BrokerObservationContractViolation,
    Config,
    EngineStatus,
    EntryPolicyDeclinedError,
    PositionStatus,
    TradeContext,
    canonical_decimal_string,
    order_matches_entry_fingerprint,
    order_matches_exit_fingerprint,
)

IST = ZoneInfo("Asia/Kolkata")
EXCHANGE = "NSE"

ACTIVE_ORDER_STATUSES = {
    "OPEN",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
    "AMO REQ RECEIVED",
    "MODIFY PENDING",
    "CANCEL PENDING",
}

# Duplicated from institutional_engine_v34_p01d_candidate.py rather than
# imported, per this module's isolation guarantee (nothing here imports
# from any production file). Cross-checked against two independent
# published NSE 2026 holiday calendars on 2026-08-14.
NSE_HOLIDAYS_2026 = frozenset({
    date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26), date(2026, 3, 31),
    date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1), date(2026, 5, 28),
    date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 20),
    date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
})

DEFAULT_ENTRY_TAG = "V3.4_P02_ENTRY"
DEFAULT_EXIT_TAG = "V3.4_P02_EXIT"
SL_TAGS = {"V3.3_SL", "V3.4_SL", "V3.4_P02_SL"}


def describe_nse_trading_window(
    now: datetime,
    *,
    holidays: frozenset,
    market_open_time: time,
    market_close_time: time,
) -> tuple[bool, str]:
    """Return (is_open, reason). Defense-in-depth gate on *new* entries
    only - never inspects or blocks management/exit of an already active
    position."""
    if now.tzinfo is None:
        raise ValueError("describe_nse_trading_window requires a timezone-aware datetime.")
    local = now.astimezone(IST)
    if local.weekday() >= 5:
        return False, f"MARKET_CLOSED_WEEKEND: {local.date().isoformat()}"
    if local.date() in holidays:
        return False, f"MARKET_CLOSED_HOLIDAY: {local.date().isoformat()}"
    local_time = local.time()
    if local_time < market_open_time or local_time >= market_close_time:
        return False, (
            f"MARKET_CLOSED_OUTSIDE_SESSION: {local_time.isoformat(timespec='minutes')} "
            f"not within [{market_open_time.isoformat(timespec='minutes')}, "
            f"{market_close_time.isoformat(timespec='minutes')})"
        )
    return True, "MARKET_OPEN"


@dataclass
class ObservationTracker:
    consecutive_failures: int = 0
    last_operation: Optional[str] = None


class _ObservationRetrySentinel:
    __slots__ = ()


OBSERVATION_RETRY = _ObservationRetrySentinel()


class TradingEngineV34P02:
    """Multi-position CNC candidate engine. See module docstring."""

    def __init__(self, broker, clock, sleeper, store, audit, alert, lock, terminator, cfg: Config):
        self.broker = broker
        self.clock = clock
        self.sleeper = sleeper
        self.store = store
        self.audit = audit
        self.alert = alert
        self.lock_provider = lock
        self.terminator = terminator
        self.cfg = cfg
        self._observation_trackers: Dict[str, ObservationTracker] = {}

        self._lock_ref = self.lock_provider.acquire()
        self.state: BotState = self.store.load(self.clock.now().date())

        if self.state.clearance_required or self.state.status == EngineStatus.RECONCILIATION_HALT:
            reason = self.state.halt_reason or "Persisted durable reconciliation halt active on startup."
            self.terminator.halt(reason)
            return

    # -- observation plumbing -------------------------------------------------

    @staticmethod
    def _is_transient_observation_exception(exc: Exception) -> bool:
        cls = type(exc)
        if cls.__name__ in {"TimeoutError", "ConnectionError", "ReadTimeout", "ConnectTimeout"}:
            return True
        module = getattr(cls, "__module__", "")
        if module.startswith("requests.exceptions") and cls.__name__ in {
            "Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError"
        }:
            return True
        if module.startswith("kiteconnect.exceptions") and cls.__name__ == "NetworkException":
            return getattr(exc, "code", None) in {502, 503, 504}
        return False

    def _is_transient_submission_exception(self, exc: Exception) -> bool:
        current: Optional[BaseException] = exc
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, Exception) and self._is_transient_observation_exception(current):
                return True
            current = getattr(current, "__cause__", None)
        return False

    def _observation_key(self, symbol: str, status: PositionStatus, operation: str) -> str:
        # Symbol-scoped (spec/V17 plan): one symbol's transient broker
        # error must never exhaust or reset a different symbol's retry
        # budget for the same operation name.
        return f"{symbol}:{status.value}:{operation}"

    def _observe(self, symbol: str, status: PositionStatus, operation: str, fn):
        key = self._observation_key(symbol, status, operation)
        tracker = self._observation_trackers.setdefault(key, ObservationTracker())
        try:
            result = fn()
        except Exception as exc:
            if not self._is_transient_observation_exception(exc):
                raise
            tracker.consecutive_failures += 1
            tracker.last_operation = operation
            failure_no = tracker.consecutive_failures
            budget = int(self.cfg.observation_retry_budget)
            remaining = max(0, budget - failure_no)
            try:
                self.audit.log(
                    "OBSERVATION_FAILURE", symbol=symbol, state=status.value, operation=operation,
                    attempt=failure_no, consecutive_failures=failure_no, budget=budget,
                    remaining_budget=remaining, exception_type=type(exc).__name__,
                    exception_message=str(exc),
                    action="HARD_HALT" if failure_no > budget else "RETRY_NEXT_POLL",
                )
            except Exception:
                pass
            if failure_no > budget:
                self.trigger_hard_halt(
                    f"Observation Budget Exhausted: {symbol}:{operation} failed "
                    f"{failure_no} consecutive times. Last error: {exc}",
                    source="RESILIENCE_LAYER",
                )
            return OBSERVATION_RETRY
        tracker.consecutive_failures = 0
        tracker.last_operation = operation
        return result

    # -- whole-engine fail-closed halt (ENGINE_HALT class, spec §2) ----------

    def trigger_hard_halt(self, reason: str, source: str = "ENGINE") -> None:
        self.state.status = EngineStatus.RECONCILIATION_HALT
        self.state.halt_reason = reason
        self.state.halt_source = source
        self.state.halted_at = self.clock.now().isoformat()
        self.state.clearance_required = True
        self.store.save(self.state)
        try:
            self.audit.log("HARD_HALT", reason=reason, source=source, halted_at=self.state.halted_at)
        except Exception:
            pass
        try:
            self.alert.send("CRITICAL", reason)
        except Exception:
            pass
        self.terminator.halt(reason)

    def clear_halt_and_reconcile(self, operator_note: str) -> bool:
        """Require an operator note, then defer proof to reconcile_startup()
        rather than deciding 'clean' here directly - for a multi-position
        engine, 'clean' legitimately means several open CNC positions can
        exist; the only thing worth proving is that every one of them
        reconciles, which is exactly what reconcile_startup() already does.
        Prove state, then persist - never repair first and justify after."""
        if not self.state.clearance_required:
            return True
        if not operator_note or not operator_note.strip():
            self.audit.log("OPERATOR_CLEARANCE_REJECTED", reason="Operator acknowledgement is required.")
            return False
        try:
            self.broker.get_positions()
            self.broker.get_orders()
        except Exception as exc:
            self.audit.log("OPERATOR_CLEARANCE_REJECTED", reason=f"Broker verification failed: {exc}")
            return False

        self.audit.log("OPERATOR_CLEARANCE_RECORDED", note=operator_note)
        self.state.clearance_required = False
        self.state.operator_acknowledgement = operator_note.strip()
        self.state.halt_reason = None
        self.state.halt_source = None
        self.state.status = EngineStatus.STARTUP
        self.store.save(self.state)
        if hasattr(self.terminator, "halted"):
            self.terminator.halted = False
        if hasattr(self.terminator, "reason"):
            self.terminator.reason = None
        self.audit.log("RUNTIME_HALT_CLEARED_AFTER_BROKER_VERIFICATION")
        return True

    # -- broker payload helpers -----------------------------------------------

    def _active_positions(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return nonzero exposure in cfg.product; ignore the other product."""
        managed, _ignored = classify_positions(positions, managed_product=self.cfg.product)
        return managed

    @staticmethod
    def _active_orders(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(orders, list):
            raise RuntimeError("FAIL_CLOSED: Broker orders response is not a list.")
        active: List[Dict[str, Any]] = []
        for o in orders:
            if not isinstance(o, dict):
                raise RuntimeError("FAIL_CLOSED: Broker order entry is not an object.")
            if o.get("status") in ACTIVE_ORDER_STATUSES:
                active.append(o)
        return active

    @staticmethod
    def _order_status(order: Dict[str, Any]) -> str:
        if not isinstance(order, dict):
            raise RuntimeError("FAIL_CLOSED: Malformed broker order.")
        status = order.get("status")
        if not isinstance(status, str) or not status:
            raise RuntimeError("FAIL_CLOSED: Broker order has no valid status.")
        return status

    @staticmethod
    def _order_qty(order: Dict[str, Any]) -> int:
        raw = order.get("quantity", order.get("target_qty", 0))
        try:
            qty = int(raw or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("FAIL_CLOSED: Broker order quantity is malformed.") from exc
        if qty < 0:
            raise RuntimeError("FAIL_CLOSED: Broker order quantity is negative.")
        return qty

    @staticmethod
    def _filled_qty(order: Dict[str, Any]) -> int:
        raw = order.get("filled_quantity", 0)
        try:
            qty = int(raw or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("FAIL_CLOSED: Broker filled_quantity is malformed.") from exc
        if qty < 0:
            raise RuntimeError("FAIL_CLOSED: Broker filled_quantity is negative.")
        return qty

    @staticmethod
    def _position_matches(pos: Dict[str, Any], *, symbol: str, quantity: int, product: str, exchange: str = EXCHANGE) -> bool:
        # `product` is required (no default) so a call site that forgets it
        # is an immediate TypeError, not a silent fallback to the wrong
        # product (V17 plan's deliberate hardening over the ported original).
        if pos.get("tradingsymbol") != symbol:
            return False
        if pos.get("exchange", exchange) != exchange:
            return False
        if pos.get("product") != product:
            return False
        try:
            return int(pos.get("quantity", 0) or 0) == quantity
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _matching_positions(positions: List[Dict[str, Any]], *, symbol: str, product: str, quantity: Optional[int] = None) -> List[Dict[str, Any]]:
        result = []
        for p in positions:
            if p.get("tradingsymbol") != symbol:
                continue
            if p.get("exchange", EXCHANGE) != EXCHANGE:
                continue
            if p.get("product") != product:
                continue
            if quantity is not None:
                try:
                    if int(p.get("quantity", 0) or 0) != quantity:
                        continue
                except (TypeError, ValueError):
                    continue
            result.append(p)
        return result

    # Fingerprint canonicalization/matching lives in v34_p02_state.py now,
    # shared with the accounting layer (P02-D) so "does this broker order
    # correspond to this durable intent" has exactly one implementation -
    # these three stay as thin, call-site-preserving wrappers.
    @staticmethod
    def _canonical_decimal_string(value: Any) -> str:
        return canonical_decimal_string(value)

    def _build_entry_submission_fingerprint(self, *, symbol: str, quantity: int, price: Decimal) -> Dict[str, Any]:
        return {
            "exchange": "NSE", "tradingsymbol": str(symbol), "transaction_type": "BUY",
            "product": self.cfg.product, "order_type": "LIMIT", "quantity": int(quantity),
            "price": canonical_decimal_string(price), "tag": DEFAULT_ENTRY_TAG,
        }

    def _build_exit_submission_fingerprint(self, *, symbol: str, quantity: int, trigger_price: Decimal, market_protection: Decimal) -> Dict[str, Any]:
        return {
            "exchange": "NSE", "tradingsymbol": str(symbol), "transaction_type": "SELL",
            "product": self.cfg.product, "order_type": "SL-M", "quantity": int(quantity),
            "trigger_price": canonical_decimal_string(trigger_price),
            "market_protection": canonical_decimal_string(market_protection),
            "tag": DEFAULT_EXIT_TAG,
        }

    @staticmethod
    def _order_matches_entry_fingerprint(order: Dict[str, Any], fingerprint: Dict[str, Any]) -> bool:
        return order_matches_entry_fingerprint(order, fingerprint)

    @staticmethod
    def _order_matches_exit_fingerprint(order: Dict[str, Any], fingerprint: Dict[str, Any]) -> bool:
        return order_matches_exit_fingerprint(order, fingerprint)

    # -- public API: request_entry / request_exit -----------------------------

    def request_entry(self, *, symbol: str, quantity: int, price: Decimal, entry_tag: str = DEFAULT_ENTRY_TAG) -> str:
        """Create a durable entry intent for `symbol`. Never submits a
        broker order itself - step() performs the controlled BUY.
        Preconditions per spec §2/§12 and the V17 plan's request_entry()
        redesign."""
        if self.terminator.halted:
            return "HALTED"
        if self.state.status != EngineStatus.RUNNING:
            raise RuntimeError(f"ENTRY_REQUEST_REJECTED: engine state is {self.state.status.value}, not RUNNING.")
        if self.state.clearance_required:
            raise RuntimeError("ENTRY_REQUEST_REJECTED: durable clearance is required.")

        symbol = str(symbol).strip().upper()
        if not symbol:
            raise RuntimeError("ENTRY_REQUEST_REJECTED: symbol is empty.")
        if symbol in self.state.active_trades:
            raise RuntimeError(f"ENTRY_REQUEST_REJECTED: SYMBOL_ALREADY_HELD: {symbol}.")
        if len(self.state.active_trades) >= self.cfg.max_simultaneous_positions:
            raise RuntimeError(
                f"ENTRY_REQUEST_REJECTED: SIMULTANEOUS_POSITION_LIMIT: "
                f"{len(self.state.active_trades)} >= {self.cfg.max_simultaneous_positions}."
            )

        try:
            quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("ENTRY_REQUEST_REJECTED: quantity must be an integer.") from exc
        if quantity <= 0:
            raise RuntimeError("ENTRY_REQUEST_REJECTED: quantity must be positive.")

        price = Decimal(str(price))
        if not price.is_finite() or price <= 0:
            raise RuntimeError("ENTRY_REQUEST_REJECTED: price must be positive and finite.")

        if entry_tag != DEFAULT_ENTRY_TAG:
            raise RuntimeError("ENTRY_REQUEST_REJECTED: invalid entry tag.")

        is_open, window_reason = describe_nse_trading_window(
            self.clock.now(), holidays=NSE_HOLIDAYS_2026,
            market_open_time=self.cfg.market_open_time, market_close_time=self.cfg.market_close_time,
        )
        if not is_open:
            raise RuntimeError(f"ENTRY_REQUEST_REJECTED: {window_reason}")

        self.state.active_trades[symbol] = TradeContext(
            symbol=symbol, entry_tag=entry_tag, target_qty=quantity, tranche_qty=quantity,
            status=PositionStatus.ENTRY_SUBMIT, filled_qty=0, pending_qty=quantity,
            entry_order_id=None, entry_price=price, order_status="PENDING",
            opened_trading_day=self.state.trading_day,
        )
        # BotState.status is deliberately untouched - it stays RUNNING;
        # only the new position's own status changes (spec §12/V17 plan).
        self.store.save(self.state)
        self.audit.log("ENTRY_INTENT_CREATED", symbol=symbol, quantity=quantity, price=str(price), tag=entry_tag)
        return "STATE_CHANGED"

    def request_exit(self, *, symbol: str, exit_tag: str = DEFAULT_EXIT_TAG) -> str:
        """Proactive exit entry point - the single-position engine had none;
        every exit there was reactive (SL-fill driven). Required because
        the no-mandatory-SL policy means nothing else can move a slot out
        of MANAGING. Checks ENGINE_HALT only (spec §2) - completely
        independent of any ENTRY_LOCK condition."""
        if self.terminator.halted:
            return "HALTED"
        if self.state.status != EngineStatus.RUNNING:
            raise RuntimeError(f"EXIT_REQUEST_REJECTED: engine state is {self.state.status.value}, not RUNNING.")
        if self.state.clearance_required:
            raise RuntimeError("EXIT_REQUEST_REJECTED: durable clearance is required.")

        symbol = str(symbol).strip().upper()
        ctx = self.state.active_trades.get(symbol)
        if ctx is None:
            raise RuntimeError(f"EXIT_REQUEST_REJECTED: no open position for {symbol}.")
        if ctx.status != PositionStatus.MANAGING:
            raise RuntimeError(
                f"EXIT_REQUEST_REJECTED: {symbol} is in {ctx.status.value}, not MANAGING."
            )
        if exit_tag != DEFAULT_EXIT_TAG:
            raise RuntimeError("EXIT_REQUEST_REJECTED: invalid exit tag.")

        ctx.pending_qty = ctx.filled_qty
        ctx.status = PositionStatus.EXIT_SUBMIT
        self.store.save(self.state)
        self.audit.log("EXIT_INTENT_CREATED", symbol=symbol, quantity=ctx.filled_qty, tag=exit_tag)
        return "STATE_CHANGED"

    # -- reconciliation (P02-C's core) -----------------------------------------

    def reconcile_startup(self) -> None:
        """Reconcile every locally-known position against broker reality,
        symbol by symbol, then detect orphans (broker exposure with no
        local match). Fails fast and whole-engine-halts on the first
        unresolved ambiguity anywhere - no per-symbol quarantine. Never
        repairs a contradiction by mutating first; always proves, then
        persists."""
        persisted_status = self.state.status
        self.state.status = EngineStatus.RECONCILING
        self.store.save(self.state)

        try:
            positions = self.broker.get_positions()
            open_orders = self.broker.get_orders()
            active_pos = self._active_positions(positions)
            active_orders = self._active_orders(open_orders)

            self.state.status = persisted_status

            unresolved_broker_symbols = {
                p.get("tradingsymbol") for p in active_pos if p.get("tradingsymbol")
            }

            for symbol, ctx in list(self.state.active_trades.items()):
                halted = self._reconcile_one_position(symbol, ctx, active_pos, active_orders, open_orders)
                if halted:
                    return
                unresolved_broker_symbols.discard(symbol)

            if unresolved_broker_symbols:
                self.trigger_hard_halt(
                    "Reconciliation Failure: Broker reports CNC position(s) "
                    f"{sorted(unresolved_broker_symbols)} with no matching local TradeContext."
                )
                return

            known_symbols = set(self.state.active_trades)
            for order in active_orders:
                order_symbol = order.get("tradingsymbol")
                if order_symbol is not None and order_symbol not in known_symbols:
                    self.trigger_hard_halt(
                        f"Reconciliation Failure: Broker has an active order for "
                        f"{order_symbol!r} with no matching local TradeContext."
                    )
                    return

            current_trading_day = str(self.clock.now().date())
            if self.state.trading_day != current_trading_day:
                previous_trading_day = self.state.trading_day
                self.state.trading_day = current_trading_day
                self.audit.log(
                    "TRADING_DAY_ROLLED_FORWARD",
                    previous_trading_day=previous_trading_day,
                    trading_day=current_trading_day,
                    open_positions=len(self.state.active_trades),
                )

            self.audit.log("STARTUP_RECONCILIATION_PASSED", open_positions=len(self.state.active_trades))
            self.state.status = EngineStatus.RUNNING
            self.store.save(self.state)
        except Exception as exc:
            self.trigger_hard_halt(f"Reconciliation exception: {exc}")

    def _reconcile_one_position(self, symbol: str, ctx: TradeContext, active_pos, active_orders, open_orders) -> bool:
        """Returns True if this call resulted in a whole-engine hard halt."""
        matching_pos = self._matching_positions(active_pos, symbol=symbol, product=self.cfg.product)
        symbol_orders = [o for o in active_orders if o.get("tradingsymbol") == symbol]

        # ENTRY_SUBMIT with no order id yet: durable, pre-side-effect
        # boundary. Safe to resume only when broker has zero activity for
        # this symbol; any activity is ambiguous.
        if ctx.status == PositionStatus.ENTRY_SUBMIT and ctx.entry_order_id is None:
            if matching_pos or symbol_orders:
                self.trigger_hard_halt(
                    f"Reconciliation Failure: {symbol} persisted ENTRY_SUBMIT has ambiguous broker activity."
                )
                return True
            return False

        if ctx.status in {PositionStatus.ENTRY_SUBMITTING, PositionStatus.ENTRY_UNKNOWN}:
            return self._reconcile_unknown_entry(symbol, ctx, open_orders)

        if not ctx.entry_order_id:
            self.trigger_hard_halt(
                f"Reconciliation Failure: {symbol} local trade is missing entry_order_id."
            )
            return True

        if ctx.status in {PositionStatus.ENTRY_PENDING, PositionStatus.PARTIAL_POSITION} and not ctx.stop_order_id:
            return self._reconcile_entry_fill(symbol, ctx, active_pos, matching_pos, open_orders)

        if ctx.status in {PositionStatus.EXIT_SUBMITTING, PositionStatus.EXIT_UNKNOWN}:
            return self._reconcile_unknown_exit(symbol, ctx, open_orders)

        if ctx.status in {
            PositionStatus.PROTECTION, PositionStatus.PROTECTION_PENDING, PositionStatus.MANAGING,
            PositionStatus.EXIT_SUBMIT,
        }:
            if len(matching_pos) != 1:
                self.trigger_hard_halt(
                    f"Reconciliation Failure: {symbol} managed state does not map to exactly one broker position."
                )
                return True

            # An already-owned position's broker-reported quantity must
            # match what was last durably recorded. This is a deliberate
            # strengthening over a pure port: the original single-position
            # engine trusted "exactly one matching symbol+product position"
            # without independently re-verifying quantity here, deferring
            # that check to the next MANAGING poll. For a portfolio engine,
            # proving quantity at reconciliation time - not just symbol/
            # product - is cheap and closes a real "quantity disagreement"
            # gap rather than silently adopting whatever the broker reports.
            try:
                broker_qty = int(matching_pos[0].get("quantity", 0) or 0)
            except (TypeError, ValueError):
                self.trigger_hard_halt(f"Reconciliation Failure: {symbol} broker position quantity is malformed.")
                return True
            if broker_qty != ctx.filled_qty:
                self.trigger_hard_halt(
                    f"Reconciliation Failure: {symbol} broker-reported quantity ({broker_qty}) "
                    f"disagrees with the locally recorded filled quantity ({ctx.filled_qty})."
                )
                return True

            if ctx.status in {PositionStatus.PROTECTION, PositionStatus.PROTECTION_PENDING, PositionStatus.MANAGING}:
                sl_orders = [
                    o for o in symbol_orders
                    if o.get("exchange", EXCHANGE) == EXCHANGE
                    and o.get("product") == self.cfg.product
                    and o.get("transaction_type") == "SELL"
                    and o.get("tag") in SL_TAGS
                ]
                if len(sl_orders) > 1:
                    self.trigger_hard_halt(
                        f"Reconciliation Failure: {symbol} has more than one active protective stop."
                    )
                    return True
                # Zero is the expected default under the no-mandatory-SL
                # policy; one means an operator placed a discretionary
                # stop and it's adopted.
                ctx.stop_order_id = sl_orders[0].get("order_id") if sl_orders else None
                ctx.status = PositionStatus.MANAGING
                self.store.save(self.state)
            return False

        if ctx.status == PositionStatus.EXIT_PENDING:
            if not ctx.exit_order_id:
                self.trigger_hard_halt(f"Reconciliation Failure: {symbol} EXIT_PENDING without exit_order_id.")
                return True
            exit_order = next((o for o in open_orders if str(o.get("order_id")) == str(ctx.exit_order_id)), None)
            if exit_order is None:
                try:
                    exit_order = self.broker.get_order_details(ctx.exit_order_id)
                except Exception:
                    exit_order = None
            if exit_order is None:
                self.trigger_hard_halt(f"Reconciliation Failure: {symbol} EXIT_PENDING has no broker order evidence.")
                return True
            return False

        self.trigger_hard_halt(
            f"Reconciliation Failure: {symbol} has unsupported or unsafe persisted trade state '{ctx.status.value}'."
        )
        return True

    def _reconcile_unknown_entry(self, symbol: str, ctx: TradeContext, open_orders) -> bool:
        if not ctx.entry_submission_fingerprint:
            self.trigger_hard_halt(f"P0-1D: {symbol} ENTRY_UNKNOWN/ENTRY_SUBMITTING lacks immutable submission fingerprint.")
            return True
        try:
            required = {"order_id", "exchange", "tradingsymbol", "transaction_type", "product", "order_type", "quantity", "price", "tag", "status"}
            for order in open_orders:
                if not isinstance(order, dict) or required.difference(order):
                    raise RuntimeError("FAIL_CLOSED: Broker order is missing required entry reconciliation fields.")
            matches = [o for o in open_orders if self._order_matches_entry_fingerprint(o, ctx.entry_submission_fingerprint)]
            if len(matches) > 1:
                self.trigger_hard_halt(f"P0-1D: {symbol} - multiple exact entry orders match the immutable fingerprint.", source="ENTRY_RECONCILIATION")
                return True
            if len(matches) == 1:
                ctx.entry_order_id = str(matches[0]["order_id"])
                ctx.order_status = str(matches[0]["status"])
                ctx.entry_reconciliation_failures = 0
                ctx.status = PositionStatus.ENTRY_PENDING
                self.store.save(self.state)
                self.audit.log("ENTRY_ORDER_RECOVERED", symbol=symbol, order_id=ctx.entry_order_id, source_state="ENTRY_UNKNOWN")
                return False
            # Zero exact matches, proven via the broker's own order list:
            # this is the "provably stale" case (spec §7's spirit applied
            # to entry intents) - not itself a halt on its own; only the
            # observation-budget-exhausted path below halts.
            ctx.entry_reconciliation_failures = int(ctx.entry_reconciliation_failures or 0) + 1
            if ctx.status == PositionStatus.ENTRY_SUBMITTING:
                ctx.status = PositionStatus.ENTRY_UNKNOWN
            self.store.save(self.state)
            if ctx.entry_reconciliation_failures > int(self.cfg.observation_retry_budget):
                self.trigger_hard_halt(f"P0-1D: {symbol} entry reconciliation budget exhausted without an exact broker-order match.", source="ENTRY_RECONCILIATION")
                return True
            return False
        except Exception as exc:
            if "FAIL_CLOSED" in str(exc):
                self.trigger_hard_halt(f"P0-1D: {symbol} malformed entry reconciliation observation: {exc}", source="ENTRY_RECONCILIATION")
                return True
            ctx.entry_reconciliation_failures = int(ctx.entry_reconciliation_failures or 0) + 1
            try:
                self.store.save(self.state)
            except Exception:
                pass
            if ctx.entry_reconciliation_failures > int(self.cfg.observation_retry_budget):
                self.trigger_hard_halt(f"P0-1D: {symbol} entry reconciliation failed repeatedly: {exc}", source="ENTRY_RECONCILIATION")
                return True
            return False

    def _reconcile_unknown_exit(self, symbol: str, ctx: TradeContext, open_orders) -> bool:
        if not ctx.exit_submission_fingerprint:
            self.trigger_hard_halt(f"P0-1D: {symbol} EXIT_UNKNOWN/EXIT_SUBMITTING lacks immutable submission fingerprint.")
            return True
        try:
            required = {"order_id", "exchange", "tradingsymbol", "transaction_type", "product", "order_type", "quantity", "trigger_price", "market_protection", "tag", "status"}
            for order in open_orders:
                if not isinstance(order, dict) or required.difference(order):
                    raise RuntimeError("FAIL_CLOSED: Broker order is missing required reconciliation fields.")
            matches = [o for o in open_orders if self._order_matches_exit_fingerprint(o, ctx.exit_submission_fingerprint)]
            if len(matches) > 1:
                self.trigger_hard_halt(f"P0-1D: {symbol} - multiple exact emergency-exit orders match the immutable fingerprint.", source="EXIT_RECONCILIATION")
                return True
            if len(matches) == 1:
                ctx.exit_order_id = str(matches[0]["order_id"])
                ctx.exit_reconciliation_failures = 0
                ctx.status = PositionStatus.EXIT_PENDING
                self.store.save(self.state)
                self.audit.log("EMERGENCY_EXIT_ORDER_RECOVERED", symbol=symbol, order_id=ctx.exit_order_id, source_state="EXIT_UNKNOWN")
                return False
            ctx.exit_reconciliation_failures = int(ctx.exit_reconciliation_failures or 0) + 1
            if ctx.status == PositionStatus.EXIT_SUBMITTING:
                ctx.status = PositionStatus.EXIT_UNKNOWN
            self.store.save(self.state)
            if ctx.exit_reconciliation_failures > int(self.cfg.observation_retry_budget):
                self.trigger_hard_halt(f"P0-1D: {symbol} emergency-exit reconciliation budget exhausted without an exact broker-order match.", source="EXIT_RECONCILIATION")
                return True
            return False
        except Exception as exc:
            if "FAIL_CLOSED" in str(exc):
                self.trigger_hard_halt(f"P0-1D: {symbol} malformed emergency-exit reconciliation observation: {exc}", source="EXIT_RECONCILIATION")
                return True
            ctx.exit_reconciliation_failures = int(ctx.exit_reconciliation_failures or 0) + 1
            try:
                self.store.save(self.state)
            except Exception:
                pass
            if ctx.exit_reconciliation_failures > int(self.cfg.observation_retry_budget):
                self.trigger_hard_halt(f"P0-1D: {symbol} emergency-exit reconciliation failed repeatedly: {exc}", source="EXIT_RECONCILIATION")
                return True
            return False

    def _reconcile_entry_fill(self, symbol: str, ctx: TradeContext, active_pos, matching_pos, open_orders) -> bool:
        entry_order = next((o for o in open_orders if str(o.get("order_id")) == str(ctx.entry_order_id)), None)
        if entry_order is None:
            try:
                entry_order = self.broker.get_order_details(ctx.entry_order_id)
            except Exception:
                entry_order = None
        if entry_order is None:
            self.trigger_hard_halt(f"Reconciliation Failure (P0): {symbol} local entry has no broker order evidence.")
            return True

        status = self._order_status(entry_order)
        filled = self._filled_qty(entry_order)
        target = int(ctx.target_qty)
        if filled > target:
            self.trigger_hard_halt(f"Reconciliation Failure: {symbol} broker entry fill exceeds local target.")
            return True

        if filled == 0:
            if status in ACTIVE_ORDER_STATUSES and not matching_pos:
                ctx.filled_qty = 0
                ctx.pending_qty = target
                ctx.order_status = "PENDING"
                ctx.status = PositionStatus.ENTRY_PENDING
                self.store.save(self.state)
                return False
            if status in {"REJECTED", "CANCELLED", "EXPIRED"} and not matching_pos:
                del self.state.active_trades[symbol]
                self.store.save(self.state)
                self.audit.log("ORDER_DEAD_NO_FILL", symbol=symbol, order_id=ctx.entry_order_id, status=status)
                return False
            self.trigger_hard_halt(f"Reconciliation Failure: {symbol} zero-fill entry does not reconcile with broker state.")
            return True

        if 0 < filled < target:
            if len(matching_pos) != 1:
                self.trigger_hard_halt(f"Reconciliation Failure: {symbol} partial entry lacks a unique matching broker position.")
                return True
            pos_qty = int(matching_pos[0].get("quantity", 0) or 0)
            if pos_qty != filled:
                self.trigger_hard_halt(f"Reconciliation Failure: {symbol} partial entry quantity disagrees with broker position.")
                return True
            ctx.filled_qty = filled
            ctx.pending_qty = target - filled
            ctx.order_status = "PARTIAL"
            ctx.status = PositionStatus.PARTIAL_POSITION
            self.store.save(self.state)
            return False

        if filled == target and status == "COMPLETE":
            if len(matching_pos) != 1:
                self.trigger_hard_halt(f"Reconciliation Failure: {symbol} completed entry lacks a unique matching broker position.")
                return True
            ctx.status = PositionStatus.PROTECTION
            ctx.filled_qty = target
            ctx.pending_qty = 0
            ctx.order_status = "COMPLETE"
            self.store.save(self.state)
            return False

        self.trigger_hard_halt(f"Reconciliation Failure: {symbol} ambiguous entry state status={status}, filled={filled}.")
        return True

    # -- step(): per-position lifecycle dispatch --------------------------------

    def step(self) -> Dict[str, str]:
        """Advance the engine one poll cycle. Returns a per-symbol outcome
        map; a bare "HALTED" is returned for the whole call (as
        `{"__engine__": "HALTED"}`) when the engine itself is not RUNNING."""
        if self.terminator.halted:
            return {"__engine__": "HALTED"}
        if self.state.status == EngineStatus.RECONCILIATION_HALT or self.state.clearance_required:
            return {"__engine__": "HALTED"}

        if self.state.status == EngineStatus.STARTUP:
            self.reconcile_startup()
            if self.state.status == EngineStatus.RECONCILIATION_HALT:
                return {"__engine__": "HALTED"}
            return {"__engine__": "STATE_CHANGED"}

        if self.state.status != EngineStatus.RUNNING:
            return {"__engine__": "HALTED"}

        results: Dict[str, str] = {}
        for symbol in list(self.state.active_trades):
            ctx = self.state.active_trades.get(symbol)
            if ctx is None:
                continue  # removed mid-loop by an earlier symbol's step (e.g. abandoned entry)
            try:
                results[symbol] = self._step_position(symbol, ctx)
            except Exception as exc:
                self.trigger_hard_halt(f"Engine V3.4-P02 exception on {symbol}: {exc}")
                results[symbol] = "HALTED"
            if self.state.status == EngineStatus.RECONCILIATION_HALT:
                # Whole-engine halt: stop dispatching further symbols this
                # cycle too - no partial-symbol quarantine (spec §2/§9 of
                # the V17 plan).
                break
        return results

    def _step_position(self, symbol: str, ctx: TradeContext) -> str:
        status = ctx.status

        if status in {PositionStatus.ENTRY_SUBMITTING, PositionStatus.ENTRY_UNKNOWN}:
            open_orders = self.broker.get_orders()
            halted = self._reconcile_unknown_entry(symbol, ctx, open_orders)
            if halted:
                return "HALTED"
            # _reconcile_unknown_entry only reports halted-or-not; a
            # successful fingerprint recovery (-> ENTRY_PENDING) is real
            # progress and must be reported as such, not folded into the
            # same NO_ACTION outcome as "still unresolved, retry budget ticked."
            return "STATE_CHANGED" if ctx.status != status else "NO_ACTION"

        if status == PositionStatus.ENTRY_SUBMIT:
            return self._step_entry_submit(symbol, ctx)

        if status in {PositionStatus.ENTRY_PENDING, PositionStatus.PARTIAL_POSITION} and not ctx.stop_order_id:
            return self._step_entry_fill(symbol, ctx)

        if status == PositionStatus.PARTIAL_POSITION and ctx.stop_order_id:
            return self._step_partial_protective_fill(symbol, ctx)

        if status == PositionStatus.PROTECTION:
            return self._step_protection(symbol, ctx)

        if status == PositionStatus.PROTECTION_PENDING:
            return self._step_protection_pending(symbol, ctx)

        if status == PositionStatus.MANAGING:
            return self._step_managing(symbol, ctx)

        if status in {PositionStatus.EXIT_SUBMITTING, PositionStatus.EXIT_UNKNOWN}:
            open_orders = self.broker.get_orders()
            halted = self._reconcile_unknown_exit(symbol, ctx, open_orders)
            if halted:
                return "HALTED"
            return "STATE_CHANGED" if ctx.status != status else "NO_ACTION"

        if status == PositionStatus.EXIT_SUBMIT:
            return self._step_exit_submit(symbol, ctx)

        if status == PositionStatus.EXIT_PENDING:
            return self._step_exit_pending(symbol, ctx)

        self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} unsupported V3.4-P02 state '{status.value}'.")
        return "HALTED"

    def _step_entry_submit(self, symbol: str, ctx: TradeContext) -> str:
        if ctx.entry_order_id:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} ENTRY_SUBMIT already contains an entry_order_id.")
            return "HALTED"
        if ctx.entry_tag != DEFAULT_ENTRY_TAG:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} ENTRY_SUBMIT has invalid entry tag.")
            return "HALTED"
        if ctx.target_qty <= 0:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} ENTRY_SUBMIT target quantity must be positive.")
            return "HALTED"
        if ctx.entry_price <= 0 or not ctx.entry_price.is_finite():
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} ENTRY_SUBMIT entry price must be positive and finite.")
            return "HALTED"

        positions = self._observe(symbol, ctx.status, "get_positions:entry_submit", self.broker.get_positions)
        if positions is OBSERVATION_RETRY:
            return "HALTED" if self.terminator.halted else "NO_ACTION"
        matching_pos = self._matching_positions(self._active_positions(positions), symbol=symbol, product=self.cfg.product)
        if matching_pos:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} ENTRY_SUBMIT found an existing broker position.")
            return "HALTED"

        open_orders = self._observe(symbol, ctx.status, "get_orders:entry_submit", self.broker.get_orders)
        if open_orders is OBSERVATION_RETRY:
            return "HALTED" if self.terminator.halted else "NO_ACTION"
        if not isinstance(open_orders, list):
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} broker orders response is not a list.")
            return "HALTED"

        matching_orders, conflicting_orders = [], []
        for order in open_orders:
            if not isinstance(order, dict):
                self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} broker order entry is not an object.")
                return "HALTED"
            if order.get("status") not in ACTIVE_ORDER_STATUSES:
                continue
            if str(order.get("transaction_type", "")).upper() != "BUY":
                continue
            if str(order.get("tradingsymbol", "")).upper() != symbol:
                continue
            try:
                order_qty = int(order.get("quantity", 0) or 0)
                order_price = Decimal(str(order.get("price", 0) or 0))
            except Exception:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} existing BUY order has malformed quantity/price.")
                return "HALTED"
            if (
                str(order.get("tag", "") or "") == ctx.entry_tag
                and order_qty == ctx.target_qty
                and order_price == ctx.entry_price
                and str(order.get("exchange", "NSE")) == "NSE"
                and str(order.get("product", "")) == self.cfg.product
            ):
                matching_orders.append(order)
            else:
                conflicting_orders.append(order)

        if len(matching_orders) > 1:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} multiple active broker BUY orders match the durable entry intent.")
            return "HALTED"
        if conflicting_orders:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} active same-symbol BUY order conflicts with durable entry intent.")
            return "HALTED"

        if len(matching_orders) == 1:
            existing_id = matching_orders[0].get("order_id")
            if not isinstance(existing_id, str) or not existing_id.strip():
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} matching broker entry order has no valid order ID.")
                return "HALTED"
            ctx.entry_order_id = existing_id
            ctx.order_status = str(matching_orders[0].get("status", "OPEN"))
            ctx.pending_qty = ctx.target_qty
            ctx.status = PositionStatus.ENTRY_PENDING
            self.store.save(self.state)
            self.audit.log("ENTRY_INTENT_RECONCILED_TO_EXISTING_ORDER", symbol=symbol, order_id=existing_id, quantity=ctx.target_qty)
            return "STATE_CHANGED"

        fingerprint = self._build_entry_submission_fingerprint(symbol=symbol, quantity=ctx.target_qty, price=ctx.entry_price)
        if ctx.entry_submission_fingerprint is not None:
            if ctx.entry_submission_fingerprint != fingerprint:
                self.trigger_hard_halt(f"P0-1D: {symbol} existing entry fingerprint conflicts with current intent.", source="ENTRY_SUBMIT")
                return "HALTED"
        else:
            ctx.entry_submission_fingerprint = fingerprint
        ctx.entry_reconciliation_failures = 0
        ctx.status = PositionStatus.ENTRY_SUBMITTING
        self.store.save(self.state)

        try:
            order_id = self.broker.place_order(
                exchange="NSE", tradingsymbol=symbol, transaction_type="BUY",
                quantity=ctx.target_qty, product=self.cfg.product, order_type="LIMIT",
                price=float(ctx.entry_price), tag=ctx.entry_tag,
            )
        except EntryPolicyDeclinedError as exc:
            # ENTRY_LOCK class (spec §2): never touches terminator.halted or
            # RECONCILIATION_HALT. Abandon this one entry; every other
            # position, and this symbol's future entries once the lock
            # clears, are completely unaffected.
            del self.state.active_trades[symbol]
            self.store.save(self.state)
            self.audit.log("ENTRY_ABANDONED_POLICY_HALT", symbol=symbol, reason=exc.reason)
            return "ENTRY_ABANDONED_POLICY_HALT"
        except Exception as exc:
            if self._is_transient_submission_exception(exc):
                ctx.status = PositionStatus.ENTRY_UNKNOWN
                self.store.save(self.state)
                return "STATE_CHANGED"
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} entry submission rejected/failed with known outcome: {exc}", source="ENTRY_SUBMIT")
            return "HALTED"

        if not isinstance(order_id, str) or not order_id.strip():
            ctx.status = PositionStatus.ENTRY_UNKNOWN
            self.store.save(self.state)
            return "STATE_CHANGED"

        ctx.entry_order_id = order_id
        ctx.order_status = "SUBMITTED"
        ctx.pending_qty = ctx.target_qty
        ctx.status = PositionStatus.ENTRY_PENDING
        self.store.save(self.state)
        self.audit.log("ENTRY_ORDER_SUBMITTED", symbol=symbol, order_id=order_id, quantity=ctx.target_qty, price=str(ctx.entry_price))
        return "STATE_CHANGED"

    def _step_entry_fill(self, symbol: str, ctx: TradeContext) -> str:
        if not ctx.entry_order_id:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} ENTRY_PENDING without entry_order_id.")
            return "HALTED"
        try:
            order_info = self.broker.get_order_details(ctx.entry_order_id)
            status = self._order_status(order_info)
            filled_qty = self._filled_qty(order_info)
            target_qty = int(ctx.target_qty)
            if target_qty <= 0:
                raise RuntimeError("Entry target quantity must be positive.")
            avg_price = Decimal(str(order_info.get("average_price", 0) or 0))
            if avg_price < 0:
                raise RuntimeError("Entry average price cannot be negative.")
        except Exception as exc:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} invalid entry order payload: {exc}")
            return "HALTED"

        if filled_qty > target_qty:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} broker entry filled_quantity exceeds requested quantity.")
            return "HALTED"

        if status in {"REJECTED", "CANCELLED", "EXPIRED"}:
            if filled_qty == 0:
                self.audit.log("ORDER_DEAD_NO_FILL", symbol=symbol, order_id=ctx.entry_order_id, status=status)
                del self.state.active_trades[symbol]
                self.store.save(self.state)
                return "STATE_CHANGED"
            positions = self.broker.get_positions()
            matching = self._matching_positions(self._active_positions(positions), symbol=symbol, product=self.cfg.product, quantity=filled_qty)
            if len(matching) != 1:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} entry order terminated after partial fill, but broker position cannot be uniquely reconciled.")
                return "HALTED"
            ctx.filled_qty = filled_qty
            ctx.pending_qty = max(target_qty - filled_qty, 0)
            if avg_price > 0:
                ctx.avg_entry_price = avg_price
            ctx.order_status = "PARTIAL"
            ctx.status = PositionStatus.PARTIAL_POSITION
            self.store.save(self.state)
            return "STATE_CHANGED"

        if filled_qty == 0:
            if status not in ACTIVE_ORDER_STATUSES:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} zero-fill entry has unexpected status '{status}'.")
                return "HALTED"
            ctx.filled_qty = 0
            ctx.pending_qty = target_qty
            ctx.order_status = "PENDING"
            ctx.status = PositionStatus.ENTRY_PENDING
            self.store.save(self.state)
            return "NO_ACTION"

        if 0 < filled_qty < target_qty:
            if status not in ACTIVE_ORDER_STATUSES and status != "COMPLETE":
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} partial entry has invalid status '{status}'.")
                return "HALTED"
            positions = self.broker.get_positions()
            matching = self._matching_positions(self._active_positions(positions), symbol=symbol, product=self.cfg.product, quantity=filled_qty)
            if len(matching) != 1:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} partial entry fill does not match a unique broker position.")
                return "HALTED"
            pos_avg = Decimal(str(matching[0].get("average_price", 0) or 0))
            if pos_avg <= 0:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} partial entry broker position has invalid average price.")
                return "HALTED"
            ctx.filled_qty = filled_qty
            ctx.pending_qty = target_qty - filled_qty
            ctx.avg_entry_price = pos_avg
            ctx.order_status = "PARTIAL"
            ctx.status = PositionStatus.PARTIAL_POSITION
            self.store.save(self.state)
            return "STATE_CHANGED"

        if filled_qty == target_qty:
            if status != "COMPLETE":
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} full entry quantity reported with non-COMPLETE status '{status}'.")
                return "HALTED"
            positions = self.broker.get_positions()
            matching = self._matching_positions(self._active_positions(positions), symbol=symbol, product=self.cfg.product, quantity=target_qty)
            if len(matching) != 1:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} entry COMPLETE but broker position is not uniquely reconciled.")
                return "HALTED"
            pos_avg = Decimal(str(matching[0].get("average_price", 0) or 0))
            if pos_avg <= 0:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} entry complete with invalid broker average price.")
                return "HALTED"
            ctx.filled_qty = target_qty
            ctx.pending_qty = 0
            ctx.avg_entry_price = pos_avg
            ctx.order_status = "COMPLETE"
            ctx.status = PositionStatus.PROTECTION
            self.store.save(self.state)
            return "STATE_CHANGED"

        self.trigger_hard_halt(f"CRITICAL P0: {symbol} entry order reached an impossible lifecycle state.")
        return "HALTED"

    def _step_protection(self, symbol: str, ctx: TradeContext) -> str:
        # No-mandatory-SL policy: zero matching SL orders is expected and
        # falls straight through to MANAGING with stop_order_id=None.
        # Ambiguity (a malformed broker response, or more than one
        # matching order) still hard-halts.
        if ctx.filled_qty <= 0 or ctx.avg_entry_price <= 0:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} PROTECTION lacks a verified position context.")
            return "HALTED"
        open_orders = self.broker.get_orders()
        if not isinstance(open_orders, list):
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} malformed broker orders response.")
            return "HALTED"
        matching_sl = [
            o for o in open_orders
            if o.get("tradingsymbol") == symbol
            and o.get("exchange", EXCHANGE) == EXCHANGE
            and o.get("product") == self.cfg.product
            and o.get("tag") in SL_TAGS
            and o.get("transaction_type") == "SELL"
            and o.get("status") in ACTIVE_ORDER_STATUSES
        ]
        if len(matching_sl) > 1:
            self.trigger_hard_halt(f"Reconciliation Failure: {symbol} PROTECTION found more than one active matching SL order.")
            return "HALTED"
        if not matching_sl:
            ctx.stop_order_id = None
            ctx.status = PositionStatus.MANAGING
            self.store.save(self.state)
            self.audit.log("PROTECTION_NO_STOP_BY_DESIGN", symbol=symbol)
            return "STATE_CHANGED"

        sl = matching_sl[0]
        try:
            sl_qty = self._order_qty(sl)
        except Exception as exc:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} invalid protective order: {exc}")
            return "HALTED"
        if sl_qty != ctx.filled_qty:
            self.trigger_hard_halt(f"Reconciliation Failure: {symbol} protective stop quantity does not match verified position.")
            return "HALTED"
        ctx.stop_order_id = sl.get("order_id")
        ctx.status = PositionStatus.PROTECTION_PENDING
        self.store.save(self.state)
        return "STATE_CHANGED"

    def _step_protection_pending(self, symbol: str, ctx: TradeContext) -> str:
        if not ctx.stop_order_id:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} PROTECTION_PENDING without stop_order_id.")
            return "HALTED"
        try:
            sl_info = self.broker.get_order_details(ctx.stop_order_id)
            sl_status = self._order_status(sl_info)
            sl_qty = self._order_qty(sl_info)
        except Exception as exc:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} failed to validate protective order: {exc}")
            return "HALTED"

        matching_pos = self._matching_positions(self._active_positions(self.broker.get_positions()), symbol=symbol, product=self.cfg.product, quantity=ctx.filled_qty)
        if len(matching_pos) != 1:
            self.trigger_hard_halt(f"Reconciliation Failure: {symbol} protection confirmation requires exactly one matching broker position.")
            return "HALTED"
        if sl_qty != ctx.filled_qty:
            self.trigger_hard_halt(f"Reconciliation Failure: {symbol} protective stop quantity does not match position quantity.")
            return "HALTED"
        if sl_status not in ACTIVE_ORDER_STATUSES:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective stop is not active; status='{sl_status}'.")
            return "HALTED"

        ctx.status = PositionStatus.MANAGING
        self.store.save(self.state)
        self.audit.log("PROTECTIVE_SL_CONFIRMED_ACTIVE", symbol=symbol, order_id=ctx.stop_order_id, quantity=sl_qty)
        return "STATE_CHANGED"

    def _step_managing(self, symbol: str, ctx: TradeContext) -> str:
        if ctx.filled_qty <= 0 or ctx.avg_entry_price <= 0:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} MANAGING lacks a verified active position.")
            return "HALTED"

        quotes = self._observe(symbol, ctx.status, "ltp", lambda: self.broker.ltp([symbol]))
        if quotes is OBSERVATION_RETRY:
            return "HALTED" if self.terminator.halted else "NO_ACTION"
        if not isinstance(quotes, dict):
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} malformed LTP response.")
            return "HALTED"
        quote = quotes.get(f"NSE:{symbol}", quotes.get(symbol, {}))
        if not isinstance(quote, dict):
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} malformed quote payload.")
            return "HALTED"
        ltp_val = quote.get("last_price")
        if ltp_val is None:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} market data unavailable for active position.")
            return "HALTED"
        try:
            ltp = Decimal(str(ltp_val))
            if ltp <= 0 or not math.isfinite(float(ltp)):
                raise ValueError("Non-finite LTP")
        except Exception as exc:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} malformed LTP received: {exc}")
            return "HALTED"

        # No-mandatory-SL policy: if stop_order_id is None, there is no
        # protective order to poll. Verify the broker position still
        # matches, refresh MTM, and sit at NO_ACTION awaiting
        # request_exit(). This branch never hard-halts on the absence of
        # a stop - only on ambiguity in what IS observed.
        if not ctx.stop_order_id:
            matching_pos = self._matching_positions(self._active_positions(self.broker.get_positions()), symbol=symbol, product=self.cfg.product)
            if len(matching_pos) != 1:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} MANAGING (unprotected) requires exactly one matching broker position.")
                return "HALTED"
            broker_qty = int(matching_pos[0].get("quantity", 0) or 0)
            if broker_qty <= 0:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} broker position quantity is non-positive.")
                return "HALTED"
            # No pending order of any kind explains a quantity change on an
            # unprotected MANAGING position (no stop, no exit in flight) -
            # an unexplained change here is exactly the class of divergence
            # this design refuses to silently absorb (same discipline as
            # the reconciliation quantity check and spec §5's unexplained-
            # equity-change rule, generalized to the ongoing poll loop).
            if broker_qty != ctx.filled_qty:
                self.trigger_hard_halt(
                    f"CRITICAL P0: {symbol} broker-reported quantity ({broker_qty}) changed "
                    f"unexpectedly from locally recorded ({ctx.filled_qty}) with no explaining order."
                )
                return "HALTED"
            ctx.pending_qty = 0
            self.state.unrealised_mtm = str(Decimal(self.state.unrealised_mtm) + (ltp - ctx.avg_entry_price) * Decimal(broker_qty))
            self.store.save(self.state)
            return "NO_ACTION"

        # An operator-adopted discretionary SL exists - poll it exactly as
        # the ported single-position logic did.
        try:
            sl_info = self.broker.get_order_details(ctx.stop_order_id)
            sl_status = self._order_status(sl_info)
            sl_filled = self._filled_qty(sl_info)
            sl_qty = self._order_qty(sl_info)
        except Exception as exc:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} failed to fetch protective stop details: {exc}")
            return "HALTED"

        active_pos = self._active_positions(self.broker.get_positions())
        matching_pos = self._matching_positions(active_pos, symbol=symbol, product=self.cfg.product)
        if len(matching_pos) != 1:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} MANAGING requires exactly one matching broker position.")
            return "HALTED"
        broker_qty = int(matching_pos[0].get("quantity", 0) or 0)
        if broker_qty <= 0:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} broker position quantity is non-positive.")
            return "HALTED"
        if sl_filled > sl_qty:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective order filled_quantity exceeds order quantity.")
            return "HALTED"

        if sl_status == "COMPLETE":
            if sl_filled != sl_qty:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective order COMPLETE with incomplete fill quantity.")
                return "HALTED"
            if broker_qty == 0:
                del self.state.active_trades[symbol]
                self.store.save(self.state)
                return "STATE_CHANGED"
            ctx.filled_qty = broker_qty
            ctx.pending_qty = broker_qty
            ctx.status = PositionStatus.EXIT_SUBMIT
            self.store.save(self.state)
            self.audit.log("SL_COMPLETE_RESIDUAL_POSITION_EXIT_REQUIRED", symbol=symbol, order_id=ctx.stop_order_id, residual_qty=broker_qty)
            return "STATE_CHANGED"

        if sl_filled > 0:
            expected_remaining = ctx.target_qty - sl_filled
            if expected_remaining < 0 or broker_qty != expected_remaining:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective partial fill does not reconcile with broker position.")
                return "HALTED"
            ctx.filled_qty = broker_qty
            ctx.pending_qty = expected_remaining
            ctx.status = PositionStatus.PARTIAL_POSITION
            self.store.save(self.state)
            return "STATE_CHANGED"

        if sl_status in {"CANCELLED", "REJECTED", "EXPIRED"}:
            # The discretionary stop the operator placed is gone; this is
            # not itself dangerous under the no-mandatory-SL policy (it
            # was never required), so fall back to unprotected MANAGING
            # rather than halting.
            ctx.stop_order_id = None
            ctx.filled_qty = broker_qty
            self.store.save(self.state)
            self.audit.log("DISCRETIONARY_SL_LOST_FALLING_BACK_TO_UNPROTECTED_MANAGING", symbol=symbol, prior_status=sl_status)
            return "STATE_CHANGED"

        if sl_status not in ACTIVE_ORDER_STATUSES:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} ambiguous protective stop status '{sl_status}'.")
            return "HALTED"
        if sl_qty != broker_qty:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} active protective stop quantity does not match broker position.")
            return "HALTED"

        ctx.filled_qty = broker_qty
        ctx.pending_qty = 0
        self.state.unrealised_mtm = str(Decimal(self.state.unrealised_mtm) + (ltp - ctx.avg_entry_price) * Decimal(broker_qty))
        self.store.save(self.state)
        return "NO_ACTION"

    def _step_partial_protective_fill(self, symbol: str, ctx: TradeContext) -> str:
        try:
            sl_info = self.broker.get_order_details(ctx.stop_order_id)
            sl_status = self._order_status(sl_info)
            sl_filled = self._filled_qty(sl_info)
            sl_qty = self._order_qty(sl_info)
        except Exception as exc:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} failed to poll partial protective stop: {exc}")
            return "HALTED"
        if sl_qty != ctx.target_qty:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective stop quantity no longer matches original protected position.")
            return "HALTED"
        if sl_filled > sl_qty:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective stop cumulative fill exceeds order quantity.")
            return "HALTED"

        active_pos = self._active_positions(self.broker.get_positions())
        if sl_status == "COMPLETE":
            if sl_filled != sl_qty:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective stop COMPLETE with inconsistent cumulative fill.")
                return "HALTED"
            matching = self._matching_positions(active_pos, symbol=symbol, product=self.cfg.product)
            if not matching:
                del self.state.active_trades[symbol]
                self.store.save(self.state)
                return "STATE_CHANGED"
            if len(matching) != 1:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} SL COMPLETE left an ambiguous broker position.")
                return "HALTED"
            ctx.filled_qty = int(matching[0].get("quantity", 0) or 0)
            ctx.pending_qty = ctx.filled_qty
            ctx.status = PositionStatus.EXIT_SUBMIT
            self.store.save(self.state)
            self.audit.log("SL_COMPLETE_RESIDUAL_POSITION_EXIT_REQUIRED", symbol=symbol, order_id=ctx.stop_order_id, residual_qty=ctx.filled_qty)
            return "STATE_CHANGED"

        if sl_filled > 0:
            matching = self._matching_positions(active_pos, symbol=symbol, product=self.cfg.product)
            if len(matching) != 1:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} partial protective fill has ambiguous broker position state.")
                return "HALTED"
            remaining = int(matching[0].get("quantity", 0) or 0)
            expected_remaining = ctx.target_qty - sl_filled
            if remaining != expected_remaining:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} repeated/cumulative protective fill does not reconcile with broker quantity.")
                return "HALTED"
            ctx.filled_qty = remaining
            ctx.pending_qty = remaining
            ctx.order_status = "SL_PARTIAL"
            self.store.save(self.state)
            return "NO_ACTION"

        if sl_status in {"CANCELLED", "REJECTED", "EXPIRED"}:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} protective stop became terminal while position remains active: {sl_status}.")
            return "HALTED"
        if sl_status not in ACTIVE_ORDER_STATUSES:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} ambiguous partial protective stop status '{sl_status}'.")
            return "HALTED"

        matching = self._matching_positions(active_pos, symbol=symbol, product=self.cfg.product)
        if len(matching) != 1:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} active partial position is not uniquely identifiable.")
            return "HALTED"
        return "NO_ACTION"

    def _step_exit_submit(self, symbol: str, ctx: TradeContext) -> str:
        active_pos = self._active_positions(self.broker.get_positions())
        matching_pos = self._matching_positions(active_pos, symbol=symbol, product=self.cfg.product)

        if not matching_pos:
            del self.state.active_trades[symbol]
            self.store.save(self.state)
            return "STATE_CHANGED"
        if len(matching_pos) != 1:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} EXIT_SUBMIT requires exactly one matching broker position.")
            return "HALTED"

        try:
            pos_qty = int(matching_pos[0].get("quantity", 0) or 0)
        except (TypeError, ValueError):
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} exit position quantity is malformed.")
            return "HALTED"
        if pos_qty <= 0:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} exit position quantity is non-positive.")
            return "HALTED"
        ctx.filled_qty = pos_qty

        try:
            quotes = self._observe(symbol, ctx.status, "ltp:emergency_exit", lambda: self.broker.ltp([symbol]))
            if quotes is OBSERVATION_RETRY:
                return "HALTED" if self.terminator.halted else "NO_ACTION"
            quote = quotes.get(f"NSE:{symbol}") or quotes.get(symbol)
            if not isinstance(quote, dict) or "last_price" not in quote:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} emergency exit LTP is missing or malformed.")
                return "HALTED"
            ltp = Decimal(str(quote.get("last_price", 0) or 0))
            if ltp <= 0:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} emergency exit LTP is non-positive.")
                return "HALTED"

            tick_size = self.broker.get_tick_size(symbol)
            if not isinstance(tick_size, Decimal) or tick_size <= 0:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} emergency exit tick size is invalid.")
                return "HALTED"
            raw_trigger = ltp - tick_size
            trigger = (raw_trigger / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size
            if trigger <= 0 or trigger >= ltp:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} emergency exit trigger cannot be established safely.")
                return "HALTED"

            fingerprint = self._build_exit_submission_fingerprint(symbol=symbol, quantity=pos_qty, trigger_price=trigger, market_protection=self.cfg.market_protection_pct)
            if ctx.exit_submission_fingerprint is not None:
                if ctx.exit_submission_fingerprint != fingerprint:
                    self.trigger_hard_halt(f"P0-1D: {symbol} existing emergency-exit fingerprint conflicts with newly observed intent.", source="EXIT_RECONCILIATION")
                    return "HALTED"
            else:
                ctx.exit_submission_fingerprint = fingerprint
            ctx.exit_reconciliation_failures = 0
            ctx.status = PositionStatus.EXIT_SUBMITTING
            self.store.save(self.state)

            try:
                exit_oid = self.broker.submit_emergency_exit(
                    symbol=symbol, quantity=pos_qty, trigger_price=trigger, source_ltp=ltp,
                    tick_size=tick_size, market_protection=self.cfg.market_protection_pct, tag=DEFAULT_EXIT_TAG,
                )
            except Exception as exc:
                if self._is_transient_submission_exception(exc):
                    ctx.status = PositionStatus.EXIT_UNKNOWN
                    self.store.save(self.state)
                    return "STATE_CHANGED"
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} emergency exit submission rejected/failed: {exc}", source="EXIT_SUBMIT")
                return "HALTED"

            if not isinstance(exit_oid, str) or not exit_oid.strip():
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} emergency exit submission returned no valid order ID.", source="EXIT_SUBMIT")
                return "HALTED"

            ctx.exit_order_id = exit_oid
            ctx.status = PositionStatus.EXIT_PENDING
            self.store.save(self.state)
            return "STATE_CHANGED"
        except Exception as exc:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} failed during emergency-exit preparation/submission: {exc}", source="EXIT_SUBMIT")
            return "HALTED"

    def _step_exit_pending(self, symbol: str, ctx: TradeContext) -> str:
        if not ctx.exit_order_id:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} EXIT_PENDING without exit_order_id.")
            return "HALTED"
        try:
            exit_info = self.broker.get_order_details(ctx.exit_order_id)
            ex_status = self._order_status(exit_info)
            filled_qty = self._filled_qty(exit_info)
            requested_qty = int(ctx.filled_qty)
            order_qty = self._order_qty(exit_info)
        except Exception as exc:
            self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} failed to fetch exit order details: {exc}")
            return "HALTED"

        if requested_qty <= 0 or order_qty != requested_qty:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} exit order quantity does not match the verified position quantity.")
            return "HALTED"
        if filled_qty > requested_qty:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} exit filled_quantity exceeds requested quantity.")
            return "HALTED"

        active_pos = self._active_positions(self.broker.get_positions())
        matching = self._matching_positions(active_pos, symbol=symbol, product=self.cfg.product)

        if 0 < filled_qty < requested_qty:
            if len(matching) != 1:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} partial EXIT has ambiguous broker position state.")
                return "HALTED"
            remaining = int(matching[0].get("quantity", 0) or 0)
            expected_remaining = requested_qty - filled_qty
            if remaining != expected_remaining:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} partial EXIT fill does not reconcile with broker position.")
                return "HALTED"
            ctx.pending_qty = remaining
            self.store.save(self.state)
            return "NO_ACTION"

        if ex_status == "COMPLETE":
            if filled_qty != requested_qty:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} EXIT COMPLETE with incomplete cumulative fill.")
                return "HALTED"
            if matching:
                self.trigger_hard_halt(f"CRITICAL P0: {symbol} exit COMPLETE but residual broker position exists.")
                return "HALTED"
            self.audit.log("VERIFIED_EMERGENCY_EXIT_COMPLETE", symbol=symbol, order_id=ctx.exit_order_id, filled_qty=filled_qty)
            del self.state.active_trades[symbol]
            self.store.save(self.state)
            return "STATE_CHANGED"

        if ex_status in {"REJECTED", "CANCELLED", "EXPIRED"}:
            self.trigger_hard_halt(f"CRITICAL P0: {symbol} emergency exit order failed with status {ex_status}.")
            return "HALTED"
        if ex_status in ACTIVE_ORDER_STATUSES:
            return "NO_ACTION"

        self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: {symbol} unhandled exit status '{ex_status}'.")
        return "HALTED"
