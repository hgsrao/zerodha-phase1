"""Offline-only adapter with fill-time cost booking for the external engine."""
from revision2.transaction_costs import leg_cost
from runtime.operating_mode import PaperBrokerAdapter

class CostedPaperBrokerAdapter(PaperBrokerAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.booked_costs = 0.0
        self.cost_ledger = []
        self._conversion_receipts = {}
        self._contingent_protection = {}
        self._simulated_gtts = {}

    def place_order(self, symbol, side, quantity, order_type, market_price,
                    config=None, parameter_registry=None):
        if order_type != "MARKET":
            return {"passed": False, "reasons": ["Replay supports MARKET orders only"]}
        was_flat = self.get_position(symbol)["quantity"] == 0
        result = super().place_order(symbol, side, quantity, order_type, market_price,
                                     config, parameter_registry)
        if result["passed"]:
            # A new intraday position must not inherit a flattened position's CNC
            # product. Fills against an existing converted position retain CNC.
            if was_flat:
                self.positions[symbol]["product"] = "MIS"
            else:
                self.positions[symbol].setdefault("product", "MIS")
            cost = leg_cost(result["filled_price"], result["filled_quantity"], side)
            self.booked_costs += cost
            self.cost_ledger.append({"order_id": result["order_id"], "cost": cost})
            result["cost"] = cost
            # This adapter acknowledges synchronously in simulated event time.
            result["ack_elapsed_seconds"] = 0.0
        return result

    def request_product_conversion(self, request_id, symbol, quantity,
                                   from_product="MIS", to_product="CNC", timestamp=None):
        """Paper-only idempotent conversion; never a second fill or cost event."""
        import pandas as pd
        request = (symbol, int(quantity), from_product, to_product)
        previous = self._conversion_receipts.get(request_id)
        if previous:
            if tuple(previous["request"]) != request:
                raise ValueError("Conversion request identity changed")
            return dict(previous)
        position = self.get_position(symbol)
        passed = (from_product == "MIS" and to_product == "CNC"
                  and int(quantity) > 0 and position["quantity"] == int(quantity)
                  and position.get("product", "MIS") == from_product)
        if passed:
            position["product"] = to_product
        receipt = {"request_id": request_id, "request": list(request),
                   "status": "ACKNOWLEDGED" if passed else "REJECTED",
                   "product": position.get("product", "MIS"),
                   "quantity": int(position["quantity"]),
                   "timestamp": pd.Timestamp(timestamp).isoformat() if timestamp is not None else None,
                   "environment": "paper"}
        self._conversion_receipts[request_id] = receipt
        return dict(receipt)

    def conversion_receipt(self, request_id):
        receipt = self._conversion_receipts.get(request_id)
        return dict(receipt) if receipt else None

    def ensure_protection(self, symbol, stop_price, quantity, product="MIS"):
        """Track simulated contingent protection; orchestrator executes gap/bar stops.

        This is not an exchange order and offers no real overnight guarantee.
        """
        import math
        if not math.isfinite(float(stop_price)) or stop_price <= 0 or int(quantity) <= 0:
            raise ValueError("Invalid paper protection")
        position = self.get_position(symbol)
        if abs(position["quantity"]) != int(quantity) or position.get("product", "MIS") != product:
            raise ValueError("Paper protection does not match position")
        oid = "paper-protection-" + symbol
        self._contingent_protection[oid] = {
            "order_id": oid, "tradingsymbol": symbol, "exchange": "NSE",
            "product": product, "quantity": int(quantity), "pending_quantity": int(quantity),
            "filled_quantity": 0, "transaction_type": "SELL" if position["quantity"] > 0 else "BUY",
            "order_type": "SL-M", "status": "TRIGGER PENDING", "validity": "SIMULATED",
            "trigger_price": float(stop_price), "environment": "paper"}
        return oid

    def snapshot(self):
        """JSON-safe paper state and broker-truth-shaped reconciliation view."""
        from dataclasses import asdict
        import json
        orders = []
        serialized_orders = {}
        for oid, order in self.orders.items():
            row = asdict(order)
            row["state"] = order.state.value
            serialized_orders[oid] = row
            orders.append({"order_id": oid, "tradingsymbol": order.symbol, "exchange": "NSE",
                           "product": self.get_position(order.symbol).get("product", "MIS"),
                           "status": "COMPLETE" if order.filled_quantity == order.quantity else "REJECTED",
                           "quantity": order.quantity, "filled_quantity": order.filled_quantity})
        for oid, protection in self._contingent_protection.items():
            if self.get_position(protection["tradingsymbol"])["quantity"]:
                orders.append(dict(protection))
        payload = {"passed": True, "environment": "paper",
                   "positions": [{"tradingsymbol": symbol, "exchange": "NSE", **position,
                                  "product": position.get("product", "MIS")}
                                 for symbol, position in self.positions.items()],
                   "orders": orders, "holdings": [], "margins": {},
                   "gtts": list(self._simulated_gtts.values()),
                   "paper_state": {"positions": self.positions, "orders": serialized_orders,
                                   "fills": self.fills, "realized_pnl": self.realized_pnl,
                                   "booked_costs": self.booked_costs, "cost_ledger": self.cost_ledger,
                                   "conversions": self._conversion_receipts,
                                   "protection": self._contingent_protection,
                                   "gtts": self._simulated_gtts,
                                   "slippage_fraction": self.slippage_fraction}}
        return json.loads(json.dumps(payload))

    def restore_snapshot(self, snapshot):
        """Restore only explicitly identified paper state into an empty paper adapter."""
        import copy
        import math
        from runtime.operating_mode import PaperOrder, OrderState
        if self.orders or self.fills or self.positions:
            raise ValueError("Restore requires an empty paper broker")
        if snapshot.get("environment") != "paper" or not snapshot.get("passed"):
            raise ValueError("Not an authenticated paper snapshot")
        state = copy.deepcopy(snapshot["paper_state"])
        if state["slippage_fraction"] != self.slippage_fraction:
            raise ValueError("Paper fill model changed")
        for p in state["positions"].values():
            if not math.isfinite(float(p["avg_price"])) or int(p["quantity"]) != p["quantity"]:
                raise ValueError("Invalid paper position snapshot")
        orders = {}
        for oid, row in state["orders"].items():
            row["state"] = OrderState(row["state"])
            orders[oid] = PaperOrder(**row)
        self.positions = state["positions"]
        self.orders = orders
        self.fills = state["fills"]
        self.realized_pnl = state["realized_pnl"]
        self.booked_costs = state["booked_costs"]
        self.cost_ledger = state["cost_ledger"]
        self._conversion_receipts = state["conversions"]
        self._contingent_protection = state["protection"]
        self._simulated_gtts = state.get('gtts', {})

    def ensure_cnc_gtt(self, position_id, symbol, stop_price, quantity):
        """Simulated GTT boot request; no exchange-hosted guarantee."""
        from decimal import Decimal, ROUND_FLOOR
        if self.get_position(symbol).get('quantity') != quantity or self.get_position(symbol).get('product') != 'CNC':
            raise ValueError('Simulated GTT requires matching long CNC position')
        tick = Decimal('0.05')
        limit = float((Decimal(str(stop_price))*Decimal('0.995')/tick).to_integral_value(rounding=ROUND_FLOOR)*tick)
        oid = self.ensure_protection(symbol, stop_price, quantity, 'CNC')
        self._contingent_protection[oid]['validity'] = 'GTT_SIMULATED'
        receipt = dict(passed=True, verified=True, trigger_id='paper-gtt-'+position_id,
                       symbol=symbol, quantity=quantity, trigger_price=stop_price,
                       limit_price=limit, environment='paper', simulated=True)
        self._simulated_gtts[position_id] = receipt
        return dict(receipt)

    def get_gtts(self):
        return [dict(id=r['trigger_id'], status='active', type='single',
                     condition=dict(exchange='NSE', tradingsymbol=r['symbol'], trigger_values=[r['trigger_price']]),
                     orders=[dict(exchange='NSE', tradingsymbol=r['symbol'], product='CNC',
                                  transaction_type='SELL', quantity=r['quantity'],
                                  order_type='LIMIT', price=r['limit_price'])])
                for r in self._simulated_gtts.values()]

    def protection_tick_size(self, symbol):
        return 0.05  # explicitly simulated fixture convention, not live metadata


class ReplayIntentLedger:
    """Event-time deduplication, never wall-clock based."""
    def __init__(self, window_seconds):
        self.window_seconds = float(window_seconds)
        self._submitted = {}

    def seen_recent(self, symbol, side, timestamp):
        import pandas as pd
        previous = self._submitted.get((symbol, side))
        if previous is None:
            return False
        elapsed = (pd.Timestamp(timestamp) - previous).total_seconds()
        return elapsed <= self.window_seconds

    def record(self, symbol, side, timestamp):
        import pandas as pd
        self._submitted[(symbol, side)] = pd.Timestamp(timestamp)
