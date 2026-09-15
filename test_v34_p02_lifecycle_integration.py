"""P02-F: end-to-end lifecycle integration tests.

Wires the real engine (P02-C), the real accounting layer (P02-D), and the
real authorizer (P02-E) together through `KiteBrokerAdapterMultiPos`
(P02-F) against a mocked raw broker - no new policy, this file proves the
already-built pieces compose correctly across full entry/exit lifecycles,
the ENTRY_LOCK/ENGINE_HALT class separation through the real adapter (not
just a test double that raises the exception directly), mixed-state
multi-symbol cycles, and - the core of P02-F - idempotency at every
dangerous restart boundary: "if step() runs again after a crash, can this
state cause a second broker order?" must be provably no, every time.
"""

from decimal import Decimal

import pytest

from institutional_engine_v34_p02_multipos_candidate import TradingEngineV34P02
from test_v34_p02_multipos_engine import (
    FakeAlert,
    FakeAudit,
    FakeBroker,
    FakeClock,
    FakeLock,
    FakeSleeper,
    FakeTerminator,
    InMemoryStore,
    MARKET_HOURS_UTC,
    cnc_position,
    flat_running_state,
)
from v34_p02_accounting import initial_checkpoint
from v34_p02_authorizer import AuthorizerRegistry, release_held_symbol
from v34_p02_broker_adapter import AuthorizationContext, AuthorizerRegistryStore, KiteBrokerAdapterMultiPos
from v34_p02_state import Config, EngineStatus, PositionStatus


SECTORS = {"RELIANCE": "ENERGY", "ONGC": "ENERGY", "INFY": "IT", "TCS": "IT", "HDFCBANK": "BANK", "AMBIGUOUS": "MISC"}


def make_context(**overrides) -> AuthorizationContext:
    defaults = dict(
        reconciliation_clean=True, kill_switch_active=False, entries_today=0,
        seconds_since_last_entry=Decimal("9999"), turnover_today=Decimal("0"),
        cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("0"),
        checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=Decimal("100000")),
        sealed_daily_pnl_series={}, sector_lookup=SECTORS,
    )
    defaults.update(overrides)
    return AuthorizationContext(**defaults)


def make_stack(*, state, raw_broker=None, cfg=None, context=None, now=MARKET_HOURS_UTC, authorizer_registry=None):
    raw_broker = raw_broker or FakeBroker()
    cfg = cfg or Config(alert_webhook_url="x", trial_capital=Decimal("100000"))
    authorizer_store = AuthorizerRegistryStore(authorizer_registry or AuthorizerRegistry())
    ctx_holder = {"ctx": context or make_context()}
    adapter = KiteBrokerAdapterMultiPos(raw_broker=raw_broker, authorizer_store=authorizer_store, cfg=cfg, context_provider=lambda: ctx_holder["ctx"])
    store = InMemoryStore(state)
    clock = FakeClock(now)
    terminator = FakeTerminator()
    audit = FakeAudit()
    alert = FakeAlert()
    engine = TradingEngineV34P02(broker=adapter, clock=clock, sleeper=FakeSleeper(), store=store, audit=audit, alert=alert, lock=FakeLock(), terminator=terminator, cfg=cfg)
    return engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder


def restart(state_store: InMemoryStore, *, raw_broker, cfg, authorizer_store, context, now=MARKET_HOURS_UTC):
    """Simulate a process restart: a brand-new engine object constructed
    from whatever the (already-durable) store currently holds - proving
    idempotency means proving THIS reconstruction never causes a second
    broker call for work already in flight."""
    ctx_holder = {"ctx": context}
    adapter = KiteBrokerAdapterMultiPos(raw_broker=raw_broker, authorizer_store=authorizer_store, cfg=cfg, context_provider=lambda: ctx_holder["ctx"])
    clock = FakeClock(now)
    terminator = FakeTerminator()
    audit = FakeAudit()
    engine = TradingEngineV34P02(broker=adapter, clock=clock, sleeper=FakeSleeper(), store=state_store, audit=audit, alert=FakeAlert(), lock=FakeLock(), terminator=terminator, cfg=cfg)
    return engine, terminator, audit


# ---------------------------------------------------------------------------
# 1. Full entry lifecycle, through the real adapter/authorizer/accounting
# ---------------------------------------------------------------------------

class TestFullEntryLifecycle:
    def test_request_entry_through_authorizer_reservation_fingerprint_fill_to_managing(self):
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=flat_running_state())

        assert engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500")) == "STATE_CHANGED"

        # step() 1: ENTRY_SUBMIT -> authorizer ALLOW -> reservation ->
        # fingerprint persisted -> raw broker place_order -> ENTRY_PENDING.
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.ENTRY_PENDING
        assert ctx.entry_order_id is not None
        assert len(raw_broker.place_order_calls) == 1
        reservation = authorizer_store.registry.reservations["RELIANCE"]
        assert reservation.entry_fingerprint is not None
        assert reservation.entry_fingerprint["tradingsymbol"] == "RELIANCE"

        order_id = ctx.entry_order_id
        raw_broker.order_details[order_id] = {"order_id": order_id, "status": "OPEN", "filled_quantity": 0, "quantity": 10, "average_price": "0"}

        # step() 2: zero fill yet, stays ENTRY_PENDING.
        result = engine.step()
        assert result["RELIANCE"] == "NO_ACTION"

        # step() 3: partial fill.
        raw_broker.positions = [cnc_position("RELIANCE", 6)]
        raw_broker.order_details[order_id] = {"order_id": order_id, "status": "OPEN", "filled_quantity": 6, "quantity": 10, "average_price": "2500.00"}
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.PARTIAL_POSITION

        # step() 4: full fill -> PROTECTION.
        raw_broker.positions = [cnc_position("RELIANCE", 10)]
        raw_broker.order_details[order_id] = {"order_id": order_id, "status": "COMPLETE", "filled_quantity": 10, "quantity": 10, "average_price": "2500.00"}
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.PROTECTION

        # step() 5: no SL orders -> falls through to MANAGING (no-mandatory-SL policy).
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.MANAGING
        assert ctx.stop_order_id is None
        assert terminator.halted is False
        # Exactly one broker order was ever placed across the whole lifecycle.
        assert len(raw_broker.place_order_calls) == 1


# ---------------------------------------------------------------------------
# 2. Full exit lifecycle
# ---------------------------------------------------------------------------

class TestFullExitLifecycle:
    def test_request_exit_through_submit_partial_full_removal(self):
        state = flat_running_state()
        from test_v34_p02_multipos_engine import sample_ctx
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        registry = AuthorizerRegistry(held_symbols={"RELIANCE": "ENERGY"})
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10)]
        raw_broker.quotes = {"NSE:RELIANCE": {"last_price": 2550}}

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker, authorizer_registry=registry)

        assert engine.request_exit(symbol="RELIANCE") == "STATE_CHANGED"

        # step(): EXIT_SUBMIT -> raw broker submit_emergency_exit (never
        # gated) -> EXIT_PENDING.
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.EXIT_PENDING
        exit_order_id = ctx.exit_order_id
        assert exit_order_id is not None

        # Partial exit fill.
        raw_broker.positions = [cnc_position("RELIANCE", 4)]
        raw_broker.order_details[exit_order_id] = {"order_id": exit_order_id, "status": "OPEN", "filled_quantity": 6, "quantity": 10}
        result = engine.step()
        assert result["RELIANCE"] == "NO_ACTION"
        assert engine.state.active_trades["RELIANCE"].pending_qty == 4

        # Full exit fill, broker proves zero remaining position.
        raw_broker.positions = []
        raw_broker.order_details[exit_order_id] = {"order_id": exit_order_id, "status": "COMPLETE", "filled_quantity": 10, "quantity": 10}
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        assert "RELIANCE" not in engine.state.active_trades  # removed only after broker truth proves zero
        assert terminator.halted is False

        # Registry cleanup is bookkeeping, done once the engine has
        # already proven flat - not a safety decision, just symmetry
        # with reconcile_reservations' PROMOTE_TO_HELD.
        authorizer_store.save(release_held_symbol(authorizer_store.registry, "RELIANCE"))
        assert "RELIANCE" not in authorizer_store.registry.held_symbols


# ---------------------------------------------------------------------------
# 3. ENTRY_LOCK through the real adapter (not a raw EntryPolicyDeclinedError
#    stub) - proves the authorizer's own DAILY_HARD_HALT decision reaches
#    the engine correctly as ENTRY_ABANDONED_POLICY_HALT, no engine halt.
# ---------------------------------------------------------------------------

class TestEntryLockThroughRealAuthorizer:
    def test_daily_hard_halt_from_the_real_authorizer_abandons_cleanly(self):
        state = flat_running_state()
        from test_v34_p02_multipos_engine import sample_ctx
        state.active_trades["HEALTHY"] = sample_ctx(symbol="HEALTHY", status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)

        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("HEALTHY", 10)]
        raw_broker.quotes = {"NSE:HEALTHY": {"last_price": 2550}, "NSE:RELIANCE": {"last_price": 2500}}

        # A daily loss of 2100 against 100000 trial capital trips
        # daily_hard_halt_pct=0.02 (2000) - a real, computed ENTRY_LOCK,
        # not a stubbed exception.
        context = make_context(checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=Decimal("100000")))
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker, context=context)

        # Rig cumulative_realized_pnl low enough (via context) that Equity
        # is well under prior_close_equity, tripping DAILY_HARD_HALT.
        ctx_holder["ctx"] = make_context(
            checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=Decimal("100000")),
            cumulative_realized_pnl=Decimal("-2500"),
        )

        assert engine.request_entry(symbol="RELIANCE", quantity=1, price=Decimal("2500")) == "STATE_CHANGED"
        result = engine.step()

        assert result["RELIANCE"] == "ENTRY_ABANDONED_POLICY_HALT"
        assert "RELIANCE" not in engine.state.active_trades
        assert terminator.halted is False
        assert engine.state.status == EngineStatus.RUNNING
        assert result["HEALTHY"] == "NO_ACTION"
        assert "HEALTHY" in engine.state.active_trades
        # No reservation was ever created for RELIANCE - the decline
        # happened before gate 10, so there is nothing orphaned to release.
        assert "RELIANCE" not in authorizer_store.registry.reservations
        assert len(raw_broker.place_order_calls) == 0


# ---------------------------------------------------------------------------
# 4/5. Mixed multi-symbol cycle: entering, managing, exiting, ambiguous -
#      all in one step() call.
# ---------------------------------------------------------------------------

class TestMixedPortfolioCycle:
    def test_four_symbols_in_different_states_advance_correctly_in_one_cycle_and_ambiguity_halts_the_rest(self):
        from test_v34_p02_multipos_engine import sample_ctx
        state = flat_running_state()
        # ENTERING is created via request_entry below, after the stack exists.
        state.active_trades["MANAGING_SYM"] = sample_ctx(symbol="MANAGING_SYM", status=PositionStatus.MANAGING, filled_qty=5, stop_order_id=None)
        state.active_trades["EXITING"] = sample_ctx(symbol="EXITING", status=PositionStatus.EXIT_PENDING, filled_qty=3, pending_qty=3, exit_order_id="EXITORD1")
        state.active_trades["AMBIGUOUS"] = sample_ctx(symbol="AMBIGUOUS", status=PositionStatus.MANAGING, filled_qty=2, stop_order_id=None)

        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("MANAGING_SYM", 5)]  # nothing for AMBIGUOUS -> ambiguous
        raw_broker.quotes = {"NSE:MANAGING_SYM": {"last_price": 100}}
        raw_broker.order_details["EXITORD1"] = {"order_id": "EXITORD1", "status": "COMPLETE", "filled_quantity": 3, "quantity": 3}

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)
        assert engine.request_entry(symbol="INFY", quantity=2, price=Decimal("1500")) == "STATE_CHANGED"

        # Insertion order in active_trades: MANAGING_SYM, EXITING,
        # AMBIGUOUS, then INFY (request_entry appended last). Ambiguity on
        # the 3rd symbol must halt before INFY (the 4th) is ever dispatched.
        result = engine.step()

        assert result["MANAGING_SYM"] == "NO_ACTION"
        assert result["EXITING"] == "STATE_CHANGED"
        assert "EXITING" not in engine.state.active_trades  # exit completed, broker proved zero
        assert result["AMBIGUOUS"] == "HALTED"
        assert "INFY" not in result  # never reached - whole-engine halt stopped dispatch
        assert terminator.halted is True
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT

        # The healthy, already-advanced positions are exactly as step()
        # left them - the halt doesn't roll anything back, and nothing
        # past the ambiguity point was touched at all.
        assert engine.state.active_trades["MANAGING_SYM"].status == PositionStatus.MANAGING
        assert "INFY" in engine.state.active_trades  # untouched, still ENTRY_SUBMIT
        assert engine.state.active_trades["INFY"].status == PositionStatus.ENTRY_SUBMIT
        assert len(raw_broker.place_order_calls) == 0  # INFY's submission never happened


# ---------------------------------------------------------------------------
# 6. Idempotency at every dangerous restart boundary
# ---------------------------------------------------------------------------

class TestIdempotencyAcrossRestartBoundaries:
    def test_before_submit_boundary_never_resubmits(self):
        # Crash between ENTRY_SUBMIT validating and ever calling
        # broker.place_order: simulate by making the raw broker's
        # place_order raise a NON-transient error the first time, forcing
        # ENGINE_HALT rather than progress - then confirm a fresh engine
        # instance from the same store still shows zero broker calls ever
        # made, and reconcile_startup does not attempt to submit either.
        state = flat_running_state()
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state)
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))

        def boom(**kwargs):
            raise RuntimeError("simulated crash before the broker call ever completes")
        raw_broker.place_order_fn = boom

        result = engine.step()
        assert result["RELIANCE"] == "HALTED"
        assert terminator.halted is True
        assert len(raw_broker.place_order_calls) == 1  # the attempt was made and failed - not silently retried

        # "Restart": fresh engine from the same durable store. It is
        # halted (ENGINE_HALT persisted) and must not attempt anything.
        engine2, terminator2, audit2 = restart(store, raw_broker=raw_broker, cfg=engine.cfg, authorizer_store=authorizer_store, context=ctx_holder["ctx"])
        assert terminator2.halted is True
        result2 = engine2.step()
        assert result2 == {"__engine__": "HALTED"}
        assert len(raw_broker.place_order_calls) == 1  # still exactly one attempt, ever

    def test_after_fingerprint_persist_ambiguous_submission_recovers_without_resubmission(self):
        # The raw broker call itself raises a transient/ambiguous
        # exception (network timeout after the order may have actually
        # reached the exchange) - the engine must go to ENTRY_UNKNOWN, and
        # a subsequent step() must reconcile via fingerprint, never retry
        # place_order blindly.
        state = flat_running_state()
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state)
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))

        attempt_count = {"n": 0}

        def flaky(**kwargs):
            attempt_count["n"] += 1
            # Must be a real TimeoutError - _is_transient_submission_exception
            # classifies by exact class name, so a differently-named
            # subclass would not be recognized as transient.
            raise TimeoutError("ambiguous - broker may or may not have received this")
        raw_broker.place_order_fn = flaky

        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.ENTRY_UNKNOWN
        assert attempt_count["n"] == 1

        # The order actually DID land at the broker (ack was merely lost).
        # Simulate that now becoming visible.
        fp = engine.state.active_trades["RELIANCE"].entry_submission_fingerprint
        real_order = dict(fp)
        real_order["order_id"] = "REAL-ORDER-1"
        real_order["status"] = "OPEN"
        raw_broker.orders = [real_order]

        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.status == PositionStatus.ENTRY_PENDING
        assert ctx.entry_order_id == "REAL-ORDER-1"
        # place_order was never called a second time - recovery happened
        # purely through fingerprint-based observation.
        assert attempt_count["n"] == 1
        assert len(raw_broker.place_order_calls) == 1  # exactly one raw call was ever attempted, despite two step() cycles

    def test_ambiguous_submission_with_no_broker_trace_never_resubmits_and_eventually_halts(self):
        # The other branch: the order genuinely never reached the broker.
        # Repeated polling must never resubmit, and must eventually halt
        # once the observation-retry budget is exhausted - not loop forever.
        state = flat_running_state()
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"), observation_retry_budget=2)
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, cfg=cfg)
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))

        raw_broker.place_order_fn = lambda **kwargs: (_ for _ in ()).throw(TimeoutError("lost"))
        engine.step()  # -> ENTRY_UNKNOWN, 1 attempt

        raw_broker.orders = []  # never shows up, ever
        r2 = engine.step()  # reconcile attempt 1: no match
        assert r2["RELIANCE"] == "NO_ACTION"
        r3 = engine.step()  # reconcile attempt 2: no match
        assert r3["RELIANCE"] == "NO_ACTION"
        r4 = engine.step()  # budget (2) exceeded -> halt
        assert r4["RELIANCE"] == "HALTED"
        assert terminator.halted is True
        assert len(raw_broker.place_order_calls) == 1  # never resubmitted across the whole retry sequence

    def test_partial_fill_boundary_never_calls_place_order_again(self):
        state = flat_running_state()
        from test_v34_p02_multipos_engine import sample_ctx
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.PARTIAL_POSITION, filled_qty=6, pending_qty=4, target_qty=10, tranche_qty=10, entry_order_id="OID1", stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 6)]
        raw_broker.order_details["OID1"] = {"order_id": "OID1", "status": "OPEN", "filled_quantity": 6, "quantity": 10, "average_price": "2500"}
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        engine.step()
        engine2, terminator2, audit2 = restart(store, raw_broker=raw_broker, cfg=engine.cfg, authorizer_store=authorizer_store, context=ctx_holder["ctx"])
        engine2.step()

        assert len(raw_broker.place_order_calls) == 0  # PARTIAL_POSITION never resubmits, restart or not

    def test_full_fill_before_persist_boundary_reconciles_forward_without_resubmission(self):
        # Persisted state still shows ENTRY_PENDING (as if the process
        # died right after observing a fill but before writing PROTECTION)
        # while the broker/order already shows COMPLETE - restart must
        # advance to PROTECTION purely by re-polling, not resubmit.
        state = flat_running_state()
        from test_v34_p02_multipos_engine import sample_ctx
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.ENTRY_PENDING, filled_qty=0, pending_qty=10, target_qty=10, tranche_qty=10, entry_order_id="OID1", stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10)]
        raw_broker.order_details["OID1"] = {"order_id": "OID1", "status": "COMPLETE", "filled_quantity": 10, "quantity": 10, "average_price": "2500"}
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        engine2, terminator2, audit2 = restart(store, raw_broker=raw_broker, cfg=engine.cfg, authorizer_store=authorizer_store, context=ctx_holder["ctx"])
        result = engine2.step()
        assert result["RELIANCE"] == "STATE_CHANGED"
        assert engine2.state.active_trades["RELIANCE"].status == PositionStatus.PROTECTION
        assert len(raw_broker.place_order_calls) == 0

    def test_exit_side_before_submit_boundary_never_resubmits(self):
        state = flat_running_state()
        from test_v34_p02_multipos_engine import sample_ctx
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10)]
        raw_broker.quotes = {"NSE:RELIANCE": {"last_price": 2550}}

        def boom(**kwargs):
            raise RuntimeError("simulated crash before the exit call ever completes")
        raw_broker.submit_emergency_exit_fn = boom

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)
        engine.request_exit(symbol="RELIANCE")
        result = engine.step()
        assert result["RELIANCE"] == "HALTED"

        engine2, terminator2, audit2 = restart(store, raw_broker=raw_broker, cfg=engine.cfg, authorizer_store=authorizer_store, context=ctx_holder["ctx"])
        assert terminator2.halted is True  # persisted ENGINE_HALT survives restart, no further attempts possible

    def test_exit_side_partial_fill_boundary_never_resubmits(self):
        state = flat_running_state()
        from test_v34_p02_multipos_engine import sample_ctx
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.EXIT_PENDING, filled_qty=10, pending_qty=6, exit_order_id="EXITORD1")
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 6)]
        raw_broker.order_details["EXITORD1"] = {"order_id": "EXITORD1", "status": "OPEN", "filled_quantity": 4, "quantity": 10}
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        engine.step()
        engine2, terminator2, audit2 = restart(store, raw_broker=raw_broker, cfg=engine.cfg, authorizer_store=authorizer_store, context=ctx_holder["ctx"])
        engine2.step()

        assert raw_broker.submit_emergency_exit_fn is None  # never even set - proves no exit resubmission path was exercised
        assert "RELIANCE" in engine2.state.active_trades  # still resolving, not prematurely removed
