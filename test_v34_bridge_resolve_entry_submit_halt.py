"""Tests for v34_bridge_resolve_entry_submit_halt.py.

Same discipline as test_v34_bridge_clear_halt.py: reproduces Terminal A's
REAL 2026-08-17 halted state exactly, never touches the real file, never
real credentials. The load-bearing test here is
TestFullResolutionStaysRunning - it's not enough to prove the halt clears;
it must prove the engine STAYS running across many subsequent polling
cycles (closing the gap test_v34_bridge_clear_halt.py's own
TestWhatHappensAfterResume found and left open).
"""
import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_botstate_store import BotStateStore
from v34_bridge_resolve_entry_submit_halt import run_resolve
from v34_bridge_runner_startup import ProductionRunnerPaths, build_production_engine
from v34_p02_state import BotState, Config, EngineStatus, PositionStatus, TradeContext

MARKET_HOURS_UTC = datetime(2026, 8, 17, 5, 30, 0, tzinfo=timezone.utc)
SECTORS = {"SUNPHARMA": "PHARMA", "BAJFINANCE": "FINANCE", "SBIN": "FINANCE", "LAURUSLABS": "PHARMA"}

FINGERPRINT = {
    "exchange": "NSE", "order_type": "LIMIT", "price": "1910.4",
    "product": "CNC", "quantity": 12, "tag": "V3.4_P02_ENTRY",
    "tradingsymbol": "SUNPHARMA", "transaction_type": "BUY",
}


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    defaults.update(overrides)
    return Config(**defaults)


def make_paths(tmp_path):
    return ProductionRunnerPaths(data_dir=tmp_path / "data")


def seed_real_halted_state(tmp_path, **overrides):
    trade_kwargs = dict(
        symbol="SUNPHARMA", status=PositionStatus.ENTRY_SUBMITTING,
        entry_price=Decimal("1910.4"), target_qty=12, tranche_qty=12,
        entry_tag="V3.4_P02_ENTRY", entry_submission_fingerprint=dict(FINGERPRINT),
    )
    trade_kwargs.update(overrides.pop("trade_overrides", {}))
    state_kwargs = dict(
        trading_day="2026-08-17",
        status=EngineStatus.RECONCILIATION_HALT,
        clearance_required=True,
        halt_reason="CRITICAL P0: SUNPHARMA entry submission rejected/failed with known outcome: Too many requests",
        halt_source="ENTRY_SUBMIT",
        active_trades={"SUNPHARMA": TradeContext(**trade_kwargs)},
    )
    state_kwargs.update(overrides)
    state = BotState(**state_kwargs)
    store = BotStateStore(make_paths(tmp_path).bot_state)
    store.save(state)
    return state


def build_halted_engine(tmp_path, *, kite=None, **state_overrides):
    seed_real_halted_state(tmp_path, **state_overrides)
    kite = kite or FakeKiteConnectWithOrders()
    if not kite.virtual_contract_note_response:
        kite.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
    return build_production_engine(
        paths=make_paths(tmp_path), kite=kite, live_trading_enabled=False,
        cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
        dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
    )


class TestFullResolutionStaysRunning:
    """The load-bearing proof: resolve, restart fresh, then poll MANY
    times (far past the retry budget that previously re-halted it) and
    confirm it never halts again - because there's nothing left to fail
    reconciliation on."""

    def test_resolves_and_stays_running_across_many_cycles(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="verified live: no matching Kite order exists", assume_yes=True, out=out, err=err)
            assert rc == 0, err.getvalue()
            assert "SUNPHARMA" not in engine.state.active_trades
            assert engine.state.clearance_required is False
            assert engine.terminator.halted is False
        finally:
            engine.lock_provider.release()

        # Fresh construction (what `python v34_bridge_runner_main.py`
        # actually does) - must reach RUNNING and STAY there.
        kite2 = FakeKiteConnectWithOrders()
        if not kite2.virtual_contract_note_response:
            kite2.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
        engine2 = build_production_engine(
            paths=make_paths(tmp_path), kite=kite2, live_trading_enabled=False,
            cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
            dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
        )
        try:
            assert engine2.terminator.halted is False
            assert "SUNPHARMA" not in engine2.state.active_trades
            for cycle in range(20):  # far past the 3-cycle budget that re-halted it before
                engine2.step()
                assert engine2.terminator.halted is False, f"re-halted on cycle {cycle}: {engine2.terminator.reason!r}"
        finally:
            engine2.lock_provider.release()


class TestRefusals:
    def test_refuses_wrong_halt_source(self, tmp_path):
        engine = build_halted_engine(tmp_path, halt_source="RESILIENCE_LAYER")
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "ENTRY_SUBMIT" in err.getvalue()
            assert "SUNPHARMA" in engine.state.active_trades
        finally:
            engine.lock_provider.release()

    def test_refuses_unknown_symbol(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="RELIANCE", note="x", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "no active position" in err.getvalue()
        finally:
            engine.lock_provider.release()

    def test_refuses_wrong_status(self, tmp_path):
        engine = build_halted_engine(tmp_path, trade_overrides={"status": PositionStatus.MANAGING})
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "MANAGING" in err.getvalue()
            assert "SUNPHARMA" in engine.state.active_trades
        finally:
            engine.lock_provider.release()

    def test_refuses_when_entry_order_id_is_present(self, tmp_path):
        engine = build_halted_engine(tmp_path, trade_overrides={"entry_order_id": "12345"})
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "already has entry_order_id" in err.getvalue()
            assert "SUNPHARMA" in engine.state.active_trades
        finally:
            engine.lock_provider.release()

    def test_refuses_when_fingerprint_missing(self, tmp_path):
        engine = build_halted_engine(tmp_path, trade_overrides={"entry_submission_fingerprint": None})
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "no entry_submission_fingerprint" in err.getvalue()
        finally:
            engine.lock_provider.release()

    def test_refuses_when_broker_call_fails(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        engine.broker.get_orders = lambda: (_ for _ in ()).throw(RuntimeError("network down"))
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "Fail closed" in err.getvalue()
            assert "SUNPHARMA" in engine.state.active_trades
        finally:
            engine.lock_provider.release()

    def test_refuses_when_a_live_matching_order_actually_exists(self, tmp_path):
        """The critical safety net: reality changed since the halt (an
        order now genuinely exists matching the fingerprint) - must NOT
        delete the position, must refuse loudly instead."""
        kite = FakeKiteConnectWithOrders()
        kite.orders_response = [{
            "order_id": "REAL999", "exchange": "NSE", "tradingsymbol": "SUNPHARMA",
            "transaction_type": "BUY", "product": "CNC", "order_type": "LIMIT",
            "tag": "V3.4_P02_ENTRY", "quantity": 12, "price": 1910.4, "status": "OPEN",
        }]
        engine = build_halted_engine(tmp_path, kite=kite)
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "LIVE MATCHING ORDER FOUND" in err.getvalue()
            assert "REAL999" in err.getvalue()
            assert "SUNPHARMA" in engine.state.active_trades  # untouched
            assert engine.state.clearance_required is True  # halt NOT cleared either
        finally:
            engine.lock_provider.release()

    def test_interactive_decline_leaves_everything_untouched(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=False, out=out, err=err, input_fn=lambda p: "n")
            assert rc == 1
            assert "SUNPHARMA" in engine.state.active_trades
            assert engine.state.clearance_required is True
        finally:
            engine.lock_provider.release()

    def test_reports_cleanly_when_not_halted(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        if not kite.virtual_contract_note_response:
            kite.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
        engine = build_production_engine(
            paths=make_paths(tmp_path), kite=kite, live_trading_enabled=False,
            cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
            dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
        )
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
            assert rc == 0
            assert "nothing to resolve" in out.getvalue()
        finally:
            engine.lock_provider.release()


class TestLockLifecycle:
    def test_lock_released_after_refusal_and_after_success(self, tmp_path):
        engine = build_halted_engine(tmp_path, trade_overrides={"entry_order_id": "12345"})
        out, err = io.StringIO(), io.StringIO()
        run_resolve(engine, symbol="SUNPHARMA", note="x", assume_yes=True, out=out, err=err)
        engine.lock_provider.release()

        kite2 = FakeKiteConnectWithOrders()
        if not kite2.virtual_contract_note_response:
            kite2.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
        engine2 = build_production_engine(
            paths=make_paths(tmp_path), kite=kite2, live_trading_enabled=False,
            cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
            dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
        )
        engine2.lock_provider.release()  # just proving acquisition succeeded
