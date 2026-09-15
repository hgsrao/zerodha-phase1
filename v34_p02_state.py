"""V34-P02 multi-position CNC candidate — shared state structures (P02-B).

Implements exactly the data-structure scope frozen in
`V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` §12, and nothing beyond it:
`Config`, `BotState`, `TradeContext`, `DailyAccountingCheckpoint`,
`EntryReservation`, the engine-level/position-level status enums, and the
three control classes from spec §2. No broker execution logic, no
reconciliation logic, no strategy logic, and no risk policy not already
written down in the spec lives here - this module is deliberately boring.

Shared (not split between the future engine and runner files) because the
spec's whole philosophy is "one formula, one representation, not several
independently-computed ones" (§1) - `DailyAccountingCheckpoint` and
`EntryReservation` are runner/authorizer-owned concepts, `TradeContext`/
`BotState` are engine-owned, but both sides must agree on exactly one
shape for each, so both import from here rather than each defining their
own.

Fail-closed deserialization (spec §12, binding): every `from_dict` in this
module raises `StateIntegrityError` on any missing, malformed, or
mistyped safety-critical field. None of them silently substitute a
default for data that came from a persisted file - a corrupted
`DailyAccountingCheckpoint` next to valid `active_trades` must surface as
an integrity failure, never resolve to "prior_close_equity = trial_capital,
continue." Normal in-memory construction (e.g. `TradeContext(symbol=...)`)
is unaffected by this rule - it only binds when reading persisted JSON
back via `from_dict`.

LIVE_TRADING_ENABLED is not defined in this module and nothing here can
place, modify, or cancel a broker order - see the spec's release boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import time
from enum import Enum
from typing import Any, Dict, List, Optional


class StateIntegrityError(RuntimeError):
    """Persisted state is missing, malformed, or otherwise cannot be
    trusted. Always fail closed - never substitute a default for a
    safety-critical field read from disk."""


class BrokerObservationContractViolation(RuntimeError):
    """Broker payload data needed for a reconciliation or accounting
    decision was absent, non-numeric, negative where impossible,
    duplicated, or otherwise untrustworthy. Shared across the engine
    (P02-C) and the accounting layer (P02-D) so both fail closed on
    malformed broker data through exactly one exception type."""


class EntryPolicyDeclinedError(RuntimeError):
    """Raised by a broker adapter's place_order() when an ENTRY_LOCK-class
    condition (spec P02-0 §2) is why a BUY cannot proceed right now - a
    computed financial/policy threshold, or a kill-switch file. This is
    deliberately NOT the same exception family as a genuine broker
    integrity problem: the engine catches this one specifically and
    abandons the one pending entry it applies to, without touching
    terminator.halted/RECONCILIATION_HALT - every other position, and
    request_exit() on any position, must be completely unaffected. Any
    other exception from place_order() is treated as ENGINE_HALT-class,
    exactly as before this class existed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Status / control-class enums (spec §2, §12)
# ---------------------------------------------------------------------------

class EngineStatus(str, Enum):
    """Whole-engine status. Shrunk from the single-position engine's set
    per the V17 plan - FLAT is retired; RUNNING with zero open positions
    replaces it."""
    STARTUP = "STARTUP"
    RECONCILING = "RECONCILING"
    RUNNING = "RUNNING"
    RECONCILIATION_HALT = "RECONCILIATION_HALT"


class PositionStatus(str, Enum):
    """Per-position lifecycle status - one TradeContext's own state,
    independent of every other open position's state (V17 plan)."""
    ENTRY_SUBMIT = "ENTRY_SUBMIT"
    ENTRY_SUBMITTING = "ENTRY_SUBMITTING"
    ENTRY_UNKNOWN = "ENTRY_UNKNOWN"
    ENTRY_PENDING = "ENTRY_PENDING"
    PARTIAL_POSITION = "PARTIAL_POSITION"
    PROTECTION = "PROTECTION"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    MANAGING = "MANAGING"
    EXIT_SUBMIT = "EXIT_SUBMIT"
    EXIT_SUBMITTING = "EXIT_SUBMITTING"
    EXIT_UNKNOWN = "EXIT_UNKNOWN"
    EXIT_PENDING = "EXIT_PENDING"
    # Terminal, non-halting outcome when an ENTRY_LOCK trips between
    # request_entry() and step() reaching submission (spec §2, test #19).
    # Audit-logged, then the position is removed from active_trades - this
    # status is never itself persisted for long.
    ENTRY_ABANDONED_POLICY_HALT = "ENTRY_ABANDONED_POLICY_HALT"


class ControlClass(str, Enum):
    """The three non-overlapping control classes frozen in spec §2.
    ENTRY_LOCK blocks new entries only and self-clears; ENGINE_HALT blocks
    everything and requires clear_halt_and_reconcile(); KILL_SWITCH blocks
    new entries only (existing emergency-exit-always-available property is
    preserved) and requires the operator to remove the kill-switch file."""
    ENTRY_LOCK = "ENTRY_LOCK"
    ENGINE_HALT = "ENGINE_HALT"
    KILL_SWITCH = "KILL_SWITCH"


_ENGINE_STATUS_VALUES = {s.value for s in EngineStatus}
_POSITION_STATUS_VALUES = {s.value for s in PositionStatus}


# ---------------------------------------------------------------------------
# Decimal / primitive helpers - shared canonicalization for every dataclass
# below, so "how do we serialize a Decimal" is answered once.
# ---------------------------------------------------------------------------

def _require_keys(raw: Any, required: set, *, context: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise StateIntegrityError(f"{context}: expected an object, got {type(raw).__name__}.")
    missing = required.difference(raw)
    if missing:
        raise StateIntegrityError(f"{context}: missing required field(s) {sorted(missing)}.")
    return raw


def _decimal_from(raw: Any, *, field_name: str, context: str) -> Decimal:
    if raw is None or isinstance(raw, bool):
        raise StateIntegrityError(f"{context}.{field_name}: must be a numeric string, got {raw!r}.")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise StateIntegrityError(f"{context}.{field_name}: not a valid decimal ({raw!r}).") from exc
    if not value.is_finite():
        raise StateIntegrityError(f"{context}.{field_name}: must be finite, got {raw!r}.")
    return value


def _optional_decimal_from(raw: Any, *, field_name: str, context: str) -> Optional[Decimal]:
    if raw is None:
        return None
    return _decimal_from(raw, field_name=field_name, context=context)


def _int_from(raw: Any, *, field_name: str, context: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise StateIntegrityError(f"{context}.{field_name}: must be an integer, got {raw!r}.")
    return raw


def _str_from(raw: Any, *, field_name: str, context: str, allow_empty: bool = False) -> str:
    if not isinstance(raw, str) or (not allow_empty and not raw.strip()):
        raise StateIntegrityError(f"{context}.{field_name}: must be a non-empty string, got {raw!r}.")
    return raw


def _optional_str_from(raw: Any, *, field_name: str, context: str) -> Optional[str]:
    if raw is None:
        return None
    return _str_from(raw, field_name=field_name, context=context)


def _enum_from(raw: Any, enum_cls, *, field_name: str, context: str):
    if not isinstance(raw, str) or raw not in {member.value for member in enum_cls}:
        valid = sorted(member.value for member in enum_cls)
        raise StateIntegrityError(
            f"{context}.{field_name}: must be one of {valid}, got {raw!r}."
        )
    return enum_cls(raw)


def _optional_dict_from(raw: Any, *, field_name: str, context: str) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise StateIntegrityError(f"{context}.{field_name}: must be an object or null, got {type(raw).__name__}.")
    return raw


# ---------------------------------------------------------------------------
# Config (engine-owned; product parameterized to CNC per V17 plan)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    alert_webhook_url: str
    product: str = "CNC"
    market_protection_pct: Decimal = Decimal("-1")
    tick_size: Decimal = Decimal("0.05")
    holidays: frozenset = field(default_factory=frozenset)
    trial_capital: Decimal = Decimal("100000")
    # Sourced 2026 retail/prop-firm risk-management standards - unchanged
    # from the single-position engine's Config (BRAIN_RESEARCH_SPEC_V15).
    # Position-SIZING convention only (spec P02-0 §6: "per-trade risk
    # budget... unchanged from today"). Under the no-mandatory-SL policy
    # (P02-C) this does NOT imply a real protective stop will ever be
    # placed - it bounds how large a single position can be, as a sizing
    # discipline, exactly as target_risk_pct/max_trade_risk_pct already
    # did independent of whether a stop exists.
    stop_loss_pct: Decimal = Decimal("0.02")
    target_risk_pct: Decimal = Decimal("0.005")
    max_trade_risk_pct: Decimal = Decimal("0.0075")
    daily_entry_lock_pct: Decimal = Decimal("0.015")
    daily_hard_halt_pct: Decimal = Decimal("0.02")
    rolling_week_halt_pct: Decimal = Decimal("0.04")
    trial_drawdown_halt_pct: Decimal = Decimal("0.05")
    daily_turnover_pct: Decimal = Decimal("1.00")
    max_daily_entries: int = 2
    # Real, count-based ceiling as of P02 (spec §6/§9) - default 6, the top
    # of V16's validated no-material-downside range. Never enforced above
    # the universe's structural sector ceiling; that assertion belongs at
    # the runner layer (P02-E), where the sector mapping is available.
    max_simultaneous_positions: int = 6
    entry_cooldown_seconds: int = 900
    observation_retry_budget: int = 3
    universe: List[str] = field(default_factory=lambda: [
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS"
    ])
    market_open_time: time = time(9, 15)
    market_close_time: time = time(15, 30)

    def __post_init__(self) -> None:
        if self.product not in {"MIS", "CNC"}:
            raise ValueError(f"Config.product must be 'MIS' or 'CNC', got {self.product!r}.")
        if self.max_simultaneous_positions < 1:
            raise ValueError("Config.max_simultaneous_positions must be >= 1.")
        if int(self.observation_retry_budget) < 0:
            raise ValueError("Config.observation_retry_budget must be >= 0.")
        if self.trial_capital <= 0:
            raise ValueError("Config.trial_capital must be > 0 (used as a divisor for drawdown).")


# ---------------------------------------------------------------------------
# TradeContext (engine-owned; one per open/opening position, keyed by
# symbol in BotState.active_trades)
# ---------------------------------------------------------------------------

@dataclass
class TradeContext:
    symbol: str
    entry_tag: str
    target_qty: int
    tranche_qty: int
    status: PositionStatus = PositionStatus.ENTRY_SUBMIT
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
    opened_trading_day: Optional[str] = None
    # Diagnostic only (spec §6) - explains "which positions moved the
    # number." Never itself the source of a halt decision; DailyPnL_t is
    # defined on portfolio equity change (spec §5), not this field.
    prior_close_mtm: Decimal = Decimal("0")
    entry_max_observed_fill: int = 0
    entry_hwm_order_id: Optional[str] = None
    stop_max_observed_fill: int = 0
    stop_hwm_order_id: Optional[str] = None
    exit_max_observed_fill: int = 0
    exit_hwm_order_id: Optional[str] = None
    exit_submission_fingerprint: Optional[Dict[str, Any]] = None
    exit_reconciliation_failures: int = 0
    entry_submission_fingerprint: Optional[Dict[str, Any]] = None
    entry_reconciliation_failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_tag": self.entry_tag,
            "target_qty": self.target_qty,
            "tranche_qty": self.tranche_qty,
            "status": self.status.value,
            "filled_qty": self.filled_qty,
            "pending_qty": self.pending_qty,
            "entry_order_id": self.entry_order_id,
            "entry_price": str(self.entry_price),
            "stop_order_id": self.stop_order_id,
            "exit_order_id": self.exit_order_id,
            "order_status": self.order_status,
            "executed_tranches": self.executed_tranches,
            "booked_pnl": str(self.booked_pnl),
            "avg_entry_price": str(self.avg_entry_price),
            "stop_loss_price": str(self.stop_loss_price),
            "exit_reason": self.exit_reason,
            "opened_trading_day": self.opened_trading_day,
            "prior_close_mtm": str(self.prior_close_mtm),
            "entry_max_observed_fill": self.entry_max_observed_fill,
            "entry_hwm_order_id": self.entry_hwm_order_id,
            "stop_max_observed_fill": self.stop_max_observed_fill,
            "stop_hwm_order_id": self.stop_hwm_order_id,
            "exit_max_observed_fill": self.exit_max_observed_fill,
            "exit_hwm_order_id": self.exit_hwm_order_id,
            "exit_submission_fingerprint": self.exit_submission_fingerprint,
            "exit_reconciliation_failures": self.exit_reconciliation_failures,
            "entry_submission_fingerprint": self.entry_submission_fingerprint,
            "entry_reconciliation_failures": self.entry_reconciliation_failures,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "TradeContext":
        c = "TradeContext"
        required = {
            "symbol", "entry_tag", "target_qty", "tranche_qty", "status",
            "filled_qty", "pending_qty", "entry_price", "order_status",
            "executed_tranches", "booked_pnl", "avg_entry_price",
            "stop_loss_price", "prior_close_mtm", "entry_max_observed_fill",
            "stop_max_observed_fill", "exit_max_observed_fill",
            "exit_reconciliation_failures", "entry_reconciliation_failures",
        }
        raw = _require_keys(raw, required, context=c)
        return cls(
            symbol=_str_from(raw["symbol"], field_name="symbol", context=c),
            entry_tag=_str_from(raw["entry_tag"], field_name="entry_tag", context=c),
            target_qty=_int_from(raw["target_qty"], field_name="target_qty", context=c),
            tranche_qty=_int_from(raw["tranche_qty"], field_name="tranche_qty", context=c),
            status=_enum_from(raw["status"], PositionStatus, field_name="status", context=c),
            filled_qty=_int_from(raw["filled_qty"], field_name="filled_qty", context=c),
            pending_qty=_int_from(raw["pending_qty"], field_name="pending_qty", context=c),
            entry_order_id=_optional_str_from(raw.get("entry_order_id"), field_name="entry_order_id", context=c),
            entry_price=_decimal_from(raw["entry_price"], field_name="entry_price", context=c),
            stop_order_id=_optional_str_from(raw.get("stop_order_id"), field_name="stop_order_id", context=c),
            exit_order_id=_optional_str_from(raw.get("exit_order_id"), field_name="exit_order_id", context=c),
            order_status=_str_from(raw["order_status"], field_name="order_status", context=c),
            executed_tranches=_int_from(raw["executed_tranches"], field_name="executed_tranches", context=c),
            booked_pnl=_decimal_from(raw["booked_pnl"], field_name="booked_pnl", context=c),
            avg_entry_price=_decimal_from(raw["avg_entry_price"], field_name="avg_entry_price", context=c),
            stop_loss_price=_decimal_from(raw["stop_loss_price"], field_name="stop_loss_price", context=c),
            exit_reason=_optional_str_from(raw.get("exit_reason"), field_name="exit_reason", context=c),
            opened_trading_day=_optional_str_from(raw.get("opened_trading_day"), field_name="opened_trading_day", context=c),
            prior_close_mtm=_decimal_from(raw["prior_close_mtm"], field_name="prior_close_mtm", context=c),
            entry_max_observed_fill=_int_from(raw["entry_max_observed_fill"], field_name="entry_max_observed_fill", context=c),
            entry_hwm_order_id=_optional_str_from(raw.get("entry_hwm_order_id"), field_name="entry_hwm_order_id", context=c),
            stop_max_observed_fill=_int_from(raw["stop_max_observed_fill"], field_name="stop_max_observed_fill", context=c),
            stop_hwm_order_id=_optional_str_from(raw.get("stop_hwm_order_id"), field_name="stop_hwm_order_id", context=c),
            exit_max_observed_fill=_int_from(raw["exit_max_observed_fill"], field_name="exit_max_observed_fill", context=c),
            exit_hwm_order_id=_optional_str_from(raw.get("exit_hwm_order_id"), field_name="exit_hwm_order_id", context=c),
            exit_submission_fingerprint=_optional_dict_from(raw.get("exit_submission_fingerprint"), field_name="exit_submission_fingerprint", context=c),
            exit_reconciliation_failures=_int_from(raw["exit_reconciliation_failures"], field_name="exit_reconciliation_failures", context=c),
            entry_submission_fingerprint=_optional_dict_from(raw.get("entry_submission_fingerprint"), field_name="entry_submission_fingerprint", context=c),
            entry_reconciliation_failures=_int_from(raw["entry_reconciliation_failures"], field_name="entry_reconciliation_failures", context=c),
        )


# ---------------------------------------------------------------------------
# BotState (engine-owned; active_trades keyed by symbol per spec/V17 plan)
# ---------------------------------------------------------------------------

@dataclass
class BotState:
    trading_day: str
    status: EngineStatus = EngineStatus.STARTUP
    realised_net_pnl: Decimal = Decimal("0")
    unrealised_mtm: Decimal = Decimal("0")
    active_trades: Dict[str, TradeContext] = field(default_factory=dict)
    halt_reason: Optional[str] = None
    halt_source: Optional[str] = None
    halted_at: Optional[str] = None
    clearance_required: bool = False
    operator_acknowledgement: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trading_day": self.trading_day,
            "status": self.status.value,
            "realised_net_pnl": str(self.realised_net_pnl),
            "unrealised_mtm": str(self.unrealised_mtm),
            "active_trades": {symbol: ctx.to_dict() for symbol, ctx in self.active_trades.items()},
            "halt_reason": self.halt_reason,
            "halt_source": self.halt_source,
            "halted_at": self.halted_at,
            "clearance_required": self.clearance_required,
            "operator_acknowledgement": self.operator_acknowledgement,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "BotState":
        c = "BotState"
        required = {
            "trading_day", "status", "realised_net_pnl", "unrealised_mtm",
            "active_trades", "clearance_required",
        }
        raw = _require_keys(raw, required, context=c)

        active_trades_raw = raw["active_trades"]
        if not isinstance(active_trades_raw, dict):
            raise StateIntegrityError(f"{c}.active_trades: must be an object keyed by symbol.")
        active_trades: Dict[str, TradeContext] = {}
        for symbol_key, ctx_raw in active_trades_raw.items():
            if not isinstance(symbol_key, str) or not symbol_key.strip():
                raise StateIntegrityError(f"{c}.active_trades: key {symbol_key!r} is not a valid symbol.")
            ctx = TradeContext.from_dict(ctx_raw)
            if ctx.symbol != symbol_key:
                raise StateIntegrityError(
                    f"{c}.active_trades[{symbol_key!r}]: TradeContext.symbol "
                    f"{ctx.symbol!r} disagrees with its own dict key."
                )
            active_trades[symbol_key] = ctx

        clearance_required = raw["clearance_required"]
        if not isinstance(clearance_required, bool):
            raise StateIntegrityError(f"{c}.clearance_required: must be a boolean, got {clearance_required!r}.")

        return cls(
            trading_day=_str_from(raw["trading_day"], field_name="trading_day", context=c),
            status=_enum_from(raw["status"], EngineStatus, field_name="status", context=c),
            realised_net_pnl=_decimal_from(raw["realised_net_pnl"], field_name="realised_net_pnl", context=c),
            unrealised_mtm=_decimal_from(raw["unrealised_mtm"], field_name="unrealised_mtm", context=c),
            active_trades=active_trades,
            halt_reason=_optional_str_from(raw.get("halt_reason"), field_name="halt_reason", context=c),
            halt_source=_optional_str_from(raw.get("halt_source"), field_name="halt_source", context=c),
            halted_at=_optional_str_from(raw.get("halted_at"), field_name="halted_at", context=c),
            clearance_required=clearance_required,
            operator_acknowledgement=_optional_str_from(raw.get("operator_acknowledgement"), field_name="operator_acknowledgement", context=c),
        )


# ---------------------------------------------------------------------------
# DailyAccountingCheckpoint (runner/authorizer-owned; spec §6)
# ---------------------------------------------------------------------------

@dataclass
class DailyAccountingCheckpoint:
    trading_day: str
    prior_close_equity: Decimal
    day_start_equity: Decimal
    trial_high_water_mark: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trading_day": self.trading_day,
            "prior_close_equity": str(self.prior_close_equity),
            "day_start_equity": str(self.day_start_equity),
            "trial_high_water_mark": str(self.trial_high_water_mark),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "DailyAccountingCheckpoint":
        c = "DailyAccountingCheckpoint"
        required = {"trading_day", "prior_close_equity", "day_start_equity", "trial_high_water_mark"}
        raw = _require_keys(raw, required, context=c)
        return cls(
            trading_day=_str_from(raw["trading_day"], field_name="trading_day", context=c),
            prior_close_equity=_decimal_from(raw["prior_close_equity"], field_name="prior_close_equity", context=c),
            day_start_equity=_decimal_from(raw["day_start_equity"], field_name="day_start_equity", context=c),
            trial_high_water_mark=_decimal_from(raw["trial_high_water_mark"], field_name="trial_high_water_mark", context=c),
        )


# ---------------------------------------------------------------------------
# EntryReservation (runner/authorizer-owned; spec §7/§8/§9)
# ---------------------------------------------------------------------------

@dataclass
class EntryReservation:
    """Durable, provable evidence of an in-flight or recently-authorized
    entry - never authoritative portfolio truth (spec §1), just enough to
    (a) hold capital/sector capacity atomically (§8/§9) and (b) let a
    restart distinguish a PROVABLY_STALE_RESERVATION from an
    UNEXPLAINED_REGISTRY_DISAGREEMENT (§7) by proof, not inference."""
    symbol: str
    sector: str
    reserved_capital: Decimal
    reserved_at: str  # ISO-8601 datetime string
    entry_fingerprint: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            "reserved_capital": str(self.reserved_capital),
            "reserved_at": self.reserved_at,
            "entry_fingerprint": self.entry_fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "EntryReservation":
        c = "EntryReservation"
        required = {"symbol", "sector", "reserved_capital", "reserved_at"}
        raw = _require_keys(raw, required, context=c)
        return cls(
            symbol=_str_from(raw["symbol"], field_name="symbol", context=c),
            sector=_str_from(raw["sector"], field_name="sector", context=c),
            reserved_capital=_decimal_from(raw["reserved_capital"], field_name="reserved_capital", context=c),
            reserved_at=_str_from(raw["reserved_at"], field_name="reserved_at", context=c),
            entry_fingerprint=_optional_dict_from(raw.get("entry_fingerprint"), field_name="entry_fingerprint", context=c),
        )


# ---------------------------------------------------------------------------
# Shared fingerprint utilities - used by the engine (P02-C, entry/exit
# submission reconciliation) and by the accounting layer (P02-D, dedup of
# EntryReservation vs broker-visible PendingBuyExposure) so "does this
# broker order correspond to this durable intent" is answered by exactly
# one implementation, not two independently-maintained copies.
# ---------------------------------------------------------------------------

def canonical_decimal_string(value: Any) -> str:
    try:
        d = Decimal(str(value))
    except Exception as exc:
        raise RuntimeError(f"FAIL_CLOSED: Cannot canonicalize decimal value: {value!r}") from exc
    if not d.is_finite():
        raise RuntimeError("FAIL_CLOSED: Non-finite decimal value.")
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in {"", "-0"}:
        s = "0"
    return s


def order_matches_entry_fingerprint(order: Dict[str, Any], fingerprint: Dict[str, Any]) -> bool:
    if not isinstance(order, dict) or not isinstance(fingerprint, dict):
        return False
    for f in ("exchange", "tradingsymbol", "transaction_type", "product", "order_type", "tag"):
        if order.get(f) != fingerprint.get(f):
            return False
    try:
        if int(order.get("quantity", 0) or 0) != int(fingerprint["quantity"]):
            return False
        if canonical_decimal_string(order.get("price")) != fingerprint["price"]:
            return False
    except Exception:
        return False
    order_id = order.get("order_id")
    return isinstance(order_id, str) and bool(order_id.strip())


def order_matches_exit_fingerprint(order: Dict[str, Any], fingerprint: Dict[str, Any]) -> bool:
    if not isinstance(order, dict) or not isinstance(fingerprint, dict):
        return False
    if order.get("exchange", "NSE") != fingerprint.get("exchange", "NSE"):
        return False
    for f in ("tradingsymbol", "transaction_type", "product", "order_type", "tag"):
        if order.get(f) != fingerprint.get(f):
            return False
    try:
        if int(order.get("quantity", 0) or 0) != int(fingerprint["quantity"]):
            return False
    except (TypeError, ValueError):
        return False
    try:
        if canonical_decimal_string(order.get("trigger_price")) != fingerprint["trigger_price"]:
            return False
        if "market_protection" not in order:
            return False
        if canonical_decimal_string(order.get("market_protection")) != fingerprint["market_protection"]:
            return False
    except Exception:
        return False
    order_id = order.get("order_id")
    return isinstance(order_id, str) and bool(order_id.strip())
