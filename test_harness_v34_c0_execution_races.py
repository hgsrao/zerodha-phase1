"""C0 execution-race tests for the provenance-locked V3.4-B.1.3 engine.

Run this file from the directory containing institutional_engine_v34.py:

    python test_harness_v34_c0_execution_races.py

This harness intentionally does not modify the engine.  C2 attacks the actual
EXIT_SUBMIT write boundary.  C5 is a strict temporal invariant test: a broker
must never report a smaller cumulative fill for the same exit order.  If C5
fails, that is an engine finding, not a reason to weaken this test.
"""

from __future__ import annotations

import hashlib
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import institutional_engine_v34 as eng
from kiteconnect import exceptions as ke


EXPECTED_SHA256 = "9A6DA05D3131FF6D66E727C758E14FA5453799B52618352498950DF4D06217DC"
SYMBOL = "RELIANCE"
TARGET_QTY = 100


def position(quantity: int) -> Dict[str, Any]:
    return {
        "tradingsymbol": SYMBOL,
        "exchange": "NSE",
        "product": "MIS",
        "quantity": quantity,
    }


def exit_order(filled: int) -> Dict[str, Any]:
    # OPEN is deliberate: the order remains live while its cumulative fill is
    # observed.  quantity stays at the original exit quantity on every tick.
    return {
        "order_id": "EX123",
        "tradingsymbol": SYMBOL,
        "exchange": "NSE",
        "product": "MIS",
        "transaction_type": "SELL",
        "status": "OPEN",
        "quantity": TARGET_QTY,
        "filled_quantity": filled,
        "tag": "V3.4_EXIT",
    }


class TerminatorFixture:
    def __init__(self) -> None:
        self.halted = False
        self.reason = ""
        self.calls: List[str] = []

    def halt(self, reason: str) -> None:
        self.halted = True
        self.reason = str(reason)
        self.calls.append(self.reason)


class MemoryStore:
    """A real state store fixture; unlike a bare MagicMock it returns BotState."""

    def __init__(self, state: Any) -> None:
        self.state = state
        self.saves: List[Any] = []

    def load(self, _trading_day: Any) -> Any:
        return self.state

    def save(self, state: Any) -> None:
        self.state = state
        self.saves.append(state)


class RaceBroker:
    def __init__(self) -> None:
        self.write_exception: BaseException | None = None
        self.place_order_calls: List[tuple[tuple[Any, ...], Dict[str, Any]]] = []
        self.order_events: List[Any] = []
        self.position_events: List[Any] = []
        self.get_order_details_calls = 0
        self.get_positions_calls = 0

    @staticmethod
    def _next(events: List[Any], operation: str) -> Any:
        if not events:
            raise AssertionError(f"Unexpected {operation} call: no fixture event remains")
        event = events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def place_order(self, *args: Any, **kwargs: Any) -> str:
        self.place_order_calls.append((args, kwargs))
        if self.write_exception is not None:
            raise self.write_exception
        return "EX123"

    def get_order_details(self, order_id: str) -> Dict[str, Any]:
        self.get_order_details_calls += 1
        self._require_exit_id(order_id)
        return self._next(self.order_events, "get_order_details")

    def get_positions(self) -> List[Dict[str, Any]]:
        self.get_positions_calls += 1
        return self._next(self.position_events, "get_positions")

    def get_orders(self) -> List[Dict[str, Any]]:
        # EXIT_PENDING polls its known exit order directly in the source path.
        return []

    @staticmethod
    def _require_exit_id(order_id: str) -> None:
        if str(order_id) != "EX123":
            raise AssertionError(f"Expected EXIT_PENDING to poll EX123, got {order_id!r}")


class TestC0ExecutionRaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = Path(eng.__file__).resolve()
        actual = hashlib.sha256(source.read_bytes()).hexdigest().upper()
        if actual != EXPECTED_SHA256:
            raise RuntimeError(
                "Refusing to run C0 against an unverified engine artifact. "
                f"Expected {EXPECTED_SHA256}; got {actual} ({source})."
            )

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

    @staticmethod
    def _trade(*, exit_order_id: str | None = None) -> Any:
        return eng.TradeContext(
            symbol=SYMBOL,
            entry_tag="C0_TEST",
            target_qty=TARGET_QTY,
            tranche_qty=TARGET_QTY,
            filled_qty=TARGET_QTY,
            pending_qty=0,
            avg_entry_price=Decimal("1300.0"),
            entry_order_id="EN123",
            stop_order_id="SL123",
            exit_order_id=exit_order_id,
            order_status="COMPLETE",
        )

    def _engine(self, state: Any, broker: RaceBroker) -> Any:
        store = MemoryStore(state)
        engine = eng.TradingEngineV34(
            broker,
            self.clock,
            MagicMock(),
            store,
            self.audit,
            self.alert,
            self.lock,
            self.terminator,
            self.config,
        )
        return engine

    def _exit_submit_state(self) -> Any:
        return eng.BotState(
            trading_day="2026-08-10",
            status="EXIT_SUBMIT",
            active_trade=self._trade(),
        )

    def _exit_pending_state(self) -> Any:
        return eng.BotState(
            trading_day="2026-08-10",
            status="EXIT_PENDING",
            active_trade=self._trade(exit_order_id="EX123"),
        )

    def _assert_single_failed_write(self, exc: BaseException, label: str) -> None:
        broker = RaceBroker()
        # A valid, unique active broker position is required before EXIT_SUBMIT
        # may invoke the mutation boundary.
        broker.position_events = [[position(TARGET_QTY)]]
        broker.write_exception = exc
        engine = self._engine(self._exit_submit_state(), broker)

        result = engine.step()

        self.assertEqual(1, len(broker.place_order_calls), f"{label}: write was not attempted exactly once")
        self.assertTrue(self.terminator.halted, f"{label}: ambiguous failed write must hard-halt")
        self.assertTrue(self.terminator.reason.strip(), f"{label}: halt reason must be recorded safely")
        self.assertIn(result, {"HALTED", "STATE_CHANGED"}, f"{label}: unexpected step result {result!r}")
        # A second call would be a blind retry.  There is only one supplied
        # position event, so a retry also fails loudly at the fixture boundary.
        self.assertEqual(1, len(broker.place_order_calls), f"{label}: blind retry detected")

    def test_c2a_rate_limit_on_exit_write_halts_without_retry(self) -> None:
        self._assert_single_failed_write(
            ke.NetworkException("Rate limit", code=429),
            "C2A / HTTP 429",
        )

    def test_c2b_timeout_on_exit_write_halts_without_retry(self) -> None:
        self._assert_single_failed_write(TimeoutError("write timed out"), "C2B / write timeout")

    def test_c5_exit_cumulative_fill_cannot_regress_across_observation_retry(self) -> None:
        broker = RaceBroker()
        # Tick 1: cumulative exit fill 30, residual broker position 70.
        # Tick 2: transient observation failure (within the configured budget).
        # Tick 3: invalid regression to cumulative fill 20, position 80.
        broker.order_events = [exit_order(30), TimeoutError("temporary observation timeout"), exit_order(20)]
        broker.position_events = [[position(70)], [position(80)]]
        engine = self._engine(self._exit_pending_state(), broker)

        first = engine.step()
        self.assertFalse(self.terminator.halted, f"C5 tick 1 unexpectedly halted: {self.terminator.reason}")
        self.assertNotEqual("HALTED", first, "C5 tick 1 must accept internally consistent 30/70")

        second = engine.step()
        self.assertFalse(
            self.terminator.halted,
            f"C5 tick 2 must consume one transient observation failure, not halt: {self.terminator.reason}",
        )
        self.assertIn(second, {"NO_ACTION", "STATE_CHANGED"}, "C5 tick 2 did not use the observation-retry path")

        third = engine.step()
        self.assertTrue(
            self.terminator.halted,
            "C5 FAILURE: EXIT_PENDING accepted cumulative fill regression 30 -> 20. "
            "The engine needs a per-order cumulative-fill high-water-mark invariant; do not weaken this test.",
        )
        self.assertIn("fill", self.terminator.reason.lower(), "C5 halt reason must identify fill safety")
        self.assertIn(third, {"HALTED", "STATE_CHANGED"})
        self.assertEqual([], broker.place_order_calls, "EXIT_PENDING observation must not issue another exit write")


if __name__ == "__main__":
    unittest.main(verbosity=2)
