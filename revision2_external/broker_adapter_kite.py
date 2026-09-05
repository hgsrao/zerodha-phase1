"""Box 10 (UnifiedExecution) -- real kiteconnect + tenacity broker adapter.

runtime/operating_mode.py already has a KiteBrokerAdapter class, but it's
an empty stub (environment="live", nothing else). This implements it for
real against the official kiteconnect SDK -- the actual Zerodha broker
this project targets, which speaks a REST + WebSocket API, not FIX (see
this branch's own earlier review of the original 10-box proposal).

tenacity provides retry/backoff for Zerodha's documented rate limits (10
order placements/second, 3 modifications/second) -- transient network or
429-style failures get retried with exponential backoff; a genuine
rejection (bad symbol, insufficient margin) is NOT retried, since retrying
a rejection just resubmits the same invalid order.

NOT wired into calibration: calibration is an offline, historical exercise
against frozen CSV bars -- there is no live broker call anywhere in that
path, and this module is never imported by the calibration engine built in
this branch. It exists as real, tested infrastructure for a future live
milestone, gated the same way runtime/operating_mode.py's own
OperatingMode.LIVE already is (StartupGate fails closed without
live_trading_enabled=True, a signing key, and durable_db=True). No real
Zerodha credentials exist in this environment; tests here use a mocked
KiteConnect client to prove the retry/backoff and order-translation logic,
not real order placement.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException, NetworkException
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from runtime.operating_mode import BrokerAdapter

_SIDE_TO_TRANSACTION_TYPE = {"BUY": "BUY", "SELL": "SELL"}
_ORDER_TYPE_MAP = {"MARKET": "MARKET", "LIMIT": "LIMIT"}

# Only retry genuinely transient failures -- never a broker-side rejection
# (KiteException that isn't a NetworkException), since resubmitting a
# rejected order just repeats the same invalid request.
_retry_transient = retry(
    retry=retry_if_exception_type(NetworkException),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
    reraise=True,
)


class KiteConnectBrokerAdapter(BrokerAdapter):
    environment = "live"

    def __init__(self, api_key: str, access_token: str, account_id: Optional[str] = None) -> None:
        super().__init__(account_id=account_id)
        self.client = KiteConnect(api_key=api_key)
        self.client.set_access_token(access_token)

    @_retry_transient
    def place_order(
        self, symbol: str, side: str, quantity: int, order_type: str,
        limit_price: Optional[float] = None, exchange: str = "NSE", product: str = "MIS",
    ) -> Dict[str, Any]:
        if side not in _SIDE_TO_TRANSACTION_TYPE:
            return {"passed": False, "reason": f"invalid side: {side}"}
        if order_type not in _ORDER_TYPE_MAP:
            return {"passed": False, "reason": f"invalid order_type: {order_type}"}
        if quantity <= 0:
            return {"passed": False, "reason": "quantity must be positive"}

        kwargs: Dict[str, Any] = dict(
            variety=self.client.VARIETY_REGULAR,
            exchange=exchange, tradingsymbol=symbol,
            transaction_type=_SIDE_TO_TRANSACTION_TYPE[side],
            quantity=int(quantity), order_type=_ORDER_TYPE_MAP[order_type],
            product=product,
        )
        if order_type == "LIMIT":
            if limit_price is None or limit_price <= 0:
                return {"passed": False, "reason": "LIMIT order requires a positive limit_price"}
            kwargs["price"] = float(limit_price)

        try:
            order_id = self.client.place_order(**kwargs)
        except NetworkException:
            raise  # let tenacity retry
        except KiteException as exc:
            return {"passed": False, "reason": f"broker rejected order: {exc}"}
        return {"passed": True, "order_id": order_id}

    @_retry_transient
    def order_status(self, order_id: str) -> Dict[str, Any]:
        try:
            history = self.client.order_history(order_id)
        except NetworkException:
            raise
        except KiteException as exc:
            return {"passed": False, "reason": str(exc)}
        return {"passed": True, "history": history}
