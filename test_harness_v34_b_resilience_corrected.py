from __future__ import annotations

import copy
import unittest
from decimal import Decimal
from datetime import date, datetime, timezone

from institutional_engine_v34 import (
    TradingEngineV34,
    Config,
    BotState,
    TradeContext,
)


class FaultInjectingMockBroker:
    """
    Advanced adversarial broker simulator for V3.4.0-B.1 Resilience Testing (B16-B24).
    Tracks precise call telemetry and supports multi-poll temporal fault injection, per-operation telemetry, and B23/B24 reset/isolation checks.
    """

    def __init__(self, positions=None, orders=None, order_details=None):
        self.positions = copy.deepcopy(positions or [])
        self.orders = copy.deepcopy(orders or [])
        self.order_details = copy.deepcopy(order_details or {})
        self.placed_orders = []
        self.cancelled_order_ids = set()

        # Telemetry and fault tracking
        self.fault_mode = None
        self.fault_call_count = 0
        self.fault_limit = 0
        self.malformed_payload = None
        
        self.total_calls = 0
        self.failed_calls = 0
        self.successful_calls = 0
        self.calls_by_operation = {}
        self.failed_calls_by_operation = {}
        self.successful_calls_by_operation = {}

    def _record_call(self, operation: str, success: bool):
        self.calls_by_operation[operation] = self.calls_by_operation.get(operation, 0) + 1
        bucket = self.successful_calls_by_operation if success else self.failed_calls_by_operation
        bucket[operation] = bucket.get(operation, 0) + 1
        if success:
            self.successful_calls += 1
        else:
            self.failed_calls += 1
        self.total_calls += 1

        self._ltp = {"NSE:RELIANCE": {"last_price": 1300.0}, "RELIANCE": {"last_price": 1300.0}}
        self._depth = {
            "NSE:RELIANCE": {
                "buy": [{"quantity": 100}],
                "sell": [{"quantity": 100}],
            }
        }

        for oid, details in self.order_details.items():
            details.setdefault("order_id", oid)
            self._upsert_order(details)

    def _upsert_order(self, order):
        oid = order["order_id"]
        self.orders = [o for o in self.orders if o.get("order_id") != oid]
        self.orders.append(copy.deepcopy(order))

    def get_positions(self):
        if self.fault_mode == "POSITION_TIMEOUT":
            self.fault_call_count += 1
            if self.fault_call_count <= self.fault_limit:
                self._record_call("get_positions", False)
                raise TimeoutError("Simulated network timeout during get_positions()")
        self._record_call("get_positions", True)
        return copy.deepcopy(self.positions)

    def get_orders(self):
        self._record_call("get_orders", True)
        return [
            copy.deepcopy(o)
            for o in self.orders
            if o.get("order_id") not in self.cancelled_order_ids
        ]

    def get_order_details(self, order_id):
        if self.fault_mode == "ORDER_TIMEOUT":
            self.fault_call_count += 1
            if self.fault_call_count <= self.fault_limit:
                self._record_call("get_order_details", False)
                raise TimeoutError(f"Simulated network timeout for order {order_id}")

        if self.fault_mode == "MALFORMED_RESPONSE":
            self._record_call("get_order_details", True)
            return self.malformed_payload

        if order_id not in self.order_details:
            self._record_call("get_order_details", False)
            raise KeyError(f"Unknown order_id: {order_id}")

        self._record_call("get_order_details", True)
        return copy.deepcopy(self.order_details[order_id])

    def place_order(self, **kwargs):
        order_id = f"ORD_RES_{len(self.placed_orders) + 1}"
        quantity = int(kwargs.get("quantity", 0) or 0)
        order_data = {
            "order_id": order_id,
            "tradingsymbol": kwargs.get("tradingsymbol"),
            "exchange": kwargs.get("exchange", "NSE"),
            "product": kwargs.get("product", "MIS"),
            "quantity": quantity,
            "filled_quantity": 0,
            "pending_quantity": quantity,
            "status": "OPEN",
            "tag": kwargs.get("tag"),
            "average_price": kwargs.get("price", 1300.0),
        }
        self.placed_orders.append(order_data)
        self.order_details[order_id] = order_data
        self._upsert_order(order_data)
        self._record_call("place_order", True)
        return order_id

    def cancel_order(self, order_id):
        if order_id in self.order_details:
            self.order_details[order_id]["status"] = "CANCELLED"
        self._record_call("cancel_order", True)

    def ltp(self, symbols):
        res = {}
        for symbol in symbols:
            key = symbol if ":" in symbol else f"NSE:{symbol}"
            if key in self._ltp:
                res[symbol] = copy.deepcopy(self._ltp[key])
        self._record_call("ltp", True)
        return res

    def get_market_depth(self, symbol):
        self._record_call("get_market_depth", True)
        return {"buy": [{"quantity": 100}], "sell": [{"quantity": 100}]}

    def inject_transient_timeout(self, operation: str, fail_count: int):
        if fail_count < 1:
            raise ValueError("fail_count must be >= 1")
        if operation not in {"ORDER_TIMEOUT", "POSITION_TIMEOUT"}:
            raise ValueError(f"Unsupported transient fault operation: {operation}")
        self.fault_mode = operation
        self.fault_call_count = 0
        self.fault_limit = fail_count

    def inject_malformed_data(self, payload):
        self.fault_mode = "MALFORMED_RESPONSE"
        self.malformed_payload = payload

    def clear_fault(self):
        """Clear all injected faults without altering broker state or telemetry."""
        self.fault_mode = None
        self.fault_call_count = 0
        self.fault_limit = 0
        self.malformed_payload = None


class MockStore:
    def __init__(self, state: BotState):
        self.state = copy.deepcopy(state)

    def load(self, today: date) -> BotState:
        return copy.deepcopy(self.state)

    def save(self, state: BotState):
        self.state = copy.deepcopy(state)


class MockClock:
    def now(self):
        return datetime(2026, 8, 10, 9, 15, 0, tzinfo=timezone.utc)


class MockTerminator:
    def __init__(self):
        self.halted = False
        self.reason = ""

    def halt(self, reason: str):
        self.halted = True
        self.reason = reason


class MockAudit:
    def log(self, event: str, **kwargs):
        pass


class MockAlert:
    def send(self, level: str, message: str):
        pass


class MockLock:
    def acquire(self):
        return "lock"
    def release(self):
        pass


class TestV340BResilienceGate(unittest.TestCase):

    def setUp(self):
        self.cfg = Config(
            alert_webhook_url="http://test",
            max_daily_loss=Decimal("300"),
        )

    def build_engine(self, state, broker):
        store = MockStore(state)
        terminator = MockTerminator()
        engine = TradingEngineV34(
            broker, MockClock(), None, store, MockAudit(), MockAlert(), MockLock(), terminator, self.cfg
        )
        return engine, terminator

    def test_reg_b16_transient_sl_polling_recovery(self):
        """B16 (Resilience): Multi-poll transient SL timeout recovers successfully across successive ticks."""
        ctx = TradeContext(symbol="RELIANCE", entry_tag="V3.4", target_qty=10, tranche_qty=10, filled_qty=10, avg_entry_price=Decimal("1300.0"), stop_order_id="SL16")
        state = BotState(trading_day="2026-08-10", status="MANAGING", active_trade=ctx)
        
        broker = FaultInjectingMockBroker(
            positions=[{"tradingsymbol": "RELIANCE", "quantity": 10, "exchange": "NSE", "product": "MIS"}],
            order_details={"SL16": {"status": "OPEN", "quantity": 10, "filled_quantity": 0, "tradingsymbol": "RELIANCE"}}
        )
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=1)

        engine, terminator = self.build_engine(state, broker)
        
        # Tick 1: Expect failure (will currently halt on un-resilient engine)
        engine.step()
        
        # Tick 2: Recovers after clearing fault
        broker.clear_fault()
        engine.step()

        self.assertFalse(terminator.halted, f"Engine halted unexpectedly: {terminator.reason}")
        self.assertEqual(engine.state.status, "MANAGING")
        self.assertEqual(broker.failed_calls_by_operation.get("get_order_details", 0), 1)
        self.assertGreaterEqual(
            broker.successful_calls_by_operation.get("get_order_details", 0), 1
        )

    def test_reg_b17_repeated_sl_polling_exhaustion(self):
        """B17 (Resilience): Persistent multi-poll SL timeout consumes budget step-by-step -> HARD HALT."""
        ctx = TradeContext(symbol="RELIANCE", entry_tag="V3.4", target_qty=10, tranche_qty=10, filled_qty=10, avg_entry_price=Decimal("1300.0"), stop_order_id="SL17")
        state = BotState(trading_day="2026-08-10", status="MANAGING", active_trade=ctx)
        
        broker = FaultInjectingMockBroker(
            positions=[{"tradingsymbol": "RELIANCE", "quantity": 10, "exchange": "NSE", "product": "MIS"}],
            order_details={"SL17": {"status": "OPEN", "quantity": 10, "filled_quantity": 0, "tradingsymbol": "RELIANCE"}}
        )
        # Budget = 3 failures tolerated; tick 4 halts
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=5)

        engine, terminator = self.build_engine(state, broker)
        
        # Tick 1: Tolerance budget active -> not halted yet
        engine.step()
        self.assertFalse(terminator.halted)

        # Tick 2: Tolerance budget active -> not halted yet
        engine.step()
        self.assertFalse(terminator.halted)

        # Tick 3: Tolerance budget active -> not halted yet
        engine.step()
        self.assertFalse(terminator.halted)

        # Tick 4: Budget exhausted -> HARD HALT triggered
        engine.step()
        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")

    def test_reg_b18_transient_position_query_recovery(self):
        """B18 (Resilience): Multi-poll position query timeout recovers on subsequent tick with telemetry check."""
        ctx = TradeContext(symbol="RELIANCE", entry_tag="V3.4", target_qty=10, tranche_qty=10, filled_qty=10, avg_entry_price=Decimal("1300.0"), stop_order_id="SL18")
        state = BotState(trading_day="2026-08-10", status="MANAGING", active_trade=ctx)
        
        broker = FaultInjectingMockBroker(
            positions=[{"tradingsymbol": "RELIANCE", "quantity": 10, "exchange": "NSE", "product": "MIS"}],
            order_details={"SL18": {"status": "OPEN", "quantity": 10, "filled_quantity": 0, "tradingsymbol": "RELIANCE"}}
        )
        broker.inject_transient_timeout("POSITION_TIMEOUT", fail_count=1)

        engine, terminator = self.build_engine(state, broker)
        engine.step() # Tick 1: fails
        
        broker.clear_fault() # Tick 2: recovers
        engine.step()

        self.assertFalse(terminator.halted, f"Engine halted unexpectedly: {terminator.reason}")
        self.assertEqual(engine.state.status, "MANAGING")
        self.assertEqual(broker.failed_calls_by_operation.get("get_positions", 0), 1)

    def test_reg_b19_exit_pending_timeout_recovery(self):
        """B19 (Resilience): Multi-poll EXIT_PENDING timeout recovers and completes exit."""
        ctx = TradeContext(symbol="RELIANCE", entry_tag="V3.4", target_qty=10, tranche_qty=10, filled_qty=10, exit_order_id="EXIT19")
        state = BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=ctx)
        
        broker = FaultInjectingMockBroker(
            positions=[],
            order_details={"EXIT19": {"status": "COMPLETE", "quantity": 10, "filled_quantity": 10, "tradingsymbol": "RELIANCE"}}
        )
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=1)

        engine, terminator = self.build_engine(state, broker)
        engine.step() # Tick 1: fails
        
        broker.clear_fault() # Tick 2: succeeds
        engine.step()

        self.assertFalse(terminator.halted, f"Engine halted unexpectedly: {terminator.reason}")
        self.assertEqual(engine.state.status, "STARTUP")
        self.assertEqual(broker.failed_calls_by_operation.get("get_order_details", 0), 1)

    def test_reg_b20_exit_pending_exhaustion(self):
        """B20 (Resilience): Persistent EXIT_PENDING timeout consumes budget step-by-step -> HARD HALT."""
        ctx = TradeContext(symbol="RELIANCE", entry_tag="V3.4", target_qty=10, tranche_qty=10, filled_qty=10, exit_order_id="EXIT20")
        state = BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=ctx)
        
        broker = FaultInjectingMockBroker(
            positions=[{"tradingsymbol": "RELIANCE", "quantity": 10, "exchange": "NSE", "product": "MIS"}],
            order_details={"EXIT20": {"status": "OPEN", "quantity": 10, "filled_quantity": 0, "tradingsymbol": "RELIANCE"}}
        )
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=5)

        engine, terminator = self.build_engine(state, broker)
        
        # Step-by-step budget consumption assertions
        engine.step()
        self.assertFalse(terminator.halted)
        engine.step()
        self.assertFalse(terminator.halted)
        engine.step()
        self.assertFalse(terminator.halted)
        
        engine.step()
        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")

    def test_reg_b21_malformed_response_immediate_halt(self):
        """B21 (Resilience): Malformed broker response halts immediately on contact (0 retries)."""
        ctx = TradeContext(symbol="RELIANCE", entry_tag="V3.4", target_qty=10, tranche_qty=10, filled_qty=10, avg_entry_price=Decimal("1300.0"), stop_order_id="SL21")
        state = BotState(trading_day="2026-08-10", status="MANAGING", active_trade=ctx)
        
        broker = FaultInjectingMockBroker(
            positions=[{"tradingsymbol": "RELIANCE", "quantity": 10, "exchange": "NSE", "product": "MIS"}],
            order_details={"SL21": {"status": "OPEN", "quantity": 10, "filled_quantity": "banana"}}
        )
        broker.inject_malformed_data({"status": "OPEN", "quantity": 10, "filled_quantity": "banana", "tradingsymbol": "RELIANCE"})

        engine, terminator = self.build_engine(state, broker)
        engine.step()

        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")
        self.assertEqual(broker.failed_calls, 0)
        self.assertEqual(
            broker.successful_calls_by_operation.get("get_order_details", 0), 1,
            "Malformed payload must have been delivered successfully by the broker simulator",
        )

    def test_reg_b22_definitive_broker_anomaly_immediate_halt(self):
        """B22 (Resilience): Definitive reality breach halts immediately on contact (0 retries)."""
        ctx = TradeContext(symbol="RELIANCE", entry_tag="V3.4", target_qty=5, tranche_qty=5, filled_qty=5, avg_entry_price=Decimal("1300.0"), stop_order_id="SL22")
        state = BotState(trading_day="2026-08-10", status="MANAGING", active_trade=ctx)
        
        broker = FaultInjectingMockBroker(
            positions=[{"tradingsymbol": "RELIANCE", "quantity": 5, "exchange": "NSE", "product": "MIS"}],
            order_details={"SL22": {"status": "OPEN", "quantity": 5, "filled_quantity": 3, "tradingsymbol": "RELIANCE"}}
        )

        engine, terminator = self.build_engine(state, broker)
        engine.step()

        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")

    def test_reg_b23_recovery_counter_reset(self):
        """
        B23: A successful observation resets consecutive transient failures.

        Contract:
          timeout #1 -> tolerate
          timeout #2 -> tolerate
          success    -> reset consecutive budget
          timeout #1 -> tolerate again

        The test is intentionally black-box: it does not access private engine
        attributes. The observable proof is that the post-recovery timeout is
        treated as the first failure of a fresh sequence.
        """
        ctx = TradeContext(
            symbol="RELIANCE",
            entry_tag="V3.4",
            target_qty=10,
            tranche_qty=10,
            filled_qty=10,
            avg_entry_price=Decimal("1300.0"),
            stop_order_id="SL23",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="MANAGING",
            active_trade=ctx,
        )

        broker = FaultInjectingMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "SL23": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        engine, terminator = self.build_engine(state, broker)

        # Failures 1 and 2: must be tolerated by the B.1 implementation.
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=2)
        engine.step()
        self.assertFalse(terminator.halted)
        engine.step()
        self.assertFalse(terminator.halted)

        # Successful observation must reset the consecutive-failure sequence.
        broker.clear_fault()
        engine.step()
        self.assertFalse(terminator.halted)

        # A new timeout after recovery is failure #1, not failure #3.
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=1)
        engine.step()
        self.assertFalse(
            terminator.halted,
            f"Counter was not reset after successful observation: {terminator.reason}",
        )

        self.assertEqual(
            broker.failed_calls_by_operation.get("get_order_details", 0), 3
        )

    def test_reg_b24_state_isolation(self):
        """
        B24: Observation-failure budget is isolated by state/operation.

        Contract:
          MANAGING accumulates two transient failures.
          A successful state transition into EXIT_PENDING must not carry those
          two failures into the EXIT_PENDING observation budget.
          The first EXIT_PENDING timeout is therefore tolerated.
        """
        ctx = TradeContext(
            symbol="RELIANCE",
            entry_tag="V3.4",
            target_qty=10,
            tranche_qty=10,
            filled_qty=10,
            avg_entry_price=Decimal("1300.0"),
            stop_order_id="SL24",
            exit_order_id="EXIT24",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="MANAGING",
            active_trade=ctx,
        )

        broker = FaultInjectingMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "SL24": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "tradingsymbol": "RELIANCE",
                },
                "EXIT24": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "tradingsymbol": "RELIANCE",
                },
            },
        )
        engine, terminator = self.build_engine(state, broker)

        # Accumulate two MANAGING failures.
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=2)
        engine.step()
        self.assertFalse(terminator.halted)
        engine.step()
        self.assertFalse(terminator.halted)

        # Move to EXIT_PENDING with an otherwise valid exit observation path.
        broker.clear_fault()
        engine.state.status = "EXIT_PENDING"
        engine.state.active_trade.exit_order_id = "EXIT24"

        # First EXIT_PENDING timeout must start a fresh budget.
        broker.inject_transient_timeout("ORDER_TIMEOUT", fail_count=1)
        engine.step()

        self.assertFalse(
            terminator.halted,
            f"Previous MANAGING failures contaminated EXIT_PENDING budget: {terminator.reason}",
        )
        self.assertEqual(
            broker.failed_calls_by_operation.get("get_order_details", 0), 3
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)