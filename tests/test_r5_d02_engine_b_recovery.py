"""R5-D02.2 acceptance: Engine B / CNC (B_OPEN) exit recovery across the three interrupted-finalization windows.

Real objects only: a normally constructed Revision2ExternalEngineOrchestrator (full PAPER_APPLY plant), the real CostedPaperBrokerAdapter,
CombinedCycleRuntime, CombinedCycleStore (SQLite), HandoffManager and EngineBController.  The position reaches B_OPEN/CNC through the
production handoff (CombinedCycleRuntime.handle_bar at 15:08-15:10 -> conversion request -> paper ACK -> B_OPEN), using the same synthetic
carry fixture as tests/test_r5_morning_startup.py.  The exit is driven through the production route
(handle_bar -> _protective_exit -> _execute_exit) with a next-session gap-down bar.  Failures are injected test-locally with
instance-level wrappers; no production source is edited and no durable state is hand-mutated.

Windows (see tests/test_r5_d02_exit_finalization.py for the contract):
  A  broker closed + FILLED durable, BARRIER 1 (durable CLOSED) fails
  B  BARRIER 1 succeeded, realized-R feedback fails
  C  feedback applied in memory, BARRIER 2 (durable receipt / FINALIZED) fails

"Fresh" means: new store connection on the same SQLite file, new CombinedCycleRuntime/EngineBController, new orchestrator and a NEW
CostedPaperBrokerAdapter populated only through ``restore_snapshot(old.snapshot())``; the old adapter object is never reused.

Realized R is never hard-coded: it is recomputed from the paper fills of the fixture, (exit_fill - entry_fill) / |entry_fill - stop|.

Controller persistence: the only persisted controller restoration is the boot checkpoint (``_checkpoint_morning_recovery`` ->
revision5.morning_recovery.capture, restored by ``_reconcile_morning_startup`` -> prepare).  With the explicit ``recovery_account_id``
configured, ``_finalize_exit`` takes that checkpoint itself between "feedback applied" and BARRIER 2, and BARRIER 2 re-pins INSIDE the same
SQLite transaction as the FINALIZED row (runtime.close(recovery_checkpoint=...)), so there is no row/pin gap and a pin fault rolls back.  Without an account nothing is
captured automatically, and the stale-checkpoint test pins that a pre-exit checkpoint fails closed.  Only the controller effects and the
DONE guard are covered; full stock PID / cursor recovery and live readiness are NOT claimed.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from revision2_external.paper_execution import CostedPaperBrokerAdapter
from revision5.combined_cycle_runtime import (
    CombinedCycleReconciliationError, CombinedCycleRuntime, EXIT_FILLED, EXIT_FINALIZED, EXIT_RESERVED)
from revision5.combined_cycle_store import CombinedCycleStore
from revision5.engine_b_management import EngineBController, EngineBPolicy
from revision5.handoff_manager import HandoffConfig, HandoffManager
from revision5.position_lifecycle import B_OPEN, CLOSED, ENGINE_B
from revision5.state_recovery import StateRecoveryJournal
from revision5.topology import bay_for_symbol
from tests.test_r5_d01_orchestrator_run_integration import build_orchestrator

SYMBOL = "TITAN"
TRADE_ID = "trade-1"
EVENT_ID = "exit:trade-1"
BAY = bay_for_symbol(SYMBOL)
QTY = 10
STOP = 90.0
ACCOUNT = "fixture-account"
GAP_TS = pd.Timestamp("2023-12-06 09:15")                                   # next session, after the 15:10 handoff
GAP_BAR = dict(open=89.0, high=90.0, low=88.0, close=89.0)                   # open <= stop: production 'stop_gap' exit
FINALIZED_FROM_FILLED = dict(trade_id=TRADE_ID, action="FINALIZED_FROM_FILLED", resubmitted=False)


# --------------------------------------------------------------------------- fixture
def policy():
    return EngineBPolicy(enabled=True, structural_window=2, trail_mode="NONE")      # same policy as the carry fixture


def new_runtime(store):
    return CombinedCycleRuntime(store, HandoffManager(store, HandoffConfig(enabled=True)), EngineBController(policy()))


def open_b_cnc(tmp_path, *, checkpoint=False, account=False):
    """A genuine B_OPEN / CNC / BUY position produced by the production handoff path (SYNTHETIC_UNIT_FIXTURE inputs).

    ``account=True`` configures the explicit recovery account (same attribute assignment as tests/test_r5_morning_startup.py)."""
    orch, runtime, store, _, _, _ = build_orchestrator(tmp_path)
    if account:
        orch.recovery_account_id = ACCOUNT
    runtime.engine_b = EngineBController(policy())
    fill = orch.broker.place_order(SYMBOL, "BUY", QTY, "MARKET", 100, orch.safety_contract.as_dict(), orch.registry)
    assert fill["passed"]
    entry = fill["filled_price"]
    trade = dict(trade_id=TRADE_ID, candidate_id="candidate-1", symbol=SYMBOL, side="BUY", quantity=QTY, entry_price=entry,
                 stop_price=STOP, initial_stop_price=STOP, target_price=200.0, entry_timestamp=pd.Timestamp("2023-12-05 15:07"))
    orch._trade_sequence = 1
    orch.open_trades[SYMBOL] = trade
    orch._register_position_lifecycle(SYMBOL, trade, trade["entry_timestamp"])
    orch._exit_controller_states[SYMBOL] = orch.exit_controller.open_position("BUY", entry, STOP, 200.0, 375)
    orch._bay_governors[BAY].begin_position(position_id=TRADE_ID)
    for minute in ("15:08", "15:09", "15:10"):
        runtime.handle_bar(orch, SYMBOL, pd.Timestamp("2023-12-05 " + minute), dict(open=110, high=111, low=109, close=110), False)
    # explicit fleet fixture values required by the boot checkpoint validator (same role as in the carry fixture)
    orch._mtm_peak = max(orch._mtm_peak, orch.starting_equity)
    orch.plant_control.ecs._previous_demand = 0.8
    if checkpoint:
        orch._checkpoint_morning_recovery(account_id=ACCOUNT, timestamp=pd.Timestamp("2023-12-05 15:25"))
    return SimpleNamespace(orch=orch, runtime=runtime, store=store, store_path=tmp_path / "cycle.db", error=None, handled=None,
                           close_calls=0, close_log=[], place_calls=[], flags=SimpleNamespace(crash_before_send=False))


def arm(ctx, *, fail_close_calls=(), flaky_feedback=False, crash_before_send=False):
    """Instance-level observation/injection wrappers around the real runtime.close / broker.place_order / merit.register_trade."""
    orch, runtime, store = ctx.orch, ctx.runtime, ctx.store
    ctx.flags.crash_before_send = crash_before_send
    real_close, real_place = runtime.close, orch.broker.place_order

    def close_spy(record, trade, **kwargs):
        ctx.close_calls += 1
        number, before = ctx.close_calls, engine_b_tracked(runtime)
        try:
            if number in fail_close_calls:
                raise OSError(f"injected durable close failure (call {number})")
            result = real_close(record, trade, **kwargs)
        except BaseException:
            ctx.close_log.append((number, before, engine_b_tracked(runtime), False))
            raise
        ctx.close_log.append((number, before, engine_b_tracked(runtime), True))
        return result

    def place_spy(*args, **kwargs):
        identity = kwargs.get("client_order_id")
        if identity is not None:
            ctx.place_calls.append(dict(identity=identity, durable=durable(store),
                                        product=orch.broker.get_position(SYMBOL).get("product")))
            if ctx.flags.crash_before_send:
                raise RuntimeError("process died before the broker order was sent")
        return real_place(*args, **kwargs)

    runtime.close, orch.broker.place_order = close_spy, place_spy
    if flaky_feedback:
        merit = orch.plant_control.dispatch_controller.merit_source
        real_register, calls = merit.register_trade, {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected feedback failure")
            return real_register(*args, **kwargs)
        merit.register_trade = flaky


def drive_exit(ctx):
    try:
        ctx.handled = ctx.runtime.handle_bar(ctx.orch, SYMBOL, GAP_TS, GAP_BAR, False)
    except BaseException as exc:                                              # noqa: BLE001 - asserted by the tests
        ctx.error = exc
    return ctx


def interrupted(tmp_path, account=False, **injection):
    ctx = open_b_cnc(tmp_path, account=account)
    arm(ctx, **injection)
    return drive_exit(ctx)


def restart(tmp_path, ctx):
    """A fresh process: new store connection, runtime, controllers, orchestrator and a NEW paper broker restored from the snapshot."""
    old_broker = ctx.orch.broker
    snapshot = old_broker.snapshot()                                          # broker truth outlives the process
    ctx.store.close()
    (tmp_path / "restart").mkdir()
    engine, _, scratch_store, _, _, _ = build_orchestrator(tmp_path / "restart")
    scratch_store.close()
    broker = CostedPaperBrokerAdapter(slippage_fraction=old_broker.slippage_fraction)
    broker.restore_snapshot(snapshot)
    assert broker is not old_broker and broker.fills == old_broker.fills and broker.client_orders == old_broker.client_orders
    store = CombinedCycleStore(ctx.store_path)
    runtime = new_runtime(store)
    engine.combined_cycle_runtime, engine.broker = runtime, broker
    placed = []
    real_place = broker.place_order

    def place_spy(*args, **kwargs):
        placed.append(kwargs.get("client_order_id"))
        return real_place(*args, **kwargs)
    broker.place_order = place_spy
    return SimpleNamespace(orch=engine, runtime=runtime, store=store, broker=broker, old_broker=old_broker, placed=placed)


# --------------------------------------------------------------------------- observers
def sell_fills(broker):
    return [f for f in broker.fills if f["side"] == "SELL"]                   # the fixture entry is a BUY


def expected_r(broker):
    """Realized R recomputed from the fixture's own paper fills (never a literal)."""
    entry = next(f for f in broker.fills if f["side"] == "BUY")["price"]
    return (sell_fills(broker)[0]["price"] - entry) / abs(entry - STOP)


def feedback_counts(orch):
    merit = orch.plant_control.dispatch_controller.merit_source
    governor = orch.real_plant_dcs.bays[BAY].governor
    return len(merit.trade_history_r[BAY]), len(governor.history_r)


def engine_b_tracked(runtime):
    return TRADE_ID in runtime.engine_b.export_state()["positions"]


def durable(store):
    snap = store.load(TRADE_ID)
    intent = snap.protection.get("exit_intent") or {}
    return dict(state=snap.record.lifecycle_state, status=intent.get("status"), order_id=intent.get("order_id"),
                receipts=snap.protection.get("close_feedback_receipts"), has_completed="completed_trade" in snap.protection,
                engine_b_durable=TRADE_ID in snap.protection.get("engine_b_state", {}).get("positions", {}),
                feedback=intent.get("feedback"))


def assert_converged(orch, runtime, store, broker, *, r=None):
    """Exactly one broker close, one completed trade, one merit + one bay feedback, durable FINALIZED, Engine B evicted."""
    r = expected_r(broker) if r is None else r
    assert len(sell_fills(broker)) == 1
    assert broker.get_position(SYMBOL)["quantity"] == 0
    assert SYMBOL not in orch.open_trades
    assert [t["trade_id"] for t in orch.completed_trades] == [TRADE_ID]
    assert orch._position_lifecycle[TRADE_ID].lifecycle_state == CLOSED
    row = durable(store)
    assert row["state"] == CLOSED and row["status"] == EXIT_FINALIZED and row["has_completed"]
    assert row["receipts"] == {f"trade_id:{TRADE_ID}": "DONE"}
    assert row["order_id"] == sell_fills(broker)[0]["order_id"] == broker.find_client_order(EVENT_ID)["order_id"]
    assert row["feedback"]["realized_r"] == pytest.approx(r, rel=1e-12)
    assert store.list_open() == []
    assert not engine_b_tracked(runtime) and row["engine_b_durable"] is False          # evicted after durable CLOSED
    assert feedback_counts(orch) == (1, 1)
    merit = orch.plant_control.dispatch_controller.merit_source
    assert merit.trade_history_r[BAY] == pytest.approx([r], rel=1e-12)
    assert orch.real_plant_dcs.bays[BAY].governor.history_r == pytest.approx([r], rel=1e-12)


def assert_idempotent_afterwards(orch):
    for _ in range(2):
        assert orch.recover_exit_intents() == []
    assert feedback_counts(orch) == (1, 1)


# --------------------------------------------------------------------------- fixture + control
def test_fixture_is_a_real_b_open_cnc_position_with_retained_engine_b_state(tmp_path):
    ctx = open_b_cnc(tmp_path)
    record = ctx.store.load(TRADE_ID).record
    assert (record.lifecycle_state, record.owner_engine, record.product, record.direction) == (B_OPEN, ENGINE_B, "CNC", "BUY")
    assert ctx.orch.broker.get_position(SYMBOL)["product"] == "CNC" and ctx.orch.broker.get_position(SYMBOL)["quantity"] == QTY
    assert [r["status"] for r in ctx.store.requests()] == ["ACKNOWLEDGED"]
    assert engine_b_tracked(ctx.runtime) and durable(ctx.store)["engine_b_durable"] is True
    assert feedback_counts(ctx.orch) == (0, 0) and ctx.orch.completed_trades == []
    ctx.store.close()


def test_control_b_cnc_exit_normal_path_is_the_reference(tmp_path):
    ctx = open_b_cnc(tmp_path)
    arm(ctx)
    drive_exit(ctx)
    assert ctx.error is None and ctx.handled is True
    orch = ctx.orch
    # the durable intent was RESERVED at the broker-call instant, on a B_OPEN/CNC position, with the stable identity
    assert len(ctx.place_calls) == 1 and ctx.place_calls[0]["identity"] == EVENT_ID and ctx.place_calls[0]["product"] == "CNC"
    assert ctx.place_calls[0]["durable"]["status"] == EXIT_RESERVED and ctx.place_calls[0]["durable"]["state"] == B_OPEN
    assert_converged(orch, ctx.runtime, ctx.store, orch.broker)
    assert ctx.close_calls == 2                                                # both durability barriers
    # Engine B state is retained until the first durable CLOSED succeeds, then evicted
    assert ctx.close_log == [(1, True, False, True), (2, False, False, True)]
    trade = orch.completed_trades[0]
    assert trade["reason"] == "stop_gap" and trade["exit_price"] == sell_fills(orch.broker)[0]["price"]
    ctx.store.close()


# --------------------------------------------------------------------------- Window A
def test_window_a_in_process_retry_converges(tmp_path):
    ctx = interrupted(tmp_path, fail_close_calls=(1,))
    assert isinstance(ctx.error, OSError) and "durable close" in str(ctx.error)
    orch = ctx.orch
    row = durable(ctx.store)
    assert row["state"] == B_OPEN and row["status"] == EXIT_FILLED and row["order_id"] == sell_fills(orch.broker)[0]["order_id"]
    assert len(sell_fills(orch.broker)) == 1 and feedback_counts(orch) == (0, 0)
    # a failed durable close must NOT discard Engine B management state (in memory or in the durable row)
    assert ctx.close_log == [(1, True, True, False)] and engine_b_tracked(ctx.runtime) and row["engine_b_durable"] is True
    assert orch.recover_exit_intents() == [FINALIZED_FROM_FILLED]
    assert_converged(orch, ctx.runtime, ctx.store, orch.broker)
    assert [entry for entry in ctx.close_log if entry[3]][0] == (2, True, False, True)    # evicted by the first durable CLOSED
    assert_idempotent_afterwards(orch)
    ctx.store.close()


def test_window_a_fresh_runtime_store_and_restored_broker_converges(tmp_path):
    ctx = interrupted(tmp_path, fail_close_calls=(1,))
    assert isinstance(ctx.error, OSError)
    fresh = restart(tmp_path, ctx)
    assert durable(fresh.store)["state"] == B_OPEN and durable(fresh.store)["status"] == EXIT_FILLED
    assert durable(fresh.store)["engine_b_durable"] is True                    # durable Engine B receipt survived the failed close
    assert feedback_counts(fresh.orch) == (0, 0)
    fresh.runtime.restore(fresh.orch)                                          # the startup path resolves the unfinished exit
    assert_converged(fresh.orch, fresh.runtime, fresh.store, fresh.broker)
    assert len(sell_fills(fresh.old_broker)) == 1 and fresh.placed == []       # never resubmitted
    assert_idempotent_afterwards(fresh.orch)
    fresh.store.close()


# --------------------------------------------------------------------------- Window B
def test_window_b_in_process_retry_converges(tmp_path):
    ctx = interrupted(tmp_path, flaky_feedback=True)
    assert isinstance(ctx.error, RuntimeError) and "feedback" in str(ctx.error)
    orch = ctx.orch
    row = durable(ctx.store)
    assert row["state"] == CLOSED and row["status"] == EXIT_FILLED and row["receipts"] == {}
    assert ctx.close_log[0] == (1, True, False, True)                          # durable CLOSED succeeded -> Engine B evicted
    assert not engine_b_tracked(ctx.runtime) and row["engine_b_durable"] is False
    assert feedback_counts(orch) == (0, 0)
    assert [o["action"] for o in orch.recover_exit_intents()] == ["FINALIZED_FROM_FILLED"]
    assert_converged(orch, ctx.runtime, ctx.store, orch.broker)
    assert_idempotent_afterwards(orch)
    ctx.store.close()


def test_window_b_fresh_runtime_store_and_restored_broker_converges(tmp_path):
    ctx = interrupted(tmp_path, flaky_feedback=True)
    assert isinstance(ctx.error, RuntimeError)
    fresh = restart(tmp_path, ctx)
    assert feedback_counts(fresh.orch) == (0, 0)
    assert fresh.orch.recover_exit_intents() == [FINALIZED_FROM_FILLED]
    assert_converged(fresh.orch, fresh.runtime, fresh.store, fresh.broker)
    assert len(sell_fills(fresh.old_broker)) == 1 and fresh.placed == []
    assert_idempotent_afterwards(fresh.orch)
    fresh.store.close()


# --------------------------------------------------------------------------- Window C
def test_window_c_in_process_retry_does_not_apply_feedback_twice(tmp_path):
    ctx = interrupted(tmp_path, fail_close_calls=(2,))
    assert isinstance(ctx.error, OSError) and "durable close" in str(ctx.error)
    orch = ctx.orch
    row = durable(ctx.store)
    assert row["state"] == CLOSED and row["status"] == EXIT_FILLED and row["receipts"] == {}      # durable row alone: not DONE
    assert ctx.close_log == [(1, True, False, True), (2, False, False, False)]
    assert feedback_counts(orch) == (1, 1) and orch._close_feedback_receipts == {f"trade_id:{TRADE_ID}": "DONE"}
    assert [o["action"] for o in orch.recover_exit_intents()] == ["FINALIZED_FROM_FILLED"]
    assert_converged(orch, ctx.runtime, ctx.store, orch.broker)
    assert_idempotent_afterwards(orch)
    ctx.store.close()


def test_window_c_fresh_lineage_lacking_the_effect_applies_it_once_from_the_durable_fact(tmp_path):
    ctx = interrupted(tmp_path, fail_close_calls=(2,))
    assert isinstance(ctx.error, OSError) and feedback_counts(ctx.orch) == (1, 1)
    r_live = ctx.orch.plant_control.dispatch_controller.merit_source.trade_history_r[BAY][0]
    fresh = restart(tmp_path, ctx)                                             # controller memory died with the process
    assert feedback_counts(fresh.orch) == (0, 0)
    fresh.runtime.restore(fresh.orch)
    assert_converged(fresh.orch, fresh.runtime, fresh.store, fresh.broker, r=r_live)    # same R the live process applied
    assert len(sell_fills(fresh.old_broker)) == 1 and fresh.placed == []
    assert_idempotent_afterwards(fresh.orch)
    fresh.store.close()


def test_window_c_restored_effect_via_automatic_boot_checkpoint_is_not_applied_twice(tmp_path):
    """No manual checkpoint: production wiring in _finalize_exit made effects + DONE durable before BARRIER 2 failed."""
    ctx = interrupted(tmp_path, account=True, fail_close_calls=(2,))
    assert isinstance(ctx.error, OSError) and feedback_counts(ctx.orch) == (1, 1)
    fresh = restart(tmp_path, ctx)
    assert feedback_counts(fresh.orch) == (0, 0) and fresh.orch._close_feedback_receipts == {}
    receipt = fresh.orch._reconcile_morning_startup(account_id=ACCOUNT, broker=fresh.broker)
    assert receipt["prepared"] is True and receipt["restored_positions"] == 0 and receipt["admissions_allowed"] is False
    # controller state AND the DONE guard were restored from the durable checkpoint
    assert feedback_counts(fresh.orch) == (1, 1)
    assert fresh.orch._close_feedback_receipts == {f"trade_id:{TRADE_ID}": "DONE"}
    applications = []
    merit = fresh.orch.plant_control.dispatch_controller.merit_source
    bay = fresh.orch.real_plant_dcs.bays[BAY]
    real_register, real_outcome = merit.register_trade, bay.register_outcome
    merit.register_trade = lambda *a, **k: (applications.append("merit"), real_register(*a, **k))[1]
    bay.register_outcome = lambda *a, **k: (applications.append("bay"), real_outcome(*a, **k))[1]
    assert fresh.orch.recover_exit_intents() == [FINALIZED_FROM_FILLED]
    assert applications == []                                                  # the restored lineage already carries the effect
    assert_converged(fresh.orch, fresh.runtime, fresh.store, fresh.broker)
    assert len(sell_fills(fresh.old_broker)) == 1 and fresh.placed == []
    assert_idempotent_afterwards(fresh.orch)
    fresh.store.close()


def test_account_mode_successful_close_leaves_a_boot_checkpoint_consistent_with_the_latest_store(tmp_path):
    ctx = interrupted(tmp_path, account=True)
    assert ctx.error is None and ctx.handled is True and ctx.close_calls == 2
    assert_converged(ctx.orch, ctx.runtime, ctx.store, ctx.orch.broker)
    fresh = restart(tmp_path, ctx)                                             # no tick-boundary/FINAL checkpoint ever ran
    receipt = fresh.orch._reconcile_morning_startup(account_id=ACCOUNT, broker=fresh.broker)
    assert receipt["prepared"] is True and receipt["restored_positions"] == 0 and receipt["admissions_allowed"] is False
    assert feedback_counts(fresh.orch) == (1, 1) and fresh.orch._close_feedback_receipts == {f"trade_id:{TRADE_ID}": "DONE"}
    assert fresh.orch.recover_exit_intents() == []                             # FINALIZED: nothing pending, nothing re-applied
    assert feedback_counts(fresh.orch) == (1, 1)
    assert len(sell_fills(fresh.old_broker)) == 1 and fresh.placed == []
    fresh.store.close()


def test_account_mode_b2_row_and_boot_pin_commit_atomically_with_no_gap(tmp_path):
    ctx = open_b_cnc(tmp_path, account=True)
    arm(ctx)
    pins = []
    real_checkpoint = ctx.orch._checkpoint_morning_recovery

    def spy(**kwargs):
        pins.append(ctx.store.connection.in_transaction)                       # the in-B2 pin runs inside the open B2 transaction
        return real_checkpoint(**kwargs)
    ctx.orch._checkpoint_morning_recovery = spy
    drive_exit(ctx)
    assert ctx.error is None and pins == [False, True]                         # pre-B2 pin top-level, B2 pin inside the transaction
    row = durable(ctx.store)
    assert row["status"] == EXIT_FINALIZED
    StateRecoveryJournal(ctx.store.connection).load_boot()                     # latest pin already matches the FINALIZED row
    assert ctx.store.connection.in_transaction is False
    ctx.store.close()


def test_account_mode_inside_b2_checkpoint_failure_rolls_row_back_and_recovers_without_duplicate_feedback(tmp_path):
    ctx = open_b_cnc(tmp_path, account=True)
    arm(ctx)
    orch, real_checkpoint, calls = ctx.orch, ctx.orch._checkpoint_morning_recovery, {"n": 0}

    def flaky_checkpoint(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:                                                    # 1 = pre-B2 pin, 2 = pin inside the B2 transaction
            raise OSError("injected inside-B2 boot checkpoint failure")
        return real_checkpoint(**kwargs)
    orch._checkpoint_morning_recovery = flaky_checkpoint
    drive_exit(ctx)
    assert isinstance(ctx.error, OSError) and "inside-B2" in str(ctx.error) and orch._execution_halted is True
    assert ctx.close_calls == 2 and ctx.close_log[-1][3] is False
    assert ctx.store.connection.in_transaction is False
    row = durable(ctx.store)                                                   # FINALIZED + DONE receipt rolled back
    assert row["state"] == CLOSED and row["status"] == EXIT_FILLED and row["receipts"] == {}
    assert engine_b_tracked(ctx.runtime) == ctx.close_log[-1][1]               # eviction happens only after commit
    StateRecoveryJournal(ctx.store.connection).load_boot()                     # the pre-B2 pin still matches the rows
    assert feedback_counts(orch) == (1, 1)
    fresh = restart(tmp_path, ctx)
    receipt = fresh.orch._reconcile_morning_startup(account_id=ACCOUNT, broker=fresh.broker)
    assert receipt["prepared"] is True and receipt["admissions_allowed"] is False
    assert feedback_counts(fresh.orch) == (1, 1)
    applications = []
    merit = fresh.orch.plant_control.dispatch_controller.merit_source
    real_register = merit.register_trade
    merit.register_trade = lambda *a, **k: (applications.append("merit"), real_register(*a, **k))[1]
    assert fresh.orch.recover_exit_intents() == [FINALIZED_FROM_FILLED]
    assert applications == [] and feedback_counts(fresh.orch) == (1, 1)        # zero duplicate feedback
    assert_converged(fresh.orch, fresh.runtime, fresh.store, fresh.broker)
    assert len(sell_fills(fresh.old_broker)) == 1 and fresh.placed == []
    fresh.store.close()


def test_account_mode_checkpoint_persistence_fault_before_barrier_2_then_retry_converges(tmp_path):
    ctx = open_b_cnc(tmp_path, account=True)
    arm(ctx)
    orch, real_checkpoint, calls = ctx.orch, ctx.orch._checkpoint_morning_recovery, {"n": 0}

    def flaky_checkpoint(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected boot checkpoint persistence failure")
        return real_checkpoint(**kwargs)
    orch._checkpoint_morning_recovery = flaky_checkpoint
    drive_exit(ctx)
    assert isinstance(ctx.error, OSError) and "checkpoint persistence" in str(ctx.error)
    assert orch._execution_halted is True
    assert ctx.close_calls == 1                                                # BARRIER 2 not attempted without the durable effect
    row = durable(ctx.store)
    assert row["state"] == CLOSED and row["status"] == EXIT_FILLED and row["receipts"] == {}
    assert feedback_counts(orch) == (1, 1)
    assert [o["action"] for o in orch.recover_exit_intents()] == ["FINALIZED_FROM_FILLED"]
    assert calls["n"] == 3                                                     # failed pre-barrier, retried pre-barrier, in-B2 pin
    assert_converged(orch, ctx.runtime, ctx.store, orch.broker)                # feedback still applied exactly once
    assert_idempotent_afterwards(orch)
    fresh = restart(tmp_path, ctx)
    receipt = fresh.orch._reconcile_morning_startup(account_id=ACCOUNT, broker=fresh.broker)
    assert receipt["prepared"] is True and feedback_counts(fresh.orch) == (1, 1)
    assert fresh.orch.recover_exit_intents() == [] and fresh.placed == []
    fresh.store.close()


def test_window_c_stale_pre_exit_boot_checkpoint_fails_closed_and_restores_nothing(tmp_path):
    ctx = open_b_cnc(tmp_path, checkpoint=True)                                # the last checkpoint a tick boundary would have left
    arm(ctx, fail_close_calls=(2,))
    drive_exit(ctx)
    assert isinstance(ctx.error, OSError) and feedback_counts(ctx.orch) == (1, 1)
    fresh = restart(tmp_path, ctx)
    with pytest.raises(RuntimeError, match="advanced beyond boot checkpoint"):
        fresh.orch._reconcile_morning_startup(account_id=ACCOUNT, broker=fresh.broker)
    assert fresh.orch._execution_halted is True
    assert feedback_counts(fresh.orch) == (0, 0) and fresh.orch._close_feedback_receipts == {}
    assert fresh.orch.open_trades == {} and fresh.orch.completed_trades == []
    row = durable(fresh.store)                                                 # the unfinished exit is left for explicit recovery
    assert row["state"] == CLOSED and row["status"] == EXIT_FILLED and row["receipts"] == {}
    assert len(sell_fills(fresh.broker)) == 1 and fresh.placed == []
    fresh.store.close()


# --------------------------------------------------------------------------- crash before the FILLED fact / ambiguity
def test_reserved_never_executed_fresh_restart_resubmits_the_same_event_once_with_the_normal_r(tmp_path):
    ctx = interrupted(tmp_path, crash_before_send=True)
    assert isinstance(ctx.error, RuntimeError) and "before the broker order was sent" in str(ctx.error)
    assert durable(ctx.store)["status"] == EXIT_RESERVED and durable(ctx.store)["state"] == B_OPEN
    assert sell_fills(ctx.orch.broker) == [] and ctx.orch.broker.get_position(SYMBOL)["product"] == "CNC"
    fresh = restart(tmp_path, ctx)
    assert fresh.broker.find_client_order(EVENT_ID) is None
    fresh.runtime.restore(fresh.orch)                                          # no exit-controller state: R comes from the durable record
    assert fresh.placed == [EVENT_ID]
    assert_converged(fresh.orch, fresh.runtime, fresh.store, fresh.broker)     # R equals the fill-derived normal-path formula
    assert_idempotent_afterwards(fresh.orch)
    fresh.store.close()


@pytest.mark.parametrize("fresh_process", [False, True], ids=["in_process", "fresh_restored_broker"])
def test_reserved_with_flat_broker_and_no_client_order_is_ambiguous_and_never_resubmits(tmp_path, fresh_process):
    ctx = interrupted(tmp_path, crash_before_send=True)
    orch = ctx.orch
    # the CNC position disappears for a reason that is NOT our close (no client order exists for our identity)
    orch.broker.place_order(SYMBOL, "SELL", QTY, "MARKET", 100.0, orch.safety_contract.as_dict(), orch.registry)
    assert orch.broker.get_position(SYMBOL)["quantity"] == 0 and orch.broker.find_client_order(EVENT_ID) is None
    if fresh_process:
        fresh = restart(tmp_path, ctx)
        engine, store, broker, act = fresh.orch, fresh.store, fresh.broker, lambda: fresh.runtime.restore(fresh.orch)
    else:
        engine, store, broker, act = orch, ctx.store, orch.broker, orch.recover_exit_intents
    fills_before = len(broker.fills)
    with pytest.raises(CombinedCycleReconciliationError, match="ambiguous"):
        act()
    assert engine._execution_halted is True and len(broker.fills) == fills_before        # no resubmission
    row = durable(store)
    assert row["status"] == EXIT_RESERVED and row["state"] == B_OPEN
    assert engine.completed_trades == [] and feedback_counts(engine) == (0, 0)
    store.close()


def _protection(broker):
    return next(iter(broker._contingent_protection.values()))


MISMATCHES = {
    "product_changed_to_mis": lambda broker: broker.positions[SYMBOL].update(product="MIS"),
    "protection_missing": lambda broker: broker._contingent_protection.clear(),
    "protection_trigger_differs": lambda broker: _protection(broker).update(trigger_price=STOP - 5.0),
    "protection_quantity_differs": lambda broker: _protection(broker).update(pending_quantity=QTY - 1),
    "protection_product_differs": lambda broker: _protection(broker).update(product="MIS"),
    "protection_cancelled": lambda broker: _protection(broker).update(status="CANCELLED"),
}


@pytest.mark.parametrize("fresh_process", [False, True], ids=["in_process", "fresh_restored_broker"])
@pytest.mark.parametrize("mismatch", list(MISMATCHES))
def test_reserved_never_executed_with_broker_product_or_protection_mismatch_halts_without_resubmitting(
        tmp_path, mismatch, fresh_process):
    ctx = interrupted(tmp_path, crash_before_send=True)
    assert isinstance(ctx.error, RuntimeError) and len(ctx.place_calls) == 1
    ctx.flags.crash_before_send = False                                        # a resubmission WOULD now reach the broker
    if fresh_process:
        fresh = restart(tmp_path, ctx)
        engine, store, broker = fresh.orch, fresh.store, fresh.broker
        sent = lambda: fresh.placed                                            # noqa: E731
    else:
        engine, store, broker = ctx.orch, ctx.store, ctx.orch.broker
        sent = lambda: ctx.place_calls                                         # noqa: E731
    assert broker.get_position(SYMBOL)["quantity"] == QTY and broker.find_client_order(EVENT_ID) is None
    MISMATCHES[mismatch](broker)
    quantity = broker.get_position(SYMBOL)["quantity"]
    sent_before, fills_before, orders_before = len(sent()), len(broker.fills), len(broker.orders)
    with pytest.raises(CombinedCycleReconciliationError, match="refusing to resubmit"):
        engine.recover_exit_intents()
    assert engine._execution_halted is True
    assert len(sent()) == sent_before and len(broker.fills) == fills_before and len(broker.orders) == orders_before
    assert broker.get_position(SYMBOL)["quantity"] == quantity and broker.find_client_order(EVENT_ID) is None
    row = durable(store)
    assert row["status"] == EXIT_RESERVED and row["state"] == B_OPEN and row["order_id"] is None
    assert sell_fills(broker) == [] and engine.completed_trades == [] and feedback_counts(engine) == (0, 0)
    store.close()


def test_reserved_never_executed_valid_fresh_cnc_position_and_protection_still_resubmits(tmp_path):
    """Positive control for the continuity gate: unmutated CNC product, quantity and protection pass reconcile."""
    ctx = interrupted(tmp_path, crash_before_send=True)
    fresh = restart(tmp_path, ctx)
    assert fresh.broker.get_position(SYMBOL)["product"] == "CNC" and _protection(fresh.broker)["product"] == "CNC"
    assert fresh.orch.recover_exit_intents() == [dict(trade_id=TRADE_ID, action="RESUBMITTED_SAME_EVENT", resubmitted=True)]
    assert fresh.placed == [EVENT_ID]
    assert_converged(fresh.orch, fresh.runtime, fresh.store, fresh.broker)
    fresh.store.close()


# --------------------------------------------------------------------------- explicit broker rejection
def test_explicit_rejection_keeps_reserved_without_a_broker_identity_and_the_retry_reuses_the_same_event(tmp_path):
    """Deliberate policy: the paper broker records a client identity ONLY for a successful order, so a rejected close
    leaves the durable intent RESERVED, nothing at the broker for EVENT_ID, and the position untouched.  Recovery then
    treats it as 'never executed' (continuity gate passes) and resubmits the SAME event id; it never invents a new one."""
    ctx = open_b_cnc(tmp_path)
    arm(ctx)
    orch = ctx.orch
    real_place, identities = orch.broker.place_order, []

    def reject_first(*args, **kwargs):
        identity = kwargs.get("client_order_id")
        identities.append(identity)
        if len(identities) == 1:
            return {"passed": False, "reasons": ["injected broker rejection"]}
        return real_place(*args, **kwargs)
    orch.broker.place_order = reject_first
    drive_exit(ctx)
    assert ctx.error is None and identities == [EVENT_ID]
    row = durable(ctx.store)
    assert row["status"] == EXIT_RESERVED and row["state"] == B_OPEN and row["order_id"] is None
    assert orch.broker.find_client_order(EVENT_ID) is None and sell_fills(orch.broker) == []
    assert orch.broker.get_position(SYMBOL)["quantity"] == QTY and orch.completed_trades == []
    assert orch.recover_exit_intents() == [dict(trade_id=TRADE_ID, action="RESUBMITTED_SAME_EVENT", resubmitted=True)]
    assert identities == [EVENT_ID, EVENT_ID]                                  # same stable identity on the retry
    assert_converged(orch, ctx.runtime, ctx.store, orch.broker)
    assert_idempotent_afterwards(orch)
    assert len(identities) == 2                                                # nothing further was sent
    ctx.store.close()


# --------------------------------------------------------------------------- broker identity across snapshot/restore
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_client_order_identity_survives_snapshot_restore_and_a_retry_is_deduplicated(tmp_path, side):
    orch, _, store, _, _, _ = build_orchestrator(tmp_path)
    old, config, registry = orch.broker, orch.safety_contract.as_dict(), orch.registry
    first = old.place_order("INFY", side, 5, "MARKET", 100.0, config, registry, client_order_id="exit:probe")
    assert first["passed"] and "duplicate_submission" not in first
    snapshot = old.snapshot()
    assert "exit:probe" in snapshot["paper_state"]["client_orders"]

    fresh = CostedPaperBrokerAdapter(slippage_fraction=old.slippage_fraction)
    fresh.restore_snapshot(snapshot)
    assert fresh is not old

    def state(broker):
        return (len(broker.fills), broker.get_position("INFY")["quantity"], broker.booked_costs, broker.realized_pnl,
                len(broker.orders), len(broker.cost_ledger))
    before = state(fresh)
    assert before == state(old)
    found = fresh.find_client_order("exit:probe")
    assert found["order_id"] == first["order_id"] and list(found["fingerprint"]) == ["INFY", side, 5, "MARKET"]
    for price in (90.0, 101.0, 150.0):
        again = fresh.place_order("INFY", side, 5, "MARKET", price, config, registry, client_order_id="exit:probe")
        assert again["duplicate_submission"] is True
        assert (again["order_id"], again["filled_price"], again["cost"]) == (first["order_id"], first["filled_price"], first["cost"])
    assert state(fresh) == before                                              # no second fill, cost, order or P&L
    with pytest.raises(ValueError, match="identity collision"):
        fresh.place_order("INFY", side, 6, "MARKET", 100.0, config, registry, client_order_id="exit:probe")
    assert state(fresh) == before
    with pytest.raises(ValueError, match="empty paper broker"):                # restore never merges into a used adapter
        fresh.restore_snapshot(snapshot)
    other = fresh.place_order("INFY", side, 5, "MARKET", 100.0, config, registry, client_order_id="exit:other")
    assert "duplicate_submission" not in other and len(fresh.fills) == before[0] + 1      # identity, not shape, dedups
    store.close()
