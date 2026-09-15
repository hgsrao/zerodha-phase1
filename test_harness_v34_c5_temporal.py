"""13-point independent temporal high-water mark test suite for V3.4-B.1.4 temporal engine."""

from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import MagicMock

import institutional_engine_v34_B14_temporal as eng


class TerminatorFixture:
    def __init__(self) -> None:
        self.halted = False
        self.reason = ""

    def halt(self, reason: str) -> None:
        self.halted = True
        self.reason = str(reason)


class MemoryStore:
    def __init__(self, state: Any) -> None:
        self.state = state

    def load(self, _trading_day: Any) -> Any:
        return self.state

    def save(self, state: Any) -> None:
        self.state = state


class TemporalBroker:
    def __init__(self) -> None:
        self.order_events: List[Any] = []
        self.position_events: List[Any] = []

    def get_order_details(self, order_id: str) -> Dict[str, Any]:
        if not self.order_events:
            raise AssertionError("No more order events in fixture")
        ev = self.order_events.pop(0)
        if isinstance(ev, BaseException):
            raise ev
        return ev

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.position_events:
            return []
        ev = self.position_events.pop(0)
        if isinstance(ev, BaseException):
            raise ev
        return ev

    def get_orders(self) -> List[Dict[str, Any]]:
        return []


class TestTemporalHWM13(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MagicMock()
        self.clock.now.return_value = datetime(2026, 8, 10, 9, 30)
        self.audit = MagicMock()
        self.alert = MagicMock()
        self.lock = MagicMock()
        self.lock.acquire.return_value = True
        self.terminator = TerminatorFixture()
        self.config = eng.Config(
            alert_webhook_url="",
            max_daily_loss=Decimal("100"),
            observation_retry_budget=3,
        )

    def _engine(self, state: Any, broker: TemporalBroker) -> Any:
        store = MemoryStore(state)
        return eng.TradingEngineV34(
            broker, self.clock, MagicMock(), store, self.audit,
            self.alert, self.lock, self.terminator, self.config
        )

    @staticmethod
    def _mk_order(oid: str, filled: int, status="OPEN", qty=100) -> Dict[str, Any]:
        return {
            "order_id": oid,
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "product": "MIS",
            "status": status,
            "quantity": qty,
            "filled_quantity": filled,
            "average_price": 1300.0,
        }

    @staticmethod
    def _mk_pos(qty: int) -> List[Dict[str, Any]]:
        return [{
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "product": "MIS",
            "quantity": qty,
            "average_price": 1300.0,
        }]

    # --- T1 to T3 & T5: Entry Vector ---

    def test_t1_entry_zero_to_thirty(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T1", target_qty=100, tranche_qty=100, filled_qty=0, entry_order_id="EN123")
        state = eng.BotState(trading_day="2026-08-10", status="ENTRY_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EN123", 30)]
        broker.position_events = [self._mk_pos(30)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertFalse(self.terminator.halted, f"T1 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.entry_max_observed_fill, 30)
        self.assertEqual(trade.entry_hwm_order_id, "EN123")

    def test_t2_entry_equal_fill(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T2", target_qty=100, tranche_qty=100, filled_qty=30, entry_order_id="EN123", entry_max_observed_fill=30, entry_hwm_order_id="EN123")
        state = eng.BotState(trading_day="2026-08-10", status="ENTRY_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EN123", 30)]
        broker.position_events = [self._mk_pos(30)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertFalse(self.terminator.halted, f"T2 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.entry_max_observed_fill, 30)

    def test_t3_entry_advance_fill(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T3", target_qty=100, tranche_qty=100, filled_qty=30, entry_order_id="EN123", entry_max_observed_fill=30, entry_hwm_order_id="EN123")
        state = eng.BotState(trading_day="2026-08-10", status="ENTRY_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EN123", 50)]
        broker.position_events = [self._mk_pos(50)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertFalse(self.terminator.halted, f"T3 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.entry_max_observed_fill, 50)

    def test_t5_entry_regression_halts(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T5", target_qty=100, tranche_qty=100, filled_qty=50, entry_order_id="EN123", entry_max_observed_fill=50, entry_hwm_order_id="EN123")
        state = eng.BotState(trading_day="2026-08-10", status="ENTRY_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EN123", 20)]
        broker.position_events = [self._mk_pos(20)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertTrue(self.terminator.halted, "T5 failed to halt on regression")
        self.assertIn("regressed", self.terminator.reason)

    # --- T6 to T8: Protective Stop Vector ---

    def test_t6_stop_monotonic_progression(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T6", target_qty=100, tranche_qty=100, filled_qty=100, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", stop_order_id="SL123", order_status="COMPLETE")
        state = eng.BotState(trading_day="2026-08-10", status="PARTIAL_POSITION", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("SL123", 30), self._mk_order("SL123", 70)]
        broker.position_events = [self._mk_pos(70), self._mk_pos(30)]

        engine = self._engine(state, broker)
        engine.step() # 30 filled
        self.assertFalse(self.terminator.halted, f"T6 step 1 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.stop_max_observed_fill, 30)

        engine.step() # 70 filled
        self.assertFalse(self.terminator.halted, f"T6 step 2 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.stop_max_observed_fill, 70)

    def test_t7_stop_equal_fill(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T7", target_qty=100, tranche_qty=100, filled_qty=30, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", stop_order_id="SL123", order_status="COMPLETE", stop_max_observed_fill=70, stop_hwm_order_id="SL123")
        state = eng.BotState(trading_day="2026-08-10", status="PARTIAL_POSITION", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("SL123", 70)]
        broker.position_events = [self._mk_pos(30)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertFalse(self.terminator.halted, f"T7 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.stop_max_observed_fill, 70)

    def test_t8_stop_regression_halts(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T8", target_qty=100, tranche_qty=100, filled_qty=70, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", stop_order_id="SL123", order_status="COMPLETE", stop_max_observed_fill=30, stop_hwm_order_id="SL123")
        state = eng.BotState(trading_day="2026-08-10", status="PARTIAL_POSITION", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("SL123", 20)]
        broker.position_events = [self._mk_pos(80)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertTrue(self.terminator.halted, "T8 failed to halt on stop regression")
        self.assertIn("regressed", self.terminator.reason)

    # --- T9 to T11: Exit Vector ---

    def test_t9_exit_monotonic_progression(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T9", target_qty=100, tranche_qty=100, filled_qty=100, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", exit_order_id="EX123", order_status="COMPLETE")
        state = eng.BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EX123", 30), self._mk_order("EX123", 70)]
        broker.position_events = [self._mk_pos(70), self._mk_pos(30)]

        engine = self._engine(state, broker)
        engine.step() # 30 filled
        self.assertFalse(self.terminator.halted, f"T9 step 1 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.exit_max_observed_fill, 30)

        engine.step() # 70 filled
        self.assertFalse(self.terminator.halted, f"T9 step 2 failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.exit_max_observed_fill, 70)

    def test_t10_exit_timeout_recovery(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T10", target_qty=100, tranche_qty=100, filled_qty=100, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", exit_order_id="EX123", order_status="COMPLETE", exit_max_observed_fill=30, exit_hwm_order_id="EX123")
        state = eng.BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [TimeoutError("timeout"), self._mk_order("EX123", 50)]
        broker.position_events = [self._mk_pos(50)]

        engine = self._engine(state, broker)
        engine.step() # Timeout
        self.assertFalse(self.terminator.halted, f"T10 timeout step failed. Halt reason: {self.terminator.reason}")
        engine.step() # Recovery 50
        self.assertFalse(self.terminator.halted, f"T10 recovery step failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.exit_max_observed_fill, 50)

    def test_t11_exit_regression_halts(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T11", target_qty=100, tranche_qty=100, filled_qty=100, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", exit_order_id="EX123", order_status="COMPLETE", exit_max_observed_fill=30, exit_hwm_order_id="EX123")
        state = eng.BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EX123", 20)]
        broker.position_events = [self._mk_pos(80)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertTrue(self.terminator.halted, "T11 failed to halt on exit regression")
        self.assertIn("regressed", self.terminator.reason)

    # --- T12 to T14: Order Rotation & Restart Persistence ---

    def test_t12_order_id_rotation_isolation(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T12", target_qty=100, tranche_qty=100, filled_qty=100, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", exit_order_id="EX123", order_status="COMPLETE", exit_max_observed_fill=30, exit_hwm_order_id="EX123")
        state = eng.BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EX456", 5)]
        broker.position_events = [self._mk_pos(95)]

        trade.exit_order_id = "EX456"
        engine = self._engine(state, broker)

        engine.step()
        self.assertFalse(self.terminator.halted, f"Rotation isolation failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.exit_hwm_order_id, "EX456")
        self.assertEqual(trade.exit_max_observed_fill, 5)

    def test_t13_persisted_hwm_regression_halts_after_restart(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T13", target_qty=100, tranche_qty=100, filled_qty=100, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", exit_order_id="EX123", order_status="COMPLETE", exit_max_observed_fill=30, exit_hwm_order_id="EX123")
        state = eng.BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EX123", 20)]
        broker.position_events = [self._mk_pos(80)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertTrue(self.terminator.halted, "T13 failed to halt on restart regression")
        self.assertIn("regressed", self.terminator.reason)

    def test_t14_persisted_hwm_new_order_after_restart_passes(self) -> None:
        trade = eng.TradeContext(symbol="RELIANCE", entry_tag="T14", target_qty=100, tranche_qty=100, filled_qty=100, avg_entry_price=Decimal("1300.0"), entry_order_id="EN123", exit_order_id="EX456", order_status="COMPLETE", exit_max_observed_fill=30, exit_hwm_order_id="EX123")
        state = eng.BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=trade)
        broker = TemporalBroker()
        broker.order_events = [self._mk_order("EX456", 5)]
        broker.position_events = [self._mk_pos(95)]

        engine = self._engine(state, broker)
        engine.step()
        self.assertFalse(self.terminator.halted, f"Post-restart new order rotation failed. Halt reason: {self.terminator.reason}")
        self.assertEqual(trade.exit_hwm_order_id, "EX456")
        self.assertEqual(trade.exit_max_observed_fill, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)