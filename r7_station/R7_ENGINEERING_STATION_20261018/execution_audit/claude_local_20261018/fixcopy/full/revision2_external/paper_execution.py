"""Offline-only adapter with fill-time cost booking for the external engine."""
from revision2.transaction_costs import leg_cost
from runtime.operating_mode import PaperBrokerAdapter

class CostedPaperBrokerAdapter(PaperBrokerAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.booked_costs = 0.0
        self.cost_ledger = []

    def place_order(self, symbol, side, quantity, order_type, market_price,
                    config=None, parameter_registry=None):
        if order_type != "MARKET":
            return {"passed": False, "reasons": ["Replay supports MARKET orders only"]}
        result = super().place_order(symbol, side, quantity, order_type, market_price,
                                     config, parameter_registry)
        if result["passed"]:
            cost = leg_cost(result["filled_price"], result["filled_quantity"], side)
            self.booked_costs += cost
            self.cost_ledger.append({"order_id": result["order_id"], "cost": cost})
            result["cost"] = cost
            # This adapter acknowledges synchronously in simulated event time.
            result["ack_elapsed_seconds"] = 0.0
        return result


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
