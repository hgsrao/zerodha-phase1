from __future__ import annotations

import unittest
from decimal import Decimal
from datetime import date

from institutional_engine_v34 import (
    TradingEngineV34,
    Config,
    BotState,
    TradeContext,
)


class MockBroker:
    def __init__(self, positions=None, orders=None, order_details=None):
        self.positions = positions if positions is not None else []
        self.orders = orders if orders is not None else []
        self.order_details = order_details if order_details is not None else {}
        self.get_positions_calls = 0
        self.get_orders_calls = 0
        self.get_order_details_calls = 0

    def get_positions(self):
        self.get_positions_calls += 1
        return self.positions

    def get_orders(self):
        self.get_orders_calls += 1
        return self.orders

    def get_order_details(self, order_id):
        self.get_order_details_calls += 1
        if order_id not in self.order_details:
            raise KeyError(f"Unknown order_id: {order_id}")
        return self.order_details[order_id]


class MockStore:
    def __init__(self, state: BotState):
        self.state = state
        self.save_count = 0

    def load(self, today: date) -> BotState:
        return self.state

    def save(self, state: BotState):
        self.state = state
        self.save_count += 1


class MockClock:
    def now(self):
        from datetime import datetime, timezone
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
        self.calls = []

    def send(self, level: str, message: str):
        self.calls.append((level, message))


class MockLock:
    def acquire(self):
        return "lock"

    def release(self):
        pass


class TestV340RegressionSuite(unittest.TestCase):

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

    def test_reg_01_persisted_halt_survives_restart(self):
        """REG-01: Persisted HALT survives restart with zero broker queries."""
        state = BotState(
            trading_day="2026-08-10",
            status="RECONCILIATION_HALT",
            clearance_required=True,
            halt_reason="Previous fatal error",
        )
        broker = MockBroker()

        engine, store, terminator, _, _ = self.build_engine(state, broker)

        self.assertTrue(terminator.halted)
        self.assertEqual(broker.get_positions_calls, 0)
        self.assertEqual(broker.get_orders_calls, 0)
        self.assertEqual(broker.get_order_details_calls, 0)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")
        self.assertTrue(engine.state.clearance_required)
        self.assertEqual(engine.step(), "HALTED")

    def test_reg_02_ghost_entry_pending_halts(self):
        """REG-02: Pending entry with no matching broker order/position halts."""
        ctx = TradeContext(
            symbol="RELIANCE",
            entry_tag="V3.4_ENTRY",
            target_qty=10,
            tranche_qty=10,
            entry_order_id="ORD123",
            order_status="PENDING",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="STARTUP",
            active_trade=ctx,
        )

        # No broker order and no position. This is the actual ghost case.
        broker = MockBroker(positions=[], orders=[])

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        result = engine.step()

        self.assertEqual(result, "HALTED")
        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")
        self.assertEqual(broker.get_order_details_calls, 0)

    def test_reg_03_valid_entry_pending_recovers(self):
        """REG-03: Matching OPEN broker order remains ENTRY_PENDING."""
        ctx = TradeContext(
            symbol="RELIANCE",
            entry_tag="V3.4_ENTRY",
            target_qty=10,
            tranche_qty=10,
            entry_order_id="ORD123",
            order_status="PENDING",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="STARTUP",
            active_trade=ctx,
        )

        broker = MockBroker(
            positions=[],
            orders=[{
                "order_id": "ORD123",
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "product": "MIS",
                "status": "OPEN",
                "tag": "V3.4_ENTRY",
            }],
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        result = engine.step()

        self.assertEqual(result, "STATE_CHANGED")
        self.assertFalse(terminator.halted)
        self.assertEqual(engine.state.status, "ENTRY_PENDING")
        self.assertEqual(broker.get_order_details_calls, 0)

    def test_reg_04_verified_exit_complete(self):
        """REG-04: Complete verified exit clears trade and becomes FLAT."""
        ctx = TradeContext(
            symbol="RELIANCE",
            entry_tag="V3.4_ENTRY",
            target_qty=10,
            tranche_qty=10,
            filled_qty=10,
            exit_order_id="EXIT123",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_PENDING",
            active_trade=ctx,
        )

        broker = MockBroker(
            positions=[],
            order_details={
                "EXIT123": {
                    "status": "COMPLETE",
                    "filled_quantity": 10,
                }
            },
        )

        engine, store, terminator, _, _ = self.build_engine(state, broker)
        result = engine.step()

        self.assertEqual(result, "STATE_CHANGED")
        self.assertEqual(engine.state.status, "FLAT")
        self.assertIsNone(engine.state.active_trade)
        self.assertFalse(terminator.halted)
        self.assertGreater(store.save_count, 0)
        self.assertEqual(broker.get_positions_calls, 1)

    def test_reg_05_exit_complete_residual_position_halts(self):
        """REG-05: COMPLETE exit with residual position hard-halts."""
        ctx = TradeContext(
            symbol="RELIANCE",
            entry_tag="V3.4_ENTRY",
            target_qty=10,
            tranche_qty=10,
            filled_qty=10,
            exit_order_id="EXIT123",
        )
        state = BotState(
            trading_day="2026-08-10",
            status="EXIT_PENDING",
            active_trade=ctx,
        )

        broker = MockBroker(
            positions=[{
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "product": "MIS",
                "quantity": 5,
            }],
            order_details={
                "EXIT123": {
                    "status": "COMPLETE",
                    "filled_quantity": 10,
                }
            },
        )

        engine, _, terminator, _, _ = self.build_engine(state, broker)
        result = engine.step()

        self.assertEqual(result, "HALTED")
        self.assertTrue(terminator.halted)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")
        self.assertTrue(engine.state.clearance_required)

    def test_reg_06_clearance_rejected(self):
        """REG-06: Clearance rejected while broker has active exposure/order."""
        state = BotState(
            trading_day="2026-08-10",
            status="RECONCILIATION_HALT",
            clearance_required=True,
            halt_reason="Test Halt",
        )
        broker = MockBroker(
            positions=[{"tradingsymbol": "RELIANCE", "quantity": 10}],
            orders=[],
        )

        engine, _, terminator, audit, _ = self.build_engine(state, broker)
        cleared = engine.clear_halt_and_reconcile("Operator verification attempt")

        self.assertFalse(cleared)
        self.assertTrue(engine.state.clearance_required)
        self.assertEqual(engine.state.status, "RECONCILIATION_HALT")
        self.assertTrue(terminator.halted)
        self.assertTrue(any(e[0] == "OPERATOR_CLEARANCE_REJECTED" for e in audit.events))

    def test_reg_07_clearance_accepted_then_fresh_startup(self):
        """
        REG-07: Clean broker permits durable clearance recording, but the
        halted runtime is NOT revived. A fresh engine must perform reconciliation.
        """
        state = BotState(
            trading_day="2026-08-10",
            status="RECONCILIATION_HALT",
            clearance_required=True,
            halt_reason="Test Halt",
        )
        broker = MockBroker(positions=[], orders=[])

        engine, store, terminator, audit, _ = self.build_engine(state, broker)

        cleared = engine.clear_halt_and_reconcile(
            "Operator verified account flat"
        )

        self.assertTrue(cleared)
        self.assertFalse(engine.state.clearance_required)
        self.assertEqual(engine.state.status, "STARTUP")

        # Two-phase rule: current runtime remains halted.
        self.assertTrue(terminator.halted)
        self.assertEqual(engine.step(), "HALTED")

        # The durable state is now ready for a fresh process.
        fresh_terminator = MockTerminator()
        fresh_engine = TradingEngineV34(
            broker,
            MockClock(),
            None,
            store,
            MockAudit(),
            MockAlert(),
            MockLock(),
            fresh_terminator,
            self.cfg,
        )

        self.assertFalse(fresh_terminator.halted)
        self.assertEqual(fresh_engine.state.status, "STARTUP")

        result = fresh_engine.step()

        self.assertEqual(result, "STATE_CHANGED")
        self.assertEqual(fresh_engine.state.status, "FLAT")
        self.assertFalse(fresh_terminator.halted)

        self.assertTrue(
            any(e[0] == "OPERATOR_CLEARANCE_RECORDED" for e in audit.events)
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
