"""Tests for v34_bridge_terminator.py.

Two layers: pure unit tests of Terminator in isolation, then a real-
engine integration class - constructing the actual, real
TradingEngineV34P02 with this real Terminator substituted for
FakeTerminator (make_stack() itself doesn't expose a terminator
override hook, so the stack is built inline here, mirroring make_stack's
own internals exactly rather than editing that shared, already-relied-on
test helper) - proving "terminator invoked during ordinary running" and
"terminator invoked while state persistence itself is failing" against
the real frozen engine, not just this module's own class in isolation.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from institutional_engine_v34_p02_multipos_candidate import PositionStatus, TradingEngineV34P02
from test_v34_p02_lifecycle_integration import make_context
from test_v34_p02_multipos_engine import (
    FakeAlert,
    FakeAudit,
    FakeBroker,
    FakeClock,
    FakeLock,
    FakeSleeper,
    cnc_position,
    flat_running_state,
    sample_ctx,
)
from v34_bridge_terminator import EngineHaltedError, Terminator
from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_broker_adapter import AuthorizerRegistryStore, KiteBrokerAdapterMultiPos
from v34_p02_state import Config, EngineStatus


class TestTerminatorUnit:
    def test_fresh_terminator_is_not_halted(self):
        t = Terminator()
        assert t.halted is False
        assert t.reason is None

    def test_halt_sets_both_fields_and_returns_none(self):
        t = Terminator()
        result = t.halt("test reason")
        assert result is None  # never raises, matching trigger_hard_halt()'s dependency on this
        assert t.halted is True
        assert t.reason == "test reason"

    def test_repeated_halt_overwrites_reason_last_write_wins(self):
        # Matches trigger_hard_halt()'s own policy on BotState.halt_reason
        # - unconditional overwrite, not "first reason wins".
        t = Terminator()
        t.halt("first reason")
        t.halt("second reason")
        assert t.reason == "second reason"
        assert t.halted is True

    def test_plain_attributes_are_directly_settable(self):
        # clear_halt_and_reconcile() does exactly this - hasattr-guarded
        # direct assignment, not a method call.
        t = Terminator()
        t.halt("reason")
        t.halted = False
        t.reason = None
        assert t.halted is False
        assert t.reason is None

    def test_raise_if_halted_does_nothing_when_not_halted(self):
        t = Terminator()
        t.raise_if_halted()  # must not raise

    def test_raise_if_halted_raises_with_the_recorded_reason(self):
        t = Terminator()
        t.halt("Reconciliation Failure: test")
        with pytest.raises(EngineHaltedError, match="Reconciliation Failure: test"):
            t.raise_if_halted()

    def test_raise_if_halted_has_a_fallback_message_if_reason_is_somehow_none(self):
        t = Terminator()
        t.halted = True  # bypassing halt() directly, reason left None
        with pytest.raises(EngineHaltedError, match="no reason recorded"):
            t.raise_if_halted()


class _FailableStore:
    """InMemoryStore (test_v34_p02_multipos_engine.py) with an injectable
    save() failure - needed to prove the terminator is never touched when
    trigger_hard_halt()'s own store.save() call (its first action, before
    audit/alert/terminator) fails first."""

    def __init__(self, initial):
        self._state = initial
        self.save_count = 0
        self.fail_next_save = False

    def load(self, _today=None):
        return self._state

    def save(self, state):
        if self.fail_next_save:
            raise OSError("simulated durable-state write failure")
        self._state = state
        self.save_count += 1


def _real_engine_with_real_terminator(*, state, store=None, raw_broker=None):
    raw_broker = raw_broker or FakeBroker()
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
    authorizer_store = AuthorizerRegistryStore(AuthorizerRegistry())
    context = make_context()
    adapter = KiteBrokerAdapterMultiPos(raw_broker=raw_broker, authorizer_store=authorizer_store, cfg=cfg, context_provider=lambda: context)
    store = store or _FailableStore(state)
    terminator = Terminator()  # the real thing, not FakeTerminator
    audit = FakeAudit()
    alert = FakeAlert()
    engine = TradingEngineV34P02(
        broker=adapter, clock=FakeClock(datetime(2026, 8, 15, 5, 0, tzinfo=timezone.utc)),
        sleeper=FakeSleeper(), store=store, audit=audit, alert=alert,
        lock=FakeLock(), terminator=terminator, cfg=cfg,
    )
    return engine, terminator, store


class TestTerminatorInsideARealEngine:
    def test_terminator_invoked_during_ordinary_running_via_a_real_hard_halt(self):
        # A real, definite (non-transient) failure during ordinary step()
        # dispatch - the real trigger_hard_halt() path, real Terminator.
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.EXIT_PENDING, exit_order_id="OID-1", filled_qty=50)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]

        def broken_get_order_details(order_id):
            raise ValueError("malformed broker response")
        raw_broker.get_order_details = broken_get_order_details

        engine, terminator, store = _real_engine_with_real_terminator(state=state, raw_broker=raw_broker)
        assert terminator.halted is False

        result = engine.step()  # must not raise - trigger_hard_halt() must return cleanly
        assert result == {"RELIANCE": "HALTED"}
        assert terminator.halted is True
        assert terminator.reason is not None
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT

    def test_terminator_is_never_touched_when_state_persistence_itself_fails(self):
        # trigger_hard_halt()'s FIRST action is store.save(self.state),
        # before audit/alert/terminator are ever reached. If that save()
        # fails, the whole call must propagate uncaught (v34_bridge_
        # botstate_store.py's own Phase 3.2 finding) and the terminator
        # must be left completely untouched - not halted, not partially
        # set - because execution never reached it.
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.EXIT_PENDING, exit_order_id="OID-1", filled_qty=50)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]

        def broken_get_order_details(order_id):
            raise ValueError("malformed broker response")
        raw_broker.get_order_details = broken_get_order_details

        engine, terminator, store = _real_engine_with_real_terminator(state=state, raw_broker=raw_broker)
        store.fail_next_save = True

        with pytest.raises(OSError, match="simulated durable-state write failure"):
            engine.step()

        assert terminator.halted is False  # trigger_hard_halt() never got past its own store.save()
        assert terminator.reason is None
