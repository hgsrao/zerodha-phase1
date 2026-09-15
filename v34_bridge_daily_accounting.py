"""Phase 3.5 — daily-accounting provider: entries/turnover/realized P&L/
charges, and the AuthorizationContext assembly point.

ACCOUNTING BOUNDARY (a design correction made mid-Phase-3.5, after the
first pass assumed order-level `filled_quantity`/`price` were enough):

    Orders determine intent/metadata: tag, transaction_type, product,
    order status, order_id -> strategy mapping.
    Trades determine executions: actual filled quantity, actual fill
    price, fill timestamp, execution identity, realized P&L, executed
    turnover.

Kite's order object reports the *submitted* `price` (limit price), not
the true per-fill execution price, and can execute in multiple chunks -
each chunk is its own `trade`, with its own exchange-generated
`trade_id`, distinct from the parent `order_id`. `trade_id` is therefore
the correct dedup identity for realized-P&L purposes: a partially-filled
order that never reaches COMPLETE (the remainder cancelled/rejected) can
still have generated real, already-economically-real trades that must be
accounted for even though the parent order never completes. Charges are
computed at ORDER granularity instead (Kite's `get_virtual_contract_note`
calculator wants one order's aggregate quantity/average price, not a
per-fill breakdown).

PHASE 3.5A CORRECTION - CHARGES ARE INCREMENTAL, NOT TERMINAL-GATED
(verified against Kite's current, official documentation, not assumed -
an earlier version of this module got both of the following wrong):

1. Charges are recomputed every time an order gets a NEW qualifying
   trade, against that order's CURRENT aggregate quantity/VWAP - not
   deferred until the order reaches a terminal status. Only the POSITIVE
   delta versus what was already recorded (`DailyAccountingState.
   accounted_charge_by_order_id`) is added to `cumulative_charges`. This
   means a still-partially-filling order's true-so-far cost is never
   understated while it remains live, and nothing is ever double-counted
   once it does terminate - the delta for its last chunk is simply
   whatever's left. A charges total LOWER than what's already recorded
   is never silently subtracted - see `DailyAccountingReconciliationError`
   below; that direction of "correction" isn't something this module
   trusts itself to interpret automatically.
2. `get_virtual_contract_note` (Kite's only broker-sourced charges
   endpoint) does NOT include DP (depository participant) charges -
   Zerodha's own documentation calls the virtual contract note an
   approximation and states DP charges are billed separately, once per
   stock per day, specifically when shares are genuinely debited from
   demat (an overnight-held CNC position being sold - Zerodha explicitly
   documents that a same-day CNC buy-then-sell round trip does NOT
   receive this treatment). `dp_triggering` below is determined from
   `PositionBasis.entry_trading_day` - whether the position being closed
   was opened on an earlier trading day than today - not from
   `transaction_type == "SELL"` alone, which would incorrectly flag
   same-day round trips too. No DP amount is ever hardcoded: `reconcile_
   daily_accounting`'s `dp_charge_per_symbol` parameter must be supplied
   by the caller (account-dependent - Zerodha's own DP rate varies by
   primary demat holder category); if a DP-triggering close is detected
   and no rate is available, this module fails closed rather than
   silently omitting the cost, because an omitted charge understates
   cost, which overstates equity - the dangerous direction for a
   risk-gating system to be wrong in.

PRE-LIVE GATE 3 CORRECTION TO THE DP MODEL ABOVE, worth recording rather
than silently folding in: Zerodha's own documentation confirms BTST
(buy-today-sell-tomorrow) sales also attract DP charges, because the
shares are genuinely credited/debited through the customer's demat
account regardless of how few days the position was held - so
`entry_trading_day != state.trading_day` (any exit on a later trading
day than entry, even the very next one) remains the correct trigger for
THIS design; it was never meant to imply "held for many days," only
"not a same-day round trip." The true governing condition is demat
debit/settlement behavior, not elapsed holding time - this module's
day-boundary check is a faithful proxy for that, not the settlement
model itself. STATED, NOT SOLVED: Zerodha also documents cases where DP
can be levied TWICE for the same stock/day when transactions span
separate settlements (e.g. normal-market plus auction transactions, or
certain settlement-holiday situations) - the `(trading_day, symbol)`
dedup ledger here books at most once per day by design, which is correct
for this bot's supported normal CNC flow but is not a universal model of
every Zerodha settlement edge case. Out of scope for the current P02
execution path; not silently assumed to be handled.

TAG-BASED IDENTITY, TRACED FROM THE FROZEN ENGINE, NOT ASSUMED: every
real P02 entry order carries `tag=DEFAULT_ENTRY_TAG` ("V3.4_P02_ENTRY") -
`request_entry()` (institutional_engine_v34_p02_multipos_candidate.py)
raises if any other tag is supplied, so this is a hard invariant, not a
convention that could silently drift. Every real exit order (ordinary SL
completion AND emergency exit both) carries `tag=DEFAULT_EXIT_TAG`
("V3.4_P02_EXIT") via `_build_exit_submission_fingerprint`. Filtering by
(transaction_type, tag, product) therefore identifies P02's own orders
unambiguously against anything else that might exist on the same Kite
account.

WHY entries_today/turnover_today/seconds_since_last_entry NEED NO
DURABLE COUNTER AT ALL: Kite's `orders()`/`trades()` are day-scoped and
complete (Kite's own documentation: the current day's order/trade book,
not a paginated historical ledger) - so these three fields are safely,
completely re-derivable from a single fresh broker call every cycle.
Counting DISTINCT qualifying order_ids among today's BUY trades (not
counting trade/fill events) is what makes a partially-filled order count
as one entry regardless of how many chunks it took, and what makes a
recovered ENTRY_UNKNOWN order count exactly once no matter how many times
the engine's own audit trail mentions it - there is only one order_id
either way. A cancelled/rejected order with zero fill never appears in
`trades()` at all, so it can never inflate this count.

WHY REALIZED P&L NEEDS A DURABLE OPEN-POSITION-BASIS LEDGER, NOT JUST A
DEDUP SET (the corrected design - see v34_bridge_daily_accounting_state.py):
a position can be entered on one trading day and exited days later.
`trades()` only ever shows *today's* fills, so by the time a closing SELL
trade is observed, the entry BUY trade(s) that established its cost basis
may no longer be visible from ANY broker call - they must have been
captured durably the day they actually happened. `open_position_basis`
is that capture, keyed by symbol (not order_id): the frozen engine's own
gate 3 (SYMBOL_ALREADY_HELD, v34_p02_authorizer.authorize_entry) makes at
most one open position per symbol structurally guaranteed, so no lot-
level FIFO matching across overlapping cycles is ever needed - a symbol's
basis is unambiguous while it exists.

FAIL CLOSED, NOT ZERO: a SELL trade observed with no known open basis for
its symbol is not "assume cost basis was zero" - it's proof this
accounting system's durable state does not (or no longer) contains the
information needed to compute a real number, and `DailyAccounting
ReconciliationError` is raised rather than silently producing a wrong
P&L. This is the direct, deliberate consequence of Phase 3.5's own
governing principle: if a required risk number cannot be established
after restart, the correct result is not zero.

GROSS, NOT NET: `cumulative_realized_pnl` accumulates only the price
delta ((sell_price - buy_price) * qty) - never charges. `compute_equity`
(v34_p02_accounting.py, frozen) already subtracts `cumulative_charges` as
its own separate term (`Equity = trial_capital + cumulative_realized_pnl
- cumulative_charges + unrealized_pnl`); netting charges into
`cumulative_realized_pnl` here as well would double-subtract them.

NOTHING IN THIS MODULE CALLS A BROKER DIRECTLY. `reconcile_daily_
accounting()` takes `orders`/`trades` (already-fetched lists) and a
`charge_calculator` callable the caller supplies - a ONE-order-in,
ONE-response-item-out function (in production: `lambda params: broker.
get_virtual_contract_note_for_one_order(params)`; in tests: a fake) -
the same dependency-injection-for-testability pattern this project uses
everywhere else (FakeClock, FakeBroker, ...). Deliberately single-order,
matching v34_bridge_kite_read_only_client.py's own Phase 1A finding: the
virtual-contract-note response never echoes `order_id`, so a batch call
would have no reliable way to correlate results back to requests. This
function does not persist anything either - it returns a new
DailyAccountingState; persisting it is the caller's job, exactly as
`authorize_entry`/`apply_decision` are pure and `authorize_and_persist`
is the store-first wrapper (v34_p02_authorizer.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

from institutional_engine_v34_p02_multipos_candidate import DEFAULT_ENTRY_TAG, DEFAULT_EXIT_TAG
from v34_bridge_daily_accounting_state import DailyAccountingState, PositionBasis
from v34_p02_broker_adapter import AuthorizationContext
from v34_p02_state import Config

IST = timezone(timedelta(hours=5, minutes=30))


class DailyAccountingReconciliationError(RuntimeError):
    """A broker payload could not be trusted, or an accounting invariant
    was violated - always fail closed. Never silently skip, zero-fill, or
    guess at a missing/ambiguous value."""


# ---------------------------------------------------------------------------
# Broker payload parsing - this module owns its own, independent of
# v34_p02_accounting._validate_order (frozen), because it needs fields
# (tag, average_price, fill_timestamp) that module never parses.
# ---------------------------------------------------------------------------

def _parse_kite_naive_ist_timestamp(raw: Any, *, context: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise DailyAccountingReconciliationError(f"{context}: fill_timestamp must be a non-empty string, got {raw!r}.")
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise DailyAccountingReconciliationError(f"{context}: malformed fill_timestamp {raw!r}: {exc}") from exc
    return naive.replace(tzinfo=IST).astimezone(timezone.utc)


def _validated_order_index(orders: List[Any], *, product: str) -> Dict[str, Dict[str, Any]]:
    """order_id -> {order_id, tradingsymbol, transaction_type, product,
    status, tag}. Only the fields this module needs beyond what P02-D's
    own _validate_order already covers (tag, in particular)."""
    index: Dict[str, Dict[str, Any]] = {}
    for order in orders:
        if not isinstance(order, dict):
            raise DailyAccountingReconciliationError(f"orders(): malformed order record {order!r}.")
        order_id = order.get("order_id")
        if not isinstance(order_id, str) or not order_id.strip():
            raise DailyAccountingReconciliationError(f"orders(): order missing a valid order_id: {order!r}.")
        if order_id in index:
            raise DailyAccountingReconciliationError(f"orders(): duplicate order_id {order_id!r}.")
        symbol = order.get("tradingsymbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise DailyAccountingReconciliationError(f"orders(): order {order_id!r} missing a valid tradingsymbol.")
        transaction_type = order.get("transaction_type")
        if transaction_type not in {"BUY", "SELL"}:
            raise DailyAccountingReconciliationError(f"orders(): order {order_id!r} has invalid transaction_type {transaction_type!r}.")
        status = order.get("status")
        if not isinstance(status, str) or not status:
            raise DailyAccountingReconciliationError(f"orders(): order {order_id!r} has no valid status.")
        tag = order.get("tag")
        index[order_id] = {
            "order_id": order_id, "tradingsymbol": symbol, "transaction_type": transaction_type,
            "product": order.get("product"), "status": status, "tag": tag,
        }
    return index


def _validate_trade(trade: Any) -> Dict[str, Any]:
    if not isinstance(trade, dict):
        raise DailyAccountingReconciliationError(f"trades(): malformed trade record {trade!r}.")
    trade_id = trade.get("trade_id")
    order_id = trade.get("order_id")
    symbol = trade.get("tradingsymbol")
    transaction_type = trade.get("transaction_type")
    for name, value in (("trade_id", trade_id), ("order_id", order_id), ("tradingsymbol", symbol)):
        if not isinstance(value, str) or not value.strip():
            raise DailyAccountingReconciliationError(f"trades(): trade missing valid {name}: {trade!r}.")
    if transaction_type not in {"BUY", "SELL"}:
        raise DailyAccountingReconciliationError(f"trades(): trade {trade_id!r} has invalid transaction_type {transaction_type!r}.")
    quantity_raw = trade.get("quantity")
    if isinstance(quantity_raw, bool):
        raise DailyAccountingReconciliationError(f"trades(): trade {trade_id!r} has a boolean quantity.")
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        raise DailyAccountingReconciliationError(f"trades(): trade {trade_id!r} has a non-numeric quantity {quantity_raw!r}.")
    if quantity <= 0:
        raise DailyAccountingReconciliationError(f"trades(): trade {trade_id!r} has a non-positive quantity.")
    price_raw = trade.get("average_price")
    try:
        price = Decimal(str(price_raw))
    except (InvalidOperation, ValueError, TypeError):
        raise DailyAccountingReconciliationError(f"trades(): trade {trade_id!r} has a non-numeric average_price {price_raw!r}.")
    if not price.is_finite() or price <= 0:
        raise DailyAccountingReconciliationError(f"trades(): trade {trade_id!r} has an invalid average_price {price_raw!r}.")
    fill_time = _parse_kite_naive_ist_timestamp(trade.get("fill_timestamp"), context=f"trade {trade_id!r}")
    return {
        "trade_id": trade_id, "order_id": order_id, "tradingsymbol": symbol,
        "transaction_type": transaction_type, "quantity": quantity, "average_price": price, "fill_time_utc": fill_time,
    }


def _qualifying_trades(*, orders_index: Dict[str, Dict[str, Any]], trades: List[Any], product: str) -> List[Dict[str, Any]]:
    """Validated trades whose parent order is one of P02's own tagged
    entry/exit orders in the configured product - sorted chronologically
    (ties broken by trade_id) so basis-building processes fills in
    execution order, not broker response order."""
    result = []
    for raw_trade in trades:
        trade = _validate_trade(raw_trade)
        order = orders_index.get(trade["order_id"])
        if order is None:
            # A trade whose parent order isn't in today's order book at
            # all is an unexplained inconsistency between two broker
            # calls that are supposed to be consistent (both day-scoped,
            # same trading day) - not something to silently skip.
            raise DailyAccountingReconciliationError(
                f"trade {trade['trade_id']!r} references order_id {trade['order_id']!r}, "
                "which does not appear anywhere in today's orders() response."
            )
        if order["product"] != product:
            continue
        if order["tag"] not in (DEFAULT_ENTRY_TAG, DEFAULT_EXIT_TAG):
            continue
        if order["transaction_type"] != trade["transaction_type"]:
            raise DailyAccountingReconciliationError(
                f"trade {trade['trade_id']!r} transaction_type {trade['transaction_type']!r} disagrees "
                f"with its own order {trade['order_id']!r}'s transaction_type {order['transaction_type']!r}."
            )
        expected_type = "BUY" if order["tag"] == DEFAULT_ENTRY_TAG else "SELL"
        if trade["transaction_type"] != expected_type:
            raise DailyAccountingReconciliationError(
                f"trade {trade['trade_id']!r}: tag {order['tag']!r} implies {expected_type}, "
                f"but transaction_type is {trade['transaction_type']!r}."
            )
        result.append(trade)
    result.sort(key=lambda t: (t["fill_time_utc"], t["trade_id"]))
    return result


# ---------------------------------------------------------------------------
# entries_today / turnover_today / seconds_since_last_entry - stateless,
# recomputed fresh every cycle from today's broker data alone.
# ---------------------------------------------------------------------------

def compute_daily_entry_stats(
    *, orders: List[Any], trades: List[Any], cfg: Config, now: datetime,
) -> Tuple[int, Decimal, Optional[Decimal]]:
    orders_index = _validated_order_index(orders, product=cfg.product)
    qualifying = _qualifying_trades(orders_index=orders_index, trades=trades, product=cfg.product)
    buy_trades = [t for t in qualifying if t["transaction_type"] == "BUY"]

    entries_today = len({t["order_id"] for t in buy_trades})
    turnover_today = sum((t["quantity"] * t["average_price"] for t in buy_trades), Decimal("0"))

    if not buy_trades:
        seconds_since_last_entry: Optional[Decimal] = None
    else:
        latest = max(t["fill_time_utc"] for t in buy_trades)
        delta = now - latest
        seconds_since_last_entry = Decimal(str(max(delta.total_seconds(), 0.0)))

    return entries_today, turnover_today, seconds_since_last_entry


# ---------------------------------------------------------------------------
# Realized P&L / charges - durable, trade_id/order_id deduped.
# ---------------------------------------------------------------------------

def _parse_charges_total(item: Any, *, order_id: str) -> Decimal:
    """Kite's confirmed virtual-contract-note response shape - a per-order
    record with a `"charges"` sub-dict carrying `"total"` (see
    v34_bridge_kite_read_only_client.py's Phase 1A docstring). Fails
    closed, loudly, on any other shape rather than silently booking zero
    charges."""
    if not isinstance(item, dict):
        raise DailyAccountingReconciliationError(f"get_virtual_contract_note(): malformed response item for order {order_id!r}: {item!r}.")
    charges = item.get("charges")
    if not isinstance(charges, dict) or "total" not in charges:
        raise DailyAccountingReconciliationError(
            f"get_virtual_contract_note(): response for order {order_id!r} has no charges.total: {item!r}."
        )
    try:
        total = Decimal(str(charges["total"]))
    except (InvalidOperation, ValueError, TypeError):
        raise DailyAccountingReconciliationError(f"get_virtual_contract_note(): non-numeric charges.total for order {order_id!r}: {charges['total']!r}.")
    if not total.is_finite() or total < 0:
        raise DailyAccountingReconciliationError(f"get_virtual_contract_note(): invalid charges.total for order {order_id!r}: {total}.")
    return total


def _order_params_for_charges(*, order: Dict[str, Any], quantity: int, average_price: Decimal, cfg: Config) -> Dict[str, Any]:
    """Builds the request body for Kite's /charges/orders calculator.
    GATE 2 CORRECTION HISTORY, kept honest rather than tidied up:

    1. First version sent `"average_price": str(average_price)` (a
       string) - a live `InputException` against the real endpoint.
    2. Second version guessed the field name itself was wrong - Kite's
       documented RESPONSE example echoes the submitted value back under
       the key `"price"`, so it seemed reasonable that the REQUEST field
       was also `price`. Changed field name AND type (to a float) in the
       same edit - a mistake: two variables changed at once. That
       produced a live `GeneralException: error computing charges:
       average_price value not found` - definitive, explicit evidence
       from Kite's own server that the REQUEST field genuinely is
       `average_price`; the response simply echoes it back under a
       DIFFERENT key than it was submitted with, which this module's
       earlier assumption (request key == response key) got wrong.
    3. This version: field name reverted to `average_price` (confirmed),
       kept as a plain numeric value rather than the original string -
       isolating that one remaining, still-unconfirmed variable instead
       of changing it alongside the field name again."""
    return {
        "order_id": order["order_id"], "exchange": "NSE", "tradingsymbol": order["tradingsymbol"],
        "transaction_type": order["transaction_type"], "variety": "regular", "product": cfg.product,
        "order_type": "MARKET", "quantity": quantity, "average_price": float(average_price),
    }


def _order_aggregate(order_trades: List[Dict[str, Any]]) -> Tuple[int, Decimal]:
    total_qty = sum(t["quantity"] for t in order_trades)
    weighted_avg = sum((t["quantity"] * t["average_price"] for t in order_trades), Decimal("0")) / total_qty
    return total_qty, weighted_avg


def reconcile_daily_accounting(
    state: DailyAccountingState, *, orders: List[Any], trades: List[Any], cfg: Config,
    charge_calculator: Callable[[Dict[str, Any]], Dict[str, Any]],
    dp_charge_per_symbol: Optional[Decimal] = None,
) -> DailyAccountingState:
    """Pure state transition (aside from the injected `charge_calculator`
    calls): folds every not-yet-processed qualifying trade into a new
    DailyAccountingState. Does not persist - see module docstring.

    `dp_charge_per_symbol`: the known, account-specific DP charge to book
    (once per (trading_day, symbol)) whenever a genuinely overnight CNC
    position is closed today - see module docstring's Phase 3.5A note.
    Left as `None` unless the caller has a real, confirmed rate; if a
    DP-triggering close is observed with no rate supplied, this function
    raises rather than silently omitting the cost."""
    orders_index = _validated_order_index(orders, product=cfg.product)
    qualifying = _qualifying_trades(orders_index=orders_index, trades=trades, product=cfg.product)

    cumulative_realized_pnl = state.cumulative_realized_pnl
    booked_trade_ids = set(state.booked_trade_ids)
    open_position_basis = dict(state.open_position_basis)
    dp_triggered_symbols: set = set()

    trades_by_order_id: Dict[str, List[Dict[str, Any]]] = {}
    newly_touched_order_ids: set = set()
    for trade in qualifying:
        trades_by_order_id.setdefault(trade["order_id"], []).append(trade)

        if trade["trade_id"] in booked_trade_ids:
            continue
        newly_touched_order_ids.add(trade["order_id"])

        symbol = trade["tradingsymbol"]
        if trade["transaction_type"] == "BUY":
            existing = open_position_basis.get(symbol)
            if existing is not None and existing.entry_order_id != trade["order_id"]:
                raise DailyAccountingReconciliationError(
                    f"{symbol}: a new entry order {trade['order_id']!r} has a fill while an existing open "
                    f"basis from a different order {existing.entry_order_id!r} is still on record - the "
                    "frozen engine's own SYMBOL_ALREADY_HELD gate should make this impossible."
                )
            if existing is None:
                open_position_basis[symbol] = PositionBasis(
                    entry_order_id=trade["order_id"], quantity=trade["quantity"], avg_price=trade["average_price"],
                    entry_trading_day=state.trading_day,
                )
            else:
                combined_qty = existing.quantity + trade["quantity"]
                combined_avg = (existing.quantity * existing.avg_price + trade["quantity"] * trade["average_price"]) / combined_qty
                open_position_basis[symbol] = PositionBasis(
                    entry_order_id=existing.entry_order_id, quantity=combined_qty, avg_price=combined_avg,
                    entry_trading_day=existing.entry_trading_day,
                )
        else:  # SELL
            existing = open_position_basis.get(symbol)
            if existing is None:
                raise DailyAccountingReconciliationError(
                    f"{symbol}: SELL trade {trade['trade_id']!r} has no known open position basis - cannot "
                    "safely compute realized P&L. Not reconstructible; refusing to assume a zero cost basis."
                )
            if trade["quantity"] > existing.quantity:
                raise DailyAccountingReconciliationError(
                    f"{symbol}: SELL trade {trade['trade_id']!r} quantity {trade['quantity']} exceeds "
                    f"the known open basis quantity {existing.quantity}."
                )
            gross_pnl = (trade["average_price"] - existing.avg_price) * trade["quantity"]
            cumulative_realized_pnl += gross_pnl
            if existing.entry_trading_day != state.trading_day:
                # Genuinely overnight - shares were actually debited from
                # demat, not a same-day CNC round trip (Zerodha documents
                # the latter as not receiving delivery treatment).
                dp_triggered_symbols.add(symbol)
            remaining_qty = existing.quantity - trade["quantity"]
            if remaining_qty == 0:
                del open_position_basis[symbol]
            else:
                open_position_basis[symbol] = PositionBasis(
                    entry_order_id=existing.entry_order_id, quantity=remaining_qty, avg_price=existing.avg_price,
                    entry_trading_day=existing.entry_trading_day,
                )

        booked_trade_ids.add(trade["trade_id"])

    # Exchange/broker/tax charges: order-granularity, incremental. Only
    # orders that got at least one newly-observed fill this pass are
    # recomputed - nothing changed for an order with no new trades, so
    # there is nothing new to charge and no reason to call the broker
    # again for it.
    cumulative_charges = state.cumulative_charges
    accounted_charge_by_order_id = dict(state.accounted_charge_by_order_id)
    for order_id in newly_touched_order_ids:
        order = orders_index[order_id]
        total_qty, weighted_avg = _order_aggregate(trades_by_order_id[order_id])
        params = _order_params_for_charges(order=order, quantity=total_qty, average_price=weighted_avg, cfg=cfg)
        response_item = charge_calculator(params)
        new_total = _parse_charges_total(response_item, order_id=order_id)
        previous_total = accounted_charge_by_order_id.get(order_id, Decimal("0"))
        if new_total < previous_total:
            raise DailyAccountingReconciliationError(
                f"order {order_id!r}: recomputed charges ({new_total}) are LOWER than what was already "
                f"durably recorded ({previous_total}) - an unexplained decrease, refusing to silently "
                "reduce cumulative_charges. This needs investigation, not an automatic correction."
            )
        cumulative_charges += new_total - previous_total
        accounted_charge_by_order_id[order_id] = new_total

    # DP (depository participant) charges - a separate, delivery-
    # settlement-level ledger, dedup'd by (trading_day, symbol) per
    # Zerodha's own once-per-stock-per-day billing model.
    dp_charges_booked = set(state.dp_charges_booked)
    for symbol in dp_triggered_symbols:
        key = (state.trading_day, symbol)
        if key in dp_charges_booked:
            continue
        if dp_charge_per_symbol is None:
            raise DailyAccountingReconciliationError(
                f"{symbol}: an overnight CNC position was sold today (trading_day={state.trading_day!r}), "
                "which Zerodha bills a DP charge for, but no dp_charge_per_symbol was supplied - refusing "
                "to silently omit this cost. Supply the account's confirmed DP rate to proceed."
            )
        cumulative_charges += dp_charge_per_symbol
        dp_charges_booked.add(key)

    return DailyAccountingState(
        trading_day=state.trading_day, cumulative_realized_pnl=cumulative_realized_pnl,
        cumulative_charges=cumulative_charges, checkpoint=state.checkpoint,
        sealed_daily_pnl_series=state.sealed_daily_pnl_series, booked_trade_ids=frozenset(booked_trade_ids),
        accounted_charge_by_order_id=accounted_charge_by_order_id, dp_charges_booked=frozenset(dp_charges_booked),
        open_position_basis=open_position_basis,
    )


def reconcile_and_persist(
    store, state: DailyAccountingState, *, orders: List[Any], trades: List[Any], cfg: Config,
    charge_calculator: Callable[[Dict[str, Any]], Dict[str, Any]],
    dp_charge_per_symbol: Optional[Decimal] = None,
) -> DailyAccountingState:
    """Store-first wrapper, mirroring v34_p02_authorizer.authorize_and_
    persist: the new state is durably saved before this returns. If
    store.save() raises, the caller never sees the updated totals -
    matching this project's crash-and-restart-is-the-safety-mechanism
    discipline rather than swallowing the failure."""
    new_state = reconcile_daily_accounting(
        state, orders=orders, trades=trades, cfg=cfg, charge_calculator=charge_calculator, dp_charge_per_symbol=dp_charge_per_symbol,
    )
    store.save(new_state)
    return new_state


# ---------------------------------------------------------------------------
# AuthorizationContext assembly.
# ---------------------------------------------------------------------------

def build_authorization_context(
    state: DailyAccountingState, *, orders: List[Any], trades: List[Any], cfg: Config, now: datetime,
    reconciliation_clean: bool, kill_switch_active: bool, sector_lookup: Dict[str, str],
) -> AuthorizationContext:
    """`reconciliation_clean`/`kill_switch_active`/`sector_lookup` are
    NOT daily-accounting concerns (traced: the first comes from engine/
    terminator state, the second from an external operator control,
    the third is a static universe mapping) - Phase 3.5 does not invent
    values for them, it only accepts what the caller (Phase 3.6's future
    wiring) already has and folds in the accounting-derived fields."""
    entries_today, turnover_today, seconds_since_last_entry = compute_daily_entry_stats(orders=orders, trades=trades, cfg=cfg, now=now)
    return AuthorizationContext(
        reconciliation_clean=reconciliation_clean, kill_switch_active=kill_switch_active,
        entries_today=entries_today, seconds_since_last_entry=seconds_since_last_entry, turnover_today=turnover_today,
        cumulative_realized_pnl=state.cumulative_realized_pnl, cumulative_charges=state.cumulative_charges,
        checkpoint=state.checkpoint, sealed_daily_pnl_series=state.sealed_daily_pnl_series, sector_lookup=sector_lookup,
    )
