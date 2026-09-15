"""
V3.4 P0-2 Adversarial Integration Matrix
==========================================
RED-first acceptance suite. Production candidate is not modified here.

Scope:
- persistence failure before side effect
- malformed broker payloads
- multiple exact emergency-exit matches
- position/order disagreement
- startup reconciliation edge cases
- adapter timeout ambiguity propagation
- corrupted durable state
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
ENGINE_FILE = ROOT / "institutional_engine_v34_p01d_candidate.py"
RUNNER_FILE = ROOT / "run_production_p01d_candidate.py"

spec = importlib.util.spec_from_file_location("p02_engine", ENGINE_FILE)
engine_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine_mod
spec.loader.exec_module(engine_mod)

Config = engine_mod.Config
BotState = engine_mod.BotState
TradeContext = engine_mod.TradeContext
TradingEngineV34 = engine_mod.TradingEngineV34


@dataclass
class Clock:
    def now(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class Store:
    def __init__(self, fail_on_save_number=None, loaded=None):
        self.count = 0
        self.fail_on = fail_on_save_number
        self.loaded = loaded or BotState(trading_day="2026-08-12")
        self.saved = []

    def load(self, today):
        return self.loaded

    def save(self, state):
        self.count += 1
        if self.fail_on == self.count:
            raise RuntimeError("simulated disk I/O failure")
        self.saved.append(json.loads(json.dumps(state, default=str)))


class Lock:
    def acquire(self):
        return object()


class Audit:
    def log(self, *a, **k):
        pass


class Alert:
    def send(self, *a, **k):
        pass


class Terminator:
    def __init__(self):
        self.halted = False
        self.reason = None

    def halt(self, reason):
        self.halted = True
        self.reason = reason


MATCH = {
    "order_id": "EXIT-001",
    "exchange": "NSE",
    "tradingsymbol": "RELIANCE",
    "transaction_type": "SELL",
    "product": "MIS",
    "order_type": "SL-M",
    "quantity": 20,
    "trigger_price": "999.95",
    "market_protection": "-1",
    "tag": "V3.4_EXIT",
    "status": "OPEN",
}


def fp():
    return {
        "exchange": "NSE",
        "tradingsymbol": "RELIANCE",
        "transaction_type": "SELL",
        "product": "MIS",
        "order_type": "SL-M",
        "quantity": 20,
        "trigger_price": "999.95",
        "market_protection": "-1",
        "tag": "V3.4_EXIT",
    }


class Broker:
    def __init__(self, *, orders=None, position_qty=20, submit_exc=None, malformed_orders=False):
        self.orders_data = list(orders or [])
        self.position_qty = position_qty
        self.submit_exc = submit_exc
        self.malformed_orders = malformed_orders
        self.submit_calls = 0

    def get_positions(self):
        if self.position_qty == 0:
            return []
        return [{"tradingsymbol":"RELIANCE","exchange":"NSE","product":"MIS","quantity":self.position_qty}]

    def get_orders(self):
        if self.malformed_orders:
            return {"not": "a list"}
        return list(self.orders_data)

    def orders(self):
        return self.get_orders()

    def ltp(self, symbols):
        return {"NSE:RELIANCE": {"last_price": 1000.0}}

    def get_tick_size(self, symbol):
        return Decimal("0.05")

    def submit_emergency_exit(self, **kwargs):
        self.submit_calls += 1
        if self.submit_exc:
            raise self.submit_exc
        return "NEW-EXIT"

    def get_order_details(self, order_id):
        for o in self.orders_data:
            if str(o.get("order_id")) == str(order_id):
                return dict(o)
        raise RuntimeError("order not found")


def make_engine(broker, store=None):
    store = store or Store()
    term = Terminator()
    e = TradingEngineV34(
        broker=broker, clock=Clock(), sleeper=lambda _: None,
        store=store, audit=Audit(), alert=Alert(), lock=Lock(), terminator=term,
        cfg=Config(alert_webhook_url="", max_daily_loss=Decimal("2000"),
                   market_protection_pct=Decimal("-1"), observation_retry_budget=3),
    )
    e.state.status = "EXIT_SUBMIT"
    e.state.clearance_required = False
    e.state.active_trade = TradeContext(
        symbol="RELIANCE", entry_tag="V3.4_ENTRY", target_qty=20,
        tranche_qty=20, filled_qty=20, pending_qty=0, avg_entry_price=Decimal("1000"),
    )
    return e, store, term


def make_unknown(broker, store=None):
    e, store, term = make_engine(broker, store)
    e.state.status = "EXIT_UNKNOWN"
    e.state.active_trade.exit_submission_fingerprint = fp()
    return e, store, term


def test_P02_01_persistence_failure_blocks_side_effect():
    broker = Broker()
    # First save is the store-first EXIT_SUBMITTING persistence boundary.
    store = Store(fail_on_save_number=1)
    e, store, term = make_engine(broker, store)
    result = e.step()
    assert result == "HALTED"
    assert broker.submit_calls == 0
    assert term.halted
    assert e.state.status == "RECONCILIATION_HALT"


def test_P02_02_malformed_orders_payload_fails_closed_immediately():
    broker = Broker(malformed_orders=True)
    e, store, term = make_unknown(broker)
    result = e.step()
    assert result == "HALTED"
    assert e.state.status == "RECONCILIATION_HALT"
    assert term.halted
    assert broker.submit_calls == 0


def test_P02_03_multiple_exact_matches_halt_without_adoption():
    broker = Broker(orders=[dict(MATCH, order_id="EXIT-1"), dict(MATCH, order_id="EXIT-2")])
    e, store, term = make_unknown(broker)
    result = e.step()
    assert result == "HALTED"
    assert e.state.status == "RECONCILIATION_HALT"
    assert e.state.active_trade.exit_order_id is None
    assert broker.submit_calls == 0
    assert term.halted


def test_P02_04_complete_exit_with_residual_position_halts():
    broker = Broker(orders=[dict(MATCH, status="COMPLETE")], position_qty=1)
    e, store, term = make_unknown(broker)
    assert e.step() == "STATE_CHANGED"
    assert e.state.status == "EXIT_PENDING"
    # Recovered order is now independently verified against broker position.
    result = e.step()
    assert result == "HALTED"
    assert e.state.status == "RECONCILIATION_HALT"
    assert term.halted


def test_P02_05_flat_local_state_with_broker_order_halts_startup():
    broker = Broker(orders=[dict(MATCH)])
    store = Store(loaded=BotState(trading_day="2026-08-12", status="FLAT"))
    term = Terminator()
    e = TradingEngineV34(
        broker=broker, clock=Clock(), sleeper=lambda _: None,
        store=store, audit=Audit(), alert=Alert(), lock=Lock(), terminator=term,
        cfg=Config(alert_webhook_url="", max_daily_loss=Decimal("2000")),
    )
    e.reconcile_startup()
    assert e.state.status == "RECONCILIATION_HALT"
    assert term.halted


def test_P02_06_unknown_state_with_exact_order_recovers_without_submission():
    broker = Broker(orders=[MATCH])
    e, store, term = make_unknown(broker)
    assert e.step() == "STATE_CHANGED"
    assert e.state.status == "EXIT_PENDING"
    assert e.state.active_trade.exit_order_id == "EXIT-001"
    assert broker.submit_calls == 0


def test_P02_07_near_match_does_not_get_adopted_or_rewritten():
    near = dict(MATCH, quantity=19, order_id="NEAR-1")
    broker = Broker(orders=[near])
    e, store, term = make_unknown(broker)
    before = dict(e.state.active_trade.exit_submission_fingerprint)
    assert e.step() == "NO_ACTION"
    assert e.state.status == "EXIT_UNKNOWN"
    assert e.state.active_trade.exit_order_id is None
    assert e.state.active_trade.exit_submission_fingerprint == before
    assert broker.submit_calls == 0


def test_P02_08_timeout_from_adapter_preserves_ambiguous_side_effect():
    # Exercise the real candidate adapter's exception wrapping and engine
    # cause-chain classification without contacting Zerodha.
    if "kiteconnect" not in sys.modules:
        import types
        kc = types.ModuleType("kiteconnect")
        kc.KiteConnect = type("KiteConnect", (), {})
        sys.modules["kiteconnect"] = kc
    rspec = importlib.util.spec_from_file_location("p02_runner", RUNNER_FILE)
    runner = importlib.util.module_from_spec(rspec)
    sys.modules[rspec.name] = runner
    rspec.loader.exec_module(runner)

    class Kite:
        def place_order(self, **kwargs):
            raise TimeoutError("network timeout")

    adapter = runner.KiteBrokerAdapter(Kite(), live_trading=True)
    with pytest.raises(RuntimeError) as exc:
        adapter.submit_emergency_exit(
            symbol="RELIANCE", quantity=20, trigger_price=Decimal("999.95"),
            source_ltp=Decimal("1000"), tick_size=Decimal("0.05"),
            market_protection=Decimal("-1"), tag="V3.4_EXIT",
        )
    assert isinstance(exc.value.__cause__, TimeoutError)
    assert "UNKNOWN_OR_REJECTED" in str(exc.value)
    assert TradingEngineV34._is_transient_submission_exception(TradingEngineV34, exc.value)


def test_P02_09_corrupt_state_file_fails_closed():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        path.write_text("{not-json", encoding="utf-8")
        if "kiteconnect" not in sys.modules:
            import types
            kc = types.ModuleType("kiteconnect")
            kc.KiteConnect = type("KiteConnect", (), {})
            sys.modules["kiteconnect"] = kc
        rspec = importlib.util.spec_from_file_location("p02_runner_store", RUNNER_FILE)
        runner = importlib.util.module_from_spec(rspec)
        sys.modules[rspec.name] = runner
        rspec.loader.exec_module(runner)
        store = runner.JsonFileStore(str(path))
        with pytest.raises(RuntimeError, match="FAIL_CLOSED"):
            store.load("2026-08-12")


def test_P02_10_unknown_state_never_submits_after_budget_exhaustion():
    broker = Broker(orders=[])
    e, store, term = make_unknown(broker)
    for _ in range(4):
        e.step()
    assert e.state.status == "RECONCILIATION_HALT"
    assert term.halted
    assert broker.submit_calls == 0
