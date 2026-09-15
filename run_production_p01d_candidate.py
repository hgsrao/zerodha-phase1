import os
import sys
import json
import time
import logging
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from dataclasses import asdict, is_dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import (
    List,
    Dict,
    Any,
    Optional,
    get_type_hints,
    get_origin,
    get_args,
    Union,
)

import requests
from kiteconnect import KiteConnect

from broker_exposure_scope import classify_positions

from institutional_engine_v34_p01d_candidate import (
    TradingEngineV34,
    Config,
    BotState,
    TradeContext,
    BrokerRiskSnapshot,
    BrokerObservationContractViolation,
    P03RiskController,
    NSE_HOLIDAYS_2026,
)


# ============================================================
# GLOBAL SAFETY CONFIGURATION
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

# HARD SAFETY BARRIER:
# Keep False for observation/testing.
# Do NOT change this to True until a separate controlled
# production authorization step has been completed.
LIVE_TRADING_ENABLED = False

STATE_FILE = "bot_state_v34.json"
LOCK_FILE = "bot_state_v34.lock"
SESSION_ID = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
SESSION_LOG_DIR = os.path.join("session_logs", SESSION_ID)
os.makedirs(SESSION_LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(SESSION_LOG_DIR, "bot_production.log")
P03_ENTRY_STATE_FILE = "bot_state_v34_p03_entry_control.json"
P03_KILL_SWITCH_FILE = "KILL_SWITCH"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)


# ============================================================
# TYPE / SERIALIZATION HELPERS
# ============================================================

def _is_decimal_type(tp: Any) -> bool:
    """
    Returns True if a type annotation represents Decimal,
    including Optional[Decimal] / Union[Decimal, None].
    """
    if tp is Decimal:
        return True

    origin = get_origin(tp)

    if origin is Union:
        return any(_is_decimal_type(arg) for arg in get_args(tp))

    return False


def _is_dataclass_type(tp: Any) -> bool:
    """
    Safely determine whether a typing annotation represents
    a dataclass type.
    """
    try:
        return isinstance(tp, type) and is_dataclass(tp)
    except Exception:
        return False


def _restore_value(value: Any, expected_type: Any) -> Any:
    """
    Recursively restore JSON-decoded values according to
    dataclass/type annotations.

    Important:
    - Decimal values are restored exactly from their string
      representation.
    - Nested dataclasses are reconstructed.
    - Optional/Union types are handled.
    - Lists and dictionaries are recursively restored.
    """

    if value is None:
        return None

    # --------------------------------------------------------
    # Decimal / Optional[Decimal]
    # --------------------------------------------------------

    if _is_decimal_type(expected_type):
        try:
            return Decimal(str(value))
        except Exception as exc:
            raise RuntimeError(
                f"FAIL_CLOSED: Cannot restore Decimal value "
                f"{value!r}: {exc}"
            ) from exc

    origin = get_origin(expected_type)
    args = get_args(expected_type)

    # --------------------------------------------------------
    # Optional / Union
    # --------------------------------------------------------

    if origin is Union:
        non_none_args = [arg for arg in args if arg is not type(None)]

        for candidate in non_none_args:
            try:
                return _restore_value(value, candidate)
            except (TypeError, ValueError, RuntimeError):
                continue

        raise RuntimeError(
            f"FAIL_CLOSED: Unable to restore value {value!r} "
            f"against Union type {expected_type}"
        )

    # --------------------------------------------------------
    # Nested dataclass
    # --------------------------------------------------------

    if _is_dataclass_type(expected_type):
        if not isinstance(value, dict):
            raise RuntimeError(
                f"FAIL_CLOSED: Expected object for "
                f"{expected_type.__name__}, got {type(value).__name__}"
            )

        return _restore_dataclass(value, expected_type)

    # --------------------------------------------------------
    # List / list[T]
    # --------------------------------------------------------

    if origin in (list, List):
        if not isinstance(value, list):
            raise RuntimeError(
                f"FAIL_CLOSED: Expected list, got "
                f"{type(value).__name__}"
            )

        item_type = args[0] if args else Any

        return [
            _restore_value(item, item_type)
            for item in value
        ]

    # --------------------------------------------------------
    # Dict / dict[K, V]
    # --------------------------------------------------------

    if origin in (dict, Dict):
        if not isinstance(value, dict):
            raise RuntimeError(
                f"FAIL_CLOSED: Expected dictionary, got "
                f"{type(value).__name__}"
            )

        key_type = args[0] if len(args) >= 1 else Any
        val_type = args[1] if len(args) >= 2 else Any

        return {
            _restore_value(k, key_type):
            _restore_value(v, val_type)
            for k, v in value.items()
        }

    # --------------------------------------------------------
    # Any / unknown annotation
    # --------------------------------------------------------

    if expected_type is Any:
        return value

    # --------------------------------------------------------
    # Primitive validation / conversion
    # --------------------------------------------------------

    if expected_type in (str, int, float, bool):
        try:
            return expected_type(value)
        except Exception as exc:
            raise RuntimeError(
                f"FAIL_CLOSED: Cannot restore {value!r} "
                f"as {expected_type}: {exc}"
            ) from exc

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    return value


def _restore_dataclass(data: Dict[str, Any], cls: type) -> Any:
    """
    Reconstruct a dataclass from JSON using resolved type hints.

    Fail-closed behavior:
    - Unknown JSON fields are rejected.
    - Missing required fields are rejected.
    - Type reconstruction failures are fatal.
    """

    if not isinstance(data, dict):
        raise RuntimeError(
            f"FAIL_CLOSED: Expected dictionary for {cls.__name__}"
        )

    try:
        hints = get_type_hints(cls)
    except Exception as exc:
        raise RuntimeError(
            f"FAIL_CLOSED: Unable to resolve type hints for "
            f"{cls.__name__}: {exc}"
        ) from exc

    # --------------------------------------------------------
    # Reject unknown fields.
    #
    # This is intentional. A state schema mismatch should halt
    # rather than silently discard information.
    # --------------------------------------------------------

    unknown_fields = set(data.keys()) - set(hints.keys())

    if unknown_fields:
        raise RuntimeError(
            f"FAIL_CLOSED: Unknown fields in {cls.__name__}: "
            f"{sorted(unknown_fields)}"
        )

    restored = {}

    for field_name, field_type in hints.items():

        if field_name not in data:
            # Let the dataclass default/default_factory handle
            # fields that are optional in the schema.
            continue

        restored[field_name] = _restore_value(
            data[field_name],
            field_type,
        )

    try:
        return cls(**restored)
    except Exception as exc:
        raise RuntimeError(
            f"FAIL_CLOSED: Unable to reconstruct "
            f"{cls.__name__}: {exc}"
        ) from exc


# ============================================================
# KITE BROKER ADAPTER
# ============================================================

class KiteBrokerAdapter:
    """
    Adapter wrapping KiteConnect.

    The adapter is deliberately strict:
    malformed broker responses become hard failures rather
    than being converted into plausible-looking empty data.
    """

    def __init__(
        self,
        kite: KiteConnect,
        live_trading: bool = False,
        expected_account_id: Optional[str] = None,
    ):
        self.kite = kite
        self.live_trading = live_trading
        self.expected_account_id = expected_account_id
        self.entry_authorizer = None

    def set_entry_authorizer(self, authorizer) -> None:
        """Install the sole authorization path for runner BUY submissions."""
        self.entry_authorizer = authorizer

    # --------------------------------------------------------
    # Orders
    # --------------------------------------------------------

    def get_orders(self) -> List[Dict[str, Any]]:
        try:
            orders = self.kite.orders()

            if not isinstance(orders, list):
                raise RuntimeError(
                    "FAIL_CLOSED: Malformed orders response "
                    "(expected list)."
                )

            for order in orders:
                if not isinstance(order, dict):
                    raise RuntimeError(
                        "FAIL_CLOSED: Malformed order record "
                        "(expected dictionary)."
                    )

            return orders

        except Exception as exc:
            if "FAIL_CLOSED" in str(exc):
                raise

            raise RuntimeError(
                f"FAIL_CLOSED: Failed to fetch orders: {exc}"
            ) from exc

    # --------------------------------------------------------
    # Order history
    # --------------------------------------------------------

    def get_order_details(
        self,
        order_id: str,
    ) -> Dict[str, Any]:

        if not order_id:
            raise RuntimeError(
                "FAIL_CLOSED: Empty order ID supplied."
            )

        try:
            history = self.kite.order_history(order_id)

            if not isinstance(history, list):
                raise RuntimeError(
                    f"FAIL_CLOSED: Malformed order history for "
                    f"{order_id}; expected list."
                )

            if not history:
                raise RuntimeError(
                    f"FAIL_CLOSED: Empty order history returned "
                    f"for {order_id}."
                )

            latest = history[-1]

            if not isinstance(latest, dict):
                raise RuntimeError(
                    f"FAIL_CLOSED: Malformed latest order history "
                    f"record for {order_id}."
                )

            return latest

        except Exception as exc:
            if "FAIL_CLOSED" in str(exc):
                raise

            raise RuntimeError(
                f"FAIL_CLOSED: Failed to fetch order details "
                f"for {order_id}: {exc}"
            ) from exc

    # --------------------------------------------------------
    # Positions
    # --------------------------------------------------------

    def get_positions(self) -> List[Dict[str, Any]]:
        try:
            response = self.kite.positions()

            if not isinstance(response, dict):
                raise RuntimeError(
                    "FAIL_CLOSED: Malformed positions response "
                    "(expected dictionary)."
                )

            net_positions = response.get("net")

            if not isinstance(net_positions, list):
                raise RuntimeError(
                    "FAIL_CLOSED: Broker positions 'net' field "
                    "is missing or is not a list."
                )

            for position in net_positions:
                if not isinstance(position, dict):
                    raise RuntimeError(
                        "FAIL_CLOSED: Malformed position record."
                    )

            return net_positions

        except Exception as exc:
            if "FAIL_CLOSED" in str(exc):
                raise

            raise RuntimeError(
                f"FAIL_CLOSED: Failed to fetch positions: {exc}"
            ) from exc

    def get_daily_risk_snapshot(self) -> BrokerRiskSnapshot:
        """Return the P0-3 broker-authoritative V3.4/MIS risk observation.

        Any missing or structurally invalid broker datum is a contract violation,
        rather than a reason to guess at capital, P&L, or charges.
        """
        def decimal_value(value, name):
            if isinstance(value, bool):
                raise BrokerObservationContractViolation(f"{name} must be numeric")
            try:
                parsed = Decimal(str(value))
            except Exception as exc:
                raise BrokerObservationContractViolation(f"{name} must be numeric") from exc
            if not parsed.is_finite():
                raise BrokerObservationContractViolation(f"{name} must be finite")
            return parsed

        expected_account_id = getattr(self, "expected_account_id", None)
        if expected_account_id is not None:
            try:
                profile = self.kite.profile()
            except Exception as exc:
                raise BrokerObservationContractViolation("broker account identity unavailable") from exc
            if not isinstance(profile, dict) or str(profile.get("user_id") or "") != str(expected_account_id):
                raise BrokerObservationContractViolation("broker account identity mismatch")

        try:
            positions = self.kite.positions()
            orders = self.kite.orders()
        except Exception as exc:
            raise BrokerObservationContractViolation("broker risk observation failed") from exc
        if not isinstance(positions, dict) or not isinstance(positions.get("net"), list) or not isinstance(positions.get("day"), list):
            raise BrokerObservationContractViolation("positions must contain net and day lists")
        if not isinstance(orders, list) or not all(isinstance(order, dict) for order in orders):
            raise BrokerObservationContractViolation("orders must be a list of dictionaries")

        def is_bot_order(order):
            return order.get("product") == "MIS" and str(order.get("exchange", "")).upper() == "NSE" and str(order.get("tag") or "").startswith("V3.4_")

        bot_orders = [order for order in orders if is_bot_order(order)]
        seen_order_ids = set()
        executed_qty = 0
        executed_value = Decimal("0")
        pending_buy = Decimal("0")
        charges = Decimal("0")
        bot_symbols = set()
        for order in bot_orders:
            order_id = order.get("order_id")
            if not isinstance(order_id, str) or not order_id or order_id in seen_order_ids:
                raise BrokerObservationContractViolation("bot order_id is missing or duplicated")
            seen_order_ids.add(order_id)
            bot_symbols.add(order.get("tradingsymbol"))
            quantity = decimal_value(order.get("quantity"), "order quantity")
            filled = decimal_value(order.get("filled_quantity"), "order filled_quantity")
            price = decimal_value(order.get("average_price"), "order average_price")
            if quantity < 0 or filled < 0 or filled > quantity or quantity != quantity.to_integral_value() or filled != filled.to_integral_value():
                raise BrokerObservationContractViolation("order quantities are invalid")
            if str(order.get("transaction_type", "")).upper() == "BUY":
                executed_qty += int(filled)
                executed_value += filled * price
                if str(order.get("status", "")).upper() in {"OPEN", "TRIGGER PENDING", "VALIDATION PENDING", "AMO REQ RECEIVED", "MODIFY PENDING", "CANCEL PENDING"}:
                    # A live BUY remains authorized exposure until reconciliation
                    # proves it terminal, even where the broker reports a fill.
                    pending_buy += quantity * price
            try:
                charges += decimal_value(self.kite.order_charges(order_id), "order charges")
            except Exception as exc:
                if isinstance(exc, BrokerObservationContractViolation):
                    raise
                raise BrokerObservationContractViolation("order charges unavailable") from exc

        deployed = Decimal("0")
        realised = Decimal("0")
        unrealised = Decimal("0")
        for position in positions["net"]:
            if not isinstance(position, dict):
                raise BrokerObservationContractViolation("position must be a dictionary")
            symbol = position.get("tradingsymbol")
            if symbol not in bot_symbols or position.get("product") != "MIS" or str(position.get("exchange", "")).upper() != "NSE":
                continue
            quantity = decimal_value(position.get("quantity"), "position quantity")
            price = decimal_value(position.get("average_price"), "position average_price")
            if quantity != quantity.to_integral_value():
                raise BrokerObservationContractViolation("position quantity must be integral")
            deployed += max(quantity, Decimal("0")) * price
            realised += decimal_value(position.get("realised"), "position realised")
            unrealised += decimal_value(position.get("unrealised"), "position unrealised")

        cfg = getattr(self, "config", None)
        capital_limit = getattr(cfg, "trial_capital", Decimal("20000"))
        daily_loss_limit = getattr(cfg, "max_daily_loss", Decimal("2000"))
        return BrokerRiskSnapshot(realised, unrealised, charges, deployed, pending_buy,
                                  executed_qty, executed_value,
                                  capital_limit=Decimal(str(capital_limit)),
                                  daily_loss_limit=Decimal(str(daily_loss_limit)))

    # --------------------------------------------------------
    # LTP
    # --------------------------------------------------------

    def ltp(
        self,
        instruments: List[str],
    ) -> Dict[str, Dict[str, Any]]:

        if not isinstance(instruments, list):
            raise RuntimeError(
                "FAIL_CLOSED: LTP instruments must be a list."
            )

        formatted_instruments = [
            f"NSE:{symbol}" if ":" not in symbol else symbol
            for symbol in instruments
        ]

        try:
            raw_quotes = self.kite.ltp(formatted_instruments)

            if not isinstance(raw_quotes, dict):
                raise RuntimeError(
                    "FAIL_CLOSED: Malformed LTP response "
                    "(expected dictionary)."
                )

            normalized_quotes = {}

            for key, value in raw_quotes.items():

                if not isinstance(value, dict):
                    raise RuntimeError(
                        f"FAIL_CLOSED: Malformed quote payload "
                        f"for {key}."
                    )

                if "last_price" not in value:
                    raise RuntimeError(
                        f"FAIL_CLOSED: Quote for {key} does not "
                        f"contain last_price."
                    )

                # Preserve original Kite key.
                normalized_quotes[key] = value

                # Also provide clean symbol key.
                clean_symbol = key.split(":")[-1]
                normalized_quotes[clean_symbol] = value

            return normalized_quotes

        except Exception as exc:
            if "FAIL_CLOSED" in str(exc):
                raise

            raise RuntimeError(
                f"FAIL_CLOSED: Failed to fetch LTP quotes: {exc}"
            ) from exc

    # --------------------------------------------------------
    # Emergency exit
    # --------------------------------------------------------

    def get_tick_size(self, tradingsymbol: str) -> Decimal:
        """Return the current NSE instrument tick size; fail closed on ambiguity."""
        if not isinstance(tradingsymbol, str) or not tradingsymbol.strip():
            raise RuntimeError("FAIL_CLOSED: Emergency tick-size lookup requires a valid symbol.")

        try:
            instruments = self.kite.instruments("NSE")
            if not isinstance(instruments, list):
                raise RuntimeError("FAIL_CLOSED: NSE instruments response is not a list.")

            matches = [
                row for row in instruments
                if isinstance(row, dict)
                and row.get("exchange") == "NSE"
                and row.get("tradingsymbol") == tradingsymbol
                and row.get("instrument_type") == "EQ"
                and row.get("segment") == "NSE"
            ]

            if len(matches) != 1:
                raise RuntimeError(
                    f"FAIL_CLOSED: Expected exactly one NSE EQ instrument for {tradingsymbol}; "
                    f"found {len(matches)}."
                )

            tick_size = Decimal(str(matches[0].get("tick_size", "0")))
            if tick_size <= 0:
                raise RuntimeError(
                    f"FAIL_CLOSED: Invalid tick_size for {tradingsymbol}: {tick_size}."
                )
            return tick_size

        except Exception as exc:
            if "FAIL_CLOSED" in str(exc):
                raise
            raise RuntimeError(
                f"FAIL_CLOSED: Failed to obtain NSE tick size for {tradingsymbol}: {exc}"
            ) from exc

    def submit_emergency_exit(
        self,
        *,
        symbol: str,
        quantity: int,
        trigger_price: Decimal,
        source_ltp: Decimal,
        tick_size: Decimal,
        market_protection: Decimal = Decimal("-1"),
        tag: str = "V3.4_EXIT",
    ) -> str:
        """Submit the narrowly scoped V3.4 emergency SL-M liquidation order.

        The engine owns broker-state observation and trigger mathematics. This
        adapter method is the final execution interlock: it validates the
        immutable command and never silently changes it.
        """
        if not self.live_trading:
            raise RuntimeError(
                "SAFETY_HALT: submit_emergency_exit invoked while "
                "LIVE_TRADING_ENABLED is False. Order execution is physically disabled."
            )

        if not isinstance(symbol, str) or not symbol.strip():
            raise RuntimeError("SAFETY_HALT: Emergency exit symbol is invalid.")
        if symbol != symbol.strip():
            raise RuntimeError("SAFETY_HALT: Emergency exit symbol contains surrounding whitespace.")

        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise RuntimeError("SAFETY_HALT: Emergency exit quantity must be a positive integer.")

        try:
            trigger = Decimal(str(trigger_price))
            ltp = Decimal(str(source_ltp))
            tick = Decimal(str(tick_size))
            protection = Decimal(str(market_protection))
        except Exception as exc:
            raise RuntimeError(
                f"SAFETY_HALT: Emergency exit numeric parameter is malformed: {exc}"
            ) from exc

        if ltp <= 0:
            raise RuntimeError("SAFETY_HALT: Emergency exit source LTP must be positive.")
        if tick <= 0:
            raise RuntimeError("SAFETY_HALT: Emergency exit tick size must be positive.")
        if trigger <= 0:
            raise RuntimeError("SAFETY_HALT: Emergency exit trigger price must be positive.")
        if not trigger < ltp:
            raise RuntimeError(
                "SAFETY_HALT: Emergency SELL SL-M trigger must be strictly below source LTP."
            )

        # No silent rounding. The engine must provide an already tick-aligned price.
        tick_units = trigger / tick
        if tick_units != tick_units.to_integral_value():
            raise RuntimeError(
                "SAFETY_HALT: Emergency exit trigger price is not aligned to instrument tick size."
            )

        if protection != Decimal("-1"):
            raise RuntimeError(
                "SAFETY_HALT: V3.4 emergency market protection is frozen to -1 (automatic)."
            )

        if tag != "V3.4_EXIT":
            raise RuntimeError("SAFETY_HALT: Invalid V3.4 emergency exit tag.")

        try:
            order_id = self.kite.place_order(
                variety="regular",
                exchange="NSE",
                tradingsymbol=symbol,
                transaction_type="SELL",
                quantity=quantity,
                product="MIS",
                order_type="SL-M",
                trigger_price=float(trigger),
                market_protection=-1,
                tag=tag,
            )
        except Exception as exc:
            raise RuntimeError(
                f"EMERGENCY_EXIT_SUBMISSION_UNKNOWN_OR_REJECTED: {exc}"
            ) from exc

        if not isinstance(order_id, str) or not order_id.strip():
            raise RuntimeError(
                "SAFETY_HALT: Emergency exit broker response did not contain a valid order ID."
            )

        return order_id

    # --------------------------------------------------------
    # Order placement
    # --------------------------------------------------------

    def place_order(self, **kwargs) -> str:

        transaction_type = str(kwargs.get("transaction_type", "")).upper()
        order_type = str(kwargs.get("order_type", "")).upper()
        if order_type == "MARKET":
            raise RuntimeError(
                "SAFETY_HALT: MARKET orders are blocked by "
                "the V3.4 controlled execution adapter."
            )
        if transaction_type == "BUY":
            if self.entry_authorizer is None:
                raise RuntimeError(
                    "SAFETY_HALT: BUY dispatch has no P0-3B-D entry authorizer."
                )
            if kwargs.get("tag") != "V3.4_ENTRY":
                raise RuntimeError(
                    "SAFETY_HALT: BUY dispatch requires the V3.4_ENTRY execution tag."
                )
            self.entry_authorizer.authorize_buy(
                symbol=kwargs.get("tradingsymbol"),
                quantity=kwargs.get("quantity"),
                price=kwargs.get("price"),
                live_trading_enabled=self.live_trading,
            )
            logging.info("[AUDIT] P03BD_BUY_AUTHORIZED | symbol=%s quantity=%s", kwargs.get("tradingsymbol"), kwargs.get("quantity"))

        if not self.live_trading:
            raise RuntimeError(
                "SAFETY_HALT: place_order invoked while "
                "LIVE_TRADING_ENABLED is False. "
                "Order execution is physically disabled."
            )

        # Additional production guard:
        # Do not allow accidental MARKET orders through this
        # adapter during the controlled rollout.
        return self.kite.place_order(**kwargs)


# ============================================================
# P0-3B-D RUNNER ENTRY AUTHORIZATION
# ============================================================

class RunnerEntryStateStore:
    """Small dedicated durable store; it never mutates the engine state file."""
    def __init__(self, filepath: str):
        self.path = Path(filepath)

    def load(self):
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("FAIL_CLOSED: P0-3B-D durable state is unreadable") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("FAIL_CLOSED: P0-3B-D durable state must be a JSON object")
        return payload

    def save(self, state):
        if not isinstance(state, dict):
            raise RuntimeError("FAIL_CLOSED: P0-3B-D state must be a dictionary")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        encoded = json.dumps(state, sort_keys=True).encode("utf-8")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


class RunnerEntryAuthorizer:
    """P0-3B-F guard for the only adapter BUY dispatch path.

    Entry usage is reserved durably before the broker side effect.  A crash can
    therefore make the policy more conservative, but can never restore an
    already-consumed entry, symbol, cooldown, or turnover allowance.
    """
    MAX_SNAPSHOT_AGE_SECONDS = Decimal("2")

    def __init__(
        self, *, broker, state_store, kill_switch_file, capital_limit,
        daily_loss_limit=None, target_risk_pct=Decimal("0.005"),
        max_trade_risk_pct=Decimal("0.0075"),
        daily_entry_lock_pct=Decimal("0.015"),
        daily_hard_halt_pct=Decimal("0.02"),
        rolling_week_halt_pct=Decimal("0.04"),
        trial_drawdown_halt_pct=Decimal("0.05"),
        daily_turnover_pct=Decimal("1.00"), stop_loss_pct=Decimal("0.02"),
        max_daily_entries=2, max_simultaneous_positions=1,
        entry_cooldown_seconds=900, now_fn=None,
    ):
        self.broker = broker
        self.state_store = state_store
        self.capital_limit = Decimal(str(capital_limit))
        self.target_risk = self.capital_limit * Decimal(str(target_risk_pct))
        self.max_trade_risk = self.capital_limit * Decimal(str(max_trade_risk_pct))
        self.daily_entry_lock = self.capital_limit * Decimal(str(daily_entry_lock_pct))
        self.daily_hard_halt = self.capital_limit * Decimal(str(daily_hard_halt_pct))
        self.rolling_week_halt = self.capital_limit * Decimal(str(rolling_week_halt_pct))
        self.trial_drawdown_halt = self.capital_limit * Decimal(str(trial_drawdown_halt_pct))
        self.daily_turnover_limit = self.capital_limit * Decimal(str(daily_turnover_pct))
        self.stop_loss_pct = Decimal(str(stop_loss_pct))
        self.max_daily_entries = int(max_daily_entries)
        self.max_simultaneous_positions = int(max_simultaneous_positions)
        self.entry_cooldown_seconds = int(entry_cooldown_seconds)
        self._now = now_fn or (lambda: datetime.now(IST))
        if min(self.capital_limit, self.target_risk, self.max_trade_risk,
               self.daily_entry_lock, self.daily_hard_halt,
               self.rolling_week_halt, self.trial_drawdown_halt,
               self.daily_turnover_limit, self.stop_loss_pct) <= 0:
            raise ValueError("P0-3B-F percentage-derived limits must be positive")
        if self.target_risk > self.max_trade_risk:
            raise ValueError("target trade risk cannot exceed maximum authorized risk")
        # The legacy controller remains a backstop.  P0-3B-F owns the tighter
        # percentage boundary and its specific durable halt reason.
        effective_daily_halt = self.daily_hard_halt * Decimal("1000000")
        if daily_loss_limit is not None:
            effective_daily_halt = max(effective_daily_halt, Decimal(str(daily_loss_limit)))
        self.controller = P03RiskController(
            broker=broker,
            store=state_store,
            kill_switch_file=kill_switch_file,
            capital_limit=self.capital_limit,
            daily_loss_limit=effective_daily_halt,
        )

    def risk_based_quantity(self, price):
        price = self.controller._decimal(price, "entry price")
        per_share_risk = price * self.stop_loss_pct
        return int(self.target_risk // per_share_risk)

    def _policy_state(self, snapshot):
        now = self._now()
        day = now.date().isoformat()
        state = dict(getattr(self.controller, "durable_state", {}) or {})
        if state.get("counter_day") != day:
            state.update({"counter_day": day, "entries_today": 0,
                          "symbols_today": [], "turnover_today": "0"})
        week_pnl = dict(state.get("week_pnl_by_day", {}) or {})
        rolling_start = now.date() - timedelta(days=6)
        try:
            week_pnl = {
                recorded_day: value
                for recorded_day, value in week_pnl.items()
                if rolling_start <= datetime.fromisoformat(recorded_day).date() <= now.date()
            }
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "FAIL_CLOSED: P0-3B-F rolling-week counters are invalid"
            ) from exc
        week_pnl[day] = str(snapshot.daily_net_pnl)
        state["week_pnl_by_day"] = week_pnl
        state["rolling_week_start"] = rolling_start.isoformat()
        state["last_observed_daily_net_pnl"] = str(snapshot.daily_net_pnl)
        return state, now, day

    def _hard_policy_halt(self, state, reason):
        state.update({"status": "TRADING_HALTED_RISK", "halt_reason": reason,
                      "halt_source": "P0-3B-F", "halted_at": self._now().isoformat(),
                      "clearance_required": True})
        self.state_store.save(state)
        self.controller.durable_state = state
        raise RuntimeError(f"P0-3B-F BUY authorization blocked: {reason}")

    def _apply_policy_and_reserve(self, *, snapshot, symbol, quantity, price):
        state, now, day = self._policy_state(snapshot)
        daily_loss = max(-snapshot.daily_net_pnl, Decimal("0"))
        week_pnl = sum((Decimal(str(v)) for v in state["week_pnl_by_day"].values()), Decimal("0"))
        cumulative_pnl = Decimal(str(state.get("trial_cumulative_pnl", "0")))
        previous_day_pnl = Decimal(str(state.get("trial_last_recorded_day_pnl", "0")))
        previous_day = state.get("trial_last_recorded_day")
        if previous_day == day:
            cumulative_pnl += snapshot.daily_net_pnl - previous_day_pnl
        else:
            cumulative_pnl += snapshot.daily_net_pnl
        state.update({"trial_cumulative_pnl": str(cumulative_pnl),
                      "trial_last_recorded_day": day,
                      "trial_last_recorded_day_pnl": str(snapshot.daily_net_pnl)})

        if daily_loss >= self.daily_hard_halt:
            self._hard_policy_halt(state, "DAILY_HARD_HALT")
        if week_pnl <= -self.rolling_week_halt:
            self._hard_policy_halt(state, "ROLLING_WEEK_HALT")
        if cumulative_pnl <= -self.trial_drawdown_halt:
            self._hard_policy_halt(state, "TRIAL_DRAWDOWN_HALT")
        if daily_loss >= self.daily_entry_lock:
            state.update({"daily_entry_locked": day, "halt_reason": "DAILY_ENTRY_LOCK"})
            self.state_store.save(state)
            self.controller.durable_state = state
            raise RuntimeError("P0-3B-F BUY authorization blocked: DAILY_ENTRY_LOCK")

        quantity = self.controller._positive_integral(quantity, "proposed quantity")
        price = self.controller._decimal(price, "proposed price")
        proposed_risk = Decimal(quantity) * price * self.stop_loss_pct
        target_quantity = self.risk_based_quantity(price)
        if target_quantity < 1 or quantity > target_quantity or proposed_risk > self.max_trade_risk:
            raise RuntimeError("P0-3B-F BUY authorization blocked: TRADE_RISK_LIMIT")
        if snapshot.deployed_capital > 0 or snapshot.pending_buy_exposure > 0:
            raise RuntimeError("P0-3B-F BUY authorization blocked: SIMULTANEOUS_POSITION_LIMIT")
        if int(state.get("entries_today", 0)) >= self.max_daily_entries:
            raise RuntimeError("P0-3B-F BUY authorization blocked: DAILY_ENTRY_LIMIT")
        symbols = list(state.get("symbols_today", []) or [])
        normalized_symbol = str(symbol).strip().upper()
        if normalized_symbol in symbols:
            raise RuntimeError("P0-3B-F BUY authorization blocked: SAME_SYMBOL_DAILY_LOCK")
        last_entry = state.get("last_entry_reserved_at")
        if last_entry:
            elapsed = (now - datetime.fromisoformat(last_entry)).total_seconds()
            if elapsed < self.entry_cooldown_seconds:
                raise RuntimeError("P0-3B-F BUY authorization blocked: ENTRY_COOLDOWN")
        proposed_turnover = Decimal(str(state.get("turnover_today", "0"))) + Decimal(quantity) * price
        if proposed_turnover > self.daily_turnover_limit:
            raise RuntimeError("P0-3B-F BUY authorization blocked: DAILY_TURNOVER_LIMIT")

        symbols.append(normalized_symbol)
        state.update({"entries_today": int(state.get("entries_today", 0)) + 1,
                      "symbols_today": symbols, "turnover_today": str(proposed_turnover),
                      "last_entry_reserved_at": now.isoformat(),
                      "last_entry_risk": str(proposed_risk),
                      "last_entry_target_quantity": target_quantity})
        self.state_store.save(state)
        self.controller.durable_state = state

    @staticmethod
    def _snapshot_identity(snapshot):
        return (
            snapshot.realized_pnl, snapshot.unrealized_pnl, snapshot.charges,
            snapshot.deployed_capital, snapshot.pending_buy_exposure,
            snapshot.executed_buy_quantity, snapshot.executed_buy_value,
        )

    def _halt(self, reason):
        state = dict(getattr(self.controller, "durable_state", {}) or {})
        state.update({
            "status": "RECONCILIATION_HALT",
            "halt_reason": str(reason),
            "halt_source": "P0-3B-D",
            "halted_at": datetime.now(IST).isoformat(),
            "clearance_required": True,
        })
        self.state_store.save(state)
        self.controller.durable_state = state
        raise RuntimeError(f"P0-3B-D BUY authorization blocked: {reason}")

    def _fresh_snapshot(self):
        try:
            snapshot = self.broker.get_daily_risk_snapshot()
        except Exception as exc:
            self._halt(f"BROKER_SNAPSHOT_UNAVAILABLE: {exc}")
        if not isinstance(snapshot, BrokerRiskSnapshot) or not snapshot.valid:
            self._halt("BROKER_SNAPSHOT_INVALID")
        if not snapshot.is_fresh(self.MAX_SNAPSHOT_AGE_SECONDS):
            self._halt("BROKER_SNAPSHOT_STALE")
        return snapshot

    def authorize_buy(self, *, symbol, quantity, price, live_trading_enabled):
        # The global execution barrier is authoritative and entirely local.
        # Never consult the broker when live trading is disabled: doing so would
        # turn a disabled dispatch into an unnecessary external side effect.
        if not live_trading_enabled:
            self._halt("LIVE_TRADING_DISABLED")

        # First observation establishes the pre-decision broker reality.
        initial = self._fresh_snapshot()

        try:
            first = self.controller.evaluate_entry(symbol, quantity, Decimal(str(price)))
        except Exception as exc:
            self._halt(f"BROKER_SNAPSHOT_INVALID: {exc}")
        if not first.allowed:
            self._halt(first.reason or "ENTRY_DENIED")

        # Re-observe immediately before the irreversible side effect. A changed
        # observation is not silently accepted, even if both snapshots look safe.
        final = self._fresh_snapshot()
        if self._snapshot_identity(initial) != self._snapshot_identity(final):
            self._halt("BROKER_SNAPSHOT_CHANGED_BEFORE_SUBMISSION")

        try:
            final_decision = self.controller.evaluate_entry(symbol, quantity, Decimal(str(price)))
        except Exception as exc:
            self._halt(f"BROKER_SNAPSHOT_INVALID: {exc}")
        if not final_decision.allowed:
            self._halt(final_decision.reason or "ENTRY_DENIED_FINAL_RECHECK")
        self._apply_policy_and_reserve(
            snapshot=final, symbol=symbol, quantity=quantity, price=price,
        )
        return final_decision


# ============================================================
# JSON FILE STORE
# ============================================================

class JsonFileStore:
    """
    Fail-closed persistent state store.

    Important:
    Existing corrupted state is NEVER replaced by a fresh
    BotState.

    Decimal values are serialized as strings and reconstructed
    from the dataclass type annotations.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath

    def save(self, state: BotState) -> None:

        if not is_dataclass(state):
            raise RuntimeError(
                "FAIL_CLOSED: Attempted to persist a non-dataclass state."
            )

        try:
            state_dict = asdict(state)

            # Write to a temporary file first.
            directory = os.path.dirname(
                os.path.abspath(self.filepath)
            )

            fd, temp_path = tempfile.mkstemp(
                prefix=".bot_state_v34_",
                suffix=".tmp",
                dir=directory,
                text=True,
            )

            try:
                with os.fdopen(
                    fd,
                    "w",
                    encoding="utf-8",
                ) as handle:

                    json.dump(
                        state_dict,
                        handle,
                        indent=4,
                        default=str,
                        sort_keys=True,
                    )

                    handle.flush()
                    os.fsync(handle.fileno())

                os.replace(
                    temp_path,
                    self.filepath,
                )

            except Exception:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

                raise

        except Exception as exc:
            logging.critical(
                f"[FATAL] Failed to persist state to "
                f"{self.filepath}: {exc}"
            )
            raise RuntimeError(
                f"FAIL_CLOSED: State persistence failed: {exc}"
            ) from exc

    def load(self, target_date) -> BotState:

        if not os.path.exists(self.filepath):
            logging.info(
                f"[STATE] No existing state file found at "
                f"{self.filepath}. Creating initial state."
            )

            return BotState(
                trading_day=str(target_date)
            )

        try:

            with open(
                self.filepath,
                "r",
                encoding="utf-8",
            ) as handle:

                data = json.load(handle)

            if not isinstance(data, dict):
                raise RuntimeError(
                    "State root must be a JSON object."
                )

            state = _restore_dataclass(
                data,
                BotState,
            )

            logging.info(
                f"[STATE] Existing state loaded successfully "
                f"from {self.filepath}"
            )

            return state

        except Exception as exc:

            logging.critical(
                f"[FATAL] State corruption/read failure detected "
                f"in {self.filepath}: {exc}"
            )

            raise RuntimeError(
                f"FAIL_CLOSED: Unreadable or incompatible state "
                f"file {self.filepath}. Manual intervention required."
            ) from exc


# ============================================================
# CLOCK
# ============================================================

class SystemClock:

    def now(self) -> datetime:
        return datetime.now(IST)


# ============================================================
# AUDIT
# ============================================================

class ProductionAudit:

    def log(
        self,
        event: str,
        **kwargs,
    ) -> None:

        logging.info(
            f"[AUDIT] {event} | Details: {kwargs}"
        )


# ============================================================
# ALERT
# ============================================================

class WebhookAlert:
    """
    Flexible alert adapter.

    Supports:
        send("message")
        send("message", "CRITICAL")
        send(message="message", level="CRITICAL")
        send(level="CRITICAL", message="message")
    """

    def __init__(
        self,
        webhook_url: Optional[str],
    ):
        self.webhook_url = webhook_url

    def send(
        self,
        *args,
        **kwargs,
    ) -> None:

        message = kwargs.pop("message", None)
        level = kwargs.pop("level", None)

        if kwargs:
            raise RuntimeError(
                f"FAIL_CLOSED: Unknown alert arguments: "
                f"{sorted(kwargs.keys())}"
            )

        # Keyword form.
        if message is not None:
            message = str(message)
            level = str(level or "INFO")

        # Positional form.
        elif len(args) == 1:
            message = str(args[0])
            level = "INFO"

        elif len(args) == 2:
            first = str(args[0])
            second = str(args[1])

            known_levels = {
                "DEBUG",
                "INFO",
                "WARNING",
                "WARN",
                "ERROR",
                "CRITICAL",
                "FATAL",
            }

            if first.upper() in known_levels:
                level = first
                message = second
            else:
                message = first
                level = second

        else:
            raise RuntimeError(
                "FAIL_CLOSED: Invalid alert.send() arguments."
            )

        logging.info(
            f"[ALERT [{level}]] {message}"
        )

        if self.webhook_url:

            try:

                response = requests.post(
                    self.webhook_url,
                    json={
                        "content": f"[{level}] {message}"
                    },
                    timeout=5,
                )

                response.raise_for_status()

            except Exception as exc:
                logging.error(
                    f"Failed to dispatch webhook alert: {exc}"
                )


# ============================================================
# PROCESS LOCK
# ============================================================

class SimpleFileLock:
    """
    Atomic PID-owned lock.
    """

    def __init__(
        self,
        lockfile: str = LOCK_FILE,
    ):
        self.lockfile = lockfile

    def acquire(self) -> bool:

        if os.path.exists(self.lockfile):
            raise RuntimeError(
                f"FAIL_CLOSED: Lock file already exists: "
                f"{self.lockfile}. "
                f"Verify that no V3.4 process is running."
            )

        try:

            with open(
                self.lockfile,
                "x",
                encoding="utf-8",
            ) as handle:

                handle.write(
                    str(os.getpid())
                )

            return True

        except FileExistsError as exc:

            raise RuntimeError(
                f"FAIL_CLOSED: Concurrent lock acquisition "
                f"detected on {self.lockfile}"
            ) from exc

        except Exception as exc:

            raise RuntimeError(
                f"FAIL_CLOSED: Unable to acquire execution "
                f"lock: {exc}"
            ) from exc

    def release(self) -> None:

        if not os.path.exists(self.lockfile):
            return

        try:

            with open(
                self.lockfile,
                "r",
                encoding="utf-8",
            ) as handle:

                owner_pid = handle.read().strip()

            if owner_pid != str(os.getpid()):

                logging.critical(
                    f"[FATAL] Refusing to remove lock owned "
                    f"by PID {owner_pid}."
                )

                return

            os.remove(self.lockfile)

            logging.info(
                "[LOCK] Owned execution lock released."
            )

        except Exception as exc:

            logging.critical(
                f"[FATAL] Failed to release owned lock: {exc}"
            )


# ============================================================
# TERMINATOR
# ============================================================

class ProcessTerminator:
    def __init__(self):
        self.halted = False
        self.reason = None

    def halt(self, reason: str) -> None:
        self.halted = True
        self.reason = str(reason)
        logging.critical(f'[TERMINATOR] Hard halt triggered. Reason: {reason}')
        raise SystemExit(1)
class MockKiteClientForPreflight:

    def orders(self):

        return [
            {
                "order_id": "TEST_ORD",
                "status": "COMPLETE",
                "quantity": 100,
                "filled_quantity": 100,
            }
        ]

    def order_history(
        self,
        order_id,
    ):

        return [
            {
                "order_id": order_id,
                "status": "COMPLETE",
                "quantity": 100,
                "filled_quantity": 100,
                "average_price": 1300.0,
            }
        ]

    def positions(self):

        return {
            "net": [
                {
                    "tradingsymbol": "RELIANCE",
                    "quantity": 100,
                }
            ]
        }

    def ltp(
        self,
        instruments,
    ):

        return {
            instruments[0]: {
                "last_price": 1300.0
            }
        }


# ============================================================
# PREFLIGHT TESTS
# ============================================================

def run_preflight_contract_validation() -> None:

    logging.info(
        "===================================================="
    )
    logging.info(
        "V3.4 LOCAL PREFLIGHT CONTRACT VALIDATION"
    )
    logging.info(
        "===================================================="
    )

    mock_kite = MockKiteClientForPreflight()

    adapter = KiteBrokerAdapter(
        mock_kite,
        live_trading=False,
    )

    # 1. Orders
    orders = adapter.get_orders()
    assert isinstance(orders, list)
    assert len(orders) == 1
    logging.info("[PASS] Broker orders contract.")

    # 2. Order details
    details = adapter.get_order_details("TEST_ORD")
    assert isinstance(details, dict)
    assert details["status"] == "COMPLETE"
    logging.info("[PASS] Broker order-history contract.")

    # 3. Positions
    positions = adapter.get_positions()
    assert isinstance(positions, list)
    assert positions[0]["quantity"] == 100
    logging.info("[PASS] Broker positions contract.")

    # 4. LTP
    quotes = adapter.ltp(["RELIANCE"])
    assert isinstance(quotes, dict)
    assert "RELIANCE" in quotes
    assert isinstance(quotes["RELIANCE"], dict)
    assert quotes["RELIANCE"]["last_price"] == 1300.0
    logging.info("[PASS] Broker LTP contract.")

    # 5. Physical order barrier
    try:
        adapter.place_order(
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=100,
            product="MIS",
            order_type="LIMIT",
            price=1300.0,
        )
        raise AssertionError("SAFETY VIOLATION: place_order() succeeded while live trading was disabled.")
    except RuntimeError as exc:
        assert "SAFETY_HALT" in str(exc)
    logging.info("[PASS] Physical order-placement safety barrier.")

    # 6. MARKET order protection
    live_adapter = KiteBrokerAdapter(mock_kite, live_trading=True)
    try:
        live_adapter.place_order(
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=100,
            product="MIS",
            order_type="MARKET",
        )
        raise AssertionError("SAFETY VIOLATION: MARKET order was permitted.")
    except RuntimeError as exc:
        assert "MARKET orders are blocked" in str(exc)
    logging.info("[PASS] MARKET-order safety barrier.")

    # 7. Full persistence round-trip (Matching actual B1.4 schema types: BotState realised/unrealised are str)
    fd, test_state_file = tempfile.mkstemp(
        prefix="v34_persistence_test_",
        suffix=".json",
    )
    os.close(fd)

    try:
        store = JsonFileStore(test_state_file)

        sample_trade = TradeContext(
            symbol="RELIANCE",
            entry_tag="TEST_HWM",
            target_qty=100,
            tranche_qty=100,
            filled_qty=100,
            avg_entry_price=Decimal("1300.50"),
            stop_loss_price=Decimal("1275.25"),
            booked_pnl=Decimal("123.45"),
            stop_order_id="SL_ORD_123",
            exit_order_id="EX_ORD_456",
            entry_order_id="EN_ORD_789",
            entry_max_observed_fill=87,
            entry_hwm_order_id="EN_ORD_789",
            stop_max_observed_fill=92,
            stop_hwm_order_id="SL_ORD_123",
            exit_max_observed_fill=81,
            exit_hwm_order_id="EX_ORD_456",
        )

        sample_state = BotState(
            trading_day="2026-08-11",
            status="MANAGING",
            realised_net_pnl="150.00",
            unrealised_mtm="500.00",
            active_trade=sample_trade,
        )

        store.save(sample_state)
        assert os.path.exists(test_state_file)

        loaded_state = store.load("2026-08-11")
        assert loaded_state.status == "MANAGING"
        assert loaded_state.active_trade is not None

        loaded_trade = loaded_state.active_trade

        # Order IDs
        assert loaded_trade.entry_order_id == "EN_ORD_789"
        assert loaded_trade.stop_order_id == "SL_ORD_123"
        assert loaded_trade.exit_order_id == "EX_ORD_456"

        # TradeContext Decimal fields
        assert isinstance(loaded_trade.avg_entry_price, Decimal)
        assert loaded_trade.avg_entry_price == Decimal("1300.50")
        assert isinstance(loaded_trade.stop_loss_price, Decimal)
        assert loaded_trade.stop_loss_price == Decimal("1275.25")
        assert isinstance(loaded_trade.booked_pnl, Decimal)
        assert loaded_trade.booked_pnl == Decimal("123.45")

        # BotState string fields (matching B1.4 engine schema annotations)
        assert isinstance(loaded_state.realised_net_pnl, str)
        assert loaded_state.realised_net_pnl == "150.00"
        assert isinstance(loaded_state.unrealised_mtm, str)
        assert loaded_state.unrealised_mtm == "500.00"

        # B1.4 HWM fields
        assert loaded_trade.entry_max_observed_fill == 87
        assert loaded_trade.entry_hwm_order_id == "EN_ORD_789"
        assert loaded_trade.stop_max_observed_fill == 92
        assert loaded_trade.stop_hwm_order_id == "SL_ORD_123"
        assert loaded_trade.exit_max_observed_fill == 81
        assert loaded_trade.exit_hwm_order_id == "EX_ORD_456"

        logging.info("[PASS] Full JSON persistence round-trip.")
        logging.info("[PASS] Decimal precision restoration.")
        logging.info("[PASS] BotState string field restoration.")
        logging.info("[PASS] ALL SIX B1.4 HWM fields restored.")
        logging.info("[PASS] ALL THREE HWM order IDs restored.")

    finally:
        try:
            os.remove(test_state_file)
        except OSError:
            pass

    logging.info("====================================================")
    logging.info("LOCAL PREFLIGHT VALIDATION PASSED.")
    logging.info("====================================================")


# ============================================================
# MAIN
# ============================================================

def _capture_shutdown_broker_snapshot(broker) -> bool:
    """Record final broker reality without performing any broker write."""
    try:
        positions = broker.get_positions()
        orders = broker.get_orders()
        if not isinstance(positions, list) or not isinstance(orders, list):
            raise RuntimeError("broker positions/orders response is not a list")

        active_positions, ignored_cnc_holdings = classify_positions(positions)

        terminal_statuses = {"COMPLETE", "CANCELLED", "REJECTED", "EXPIRED"}
        active_orders = []
        for order in orders:
            if not isinstance(order, dict):
                raise RuntimeError("broker order entry is not an object")
            status = str(order.get("status", "")).upper()
            if not status:
                raise RuntimeError("broker order status is missing")
            if status not in terminal_statuses:
                active_orders.append(order)

        logging.info(
            "SHUTDOWN_BROKER_SNAPSHOT | active_positions=%d active_orders=%d ignored_cnc_holdings=%d",
            len(active_positions),
            len(active_orders),
            len(ignored_cnc_holdings),
        )
        if active_positions or active_orders:
            logging.critical(
                "SHUTDOWN_BROKER_STATE_NOT_CLEAN | active_positions=%d active_orders=%d",
                len(active_positions),
                len(active_orders),
            )
            return False
        return True
    except Exception as exc:
        logging.critical(
            "SHUTDOWN_BROKER_STATE_NOT_CLEAN | SHUTDOWN_BROKER_SNAPSHOT_FAILED | %s",
            exc,
        )
        return False

def main():

    exit_code = 0
    lock = SimpleFileLock(LOCK_FILE)
    broker = None

    try:
        # 1. Local Preflight
        run_preflight_contract_validation()

        # 2. Environment
        api_key = os.getenv("KITE_API_KEY")
        access_token = os.getenv("KITE_ACCESS_TOKEN")
        webhook = os.getenv("BOT_ALERT_WEBHOOK")

        if not api_key:
            raise RuntimeError("FAIL_CLOSED: KITE_API_KEY environment variable is missing.")
        if not access_token:
            raise RuntimeError("FAIL_CLOSED: KITE_ACCESS_TOKEN environment variable is missing.")

        # 3. Kite Connection
        logging.info("Initializing Kite Connect client for V3.4...")
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        try:
            profile = kite.profile()
            if not isinstance(profile, dict):
                raise RuntimeError("Malformed profile response.")
            logging.info(f"Connected to Kite successfully. User ID: {profile.get('user_id')} ({profile.get('user_name')})")
        except Exception as exc:
            raise RuntimeError(f"FAIL_CLOSED: Kite authentication/profile verification failed: {exc}") from exc

        # 4. Engine Dependencies
        state_file = STATE_FILE
        config = Config(
            alert_webhook_url=webhook or "",
            max_daily_loss=Decimal("2000"),
            observation_retry_budget=3,
            holidays=NSE_HOLIDAYS_2026,
        )

        account_id = profile.get("user_id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise RuntimeError("FAIL_CLOSED: Kite profile did not provide a usable user_id.")
        broker = KiteBrokerAdapter(
            kite,
            live_trading=LIVE_TRADING_ENABLED,
            expected_account_id=account_id,
        )
        clock = SystemClock()
        sleeper = time.sleep
        store = JsonFileStore(state_file)
        audit = ProductionAudit()
        alert = WebhookAlert(webhook)
        terminator = ProcessTerminator()
        entry_authorizer = RunnerEntryAuthorizer(
            broker=broker,
            state_store=RunnerEntryStateStore(P03_ENTRY_STATE_FILE),
            kill_switch_file=P03_KILL_SWITCH_FILE,
            capital_limit=config.trial_capital,
            # daily_loss_limit is deliberately NOT passed here. RunnerEntryAuthorizer
            # already multiplies its own daily_hard_halt_pct-derived limit by
            # 1,000,000 before comparing against any override, so any value passed
            # as daily_loss_limit could never actually bind - passing config.max_daily_loss
            # here previously disguised a percentage-based limit as an absolute-rupee
            # one that had no real effect. See BRAIN_RESEARCH_SPEC_V15_RISK_CONFIG_REALIGNMENT.md.
            target_risk_pct=config.target_risk_pct,
            max_trade_risk_pct=config.max_trade_risk_pct,
            daily_entry_lock_pct=config.daily_entry_lock_pct,
            daily_hard_halt_pct=config.daily_hard_halt_pct,
            rolling_week_halt_pct=config.rolling_week_halt_pct,
            trial_drawdown_halt_pct=config.trial_drawdown_halt_pct,
            daily_turnover_pct=config.daily_turnover_pct,
            stop_loss_pct=config.stop_loss_pct,
            max_daily_entries=config.max_daily_entries,
            max_simultaneous_positions=config.max_simultaneous_positions,
            entry_cooldown_seconds=config.entry_cooldown_seconds,
        )
        broker.set_entry_authorizer(entry_authorizer)

        # 5. Engine Initialization
        logging.info("====================================================")
        logging.info("Initializing TradingEngineV34")
        logging.info(f"State target: {state_file}")
        logging.info(f"LIVE_TRADING_ENABLED = {LIVE_TRADING_ENABLED}")
        logging.info("====================================================")

        engine = TradingEngineV34(
            broker=broker,
            clock=clock,
            sleeper=sleeper,
            store=store,
            audit=audit,
            alert=alert,
            lock=lock,
            terminator=terminator,
            cfg=config,
        )

        # 6. Startup Reconciliation
        logging.info("Executing startup reconciliation...")
        engine.reconcile_startup()
        logging.info("Startup reconciliation completed successfully.")

        # 7. Mode Declaration
        if not LIVE_TRADING_ENABLED:
            logging.info("====================================================")
            logging.info("OBSERVATION-ONLY MODE ACTIVE")
            logging.info("LIVE ORDER EXECUTION IS DISABLED.")
            logging.info("====================================================")
        else:
            logging.warning("LIVE TRADING MODE ACTIVE.")

        # 8. Operational Loop
        logging.info("Entering V3.4 operational loop.")
        while True:
            engine.step()
            time.sleep(1.0)

    except KeyboardInterrupt:
        logging.info("SHUTDOWN_REQUESTED | signal=KeyboardInterrupt")
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1
        if exit_code != 0:
            logging.critical(f"[TERMINATOR] Process exited with failure code {exit_code}.")
    except Exception as exc:
        exit_code = 1
        logging.critical(f"[FATAL] Unhandled operational exception: {exc}", exc_info=True)
    finally:
        if broker is not None:
            if not _capture_shutdown_broker_snapshot(broker):
                exit_code = 1
        lock.release()
        logging.info(
            "SHUTDOWN_COMPLETE | exit_code=%d session_id=%s log_file=%s",
            exit_code,
            SESSION_ID,
            LOG_FILE,
        )
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
