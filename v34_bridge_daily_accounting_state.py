"""Phase 3.5 — durable daily-accounting state.

New module, new dataclasses - none of this is something the frozen
engine defines or touches anywhere (traced exhaustively: grepping the
frozen engine for `realised_net_pnl`/`booked_pnl` assignment turns up
zero hits - the frozen engine never computes or persists realized P&L at
all; every exit path is exactly `del self.state.active_trades[symbol]`).
This module exists precisely because none of this has ever existed
anywhere in this project until now.

PHASE 3.5A CORRECTION (this module's second revision): the original
charges design (`charged_order_ids: FrozenSet[str]`, booked once per
order at terminal status) had two real problems, found via verification
against Kite's actual, current API documentation rather than assumption:

1. A partially-filled order's charges would be computed against its
   FINAL aggregate quantity/price only once terminal - correct in the
   end, but understates cost while the order remains live, and a
   genuinely non-terminal-but-still-filling order (more chunks still to
   come) would never get charged at all until it happened to reach a
   terminal status.
2. Zerodha's virtual-contract-note calculator (the only broker-sourced
   charges endpoint this project has access to) explicitly does NOT
   include DP (depository participant) charges - Zerodha's own
   documentation calls it an approximation and states DP charges are
   billed separately, once per stock per day, specifically when shares
   are actually debited from demat (a genuine overnight-held CNC
   position being sold - NOT a same-day round trip, which Zerodha
   documents as attracting different, intraday-like treatment instead).
   `cumulative_charges` was therefore silently incomplete for exactly
   the case this bot's whole product type (CNC) makes ordinary: a
   position entered on one day and exited on a later one.

THE CORRECTED DESIGN:

- `accounted_charge_by_order_id: Dict[str, Decimal]` replaces
  `charged_order_ids`. Not a dedup set - a running "how much have we
  already booked for this order" record. Each reconciliation pass
  recomputes charges for the order's CURRENT known aggregate quantity/
  VWAP and books only the POSITIVE delta against what was already
  recorded - so a still-partially-filling order gets charged
  incrementally, correctly, as each new chunk of information arrives,
  never understated while live and never double-counted once terminal.
  A computed total LOWER than what's already recorded is not silently
  subtracted - v34_bridge_daily_accounting.py fails closed on that,
  because a broker-reported decrease is either evidence of stale/
  incomplete broker data (this account's true costs cannot have gone
  down) or a genuine correction that needs a human's attention, not code
  silently guessing which.
- `dp_charges_booked: FrozenSet[Tuple[str, str]]` - a SEPARATE ledger,
  keyed by (trading_day, symbol), because DP charges are a delivery-
  settlement-level cost, not an order-level or trade-level one. Dedup at
  this granularity matches Zerodha's own billing model exactly: once per
  stock per day, however many partial SELL fills close that day's
  position. No amount is ever hardcoded anywhere in this module or
  v34_bridge_daily_accounting.py - DP rates are account-dependent
  (varies by primary demat holder category per Zerodha's own published
  rate schedule) and there is no broker-observable endpoint for it in
  this project's surface. The caller must supply a known rate; if a
  DP-triggering event is detected and no rate is available, v34_bridge_
  daily_accounting.py's reconciliation fails closed rather than silently
  omitting the charge - an omitted charge understates cost, which
  overstates equity, which is the dangerous direction for a risk-gating
  system to be wrong in.
- `PositionBasis.entry_trading_day` - THE NEW FIELD THAT MAKES DP
  DETECTION POSSIBLE. DP applicability is NOT `transaction_type ==
  SELL` (Zerodha explicitly documents that a same-day CNC buy-then-sell
  round trip does not receive genuine delivery treatment) - it is
  whether the closing SELL trade's position was established on an
  EARLIER trading day than today, i.e. `entry_trading_day != state.
  trading_day` at the moment of closing. Recording the entry's own
  trading day at basis-creation time is what lets a much-later
  reconciliation pass answer that question at all - it cannot be
  reconstructed after the fact from `open_position_basis` alone once the
  cycle has closed. (Simplifying assumption, stated rather than hidden:
  an entry order's OWN fills are treated as same-day, matching Kite's
  documented order-book transience - "the order book/history lives for
  one day" - so an order not fully filled by day-end is expected to
  expire/be cancelled by the exchange rather than carry fills into a
  second day; `entry_trading_day` is therefore set once, from the first
  fill, and not revisited by later fills of the same entry order.)

WHAT'S STILL DURABLE, UNCHANGED FROM 3.5'S FIRST PASS, AND WHY:

- `cumulative_realized_pnl` / `cumulative_charges`: Kite's `orders()`/
  `trades()` are both day-scoped and transient - no broker endpoint
  anywhere in this project's surface can reconstruct P&L or charges
  realized/incurred on an earlier day. Must be durable and incremental.
- `checkpoint` / `sealed_daily_pnl_series`: `roll_daily_checkpoint()`
  (v34_p02_accounting.py, frozen) is explicitly the only function allowed
  to advance either the checkpoint or the HWM, and it needs yesterday's
  values as an input - gone if not persisted.
- `open_position_basis`: a position can be entered on one trading day and
  exited days later; the entry's cost basis must be captured durably the
  day it happens, or it is unrecoverable by exit time. Keyed by symbol,
  not order_id/lot: the frozen engine's own gate 3 (SYMBOL_ALREADY_HELD,
  v34_p02_authorizer.authorize_entry) structurally guarantees at most one
  open position per symbol at a time, so a plain per-symbol running
  (quantity, weighted-average-price) is sufficient - no lot-level FIFO
  matching across overlapping cycles is ever needed.
- `booked_trade_ids`: every trade_id whose economic effect has already
  been durably folded into `open_position_basis`/`cumulative_realized_
  pnl` - `trade_id`, not `order_id`, because a partially-filled order
  that never reaches COMPLETE can still generate real, distinct,
  already-economically-real trades needing exactly-once treatment
  independent of whether its parent order ever completes.

All of the above are kept in ONE durable envelope, not split across
files: booking a trade/charge/DP-event must atomically update the
running totals and the relevant dedup/delta record together, or a crash
between them creates exactly the double/under-count these ledgers exist
to prevent.

entries_today/turnover_today/seconds_since_last_entry are deliberately
NOT part of this durable state - safely and completely re-derivable
every cycle from today's (day-scoped, complete) broker orders+trades.
See v34_bridge_daily_accounting.py.

Schema versioning matches every other store in this project: no version
field on the dataclasses themselves; versioning lives in the STORE's own
file envelope instead (v34_bridge_daily_accounting_store.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, FrozenSet, Tuple

from v34_p02_state import DailyAccountingCheckpoint


class DailyAccountingIntegrityError(RuntimeError):
    """A DailyAccountingState field failed validation - always fail
    closed, never silently default or drop a field. A new exception type
    for new dataclasses, deliberately not borrowing v34_p02_state's
    StateIntegrityError: that type belongs to the frozen file's own
    from_dict() implementations, not to dataclasses the frozen file never
    defines."""


def _decimal_from(value: Any, *, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: expected a string/int, got {value!r}.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: not a valid decimal: {value!r}.") from exc
    if not result.is_finite():
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: must be finite, got {value!r}.")
    return result


def _str_from(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: expected a non-empty string, got {value!r}.")
    return value


def _positive_int_from(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: expected an int, got {value!r}.")
    if value <= 0:
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: must be positive, got {value!r}.")
    return value


def _nonnegative_decimal_from(value: Any, *, field_name: str) -> Decimal:
    result = _decimal_from(value, field_name=field_name)
    if result < 0:
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: must be non-negative, got {value!r}.")
    return result


@dataclass(frozen=True)
class PositionBasis:
    """The durable, broker-independent record of an open position's cost
    basis - quantity still open, its weighted-average entry price, and
    (Phase 3.5A) the trading day its entry order's first fill was
    observed on, needed to determine DP-charge applicability at exit
    time. Increased by qualifying BUY trades, decreased (and, at zero,
    removed) by qualifying SELL trades. Never negative; a SELL trade that
    would take quantity below zero is a contract violation the
    reconciliation logic must refuse, not silently clamp
    (v34_bridge_daily_accounting.py)."""
    entry_order_id: str
    quantity: int
    avg_price: Decimal
    entry_trading_day: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_order_id": self.entry_order_id, "quantity": self.quantity,
            "avg_price": str(self.avg_price), "entry_trading_day": self.entry_trading_day,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PositionBasis":
        if not isinstance(raw, dict):
            raise DailyAccountingIntegrityError(f"PositionBasis: expected an object, got {raw!r}.")
        required = {"entry_order_id", "quantity", "avg_price", "entry_trading_day"}
        missing = required.difference(raw)
        if missing:
            raise DailyAccountingIntegrityError(f"PositionBasis: missing required field(s) {sorted(missing)}.")
        return cls(
            entry_order_id=_str_from(raw["entry_order_id"], field_name="open_position_basis.entry_order_id"),
            quantity=_positive_int_from(raw["quantity"], field_name="open_position_basis.quantity"),
            avg_price=_decimal_from(raw["avg_price"], field_name="open_position_basis.avg_price"),
            entry_trading_day=_str_from(raw["entry_trading_day"], field_name="open_position_basis.entry_trading_day"),
        )


@dataclass(frozen=True)
class DailyAccountingState:
    trading_day: str
    cumulative_realized_pnl: Decimal
    cumulative_charges: Decimal
    checkpoint: DailyAccountingCheckpoint
    sealed_daily_pnl_series: Dict[str, Decimal] = field(default_factory=dict)
    booked_trade_ids: FrozenSet[str] = field(default_factory=frozenset)
    accounted_charge_by_order_id: Dict[str, Decimal] = field(default_factory=dict)
    dp_charges_booked: FrozenSet[Tuple[str, str]] = field(default_factory=frozenset)
    open_position_basis: Dict[str, PositionBasis] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trading_day": self.trading_day,
            "cumulative_realized_pnl": str(self.cumulative_realized_pnl),
            "cumulative_charges": str(self.cumulative_charges),
            "checkpoint": self.checkpoint.to_dict(),
            "sealed_daily_pnl_series": {k: str(v) for k, v in self.sealed_daily_pnl_series.items()},
            # Sorted throughout for deterministic serialization - matches
            # this project's established convention (AuditSink's
            # sort_keys, etc.) of never letting dict/set iteration order
            # leak into persisted bytes.
            "booked_trade_ids": sorted(self.booked_trade_ids),
            "accounted_charge_by_order_id": {k: str(v) for k, v in sorted(self.accounted_charge_by_order_id.items())},
            "dp_charges_booked": sorted([list(pair) for pair in self.dp_charges_booked]),
            "open_position_basis": {symbol: basis.to_dict() for symbol, basis in sorted(self.open_position_basis.items())},
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "DailyAccountingState":
        if not isinstance(raw, dict):
            raise DailyAccountingIntegrityError(f"DailyAccountingState: expected an object, got {type(raw).__name__}.")
        required = {
            "trading_day", "cumulative_realized_pnl", "cumulative_charges", "checkpoint",
            "sealed_daily_pnl_series", "booked_trade_ids", "accounted_charge_by_order_id",
            "dp_charges_booked", "open_position_basis",
        }
        missing = required.difference(raw)
        if missing:
            raise DailyAccountingIntegrityError(f"DailyAccountingState: missing required field(s) {sorted(missing)}.")

        sealed_raw = raw["sealed_daily_pnl_series"]
        if not isinstance(sealed_raw, dict):
            raise DailyAccountingIntegrityError(f"DailyAccountingState.sealed_daily_pnl_series: expected an object, got {sealed_raw!r}.")
        sealed = {
            _str_from(k, field_name="sealed_daily_pnl_series key"): _decimal_from(v, field_name=f"sealed_daily_pnl_series[{k!r}]")
            for k, v in sealed_raw.items()
        }

        booked = _frozenset_of_str(raw["booked_trade_ids"], field_name="booked_trade_ids")

        charge_raw = raw["accounted_charge_by_order_id"]
        if not isinstance(charge_raw, dict):
            raise DailyAccountingIntegrityError(f"DailyAccountingState.accounted_charge_by_order_id: expected an object, got {charge_raw!r}.")
        accounted_charge_by_order_id = {
            _str_from(k, field_name="accounted_charge_by_order_id key"): _nonnegative_decimal_from(v, field_name=f"accounted_charge_by_order_id[{k!r}]")
            for k, v in charge_raw.items()
        }

        dp_raw = raw["dp_charges_booked"]
        if not isinstance(dp_raw, list):
            raise DailyAccountingIntegrityError(f"DailyAccountingState.dp_charges_booked: expected a list, got {dp_raw!r}.")
        dp_pairs = []
        for entry in dp_raw:
            if not isinstance(entry, list) or len(entry) != 2:
                raise DailyAccountingIntegrityError(f"DailyAccountingState.dp_charges_booked: malformed entry {entry!r}, expected [trading_day, symbol].")
            day = _str_from(entry[0], field_name="dp_charges_booked trading_day")
            symbol = _str_from(entry[1], field_name="dp_charges_booked symbol")
            dp_pairs.append((day, symbol))
        dp_charges_booked = frozenset(dp_pairs)
        if len(dp_charges_booked) != len(dp_pairs):
            raise DailyAccountingIntegrityError("DailyAccountingState.dp_charges_booked: contains a duplicate (trading_day, symbol) entry.")

        basis_raw = raw["open_position_basis"]
        if not isinstance(basis_raw, dict):
            raise DailyAccountingIntegrityError(f"DailyAccountingState.open_position_basis: expected an object, got {basis_raw!r}.")
        open_position_basis = {}
        for symbol, basis_dict in basis_raw.items():
            symbol = _str_from(symbol, field_name="open_position_basis key")
            open_position_basis[symbol] = PositionBasis.from_dict(basis_dict)

        return cls(
            trading_day=_str_from(raw["trading_day"], field_name="trading_day"),
            cumulative_realized_pnl=_decimal_from(raw["cumulative_realized_pnl"], field_name="cumulative_realized_pnl"),
            cumulative_charges=_decimal_from(raw["cumulative_charges"], field_name="cumulative_charges"),
            checkpoint=DailyAccountingCheckpoint.from_dict(raw["checkpoint"]),
            sealed_daily_pnl_series=sealed,
            booked_trade_ids=booked,
            accounted_charge_by_order_id=accounted_charge_by_order_id,
            dp_charges_booked=dp_charges_booked,
            open_position_basis=open_position_basis,
        )


def _frozenset_of_str(raw: Any, *, field_name: str) -> FrozenSet[str]:
    if not isinstance(raw, list):
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: expected a list, got {raw!r}.")
    result = frozenset(_str_from(v, field_name=f"{field_name} entry") for v in raw)
    if len(result) != len(raw):
        raise DailyAccountingIntegrityError(f"DailyAccountingState.{field_name}: contains a duplicate entry.")
    return result


def initial_daily_accounting_state(*, trading_day: str, trial_capital: Decimal) -> DailyAccountingState:
    """Day-1 bootstrap, mirroring v34_p02_accounting.initial_checkpoint's
    own framing: before any trading has ever happened in the trial,
    cumulative P&L/charges are exactly zero and nothing has been booked
    or is open."""
    from v34_p02_accounting import initial_checkpoint  # local import: avoids a hard module-load-order dependency for callers that only need the dataclasses
    return DailyAccountingState(
        trading_day=trading_day, cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("0"),
        checkpoint=initial_checkpoint(trading_day=trading_day, trial_capital=trial_capital),
        sealed_daily_pnl_series={}, booked_trade_ids=frozenset(), accounted_charge_by_order_id={},
        dp_charges_booked=frozenset(), open_position_basis={},
    )
