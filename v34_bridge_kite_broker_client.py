"""V11 -> P02 Rebalance Bridge — Phase 2: the runner's full raw-broker client.

`KiteBrokerClient` is the complete "raw broker" object
`v34_p02_broker_adapter.KiteBrokerAdapterMultiPos` wraps: all five
read-only methods (delegated to the sealed, unmodified
v34_bridge_kite_read_only_client.KiteReadOnlyClient — Phase 1, frozen,
not reimplemented here) plus the two write methods,
`place_order()`/`submit_emergency_exit()`, that Phase 1 deliberately did
not implement. This is the only class in the whole bridge that can
submit a real order.

THE GOVERNING RULE FOR BOTH WRITE METHODS: do nothing this module hasn't
been explicitly told to do. No try/except around the real
self.kite.place_order() call. No validation of what it returns. No
retries. The only thing added beyond a bare pass-through is the
LIVE_TRADING_ENABLED gate (checked before any broker call) and the
kwarg translation from P02's own call shape to Kite's real SDK
parameter names.

Why "do nothing extra" is the correct design, not a missing feature -
verified by reading institutional_engine_v34_p02_multipos_candidate.py's
_step_entry_submit() and _step_exit_submit() line by line before writing
any of this file:

- Both already wrap their call to self.broker.place_order()/
  submit_emergency_exit() in a try/except that classifies the outcome
  via _is_transient_submission_exception(): a transient-shaped exception
  (kiteconnect.exceptions.NetworkException with code in {502,503,504},
  TimeoutError, ConnectionError, requests.exceptions.*) moves the
  position to ENTRY_UNKNOWN/EXIT_UNKNOWN - "ambiguous, reconcile against
  the broker's own order list before assuming anything" - never a silent
  retry. Anything else classified as non-transient goes straight to
  trigger_hard_halt(). This module must let the exact, unmodified
  exception reach that classifier - catching or rewrapping it here would
  make P02's own classification blind, exactly the "shadow retry /
  shadow interception" failure mode already rejected for the read-only
  client, now doubled for the write path where the cost of getting it
  wrong is a real order.
- Entry and exit are NOT symmetric here, and this module must not
  flatten that: a malformed-but-non-exception order_id on entry (call
  succeeded, returned garbage) moves to ENTRY_UNKNOWN
  (institutional_engine_v34_p02_multipos_candidate.py line ~1000) -
  ambiguous, so it gets reconciled. The identical case on exit goes
  straight to trigger_hard_halt() (same file, line ~1450) - P02 refuses
  any doubt whatsoever on the liquidation path, full stop. If this
  module validated or "cleaned up" a malformed return value on either
  path, it would silently override a deliberate, already-frozen safety
  asymmetry. So: neither write method inspects its own return value.
  Whatever self.kite.place_order() returns - a valid order_id, an empty
  string, None, garbage - is returned unmodified, exactly as received.

The LIVE_TRADING_ENABLED gate is the one thing this module adds that
P02's frozen call sites know nothing about, and it matches
run_production.py's own established convention for this project exactly
(plain RuntimeError, "SAFETY_HALT:" prefix, no dedicated exception type)
rather than inventing a new one: deliberately a bare, unchained
RuntimeError (never `raise ... from <something>`) so its __cause__ is
None and _is_transient_submission_exception's __cause__-walk cannot
mistake it for a transient network condition - a disabled write
authority is a definite, non-ambiguous local block, and must route to
trigger_hard_halt(), never ENTRY_UNKNOWN/EXIT_UNKNOWN.

No retries anywhere in this module, matching Phase 1's own decision and
this session's own "shadow retries blind the layer above" reasoning -
now applied to a class of call where a shadow retry could mean a real
duplicate order, not just a duplicate read.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, List, Optional

from v34_bridge_kite_read_only_client import KiteReadOnlyClient


class KiteBrokerClient:
    """The complete raw-broker object for P02's multi-position engine.
    Read methods delegate to a sealed KiteReadOnlyClient instance
    (composition - Phase 1 is a dependency here, never modified).
    Write methods (place_order, submit_emergency_exit) are new in this
    pass - see module docstring for the exact "do nothing extra" rule
    both must follow."""

    def __init__(self, kite: Any, *, live_trading_enabled: bool, product: str = "CNC"):
        self.kite = kite
        self.live_trading_enabled = live_trading_enabled
        self.product = product
        self._read = KiteReadOnlyClient(kite)

    # -- read delegation - Phase 1, unmodified, just forwarded -------------

    def get_positions(self):
        return self._read.get_positions()

    def get_orders(self):
        return self._read.get_orders()

    def get_order_details(self, order_id: str):
        return self._read.get_order_details(order_id)

    def ltp(self, symbols: List[str]):
        return self._read.ltp(symbols)

    def get_tick_size(self, symbol: str) -> Decimal:
        return self._read.get_tick_size(symbol)

    # -- write authority - Phase 2, new -------------------------------------

    def _require_live_trading_enabled(self, *, action: str) -> None:
        if not self.live_trading_enabled:
            # Bare RuntimeError, no `from` chaining - see module docstring
            # for exactly why this must never look transient to
            # _is_transient_submission_exception.
            raise RuntimeError(
                f"SAFETY_HALT: {action} invoked while LIVE_TRADING_ENABLED is False. "
                "Order execution is physically disabled. Zero broker calls were made."
            )

    def place_order(
        self, *, exchange: str, tradingsymbol: str, transaction_type: str,
        quantity: int, product: str, order_type: str, price: float, tag: str,
    ) -> Any:
        """P02's own call (institutional_engine_v34_p02_multipos_
        candidate.py's _step_entry_submit(), via KiteBrokerAdapterMultiPos)
        already supplies every real Kite parameter this needs except
        `variety` - the one thing P02's frozen call site never had to
        know about, since `variety` is a broker-transport detail, not a
        trading decision. Injected here, nothing else translated.

        Returns exactly what self.kite.place_order() returns, and raises
        exactly what it raises - see module docstring."""
        self._require_live_trading_enabled(action="place_order")
        return self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=exchange, tradingsymbol=tradingsymbol, transaction_type=transaction_type,
            quantity=quantity, product=product, order_type=order_type, price=price, tag=tag,
        )

    def submit_emergency_exit(
        self, *, symbol: str, quantity: int, trigger_price: Decimal, source_ltp: Decimal,
        tick_size: Decimal, market_protection: Decimal, tag: str,
    ) -> Any:
        """P02's own call (_step_exit_submit()) passes `trigger_price` and
        `market_protection` - both real, native Kite SL-M parameters,
        forwarded unchanged (only the Decimal->float conversion Kite's
        SDK expects is applied). `source_ltp` and `tick_size` are context
        P02 computed for itself before calling this (the trigger price
        was already derived from them) - neither is a real Kite
        place_order() parameter, so neither is forwarded; they exist in
        this signature only because P02's call site supplies them and
        this method must accept whatever P02 actually sends.

        `symbol` -> `tradingsymbol`, `exchange`/`transaction_type`=SELL/
        `variety`/`order_type`=SL-M are supplied here since P02's exit
        call site (unlike its entry call site) does not carry them at
        all - an emergency exit is always NSE/SELL/regular-variety/SL-M
        by construction, not a per-call decision.

        Returns exactly what self.kite.place_order() returns, and raises
        exactly what it raises - see module docstring. In particular: a
        malformed-but-non-exception return value is NOT validated or
        rejected here - P02's own _step_exit_submit() already halts hard
        on that case itself (deliberately not ENTRY_UNKNOWN-style
        reconciliation - see module docstring's entry/exit asymmetry
        note), and duplicating that check here would risk silently
        disagreeing with it."""
        self._require_live_trading_enabled(action="submit_emergency_exit")
        return self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange="NSE", tradingsymbol=symbol, transaction_type="SELL",
            quantity=quantity, product=self.product, order_type=self.kite.ORDER_TYPE_SLM,
            trigger_price=float(trigger_price), market_protection=float(market_protection),
            tag=tag,
        )
