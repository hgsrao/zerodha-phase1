from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any
from pathlib import Path
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
    market_protection_pct: Decimal = Decimal("-1")
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
    entry_price: Decimal = Decimal("0")
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

    # P0-1D: immutable durable emergency-submission intent.
    # Numeric values are stored as strings for deterministic JSON comparison.
    exit_submission_fingerprint: Optional[Dict[str, Any]] = None
    exit_reconciliation_failures: int = 0


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


class BrokerObservationContractViolation(RuntimeError):
    """Broker data needed for risk decisions was absent or malformed."""


@dataclass(frozen=True)
class BrokerRiskSnapshot:
    """Authoritative, fail-closed P0-3 observation used only for entry gates."""
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    charges: Decimal
    deployed_capital: Decimal
    pending_buy_exposure: Decimal = Decimal("0")
    executed_buy_quantity: int = 0
    executed_buy_value: Decimal = Decimal("0")
    observation_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid: bool = True
    kill_switch_active: bool = False
    capital_limit: Decimal = Decimal("20000")
    daily_loss_limit: Decimal = Decimal("2000")

    @property
    def daily_net_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl - self.charges

    @property
    def capital_ceiling_ok(self) -> bool:
        return self.deployed_capital + self.pending_buy_exposure <= self.capital_limit

    @property
    def daily_loss_limit_ok(self) -> bool:
        return self.daily_net_pnl > -self.daily_loss_limit

    @property
    def entry_allowed(self) -> bool:
        # A snapshot is an observation, not a permission token.  The entry
        # pathway must obtain a new one at the point of submission, so a saved
        # snapshot is never independently sufficient to authorize a BUY.
        return False

    @property
    def emergency_exit_allowed(self) -> bool:
        return True

    def is_fresh(self, max_age_seconds: Decimal | int | float) -> bool:
        age = datetime.now(timezone.utc) - self.observation_timestamp
        return age.total_seconds() < float(max_age_seconds)


@dataclass(frozen=True)
class EntryRiskDecision:
    allowed: bool
    reason: Optional[str]
    projected_deployed_capital: Decimal
    clearance_required: bool


class P03RiskController:
    """Durable, fail-closed gate for *new BUY* side effects.

    It intentionally contains no emergency-exit restriction.
    """
    def __init__(self, broker, store, kill_switch_file, capital_limit, daily_loss_limit):
        self.broker = broker
        self.store = store
        self.kill_switch_file = Path(kill_switch_file)
        self.capital_limit = Decimal(str(capital_limit))
        self.daily_loss_limit = Decimal(str(daily_loss_limit))
        try:
            self.durable_state = store.load()
        except FileNotFoundError:
            self.durable_state = {}
        if not isinstance(self.durable_state, dict):
            raise BrokerObservationContractViolation("P0-3 durable state must be a dictionary")

    @staticmethod
    def _decimal(value, field_name):
        if isinstance(value, bool):
            raise BrokerObservationContractViolation(f"{field_name} must be numeric")
        try:
            parsed = Decimal(str(value))
        except Exception as exc:
            raise BrokerObservationContractViolation(f"{field_name} must be numeric") from exc
        if not parsed.is_finite():
            raise BrokerObservationContractViolation(f"{field_name} must be finite")
        return parsed

    @staticmethod
    def _positive_integral(value, field_name):
        number = P03RiskController._decimal(value, field_name)
        if number != number.to_integral_value():
            raise BrokerObservationContractViolation(f"{field_name} must be integral")
        return int(number)

    def _persist_halt(self, status, reason, snapshot=None):
        self.durable_state.update({
            "status": status, "halt_reason": reason, "halt_source": "P0-3",
            "halted_at": datetime.now(timezone.utc).isoformat(), "clearance_required": True,
        })
        if snapshot is not None:
            self.durable_state["observed_daily_net_pnl"] = str(snapshot.daily_net_pnl)
            self.durable_state["deployed_capital"] = str(snapshot.deployed_capital)
        self.store.save(self.durable_state)

    def _risk_snapshot(self):
        raw = self.broker.get_daily_risk_snapshot()
        # P0-3B adapters already return the fully validated, authoritative
        # snapshot.  Reconstructing it from a second observation would both
        # widen the race window and impose a different schema on the adapter.
        if isinstance(raw, BrokerRiskSnapshot):
            if not raw.valid:
                raise BrokerObservationContractViolation("broker risk snapshot is invalid")
            return raw
        try:
            realized = self._decimal(raw.realized_pnl, "realized_pnl")
            # The original controller seam used ``unrealized_mtm``; the
            # broker-authoritative P0-3B snapshot exposes the equivalent
            # canonical ``unrealized_pnl`` field.
            unrealized_value = getattr(raw, "unrealized_mtm", None)
            if unrealized_value is None:
                unrealized_value = raw.unrealized_pnl
            unrealized = self._decimal(unrealized_value, "unrealized_mtm")
            charges = self._decimal(raw.charges, "charges")
            stated_capital = self._decimal(raw.deployed_capital, "deployed_capital")
        except AttributeError as exc:
            raise BrokerObservationContractViolation("daily risk snapshot is structurally invalid") from exc
        positions = self.broker.get_positions()
        orders = self.broker.get_orders()
        if not isinstance(positions, list) or not isinstance(orders, list):
            raise BrokerObservationContractViolation("positions and orders must be lists")
        position_capital = Decimal("0")
        for position in positions:
            if not isinstance(position, dict):
                raise BrokerObservationContractViolation("position must be a dictionary")
            quantity = self._positive_integral(position.get("quantity"), "position quantity")
            price = self._decimal(position.get("average_price"), "position average_price")
            position_capital += Decimal(max(quantity, 0)) * price
        pending = Decimal("0")
        for order in orders:
            if not isinstance(order, dict):
                raise BrokerObservationContractViolation("order must be a dictionary")
            if str(order.get("transaction_type", "")).upper() != "BUY":
                continue
            if str(order.get("status", "")).upper() not in ACTIVE_ORDER_STATUSES:
                continue
            quantity = self._positive_integral(order.get("quantity"), "order quantity")
            filled = self._positive_integral(order.get("filled_quantity", 0), "order filled_quantity")
            if filled > quantity:
                raise BrokerObservationContractViolation("filled quantity exceeds order quantity")
            price = self._decimal(order.get("price"), "order price")
            pending += Decimal(quantity - filled) * price
        # The broker-provided total may include fills that are not represented by a
        # position during a settlement transition; never understate either source.
        deployed = max(stated_capital, position_capital)
        return BrokerRiskSnapshot(realized, unrealized, charges, deployed, pending,
                                  capital_limit=self.capital_limit, daily_loss_limit=self.daily_loss_limit)

    def evaluate_entry(self, symbol, quantity, price):
        proposed = Decimal(self._positive_integral(quantity, "proposed quantity")) * self._decimal(price, "proposed price")
        if self.kill_switch_file.exists():
            self._persist_halt("TRADING_HALTED_KILL_SWITCH", "KILL_SWITCH")
            return EntryRiskDecision(False, "KILL_SWITCH", Decimal("0"), True)
        try:
            snapshot = self._risk_snapshot()
        except BrokerObservationContractViolation:
            self._persist_halt("RECONCILIATION_HALT", "BROKER_CONTRACT_VIOLATION")
            return EntryRiskDecision(False, "BROKER_CONTRACT_VIOLATION", Decimal("0"), True)
        projected = snapshot.deployed_capital + snapshot.pending_buy_exposure + proposed
        if self.durable_state.get("clearance_required"):
            return EntryRiskDecision(False, "DURABLE_HALT", projected, True)
        if snapshot.daily_net_pnl <= -self.daily_loss_limit:
            self._persist_halt("TRADING_HALTED_RISK", "DAILY_LOSS_LIMIT", snapshot)
            return EntryRiskDecision(False, "DAILY_LOSS_LIMIT", projected, True)
        if projected > self.capital_limit:
            return EntryRiskDecision(False, "CAPITAL_CEILING", projected, False)
        return EntryRiskDecision(True, None, projected, False)

    def submit_entry_guarded(self, symbol, quantity, price):
        decision = self.evaluate_entry(symbol, quantity, price)
        if not decision.allowed:
            raise RuntimeError(f"P0-3 entry blocked: {decision.reason}")
        return self.broker.submit_buy(symbol=symbol, quantity=quantity, price=price)

    def evaluate_emergency_exit(self, symbol, quantity, trigger_price):
        return EntryRiskDecision(True, None, Decimal("0"), bool(self.durable_state.get("clearance_required")))


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

    def request_entry(
        self,
        *,
        symbol: str,
        quantity: int,
        price: Decimal,
        entry_tag: str = "V3.4_ENTRY",
    ) -> str:
        """
        Create a durable entry intent.

        This method NEVER submits a broker order.
        It only establishes ENTRY_SUBMIT state.
        The subsequent step() performs controlled BUY submission
        through KiteBrokerAdapter and P0-3B-D authorization.
        """
        if self.terminator.halted:
            return "HALTED"

        if self.state.status != "FLAT":
            raise RuntimeError(
                f"ENTRY_REQUEST_REJECTED: engine state is "
                f"{self.state.status}, not FLAT."
            )

        if self.state.clearance_required:
            raise RuntimeError(
                "ENTRY_REQUEST_REJECTED: durable clearance is required."
            )

        symbol = str(symbol).strip().upper()
        if not symbol:
            raise RuntimeError(
                "ENTRY_REQUEST_REJECTED: symbol is empty."
            )

        try:
            quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "ENTRY_REQUEST_REJECTED: quantity must be an integer."
            ) from exc

        if quantity <= 0:
            raise RuntimeError(
                "ENTRY_REQUEST_REJECTED: quantity must be positive."
            )

        price = Decimal(str(price))
        if not price.is_finite() or price <= 0:
            raise RuntimeError(
                "ENTRY_REQUEST_REJECTED: price must be positive and finite."
            )

        if entry_tag != "V3.4_ENTRY":
            raise RuntimeError(
                "ENTRY_REQUEST_REJECTED: invalid entry tag."
            )

        self.state.active_trade = TradeContext(
            symbol=symbol,
            entry_tag=entry_tag,
            target_qty=quantity,
            tranche_qty=quantity,
            filled_qty=0,
            pending_qty=quantity,
            entry_order_id=None,
            entry_price=price,
            order_status="PENDING",
        )

        self.state.status = "ENTRY_SUBMIT"
        self.store.save(self.state)

        self.audit.log(
            "ENTRY_INTENT_CREATED",
            symbol=symbol,
            quantity=quantity,
            price=str(price),
            tag=entry_tag,
        )

        return "STATE_CHANGED"
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

    @staticmethod
    def _canonical_decimal_string(value: Any) -> str:
        try:
            d = Decimal(str(value))
        except Exception as exc:
            raise RuntimeError(
                f"FAIL_CLOSED: Cannot canonicalize decimal value: {value!r}"
            ) from exc
        if not d.is_finite():
            raise RuntimeError("FAIL_CLOSED: Non-finite decimal value.")
        # Fixed-point string removes scientific notation and preserves exact
        # decimal intent for durable reconciliation.
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s in {"", "-0"}:
            s = "0"
        return s

    def _build_exit_submission_fingerprint(
        self,
        *,
        symbol: str,
        quantity: int,
        trigger_price: Decimal,
        market_protection: Decimal,
    ) -> Dict[str, Any]:
        return {
            "exchange": "NSE",
            "tradingsymbol": str(symbol),
            "transaction_type": "SELL",
            "product": "MIS",
            "order_type": "SL-M",
            "quantity": int(quantity),
            "trigger_price": self._canonical_decimal_string(trigger_price),
            "market_protection": self._canonical_decimal_string(market_protection),
            "tag": "V3.4_EXIT",
        }

    @staticmethod
    def _order_matches_exit_fingerprint(
        order: Dict[str, Any],
        fingerprint: Dict[str, Any],
    ) -> bool:
        if not isinstance(order, dict) or not isinstance(fingerprint, dict):
            return False

        if order.get("exchange", EXCHANGE) != fingerprint["exchange"]:
            return False
        if order.get("tradingsymbol") != fingerprint["tradingsymbol"]:
            return False
        if order.get("transaction_type") != fingerprint["transaction_type"]:
            return False
        if order.get("product", "MIS") != fingerprint["product"]:
            return False
        if order.get("order_type") != fingerprint["order_type"]:
            return False
        if order.get("tag") != fingerprint["tag"]:
            return False

        try:
            if int(order.get("quantity", 0) or 0) != int(fingerprint["quantity"]):
                return False
        except (TypeError, ValueError):
            return False

        try:
            if TradingEngineV34._canonical_decimal_string(
                order.get("trigger_price")
            ) != fingerprint["trigger_price"]:
                return False
            # Zerodha order payloads may omit market_protection on some
            # observations. Omission is not an exact match.
            if "market_protection" not in order:
                return False
            if TradingEngineV34._canonical_decimal_string(
                order.get("market_protection")
            ) != fingerprint["market_protection"]:
                return False
        except Exception:
            return False

        order_id = order.get("order_id")
        return isinstance(order_id, str) and bool(order_id.strip())

    def _reconcile_unknown_exit(self) -> str:
        ctx = self.state.active_trade
        if not ctx or not ctx.exit_submission_fingerprint:
            self.trigger_hard_halt(
                "P0-1D: EXIT_UNKNOWN/EXIT_SUBMITTING lacks immutable submission fingerprint."
            )
            return "HALTED"

        try:
            get_orders = getattr(self.broker, "get_orders", None)
            if get_orders is None:
                get_orders = getattr(self.broker, "orders", None)
            if get_orders is None:
                raise RuntimeError(
                    "FAIL_CLOSED: Broker adapter has no orders observation method."
                )

            orders = self._observe(
                "get_orders:emergency_exit_reconciliation",
                get_orders,
            )
            if orders is OBSERVATION_RETRY:
                return "HALTED" if self.terminator.halted else "NO_ACTION"

            if not isinstance(orders, list):
                raise RuntimeError(
                    "FAIL_CLOSED: Broker orders response is not a list."
                )

            required_order_fields = {
                "order_id", "exchange", "tradingsymbol", "transaction_type",
                "product", "order_type", "quantity", "trigger_price",
                "market_protection", "tag", "status",
            }
            for order in orders:
                if not isinstance(order, dict) or required_order_fields.difference(order):
                    raise RuntimeError(
                        "FAIL_CLOSED: Broker order is missing required reconciliation fields."
                    )

            matches = [
                o for o in orders
                if self._order_matches_exit_fingerprint(
                    o, ctx.exit_submission_fingerprint
                )
            ]

            if len(matches) > 1:
                self.trigger_hard_halt(
                    "P0-1D: Multiple exact V3.4 emergency-exit orders match the immutable fingerprint.",
                    source="EXIT_RECONCILIATION",
                )
                return "HALTED"

            if len(matches) == 1:
                recovered_id = matches[0]["order_id"]
                ctx.exit_order_id = str(recovered_id)
                ctx.exit_reconciliation_failures = 0
                self.state.status = "EXIT_PENDING"
                self.store.save(self.state)
                self.audit.log(
                    "EMERGENCY_EXIT_ORDER_RECOVERED",
                    order_id=ctx.exit_order_id,
                    source_state="EXIT_UNKNOWN",
                )
                return "STATE_CHANGED"

            # Zero exact matches is an observation failure, not permission to
            # submit another side effect. The counter is durable across restart.
            ctx.exit_reconciliation_failures = int(
                getattr(ctx, "exit_reconciliation_failures", 0) or 0
            ) + 1
            budget = int(self.cfg.observation_retry_budget)

            if self.state.status == "EXIT_SUBMITTING":
                # Durable side-effect boundary was crossed, but no broker order
                # is observable. Preserve ambiguity; never resubmit.
                self.state.status = "EXIT_UNKNOWN"

            self.store.save(self.state)

            if ctx.exit_reconciliation_failures > budget:
                self.trigger_hard_halt(
                    "P0-1D: Emergency-exit reconciliation observation budget exhausted "
                    "without an exact broker-order match.",
                    source="EXIT_RECONCILIATION",
                )
                return "HALTED"

            return "NO_ACTION"

        except Exception as exc:
            # Structural broker-contract failures are never retryable ambiguity.
            # Retrying them could defer a required liquidation indefinitely.
            if "FAIL_CLOSED" in str(exc):
                self.trigger_hard_halt(
                    f"P0-1D: Malformed emergency-exit reconciliation observation: {exc}",
                    source="EXIT_RECONCILIATION",
                )
                return "HALTED"
            ctx.exit_reconciliation_failures = int(
                getattr(ctx, "exit_reconciliation_failures", 0) or 0
            ) + 1
            try:
                self.store.save(self.state)
            except Exception:
                pass

            if ctx.exit_reconciliation_failures > int(self.cfg.observation_retry_budget):
                self.trigger_hard_halt(
                    f"P0-1D: Emergency-exit reconciliation failed repeatedly: {exc}",
                    source="EXIT_RECONCILIATION",
                )
                return "HALTED"

            return "NO_ACTION"

    def _is_transient_submission_exception(self, exc: Exception) -> bool:
        """Treat transport ambiguity as UNKNOWN, including adapter-wrapped causes."""
        current: Optional[BaseException] = exc
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, Exception) and self._is_transient_observation_exception(current):
                return True
            current = getattr(current, "__cause__", None)
        return False

    def reconcile_startup(self) -> None:
        """Reconcile local state against broker reality without blind fallthrough."""
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
            if self.state.status in {"ENTRY_PENDING", "PARTIAL_POSITION"} and not local_trade.stop_order_id:
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

            # P0-1D: a persisted submission boundary is never allowed to
            # fall through into a fresh side-effect. Reconcile broker orders first.
            if self.state.status in {"EXIT_SUBMITTING", "EXIT_UNKNOWN"}:
                return_value = self._reconcile_unknown_exit()
                if return_value in {"HALTED", "STATE_CHANGED", "NO_ACTION"}:
                    return

            # Any managed/protection/exit state must have exactly one matching position
            # unless it is explicitly in a terminal exit-reconciliation state.
            matching_pos = self._matching_positions(
                active_pos,
                symbol=local_trade.symbol,
                product="MIS",
            )

            if self.state.status in {"PROTECTION", "PROTECTION_PENDING", "MANAGING", "EXIT_CANCEL_SL", "EXIT_SUBMIT", "EXIT_SUBMITTING", "EXIT_UNKNOWN"}:
                if len(active_pos) != 1 or len(matching_pos) != 1:
                    self.trigger_hard_halt(
                        "Reconciliation Failure: Managed state does not map to exactly one broker position."
                    )
                    return

                # Never recover directly into MANAGING without proving an active SL.
                if self.state.status in {"PROTECTION", "PROTECTION_PENDING", "MANAGING"}:
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
                    local_trade.stop_order_id = sl_orders[0].get("order_id")
                    self.state.status = "MANAGING"
                    self.store.save(self.state)
                    return

            if self.state.status == "EXIT_PENDING":
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

            elif self.state.status == "ENTRY_SUBMIT":
                    # P0-3B-D / P0-1D:
                    # ENTRY_SUBMIT is the sole controlled BUY side-effect boundary.
                    #
                    # The durable intent was created by request_entry(). At this point
                    # we must validate that intent, reconcile broker reality, and only
                    # then cross the broker side-effect boundary.
                    ctx = self.state.active_trade

                    if not ctx:
                        self.trigger_hard_halt(
                            "FAIL_CLOSED_ERROR: ENTRY_SUBMIT without active trade."
                        )
                        return "HALTED"

                    if ctx.entry_order_id:
                        self.trigger_hard_halt(
                            "CRITICAL P0: ENTRY_SUBMIT already contains an entry_order_id."
                        )
                        return "HALTED"

                    if ctx.entry_tag != "V3.4_ENTRY":
                        self.trigger_hard_halt(
                            "CRITICAL P0: ENTRY_SUBMIT has invalid entry tag."
                        )
                        return "HALTED"

                    if ctx.target_qty <= 0:
                        self.trigger_hard_halt(
                            "CRITICAL P0: ENTRY_SUBMIT target quantity must be positive."
                        )
                        return "HALTED"

                    if ctx.entry_price <= 0 or not ctx.entry_price.is_finite():
                        self.trigger_hard_halt(
                            "CRITICAL P0: ENTRY_SUBMIT entry price must be positive and finite."
                        )
                        return "HALTED"

                    # Reconcile broker positions before creating a new BUY.
                    positions = self._observe(
                        "get_positions:entry_submit",
                        self.broker.get_positions,
                    )
                    if positions is OBSERVATION_RETRY:
                        return "HALTED" if self.terminator.halted else "NO_ACTION"

                    active_pos = self._active_positions(positions)

                    if active_pos:
                        self.trigger_hard_halt(
                            "CRITICAL P0: ENTRY_SUBMIT found an existing active broker position."
                        )
                        return "HALTED"

                    # Reconcile existing active orders before crossing the side-effect
                    # boundary. An exact matching intent may be safely adopted; an
                    # ambiguous same-symbol BUY must fail closed.
                    open_orders = self._observe(
                        "get_orders:entry_submit",
                        self.broker.get_orders,
                    )
                    if open_orders is OBSERVATION_RETRY:
                        return "HALTED" if self.terminator.halted else "NO_ACTION"

                    if not isinstance(open_orders, list):
                        self.trigger_hard_halt(
                            "FAIL_CLOSED_ERROR: Broker orders response is not a list."
                        )
                        return "HALTED"

                    matching_orders = []
                    conflicting_orders = []

                    for order in open_orders:
                        if not isinstance(order, dict):
                            self.trigger_hard_halt(
                                "FAIL_CLOSED_ERROR: Broker order entry is not an object."
                            )
                            return "HALTED"

                        if order.get("status") not in ACTIVE_ORDER_STATUSES:
                            continue

                        if str(order.get("transaction_type", "")).upper() != "BUY":
                            continue

                        if str(order.get("tradingsymbol", "")).upper() != ctx.symbol.upper():
                            continue

                        order_tag = str(order.get("tag", "") or "")
                        try:
                            order_qty = int(order.get("quantity", 0) or 0)
                        except (TypeError, ValueError):
                            self.trigger_hard_halt(
                                "CRITICAL P0: Existing BUY order has malformed quantity."
                            )
                            return "HALTED"

                        try:
                            order_price = Decimal(
                                str(order.get("price", 0) or 0)
                            )
                        except Exception:
                            self.trigger_hard_halt(
                                "CRITICAL P0: Existing BUY order has malformed price."
                            )
                            return "HALTED"

                        if (
                            order_tag == ctx.entry_tag
                            and order_qty == ctx.target_qty
                            and order_price == ctx.entry_price
                            and str(order.get("exchange", "NSE")) == "NSE"
                            and str(order.get("product", "MIS")) == "MIS"
                        ):
                            matching_orders.append(order)
                        else:
                            conflicting_orders.append(order)

                    if len(matching_orders) > 1:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Multiple active broker BUY orders match the durable entry intent."
                        )
                        return "HALTED"

                    if conflicting_orders:
                        self.trigger_hard_halt(
                            "CRITICAL P0: Active same-symbol BUY order conflicts with durable entry intent."
                        )
                        return "HALTED"

                    # If the broker already has the exact durable intent, adopt it rather
                    # than submitting a duplicate order.
                    if len(matching_orders) == 1:
                        existing_id = matching_orders[0].get("order_id")

                        if not isinstance(existing_id, str) or not existing_id.strip():
                            self.trigger_hard_halt(
                                "CRITICAL P0: Matching broker entry order has no valid order ID."
                            )
                            return "HALTED"

                        ctx.entry_order_id = existing_id
                        ctx.order_status = str(
                            matching_orders[0].get("status", "OPEN")
                        )
                        ctx.pending_qty = ctx.target_qty
                        self.state.status = "ENTRY_PENDING"
                        self.store.save(self.state)

                        self.audit.log(
                            "ENTRY_INTENT_RECONCILED_TO_EXISTING_ORDER",
                            order_id=existing_id,
                            symbol=ctx.symbol,
                            quantity=ctx.target_qty,
                        )
                        return "STATE_CHANGED"

                    # No existing order matches the durable intent. This is the only
                    # point where a new BUY may cross the broker side-effect boundary.
                    #
                    # IMPORTANT: an exception here is deliberately NOT retried. The
                    # broker may have accepted the order while the response was lost.
                    try:
                        order_id = self.broker.place_order(
                            exchange="NSE",
                            tradingsymbol=ctx.symbol,
                            transaction_type="BUY",
                            quantity=ctx.target_qty,
                            product="MIS",
                            order_type="LIMIT",
                            price=float(ctx.entry_price),
                            tag=ctx.entry_tag,
                        )
                    except Exception as exc:
                        self.trigger_hard_halt(
                            f"CRITICAL P0: Entry submission result is unknown or rejected: {exc}"
                        )
                        return "HALTED"

                    if not isinstance(order_id, str) or not order_id.strip():
                        self.trigger_hard_halt(
                            "CRITICAL P0: Entry broker response did not contain a valid order ID."
                        )
                        return "HALTED"

                    # Persist the broker identity BEFORE entering ENTRY_PENDING.
                    # This prevents a restart from losing the submitted order ID.
                    ctx.entry_order_id = order_id
                    ctx.order_status = "SUBMITTED"
                    ctx.pending_qty = ctx.target_qty
                    self.state.status = "ENTRY_PENDING"
                    self.store.save(self.state)

                    self.audit.log(
                        "ENTRY_ORDER_SUBMITTED",
                        order_id=order_id,
                        symbol=ctx.symbol,
                        quantity=ctx.target_qty,
                        price=str(ctx.entry_price),
                    )

                    return "STATE_CHANGED"
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

            elif self.state.status in {"EXIT_SUBMITTING", "EXIT_UNKNOWN"}:
                # P0-1D: UNKNOWN is observation-only. It can never submit another
                # emergency order. EXIT_SUBMITTING is reconciled first after restart.
                if self.state.status == "EXIT_UNKNOWN":
                    return self._reconcile_unknown_exit()

                ctx = self.state.active_trade
                if not ctx:
                    self.trigger_hard_halt(
                        "FAIL_CLOSED_ERROR: EXIT_SUBMITTING without active trade."
                    )
                    return "HALTED"

                if not ctx.exit_submission_fingerprint:
                    self.trigger_hard_halt(
                        "P0-1D: EXIT_SUBMITTING without immutable submission fingerprint."
                    )
                    return "HALTED"

                # Once EXIT_SUBMITTING is durable, no automatic second submission
                # is permitted. Reconcile broker reality before doing anything else.
                return self._reconcile_unknown_exit()

            elif self.state.status == "EXIT_UNKNOWN":
                # UNKNOWN is observation-only. Never submit another side effect.
                return self._reconcile_unknown_exit()

            elif self.state.status == "EXIT_SUBMITTING":
                # The side-effect boundary may already have been crossed. Reconcile
                # broker reality before permitting any other action.
                return self._reconcile_unknown_exit()

            elif self.state.status == "EXIT_SUBMIT":
                ctx = self.state.active_trade
                if not ctx:
                    self.trigger_hard_halt("FAIL_CLOSED_ERROR: EXIT_SUBMIT without active trade.")
                    return "HALTED"

                positions = self.broker.get_positions()
                active_pos = self._active_positions(positions)
                matching_pos = self._matching_positions(
                    active_pos, symbol=ctx.symbol, product="MIS"
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

                try:
                    pos_qty = int(matching_pos[0].get("quantity", 0) or 0)
                except (TypeError, ValueError):
                    self.trigger_hard_halt("CRITICAL P0: Exit position quantity is malformed.")
                    return "HALTED"

                if pos_qty <= 0:
                    self.trigger_hard_halt("CRITICAL P0: Exit position quantity is non-positive.")
                    return "HALTED"

                ctx.filled_qty = pos_qty

                try:
                    quotes = self._observe(
                        "ltp:emergency_exit",
                        lambda: self.broker.ltp([ctx.symbol]),
                    )
                    if quotes is OBSERVATION_RETRY:
                        return "HALTED" if self.terminator.halted else "NO_ACTION"

                    quote = quotes.get(f"NSE:{ctx.symbol}") or quotes.get(ctx.symbol)
                    if not isinstance(quote, dict) or "last_price" not in quote:
                        self.trigger_hard_halt("CRITICAL P0: Emergency exit LTP is missing or malformed.")
                        return "HALTED"

                    ltp = Decimal(str(quote.get("last_price", 0) or 0))
                    if ltp <= 0:
                        self.trigger_hard_halt("CRITICAL P0: Emergency exit LTP is non-positive.")
                        return "HALTED"

                    tick_size = self.broker.get_tick_size(ctx.symbol)
                    if not isinstance(tick_size, Decimal) or tick_size <= 0:
                        self.trigger_hard_halt("CRITICAL P0: Emergency exit tick size is invalid.")
                        return "HALTED"

                    from decimal import ROUND_DOWN
                    raw_trigger = ltp - tick_size
                    trigger = (
                        (raw_trigger / tick_size).to_integral_value(rounding=ROUND_DOWN)
                        * tick_size
                    )
                    if trigger <= 0 or trigger >= ltp:
                        self.trigger_hard_halt("CRITICAL P0: Emergency exit trigger cannot be established safely.")
                        return "HALTED"

                    fingerprint = self._build_exit_submission_fingerprint(
                        symbol=ctx.symbol,
                        quantity=pos_qty,
                        trigger_price=trigger,
                        market_protection=self.cfg.market_protection_pct,
                    )

                    if ctx.exit_submission_fingerprint is not None:
                        if ctx.exit_submission_fingerprint != fingerprint:
                            self.trigger_hard_halt(
                                "P0-1D: Existing emergency-exit fingerprint is immutable and conflicts with newly observed intent.",
                                source="EXIT_RECONCILIATION",
                            )
                            return "HALTED"
                    else:
                        ctx.exit_submission_fingerprint = fingerprint

                    ctx.exit_reconciliation_failures = 0
                    self.state.status = "EXIT_SUBMITTING"

                    # STORE-FIRST: if this fails, broker submission is never reached.
                    self.store.save(self.state)

                    try:
                        exit_oid = self.broker.submit_emergency_exit(
                            symbol=ctx.symbol,
                            quantity=pos_qty,
                            trigger_price=trigger,
                            source_ltp=ltp,
                            tick_size=tick_size,
                            market_protection=self.cfg.market_protection_pct,
                            tag="V3.4_EXIT",
                        )
                    except Exception as exc:
                        if self._is_transient_submission_exception(exc):
                            self.state.status = "EXIT_UNKNOWN"
                            self.store.save(self.state)
                            return "STATE_CHANGED"

                        self.trigger_hard_halt(
                            f"CRITICAL P0: Emergency exit submission rejected/failed with known outcome: {exc}",
                            source="EXIT_SUBMIT",
                        )
                        return "HALTED"

                    if not isinstance(exit_oid, str) or not exit_oid.strip():
                        self.trigger_hard_halt(
                            "CRITICAL P0: Emergency exit submission returned no valid order ID.",
                            source="EXIT_SUBMIT",
                        )
                        return "HALTED"

                    ctx.exit_order_id = exit_oid
                    self.state.status = "EXIT_PENDING"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                except Exception as exc:
                    self.trigger_hard_halt(
                        f"CRITICAL P0: Failed during emergency-exit preparation/submission: {exc}",
                        source="EXIT_SUBMIT",
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


# Deliberately exposed as a factory rather than changing the established engine
# constructor; existing P0/P0-2 lifecycle wiring remains untouched.
def _create_p03_risk_controller(*, broker, store, kill_switch_file, capital_limit, daily_loss_limit):
    return P03RiskController(broker, store, kill_switch_file, capital_limit, daily_loss_limit)


TradingEngineV34.create_p03_risk_controller = staticmethod(_create_p03_risk_controller)




