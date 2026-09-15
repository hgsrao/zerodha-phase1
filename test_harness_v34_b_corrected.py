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


class StatefulMockBroker:
    """
    Stateful broker simulator for V3.4.0-B.

    Design goals:
    - Broker-like order lifecycle: OPEN / COMPLETE / CANCELLED / REJECTED.
    - Partial fills are represented by quantities, not a fabricated PARTIAL status.
    - Multi-tick fill sequences are cumulative.
    - Position quantities change as simulated fills occur.
    - Cancellation races and SL-trigger races can be explicitly injected.
    - get_positions(), get_orders(), get_order_details(), ltp(), and
      get_market_depth() match the V3.4 broker-adapter contract.
    """

    TERMINAL_STATUSES = {"COMPLETE", "CANCELLED", "REJECTED"}

    def __init__(self, positions=None, orders=None, order_details=None):
        self.positions = copy.deepcopy(positions or [])
        self.orders = copy.deepcopy(orders or [])
        self.order_details = copy.deepcopy(order_details or {})
        self.placed_orders = []
        self.cancelled_order_ids = set()
        self._poll_count = {}

        # Explicit simulation controls.
        self._fill_sequences = {}
        self._fill_effects = {}
        self._cancel_races = {}
        self._ltp = {"NSE:RELIANCE": {"last_price": 1300.0}}
        self._depth = {
            "NSE:RELIANCE": {
                "buy": [{"quantity": 100}],
                "sell": [{"quantity": 100}],
            }
        }

        # Normalize supplied order-detail fixtures into the broker's order book.
        for oid, details in self.order_details.items():
            details.setdefault("order_id", oid)
            self._upsert_order(details)

    # ---------- Broker interface ----------

    def get_positions(self):
        return copy.deepcopy(self.positions)

    def get_orders(self):
        return [
            copy.deepcopy(o)
            for o in self.orders
            if o.get("order_id") not in self.cancelled_order_ids
        ]

    def get_order_details(self, order_id):
        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")

        details = self.order_details[order_id]

        # A cancellation race can resolve to COMPLETE before cancellation
        # becomes effective. This is deliberately deterministic.
        if self._cancel_races.get(order_id) == "COMPLETE":
            details["status"] = "COMPLETE"
            self._cancel_races.pop(order_id, None)

        sequence = self._fill_sequences.get(order_id)
        if sequence is not None:
            idx = self._poll_count.get(order_id, 0)
            fill = sequence[min(idx, len(sequence) - 1)]
            self._poll_count[order_id] = idx + 1
            self._apply_cumulative_fill(order_id, fill)

        elif details.get("status") == "COMPLETE":
            # Keep the broker payload internally consistent.
            details["filled_quantity"] = int(
                details.get("filled_quantity", details.get("quantity", 0))
            )

        details["pending_quantity"] = max(
            0,
            int(details.get("quantity", 0))
            - int(details.get("filled_quantity", 0)),
        )

        return copy.deepcopy(details)

    def place_order(self, **kwargs):
        """
        New orders begin OPEN with zero fills.

        This is deliberate: the simulator must not manufacture an immediate
        fill merely because place_order() was called.
        """
        order_id = f"ORD_AUTO_{len(self.placed_orders) + 1}"
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
            "transaction_type": kwargs.get("transaction_type"),
            "order_type": kwargs.get("order_type"),
        }

        self.placed_orders.append(order_data)
        self.order_details[order_id] = order_data
        self._upsert_order(order_data)
        return order_id

    def cancel_order(self, order_id):
        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")

        # If a race has been explicitly armed, leave the order available for
        # the next status poll; the poll will resolve it to COMPLETE.
        if self._cancel_races.get(order_id) == "COMPLETE":
            return

        self.order_details[order_id]["status"] = "CANCELLED"
        self.order_details[order_id]["pending_quantity"] = 0
        self.cancelled_order_ids.add(order_id)
        self._upsert_order(self.order_details[order_id])

    def ltp(self, symbols):
        return {
            symbol: copy.deepcopy(self._ltp[symbol])
            for symbol in symbols
            if symbol in self._ltp
        }

    def get_market_depth(self, symbol):
        key = symbol if ":" in symbol else f"NSE:{symbol}"
        return copy.deepcopy(self._depth.get(key, {"buy": [], "sell": []}))

    # ---------- Stateful simulation helpers ----------

    def simulate_partial_fill(self, order_id, fill_sequence, *, side=None):
        """
        Install a cumulative fill sequence such as [3, 3, 3, 5].

        The sequence is cumulative. Repeated 3s therefore mean "still 3
        filled", not "add another 3".
        """
        if not fill_sequence:
            raise ValueError("fill_sequence cannot be empty")
        if any(int(x) < 0 for x in fill_sequence):
            raise ValueError("fill quantities cannot be negative")
        if any(
            int(fill_sequence[i]) > int(fill_sequence[i + 1])
            for i in range(len(fill_sequence) - 1)
        ):
            raise ValueError("fill_sequence must be monotonically non-decreasing")

        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")

        self._fill_sequences[order_id] = [int(x) for x in fill_sequence]
        if side is not None:
            self.order_details[order_id]["transaction_type"] = side
        self._poll_count[order_id] = 0

    def simulate_rejection(self, order_id):
        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")
        self.order_details[order_id]["status"] = "REJECTED"
        self.order_details[order_id]["pending_quantity"] = 0
        self._upsert_order(self.order_details[order_id])

    def simulate_sl_trigger(self, order_id, fill_sequence=None):
        """
        Arm a protective-stop trigger.

        For each cumulative SL fill, the corresponding long position is
        reduced by the cumulative delta. This lets tests distinguish broker
        position reality from local fill accounting.
        """
        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")

        order = self.order_details[order_id]
        order["transaction_type"] = "SELL"

        if fill_sequence is None:
            fill_sequence = [int(order.get("quantity", 0))]

        self.simulate_partial_fill(order_id, fill_sequence, side="SELL")
        self._fill_effects[order_id] = "SELL_POSITION"

    def simulate_exit_fill(self, order_id, fill_sequence):
        """Arm cumulative fills for a market EXIT order."""
        self.simulate_partial_fill(order_id, fill_sequence, side="SELL")
        self._fill_effects[order_id] = "SELL_POSITION"

    def simulate_sl_cancellation_race(self, order_id):
        """
        Cancellation is requested, but the exchange completes the SL during
        the cancellation window.
        """
        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")
        self._cancel_races[order_id] = "COMPLETE"

    def simulate_complete_with_residual(self, order_id, filled_quantity):
        """
        Create the specific anomaly required by REG-B11:
        the broker reports SL COMPLETE while an unliquidated residual
        position remains.
        """
        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")

        order = self.order_details[order_id]
        target = int(order.get("quantity", 0))
        filled_quantity = int(filled_quantity)

        if not 0 < filled_quantity < target:
            raise ValueError("filled_quantity must be between 1 and quantity-1")

        # Apply the economic effect exactly once, then intentionally mark the
        # order COMPLETE to model the broker anomaly under test.
        self._fill_sequences.pop(order_id, None)
        self._poll_count.pop(order_id, None)

        previous = int(order.get("filled_quantity", 0))
        delta = filled_quantity - previous
        if delta < 0:
            raise ValueError("filled_quantity cannot move backwards")

        order["filled_quantity"] = filled_quantity
        order["pending_quantity"] = target - filled_quantity
        order["status"] = "COMPLETE"

        if delta:
            self._reduce_position(order, delta)

        self._upsert_order(order)

    # ---------- Internal broker bookkeeping ----------

    def _upsert_order(self, order):
        oid = order["order_id"]
        self.orders = [o for o in self.orders if o.get("order_id") != oid]
        self.orders.append(copy.deepcopy(order))

    def _apply_cumulative_fill(self, order_id, cumulative_fill):
        order = self.order_details[order_id]
        previous = int(order.get("filled_quantity", 0))
        target = int(order.get("quantity", order.get("target_qty", 0)))

        cumulative_fill = min(int(cumulative_fill), target)
        delta = cumulative_fill - previous
        if delta < 0:
            raise AssertionError(
                f"Broker simulator received decreasing cumulative fill for {order_id}"
            )

        order["filled_quantity"] = cumulative_fill
        order["pending_quantity"] = max(0, target - cumulative_fill)

        if cumulative_fill == target:
            order["status"] = "COMPLETE"
        elif cumulative_fill > 0:
            # Kite-style semantics: partial execution is represented by
            # quantities while the order remains OPEN.
            order["status"] = "OPEN"
        else:
            order["status"] = "OPEN"

        if delta and self._fill_effects.get(order_id) == "SELL_POSITION":
            self._reduce_position(order, delta)

        self._upsert_order(order)

    def _reduce_position(self, order, delta):
        symbol = order.get("tradingsymbol")
        exchange = order.get("exchange", "NSE")
        product = order.get("product", "MIS")

        for position in self.positions:
            if (
                position.get("tradingsymbol") == symbol
                and position.get("exchange") == exchange
                and position.get("product") == product
            ):
                current = int(position.get("quantity", 0))
                new_qty = current - int(delta)
                if new_qty < 0:
                    raise AssertionError(
                        f"Simulated sell exceeded broker position for {symbol}"
                    )
                position["quantity"] = new_qty
                return

    def set_ltp(self, symbol, price):
        key = symbol if ":" in symbol else f"NSE:{symbol}"
        self._ltp[key] = {"last_price": float(price)}

    def set_depth(self, symbol, buy_qty, sell_qty):
        key = symbol if ":" in symbol else f"NSE:{symbol}"
        self._depth[key] = {
            "buy": [{"quantity": int(buy_qty)}],
            "sell": [{"quantity": int(sell_qty)}],
        }


class MockStore:
    def __init__(self, state: BotState):
        self.state = copy.deepcopy(state)
        self.save_count = 0

    def load(self, today: date) -> BotState:
        return copy.deepcopy(self.state)

    def save(self, state: BotState):
        self.state = copy.deepcopy(state)
        self.save_count += 1


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
    def __init__(self):
        self.events = []

    def log(self, event: str, **kwargs):
        self.events.append((event, kwargs))


class MockAlert:
    def __init__(self):
        self.events = []

    def send(self, level: str, message: str):
        self.events.append((level, message))


class MockLock:
    def acquire(self):
        return "lock"

    def release(self):
        pass


class TestV340BManagementLifecycle(unittest.TestCase):

    def setUp(self):
        self.cfg = Config(
            alert_webhook_url="http://test",
            max_daily_loss=Decimal("300"),
        )

    def build_engine(self, state, broker):
        store = MockStore(state)
        terminator = MockTerminator()
        audit = MockAudit()
        alert = MockAlert()
        engine = TradingEngineV34(
            broker,
            MockClock(),
            None,
            store,
            audit,
            alert,
            MockLock(),
            terminator,
            self.cfg,
        )
        return engine, store, terminator, audit, alert

    def test_reg_b01_zero_fill_entry_persistence(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=0,
            entry_order_id="ORD123", order_status="PENDING"
        )
        state = BotState(
            trading_day="2026-08-10",
            status="ENTRY_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            order_details={
                "ORD123": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                }
            }
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        res = engine.step()

        self.assertEqual(res, "NO_ACTION")
        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "ENTRY_PENDING")

    def test_reg_b02_partial_entry_persistence(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=0,
            entry_order_id="ORD123", order_status="PENDING"
        )
        state = BotState(
            trading_day="2026-08-10",
            status="ENTRY_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            order_details={
                "ORD123": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "average_price": 1300.0,
                }
            }
        )
        broker.simulate_partial_fill("ORD123", [4])

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        res = engine.step()

        self.assertEqual(res, "STATE_CHANGED")
        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "PARTIAL_POSITION")
        self.assertEqual(ctx.filled_qty, 4)

    def test_reg_b03_complete_entry_requires_position(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=0,
            entry_order_id="ORD123", order_status="PENDING"
        )
        state = BotState(
            trading_day="2026-08-10",
            status="ENTRY_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            order_details={
                "ORD123": {
                    "status": "COMPLETE",
                    "quantity": 10,
                    "filled_quantity": 10,
                    "pending_quantity": 0,
                    "average_price": 1300.0,
                }
            }
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        res = engine.step()

        self.assertEqual(res, "HALTED")
        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")

    def test_reg_b04_missing_entry_average_price(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=0,
            entry_order_id="ORD123", order_status="PENDING"
        )
        state = BotState(
            trading_day="2026-08-10",
            status="ENTRY_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
                "average_price": 0,
            }],
            order_details={
                "ORD123": {
                    "status": "COMPLETE",
                    "quantity": 10,
                    "filled_quantity": 10,
                    "pending_quantity": 0,
                    "average_price": 0,
                }
            },
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        res = engine.step()

        self.assertEqual(res, "HALTED")
        self.assertTrue(terminator.halted)

    def test_reg_b05_missing_protective_stop(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            avg_entry_price=Decimal("1300.0"),
        )
        state = BotState(
            trading_day="2026-08-10",
            status="PROTECTION",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
                "average_price": 1300.0,
            }],
            orders=[],
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        engine.step()

        self.assertTrue(
            terminator.halted or engine.state.status == "RECONCILIATION_HALT"
        )

    def test_reg_b06_protective_sl_quantity_mismatch(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            avg_entry_price=Decimal("1300.0"), stop_order_id="SL123"
        )
        state = BotState(
            trading_day="2026-08-10",
            status="PROTECTION_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
                "average_price": 1300.0,
            }],
            order_details={
                "SL123": {
                    "status": "OPEN",
                    "quantity": 5,
                    "filled_quantity": 0,
                    "pending_quantity": 5,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        res = engine.step()

        self.assertEqual(res, "HALTED")
        self.assertTrue(terminator.halted)

    def test_reg_b07_valid_position_and_sl_enters_managing(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            avg_entry_price=Decimal("1300.0"), stop_order_id="SL123"
        )
        state = BotState(
            trading_day="2026-08-10",
            status="PROTECTION_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
                "average_price": 1300.0,
            }],
            order_details={
                "SL123": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        res = engine.step()

        self.assertFalse(terminator.halted)
        self.assertEqual(res, "STATE_CHANGED")
        self.assertEqual(engine.state.status, "MANAGING")

    def test_reg_b08_partial_sl_fill_cumulative_stability(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=5, tranche_qty=5, filled_qty=5,
            avg_entry_price=Decimal("1300.0"),
            stop_order_id="SL_PARTIAL",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="MANAGING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 5,
                "exchange": "NSE",
                "product": "MIS",
                "average_price": 1300.0,
            }],
            order_details={
                "SL_PARTIAL": {
                    "status": "OPEN",
                    "quantity": 5,
                    "filled_quantity": 0,
                    "pending_quantity": 5,
                    "average_price": 1290.0,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        broker.simulate_sl_trigger("SL_PARTIAL", [3, 3, 3, 5])

        engine, _, terminator, _, _ = self.build_engine(state, broker)

        engine.step()
        self.assertFalse(terminator.halted)
        self.assertEqual(ctx.filled_qty, 3)

        engine.step()
        self.assertEqual(ctx.filled_qty, 3)

        engine.step()
        self.assertEqual(ctx.filled_qty, 3)

        engine.step()
        self.assertEqual(ctx.filled_qty, 5)

    def test_reg_b09_repeated_identical_fills_stability(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            avg_entry_price=Decimal("1300.0"),
            stop_order_id="SL123",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="MANAGING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
                "average_price": 1300.0,
            }],
            order_details={
                "SL123": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        broker.simulate_sl_trigger("SL123", [3, 3, 3])

        engine, store, terminator, _, _ = self.build_engine(state, broker)

        engine.step()
        saves_after_first = store.save_count
        engine.step()
        engine.step()

        self.assertFalse(terminator.halted)
        self.assertEqual(ctx.filled_qty, 3)
        self.assertGreaterEqual(store.save_count, saves_after_first)

    def test_reg_b10_sl_complete_routes_to_startup(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            stop_order_id="SL123",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_CANCEL_SL",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "SL123": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        broker.simulate_sl_trigger("SL123", [10])

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        engine.step()

        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "STARTUP")

    def test_reg_b11_sl_complete_residual_position_halts(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            stop_order_id="SL123",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_CANCEL_SL",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "SL123": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        broker.simulate_complete_with_residual("SL123", 7)

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        engine.step()

        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")

    def test_reg_b12_sl_cancellation_race_condition(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            stop_order_id="SL_RACE",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_CANCEL_SL",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "SL_RACE": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        broker.simulate_sl_cancellation_race("SL_RACE")
        broker.simulate_sl_trigger("SL_RACE", [10])

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        engine.step()

        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "STARTUP")

    def test_reg_b13_sl_cancellation_confirmed(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            stop_order_id="SL_CANCEL",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_CANCEL_SL",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "SL_CANCEL": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tag": "V3.3_SL",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        broker.cancel_order("SL_CANCEL")

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        engine.step()

        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "EXIT_SUBMIT")

    def test_reg_b14_exit_pending_partial_fill(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            exit_order_id="EXIT_PARTIAL",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "EXIT_PARTIAL": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )
        broker.simulate_exit_fill("EXIT_PARTIAL", [6])

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        res = engine.step()

        self.assertEqual(res, "NO_ACTION")
        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "EXIT_PENDING")

    def test_reg_b15_crash_restart_durability_protection(self):
        ctx = TradeContext(
            symbol="RELIANCE", entry_tag="V3.4_ENTRY",
            target_qty=10, tranche_qty=10, filled_qty=10,
            exit_order_id="EXIT_CRASH",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_PENDING",
            active_trade=ctx,
        )
        broker = StatefulMockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "exchange": "NSE",
                "product": "MIS",
            }],
            order_details={
                "EXIT_CRASH": {
                    "status": "OPEN",
                    "quantity": 10,
                    "filled_quantity": 0,
                    "pending_quantity": 10,
                    "exchange": "NSE",
                    "product": "MIS",
                    "tradingsymbol": "RELIANCE",
                }
            },
        )

        # Persist through the store boundary, then construct a fresh engine.
        store = MockStore(state)
        persisted_state = store.load(date(2026, 8, 10))

        terminator = MockTerminator()
        engine = TradingEngineV34(
            broker,
            MockClock(),
            None,
            store,
            MockAudit(),
            MockAlert(),
            MockLock(),
            terminator,
            self.cfg,
        )

        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "EXIT_PENDING")
        self.assertEqual(
            engine.state.active_trade.exit_order_id,
            persisted_state.active_trade.exit_order_id,
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
