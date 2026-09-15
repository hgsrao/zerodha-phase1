import os
import sys
import json
import time
import logging
import tempfile
from datetime import datetime
from decimal import Decimal
from dataclasses import asdict, is_dataclass
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

import institutional_engine_v34_staging as institutional_engine_v34
from institutional_engine_v34_staging import (
    TradingEngineV34,
    TradingEngineV34,
    Config,
    BotState,
    TradeContext,
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

STATE_FILE = "bot_state_v34_staging.json"
LOCK_FILE = "bot_lock_v34_staging.lock"
LOG_FILE = "bot_production.log"


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
    ):
        self.kite = kite
        self.live_trading = live_trading

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
    # Order placement
    # --------------------------------------------------------

    def place_order(self, **kwargs) -> str:

        if not self.live_trading:
            raise RuntimeError(
                "SAFETY_HALT: place_order invoked while "
                "LIVE_TRADING_ENABLED is False. "
                "Order execution is physically disabled."
            )

        # Additional production guard:
        # Do not allow accidental MARKET orders through this
        # adapter during the controlled rollout.
        order_type = str(
            kwargs.get("order_type", "")
        ).upper()

        if order_type == "MARKET":
            raise RuntimeError(
                "SAFETY_HALT: MARKET orders are blocked by "
                "the V3.4 controlled execution adapter."
            )

        return self.kite.place_order(**kwargs)


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

def main():

    exit_code = 0
    lock = SimpleFileLock(LOCK_FILE)

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
        )

        broker = KiteBrokerAdapter(kite, live_trading=LIVE_TRADING_ENABLED)
        clock = SystemClock()
        sleeper = time.sleep
        store = JsonFileStore(state_file)
        audit = ProductionAudit()
        alert = WebhookAlert(webhook)
        terminator = ProcessTerminator()

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
        logging.info("Shutdown signal received.")
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1
        if exit_code != 0:
            logging.critical(f"[TERMINATOR] Process exited with failure code {exit_code}.")
    except Exception as exc:
        exit_code = 1
        logging.critical(f"[FATAL] Unhandled operational exception: {exc}", exc_info=True)
    finally:
        lock.release()
        logging.info(f"V3.4 bot stopped with exit code {exit_code}.")
        sys.exit(exit_code)


if __name__ == "__main__":
    main()