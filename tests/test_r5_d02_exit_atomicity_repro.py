"""R5-D02.0 DEFECT-REPRODUCTION / CHARACTERISATION tests for exit-side atomicity.

*** These tests document OBSERVED CURRENT BEHAVIOUR of the exit path when the durable close fails AFTER the broker close has
*** succeeded.  They are NOT a desired-behaviour regression and do NOT bless the observed state as correct.  A later repair
*** activity is expected to change several assertions in the "DEFECT_*" tests (they must then be replaced, not loosened).

Real objects: Revision2ExternalEngineOrchestrator.run() on the D01.1 derived-real TITAN replay, the real CostedPaperBrokerAdapter,
the real CombinedCycleRuntime and CombinedCycleStore (SQLite).  Failure injection is TEST-LOCAL: an instance-level wrapper around
``runtime.close`` raises on its FIRST call only (after the real paper broker has already closed the position); production source
is not edited and no final state is mutated by hand.  Observation spies record state and then call the real method unchanged.
"""
import copy
import json

import pytest

from revision5.combined_cycle_runtime import CombinedCycleReconciliationError
from revision5.combined_cycle_store import CombinedCycleStore
from tests.test_r5_d01_orchestrator_run_integration import SYMBOL, build_orchestrator


class InjectedDurableCloseFailure(OSError):
    pass


def snapshot(label, orch, runtime, store, trade_id):
    fills = orch.broker.fills
    merit = orch.plant_control.dispatch_controller.merit_source
    position = orch.broker.get_position(SYMBOL)
    try:
        durable = store.load(trade_id).record.lifecycle_state
    except KeyError:
        durable = "ABSENT"
    record = orch._position_lifecycle.get(trade_id)
    return dict(
        label=label,
        broker_quantity=position.get("quantity"), broker_product=position.get("product"),
        open_trades_has_symbol=SYMBOL in orch.open_trades,
        completed_trades=len(orch.completed_trades),
        completed_trade_ids=[t.get("trade_id") for t in orch.completed_trades],
        in_memory_lifecycle=record.lifecycle_state if record is not None else "ABSENT",
        durable_lifecycle=durable,
        durable_open_ids=[s.record.position_id for s in store.list_open()],
        engine_b_tracks_position=trade_id in runtime.engine_b.export_state()["positions"],
        broker_fills=len(fills), broker_sell_fills=sum(1 for f in fills if f["side"] == "SELL"),
        broker_buy_fills=sum(1 for f in fills if f["side"] == "BUY"),
        feedback_receipts=dict(orch._close_feedback_receipts),
        merit_history_total=sum(len(v) for v in merit.trade_history_r.values()),
        execution_halted=bool(orch._execution_halted),
    )


def run_replay(tmp_path, *, inject_first_close=False, inject=None):
    """Run the real replay.  Returns (orch, runtime, store, trace, error)."""
    orch, runtime, store, _, titan, warmup = build_orchestrator(tmp_path)
    trace = dict(snapshots={}, exit_calls=[], close_calls=0, trade=None, exit_price=None, reason=None, ts=None)
    real_exit, real_close = orch._execute_exit, runtime.close

    def exit_spy(symbol, timestamp, trade, exit_price, reason):                 # TEST_OBSERVATION_SPY (S0)
        tid = trade["trade_id"]
        trace["trade"], trace["exit_price"], trace["reason"], trace["ts"] = copy.deepcopy(trade), exit_price, reason, timestamp
        trace["snapshots"]["S0"] = snapshot("S0 before exit attempt", orch, runtime, store, tid)
        trace["exit_calls"].append(timestamp)
        return real_exit(symbol, timestamp, trade, exit_price, reason)

    def close_spy(record, trade, **kwargs):                                       # S1 + optional injected failure
        trace["close_calls"] += 1
        if trace["close_calls"] == 1:
            trace["snapshots"]["S1"] = snapshot("S1 broker closed, before durable close", orch, runtime, store, trade["trade_id"])
            trace["completed_at_first_close"] = copy.deepcopy(kwargs.get("completed_trade"))
            if inject_first_close:
                raise InjectedDurableCloseFailure("injected durable close failure (first call)")
        return real_close(record, trade, **kwargs)

    orch._execute_exit = exit_spy
    runtime.close = close_spy
    if inject is not None:
        inject(orch)
    error = None
    try:
        orch.run({SYMBOL: titan}, warmup=warmup)
    except BaseException as exc:                                                  # noqa: BLE001 - recorded, re-examined by tests
        error = exc
    trade_id = trace["trade"]["trade_id"] if trace["trade"] else None
    trace["snapshots"]["END"] = snapshot("END of run() or failure", orch, runtime, store, trade_id) if trade_id else None
    trace["trade_id"] = trade_id
    trace["real_exit"], trace["real_close"] = real_exit, real_close
    return orch, runtime, store, trace, error


def test_control_normal_exit_converges_to_one_coherent_state(tmp_path):
    orch, runtime, store, trace, error = run_replay(tmp_path)
    assert error is None
    s0, s1, end = trace["snapshots"]["S0"], trace["snapshots"]["S1"], trace["snapshots"]["END"]
    qty = trace["trade"]["quantity"]
    # S0: open everywhere
    assert s0["broker_quantity"] == -qty and s0["broker_product"] == "MIS" and s0["open_trades_has_symbol"]
    assert s0["completed_trades"] == 0 and s0["durable_lifecycle"] == "A_OPEN" and s0["in_memory_lifecycle"] == "A_OPEN"
    assert s0["engine_b_tracks_position"] is False
    # S1: broker already flat, books already moved, durable still A_OPEN at the instant durable close begins
    assert s1["broker_quantity"] == 0 and s1["completed_trades"] == 1 and s1["open_trades_has_symbol"] is False
    assert s1["in_memory_lifecycle"] == "CLOSED" and s1["durable_lifecycle"] == "A_OPEN"
    # END: single coherent closed state
    assert end["broker_quantity"] == 0 and end["open_trades_has_symbol"] is False
    assert end["completed_trades"] == 1 and end["completed_trade_ids"] == [trace["trade_id"]]
    assert end["in_memory_lifecycle"] == end["durable_lifecycle"] == "CLOSED" and end["durable_open_ids"] == []
    assert end["engine_b_tracks_position"] is False and end["execution_halted"] is False
    assert end["broker_buy_fills"] == 1 and end["broker_sell_fills"] == 1       # one entry leg, one exit leg
    assert end["merit_history_total"] == 1 and list(end["feedback_receipts"].values()) == ["DONE"]
    assert trace["close_calls"] == 2, "durable close is invoked twice on the success path (pre- and post-feedback)"
    print("D02_CONTROL " + json.dumps(trace["snapshots"], sort_keys=True, default=str))


def test_DEFECT_REPRODUCTION_broker_exit_succeeds_then_durable_close_fails(tmp_path):
    orch, runtime, store, trace, error = run_replay(tmp_path, inject_first_close=True)
    assert isinstance(error, InjectedDurableCloseFailure)
    s0, s1, s2 = trace["snapshots"]["S0"], trace["snapshots"]["S1"], trace["snapshots"]["END"]
    qty = trace["trade"]["quantity"]
    assert s0["broker_quantity"] == -qty and s0["durable_lifecycle"] == "A_OPEN"
    # S1: the external side effect has already happened, durable state has not
    assert s1["broker_quantity"] == 0 and s1["broker_buy_fills"] == 1 and s1["durable_lifecycle"] == "A_OPEN"
    # S2 (OBSERVED, NOT DESIRED): contradictory ownership state after the failure propagates
    assert s2["broker_quantity"] == 0                                   # broker FLAT
    assert s2["completed_trades"] == 1                                  # completed trade recorded
    assert s2["open_trades_has_symbol"] is False                        # open_trades already cleared (earlier hypothesis refuted)
    assert s2["in_memory_lifecycle"] == "CLOSED"                        # in-memory lifecycle CLOSED
    assert s2["durable_lifecycle"] == "A_OPEN" and s2["durable_open_ids"] == [trace["trade_id"]]   # durable still OPEN
    assert s2["execution_halted"] is True
    # OBSERVED, NOT DESIRED: realized-R close feedback never ran (raise happens before the feedback block)
    assert s2["feedback_receipts"] == {} and s2["merit_history_total"] == 0
    assert trace["close_calls"] == 1
    print("D02_DEFECT " + json.dumps(trace["snapshots"], sort_keys=True, default=str))


def test_DEFECT_REPRODUCTION_retry_paths_after_the_failure(tmp_path):
    orch, runtime, store, trace, error = run_replay(tmp_path, inject_first_close=True)
    assert isinstance(error, InjectedDurableCloseFailure)
    tid, trade = trace["trade_id"], trace["trade"]
    before = snapshot("S2", orch, runtime, store, tid)
    runtime.close = trace["real_close"]                                  # injection removed; no state cleaned by hand

    # (i) the production exit function is retried with the saved trade: refused because the broker is already flat
    with pytest.raises(Exception) as refused:
        trace["real_exit"](SYMBOL, trace["ts"], copy.deepcopy(trade), trace["exit_price"], "retry_after_failure")
    s3a = snapshot("S3a after retry of _execute_exit", orch, runtime, store, tid)
    assert s3a["broker_sell_fills"] == before["broker_sell_fills"] and s3a["broker_buy_fills"] == before["broker_buy_fills"]  # no duplicate broker close
    assert s3a["completed_trades"] == 1                                                                                   # no duplicate completed trade
    assert s3a["durable_lifecycle"] == "A_OPEN"                                                                           # durable unchanged
    print("D02_RETRY_EXECUTE_EXIT_REFUSED " + type(refused.value).__name__ + ": " + str(refused.value)[:160])

    # (ii) no production caller re-invokes the durable close; the already-CLOSED in-memory record makes
    #      _close_position_lifecycle a no-op, so the durable row stays A_OPEN
    orch._close_position_lifecycle(trade, completed=orch.completed_trades[0])
    s3b = snapshot("S3b after _close_position_lifecycle retry", orch, runtime, store, tid)
    assert s3b["durable_lifecycle"] == "A_OPEN" and s3b["completed_trades"] == 1

    # (iii) the public close() API invoked directly (NOT a production retry path; capability evidence only)
    runtime.close(orch._position_lifecycle[tid], trade, broker=orch.broker, completed_trade=orch.completed_trades[0],
                  close_feedback_receipts=orch._close_feedback_receipts)
    s3c = snapshot("S3c after direct runtime.close()", orch, runtime, store, tid)
    assert s3c["durable_lifecycle"] == "CLOSED" and s3c["durable_open_ids"] == []
    assert s3c["feedback_receipts"] == {} and s3c["merit_history_total"] == 0        # durable fixed, feedback permanently missing
    assert s3c["broker_buy_fills"] == before["broker_buy_fills"] and s3c["completed_trades"] == 1
    print("D02_RETRY " + json.dumps(dict(S2=before, S3a=s3a, S3b=s3b, S3c=s3c), sort_keys=True, default=str))


def test_DEFECT_REPRODUCTION_restart_recovery_cannot_consume_the_state(tmp_path):
    orch, runtime, store, trace, error = run_replay(tmp_path, inject_first_close=True)
    assert isinstance(error, InjectedDurableCloseFailure)
    tid = trace["trade_id"]
    runtime.close = trace["real_close"]
    # a restarted process sees the durable file with the position still open ...
    reopened = CombinedCycleStore(tmp_path / "cycle.db")
    try:
        assert [s.record.position_id for s in reopened.list_open()] == [tid]
        assert reopened.load(tid).record.lifecycle_state == "A_OPEN"
    finally:
        reopened.close()
    # ... and the existing recovery function refuses to reconcile it against the flat broker
    with pytest.raises(CombinedCycleReconciliationError):
        runtime.restore(orch)
    assert SYMBOL not in orch.open_trades                                # nothing is silently re-adopted


def test_matrix_case1_failure_before_broker_exit_leaves_everything_open(tmp_path):
    def inject(orch):
        real = orch._verify_broker_position_reconciles

        def failing(symbol, trade):
            raise RuntimeError("injected pre-broker failure")
        orch._verify_broker_position_reconciles = failing
    orch, runtime, store, trace, error = run_replay(tmp_path, inject=inject)
    assert isinstance(error, RuntimeError) and "pre-broker" in str(error)
    s = trace["snapshots"]["END"]
    assert s["broker_quantity"] == -trace["trade"]["quantity"] and s["open_trades_has_symbol"] is True
    assert s["completed_trades"] == 0 and s["durable_lifecycle"] == "A_OPEN" and s["broker_buy_fills"] == 0


def test_matrix_case4_later_bookkeeping_failure_after_durable_close_is_coherent(tmp_path):
    def inject(orch):
        def failing(*args, **kwargs):
            raise RuntimeError("injected post-durable bookkeeping failure")
        orch.closed_loop.record_outcome = failing
    orch, runtime, store, trace, error = run_replay(tmp_path, inject=inject)
    assert isinstance(error, RuntimeError) and "post-durable" in str(error)
    s = trace["snapshots"]["END"]
    assert s["broker_quantity"] == 0 and s["open_trades_has_symbol"] is False and s["completed_trades"] == 1
    assert s["in_memory_lifecycle"] == s["durable_lifecycle"] == "CLOSED" and s["durable_open_ids"] == []
    assert list(s["feedback_receipts"].values()) == ["DONE"] and s["merit_history_total"] == 1
