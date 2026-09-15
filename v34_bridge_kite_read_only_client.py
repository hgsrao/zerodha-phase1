"""V11 -> P02 Rebalance Bridge — the shared, real, read-only Kite client.

Implements exactly the read-only quarter of the "raw broker" protocol
v34_p02_broker_adapter.KiteBrokerAdapterMultiPos wraps: get_positions(),
get_orders(), get_order_details(order_id), ltp(symbols), get_tick_size
(symbol). That is the same protocol FakeBroker (test-only,
test_v34_p02_multipos_engine.py) already implements, and the same
protocol v34_bridge_trigger_main.py's two flagged gaps
(_fetch_live_quotes, _fetch_current_portfolio) both need. One real
client closes three flagged gaps at once, instead of three independent
implementations of the same handful of Kite calls.

Deliberately does NOT implement place_order()/submit_emergency_exit() -
the write half of the raw-broker protocol, and the only calls in this
entire bridge capable of moving real capital. That is its own separate,
even-more-scrutinized pass; v34_bridge_runner_main.py's _build_engine()
still refuses to start until it exists.

Exception taxonomy - this session's explicit design decision, not
run_production.py's older KiteBrokerAdapter pattern (which wraps every
exception, including genuine network errors, into one generic
RuntimeError - deliberately not mirrored here, because it conflicts with
this decision):

- kiteconnect.exceptions.* (NetworkException, TokenException, etc.)
  propagate completely UNWRAPPED - there is no try/except anywhere in
  this module around a self.kite.*() call itself. Two existing, tested
  mechanisms depend on seeing the exact, unmodified exception: P02's own
  _is_transient_observation_exception (institutional_engine_v34_p02_
  multipos_candidate.py) and this bridge's own _is_rate_limit_exception
  (v34_bridge_runner_entrypoint.py) - both duck-type on the real
  exception's __module__/__name__/.code. Wrapping or catching-and-
  re-raising here would make both blind.
- A response that connects fine but cannot be trusted - missing fields,
  wrong types, unexpected shape - raises KiteResponseMalformedError. A
  distinct failure class from a network/API error: the API answered
  successfully; the payload is the problem, not the connection.

No retries anywhere in this module, for any exception, transient or not.
This client sits underneath two layers that already own retry/backoff
decisions - P02's observation_retry_budget, and this bridge's own 429
handler in run_forever() (v34_bridge_runner_entrypoint.py). A third,
silent retry layer here would hide real failures from both of them, and
could itself retrigger the very rate limit it would be "recovering"
from (see this session's own design discussion on shadow retries).

No hard dependency on the kiteconnect package: `kite` is duck-typed
(Any), and this module never imports kiteconnect.exceptions - it doesn't
need to, since it never classifies or catches those exceptions at all,
only lets them propagate. Stays importable even where kiteconnect isn't
installed.

PHASE 1A ADDITION (accounting-observation extension, added for Phase
3.5): three more read-only Kite surfaces this module didn't originally
expose, needed because Phase 3.5's trace concluded that `get_orders()`/
`get_order_details()` alone are insufficient for realized-P&L/turnover
accounting - Kite's order object reports the *submitted* `price`, not
the true per-fill execution price, and carries no charges at all.

- `get_trades()` / `get_order_trades(order_id)` wrap `kite.trades()` /
  `kite.order_trades(order_id)` - Kite's own execution-level records.
  Each individual fill is a separate trade with its own exchange-
  generated `trade_id`, distinct from the parent order's `order_id`;
  Kite explicitly documents that one order can execute in multiple
  chunks, each becoming its own trade. `trade_id` is therefore the
  correct dedup identity for accounting purposes (Phase 3.5's own
  provider uses it that way) - `order_id` alone is not, because a
  partially-filled order that never reaches COMPLETE (the rest
  cancelled/rejected) can still have already generated real, distinct
  trades with real economic effect.
- `get_virtual_contract_note(order_params)` wraps `kite.get_virtual_
  contract_note(...)`, Kite's calculator endpoint (POST /charges/orders)
  for exact per-order brokerage/STT/stamp-duty/exchange/SEBI/GST charges
  given the executed order's own attributes (quantity, average price,
  etc.) - Kite's order/trade responses never report charges directly, so
  this is the only broker-sourced source of truth for exchange/broker/tax
  charges (though NOT the complete cost of CNC delivery trading - see
  below).
  CONFIRMED against Kite's official documentation (not assumed, after an
  earlier version of this module got it wrong): the endpoint's `data` is
  a list of per-order records, one per input order, each carrying its own
  `charges.total`. The Python SDK strips the usual `{"data": ...}`
  envelope, so `kite.get_virtual_contract_note(...)` returns that list
  directly - `_parse_charges_total` in v34_bridge_daily_accounting.py
  reads exactly `item["charges"]["total"]` per item. The response does
  NOT echo `order_id` even though the request REQUIRES it - CONFIRMED
  live (a `GeneralException: order_id value not found` when omitted;
  the documentation only says it's "accepted," which reads as optional
  but isn't) - so there is no way to correlate a multi-order batch
  response back to its requests by ID even though every request must
  supply one. `get_virtual_contract_note_for_one_order` below exists specifically
  so callers never have to: always submit exactly one order and take the
  single resulting item positionally, which is unambiguous by
  construction.
  DP (depository participant) charges are explicitly NOT included in this
  calculator's output - Zerodha's own documentation calls the virtual
  contract note an approximation and states DP charges appear separately
  in the funds statement, charged once per stock per day when shares are
  actually debited from demat (i.e., a genuine overnight-held CNC
  position being sold, not a same-day round trip). This client has no
  method for DP charges at all - there is no read-only Kite endpoint for
  it in this project's surface; v34_bridge_daily_accounting.py's DP-
  charge ledger requires the caller to supply a known per-symbol amount
  rather than inventing or hardcoding one (account-dependent, not a
  broker-observable constant).
  This endpoint is HTTP POST but is a pure calculator with respect to the
  trading account - it computes charges for hypothetical/executed order
  attributes the caller supplies, it does not place, modify, or cancel
  anything - so it stays within this module's read-only, no-execution-
  authority contract.

Same invariants as every other method in this module: zero retry, Kite
exceptions propagate completely unwrapped, strict response validation,
`KiteResponseMalformedError` on any untrustworthy shape.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from kite_request_governor import DEFAULT as GOV_DEFAULT
from kite_request_governor import QUOTE as GOV_QUOTE
from kite_request_governor import KiteRequestGovernor


class KiteResponseMalformedError(RuntimeError):
    """The Kite API call itself succeeded - no network/API exception was
    raised - but the response shape cannot be trusted: a missing field,
    a wrong type, or a structurally unexpected payload. Never raised for
    a network/API error; see module docstring's exception taxonomy."""


class KiteReadOnlyClient:
    """Wraps a real kiteconnect.KiteConnect instance (or any object with
    the same read-only method surface - never isinstance-checked, so a
    test double just needs matching method names). Implements exactly
    the read-only quarter of the raw-broker protocol
    KiteBrokerAdapterMultiPos expects."""

    def __init__(self, kite: Any, *, governor: Optional[KiteRequestGovernor] = None):
        self.kite = kite
        self._tick_size_cache: Optional[Dict[str, Decimal]] = None
        # EA1-R1, 2026-08-19: optional, defaults to None (zero effect on
        # any existing caller/test) - see kite_request_governor.py's
        # module docstring and v34_p02_broker_adapter.py's own governor
        # wiring for the full reasoning. Calling acquire() before a
        # self.kite.*() call does not change this module's "exceptions
        # propagate completely unwrapped" contract at all - it's a new
        # step before the call, never a wrapper around it.
        self.governor = governor

    def _gate(self, endpoint_class: str) -> None:
        if self.governor is not None:
            self.governor.acquire(endpoint_class)

    def get_positions(self) -> List[Dict[str, Any]]:
        """The "net" position book - Kite's positions() call returns both
        "day" (intraday) and "net" (overall) books; P02's own accounting
        (v34_p02_accounting.build_portfolio_snapshot) expects the overall
        holding per symbol, which "net" already is. Not filtered by
        product here - P02's own _matching_positions() already filters
        by cfg.product downstream; this method's job is only to report
        what the broker actually shows, unfiltered, matching FakeBroker's
        own pass-through behavior in every existing test."""
        self._gate(GOV_DEFAULT)
        response = self.kite.positions()
        if not isinstance(response, dict) or "net" not in response:
            raise KiteResponseMalformedError(
                f"kite.positions(): expected a dict with a 'net' key, got {response!r}."
            )
        net = response["net"]
        if not isinstance(net, list):
            raise KiteResponseMalformedError(f"kite.positions()['net']: expected a list, got {net!r}.")
        for position in net:
            if not isinstance(position, dict):
                raise KiteResponseMalformedError(f"kite.positions()['net']: malformed position record {position!r}.")
        return net

    def get_orders(self) -> List[Dict[str, Any]]:
        self._gate(GOV_DEFAULT)
        orders = self.kite.orders()
        if not isinstance(orders, list):
            raise KiteResponseMalformedError(f"kite.orders(): expected a list, got {orders!r}.")
        for order in orders:
            if not isinstance(order, dict):
                raise KiteResponseMalformedError(f"kite.orders(): malformed order record {order!r}.")
        return orders

    def get_order_details(self, order_id: str) -> Dict[str, Any]:
        """The order's current status - Kite's order_history(order_id)
        returns the full sequence of state transitions for one order;
        the last entry is its most recent (current) state, which is what
        every caller of get_order_details() in this project expects (a
        single dict, not a history)."""
        if not order_id:
            raise KiteResponseMalformedError("get_order_details(): empty order_id.")
        self._gate(GOV_DEFAULT)
        history = self.kite.order_history(order_id)
        if not isinstance(history, list) or not history:
            raise KiteResponseMalformedError(
                f"kite.order_history({order_id!r}): expected a non-empty list, got {history!r}."
            )
        latest = history[-1]
        if not isinstance(latest, dict):
            raise KiteResponseMalformedError(f"kite.order_history({order_id!r}): malformed record {latest!r}.")
        return latest

    def ltp(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """`symbols` are bare tradingsymbols (e.g. "RELIANCE"), not
        "NSE:RELIANCE" - matching how every existing caller in this
        project already calls .ltp() (e.g. v34_bridge_runner_core.
        _fetch_live_price does engine.broker.ltp([symbol]) with a bare
        symbol, and institutional_engine_v34_p02_multipos_candidate.py's
        own internal calls are the same). The "NSE:" prefix Kite's real
        API requires is added here, once, rather than by every caller."""
        instruments = [f"NSE:{s}" for s in symbols]
        self._gate(GOV_QUOTE)
        response = self.kite.ltp(instruments)
        if not isinstance(response, dict):
            raise KiteResponseMalformedError(f"kite.ltp({instruments!r}): expected a dict, got {response!r}.")
        for key, quote in response.items():
            if not isinstance(quote, dict) or "last_price" not in quote:
                raise KiteResponseMalformedError(f"kite.ltp(): malformed quote for {key!r}: {quote!r}.")
        return response

    def get_trades(self) -> List[Dict[str, Any]]:
        """Today's executions, one row per fill (not per order) - Kite's
        `trades()` is, like `orders()`, day-scoped/transient, not a
        paginated historical ledger."""
        self._gate(GOV_DEFAULT)
        trades = self.kite.trades()
        if not isinstance(trades, list):
            raise KiteResponseMalformedError(f"kite.trades(): expected a list, got {trades!r}.")
        for trade in trades:
            self._validate_trade_shape(trade, source="kite.trades()")
        return trades

    def get_order_trades(self, order_id: str) -> List[Dict[str, Any]]:
        """Every fill that belongs to exactly one order - Kite's
        `order_trades(order_id)`. Used when a caller already knows the
        order_id and wants its executions specifically, rather than
        filtering the full day's trade list itself."""
        if not order_id:
            raise KiteResponseMalformedError("get_order_trades(): empty order_id.")
        self._gate(GOV_DEFAULT)
        trades = self.kite.order_trades(order_id)
        if not isinstance(trades, list):
            raise KiteResponseMalformedError(f"kite.order_trades({order_id!r}): expected a list, got {trades!r}.")
        for trade in trades:
            self._validate_trade_shape(trade, source=f"kite.order_trades({order_id!r})")
        return trades

    @staticmethod
    def _validate_trade_shape(trade: Any, *, source: str) -> None:
        if not isinstance(trade, dict):
            raise KiteResponseMalformedError(f"{source}: malformed trade record {trade!r}.")
        for field_name in ("trade_id", "order_id", "tradingsymbol", "transaction_type"):
            value = trade.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise KiteResponseMalformedError(f"{source}: trade record missing valid {field_name!r}: {trade!r}.")

    def get_virtual_contract_note(self, order_params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Kite's calculator endpoint for exact per-order statutory
        charges - see module docstring's Phase 1A note for the confirmed
        response shape and its DP-charge exclusion. `order_params` is the
        list of executed-order attribute dicts Kite's SDK expects
        (exchange, tradingsymbol, transaction_type, variety, product,
        order_type, quantity, average_price - a plain number). GATE 2
        CORRECTION HISTORY, kept honest: an earlier version of this
        docstring guessed the field should be renamed to `price`,
        reasoning from Kite's documented RESPONSE example (which echoes
        the submitted value back under the key `"price"`) - that guess
        was live-tested and proven wrong: Kite's own server returned
        `GeneralException: error computing charges: average_price value
        not found` once the request stopped sending `average_price`. The
        response's echoed key name does NOT match the request's own field
        name - confirmed, not assumed, after getting this wrong once.
        This method does not construct or validate that shape itself, and
        does not attempt to correlate response items back to specific
        input orders (the response never echoes `order_id`) - see
        `get_virtual_contract_note_for_one_order` for the caller-facing
        pattern that avoids needing that correlation at all."""
        self._gate(GOV_DEFAULT)
        response = self.kite.get_virtual_contract_note(order_params)
        if not isinstance(response, list):
            raise KiteResponseMalformedError(f"kite.get_virtual_contract_note(): expected a list, got {response!r}.")
        for item in response:
            if not isinstance(item, dict):
                raise KiteResponseMalformedError(f"kite.get_virtual_contract_note(): malformed item {item!r}.")
        return response

    def get_virtual_contract_note_for_one_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """Submits exactly one order's params and returns its single
        response item - the only way to use this endpoint unambiguously,
        since the response never echoes `order_id` back and therefore
        offers no way to correlate a multi-order batch response to its
        requests. Callers (v34_bridge_daily_accounting.py) should always
        use this, not the batch method, for that reason."""
        response = self.get_virtual_contract_note([order_params])
        if len(response) != 1:
            raise KiteResponseMalformedError(
                f"kite.get_virtual_contract_note(): expected exactly one response item for one submitted "
                f"order, got {len(response)}: {response!r}."
            )
        return response[0]

    def get_tick_size(self, symbol: str) -> Decimal:
        """NSE equity tick size comes from Kite's instruments dump - the
        entire exchange's instrument master (thousands of rows), not a
        per-symbol endpoint - so it is fetched once and cached for this
        client's lifetime, not on every call. 0.05 is the near-universal
        NSE equity tick and is what every test double in this project has
        always defaulted to, but this looks it up for real rather than
        assuming it."""
        if self._tick_size_cache is None:
            self._tick_size_cache = self._load_tick_sizes()
        if symbol not in self._tick_size_cache:
            raise KiteResponseMalformedError(f"get_tick_size({symbol!r}): symbol not found in the NSE instruments dump.")
        return self._tick_size_cache[symbol]

    def _load_tick_sizes(self) -> Dict[str, Decimal]:
        self._gate(GOV_DEFAULT)
        instruments = self.kite.instruments("NSE")
        if not isinstance(instruments, list):
            raise KiteResponseMalformedError(f"kite.instruments('NSE'): expected a list, got {instruments!r}.")
        cache: Dict[str, Decimal] = {}
        for row in instruments:
            if not isinstance(row, dict) or "tradingsymbol" not in row or "tick_size" not in row:
                raise KiteResponseMalformedError(f"kite.instruments('NSE'): malformed instrument row {row!r}.")
            try:
                cache[row["tradingsymbol"]] = Decimal(str(row["tick_size"]))
            except (TypeError, ValueError, InvalidOperation) as exc:
                raise KiteResponseMalformedError(
                    f"kite.instruments('NSE'): malformed tick_size for {row.get('tradingsymbol')!r}: {exc}"
                ) from exc
        return cache
