"""R5-D02.2 desired-behaviour regression: durable exit intent + idempotent finalization.

Real objects: Revision2ExternalEngineOrchestrator.run() on the D01.1 derived-real TITAN replay, the real CostedPaperBrokerAdapter, the
real CombinedCycleRuntime and CombinedCycleStore (SQLite).  Failures are injected test-locally with instance-level wrappers; no
production source is edited and no final state is hand-mutated.

Contract under test (see revision5/combined_cycle_runtime.py):
  RESERVED  durable BEFORE the broker close order; the stable event id ``exit:<trade_id>`` is also the broker client order id
  FILLED    durable AFTER the broker close succeeded; recovery must finalize and must NOT resubmit
  FINALIZED set together with BARRIER 2 (durable feedback receipt) after durable CLOSED (BARRIER 1) and the realized-R feedback

Feedback semantics asserted here: realized-R feedback is applied at most once per controller lineage and is re-derived from the durable
FILLED fact for any lineage that lacks the matching in-memory receipt (fresh restart).  The controller state itself is process
memory in this path, so the guarantee is "no duplicate, re-applicable from the durable fact", not durability of the controller.
"""
from types import SimpleNamespace

import pytest

from revision5.combined_cycle_runtime import (
    CombinedCycleReconciliationError, CombinedCycleRuntime, EXIT_FILLED, EXIT_FINALIZED, EXIT_RESERVED)
from revision5.combined_cycle_store import CombinedCycleStore
from revision5.engine_b_management import EngineBController, EngineBPolicy
from revision5.handoff_manager import HandoffConfig, HandoffManager
from tests.test_r5_d01_orchestrator_run_integration import SYMBOL, build_orchestrator

TRADE_ID = "trade-1"
EVENT_ID = "exit:trade-1"
BAY = "CSTG2_CONSUMER_AUTO"


def closing_fills(orch):
    return [f for f in orch.broker.fills if f["side"] == "BUY"]          # the replayed TITAN trade is a SELL


def feedback_counts(orch):
    merit = orch.plant_control.dispatch_controller.merit_source
    governor = orch.real_plant_dcs.bays[BAY].governor
    return len(merit.trade_history_r[BAY]), len(governor.history_r)


def durable(store):
    snap = store.load(TRADE_ID)
    intent = snap.protection.get("exit_intent") or {}
    return dict(state=snap.record.lifecycle_state, status=intent.get("status"), order_id=intent.get("order_id"),
                receipts=snap.protection.get("close_feedback_receipts"), has_completed="completed_trade" in snap.protection)


def run_with(tmp_path, *, fail_close_calls=(), flaky_feedback=False, fail_record_fill=False, fail_reserve=False,
             crash_before_send=False, inject=None):
    orch, runtime, store, _, titan, warmup = build_orchestrator(tmp_path)
    ctx = SimpleNamespace(orch=orch, runtime=runtime, store=store, error=None, close_calls=0, store_path=tmp_path / "cycle.db",
                          place_calls=[], flags=SimpleNamespace(fail_record_fill=fail_record_fill, fail_reserve=fail_reserve,
                                                                crash_before_send=crash_before_send))
    real_close, real_fill, real_reserve, real_place = (runtime.close, runtime.record_exit_fill,
                                                       runtime.reserve_exit_intent, orch.broker.place_order)

    def close_spy(record, trade, **kwargs):
        ctx.close_calls += 1
        if ctx.close_calls in fail_close_calls:
            raise OSError(f"injected durable close failure (call {ctx.close_calls})")
        return real_close(record, trade, **kwargs)

    def fill_spy(*args, **kwargs):
        if ctx.flags.fail_record_fill:
            raise OSError("injected failure persisting the FILLED fact")
        return real_fill(*args, **kwargs)

    def reserve_spy(*args, **kwargs):
        if ctx.flags.fail_reserve:
            raise OSError("injected failure persisting the exit intent")
        return real_reserve(*args, **kwargs)

    def place_spy(*args, **kwargs):
        identity = kwargs.get("client_order_id")
        if identity is not None:
            ctx.place_calls.append(dict(identity=identity, durable=durable(store)))        # state at broker-call time
            if ctx.flags.crash_before_send:
                raise RuntimeError("process died before the broker order was sent")
        return real_place(*args, **kwargs)

    runtime.close, runtime.record_exit_fill, runtime.reserve_exit_intent = close_spy, fill_spy, reserve_spy
    orch.broker.place_order = place_spy
    if flaky_feedback:
        merit = orch.plant_control.dispatch_controller.merit_source
        real_register, calls = merit.register_trade, {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected feedback failure")
            return real_register(*args, **kwargs)
        merit.register_trade = flaky
    if inject is not None:
        inject(ctx)
    try:
        orch.run({SYMBOL: titan}, warmup=warmup)
    except BaseException as exc:                                          # noqa: BLE001 - asserted by the tests
        ctx.error = exc
    return ctx


def restart(tmp_path, ctx):
    """A fresh process: new engine/controllers, same durable store file, same (persistent) paper broker truth."""
    (tmp_path / "restart").mkdir()
    engine, _, scratch_store, _, _, _ = build_orchestrator(tmp_path / "restart")
    scratch_store.close()
    store = CombinedCycleStore(ctx.store_path)
    runtime = CombinedCycleRuntime(store, HandoffManager(store, HandoffConfig(enabled=True)),
                                   EngineBController(EngineBPolicy(enabled=True)))
    engine.combined_cycle_runtime, engine.broker = runtime, ctx.orch.broker
    return SimpleNamespace(orch=engine, runtime=runtime, store=store)


def assert_converged(orch, store, *, completed=1):
    assert len(closing_fills(orch)) == 1                                  # exactly one broker close
    assert orch.broker.get_position(SYMBOL)["quantity"] == 0
    assert SYMBOL not in orch.open_trades
    assert [t["trade_id"] for t in orch.completed_trades] == [TRADE_ID] * completed
    assert orch._position_lifecycle[TRADE_ID].lifecycle_state == "CLOSED"
    row = durable(store)
    assert row["state"] == "CLOSED" and row["status"] == EXIT_FINALIZED and row["has_completed"]
    assert row["receipts"] == {f"trade_id:{TRADE_ID}": "DONE"}
    assert store.list_open() == []


# --------------------------------------------------------------------------- broker identity
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_paper_broker_same_client_order_id_never_creates_a_second_fill(tmp_path, side):
    orch, runtime, store, _, _, _ = build_orchestrator(tmp_path)
    broker, config, registry = orch.broker, orch.safety_contract.as_dict(), orch.registry
    first = broker.place_order("INFY", side, 5, "MARKET", 100.0, config, registry, client_order_id="exit:probe")
    state = (len(broker.fills), broker.get_position("INFY")["quantity"], broker.booked_costs, broker.realized_pnl)
    for price in (101.0, 102.0, 103.0):
        again = broker.place_order("INFY", side, 5, "MARKET", price, config, registry, client_order_id="exit:probe")
        assert again["duplicate_submission"] is True
        assert (again["order_id"], again["filled_price"], again["cost"]) == (first["order_id"], first["filled_price"], first["cost"])
    assert (len(broker.fills), broker.get_position("INFY")["quantity"], broker.booked_costs, broker.realized_pnl) == state
    with pytest.raises(ValueError, match="identity collision"):
        broker.place_order("INFY", side, 6, "MARKET", 100.0, config, registry, client_order_id="exit:probe")
    # ordinary calls (no identity) keep their previous semantics: every call is a new order
    plain_a = broker.place_order("INFY", side, 1, "MARKET", 100.0, config, registry)
    plain_b = broker.place_order("INFY", side, 1, "MARKET", 100.0, config, registry)
    assert plain_a["order_id"] != plain_b["order_id"] and "duplicate_submission" not in plain_a
    store.close()


def test_exit_intent_reservation_is_idempotent_and_conflict_checked(tmp_path):
    ctx = run_with(tmp_path, fail_reserve=False)
    assert ctx.error is None
    # a CLOSED position can no longer reserve an exit
    with pytest.raises(ValueError, match="CLOSED"):
        ctx.runtime.reserve_exit_intent(TRADE_ID, side="BUY", quantity=5, reason="x", market_price=1.0, requested_at="t")
    ctx.store.close()


# --------------------------------------------------------------------------- normal exit, parity
def test_normal_exit_intent_flow_and_economic_parity(tmp_path):
    ctx = run_with(tmp_path)
    assert ctx.error is None
    orch = ctx.orch
    # intent was durable and RESERVED at the moment the broker close order was called, lifecycle still open
    assert len(ctx.place_calls) == 1 and ctx.place_calls[0]["identity"] == EVENT_ID
    assert ctx.place_calls[0]["durable"]["status"] == EXIT_RESERVED and ctx.place_calls[0]["durable"]["state"] == "A_OPEN"
    assert_converged(orch, ctx.store)
    assert ctx.close_calls == 2                                           # both durability barriers preserved
    assert durable(ctx.store)["order_id"] == closing_fills(orch)[0]["order_id"]
    assert feedback_counts(orch) == (1, 1)
    # trading economics identical to the pre-repair engine (measured on the archived HEAD source before the change)
    trade = orch.completed_trades[0]
    assert (trade["symbol"], trade["side"], trade["quantity"]) == ("TITAN", "SELL", 5)
    assert trade["entry_timestamp"] == "2024-02-13 09:33:00+05:30" and trade["exit_timestamp"] == "2024-02-13 09:36:00+05:30"
    assert trade["entry_price"] == pytest.approx(3559.5838, rel=1e-12)
    assert trade["exit_price"] == pytest.approx(3569.784, rel=1e-12)
    assert trade["pnl"] == pytest.approx(-51.00100000000111, rel=1e-9)
    assert trade["costs"] == pytest.approx(16.373347395499998, rel=1e-12)
    assert trade["net_pnl"] == pytest.approx(-67.3743473955011, rel=1e-9)
    assert trade["reason"] == "governor_exit:FSR_BELOW_EXIT:FSRN" and trade["bars_held"] == 3
    assert orch.plant_control.dispatch_controller.merit_source.trade_history_r[BAY] == pytest.approx([-0.2923842083613769], rel=1e-12)
    assert orch.broker.booked_costs == pytest.approx(16.373347, abs=1e-6)
    ctx.store.close()


# --------------------------------------------------------------------------- pre-broker durability
def test_intent_persistence_failure_sends_no_broker_order(tmp_path):
    ctx = run_with(tmp_path, fail_reserve=True)
    assert isinstance(ctx.error, OSError) and "exit intent" in str(ctx.error)
    assert ctx.place_calls == []                                          # no closing order was ever submitted
    assert closing_fills(ctx.orch) == [] and ctx.orch.broker.get_position(SYMBOL)["quantity"] == -5
    assert ctx.orch._execution_halted is True
    assert durable(ctx.store)["state"] == "A_OPEN" and durable(ctx.store)["status"] is None
    ctx.store.close()


# --------------------------------------------------------------------------- Window A
def test_window_a_first_durable_close_fails_then_retry_converges(tmp_path):
    ctx = run_with(tmp_path, fail_close_calls=(1,))
    assert isinstance(ctx.error, OSError)
    orch = ctx.orch
    # S2: the contradictory-looking state exists, but the durable FILLED fact proves the close executed
    row = durable(ctx.store)
    assert row["state"] == "A_OPEN" and row["status"] == EXIT_FILLED and row["order_id"] == closing_fills(orch)[0]["order_id"]
    assert feedback_counts(orch) == (0, 0)
    outcome = orch.recover_exit_intents()
    assert outcome == [dict(trade_id=TRADE_ID, action="FINALIZED_FROM_FILLED", resubmitted=False)]
    assert_converged(orch, ctx.store)
    assert feedback_counts(orch) == (1, 1)
    assert orch.recover_exit_intents() == []                              # nothing pending any more
    ctx.store.close()


def test_window_a_restart_converges_without_a_second_close(tmp_path):
    ctx = run_with(tmp_path, fail_close_calls=(1,))
    assert isinstance(ctx.error, OSError)
    fresh = restart(tmp_path, ctx)
    # the startup path (restore) resolves the unfinished exit itself: it neither misreads nor re-adopts the row
    fresh.runtime.restore(fresh.orch)
    assert SYMBOL not in fresh.orch.open_trades
    assert_converged(fresh.orch, fresh.store)
    assert fresh.orch.recover_exit_intents() == []
    assert feedback_counts(fresh.orch) == (1, 1)                          # applied once to the fresh controllers
    assert len(closing_fills(ctx.orch)) == 1
    ctx.store.close()
    fresh.store.close()


# --------------------------------------------------------------------------- Window B
def test_window_b_feedback_interrupted_after_durable_close_converges(tmp_path):
    ctx = run_with(tmp_path, flaky_feedback=True)
    assert isinstance(ctx.error, RuntimeError) and "feedback" in str(ctx.error)
    row = durable(ctx.store)
    assert row["state"] == "CLOSED" and row["status"] == EXIT_FILLED and row["receipts"] == {}
    assert feedback_counts(ctx.orch) == (0, 0)
    assert [o["action"] for o in ctx.orch.recover_exit_intents()] == ["FINALIZED_FROM_FILLED"]
    assert_converged(ctx.orch, ctx.store)
    assert feedback_counts(ctx.orch) == (1, 1)
    ctx.store.close()


def test_window_b_restart_applies_feedback_exactly_once(tmp_path):
    ctx = run_with(tmp_path, flaky_feedback=True)
    assert isinstance(ctx.error, RuntimeError)
    fresh = restart(tmp_path, ctx)
    assert [o["action"] for o in fresh.orch.recover_exit_intents()] == ["FINALIZED_FROM_FILLED"]
    assert_converged(fresh.orch, fresh.store)
    assert feedback_counts(fresh.orch) == (1, 1) and len(closing_fills(ctx.orch)) == 1
    assert fresh.orch.recover_exit_intents() == []
    ctx.store.close()
    fresh.store.close()


# --------------------------------------------------------------------------- Window C
def test_window_c_feedback_applied_but_receipt_persistence_fails_is_not_applied_twice(tmp_path):
    ctx = run_with(tmp_path, fail_close_calls=(2,))
    assert isinstance(ctx.error, OSError)
    orch = ctx.orch
    row = durable(ctx.store)
    assert row["state"] == "CLOSED" and row["status"] == EXIT_FILLED and row["receipts"] == {}   # durable row alone: not DONE
    assert feedback_counts(orch) == (1, 1) and orch._close_feedback_receipts == {f"trade_id:{TRADE_ID}": "DONE"}
    assert [o["action"] for o in orch.recover_exit_intents()] == ["FINALIZED_FROM_FILLED"]
    assert feedback_counts(orch) == (1, 1)                                # NOT applied a second time
    assert_converged(orch, ctx.store)
    for _ in range(2):
        assert orch.recover_exit_intents() == []
    assert feedback_counts(orch) == (1, 1)
    ctx.store.close()


def test_window_c_restart_with_lineage_lacking_the_effect_applies_once(tmp_path):
    ctx = run_with(tmp_path, fail_close_calls=(2,))
    assert isinstance(ctx.error, OSError) and feedback_counts(ctx.orch) == (1, 1)
    fresh = restart(tmp_path, ctx)                                        # controller memory died with the process
    assert feedback_counts(fresh.orch) == (0, 0)
    fresh.orch.recover_exit_intents()
    assert feedback_counts(fresh.orch) == (1, 1)
    assert_converged(fresh.orch, fresh.store)
    ctx.store.close()
    fresh.store.close()


def test_window_c_restart_with_lineage_already_carrying_the_effect_does_not_apply_twice(tmp_path):
    ctx = run_with(tmp_path, fail_close_calls=(2,))
    assert isinstance(ctx.error, OSError)
    fresh = restart(tmp_path, ctx)
    # emulate a controller checkpoint taken after the feedback: effect AND receipt travel together
    plan = ctx.runtime.exit_intent(TRADE_ID)["feedback"]
    trade = ctx.store.load(TRADE_ID).trade
    fresh.orch._register_realized_r_close_feedback(symbol=SYMBOL, trade=trade, bay_id=plan["bay_id"],
                                                   realized_r=plan["realized_r"], reason=plan["reason"])
    assert feedback_counts(fresh.orch) == (1, 1)
    fresh.orch.recover_exit_intents()
    assert feedback_counts(fresh.orch) == (1, 1)                          # receipt guard: not applied again
    assert_converged(fresh.orch, fresh.store)
    ctx.store.close()
    fresh.store.close()


# --------------------------------------------------------------------------- crash before the FILLED fact
def test_crash_before_filled_fact_order_executed_recovers_by_identity_without_resubmit(tmp_path):
    ctx = run_with(tmp_path, fail_record_fill=True)
    assert isinstance(ctx.error, OSError) and "FILLED fact" in str(ctx.error)
    row = durable(ctx.store)
    assert row["status"] == EXIT_RESERVED and row["state"] == "A_OPEN"
    assert len(closing_fills(ctx.orch)) == 1                              # the broker did execute the close
    ctx.flags.fail_record_fill = False                                    # injection removed
    outcome = ctx.orch.recover_exit_intents()
    assert outcome == [dict(trade_id=TRADE_ID, action="FILLED_RECORDED_FROM_BROKER_IDENTITY", resubmitted=False)]
    assert_converged(ctx.orch, ctx.store)
    assert ctx.orch.completed_trades[0]["exit_price"] == pytest.approx(3569.784, rel=1e-12)
    ctx.store.close()


def test_crash_before_filled_fact_restart_recovers_by_identity(tmp_path):
    ctx = run_with(tmp_path, fail_record_fill=True)
    ctx.flags.fail_record_fill = False
    fresh = restart(tmp_path, ctx)
    assert [o["action"] for o in fresh.orch.recover_exit_intents()] == ["FILLED_RECORDED_FROM_BROKER_IDENTITY"]
    assert_converged(fresh.orch, fresh.store)
    assert len(closing_fills(ctx.orch)) == 1
    assert feedback_counts(fresh.orch) == (1, 1)                          # re-derived from the durable record, once
    ctx.store.close()
    fresh.store.close()


def test_crash_before_filled_fact_order_never_executed_resubmits_the_same_event_once(tmp_path):
    ctx = run_with(tmp_path, crash_before_send=True)
    assert isinstance(ctx.error, RuntimeError) and "before the broker order was sent" in str(ctx.error)
    assert durable(ctx.store)["status"] == EXIT_RESERVED and closing_fills(ctx.orch) == []
    assert ctx.orch.broker.get_position(SYMBOL)["quantity"] == -5
    ctx.flags.crash_before_send = False                                   # injection removed
    outcome = ctx.orch.recover_exit_intents()
    assert outcome == [dict(trade_id=TRADE_ID, action="RESUBMITTED_SAME_EVENT", resubmitted=True)]
    assert_converged(ctx.orch, ctx.store)
    assert ctx.orch.broker.find_client_order(EVENT_ID) is not None
    ctx.store.close()


def test_ambiguous_outcome_halts_and_never_treats_broker_flat_as_proof(tmp_path):
    ctx = run_with(tmp_path, crash_before_send=True)
    orch = ctx.orch
    # the position disappears for a reason that is NOT our close (stop, manual action): no client order exists for our identity
    orch.broker.place_order("TITAN", "BUY", 5, "MARKET", 3570.0, orch.safety_contract.as_dict(), orch.registry)
    assert orch.broker.get_position(SYMBOL)["quantity"] == 0 and orch.broker.find_client_order(EVENT_ID) is None
    before = len(orch.broker.fills)
    with pytest.raises(CombinedCycleReconciliationError, match="ambiguous"):
        orch.recover_exit_intents()
    assert orch._execution_halted is True
    assert len(orch.broker.fills) == before                              # no resubmission
    assert durable(ctx.store)["status"] == EXIT_RESERVED and durable(ctx.store)["state"] == "A_OPEN"
    assert orch.completed_trades == []
    ctx.store.close()


# --------------------------------------------------------------------------- repeated finalization
def test_repeated_finalizer_is_idempotent(tmp_path):
    ctx = run_with(tmp_path, fail_close_calls=(1,))
    orch = ctx.orch
    first = orch.recover_exit_intents()
    assert len(first) == 1
    for _ in range(3):
        assert orch.recover_exit_intents() == []
    # calling the finalizer itself again for the same event is harmless
    trade = ctx.store.load(TRADE_ID).trade
    completed = ctx.store.load(TRADE_ID).protection["completed_trade"]
    plan = ctx.runtime.exit_intent(TRADE_ID)["feedback"]
    for _ in range(3):
        orch._finalize_exit(SYMBOL, completed["exit_timestamp"], trade, completed, plan)
    assert_converged(orch, ctx.store)
    assert feedback_counts(orch) == (1, 1)
    assert orch.completed_trades[0]["net_pnl"] == pytest.approx(-67.3743473955011, rel=1e-9)
    ctx.store.close()


# --------------------------------------------------------------------------- Window A': partial local bookkeeping
# _finalize_exit appends the completed trade first, then does governor close cleanup and MTM.  A failure between those steps
# leaves the trade "already booked"; recovery must still perform the cleanup that the failed attempt never reached.
def _fail_equity_once_after_booking(ctx):
    orch, state = ctx.orch, SimpleNamespace(fired=False)
    real_equity = orch._equity

    def equity():
        if not state.fired and len(orch.completed_trades) == 1:      # first call after completed_trades.append
            state.fired = True
            raise OSError("injected _equity failure after completed_trades.append")
        return real_equity()
    orch._equity = equity


def _fail_governor_confirm_once(ctx):
    governor = ctx.orch._governor_for(SYMBOL)[1]
    assert governor is not None
    ctx.governor_confirms, state, real_confirm = [], SimpleNamespace(fired=False), governor.confirm_position_closed

    def confirm(position_id=None):
        if not state.fired:
            state.fired = True
            raise OSError("injected governor.confirm_position_closed failure")
        ctx.governor_confirms.append(position_id)
        return real_confirm(position_id)
    governor.confirm_position_closed = confirm


def _recover_twice_counting_cleanup(ctx):
    """Count governor close confirmations and MTM updates performed from now on (i.e. by recovery only)."""
    orch = ctx.orch
    governor = orch._governor_for(SYMBOL)[1]
    confirms, mtm_calls = [], []
    real_confirm, real_mtm = governor.confirm_position_closed, orch._record_mtm

    def confirm(position_id=None):
        confirms.append(position_id)
        return real_confirm(position_id)

    def mtm(timestamp):
        mtm_calls.append(timestamp)
        return real_mtm(timestamp)
    governor.confirm_position_closed, orch._record_mtm = confirm, mtm
    first = orch.recover_exit_intents()
    second = orch.recover_exit_intents()
    return first, second, confirms, mtm_calls


@pytest.mark.parametrize("inject", [_fail_equity_once_after_booking, _fail_governor_confirm_once],
                         ids=["equity_raises_once", "governor_confirm_raises_once"])
def test_window_a_prime_partial_finalize_after_booking_still_runs_governor_close_and_mtm_once(tmp_path, inject):
    ctx = run_with(tmp_path, inject=inject)
    orch = ctx.orch
    assert isinstance(ctx.error, OSError) and "injected" in str(ctx.error)
    # precondition: booked, broker closed once, FILLED durable, lifecycle open, governor cleanup NOT yet done
    assert [t["trade_id"] for t in orch.completed_trades] == [TRADE_ID]
    assert len(closing_fills(orch)) == 1
    row = durable(ctx.store)
    assert row["state"] == "A_OPEN" and row["status"] == EXIT_FILLED
    assert TRADE_ID not in getattr(ctx, "governor_confirms", [])           # no successful trade-specific confirm yet

    curve_before = len(orch._equity_curve)
    first, second, confirms, mtm_calls = _recover_twice_counting_cleanup(ctx)
    # loss/cooldown bookkeeping happened once in the failed attempt and is not repeated; the equity point is appended
    # exactly once overall (by recovery if the failed attempt never reached it, otherwise already present)
    assert orch.symbol_consecutive_losses.get(SYMBOL, 0) == 1
    assert len(orch._equity_curve) == (curve_before + 1 if inject is _fail_equity_once_after_booking else curve_before)

    assert [o["action"] for o in first] == ["FINALIZED_FROM_FILLED"] and second == []
    # exactly-once effects that already hold: one broker close, one completed trade, one feedback, durable convergence
    assert_converged(orch, ctx.store)
    assert feedback_counts(orch) == (1, 1)
    # the cleanup the failed attempt never reached must eventually happen exactly once (not skipped as "already booked").
    # Only the trade-specific confirmation counts: the realized-R feedback path (bay.register_outcome ->
    # ccpp_unified_plant governor.confirm_position_closed()) also calls confirm with no id, which is unrelated cleanup
    # and says nothing about _finalize_exit's governor close for this trade.
    trade_confirms = [pid for pid in confirms if pid == TRADE_ID]
    assert trade_confirms == [TRADE_ID], f"trade-specific governor close missed; all confirms seen by recovery: {confirms}"
    # _record_mtm is only reached from _finalize_exit during recovery (the tick-loop caller is not running here)
    assert len(mtm_calls) == 1
    ctx.store.close()


def test_restore_fails_closed_on_an_ambiguous_exit_and_does_not_unmanage_the_position(tmp_path):
    ctx = run_with(tmp_path, crash_before_send=True)
    ctx.flags.crash_before_send = False
    orch = ctx.orch
    orch.broker.place_order("TITAN", "BUY", 5, "MARKET", 3570.0, orch.safety_contract.as_dict(), orch.registry)  # flattened by something else
    fresh = restart(tmp_path, ctx)
    before = len(fresh.orch.broker.fills)
    with pytest.raises(CombinedCycleReconciliationError, match="ambiguous"):
        fresh.runtime.restore(fresh.orch)
    assert fresh.orch._execution_halted is True and len(fresh.orch.broker.fills) == before
    ctx.store.close()
    fresh.store.close()


def test_restore_resubmits_a_never_executed_exit_once_through_the_startup_path(tmp_path):
    ctx = run_with(tmp_path, crash_before_send=True)
    ctx.flags.crash_before_send = False
    fresh = restart(tmp_path, ctx)
    fresh.runtime.restore(fresh.orch)                                     # position still held, no client order: same event, once
    assert_converged(fresh.orch, fresh.store)
    assert fresh.orch.recover_exit_intents() == []
    ctx.store.close()
    fresh.store.close()
