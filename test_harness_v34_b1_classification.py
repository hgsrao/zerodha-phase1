# test_harness_v34_b1_classification.py
import unittest
import json
import requests
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock
import kiteconnect.exceptions as ke

from institutional_engine_v34 import (
    TradingEngineV34, Config, BotState, TradeContext
)

class TerminatorFixture:
    def __init__(self):
        self.halted = False
        self.reason = None
    def halt(self, reason):
        self.halted = True
        self.reason = reason

class InstrumentedExceptionInjectingBroker:
    def __init__(self, exc_to_raise):
        self.exc_to_raise = exc_to_raise
        self.calls = []

    def ltp(self, symbols):
        self.calls.append(("ltp", symbols))
        raise self.exc_to_raise

    def get_order_details(self, order_id):
        self.calls.append(("get_order_details", order_id))
        return {"status": "OPEN", "filled_quantity": 0, "quantity": 100, "target_qty": 100}

    def get_positions(self):
        self.calls.append(("get_positions",))
        return [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "quantity": 100}]

    def get_orders(self):
        self.calls.append(("get_orders",))
        return [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "MIS", "transaction_type": "SELL", "tag": "V3.4_SL", "status": "OPEN", "order_id": "SL123", "quantity": 100}]

class TestB12Classification(unittest.TestCase):
    def setUp(self):
        self.store = MagicMock()
        self.clock = MagicMock()
        self.clock.now.return_value = datetime.now()
        self.audit = MagicMock()
        self.alert = MagicMock()
        self.lock = MagicMock()
        self.lock.acquire.return_value = True
        self.cfg = Config(
            alert_webhook_url="", 
            max_daily_loss=Decimal("100"), 
            observation_retry_budget=3
        )
        
        self.active_trade = TradeContext(
            symbol="RELIANCE", entry_tag="TEST", target_qty=100, tranche_qty=100,
            filled_qty=100, avg_entry_price=Decimal("1300.0"), stop_order_id="SL123"
        )
        # MANAGING state forces an immediate broker.ltp() observation on tick 1
        self.state = BotState(
            trading_day="2026-08-10", 
            status="MANAGING", 
            active_trade=self.active_trade
        )
        self.store.load.return_value = self.state

    def _execute_classification(self, exception_instance, expect_transient):
        broker = InstrumentedExceptionInjectingBroker(exception_instance)
        terminator = TerminatorFixture()
        engine = TradingEngineV34(
            broker, self.clock, MagicMock(), self.store, self.audit, 
            self.alert, self.lock, terminator, self.cfg
        )
        
        engine.step()

        # 1. Verify the target observation call was actually reached
        self.assertTrue(len(broker.calls) > 0, "Broker was never invoked by the engine")
        self.assertEqual(broker.calls[0][0], "ltp", f"Expected first call 'ltp', got '{broker.calls[0][0]}'")
        
        code_str = f" [code={getattr(exception_instance, 'code', 'N/A')}]"
        exc_info = f"{type(exception_instance).__name__}{code_str}"

        # 2. Assert classification behavior and provenance
        if expect_transient:
            self.assertFalse(
                terminator.halted, 
                f"Engine incorrectly HALTED for expected transient exception: {exc_info} - Reason: {terminator.reason}"
            )
        else:
            self.assertTrue(
                terminator.halted, 
                f"Engine incorrectly RETRIED a definitive/malformed exception: {exc_info}"
            )
            self.assertIsNotNone(terminator.reason, "Halt reason must be explicitly recorded upon hard halt")

    # --- TRANSIENT BUCKET (RETRY EXPECTED -> NO HALT) ---
    def test_b25_timeout_error(self):
        self._execute_classification(TimeoutError("Standard timeout"), True)

    def test_b26_connection_error(self):
        self._execute_classification(ConnectionError("Standard connection drop"), True)

    def test_b27_requests_timeout(self):
        self._execute_classification(requests.exceptions.Timeout("Timed out"), True)

    def test_b27b_requests_read_timeout(self):
        self._execute_classification(requests.exceptions.ReadTimeout("Read timed out"), True)

    def test_b27c_requests_connect_timeout(self):
        self._execute_classification(requests.exceptions.ConnectTimeout("Connect timed out"), True)

    def test_b28_kite_network_exception_503(self):
        self._execute_classification(ke.NetworkException("Gateway unavailable", code=503), True)

    def test_b28c_kite_network_exception_504(self):
        self._execute_classification(ke.NetworkException("Gateway timeout", code=504), True)

    # --- DEFINITIVE / MALFORMED BUCKET (IMMEDIATE HARD HALT EXPECTED) ---
    def test_b28b_kite_network_exception_429(self):
        """NetworkException carrying a 429 Rate Limit must NEVER enter retry budget."""
        self._execute_classification(ke.NetworkException("Too many requests", code=429), False)

    def test_b32_kite_data_exception_502(self):
        """DataException (even with code 502) indicates a bad OMS response -> HALT."""
        self._execute_classification(ke.DataException("Bad response from OMS", code=502), False)

    def test_b33_json_decode_error(self):
        self._execute_classification(json.decoder.JSONDecodeError("Expecting value", "doc", 0), False)

    def test_b35_kite_token_exception(self):
        self._execute_classification(ke.TokenException("Token expired", code=403), False)

    def test_b36_kite_permission_exception(self):
        self._execute_classification(ke.PermissionException("Insufficient permissions", code=403), False)

    def test_b37_kite_data_exception_generic(self):
        self._execute_classification(ke.DataException("Market data invalid"), False)

    def test_b38_kite_order_exception(self):
        self._execute_classification(ke.OrderException("Order violates limits"), False)

    def test_b38b_kite_general_exception(self):
        self._execute_classification(ke.GeneralException("Unknown Kite Error"), False)

    def test_b38c_kite_input_exception(self):
        self._execute_classification(ke.InputException("Bad Input parameters"), False)

    # --- ADVERSARIAL STRING MATCHING BYPASS TESTS ---
    def test_b39_string_matching_bypass_python_exception(self):
        """Proves python base Exception with 'timeout 503' text is strictly HALTED."""
        sneaky_exc = Exception("timeout connection 503 temporary")
        self._execute_classification(sneaky_exc, False)

    def test_b39b_string_matching_bypass_kite_general_exception(self):
        """Proves Kite GeneralException carrying 'timeout 503' text is strictly HALTED."""
        sneaky_exc = ke.GeneralException("timeout connection 503 temporary", code=500)
        self._execute_classification(sneaky_exc, False)

if __name__ == '__main__':
    unittest.main(verbosity=2)