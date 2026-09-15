# test_harness_v34_b13_convergence.py
import unittest
import hashlib
import os
import copy
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock

import institutional_engine_v34 as target_engine
from institutional_engine_v34 import TradingEngineV34, Config, BotState, TradeContext

EXPECTED_B12_SHA256 = "9A6DA05D3131FF6D66E727C758E14FA5453799B52618352498950DF4D06217DC"

class TerminatorFixture:
    def __init__(self):
        self.halted = False
        self.reason = None
    def halt(self, reason):
        self.halted = True
        self.reason = reason

class PersistentMemoryStore:
    def __init__(self):
        self._disk = None
    def save(self, state):
        self._disk = copy.deepcopy(state)
    def load(self, date):
        return copy.deepcopy(self._disk) if self._disk else BotState(trading_day=str(date))

class HostileBroker:
    def __init__(self, positions=None, orders=None, order_details=None, ltp_exc=None):
        self._positions = positions if positions is not None else []
        self._orders = orders if orders is not None else []
        self._order_details = order_details if order_details is not None else {}
        self._ltp_exc = ltp_exc

    def get_positions(self): 
        return self._positions
    def get_orders(self): 
        return self._orders
    def get_order_details(self, oid):
        if oid in self._order_details: 
            return self._order_details[oid]
        raise Exception(f"Order {oid} not found in broker reality")
    def ltp(self, symbols):
        if self._ltp_exc: 
            raise self._ltp_exc
        return {f"NSE:{s}": {"last_price": 1300.0} for s in symbols}

class TestB13RestartConvergence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine_path = os.path.abspath(target_engine.__file__)
        with open(engine_path, 'rb') as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest().upper()
        
        if actual_hash != EXPECTED_B12_SHA256:
            raise RuntimeError(
                f"CRITICAL: Artifact identity mismatch! Expected {EXPECTED_B12_SHA256}, Got {actual_hash}"
            )

    def setUp(self):
        self.store = PersistentMemoryStore()
        self.clock = MagicMock()
        self.clock.now.return_value = datetime.now()
        self.audit = MagicMock()
        self.alert = MagicMock()
        self.lock = MagicMock()
        self.lock.acquire.return_value = True
        self.cfg = Config(alert_webhook_url="", max_daily_loss=Decimal("100"), observation_retry_budget=3)
        
        self.trade_ctx = TradeContext(
            symbol="RELIANCE", entry_tag="TEST", target_qty=100, tranche_qty=100,
            filled_qty=100, avg_entry_price=Decimal("1300.0"), stop_order_id="SL123", exit_order_id="EX123"
        )

    def _create_engine(self, broker):
        terminator = TerminatorFixture()
        engine = TradingEngineV34(
            broker, self.clock, MagicMock(), self.store, self.audit, 
            self.alert, self.lock, terminator, self.cfg
        )
        return engine, terminator

    # --- B1.3 HOSTILE RESTART SCENARIOS ---

    def test_b1_ghost_position(self):
        self.store.save(BotState(trading_day="2026-08-10", status="FLAT"))
        broker = HostileBroker(positions=[{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "quantity": 100}])
        
        engine, term = self._create_engine(broker)
        
        print(f"\n--- B1 TELEMETRY LOG ---")
        print(f"State Before Step:  {engine.state.status}")
        print(f"Broker Positions:   {broker.get_positions()}")
        
        res = engine.step()
        
        print(f"Step Result:        {res}")
        print(f"State After Step:   {engine.state.status}")
        print(f"Terminator Halted:  {term.halted}")
        print(f"Terminator Reason:  {term.reason}")
        print(f"------------------------\n")
        
        self.assertTrue(term.halted, "B1 FAILED: Engine completely ignored the ghost position and failed to halt!")

    def test_b2_vanished_position(self):
        self.store.save(BotState(trading_day="2026-08-10", status="MANAGING", active_trade=self.trade_ctx))
        # Provide order details so the engine reaches the position logic instead of crashing on the fetch
        broker = HostileBroker(
            positions=[], 
            order_details={"SL123": {"status": "OPEN", "filled_quantity": 0, "quantity": 100}}
        )
        
        engine, term = self._create_engine(broker)
        engine.step()
        
        self.assertTrue(term.halted)
        self.assertIn("CRITICAL P0: MANAGING requires exactly one matching broker position", term.reason)

    def test_b3_zombie_stop_loss(self):
        self.store.save(BotState(trading_day="2026-08-10", status="MANAGING", active_trade=self.trade_ctx))
        broker = HostileBroker(
            positions=[{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "quantity": 100}],
            orders=[],
            # Mock the SL as CANCELLED while the bot was dead
            order_details={"SL123": {"status": "CANCELLED", "filled_quantity": 0, "quantity": 100}}
        )
        
        engine, term = self._create_engine(broker)
        engine.step()
        
        self.assertTrue(term.halted)
        self.assertIn("Protective stop is no longer active", term.reason)

    def test_b4_residual_exit(self):
        self.store.save(BotState(trading_day="2026-08-10", status="EXIT_PENDING", active_trade=self.trade_ctx))
        broker = HostileBroker(
            positions=[{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "quantity": 10}],
            orders=[{"order_id": "EX123", "status": "COMPLETE", "filled_quantity": 100, "quantity": 100}],
            order_details={"EX123": {"status": "COMPLETE", "filled_quantity": 100, "quantity": 100}}
        )
        
        engine, term = self._create_engine(broker)
        engine.step()
        
        self.assertTrue(term.halted)
        self.assertIn("Exit COMPLETE but residual broker position exists", term.reason)

    def test_b5_persisted_lock(self):
        self.store.save(BotState(trading_day="2026-08-10", status="RECONCILIATION_HALT", halt_reason="Previous fatal error"))
        broker = HostileBroker()
        
        engine, term = self._create_engine(broker)
        self.assertTrue(term.halted)
        self.assertIn("Previous fatal error", term.reason)

    def test_b6_resilience_amnesia(self):
        self.store.save(BotState(trading_day="2026-08-10", status="MANAGING", active_trade=self.trade_ctx))
        
        broker1 = HostileBroker(
            positions=[{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "quantity": 100}],
            order_details={"SL123": {"status": "OPEN", "filled_quantity": 0, "quantity": 100}},
            ltp_exc=TimeoutError("Transient 1")
        )
        
        engine1, term1 = self._create_engine(broker1)
        engine1.step() # Retry 1
        engine1.step() # Retry 2
        engine1.step() # Retry 3
        
        self.assertFalse(term1.halted, "Engine 1 halted early! Miscalculated budget.")
        
        del engine1
        
        broker2 = HostileBroker(
            positions=[{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "quantity": 100}],
            order_details={"SL123": {"status": "OPEN", "filled_quantity": 0, "quantity": 100}},
            ltp_exc=TimeoutError("Transient Post-Restart")
        )
        
        engine2, term2 = self._create_engine(broker2)
        engine2.step() # Retry 1 on new process
        
        self.assertFalse(term2.halted, "Engine 2 improperly inherited Engine 1's failure budget and halted!")

if __name__ == '__main__':
    unittest.main(verbosity=2)