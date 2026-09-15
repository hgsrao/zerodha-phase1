from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any
import math

IST = ZoneInfo("Asia/Kolkata")
EXCHANGE = "NSE"

TERMINAL_ORDER_STATUSES = {
    "COMPLETE",
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
}

ACTIVE_ORDER_STATUSES = {
    "OPEN",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
    "AMO REQ RECEIVED",
    "MODIFY PENDING",
    "CANCEL PENDING",
}


@dataclass(frozen=True)
class Config:
    alert_webhook_url: str
    max_daily_loss: Decimal
    market_protection_pct: Decimal = Decimal("1.0")
    stop_loss_pct: Decimal = Decimal("0.02")
    tick_size: Decimal = Decimal("0.05")
    holidays: frozenset[date] = field(default_factory=frozenset)
    trial_capital: Decimal = Decimal("20000")
    observation_retry_budget: int = 3
    universe: List[str] = field(default_factory=lambda: [
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS"
    ])


@dataclass
class TradeContext:
    symbol: str
    entry_tag: str
    target_qty: int
    tranche_qty: int
    filled_qty: int = 0
    pending_qty: int = 0
    entry_order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    order_status: str = "PENDING"
    executed_tranches: int = 0
    booked_pnl: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    stop_loss_price: Decimal = Decimal("0")
    exit_reason: Optional[str] = None
    entry_max_observed_fill: int = 0
    entry_hwm_order_id: Optional[str] = None
    stop_max_observed_fill: int = 0
    stop_hwm_order_id: Optional[str] = None
    exit_max_observed_fill: int = 0
    exit_hwm_order_id: Optional[str] = None


@dataclass
class BotState:
    trading_day: str
    status: str = "STARTUP"
    realised_net_pnl: str = "0"
    unrealised_mtm: str = "0"
    active_trade: Optional[TradeContext] = None

    # Durable V3.4 safety-lock metadata.
    halt_reason: Optional[str] = None
    halt_source: Optional[str] = None
    halted_at: Optional[str] = None
    clearance_required: bool = False
    operator_acknowledgement: Optional[str] = None


@dataclass
class ObservationTracker:
    """Runtime-only consecutive transient observation failure tracker."""
    consecutive_failures: int = 0
    last_operation: Optional[str] = None


class _ObservationRetrySentinel:
    __slots__ = ()


OBSERVATION_RETRY = _ObservationRetrySentinel()


class TradingEngineV34:
    def _check_cumulative_fill_hwm(
        self,
        current_order_id: Optional[str],
        current_fill: int,
        hwm_order_id_attr: str,
        max_fill_attr: str,
    ) -> bool:
        """Enforces monotonic non-decreasing cumulative fills strictly within
        the lifecycle of a specific order ID. Automatically resets baseline on rotation.
        """
        ctx = self.state.active_trade
        if not ctx or not current_order_id:
            return True

        tracked_id = getattr(ctx, hwm_order_id_attr)
        current_hwm = getattr(ctx, max_fill_attr)

        if current_order_id != tracked_id:
            setattr(ctx, hwm_order_id_attr, current_order_id)
            setattr(ctx, max_fill_attr, current_fill)
            self.store.save(self.state)
            return True

        if current_fill < current_hwm:
            return False

        if current_fill > current_hwm:
            setattr(ctx, max_fill_attr, current_fill)
            self.store.save(self.state)

        return True

    """
    V3.4.0-B: Execution Integrity, Durable Safety & Management Lifecycle.

    Core rule:
    The engine never advances a state merely because it requested an action.
    A transition requires independently verified broker reality.
    """

    def __init__(
        self,
        broker,
        clock,
        sleeper,
        store,
        audit,
        alert,
        lock,
        terminator,
        cfg: Config,
    ):
        self.broker = broker
        self.clock = clock
        self.sleeper = sleeper
        self.store = store
        self.audit = audit
        self.alert = alert
        self.lock_provider = lock
        self.terminator = terminator
        self.cfg = cfg
        if int(self.cfg.observation_retry_budget) < 0:
            raise ValueError("observation_retry_budget must be >= 0")
        self._observation_trackers: Dict[str, ObservationTracker] = {}

        self._lock_ref = self.lock_provider.acquire()
        self.state = self.store.load(self.clock.now().date())

        # Architectural Fix: Never trust a persisted FLAT state. Force broker reconciliation.
        if self.state.status == "FLAT":
            self.state.status = "STARTUP"

        if self.state.clearance_required or self.state.status == "RECONCILIATION_HALT":
            reason = (
                self.state.halt_reason
                or "Persisted durable reconciliation halt active on startup."
            )
            self.terminator.halt(reason)
            return

    @staticmethod
    def _is_transient_observation_exception(exc: Exception) -> bool:
        """Only classify known transport failures as transient; unknown errors fail closed."""
        cls = type(exc)
        
        if cls.__name__ in {"TimeoutError", "ConnectionError", "ReadTimeout", "ConnectTimeout"}:
            return True
            
        module = getattr(cls, "__module__", "")
        if module.startswith("requests.exceptions") and cls.__name__ in {
            "Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError"
        }:
            return True
            
        # Kite Connect network failures (Decoupled from direct import)
        if module.startswith("kiteconnect.exceptions") and cls.__name__ == "NetworkException":
            code = getattr(exc, "code", None)
            
            if code in {502, 503, 504}:
                return True
                
            return False
            
        return False

    def _observation_key(self, operation: str) -> str:
        return f"{self.state.status}:{operation}"

    def _observe(self, operation: str, fn):
        """Perform one broker observation with bounded, poll-cycle retry semantics."""
        key = self._observation_key(operation)
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
                    "OBSERVATION_FAILURE",
                    state=self.state.status,
                    operation=operation,
                    attempt=failure_no,
                    consecutive_failures=failure_no,
                    budget=budget,
                    remaining_budget=remaining,
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                    action="HARD_HALT" if failure_no > budget else "RETRY_NEXT_POLL",
                )
            except Exception:
                pass

            if failure_no > budget:
                self.trigger_hard_halt(
                    f"Observation Budget Exhausted: {operation} failed "
                    f"{failure_no} consecutive times. Last error: {exc}",
                    source="RESILIENCE_LAYER",
                )
            return OBSERVATION_RETRY

        tracker.consecutive_failures = 0
        tracker.last_operation = operation
        return result

    def trigger_hard_halt(self, reason: str, source: str = "ENGINE") -> None:
        self.state.status = "RECONCILIATION_HALT"
        self.state.halt_reason = reason
        self.state.halt_source = source
        self.state.halted_at = self.clock.now().isoformat()
        self.state.clearance_required = True
        self.store.save(self.state)

        try:
            self.audit.log(
                "HARD_HALT",
                reason=reason,
                source=source,
                halted_at=self.state.halted_at,
            )
        except Exception:
            pass

        try:
            self.alert.send("CRITICAL", reason)
        except Exception:
            pass

        self.terminator.halt(reason)

    @staticmethod
    def _active_positions(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return non-zero NET positions only; reject malformed broker payloads."""
        if not isinstance(positions, list):
            raise RuntimeError("FAIL_CLOSED: Broker positions response is not a list.")

        active: List[Dict[str, Any]] = []
        for p in positions:
            if not isinstance(p, dict):
                raise RuntimeError("FAIL_CLOSED: Broker position entry is not an object.")
            qty_raw = p.get("quantity", 0)
            try:
                qty = int(qty_raw or 0)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("FAIL_CLOSED: Broker position quantity is malformed.") from exc
            if qty != 0:
                active.append(p)
        return active

    @staticmethod
    def _active_orders(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return broker orders that are genuinely non-terminal."""
        if not isinstance(orders, list):
            raise RuntimeError("FAIL_CLOSED: Broker orders response is not a list.")
        active: List[Dict[str, Any]] = []
        for o in orders:
            if not isinstance(o, dict):
                raise RuntimeError("FAIL_CLOSED: Broker order entry is not an object.")
            status = o.get("status")
            if status in ACTIVE_ORDER_STATUSES:
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
    def _position_matches(
        pos: Dict[str, Any],
        *,
        symbol: str,
        quantity: int,
        exchange: str = EXCHANGE,
        product: str = "MIS",
    ) -> bool:
        if pos.get("tradingsymbol") != symbol:
            return False
        if pos.get("exchange", exchange) != exchange:
            return False
        if pos.get("product", product) != product:
            return False
        try:
            return int(pos.get("quantity", 0) or 0) == quantity
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _matching_positions(
        positions: List[Dict[str, Any]],
        *,
        symbol: str,
        quantity: Optional[int] = None,
        product: str = "MIS",
    ) -> List[Dict[str, Any]]:
        result = []
        for p in positions:
            if p.get("tradingsymbol") != symbol:
                continue
            if p.get("exchange", EXCHANGE) != EXCHANGE:
                continue
            if p.get("product", product) != product:
                continue
            if quantity is not None:
                try:
                    if int(p.get("quantity", 0) or 0) != quantity:
                        continue
                except (TypeError, ValueError):
                    continue
            result.append(p)
        return result

    def clear_halt_and_reconcile(self, operator_note: str) -> bool:
        if not self.state.clearance_required:
            return True

        if not operator_note or not operator_note.strip():
            self.audit.log(
                "OPERATOR_CLEARANCE_REJECTED",
                reason="Operator acknowledgement is required.",
            )
            return False

        try:
            positions = self.broker.get_positions()
            open_orders = self.broker.get_orders()
            active_pos = self._active_positions(positions)
            active_orders = self._active_orders(open_orders)
        except Exception as exc:
            self.audit.log(
                "OPERATOR_CLEARANCE_REJECTED",
                reason=f"Broker verification failed: {exc}",
            )
            return False

        if active_pos or active_orders:
            self.audit.log(
                "OPERATOR_CLEARANCE_REJECTED",
                reason="Broker state is not clean during clearance check.",
                active_positions=len(active_pos),
                active_orders=len(active_orders),
            )
            return False

        self.audit.log(
            "OPERATOR_CLEARANCE_RECORDED",
            note=operator_note,
        )
        self.state.clearance_required = False
        self.state.operator_acknowledgement = operator_note.strip()
        self.state.halt_reason = None
        self.state.halt_source = None
        self.state.status = "STARTUP"
        self.store.save(self.state)

        # A persisted halt also trips the current process terminator during __init__.
        # Clearance is the explicit operator-authorized exception that releases that
        # runtime latch, but only after the broker-clean check above has succeeded.
        if hasattr(self.terminator, "halted"):
            self.terminator.halted = False
        if hasattr(self.terminator, "reason"):
            self.terminator.reason = None
        self.audit.log("RUNTIME_HALT_CLEARED_AFTER_BROKER_VERIFICATION")
        return True

    def _matching_entry_order(self, ctx: TradeContext, orders: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not ctx.entry_order_id:
            return None
        for order in orders:
            if str(order.get("order_id")) == str(ctx.entry_order_id):
                return order
        return None

    def reconcile_startup(self) -> None:
        """Reconcile local state against broker reality without blind fallthrough."""
        # RECONCILING is an operational wrapper, not a replacement for the
        # persisted lifecycle state. Preserve the state that was actually
        # persisted so recovery routing can evaluate the correct lifecycle.
        original_status = self.state.status
        self.state.status = "RECONCILING"
        self.store.save(self.state)

        try:
            positions = self.broker.get_positions()
            open_orders = self.broker.get_orders()
            active_pos = self._active_positions(positions)
            active_orders = self._active_orders(open_orders)
            local_trade = self.state.active_trade

            if not local_trade:
                if active_pos or active_orders:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Broker has active state while local state is FLAT."
                    )
                    return
                self.audit.log(
                    "STARTUP_RECONCILIATION_PASSED",
                    active_positions=0,
                    active_orders=0,
                )
                self.state.status = "FLAT"
                self.store.save(self.state)
                return

            if not local_trade.symbol or not local_trade.entry_order_id:
                self.trigger_hard_halt(
                    "Reconciliation Failure: Local active trade is missing symbol or entry_order_id."
                )
                return

            # ENTRY_PENDING / partial-entry recovery.
            if original_status in {"ENTRY_PENDING", "PARTIAL_POSITION"} and not local_trade.stop_order_id:
                entry_order = self._matching_entry_order(local_trade, open_orders)
                if entry_order is None:
                    try:
                        entry_order = self.broker.get_order_details(local_trade.entry_order_id)
                    except Exception:
                        entry_order = None

                if entry_order is None:
                    self.trigger_hard_halt(
                        "Reconciliation Failure (P0): Local entry has no broker order evidence."
                    )
                    return

                status = self._order_status(entry_order)
                filled = self._filled_qty(entry_order)
                target = int(local_trade.target_qty)

                if filled > target:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Broker entry fill exceeds local target."
                    )
                    return

                matching_pos = self._matching_positions(
                    active_pos,
                    symbol=local_trade.symbol,
                    product="MIS",
                )

                if filled == 0:
                    if status in ACTIVE_ORDER_STATUSES and not active_pos:
                        local_trade.filled_qty = 0
                        local_trade.pending_qty = target
                        local_trade.order_status = "PENDING"
                        self.state.status = "ENTRY_PENDING"
                        self.store.save(self.state)
                        return
                    if status in {"REJECTED", "CANCELLED", "EXPIRED"} and not active_pos:
                        self.state.active_trade = None
                        self.state.status = "FLAT"
                        self.store.save(self.state)
                        return
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Zero-fill entry does not reconcile with broker state."
                    )
                    return

                if 0 < filled < target:
                    if len(active_pos) != 1 or len(matching_pos) != 1:
                        self.trigger_hard_halt(
                            "Reconciliation Failure: Partial entry lacks a unique matching broker position."
                        )
                        return
                    pos_qty = int(matching_pos[0].get("quantity", 0) or 0)
                    if pos_qty != filled:
                        self.trigger_hard_halt(
                            "Reconciliation Failure: Partial entry quantity disagrees with broker position."
                        )
                        return
                    local_trade.filled_qty = filled
                    local_trade.pending_qty = target - filled
                    local_trade.order_status = "PARTIAL"
                    self.state.status = "PARTIAL_POSITION"
                    self.store.save(self.state)
                    return

                if filled == target and status == "COMPLETE":
                    if len(active_pos) != 1 or len(matching_pos) != 1:
                        self.trigger_hard_halt(
                            "Reconciliation Failure: Completed entry lacks a unique matching broker position."
                        )
                        return
                    self.state.status = "PROTECTION"
                    local_trade.filled_qty = target
                    local_trade.pending_qty = 0
                    local_trade.order_status = "COMPLETE"
                    self.store.save(self.state)
                    return

                self.trigger_hard_halt(
                    f"Reconciliation Failure: Ambiguous entry state status={status}, filled={filled}."
                )
                return

            # Any managed/protection/exit state must have exactly one matching position
            # unless it is explicitly in a terminal exit-reconciliation state.
            matching_pos = self._matching_positions(
                active_pos,
                symbol=local_trade.symbol,
                product="MIS",
            )

            if original_status in {"PROTECTION", "PROTECTION_PENDING", "MANAGING", "EXIT_CANCEL_SL", "EXIT_SUBMIT"}:
                if len(active_pos) != 1 or len(matching_pos) != 1:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Managed state does not map to exactly one broker position."
                    )
                    return

                # For an active managed position, broker quantity must agree with
                # the persisted local target. Partial-entry states are handled
                # separately above and intentionally allow target_qty != broker_qty.
                broker_qty = int(matching_pos[0].get("quantity", 0) or 0)
                local_target_qty = int(local_trade.target_qty)
                if broker_qty != local_target_qty:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Broker position quantity does not "
                        "match persisted local target quantity."
                    )
                    return

                # Never recover directly into MANAGING without proving an active SL.
                if original_status in {"PROTECTION", "PROTECTION_PENDING", "MANAGING"}:
                    sl_orders = [
                        o for o in active_orders
                        if o.get("tradingsymbol") == local_trade.symbol
                        and o.get("exchange", EXCHANGE) == EXCHANGE
                        and o.get("product", "MIS") == "MIS"
                        and o.get("transaction_type") == "SELL"
                        and o.get("tag") in {"V3.3_SL", "V3.4_SL"}
                    ]
                    if len(sl_orders) != 1:
                        self.trigger_hard_halt(
                            "Reconciliation Failure: Managed position lacks exactly one active protective stop."
                        )
                        return
                    sl_qty = self._order_qty(sl_orders[0])
                    if sl_qty != broker_qty:
                        self.trigger_hard_halt(
                            "Reconciliation Failure: Protective stop quantity does not "
                            "match broker position quantity."
                        )
                        return
                    local_trade.stop_order_id = sl_orders[0].get("order_id")
                    self.state.status = "MANAGING"
                    self.store.save(self.state)
                    return

            if original_status == "EXIT_PENDING":
                if local_trade.exit_order_id:
                    exit_order = None
                    for o in open_orders:
                        if str(o.get("order_id")) == str(local_trade.exit_order_id):
                            exit_order = o
                            break
                    if exit_order is None:
                        try:
                            exit_order = self.broker.get_order_details(local_trade.exit_order_id)
                        except Exception:
                            exit_order = None
                    if exit_order is None:
                        self.trigger_hard_halt(
                            "Reconciliation Failure: EXIT_PENDING has no broker order evidence."
                        )
                        return
                    self.state.status = "EXIT_PENDING"
                    self.store.save(self.state)
                    return

            self.trigger_hard_halt(
                f"Reconciliation Failure: Unsupported or unsafe persisted trade state '{self.state.status}'."
            )

        except Exception as exc:
            self.trigger_hard_halt(f"Reconciliation exception: {exc}")

    def step(self) -> str:
        if self.terminator.halted:
            return "HALTED"

        if self.state.status == "RECONCILIATION_HALT" or self.state.clearance_required:
            return "HALTED"

        try:
            if self.state.status == "STARTUP":
                self.reconcile_startup()
                if self.state.status == "RECONCILIATION_HALT":
                    return "HALTED"
                return "STATE_CHANGED"

            elif self.state.status == "PARTIAL_POSITION" and self.state.active_trade and self.state.active_trade.stop_order_id:
                # PARTIAL_POSITION can represent a partially executed protective stop.
                # In that case the entry order is already COMPLETE; poll the SL using
                # cumulative exchange fill quantities and the authoritative broker position.
                ctx = self.state.active_trade
                try:
                    sl_info = self._observe(
                        "get_order_details:protective_stop",
                        lambda: self.broker.get_order_details(ctx.stop_order_id),
                    )
                    if sl_info is OBSERVATION_RETRY:
                        return "HALTED" if self.terminator.halted else "NO_ACTION"
                    sl_status = self._order_status(sl_info)
                    sl_filled = self._filled_qty(sl_info)
                    if not self._check_cumulative_fill_hwm(ctx.stop_order_id, sl_filled, "stop_hwm_order_id", "stop_max_observed_fill"):
                        self.trigger_hard_halt(f"CRITICAL P0: Protective stop cumulative fill regressed for order {ctx.stop_order_id}.")
                        return "HALTED"
                    sl_qty = self._order_qty(sl_info)
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Failed to poll partial protective stop: {exc}"
                    )
                    return "HALTED"

                if sl_qty != ctx.target_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Protective stop quantity no longer matches original protected position."
                    )
                    return "HALTED"

                if sl_filled > sl_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Protective stop cumulative fill exceeds order quantity."
                    )
                    return "HALTED"

                positions = self._observe("get_positions", self.broker.get_positions)
                if positions is OBSERVATION_RETRY:
                    return "HALTED" if self.terminator.halted else "NO_ACTION"
                active_pos = self._active_positions(positions)

                if sl_status == "COMPLETE":
                    if sl_filled != sl_qty:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Protective stop COMPLETE with inconsistent cumulative fill."
                        )
                        return "HALTED"
                    if not active_pos:
                        self.state.active_trade = None
                        self.state.status = "STARTUP"
                        self.store.save(self.state)
                        return "STATE_CHANGED"
                    matching = self._matching_positions(
                        active_pos, symbol=ctx.symbol, product="MIS"
                    )
                    if len(active_pos) != 1 or len(matching) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: SL COMPLETE left an ambiguous broker position."
                        )
                        return "HALTED"
                    ctx.filled_qty = int(matching[0].get("quantity", 0) or 0)
                    ctx.pending_qty = ctx.filled_qty
                    self.state.status = "EXIT_SUBMIT"
                    self.store.save(self.state)
                    self.audit.log(
                        "SL_COMPLETE_RESIDUAL_POSITION_EXIT_REQUIRED",
                        order_id=ctx.stop_order_id,
                        residual_qty=ctx.filled_qty,
                    )
                    return "STATE_CHANGED"

                if sl_filled > 0:
                    if len(active_pos) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Partial protective fill has ambiguous broker position state."
                        )
                        return "HALTED"
                    remaining = int(active_pos[0].get("quantity", 0) or 0)
                    expected_remaining = ctx.target_qty - sl_filled
                    if remaining != expected_remaining:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Repeated/cumulative protective fill does not reconcile with broker quantity."
                        )
                        return "HALTED"
                    ctx.filled_qty = remaining
                    ctx.pending_qty = remaining
                    ctx.order_status = "SL_PARTIAL"
                    self.store.save(self.state)
                    return "NO_ACTION"

                if sl_status in {"CANCELLED", "REJECTED", "EXPIRED"}:
                    self.trigger_hard_halt(
                        f"CRITICAL P0: Protective stop became terminal while position remains active: {sl_status}."
                    )
                    return "HALTED"

                if sl_status not in ACTIVE_ORDER_STATUSES:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Ambiguous partial protective stop status '{sl_status}'."
                    )
                    return "HALTED"

                if len(active_pos) != 1:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Active partial position is not uniquely identifiable."
                    )
                    return "HALTED"

                return "NO_ACTION"

            elif self.state.status in {"ENTRY_PENDING", "PARTIAL_POSITION"}:
                ctx = self.state.active_trade
                if not ctx or not ctx.entry_order_id:
                    self.trigger_hard_halt("FAIL_CLOSED_ERROR: ENTRY_PENDING without entry_order_id.")
                    return "HALTED"

                try:
                    order_info = self.broker.get_order_details(ctx.entry_order_id)
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Failed to fetch entry order details: {exc}"
                    )
                    return "HALTED"

                try:
                    status = self._order_status(order_info)
                    filled_qty = self._filled_qty(order_info)
                    if not self._check_cumulative_fill_hwm(ctx.entry_order_id, filled_qty, "entry_hwm_order_id", "entry_max_observed_fill"):
                        self.trigger_hard_halt(f"CRITICAL P0: Entry cumulative fill regressed for order {ctx.entry_order_id}.")
                        return "HALTED"
                    target_qty = int(ctx.target_qty)
                    if target_qty <= 0:
                        raise RuntimeError("Entry target quantity must be positive.")
                    avg_price = Decimal(str(order_info.get("average_price", 0) or 0))
                    if avg_price < 0:
                        raise RuntimeError("Entry average price cannot be negative.")
                except Exception as exc:
                    self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: Invalid entry order payload: {exc}")
                    return "HALTED"

                if filled_qty > target_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Broker entry filled_quantity exceeds requested quantity."
                    )
                    return "HALTED"

                # Terminal rejection/cancellation must be evaluated BEFORE the zero-fill
                # branch. A rejected/cancelled order is never an active pending order.
                if status in {"REJECTED", "CANCELLED", "EXPIRED"}:
                    if filled_qty == 0:
                        self.audit.log(
                            "ORDER_DEAD_NO_FILL",
                            order_id=ctx.entry_order_id,
                            status=status,
                        )
                        self.state.active_trade = None
                        self.state.status = "FLAT"
                        self.store.save(self.state)
                        return "STATE_CHANGED"

                    # A terminal order with a partial fill leaves a real broker
                    # position. Verify that position before retaining the trade.
                    positions = self.broker.get_positions()
                    active_pos = self._active_positions(positions)
                    matching = self._matching_positions(
                        active_pos,
                        symbol=ctx.symbol,
                        quantity=filled_qty,
                        product="MIS",
                    )
                    if len(active_pos) != 1 or len(matching) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Entry order terminated after partial fill, "
                            "but broker position cannot be uniquely reconciled."
                        )
                        return "HALTED"

                    ctx.filled_qty = filled_qty
                    ctx.pending_qty = max(target_qty - filled_qty, 0)
                    if avg_price > 0:
                        ctx.avg_entry_price = avg_price
                    ctx.order_status = "PARTIAL"
                    self.state.status = "PARTIAL_POSITION"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                # A live order with zero fills remains pending.
                if filled_qty == 0:
                    if status not in ACTIVE_ORDER_STATUSES:
                        self.trigger_hard_halt(
                            f"CRITICAL P0: Zero-fill entry has unexpected status '{status}'."
                        )
                        return "HALTED"
                    ctx.filled_qty = 0
                    ctx.pending_qty = target_qty
                    ctx.order_status = "PENDING"
                    self.state.status = "ENTRY_PENDING"
                    self.store.save(self.state)
                    return "NO_ACTION"

                # Partial fill: preserve cumulative exchange quantity. Never decrement
                # or add the same cumulative fill repeatedly across polling ticks.
                if 0 < filled_qty < target_qty:
                    if status not in ACTIVE_ORDER_STATUSES and status != "COMPLETE":
                        self.trigger_hard_halt(
                            f"CRITICAL P0: Partial entry has invalid status '{status}'."
                        )
                        return "HALTED"

                    positions = self.broker.get_positions()
                    active_pos = self._active_positions(positions)
                    matching = self._matching_positions(
                        active_pos,
                        symbol=ctx.symbol,
                        quantity=filled_qty,
                        product="MIS",
                    )

                    if len(active_pos) != 1 or len(matching) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Partial entry fill does not match a unique broker position."
                        )
                        return "HALTED"

                    pos_avg = Decimal(str(matching[0].get("average_price", 0) or 0))
                    if pos_avg <= 0:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Partial entry broker position has invalid average price."
                        )
                        return "HALTED"
                    ctx.filled_qty = filled_qty
                    ctx.pending_qty = target_qty - filled_qty
                    ctx.avg_entry_price = pos_avg
                    ctx.order_status = "PARTIAL"
                    self.state.status = "PARTIAL_POSITION"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                # Full fill is valid only when the broker explicitly reports COMPLETE.
                if filled_qty == target_qty:
                    if status != "COMPLETE":
                        self.trigger_hard_halt(
                            f"CRITICAL P0: Full entry quantity reported with non-COMPLETE status '{status}'."
                        )
                        return "HALTED"

                    positions = self.broker.get_positions()
                    active_pos = self._active_positions(positions)
                    matching = self._matching_positions(
                        active_pos,
                        symbol=ctx.symbol,
                        quantity=target_qty,
                        product="MIS",
                    )

                    if len(active_pos) != 1 or len(matching) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Entry COMPLETE but broker position is not uniquely reconciled."
                        )
                        return "HALTED"

                    pos = matching[0]
                    pos_avg = Decimal(str(pos.get("average_price", 0) or 0))
                    if pos_avg <= 0:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Entry complete with invalid broker average price."
                        )
                        return "HALTED"

                    ctx.filled_qty = target_qty
                    ctx.pending_qty = 0
                    ctx.avg_entry_price = pos_avg
                    ctx.order_status = "COMPLETE"
                    self.state.status = "PROTECTION"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                self.trigger_hard_halt(
                    "CRITICAL P0: Entry order reached an impossible lifecycle state."
                )
                return "HALTED"

            elif self.state.status == "PROTECTION":
                ctx = self.state.active_trade
                if not ctx or ctx.filled_qty <= 0 or ctx.avg_entry_price <= 0:
                    self.trigger_hard_halt(
                        "FAIL_CLOSED_ERROR: PROTECTION state lacks a verified position context."
                    )
                    return "HALTED"

                open_orders = self.broker.get_orders()
                if not isinstance(open_orders, list):
                    self.trigger_hard_halt("FAIL_CLOSED_ERROR: Malformed broker orders response.")
                    return "HALTED"

                matching_sl = []
                for o in open_orders:
                    if (
                        o.get("tradingsymbol") == ctx.symbol
                        and o.get("exchange", EXCHANGE) == EXCHANGE
                        and o.get("product", "MIS") == "MIS"
                        and o.get("tag") in {"V3.3_SL", "V3.4_SL"}
                        and o.get("transaction_type") == "SELL"
                        and o.get("status") in ACTIVE_ORDER_STATUSES
                    ):
                        matching_sl.append(o)

                if len(matching_sl) != 1:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: PROTECTION requires exactly one active matching SL order."
                    )
                    return "HALTED"

                sl = matching_sl[0]
                try:
                    sl_qty = self._order_qty(sl)
                except Exception as exc:
                    self.trigger_hard_halt(f"FAIL_CLOSED_ERROR: Invalid protective order: {exc}")
                    return "HALTED"

                if sl_qty != ctx.filled_qty:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Protective stop quantity does not match verified position."
                    )
                    return "HALTED"

                ctx.stop_order_id = sl.get("order_id")
                self.state.status = "PROTECTION_PENDING"
                self.store.save(self.state)
                return "STATE_CHANGED"

            elif self.state.status == "PROTECTION_PENDING":
                ctx = self.state.active_trade
                if not ctx or not ctx.stop_order_id:
                    self.trigger_hard_halt(
                        "FAIL_CLOSED_ERROR: PROTECTION_PENDING without stop_order_id."
                    )
                    return "HALTED"

                try:
                    sl_info = self.broker.get_order_details(ctx.stop_order_id)
                    sl_status = self._order_status(sl_info)
                    sl_qty = self._order_qty(sl_info)
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Failed to validate protective order: {exc}"
                    )
                    return "HALTED"

                positions = self.broker.get_positions()
                active_pos = self._active_positions(positions)
                matching_pos = self._matching_positions(
                    active_pos,
                    symbol=ctx.symbol,
                    quantity=ctx.filled_qty,
                    product="MIS",
                )

                if len(active_pos) != 1 or len(matching_pos) != 1:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Protection confirmation requires exactly one matching broker position."
                    )
                    return "HALTED"

                if sl_qty != ctx.filled_qty:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Protective stop quantity does not match position quantity."
                    )
                    return "HALTED"

                if sl_status not in ACTIVE_ORDER_STATUSES:
                    if sl_status in {"CANCELLED", "REJECTED", "EXPIRED", "COMPLETE"}:
                        self.trigger_hard_halt(
                            f"CRITICAL P0: Protective stop is not active; status='{sl_status}'."
                        )
                    else:
                        self.trigger_hard_halt(
                            f"FAIL_CLOSED_ERROR: Ambiguous protective stop status '{sl_status}'."
                        )
                    return "HALTED"

                self.state.status = "MANAGING"
                self.store.save(self.state)
                self.audit.log(
                    "PROTECTIVE_SL_CONFIRMED_ACTIVE",
                    order_id=ctx.stop_order_id,
                    quantity=sl_qty,
                )
                return "STATE_CHANGED"

            elif self.state.status == "MANAGING":
                ctx = self.state.active_trade
                if not ctx or ctx.filled_qty <= 0 or ctx.avg_entry_price <= 0:
                    self.trigger_hard_halt(
                        "FAIL_CLOSED_ERROR: MANAGING state lacks a verified active position."
                    )
                    return "HALTED"

                quotes = self._observe("ltp", lambda: self.broker.ltp([ctx.symbol]))
                if quotes is OBSERVATION_RETRY:
                    return "HALTED" if self.terminator.halted else "NO_ACTION"
                if not isinstance(quotes, dict):
                    self.trigger_hard_halt("FAIL_CLOSED_ERROR: Malformed LTP response.")
                    return "HALTED"
                quote = quotes.get(f"NSE:{ctx.symbol}", quotes.get(ctx.symbol, {}))
                if not isinstance(quote, dict):
                    self.trigger_hard_halt("FAIL_CLOSED_ERROR: Malformed quote payload.")
                    return "HALTED"
                ltp_val = quote.get("last_price")
                if ltp_val is None:
                    self.trigger_hard_halt(
                        "FAIL_CLOSED_ERROR: Market data unavailable for active position."
                    )
                    return "HALTED"
                try:
                    ltp = Decimal(str(ltp_val))
                    if ltp <= 0 or not math.isfinite(float(ltp)):
                        raise ValueError("Non-finite LTP")
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Malformed LTP received: {exc}"
                    )
                    return "HALTED"

                # Do not swallow protective-order query failures. A management loop
                # that cannot prove protection exists must halt, not continue.
                if not ctx.stop_order_id:
                    self.trigger_hard_halt(
                        "CRITICAL P0: MANAGING state has no protective stop order id."
                    )
                    return "HALTED"

                try:
                    sl_info = self._observe(
                        "get_order_details:protective_stop",
                        lambda: self.broker.get_order_details(ctx.stop_order_id),
                    )
                    if sl_info is OBSERVATION_RETRY:
                        return "HALTED" if self.terminator.halted else "NO_ACTION"
                    sl_status = self._order_status(sl_info)
                    sl_filled = self._filled_qty(sl_info)
                    if not self._check_cumulative_fill_hwm(ctx.stop_order_id, sl_filled, "stop_hwm_order_id", "stop_max_observed_fill"):
                        self.trigger_hard_halt(f"CRITICAL P0: Protective stop cumulative fill regressed for order {ctx.stop_order_id}.")
                        return "HALTED"
                    sl_qty = self._order_qty(sl_info)
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Failed to fetch protective stop details: {exc}"
                    )
                    return "HALTED"

                positions = self._observe("get_positions", self.broker.get_positions)
                if positions is OBSERVATION_RETRY:
                    return "HALTED" if self.terminator.halted else "NO_ACTION"
                active_pos = self._active_positions(positions)
                matching_pos = self._matching_positions(
                    active_pos,
                    symbol=ctx.symbol,
                    product="MIS",
                )

                if len(active_pos) != 1 or len(matching_pos) != 1:
                    self.trigger_hard_halt(
                        "CRITICAL P0: MANAGING requires exactly one matching broker position."
                    )
                    return "HALTED"

                broker_qty = int(matching_pos[0].get("quantity", 0) or 0)
                if broker_qty <= 0:
                    self.trigger_hard_halt("CRITICAL P0: Broker position quantity is non-positive.")
                    return "HALTED"

                # Active management has a strict quantity invariant:
                # local target == broker position == protective SL quantity.
                local_target_qty = int(ctx.target_qty)
                if broker_qty != local_target_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Broker position quantity does not match "
                        "local target quantity."
                    )
                    return "HALTED"

                # Protective order fill quantities are cumulative exchange quantities.
                # Repeated observations of the same cumulative fill must not subtract
                # or add quantity again.
                if sl_filled > sl_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Protective order filled_quantity exceeds order quantity."
                    )
                    return "HALTED"

                if sl_status == "COMPLETE":
                    if sl_filled != sl_qty:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Protective order COMPLETE with incomplete fill quantity."
                        )
                        return "HALTED"
                    if broker_qty == 0:
                        self.state.active_trade = None
                        self.state.status = "STARTUP"
                        self.store.save(self.state)
                        return "STATE_CHANGED"
                    # Broker position is authoritative. If the stop is terminal but
                    # shares remain, do not assume the account is flat; route the
                    # verified residual position through emergency exit.
                    ctx.filled_qty = broker_qty
                    ctx.pending_qty = broker_qty
                    self.state.status = "EXIT_SUBMIT"
                    self.store.save(self.state)
                    self.audit.log(
                        "SL_COMPLETE_RESIDUAL_POSITION_EXIT_REQUIRED",
                        order_id=ctx.stop_order_id,
                        residual_qty=broker_qty,
                    )
                    return "STATE_CHANGED"

                if sl_filled > 0:
                    expected_remaining = ctx.target_qty - sl_filled
                    if expected_remaining < 0:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Protective fill exceeds original position quantity."
                        )
                        return "HALTED"

                    if broker_qty != expected_remaining:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Protective partial fill does not reconcile with broker position."
                        )
                        return "HALTED"

                    ctx.filled_qty = broker_qty
                    ctx.pending_qty = expected_remaining
                    self.state.status = "PARTIAL_POSITION"
                    self.store.save(self.state)
                    self.audit.log(
                        "PROTECTIVE_SL_PARTIAL_FILL",
                        order_id=ctx.stop_order_id,
                        cumulative_filled=sl_filled,
                        remaining_position=broker_qty,
                    )
                    return "STATE_CHANGED"

                if sl_status in {"CANCELLED", "REJECTED", "EXPIRED"}:
                    self.trigger_hard_halt(
                        f"CRITICAL P0: Protective stop is no longer active: {sl_status}."
                    )
                    return "HALTED"

                if sl_status not in ACTIVE_ORDER_STATUSES:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Ambiguous protective stop status '{sl_status}'."
                    )
                    return "HALTED"

                if sl_qty != broker_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Active protective stop quantity does not match broker position."
                    )
                    return "HALTED"

                # MTM is calculated from authoritative broker position quantity.
                ctx.filled_qty = broker_qty
                ctx.pending_qty = 0
                self.state.unrealised_mtm = str(
                    (ltp - ctx.avg_entry_price) * Decimal(broker_qty)
                )
                self.store.save(self.state)
                return "NO_ACTION"

            elif self.state.status == "EXIT_CANCEL_SL":
                ctx = self.state.active_trade
                if not ctx or not ctx.stop_order_id:
                    self.trigger_hard_halt(
                        "FAIL_CLOSED_ERROR: EXIT_CANCEL_SL without active trade/stop order."
                    )
                    return "HALTED"

                try:
                    sl_info = self._observe(
                        "get_order_details:protective_stop",
                        lambda: self.broker.get_order_details(ctx.stop_order_id),
                    )
                    if sl_info is OBSERVATION_RETRY:
                        return "HALTED" if self.terminator.halted else "NO_ACTION"
                    sl_status = self._order_status(sl_info)
                    sl_filled = self._filled_qty(sl_info)
                    if not self._check_cumulative_fill_hwm(ctx.stop_order_id, sl_filled, "stop_hwm_order_id", "stop_max_observed_fill"):
                        self.trigger_hard_halt(f"CRITICAL P0: Protective stop cumulative fill regressed for order {ctx.stop_order_id}.")
                        return "HALTED"
                    sl_qty = self._order_qty(sl_info)
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Failed to verify SL cancellation race: {exc}"
                    )
                    return "HALTED"

                positions = self._observe("get_positions", self.broker.get_positions)
                if positions is OBSERVATION_RETRY:
                    return "HALTED" if self.terminator.halted else "NO_ACTION"
                active_pos = self._active_positions(positions)

                if sl_filled > sl_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: SL filled quantity exceeds order quantity during cancellation race."
                    )
                    return "HALTED"

                if sl_status == "COMPLETE":
                    if sl_filled != sl_qty:
                        self.trigger_hard_halt(
                            "CRITICAL P0: SL COMPLETE with inconsistent cumulative fill."
                        )
                        return "HALTED"
                    if not active_pos:
                        self.state.active_trade = None
                        self.state.status = "STARTUP"
                        self.store.save(self.state)
                        return "STATE_CHANGED"
                    matching = self._matching_positions(
                        active_pos, symbol=ctx.symbol, product="MIS"
                    )
                    if len(active_pos) != 1 or len(matching) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: SL completed during cancellation race left an ambiguous position."
                        )
                        return "HALTED"
                    ctx.filled_qty = int(matching[0].get("quantity", 0) or 0)
                    ctx.pending_qty = ctx.filled_qty
                    self.state.status = "EXIT_SUBMIT"
                    self.store.save(self.state)
                    self.audit.log(
                        "SL_COMPLETE_RESIDUAL_POSITION_EXIT_REQUIRED",
                        order_id=ctx.stop_order_id,
                        residual_qty=ctx.filled_qty,
                    )
                    return "STATE_CHANGED"

                if sl_filled > 0:
                    if len(active_pos) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Partial SL fill during cancellation race has ambiguous position state."
                        )
                        return "HALTED"
                    remaining = int(active_pos[0].get("quantity", 0) or 0)
                    expected_remaining = ctx.target_qty - sl_filled
                    if remaining != expected_remaining:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Partial SL fill does not reconcile with broker position."
                        )
                        return "HALTED"
                    ctx.filled_qty = remaining
                    ctx.pending_qty = remaining
                    self.state.status = "PARTIAL_POSITION"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                if sl_status == "CANCELLED":
                    if len(active_pos) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Confirmed SL cancellation but broker position is not uniquely identifiable."
                        )
                        return "HALTED"
                    self.state.status = "EXIT_SUBMIT"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                if sl_status in {"REJECTED", "EXPIRED"}:
                    self.trigger_hard_halt(
                        f"CRITICAL P0: SL cancellation path ended in status {sl_status}."
                    )
                    return "HALTED"

                if sl_status in ACTIVE_ORDER_STATUSES:
                    return "NO_ACTION"

                self.trigger_hard_halt(
                    f"FAIL_CLOSED_ERROR: Ambiguous SL cancellation status '{sl_status}'."
                )
                return "HALTED"

            elif self.state.status == "EXIT_SUBMIT":
                ctx = self.state.active_trade
                if not ctx:
                    self.trigger_hard_halt("FAIL_CLOSED_ERROR: EXIT_SUBMIT without active trade.")
                    return "HALTED"

                positions = self.broker.get_positions()
                active_pos = self._active_positions(positions)
                matching_pos = self._matching_positions(
                    active_pos,
                    symbol=ctx.symbol,
                    product="MIS",
                )

                if len(active_pos) == 0:
                    self.state.active_trade = None
                    self.state.status = "STARTUP"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                if len(active_pos) != 1 or len(matching_pos) != 1:
                    self.trigger_hard_halt(
                        "CRITICAL P0: EXIT_SUBMIT requires exactly one matching broker position and no unexpected positions."
                    )
                    return "HALTED"

                pos = matching_pos[0]
                try:
                    pos_qty = int(pos.get("quantity", 0) or 0)
                except (TypeError, ValueError):
                    self.trigger_hard_halt("CRITICAL P0: Exit position quantity is malformed.")
                    return "HALTED"

                if pos_qty <= 0:
                    self.trigger_hard_halt("CRITICAL P0: Exit position quantity is non-positive.")
                    return "HALTED"

                ctx.filled_qty = pos_qty
                try:
                    exit_oid = self.broker.place_order(
                        variety="regular",
                        exchange=EXCHANGE,
                        tradingsymbol=ctx.symbol,
                        transaction_type="SELL",
                        quantity=pos_qty,
                        product="MIS",
                        order_type="MARKET",
                        tag="V3.4_EXIT",
                    )
                    if not exit_oid:
                        raise RuntimeError("Broker returned an empty exit order id.")
                    ctx.exit_order_id = str(exit_oid)
                    self.state.status = "EXIT_PENDING"
                    self.store.save(self.state)
                    return "STATE_CHANGED"
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"CRITICAL P0: Failed to submit exit order: {exc}"
                    )
                    return "HALTED"

            elif self.state.status == "EXIT_PENDING":
                ctx = self.state.active_trade
                if not ctx or not ctx.exit_order_id:
                    self.trigger_hard_halt(
                        "FAIL_CLOSED_ERROR: EXIT_PENDING without exit_order_id."
                    )
                    return "HALTED"

                try:
                    exit_info = self._observe(
                        "get_order_details:exit",
                        lambda: self.broker.get_order_details(ctx.exit_order_id),
                    )
                    if exit_info is OBSERVATION_RETRY:
                        return "HALTED" if self.terminator.halted else "NO_ACTION"
                    ex_status = self._order_status(exit_info)
                    filled_qty = self._filled_qty(exit_info)
                    if not self._check_cumulative_fill_hwm(ctx.exit_order_id, filled_qty, "exit_hwm_order_id", "exit_max_observed_fill"):
                        self.trigger_hard_halt(f"CRITICAL P0: Exit cumulative fill regressed for order {ctx.exit_order_id}.")
                        return "HALTED"
                    requested_qty = int(ctx.filled_qty)
                    order_qty = self._order_qty(exit_info)
                except Exception as exc:
                    self.trigger_hard_halt(
                        f"FAIL_CLOSED_ERROR: Failed to fetch exit order details: {exc}"
                    )
                    return "HALTED"

                if requested_qty <= 0 or order_qty != requested_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Exit order quantity does not match the verified position quantity."
                    )
                    return "HALTED"

                if filled_qty > requested_qty:
                    self.trigger_hard_halt(
                        "CRITICAL P0: Exit filled_quantity exceeds requested quantity."
                    )
                    return "HALTED"

                positions = self._observe("get_positions", self.broker.get_positions)
                if positions is OBSERVATION_RETRY:
                    return "HALTED" if self.terminator.halted else "NO_ACTION"
                active_pos = self._active_positions(positions)

                # Cumulative partial exit: the remaining broker quantity must equal
                # requested - cumulative filled. Never subtract the same fill twice.
                if 0 < filled_qty < requested_qty:
                    if len(active_pos) != 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Partial EXIT has ambiguous broker position state."
                        )
                        return "HALTED"
                    remaining = int(active_pos[0].get("quantity", 0) or 0)
                    expected_remaining = requested_qty - filled_qty
                    if remaining != expected_remaining:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Partial EXIT fill does not reconcile with broker position."
                        )
                        return "HALTED"
                    ctx.pending_qty = remaining
                    return "NO_ACTION"

                if ex_status == "COMPLETE":
                    if filled_qty != requested_qty:
                        self.trigger_hard_halt(
                            "CRITICAL P0: EXIT COMPLETE with incomplete cumulative fill."
                        )
                        return "HALTED"
                    if active_pos:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Exit COMPLETE but residual broker position exists."
                        )
                        return "HALTED"

                    self.audit.log(
                        "VERIFIED_EMERGENCY_EXIT_COMPLETE",
                        order_id=ctx.exit_order_id,
                        filled_qty=filled_qty,
                    )
                    self.state.active_trade = None
                    self.state.status = "STARTUP"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                if ex_status in {"REJECTED", "CANCELLED", "EXPIRED"}:
                    self.trigger_hard_halt(
                        f"CRITICAL P0: Emergency exit order failed with status {ex_status}."
                    )
                    return "HALTED"

                if ex_status in ACTIVE_ORDER_STATUSES:
                    return "NO_ACTION"

                self.trigger_hard_halt(
                    f"FAIL_CLOSED_ERROR: Unhandled exit status '{ex_status}'."
                )
                return "HALTED"

            if self.state.status == "FLAT":
                return "FLAT"

            self.trigger_hard_halt(
                f"FAIL_CLOSED_ERROR: Unsupported V3.4.0-B state '{self.state.status}'."
            )
            return "HALTED"

        except Exception as exc:
            self.trigger_hard_halt(f"Engine V3.4 exception: {exc}")
            return "HALTED"