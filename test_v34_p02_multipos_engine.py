"""Tests for institutional_engine_v34_p02_multipos_candidate.py (P02-C).

Scope, per the P02-C priorities: startup/restart reconciliation first
(orphan detection, quantity/product mismatch, malformed/duplicate broker
records, provable-stale entry residue), whole-engine fail-closed semantics
(one ambiguous position halts everything, healthy siblings left
untouched), multi-position step() dispatch, and the ENTRY_LOCK/ENGINE_HALT
class separation. No CNC capital math (P02-D) and no real broker wiring
beyond a fully mocked adapter - LIVE_TRADING_ENABLED does not exist in
this module at all.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from institutional_engine_v34_p02_multipos_candidate import TradingEngineV34P02
from v34_p02_state import (
    BotState,
    Config,
    EngineStatus,
    EntryPolicyDeclinedError,
    PositionStatus,
    TradeContext,
)


# ---------------------------------------------------------------------------
# Test infrastructure - fully mocked, no real broker/network/filesystem.
# ---------------------------------------------------------------------------

class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class FakeSleeper:
    def sleep(self, seconds: float) -> None:
        pass


class FakeLock:
    def acquire(self):
        return object()


class FakeTerminator:
    def __init__(self):
        self.halted = False
        self.reason = None

    def halt(self, reason: str) -> None:
        self.halted = True
        self.reason = reason

    def raise_if_halted(self) -> None:
        # Mirrors the real Terminator's own method (v34_bridge_terminator.py,
        # Phase 3.4B) - added when v34_bridge_runner_entrypoint.run_forever()
        # (Phase 3.6) started calling it, so every existing caller of this
        # shared fixture gets the same interface the real class provides.
        if self.halted:
            raise RuntimeError(self.reason or "Engine is halted (no reason recorded).")


class FakeAudit:
    def __init__(self):
        self.events = []

    def log(self, event_type: str, **kwargs) -> None:
        self.events.append((event_type, kwargs))

    def has(self, event_type: str) -> bool:
        return any(e == event_type for e, _ in self.events)


class FakeAlert:
    def __init__(self):
        self.sent = []

    def send(self, level: str, message: str) -> None:
        self.sent.append((level, message))


class InMemoryStore:
    def __init__(self, initial: BotState):
        self._state = initial
        self.save_count = 0

    def load(self, _today=None) -> BotState:
        return self._state

    def save(self, state: BotState) -> None:
        self._state = state
        self.save_count += 1


class FakeBroker:
    """Configurable fake broker. Tests set .positions/.orders/.order_details
    directly; place_order/submit_emergency_exit are overridable per test via
    simple callables so submission behavior (including EntryPolicyDeclinedError)
    can be exercised without a real authorizer."""

    def __init__(self):
        self.positions = []
        self.orders = []
        self.order_details = {}
        self.quotes = {}
        self.tick_size = Decimal("0.05")
        self.place_order_fn = None
        self.submit_emergency_exit_fn = None
        self.place_order_calls = []

    def get_positions(self):
        return self.positions

    def get_orders(self):
        return self.orders

    def get_order_details(self, order_id):
        if order_id not in self.order_details:
            raise RuntimeError(f"no such order {order_id}")
        return self.order_details[order_id]

    def ltp(self, symbols):
        return self.quotes

    def get_tick_size(self, symbol):
        return self.tick_size

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        if self.place_order_fn is not None:
            return self.place_order_fn(**kwargs)
        return "ORD-DEFAULT"

    def submit_emergency_exit(self, **kwargs):
        if self.submit_emergency_exit_fn is not None:
            return self.submit_emergency_exit_fn(**kwargs)
        return "EXIT-ORD-DEFAULT"


IST_NOON = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)  # ~15:30 IST, still within session
MARKET_HOURS_UTC = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)  # 10:30 IST


def make_engine(*, state: BotState, broker: FakeBroker = None, cfg: Config = None, now: datetime = MARKET_HOURS_UTC):
    broker = broker or FakeBroker()
    cfg = cfg or Config(alert_webhook_url="https://example.invalid/hook")
    store = InMemoryStore(state)
    clock = FakeClock(now)
    terminator = FakeTerminator()
    audit = FakeAudit()
    alert = FakeAlert()
    engine = TradingEngineV34P02(
        broker=broker, clock=clock, sleeper=FakeSleeper(), store=store,
        audit=audit, alert=alert, lock=FakeLock(), terminator=terminator, cfg=cfg,
    )
    return engine, broker, store, terminator, audit


def flat_running_state(trading_day="2026-08-14") -> BotState:
    return BotState(trading_day=trading_day, status=EngineStatus.RUNNING)


def sample_ctx(symbol="RELIANCE", **overrides) -> TradeContext:
    defaults = dict(
        symbol=symbol, entry_tag="V3.4_P02_ENTRY", target_qty=10, tranche_qty=10,
        status=PositionStatus.MANAGING, filled_qty=10, pending_qty=0,
        entry_order_id="OID-1", entry_price=Decimal("2500.00"),
        order_status="COMPLETE", avg_entry_price=Decimal("2500.00"),
        opened_trading_day="2026-08-14",
    )
    defaults.update(overrides)
    return TradeContext(**defaults)


def cnc_position(symbol, qty, avg_price="2500.00", product="CNC"):
    return {"tradingsymbol": symbol, "exchange": "NSE", "product": product, "quantity": qty, "average_price": avg_price}


# ---------------------------------------------------------------------------
# Construction / basic halt semantics
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_persisted_reconciliation_halt_halts_the_terminator_immediately(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.RECONCILIATION_HALT, clearance_required=True, halt_reason="prior halt")
        engine, broker, store, terminator, audit = make_engine(state=state)
        assert terminator.halted is True

    def test_clean_flat_state_does_not_halt(self):
        engine, broker, store, terminator, audit = make_engine(state=flat_running_state())
        assert terminator.halted is False


# ---------------------------------------------------------------------------
# request_entry / request_exit
# ---------------------------------------------------------------------------

class TestRequestEntry:
    def test_happy_path_creates_a_new_position_without_touching_engine_status(self):
        engine, *_ = make_engine(state=flat_running_state())
        result = engine.request_entry(symbol="reliance", quantity=10, price=Decimal("2500"))
        assert result == "STATE_CHANGED"
        assert "RELIANCE" in engine.state.active_trades
        assert engine.state.status == EngineStatus.RUNNING  # untouched, per spec/plan

    def test_rejects_duplicate_symbol_already_held(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx()
        engine, *_ = make_engine(state=state)
        with pytest.raises(RuntimeError, match="SYMBOL_ALREADY_HELD"):
            engine.request_entry(symbol="RELIANCE", quantity=5, price=Decimal("2500"))

    def test_rejects_beyond_simultaneous_position_limit(self):
        cfg = Config(alert_webhook_url="x", max_simultaneous_positions=2)
        state = flat_running_state()
        state.active_trades["A"] = sample_ctx(symbol="A")
        state.active_trades["B"] = sample_ctx(symbol="B")
        engine, *_ = make_engine(state=state, cfg=cfg)
        with pytest.raises(RuntimeError, match="SIMULTANEOUS_POSITION_LIMIT"):
            engine.request_entry(symbol="C", quantity=1, price=Decimal("100"))

    def test_rejects_when_engine_is_not_running(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        engine, *_ = make_engine(state=state)
        with pytest.raises(RuntimeError, match="not RUNNING"):
            engine.request_entry(symbol="RELIANCE", quantity=1, price=Decimal("100"))

    def test_rejects_outside_market_hours(self):
        weekend = datetime(2026, 8, 15, 5, 0, 0, tzinfo=timezone.utc)  # a Saturday
        engine, *_ = make_engine(state=flat_running_state(), now=weekend)
        with pytest.raises(RuntimeError, match="MARKET_CLOSED"):
            engine.request_entry(symbol="RELIANCE", quantity=1, price=Decimal("100"))

    def test_returns_halted_when_terminator_already_halted(self):
        engine, broker, store, terminator, audit = make_engine(state=flat_running_state())
        terminator.halted = True
        assert engine.request_entry(symbol="RELIANCE", quantity=1, price=Decimal("100")) == "HALTED"


class TestRequestExit:
    def test_happy_path_from_managing_moves_to_exit_submit(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10)
        engine, *_ = make_engine(state=state)
        result = engine.request_exit(symbol="RELIANCE")
        assert result == "STATE_CHANGED"
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.EXIT_SUBMIT
        assert ctx.pending_qty == 10

    def test_rejects_unknown_symbol(self):
        engine, *_ = make_engine(state=flat_running_state())
        with pytest.raises(RuntimeError, match="no open position"):
            engine.request_exit(symbol="RELIANCE")

    def test_rejects_when_position_is_not_managing(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.ENTRY_PENDING)
        engine, *_ = make_engine(state=state)
        with pytest.raises(RuntimeError, match="not MANAGING"):
            engine.request_exit(symbol="RELIANCE")

    def test_is_unaffected_by_engine_halted_class_distinctions_other_than_engine_halt(self):
        # request_exit only checks ENGINE_HALT-class conditions - proven
        # properly in TestEntryLockClassSeparation below via a live
        # ENTRY_LOCK scenario; this test just pins that a healthy MANAGING
        # exit is not blocked by anything incidental.
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING)
        engine, *_ = make_engine(state=state)
        assert engine.request_exit(symbol="RELIANCE") == "STATE_CHANGED"


# ---------------------------------------------------------------------------
# reconcile_startup - the P02-C core
# ---------------------------------------------------------------------------

class TestReconciliationCleanCases:
    def test_clean_startup_with_zero_positions_reaches_running(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        engine, broker, store, terminator, audit = make_engine(state=state)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RUNNING
        assert terminator.halted is False

    def test_restart_with_three_positions_in_different_states_all_resolve_independently(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        # Position 1: ENTRY_SUBMIT, no order id yet, broker has zero activity -> stays as-is.
        state.active_trades["A"] = sample_ctx(symbol="A", status=PositionStatus.ENTRY_SUBMIT, entry_order_id=None, filled_qty=0, pending_qty=10)
        # Position 2: ENTRY_PENDING, broker order COMPLETE, filled -> should advance to PROTECTION.
        state.active_trades["B"] = sample_ctx(symbol="B", status=PositionStatus.ENTRY_PENDING, entry_order_id="OID-B", filled_qty=0, pending_qty=10, stop_order_id=None)
        # Position 3: already MANAGING with a matching broker position, no SL (no-mandatory-SL policy).
        state.active_trades["C"] = sample_ctx(symbol="C", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)

        broker = FakeBroker()
        broker.positions = [cnc_position("B", 10), cnc_position("C", 10)]
        broker.orders = []
        broker.order_details = {"OID-B": {"order_id": "OID-B", "status": "COMPLETE", "filled_quantity": 10, "quantity": 10, "average_price": "2500.00"}}

        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()

        assert engine.state.status == EngineStatus.RUNNING
        assert terminator.halted is False
        assert engine.state.active_trades["A"].status == PositionStatus.ENTRY_SUBMIT
        assert engine.state.active_trades["B"].status == PositionStatus.PROTECTION
        assert engine.state.active_trades["C"].status == PositionStatus.MANAGING

    def test_trading_day_rolls_forward_for_the_whole_portfolio_on_a_clean_restart(self):
        state = BotState(trading_day="2026-08-13", status=EngineStatus.STARTUP)
        state.active_trades["C"] = sample_ctx(symbol="C", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        broker = FakeBroker()
        broker.positions = [cnc_position("C", 10)]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker, now=MARKET_HOURS_UTC)
        engine.reconcile_startup()
        assert engine.state.trading_day == "2026-08-14"
        assert audit.has("TRADING_DAY_ROLLED_FORWARD")


class TestReconciliationOrphanAndMismatchDetection:
    def test_broker_orphan_position_with_no_local_trade_context_halts(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        broker = FakeBroker()
        broker.positions = [cnc_position("UNEXPECTED", 5)]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert terminator.halted is True
        assert "UNEXPECTED" in engine.state.halt_reason

    def test_broker_quantity_disagreement_halts(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        state.active_trades["C"] = sample_ctx(symbol="C", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        broker = FakeBroker()
        # Broker reports a different quantity for the same symbol/product -
        # _matching_positions requires exact match, so this simply looks
        # like "no matching position" (len != 1), correctly ambiguous.
        broker.positions = [cnc_position("C", 7)]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert terminator.halted is True

    def test_broker_product_mismatch_is_treated_as_no_matching_position_and_halts(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        state.active_trades["C"] = sample_ctx(symbol="C", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        broker = FakeBroker()
        # Same symbol/qty, wrong product (MIS instead of the engine's CNC) -
        # classify_positions ignores it entirely, so it's invisible to
        # active_pos and the position looks unexplained -> halt.
        broker.positions = [cnc_position("C", 10, product="MIS")]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert terminator.halted is True

    def test_malformed_broker_position_quantity_fails_closed(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        broker = FakeBroker()
        broker.positions = [{"tradingsymbol": "X", "product": "CNC", "quantity": "not-a-number"}]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert terminator.halted is True

    def test_duplicate_broker_position_records_for_the_same_symbol_halt(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        state.active_trades["C"] = sample_ctx(symbol="C", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        broker = FakeBroker()
        broker.positions = [cnc_position("C", 10), cnc_position("C", 10)]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert terminator.halted is True

    def test_orphan_active_order_with_no_local_trade_context_halts(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        broker = FakeBroker()
        broker.orders = [{"tradingsymbol": "UNEXPECTED", "status": "OPEN", "order_id": "X1"}]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert terminator.halted is True


class TestOneAmbiguousPositionHaltsWhileHealthySiblingsAreUntouched:
    def test_ambiguous_first_symbol_halts_before_touching_the_second(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.STARTUP)
        # Ambiguous: MANAGING but broker shows nothing for this symbol.
        state.active_trades["AMBIGUOUS"] = sample_ctx(symbol="AMBIGUOUS", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        # Healthy: MANAGING with a clean matching broker position.
        healthy_ctx = sample_ctx(symbol="HEALTHY", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        state.active_trades["HEALTHY"] = healthy_ctx

        broker = FakeBroker()
        broker.positions = [cnc_position("HEALTHY", 10)]  # nothing for AMBIGUOUS
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.reconcile_startup()

        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert terminator.halted is True
        # The healthy sibling, processed second, must be left exactly as
        # it was - not silently advanced, not touched at all - because
        # reconciliation stops at the first unresolved ambiguity.
        assert engine.state.active_trades["HEALTHY"] == healthy_ctx


# ---------------------------------------------------------------------------
# No-mandatory-SL policy
# ---------------------------------------------------------------------------

class TestNoMandatorySLPolicy:
    def test_protection_with_zero_matching_sl_orders_falls_through_to_managing(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.PROTECTION, filled_qty=10, avg_entry_price=Decimal("2500"))
        broker = FakeBroker()
        broker.orders = []  # no SL order anywhere
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)

        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.MANAGING
        assert ctx.stop_order_id is None
        assert terminator.halted is False
        assert audit.has("PROTECTION_NO_STOP_BY_DESIGN")

    def test_managing_with_no_stop_order_id_does_not_halt_and_refreshes_mtm(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10, avg_entry_price=Decimal("2500"), stop_order_id=None)
        broker = FakeBroker()
        broker.positions = [cnc_position("RELIANCE", 10)]
        broker.quotes = {"NSE:RELIANCE": {"last_price": 2550}}
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)

        result = engine.step()
        assert result["RELIANCE"] == "NO_ACTION"
        assert terminator.halted is False

    def test_protection_with_exactly_one_operator_placed_sl_is_adopted(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.PROTECTION, filled_qty=10, avg_entry_price=Decimal("2500"))
        broker = FakeBroker()
        broker.orders = [{
            "tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC",
            "tag": "V3.4_P02_SL", "transaction_type": "SELL", "status": "TRIGGER PENDING",
            "order_id": "SL-1", "quantity": 10,
        }]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.PROTECTION_PENDING
        assert ctx.stop_order_id == "SL-1"

    def test_protection_with_two_matching_sl_orders_is_ambiguous_and_halts(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.PROTECTION, filled_qty=10, avg_entry_price=Decimal("2500"))
        broker = FakeBroker()
        sl = {"tradingsymbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "tag": "V3.4_P02_SL", "transaction_type": "SELL", "status": "TRIGGER PENDING", "quantity": 10}
        broker.orders = [dict(sl, order_id="SL-1"), dict(sl, order_id="SL-2")]
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        result = engine.step()
        assert result["RELIANCE"] == "HALTED"
        assert terminator.halted is True


# ---------------------------------------------------------------------------
# ENTRY_LOCK vs ENGINE_HALT class separation (spec §2, test #19)
# ---------------------------------------------------------------------------

class TestEntryLockClassSeparation:
    def test_policy_decline_abandons_only_that_entry_and_does_not_halt_the_engine(self):
        state = flat_running_state()
        state.active_trades["HEALTHY"] = sample_ctx(symbol="HEALTHY", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        state.active_trades["PENDING"] = sample_ctx(symbol="PENDING", status=PositionStatus.ENTRY_SUBMIT, entry_order_id=None, filled_qty=0, pending_qty=5, target_qty=5, tranche_qty=5)

        broker = FakeBroker()
        broker.positions = [cnc_position("HEALTHY", 10)]
        broker.quotes = {"NSE:HEALTHY": {"last_price": 2550}}

        def declining_place_order(**kwargs):
            raise EntryPolicyDeclinedError("DAILY_HARD_HALT")
        broker.place_order_fn = declining_place_order

        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        result = engine.step()

        assert result["PENDING"] == "ENTRY_ABANDONED_POLICY_HALT"
        assert "PENDING" not in engine.state.active_trades
        assert terminator.halted is False
        assert engine.state.status == EngineStatus.RUNNING
        # The other, healthy position must be completely unaffected.
        assert result["HEALTHY"] == "NO_ACTION"
        assert "HEALTHY" in engine.state.active_trades
        assert audit.has("ENTRY_ABANDONED_POLICY_HALT")

    def test_request_exit_on_a_different_position_still_works_after_a_policy_decline(self):
        state = flat_running_state()
        state.active_trades["HEALTHY"] = sample_ctx(symbol="HEALTHY", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        state.active_trades["PENDING"] = sample_ctx(symbol="PENDING", status=PositionStatus.ENTRY_SUBMIT, entry_order_id=None, filled_qty=0, pending_qty=5, target_qty=5, tranche_qty=5)
        broker = FakeBroker()
        broker.positions = [cnc_position("HEALTHY", 10)]
        broker.quotes = {"NSE:HEALTHY": {"last_price": 2550}}
        broker.place_order_fn = lambda **kwargs: (_ for _ in ()).throw(EntryPolicyDeclinedError("ROLLING_WEEK_HALT"))
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        engine.step()
        assert engine.request_exit(symbol="HEALTHY") == "STATE_CHANGED"

    def test_a_genuine_broker_error_on_submission_still_hard_halts_the_engine(self):
        # Contrast case: a real integrity problem (not EntryPolicyDeclinedError)
        # must still be ENGINE_HALT-class, unchanged from before this class existed.
        state = flat_running_state()
        state.active_trades["PENDING"] = sample_ctx(symbol="PENDING", status=PositionStatus.ENTRY_SUBMIT, entry_order_id=None, filled_qty=0, pending_qty=5, target_qty=5, tranche_qty=5)
        broker = FakeBroker()
        broker.place_order_fn = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("broker contract violated"))
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        result = engine.step()
        assert result["PENDING"] == "HALTED"
        assert terminator.halted is True
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT


# ---------------------------------------------------------------------------
# clear_halt_and_reconcile - defers proof to reconcile_startup()
# ---------------------------------------------------------------------------

class TestClearHaltAndReconcile:
    def test_requires_a_nonempty_operator_note(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.RECONCILIATION_HALT, clearance_required=True, halt_reason="x")
        engine, broker, store, terminator, audit = make_engine(state=state)
        assert engine.clear_halt_and_reconcile("") is False
        assert engine.state.clearance_required is True

    def test_rejects_when_broker_is_unreachable(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.RECONCILIATION_HALT, clearance_required=True, halt_reason="x")
        broker = FakeBroker()

        def boom():
            raise RuntimeError("network down")
        broker.get_positions = boom
        engine, broker, store, terminator, audit = make_engine(state=state, broker=broker)
        assert engine.clear_halt_and_reconcile("operator verified manually") is False
        assert engine.state.clearance_required is True

    def test_clears_the_durable_latch_and_defers_actual_proof_to_the_next_startup_cycle(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.RECONCILIATION_HALT, clearance_required=True, halt_reason="x")
        engine, broker, store, terminator, audit = make_engine(state=state)
        assert engine.clear_halt_and_reconcile("operator verified manually") is True
        assert engine.state.clearance_required is False
        assert engine.state.status == EngineStatus.STARTUP
        assert terminator.halted is False

        # The clearance itself did not "decide" clean - it only reset the
        # latch. If the broker turns out to still be inconsistent, the very
        # next reconcile_startup() call halts again, exactly as any other
        # startup would.
        broker.positions = [cnc_position("ORPHAN", 5)]
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
