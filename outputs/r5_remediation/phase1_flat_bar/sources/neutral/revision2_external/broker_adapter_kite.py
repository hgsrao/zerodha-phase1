"""Mockable Kite adapter with conservative reconciliation after ambiguous writes.

Order acceptance is not a fill. Placement and conversion writes are never
blindly retried; read-only history lookups may retry network failures.
No adapter is instantiated or account contacted by calibration.
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

    def __init__(self, api_key: str = "", access_token: str = "", account_id: Optional[str] = None, *, client=None) -> None:
        super().__init__(account_id=account_id)
        self.client = client if client is not None else KiteConnect(api_key=api_key)
        if client is None:
            self.client.set_access_token(access_token)

    def positions(self):
        return self.client.positions()

    def orders(self):
        return self.client.orders()

    def holdings(self):
        return self.client.holdings()

    def margins(self):
        return self.client.margins()

    def snapshot(self):
        try:
            return {"passed": True, "positions": self.positions(), "orders": self.orders(),
                    "holdings": self.holdings(), "margins": self.margins()}
        except Exception as exc:
            return {"passed": False, "reason": str(exc)}

    def place_order(self, symbol: str, side: str, quantity: int, order_type: str,
                    limit_price: Optional[float] = None, exchange: str = "NSE", product: str = "MIS",
                    correlation_id: Optional[str] = None):
        if side not in _SIDE_TO_TRANSACTION_TYPE or order_type not in _ORDER_TYPE_MAP or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            return {"passed": False, "reason": "invalid order arguments"}
        kwargs = dict(variety=self.client.VARIETY_REGULAR, exchange=exchange, tradingsymbol=symbol,
                      transaction_type=side, quantity=int(quantity), order_type=order_type, product=product)
        if correlation_id:
            if not correlation_id.isalnum() or len(correlation_id) > 20:
                return {"passed": False, "reason": "correlation_id must be alphanumeric and at most 20 characters"}
            kwargs['tag'] = correlation_id
            try:
                matches = [o for o in self.orders() if o.get('tag') == correlation_id]
            except Exception as exc:
                return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": str(exc)}
            if matches:
                return self._correlated_receipt(matches, kwargs)
        if order_type == 'LIMIT':
            if limit_price is None or limit_price <= 0:
                return {"passed": False, "reason": "LIMIT order requires a positive limit_price"}
            kwargs['price'] = float(limit_price)
        try:
            oid = self.client.place_order(**kwargs)
            return {"passed": True, "accepted": True, "filled": False, "order_id": oid, "status": "ACCEPTED"}
        except (NetworkException, TimeoutError, ConnectionError) as exc:
            if correlation_id:
                try:
                    matches = [o for o in self.orders() if o.get('tag') == correlation_id]
                    if matches:
                        return self._correlated_receipt(matches, kwargs)
                except Exception:
                    pass
            return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": str(exc)}
        except KiteException as exc:
            return {"passed": False, "accepted": False, "reason": str(exc)}

    def _correlated_receipt(self, matches, request):
        fields = ('exchange', 'tradingsymbol', 'transaction_type', 'quantity', 'order_type', 'product')
        if len(matches) != 1 or any(str(matches[0].get(k)) != str(request[k]) for k in fields):
            return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": "correlation collision or duplicate orders"}
        o = matches[0]
        status = o.get('status', 'UNKNOWN')
        accepted = status in ('OPEN', 'TRIGGER PENDING', 'COMPLETE', 'PUT ORDER REQ RECEIVED', 'VALIDATION PENDING', 'OPEN PENDING', 'MODIFY PENDING', 'MODIFY VALIDATION PENDING', 'CANCEL PENDING', 'AMO REQ RECEIVED')
        return {"passed": accepted, "accepted": accepted, "filled": status == 'COMPLETE',
                "filled_quantity": int(o.get('filled_quantity', 0)), "order_id": o.get('order_id'),
                "status": status, "reconciled": True, "retry_allowed": False}

    def convert_position(self, symbol, quantity, old_product='MIS', new_product='CNC', exchange='NSE',
                         transaction_type='BUY', correlation_id=None):
        # Conversion is acknowledged only after broker product/quantity confirmation.
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or transaction_type not in ('BUY', 'SELL') or old_product == new_product:
            return {"passed": False, "product_confirmed": False, "retry_allowed": False, "reason": "invalid conversion arguments"}
        try:
            before = self.positions().get('net', [])
            signed = int(quantity) if transaction_type == 'BUY' else -int(quantity)
            old = sum(int(p.get('quantity', 0)) for p in before if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == old_product)
            new = sum(int(p.get('quantity', 0)) for p in before if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == new_product)
            if quantity <= 0 or old * signed <= 0 or abs(old) < quantity:
                return {"passed": False, "product_confirmed": False, "reason": "source position insufficient", "correlation_id": correlation_id}
            try:
                accepted = self.client.convert_position(exchange=exchange, tradingsymbol=symbol,
                    transaction_type=transaction_type, position_type='day', quantity=int(quantity),
                    old_product=old_product, new_product=new_product)
            except (NetworkException, TimeoutError, ConnectionError):
                accepted = None
            after = self.positions().get('net', [])
            aold = sum(int(p.get('quantity', 0)) for p in after if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == old_product)
            anew = sum(int(p.get('quantity', 0)) for p in after if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == new_product)
            confirmed = aold == old - signed and anew == new + signed
            return {"passed": confirmed, "accepted": accepted, "product_confirmed": confirmed,
                    "correlation_id": correlation_id, "retry_allowed": False,
                    "confirmed_product": new_product if confirmed else None, "confirmed_quantity": quantity if confirmed else None}
        except Exception as exc:
            return {"passed": False, "product_confirmed": False, "retry_allowed": False, "reason": str(exc), "correlation_id": correlation_id}

    @_retry_transient
    def order_status(self, order_id: str):
        try:
            history = self.client.order_history(order_id)
        except NetworkException:
            raise
        except KiteException as exc:
            return {"passed": False, "reason": str(exc)}
        latest = history[-1] if history else {}
        return {"passed": bool(history), "history": history, "status": latest.get('status'),
                "filled": latest.get('status') == 'COMPLETE', "filled_quantity": latest.get('filled_quantity', 0)}
