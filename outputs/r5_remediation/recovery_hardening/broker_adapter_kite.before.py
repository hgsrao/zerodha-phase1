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

    def __init__(self, api_key: str = "", access_token: str = "", account_id: Optional[str] = None, *, client=None, recovery_journal=None) -> None:
        super().__init__(account_id=account_id)
        if recovery_journal is not None and not self.account_id:
            raise ValueError('Durable conversion requires an explicit account identity')
        self.recovery_journal = recovery_journal
        self.client = client if client is not None else KiteConnect(api_key=api_key)
        if client is None:
            self.client.set_access_token(access_token)

    def positions(self):
        return self.client.positions()

    def get_gtts(self):
        return self.client.get_gtts()

    def verify_account_identity(self, account_id):
        return self.account_id == account_id and self.client.profile().get('user_id') == account_id

    def protection_tick_size(self, symbol):
        rows = [r for r in self.client.instruments('NSE') if r.get('tradingsymbol') == symbol]
        if len(rows) != 1:
            raise ValueError('Instrument tick metadata missing or ambiguous')
        return rows[0]['tick_size']

    def ensure_cnc_gtt(self, position_id, symbol, stop_price, quantity, *, tick_size=None):
        """Durable single-leg SELL protection, verified by broker readback.

        A limit order remains subject to gaps, rejection and partial execution.
        Unknown submissions are never repeated automatically.
        """
        import math
        from decimal import Decimal, ROUND_FLOOR
        if self.recovery_journal is None or not self.account_id:
            raise ValueError('GTT recovery requires an account-scoped durable journal')
        if not self.verify_account_identity(self.account_id):
            raise ValueError('Broker session account differs from durable account')
        if type(quantity) is not int or quantity <= 0 or not isinstance(position_id, str) or not position_id:
            raise ValueError('Invalid GTT identity/quantity')
        if tick_size is None:
            tick_size = self.protection_tick_size(symbol)
        if any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) <= 0 for v in (stop_price,tick_size)):
            raise ValueError('Invalid GTT stop/tick')
        tick = Decimal(str(tick_size))
        trigger = Decimal(str(stop_price))
        if trigger % tick != 0:
            raise ValueError('Governor stop is not aligned to instrument tick size')
        limit = float((trigger*Decimal('0.995')/tick).to_integral_value(rounding=ROUND_FLOOR)*tick)
        if limit <= 0:
            raise ValueError('Invalid GTT limit price')
        order = dict(exchange='NSE', tradingsymbol=symbol, transaction_type='SELL',
                     quantity=quantity, order_type='LIMIT', product='CNC', price=limit)
        def matches(row):
            condition = row.get('condition', {})
            orders = row.get('orders', [])
            return (row.get('status') == 'active' and row.get('type') == 'single'
                    and condition.get('exchange') == 'NSE' and condition.get('tradingsymbol') == symbol
                    and condition.get('trigger_values') == [float(stop_price)]
                    and len(orders) == 1 and all(orders[0].get(k) == v for k,v in order.items()))
        arguments = dict(account_id=self.account_id, position_id=position_id, order=order,
                         stop_price=float(stop_price), tick_size=float(tick_size))
        request_id = f'gtt:{position_id}:{quantity}:{float(stop_price)}'
        prior = self.recovery_journal.reserve_protection(request_id, arguments)
        if prior is not None:
            if not prior.get('passed'):
                return dict(prior, verified=False)
            row = self.client.get_gtt(prior['trigger_id'])
            return dict(prior, verified=matches(row), passed=matches(row))
        try:
            # Do not duplicate a matching active trigger or silently replace a conflict.
            active = [r for r in self.get_gtts() if r.get('status') == 'active'
                      and r.get('condition', {}).get('tradingsymbol') == symbol]
            if active:
                if len(active) != 1 or not matches(active[0]):
                    raise ValueError('Conflicting active GTT requires reconciliation')
                trigger_id = active[0]['id']
            else:
                quote = self.client.ltp('NSE:'+symbol)
                last_price = float(quote['NSE:'+symbol]['last_price'])
                if not math.isfinite(last_price) or last_price <= stop_price:
                    raise ValueError('Price already at/below stop; protective execution required')
                response = self.client.place_gtt(trigger_type='single', tradingsymbol=symbol,
                                               exchange='NSE', trigger_values=[float(stop_price)],
                                               last_price=last_price, orders=[order])
                trigger_id = response['trigger_id']
            row = self.client.get_gtt(trigger_id)
            result = dict(passed=matches(row), verified=matches(row), trigger_id=trigger_id,
                          symbol=symbol, quantity=quantity, trigger_price=float(stop_price),
                          limit_price=limit, retry_allowed=False, environment='live')
            self.recovery_journal.finish_protection(request_id, result)
            return result
        except Exception as exc:
            # A write may have succeeded. Keep the unresolved durable intent intact.
            return dict(passed=False, verified=False, ambiguous=True, retry_allowed=False,
                        reason=str(exc))

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
        arguments = dict(symbol=symbol, quantity=quantity, old_product=old_product,
                         new_product=new_product, exchange=exchange,
                         transaction_type=transaction_type, account_id=self.account_id)
        if self.recovery_journal is not None:
            prior = self.recovery_journal.reserve_conversion(correlation_id, arguments)
            if prior is not None:
                return prior
        result = self._convert_position_once(symbol, quantity, old_product, new_product,
                                             exchange, transaction_type, correlation_id)
        if self.recovery_journal is not None:
            self.recovery_journal.finish_conversion(correlation_id, result)
        return result

    def _convert_position_once(self, symbol, quantity, old_product, new_product, exchange,
                               transaction_type, correlation_id):
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
