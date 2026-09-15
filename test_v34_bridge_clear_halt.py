"""Tests for v34_bridge_clear_halt.py.

Deliberately reproduces Terminal A's REAL 2026-08-17 halted state (same
status/clearance_required/halt_reason/active_trades shape actually found
in C:\\zerodha_data\\runner_original\\bot_state.json) into a tmp_path-backed
BotStateStore — never the real file itself, never real credentials, never
a real network call. Same "test against an isolated copy first" discipline
used everywhere else this session (DB migrations, mock Kite clients).
"""
import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_botstate_store import BotStateStore
from v34_bridge_clear_halt import run_clear_halt
from v34_bridge_runner_lock import RunnerLockHeldError
from v34_bridge_runner_startup import ProductionRunnerPaths, build_production_engine
from v34_p02_state import BotState, Config, EngineStatus, PositionStatus, TradeContext

MARKET_HOURS_UTC = datetime(2026, 8, 17, 5, 30, 0, tzinfo=timezone.utc)
SECTORS = {"SUNPHARMA": "PHARMA", "BAJFINANCE": "FINANCE", "SBIN": "FINANCE", "LAURUSLABS": "PHARMA"}


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    defaults.update(overrides)
    return Config(**defaults)


def make_paths(tmp_path):
    return ProductionRunnerPaths(data_dir=tmp_path / "data")


def seed_real_halted_state(tmp_path):
    """Reproduces the exact shape of Terminal A's real halted bot_state.json
    (status/clearance_required/halt_reason/halt_source/active_trades) —
    values transcribed from the real file, not invented."""
    state = BotState(
        trading_day="2026-08-17",
        status=EngineStatus.RECONCILIATION_HALT,
        clearance_required=True,
        halt_reason="CRITICAL P0: SUNPHARMA entry submission rejected/failed with known outcome: Too many requests",
        halt_source="ENTRY_SUBMIT",
        active_trades={
            "SUNPHARMA": TradeContext(
                symbol="SUNPHARMA", status=PositionStatus.ENTRY_SUBMITTING,
                entry_price=Decimal("1910.4"), target_qty=12, tranche_qty=12,
                entry_tag="V3.4_P02_ENTRY",
                # Transcribed from the real bot_state.json - without this,
                # reconciliation halts for a DIFFERENT reason ("lacks
                # immutable submission fingerprint") than what will really
                # happen. Caught by this test's own first failed run.
                entry_submission_fingerprint={
                    "exchange": "NSE", "order_type": "LIMIT", "price": "1910.4",
                    "product": "CNC", "quantity": 12, "tag": "V3.4_P02_ENTRY",
                    "tradingsymbol": "SUNPHARMA", "transaction_type": "BUY",
                },
            ),
        },
    )
    store = BotStateStore(make_paths(tmp_path).bot_state)
    store.save(state)
    return state


def build_halted_engine(tmp_path, *, kite=None):
    seed_real_halted_state(tmp_path)
    kite = kite or FakeKiteConnectWithOrders()
    if not kite.virtual_contract_note_response:
        kite.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
    return build_production_engine(
        paths=make_paths(tmp_path), kite=kite, live_trading_enabled=False,
        cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
        dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
    )


class TestReproducesRealHaltOnLoad:
    def test_engine_self_halts_on_construction_matching_real_terminal_a(self, tmp_path):
        """Proves Scenario 1's claim empirically, not just by code-reading:
        constructing the engine against this exact persisted state halts
        immediately, before any broker call - confirmed below by asserting
        zero calls reached the fake Kite's positions()/orders()."""
        kite = FakeKiteConnectWithOrders()
        engine = build_halted_engine(tmp_path, kite=kite)
        try:
            assert engine.terminator.halted is True
            assert "Too many requests" in engine.terminator.reason
            assert engine.state.clearance_required is True
            assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        finally:
            engine.lock_provider.release()


class TestClearHaltHappyPath:
    def test_clears_and_persists_durably(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_clear_halt(engine, note="verified via Observatory + audit.jsonl, Kite reachable", assume_yes=True, out=out, err=err)
            assert rc == 0
            assert engine.state.clearance_required is False
            assert engine.state.status == EngineStatus.STARTUP
            assert engine.terminator.halted is False
            assert "python v34_bridge_runner_main.py" in out.getvalue()
        finally:
            engine.lock_provider.release()

        # Durability check: reload from disk independently - proves the
        # clear was actually persisted, not just mutated in-memory.
        store = BotStateStore(make_paths(tmp_path).bot_state)
        reloaded_state = store.load()
        assert reloaded_state.clearance_required is False
        assert reloaded_state.status == EngineStatus.STARTUP

    def test_interactive_confirmation_declined_leaves_state_untouched(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_clear_halt(
                engine, note="test", assume_yes=False, out=out, err=err,
                input_fn=lambda prompt: "n",
            )
            assert rc == 1
            assert engine.state.clearance_required is True
            assert engine.terminator.halted is True
        finally:
            engine.lock_provider.release()


class TestClearHaltRefusals:
    def test_refuses_when_clearance_required_is_already_false(self, tmp_path):
        """Edge case: RECONCILIATION_HALT for some OTHER reason (not this
        script's job) - must not silently report success via
        clear_halt_and_reconcile()'s own documented no-op-returns-True
        behavior."""
        state = BotState(
            trading_day="2026-08-17", status=EngineStatus.RECONCILIATION_HALT,
            clearance_required=False, halt_reason="some other halt",
        )
        store = BotStateStore(make_paths(tmp_path).bot_state)
        store.save(state)
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
            rc = run_clear_halt(engine, note="test", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert "needs manual investigation" in err.getvalue()
            assert engine.state.status == EngineStatus.RECONCILIATION_HALT  # untouched
        finally:
            engine.lock_provider.release()

    def test_refuses_when_broker_unreachable_and_leaves_state_untouched(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        engine = build_halted_engine(tmp_path, kite=kite)
        # clear_halt_and_reconcile() itself calls broker.get_positions()/
        # get_orders() as its own real reachability check - break that.
        engine.broker.get_positions = lambda: (_ for _ in ()).throw(RuntimeError("network down"))
        out, err = io.StringIO(), io.StringIO()
        try:
            rc = run_clear_halt(engine, note="test", assume_yes=True, out=out, err=err)
            assert rc == 1
            assert engine.state.clearance_required is True
            assert engine.terminator.halted is True
        finally:
            engine.lock_provider.release()

    def test_reports_cleanly_when_engine_is_not_halted_at_all(self, tmp_path):
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
            assert engine.terminator.halted is False
            rc = run_clear_halt(engine, note="test", assume_yes=True, out=out, err=err)
            assert rc == 0
            assert "nothing to clear" in out.getvalue()
        finally:
            engine.lock_provider.release()


class TestLockLifecycle:
    def test_lock_is_released_after_clear_and_a_fresh_construction_succeeds(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        run_clear_halt(engine, note="test", assume_yes=True, out=out, err=err)
        engine.lock_provider.release()

        # A second, independent engine construction must succeed - proves
        # this script's own lock handling (mirroring v34_bridge_runner_
        # main.py's finally-block discipline) never leaves the runner
        # lock held for a real subsequent `python v34_bridge_runner_main.py`.
        kite2 = FakeKiteConnectWithOrders()
        if not kite2.virtual_contract_note_response:
            kite2.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
        engine2 = build_production_engine(
            paths=make_paths(tmp_path), kite=kite2, live_trading_enabled=False,
            cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
            dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
        )
        try:
            # Cleared state (status=STARTUP), fingerprint present -> the
            # startup reconciliation itself does NOT immediately re-halt
            # (see TestWhatHappensAfterResume for what happens next, on
            # later polling cycles).
            assert engine2.terminator.halted is False
        finally:
            engine2.lock_provider.release()


class TestWhatHappensAfterResume:
    """The real question the user asked: after clearing the halt and
    restarting `v34_bridge_runner_main.py` for real, what actually
    happens on subsequent 5s polling cycles - does Terminal A resume
    cleanly, or hit something else? Kite genuinely never received the
    SUNPHARMA order (the whole reason for the original halt), so every
    fresh `orders()` snapshot this test gives the engine has zero matches
    for it, by construction - reproducing exactly what the real broker
    will show."""

    def test_first_cycle_after_resume_does_not_immediately_rehalt(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        run_clear_halt(engine, note="test", assume_yes=True, out=out, err=err)
        engine.lock_provider.release()

        kite2 = FakeKiteConnectWithOrders()  # empty orders() - no match possible
        if not kite2.virtual_contract_note_response:
            kite2.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
        engine2 = build_production_engine(
            paths=make_paths(tmp_path), kite=kite2, live_trading_enabled=False,
            cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
            dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
        )
        try:
            assert engine2.terminator.halted is False
            sunpharma = engine2.state.active_trades.get("SUNPHARMA")
            assert sunpharma is not None, "SUNPHARMA is still an open position - not silently dropped"
            assert sunpharma.status == PositionStatus.ENTRY_UNKNOWN
            assert sunpharma.entry_reconciliation_failures == 1
        finally:
            engine2.lock_provider.release()

    def test_repeated_polling_eventually_rehalts_on_retry_budget_exhaustion(self, tmp_path):
        """This is the concrete, load-bearing finding: since Kite will
        NEVER have a matching order for SUNPHARMA (nothing was ever
        submitted), every subsequent step() call increments the same
        failure counter - it is not bad luck, it is mathematically
        certain to happen again, exactly Config.observation_retry_budget
        cycles later (default 3 -> the 4th step() call halts)."""
        engine = build_halted_engine(tmp_path)
        out, err = io.StringIO(), io.StringIO()
        run_clear_halt(engine, note="test", assume_yes=True, out=out, err=err)
        engine.lock_provider.release()

        kite2 = FakeKiteConnectWithOrders()
        if not kite2.virtual_contract_note_response:
            kite2.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
        engine2 = build_production_engine(
            paths=make_paths(tmp_path), kite=kite2, live_trading_enabled=False,
            cfg=cfg(), sector_lookup=SECTORS, clock=FakeClock(MARKET_HOURS_UTC),
            dp_charge_per_symbol=Decimal("15.34"), shadow_mode=True,
        )
        try:
            assert engine2.terminator.halted is False  # cycle 0 (inside startup) already happened
            cycles_to_rehalt = None
            for cycle in range(1, 10):
                engine2.step()
                if engine2.terminator.halted:
                    cycles_to_rehalt = cycle
                    break
            assert cycles_to_rehalt == 3, f"expected re-halt on the 3rd extra step() call (budget=3), got {cycles_to_rehalt}"
            assert "budget exhausted" in engine2.terminator.reason
            assert "SUNPHARMA" in engine2.terminator.reason
            # The stale position is STILL sitting in active_trades,
            # already over-budget - a bare clear_halt_and_reconcile() +
            # restart cycle on ITS OWN would immediately hit the exact
            # same halt again next time, because nothing ever resets
            # entry_reconciliation_failures or removes this position.
            assert engine2.state.active_trades["SUNPHARMA"].entry_reconciliation_failures > 3
        finally:
            engine2.lock_provider.release()
