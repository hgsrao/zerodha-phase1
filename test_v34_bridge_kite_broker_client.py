"""Tests for v34_bridge_kite_broker_client.py (Phase 2: write authority).

Two layers of proof, deliberately both present:

1. Unit tests against a fake KiteConnect - prove KiteBrokerClient's own
   behavior in isolation (kwarg translation, the LIVE_TRADING_ENABLED
   gate, exception/return-value pass-through, no retries).
2. Real engine-level integration tests - construct the actual, real
   TradingEngineV34P02 + the actual, real KiteBrokerAdapterMultiPos +
   this new adapter (wrapping a fake kite), and drive request_entry()/
   request_exit()/step() through it. This is the proof the user actually
   asked for: not "the adapter behaves reasonably in isolation" but "the
   real, frozen, already-tested engine classifies outcomes correctly
   when THIS adapter is what it's actually calling" - ENTRY_UNKNOWN vs
   RECONCILIATION_HALT, and the deliberate entry/exit asymmetry on a
   malformed-but-non-exception return value, proven by driving the real
   state machine, not by asserting what the docstring claims it does.

FakeKiteConnectWithOrders extends (never modifies)
test_v34_bridge_kite_read_only_client.FakeKiteConnect - Phase 1's own
test double - with place_order() and the SDK constants Phase 2 needs.
Phase 1's own file is not touched anywhere in this file.
"""

from datetime import datetime, timezone
from decimal import Decimal

import kiteconnect.exceptions as ke
import pytest

from institutional_engine_v34_p02_multipos_candidate import PositionStatus
from test_v34_bridge_kite_read_only_client import FakeKiteConnect
from test_v34_p02_lifecycle_integration import make_context, make_stack
from test_v34_p02_multipos_engine import flat_running_state, sample_ctx
from v34_bridge_kite_broker_client import KiteBrokerClient
from v34_p02_state import EngineStatus


class FakeKiteConnectWithOrders(FakeKiteConnect):
    """Adds place_order() and the SDK constants KiteBrokerClient's write
    methods reference - real values, verified against the installed
    kiteconnect package, not guessed."""

    VARIETY_REGULAR = "regular"
    ORDER_TYPE_SLM = "SL-M"
    PRODUCT_CNC = "CNC"

    def __init__(self):
        super().__init__()
        self.place_order_calls = []
        self.place_order_response = "ORD-DEFAULT"

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        self._maybe_raise("place_order")
        if callable(self.place_order_response):
            return self.place_order_response(**kwargs)
        return self.place_order_response


# ---------------------------------------------------------------------------
# Layer 1: unit tests against the fake Kite client directly.
# ---------------------------------------------------------------------------

class TestPlaceOrderLiveTradingGate:
    def test_disabled_raises_and_makes_zero_broker_calls(self):
        kite = FakeKiteConnectWithOrders()
        client = KiteBrokerClient(kite, live_trading_enabled=False)
        with pytest.raises(RuntimeError, match="SAFETY_HALT.*place_order"):
            client.place_order(
                exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY",
                quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="TAG",
            )
        assert kite.place_order_calls == []

    def test_disabled_error_is_not_chained_so_it_cannot_look_transient(self):
        # _is_transient_submission_exception walks __cause__ - a bare
        # RuntimeError with no chained cause is unambiguously non-
        # transient, which is exactly what a definite local block must
        # be classified as (trigger_hard_halt, not ENTRY_UNKNOWN).
        kite = FakeKiteConnectWithOrders()
        client = KiteBrokerClient(kite, live_trading_enabled=False)
        try:
            client.place_order(
                exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY",
                quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="TAG",
            )
        except RuntimeError as exc:
            assert exc.__cause__ is None


class TestPlaceOrderHappyPath:
    def test_variety_is_injected_and_every_other_kwarg_passes_through(self):
        kite = FakeKiteConnectWithOrders()
        kite.place_order_response = "REAL-ORDER-ID-1"
        client = KiteBrokerClient(kite, live_trading_enabled=True)

        result = client.place_order(
            exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY",
            quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="TAG",
        )
        assert result == "REAL-ORDER-ID-1"
        assert len(kite.place_order_calls) == 1
        call = kite.place_order_calls[0]
        assert call == {
            "variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE",
            "transaction_type": "BUY", "quantity": 10, "product": "CNC",
            "order_type": "LIMIT", "price": 2500.0, "tag": "TAG",
        }


class TestPlaceOrderExceptionAndReturnValuePassThrough:
    def test_a_real_transient_network_exception_propagates_unwrapped(self):
        kite = FakeKiteConnectWithOrders()
        exc = ke.NetworkException("Gateway unavailable", code=503)
        kite.raise_on["place_order"] = exc
        client = KiteBrokerClient(kite, live_trading_enabled=True)

        with pytest.raises(ke.NetworkException) as excinfo:
            client.place_order(
                exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY",
                quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="TAG",
            )
        assert excinfo.value is exc

    def test_a_malformed_return_value_is_not_validated_or_rejected(self):
        # KiteBrokerClient must NOT raise its own KiteResponseMalformedError
        # (or anything else) for a garbage return value - P02's own
        # _step_entry_submit() already owns this classification
        # (-> ENTRY_UNKNOWN). See module docstring.
        kite = FakeKiteConnectWithOrders()
        kite.place_order_response = ""  # garbage, but no exception raised
        client = KiteBrokerClient(kite, live_trading_enabled=True)

        result = client.place_order(
            exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY",
            quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="TAG",
        )
        assert result == ""  # passed straight through, unmodified

    def test_no_retry_exactly_one_underlying_call_even_on_failure(self):
        kite = FakeKiteConnectWithOrders()
        kite.raise_on["place_order"] = ke.NetworkException("Gateway unavailable", code=503)
        client = KiteBrokerClient(kite, live_trading_enabled=True)

        with pytest.raises(ke.NetworkException):
            client.place_order(
                exchange="NSE", tradingsymbol="RELIANCE", transaction_type="BUY",
                quantity=10, product="CNC", order_type="LIMIT", price=2500.0, tag="TAG",
            )
        assert len(kite.place_order_calls) == 1


class TestSubmitEmergencyExitLiveTradingGate:
    def test_disabled_raises_and_makes_zero_broker_calls(self):
        kite = FakeKiteConnectWithOrders()
        client = KiteBrokerClient(kite, live_trading_enabled=False)
        with pytest.raises(RuntimeError, match="SAFETY_HALT.*submit_emergency_exit"):
            client.submit_emergency_exit(
                symbol="RELIANCE", quantity=50, trigger_price=Decimal("2490"),
                source_ltp=Decimal("2495"), tick_size=Decimal("0.05"),
                market_protection=Decimal("1.0"), tag="V3.4_P02_EXIT",
            )
        assert kite.place_order_calls == []


class TestSubmitEmergencyExitHappyPathTranslation:
    def test_kwargs_are_translated_to_real_kite_parameters(self):
        kite = FakeKiteConnectWithOrders()
        kite.place_order_response = "EXIT-ORDER-ID-1"
        client = KiteBrokerClient(kite, live_trading_enabled=True, product="CNC")

        result = client.submit_emergency_exit(
            symbol="RELIANCE", quantity=50, trigger_price=Decimal("2490.15"),
            source_ltp=Decimal("2495.30"), tick_size=Decimal("0.05"),
            market_protection=Decimal("1.0"), tag="V3.4_P02_EXIT",
        )
        assert result == "EXIT-ORDER-ID-1"
        assert len(kite.place_order_calls) == 1
        call = kite.place_order_calls[0]
        assert call["variety"] == "regular"
        assert call["exchange"] == "NSE"
        assert call["tradingsymbol"] == "RELIANCE"  # symbol -> tradingsymbol
        assert call["transaction_type"] == "SELL"
        assert call["quantity"] == 50
        assert call["product"] == "CNC"
        assert call["order_type"] == "SL-M"
        assert call["trigger_price"] == 2490.15  # Decimal -> float
        assert call["market_protection"] == 1.0
        assert call["tag"] == "V3.4_P02_EXIT"
        # source_ltp/tick_size are not real Kite place_order() parameters -
        # must never be forwarded.
        assert "source_ltp" not in call
        assert "tick_size" not in call
        assert "symbol" not in call


class TestSubmitEmergencyExitExceptionAndReturnValuePassThrough:
    def test_a_real_transient_network_exception_propagates_unwrapped(self):
        kite = FakeKiteConnectWithOrders()
        exc = ke.NetworkException("Too many requests", code=429)
        kite.raise_on["place_order"] = exc
        client = KiteBrokerClient(kite, live_trading_enabled=True)

        with pytest.raises(ke.NetworkException) as excinfo:
            client.submit_emergency_exit(
                symbol="RELIANCE", quantity=50, trigger_price=Decimal("2490"),
                source_ltp=Decimal("2495"), tick_size=Decimal("0.05"),
                market_protection=Decimal("1.0"), tag="V3.4_P02_EXIT",
            )
        assert excinfo.value is exc

    def test_a_malformed_return_value_is_not_validated_or_rejected(self):
        kite = FakeKiteConnectWithOrders()
        kite.place_order_response = None
        client = KiteBrokerClient(kite, live_trading_enabled=True)

        result = client.submit_emergency_exit(
            symbol="RELIANCE", quantity=50, trigger_price=Decimal("2490"),
            source_ltp=Decimal("2495"), tick_size=Decimal("0.05"),
            market_protection=Decimal("1.0"), tag="V3.4_P02_EXIT",
        )
        assert result is None


class TestReadDelegation:
    def test_all_five_read_methods_delegate_to_the_sealed_read_only_client(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 50}], "day": []}
        kite.orders_response = [{"order_id": "O1", "status": "COMPLETE"}]
        kite.order_history_responses["O1"] = [{"order_id": "O1", "status": "COMPLETE"}]
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2500.0}}
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": 0.05}]
        client = KiteBrokerClient(kite, live_trading_enabled=True)

        assert client.get_positions() == kite.positions_response["net"]
        assert client.get_orders() == kite.orders_response
        assert client.get_order_details("O1") == {"order_id": "O1", "status": "COMPLETE"}
        assert client.ltp(["RELIANCE"]) == kite.ltp_response
        assert client.get_tick_size("RELIANCE") == Decimal("0.05")


# ---------------------------------------------------------------------------
# Layer 2: real engine-level integration - the centerpiece proof.
# ---------------------------------------------------------------------------

def _real_engine_with_write_adapter(*, kite, state, live_trading_enabled=True, max_simultaneous_positions=6):
    from portfolio_brain_v9 import SECTORS as REAL_SECTORS
    from v34_p02_accounting import initial_checkpoint
    from v34_p02_state import Config

    raw_broker = KiteBrokerClient(kite, live_trading_enabled=live_trading_enabled)
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"), max_simultaneous_positions=max_simultaneous_positions)
    context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))
    return make_stack(state=state, raw_broker=raw_broker, cfg=cfg, context=context)


class TestRealEngineEntryClassification:
    """Drives the real TradingEngineV34P02 + real KiteBrokerAdapterMultiPos
    + this adapter through request_entry() -> step(), proving the engine's
    own (already frozen, already tested) classification lands correctly
    when this adapter is what it's actually calling underneath."""

    def test_transient_failure_lands_in_entry_unknown(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [], "day": []}
        kite.orders_response = []
        kite.raise_on["place_order"] = ke.NetworkException("Gateway unavailable", code=503)

        state = flat_running_state()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state)

        assert engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500")) == "STATE_CHANGED"
        engine.step()  # ENTRY_SUBMIT -> observes clean positions/orders -> ENTRY_SUBMITTING -> place_order raises
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.ENTRY_UNKNOWN
        assert engine.state.status != EngineStatus.RECONCILIATION_HALT

    def test_a_malformed_but_non_exception_order_id_also_lands_in_entry_unknown(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [], "day": []}
        kite.orders_response = []
        kite.place_order_response = ""  # call "succeeds" but returns garbage

        state = flat_running_state()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state)

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        engine.step()
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.ENTRY_UNKNOWN
        assert engine.state.status != EngineStatus.RECONCILIATION_HALT

    def test_a_definite_non_transient_exception_halts_the_whole_engine(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [], "day": []}
        kite.orders_response = []
        kite.raise_on["place_order"] = ValueError("margin insufficient")  # definite, not transient-shaped

        state = flat_running_state()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state)

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        engine.step()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT

    def test_live_trading_disabled_halts_the_whole_engine_not_entry_unknown(self):
        # The definite-local-block proof, through the real stack: disabled
        # write authority must never look ambiguous.
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [], "day": []}
        kite.orders_response = []

        state = flat_running_state()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state, live_trading_enabled=False)

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        engine.step()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert kite.place_order_calls == []  # confirmed at the real-engine level, not just the adapter's own

    def test_happy_path_reaches_entry_pending_with_a_real_order_id(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [], "day": []}
        kite.orders_response = []
        kite.place_order_response = "REAL-ENTRY-ORDER-1"

        state = flat_running_state()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state)

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        engine.step()
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.ENTRY_PENDING
        assert ctx.entry_order_id == "REAL-ENTRY-ORDER-1"
        call = kite.place_order_calls[0]
        assert call["transaction_type"] == "BUY"
        assert call["variety"] == "regular"


class TestRealEngineExitClassification:
    """Same proof, exit side - and the specific asymmetry: a malformed
    return value on exit must halt, not reconcile as EXIT_UNKNOWN,
    unlike the identical case on entry above."""

    def _managing_state_with_reliance(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(
            symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None,
        )
        return state

    def test_transient_failure_lands_in_exit_unknown(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 50, "average_price": "2500.00"}], "day": []}
        kite.orders_response = []
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2495.0}}
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": 0.05}]
        kite.raise_on["place_order"] = ke.NetworkException("Gateway unavailable", code=503)

        state = self._managing_state_with_reliance()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state)

        assert engine.request_exit(symbol="RELIANCE") == "STATE_CHANGED"
        engine.step()
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.EXIT_UNKNOWN
        assert engine.state.status != EngineStatus.RECONCILIATION_HALT

    def test_a_malformed_but_non_exception_order_id_halts_instead_of_exit_unknown(self):
        # The asymmetry proof: identical malformed-return shape as the
        # entry test above, opposite classification - P02 refuses any
        # doubt on the liquidation path.
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 50, "average_price": "2500.00"}], "day": []}
        kite.orders_response = []
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2495.0}}
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": 0.05}]
        kite.place_order_response = ""  # garbage, no exception

        state = self._managing_state_with_reliance()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state)

        engine.request_exit(symbol="RELIANCE")
        engine.step()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT

    def test_live_trading_disabled_halts_the_whole_engine(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 50, "average_price": "2500.00"}], "day": []}
        kite.orders_response = []
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2495.0}}
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": 0.05}]

        state = self._managing_state_with_reliance()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state, live_trading_enabled=False)

        engine.request_exit(symbol="RELIANCE")
        engine.step()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert kite.place_order_calls == []

    def test_happy_path_reaches_exit_pending_with_a_real_order_id(self):
        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 50, "average_price": "2500.00"}], "day": []}
        kite.orders_response = []
        kite.ltp_response = {"NSE:RELIANCE": {"last_price": 2495.0}}
        kite.instruments_response = [{"tradingsymbol": "RELIANCE", "tick_size": 0.05}]
        kite.place_order_response = "REAL-EXIT-ORDER-1"

        state = self._managing_state_with_reliance()
        engine, *_ = _real_engine_with_write_adapter(kite=kite, state=state)

        engine.request_exit(symbol="RELIANCE")
        engine.step()
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.EXIT_PENDING
        assert ctx.exit_order_id == "REAL-EXIT-ORDER-1"
        call = kite.place_order_calls[0]
        assert call["transaction_type"] == "SELL"
        assert call["order_type"] == "SL-M"
