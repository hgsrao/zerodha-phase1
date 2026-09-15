"""V34-P02 CNC-aware broker snapshot / portfolio accounting (P02-D).

Implements exactly the formulas frozen in
`V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` §§3-6, and nothing beyond them - no
new risk policy, no gating/halt decisions (that belongs to the future
authorizer, P02-E), no live order behavior. This module produces numbers;
it does not decide what to do with them.

Two entry points, deliberately kept separate so the frozen daily-cadence
pin (spec §6) cannot be violated by accident:

- `build_portfolio_snapshot(...)` - the LIVE, intraday-safe computation.
  Safe to call every poll cycle. Reads `checkpoint.prior_close_equity` and
  `checkpoint.trial_high_water_mark` as fixed anchors and NEVER writes to
  either - structurally incapable of manufacturing a new high-water mark
  from intraday noise, because it never returns or mutates a checkpoint at
  all.
- `roll_daily_checkpoint(...)` - the ONLY function that may advance
  `trial_high_water_mark`/`prior_close_equity`. Called exactly once per
  trading day, at the same `reconcile_startup()` rollover moment the
  engine (P02-C) already detects.

Broker payload validation is strict throughout and fails closed via
`BrokerObservationContractViolation` (shared with the engine, spec-aligned
naming) on: missing/non-numeric/negative-where-impossible quantities,
prices, or average prices; duplicated position or order identity;
malformed/absent status; and unresolvable order states. This is
deliberately the layer where malformed broker data cannot quietly become
bad math.

Cost-basis figures (`DeployedCapital`, `PendingBuyExposure`,
`ReservedEntryCapital`, `CommittedCapital`) never require a market mark
and remain independently computable even when quotes are missing or
stale. Mark-dependent figures (`GrossMarketExposure`, unrealized P&L, and
therefore `Equity`/`DailyPnL`/`RollingWeekPnL`/`TrialDrawdown`) fail
closed specifically and only when a mark they need is missing or invalid
- they do not take down the cost-basis figures, which are computed by
independently callable functions, not as a side effect of the mark-
dependent path.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from v34_p02_state import (
    BrokerObservationContractViolation,
    Config,
    DailyAccountingCheckpoint,
    EntryReservation,
    order_matches_entry_fingerprint,
)

ACTIVE_ORDER_STATUSES = {
    "OPEN",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
    "AMO REQ RECEIVED",
    "MODIFY PENDING",
    "CANCEL PENDING",
}


# ---------------------------------------------------------------------------
# Strict broker payload validation
# ---------------------------------------------------------------------------

def _validate_position(position: Any) -> Dict[str, Any]:
    if not isinstance(position, dict):
        raise BrokerObservationContractViolation("Broker position entry is not an object.")
    symbol = position.get("tradingsymbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise BrokerObservationContractViolation("Broker position is missing a valid tradingsymbol.")
    product = position.get("product")
    if product not in {"MIS", "CNC"}:
        raise BrokerObservationContractViolation(
            f"Broker position for {symbol} has an unsupported or missing product {product!r}."
        )
    quantity_raw = position.get("quantity")
    if isinstance(quantity_raw, bool):
        raise BrokerObservationContractViolation(f"Broker position for {symbol} has a boolean quantity.")
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        raise BrokerObservationContractViolation(f"Broker position for {symbol} has a non-numeric quantity {quantity_raw!r}.")
    avg_price_raw = position.get("average_price")
    try:
        avg_price = Decimal(str(avg_price_raw))
    except Exception:
        raise BrokerObservationContractViolation(f"Broker position for {symbol} has a non-numeric average_price {avg_price_raw!r}.")
    if not avg_price.is_finite() or avg_price < 0:
        raise BrokerObservationContractViolation(f"Broker position for {symbol} has an invalid average_price {avg_price_raw!r}.")
    if quantity != 0 and avg_price == 0:
        raise BrokerObservationContractViolation(
            f"Broker position for {symbol} has nonzero quantity ({quantity}) with a zero average_price - impossible cost basis."
        )
    return {"tradingsymbol": symbol, "product": product, "quantity": quantity, "average_price": avg_price}


def _validate_order(order: Any) -> Dict[str, Any]:
    if not isinstance(order, dict):
        raise BrokerObservationContractViolation("Broker order entry is not an object.")
    order_id = order.get("order_id")
    if not isinstance(order_id, str) or not order_id.strip():
        raise BrokerObservationContractViolation("Broker order is missing a valid order_id.")
    symbol = order.get("tradingsymbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise BrokerObservationContractViolation(f"Broker order {order_id} is missing a valid tradingsymbol.")
    transaction_type = order.get("transaction_type")
    if transaction_type not in {"BUY", "SELL"}:
        raise BrokerObservationContractViolation(f"Broker order {order_id} has an invalid transaction_type {transaction_type!r}.")
    product = order.get("product")
    if product not in {"MIS", "CNC"}:
        raise BrokerObservationContractViolation(f"Broker order {order_id} has an unsupported or missing product {product!r}.")
    status = order.get("status")
    if not isinstance(status, str) or not status:
        raise BrokerObservationContractViolation(f"Broker order {order_id} has no valid status.")

    quantity_raw = order.get("quantity")
    if isinstance(quantity_raw, bool):
        raise BrokerObservationContractViolation(f"Broker order {order_id} has a boolean quantity.")
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        raise BrokerObservationContractViolation(f"Broker order {order_id} has a non-numeric quantity {quantity_raw!r}.")
    if quantity < 0:
        raise BrokerObservationContractViolation(f"Broker order {order_id} has a negative quantity.")

    filled_raw = order.get("filled_quantity", 0)
    if isinstance(filled_raw, bool):
        raise BrokerObservationContractViolation(f"Broker order {order_id} has a boolean filled_quantity.")
    try:
        filled = int(filled_raw)
    except (TypeError, ValueError):
        raise BrokerObservationContractViolation(f"Broker order {order_id} has a non-numeric filled_quantity {filled_raw!r}.")
    if filled < 0:
        raise BrokerObservationContractViolation(f"Broker order {order_id} has a negative filled_quantity.")
    if filled > quantity:
        raise BrokerObservationContractViolation(f"Broker order {order_id} filled_quantity exceeds quantity.")

    price_raw = order.get("price", 0)
    try:
        price = Decimal(str(price_raw))
    except Exception:
        raise BrokerObservationContractViolation(f"Broker order {order_id} has a non-numeric price {price_raw!r}.")
    if not price.is_finite() or price < 0:
        raise BrokerObservationContractViolation(f"Broker order {order_id} has an invalid price {price_raw!r}.")

    return {
        "order_id": order_id, "tradingsymbol": symbol, "transaction_type": transaction_type,
        "product": product, "status": status, "quantity": quantity, "filled_quantity": filled, "price": price,
    }


def _validate_positions(positions: List[Any]) -> List[Dict[str, Any]]:
    if not isinstance(positions, list):
        raise BrokerObservationContractViolation("Broker positions response is not a list.")
    validated = [_validate_position(p) for p in positions]
    seen = set()
    for p in validated:
        identity = (p["tradingsymbol"], p["product"])
        if identity in seen:
            raise BrokerObservationContractViolation(
                f"Duplicate broker position identity for {identity} - broker positions "
                "must report at most one row per (symbol, product)."
            )
        seen.add(identity)
    return validated


def _validate_orders(orders: List[Any]) -> List[Dict[str, Any]]:
    if not isinstance(orders, list):
        raise BrokerObservationContractViolation("Broker orders response is not a list.")
    validated = [_validate_order(o) for o in orders]
    seen = set()
    for o in validated:
        if o["order_id"] in seen:
            raise BrokerObservationContractViolation(f"Duplicate broker order_id {o['order_id']!r}.")
        seen.add(o["order_id"])
    return validated


def _mark_for_symbol(symbol: str, quotes: Dict[str, Any]) -> Decimal:
    quote = quotes.get(f"NSE:{symbol}")
    if quote is None:
        quote = quotes.get(symbol)
    if not isinstance(quote, dict):
        raise BrokerObservationContractViolation(f"No market mark available for {symbol}.")
    last_price = quote.get("last_price")
    try:
        mark = Decimal(str(last_price))
    except Exception:
        raise BrokerObservationContractViolation(f"Non-numeric market mark for {symbol}: {last_price!r}.")
    if not mark.is_finite() or mark <= 0:
        raise BrokerObservationContractViolation(f"Invalid market mark for {symbol}: {last_price!r}.")
    return mark


# ---------------------------------------------------------------------------
# Cost-basis figures - never require a market mark, independently callable.
# ---------------------------------------------------------------------------

def compute_deployed_capital(positions: List[Any], *, product: str) -> Decimal:
    """Cost basis of confirmed open positions in `product`. Does not read
    quotes - remains valid even when marks are missing or stale."""
    validated = _validate_positions(positions)
    total = Decimal("0")
    for p in validated:
        if p["product"] != product or p["quantity"] == 0:
            continue
        total += Decimal(p["quantity"]) * p["average_price"]
    return total


def compute_pending_buy_exposure(orders: List[Any], *, product: str) -> Decimal:
    """Broker-visible unresolved BUY exposure in `product`: unfilled
    quantity on active BUY orders, valued at their own limit price."""
    validated = _validate_orders(orders)
    total = Decimal("0")
    for o in validated:
        if o["status"] not in ACTIVE_ORDER_STATUSES:
            continue
        if o["transaction_type"] != "BUY" or o["product"] != product:
            continue
        unfilled = o["quantity"] - o["filled_quantity"]
        if unfilled <= 0:
            continue
        if o["price"] <= 0:
            raise BrokerObservationContractViolation(
                f"Broker order {o['order_id']} is an active unfilled BUY with a non-positive price - impossible exposure."
            )
        total += Decimal(unfilled) * o["price"]
    return total


def compute_reserved_entry_capital(reservations: Dict[str, EntryReservation], *, orders: List[Any]) -> Decimal:
    """Durable local reservation amount, excluding any reservation whose
    entry_fingerprint already matches a broker-visible order.

    This is the reservation-to-order handoff dedup: once an order becomes
    broker-visible, the same intended entry's capital is already counted
    via `compute_pending_buy_exposure`. Without this exclusion, the
    transition window between "reserved" and "broker-visible" would
    double-count a single intended entry as both ReservedEntryCapital and
    PendingBuyExposure - the exact trap this function exists to close.
    """
    raw_orders = [o for o in orders if isinstance(o, dict)]
    if len(raw_orders) != len(orders):
        raise BrokerObservationContractViolation("Broker orders response contains a non-object entry.")
    total = Decimal("0")
    for symbol, reservation in reservations.items():
        if reservation.symbol != symbol:
            raise BrokerObservationContractViolation(
                f"EntryReservation.symbol {reservation.symbol!r} disagrees with its own dict key {symbol!r}."
            )
        if reservation.entry_fingerprint is not None:
            already_broker_visible = any(
                order_matches_entry_fingerprint(o, reservation.entry_fingerprint) for o in raw_orders
            )
            if already_broker_visible:
                continue
        total += reservation.reserved_capital
    return total


def compute_committed_capital(*, deployed_capital: Decimal, pending_buy_exposure: Decimal, reserved_entry_capital: Decimal) -> Decimal:
    """One formula, spec §4: CommittedCapital = Deployed + Pending + Reserved."""
    return deployed_capital + pending_buy_exposure + reserved_entry_capital


# ---------------------------------------------------------------------------
# Mark-dependent figures - fail closed specifically on missing/invalid marks.
# ---------------------------------------------------------------------------

def compute_gross_market_exposure(positions: List[Any], *, product: str, quotes: Dict[str, Any]) -> Decimal:
    """Observational only (spec §4/§11) - never gated on. Appreciation
    moves this number, never CommittedCapital."""
    validated = _validate_positions(positions)
    total = Decimal("0")
    for p in validated:
        if p["product"] != product or p["quantity"] == 0:
            continue
        mark = _mark_for_symbol(p["tradingsymbol"], quotes)
        total += abs(Decimal(p["quantity"]) * mark)
    return total


def compute_unrealized_pnl(positions: List[Any], *, product: str, quotes: Dict[str, Any]) -> Decimal:
    validated = _validate_positions(positions)
    total = Decimal("0")
    for p in validated:
        if p["product"] != product or p["quantity"] == 0:
            continue
        mark = _mark_for_symbol(p["tradingsymbol"], quotes)
        total += (mark - p["average_price"]) * Decimal(p["quantity"])
    return total


def compute_equity(*, trial_capital: Decimal, cumulative_realized_pnl: Decimal, cumulative_charges: Decimal, unrealized_pnl: Decimal) -> Decimal:
    """Equity_t = trial_capital + cumulative realized P&L + unrealized
    P&L - cumulative charges (spec §5) - mathematically the same figure as
    Cash + MarketValue once cash is treated as capital not currently tied
    up in cost-basis positions; see spec §5 for the derivation."""
    return trial_capital + cumulative_realized_pnl - cumulative_charges + unrealized_pnl


def compute_daily_pnl(*, equity: Decimal, prior_close_equity: Decimal) -> Decimal:
    """DailyPnL_t = Equity_t - Equity_{t-1,close} (spec §5) - a pure
    differencing of two coherent equity snapshots, not a stitched
    realized+ΔMTM decomposition, so a same-day exit can never be
    double-counted: whatever the position's status today, Equity_t already
    reflects the true current total exactly once."""
    return equity - prior_close_equity


def compute_rolling_week_pnl(*, daily_pnl_today: Decimal, sealed_daily_pnl_series: Dict[str, Decimal]) -> Decimal:
    """RollingWeekPnL_t = today's live DailyPnL + the already-sealed prior
    days in the trailing window (spec §5 rev1, restated §6's cadence
    language: the historical part of the series is frozen/persisted, not
    re-derived from intraday snapshots; only today's slot is still live by
    definition of DailyPnL_t itself). The caller is responsible for
    trimming `sealed_daily_pnl_series` to the trailing window and for
    ensuring it never itself contains today's date."""
    total = daily_pnl_today
    for value in sealed_daily_pnl_series.values():
        if not isinstance(value, Decimal):
            raise BrokerObservationContractViolation("sealed_daily_pnl_series values must be Decimal.")
        total += value
    return total


def compute_trial_drawdown(*, equity: Decimal, trial_high_water_mark: Decimal, trial_capital: Decimal) -> Decimal:
    """TrialDrawdown_t = (HWM - Equity) / trial_capital (spec §6), against
    whatever `trial_high_water_mark` the caller passes in - this function
    never updates it. Peak-to-trough, not loss-from-zero."""
    if trial_capital <= 0:
        raise BrokerObservationContractViolation("trial_capital must be > 0 to compute drawdown.")
    return (trial_high_water_mark - equity) / trial_capital


def verify_no_unexplained_equity_change(
    *, computed_equity: Decimal, expected_equity_from_ledger: Decimal, tolerance: Decimal = Decimal("0.01"),
) -> None:
    """Spec §5's 'external cash flows are prohibited' made concrete and
    enforceable: the caller (the future authorizer, P02-E) is responsible
    for maintaining its own running equity expectation purely from known
    trade/charge events, and passes it in here each cycle for cross-
    verification against the freshly broker-derived `computed_equity`.
    Raising here is the detection primitive; translating a
    BrokerObservationContractViolation into an actual ENGINE_HALT is the
    engine's job (P02-C already does this for every other
    BrokerObservationContractViolation raised during step()), not this
    module's - P02-D produces numbers and proves them, it does not halt
    anything itself.
    """
    diff = computed_equity - expected_equity_from_ledger
    if abs(diff) > tolerance:
        raise BrokerObservationContractViolation(
            f"Unexplained equity change: computed {computed_equity} vs "
            f"ledger-expected {expected_equity_from_ledger} (diff {diff}). "
            "External cash flows are prohibited for the duration of this trial (spec §5)."
        )


# ---------------------------------------------------------------------------
# Aggregate snapshot - the convenience path when every figure is wanted at
# once and marks are known to be available. Individual functions above
# remain independently callable when they are not (spec §11/#10).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    deployed_capital: Decimal
    pending_buy_exposure: Decimal
    reserved_entry_capital: Decimal
    committed_capital: Decimal
    gross_market_exposure: Decimal
    unrealized_pnl: Decimal
    equity: Decimal
    daily_pnl: Decimal
    rolling_week_pnl: Decimal
    trial_drawdown: Decimal


def build_portfolio_snapshot(
    *,
    positions: List[Any],
    orders: List[Any],
    quotes: Dict[str, Any],
    reservations: Dict[str, EntryReservation],
    cfg: Config,
    checkpoint: DailyAccountingCheckpoint,
    cumulative_realized_pnl: Decimal,
    cumulative_charges: Decimal,
    sealed_daily_pnl_series: Dict[str, Decimal],
) -> PortfolioRiskSnapshot:
    """The full live/intraday snapshot. Never reads or writes anything
    beyond `checkpoint`'s existing `prior_close_equity`/
    `trial_high_water_mark` - both are treated as fixed anchors. Calling
    this any number of times within a trading day cannot itself advance
    the high-water mark; only `roll_daily_checkpoint` can."""
    deployed_capital = compute_deployed_capital(positions, product=cfg.product)
    pending_buy_exposure = compute_pending_buy_exposure(orders, product=cfg.product)
    reserved_entry_capital = compute_reserved_entry_capital(reservations, orders=orders)
    committed_capital = compute_committed_capital(
        deployed_capital=deployed_capital, pending_buy_exposure=pending_buy_exposure,
        reserved_entry_capital=reserved_entry_capital,
    )

    gross_market_exposure = compute_gross_market_exposure(positions, product=cfg.product, quotes=quotes)
    unrealized_pnl = compute_unrealized_pnl(positions, product=cfg.product, quotes=quotes)
    equity = compute_equity(
        trial_capital=cfg.trial_capital, cumulative_realized_pnl=cumulative_realized_pnl,
        cumulative_charges=cumulative_charges, unrealized_pnl=unrealized_pnl,
    )
    daily_pnl = compute_daily_pnl(equity=equity, prior_close_equity=checkpoint.prior_close_equity)
    rolling_week_pnl = compute_rolling_week_pnl(daily_pnl_today=daily_pnl, sealed_daily_pnl_series=sealed_daily_pnl_series)
    trial_drawdown = compute_trial_drawdown(
        equity=equity, trial_high_water_mark=checkpoint.trial_high_water_mark, trial_capital=cfg.trial_capital,
    )

    return PortfolioRiskSnapshot(
        deployed_capital=deployed_capital, pending_buy_exposure=pending_buy_exposure,
        reserved_entry_capital=reserved_entry_capital, committed_capital=committed_capital,
        gross_market_exposure=gross_market_exposure, unrealized_pnl=unrealized_pnl,
        equity=equity, daily_pnl=daily_pnl, rolling_week_pnl=rolling_week_pnl, trial_drawdown=trial_drawdown,
    )


# ---------------------------------------------------------------------------
# Daily rollover - the ONLY place trial_high_water_mark/prior_close_equity
# may advance (spec §6's cadence pin).
# ---------------------------------------------------------------------------

def initial_checkpoint(*, trading_day: str, trial_capital: Decimal) -> DailyAccountingCheckpoint:
    """Day-1 bootstrap: before any trading has ever happened, equity is
    exactly trial_capital and the high-water mark starts there too."""
    return DailyAccountingCheckpoint(
        trading_day=trading_day, prior_close_equity=trial_capital,
        day_start_equity=trial_capital, trial_high_water_mark=trial_capital,
    )


def roll_daily_checkpoint(
    *, previous_checkpoint: DailyAccountingCheckpoint, new_trading_day: str, fresh_equity_at_rollover: Decimal,
) -> DailyAccountingCheckpoint:
    """Spec §6, pinned exactly: called once per trading day, at the same
    reconcile_startup() rollover moment the engine already detects.
    `fresh_equity_at_rollover` is Equity_t computed from the first
    authoritative snapshot on the new day - operationally "yesterday's
    close" since nothing has traded yet today. HWM advances here and only
    here: trial_high_water_mark_d = max(trial_high_water_mark_{d-1},
    prior_close_equity_d)."""
    new_hwm = max(previous_checkpoint.trial_high_water_mark, fresh_equity_at_rollover)
    return DailyAccountingCheckpoint(
        trading_day=new_trading_day, prior_close_equity=fresh_equity_at_rollover,
        day_start_equity=fresh_equity_at_rollover, trial_high_water_mark=new_hwm,
    )
