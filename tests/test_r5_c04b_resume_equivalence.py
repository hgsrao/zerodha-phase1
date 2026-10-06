"""C04-B acceptance: certified restart must equal uninterrupted replay.

Synthetic deterministic replay fixture only.
This is continuity/safety evidence, not profitability evidence.
"""
from copy import deepcopy

import pandas as pd
import pytest

from tests.test_r5_d01_orchestrator_run_integration import (
    SYMBOL,
    build_orchestrator,
)
from revision2_external.paper_execution import CostedPaperBrokerAdapter


class SimulatedCrash(RuntimeError):
    pass


def restored_broker(snapshot):
    broker = CostedPaperBrokerAdapter()
    broker.restore_snapshot(deepcopy(snapshot))
    return broker


def run_until_checkpoint_crash(engine, bars, warmup):
    """Crash immediately after a real mid-run recovery checkpoint."""
    engine.recovery_account_id = "fixture-account"

    real_checkpoint = engine._checkpoint_morning_recovery
    captured = {}

    def crash_after_checkpoint(*, account_id, timestamp):
        result = real_checkpoint(account_id=account_id, timestamp=timestamp)

        # Ignore FINAL and wait until execution has genuinely advanced.
        if timestamp != "FINAL" and engine._resume_processed_bar_indices:
            cursor = engine._resume_processed_bar_indices.get(SYMBOL)
            # Cross a meaningful execution boundary rather than crashing on
            # the first post-warmup tick.
            if cursor is not None and cursor >= warmup + 8:
                captured["timestamp"] = timestamp
                captured["cursor"] = dict(engine._resume_processed_bar_indices)
                captured["broker_snapshot"] = deepcopy(engine.broker.snapshot())
                raise SimulatedCrash("simulated process loss after durable checkpoint")

        return result

    engine._checkpoint_morning_recovery = crash_after_checkpoint

    with pytest.raises(SimulatedCrash):
        engine.run({SYMBOL: bars}, warmup=warmup)

    assert captured["cursor"]
    assert SYMBOL in captured["cursor"]
    assert engine._resume_data_identity
    return captured


def test_checkpoint_restart_matches_uninterrupted_replay(tmp_path):
    # ---------------------------------------------------------
    # A. Uninterrupted reference replay
    # ---------------------------------------------------------
    baseline_path = tmp_path / "baseline"
    baseline_path.mkdir()

    baseline, _, baseline_store, _, baseline_bars, warmup = \
        build_orchestrator(baseline_path)

    baseline_report = baseline.run(
        {SYMBOL: baseline_bars},
        warmup=warmup,
    )

    # ---------------------------------------------------------
    # B. Interrupted replay: real durable checkpoint, then crash
    # ---------------------------------------------------------
    restart_path = tmp_path / "restart"
    restart_path.mkdir()

    first, _, first_store, _, restart_bars, restart_warmup = \
        build_orchestrator(restart_path)

    assert restart_warmup == warmup

    crash = run_until_checkpoint_crash(
        first,
        restart_bars,
        restart_warmup,
    )

    checkpoint_cursor = crash["cursor"][SYMBOL]
    assert checkpoint_cursor >= warmup + 8

    # Snapshot only represents the external broker surviving the
    # process loss. Durable controller/lifecycle state comes from
    # the recovery database.
    broker = restored_broker(crash["broker_snapshot"])

    first_store.close()

    # ---------------------------------------------------------
    # C. Fresh process + durable preparation
    # ---------------------------------------------------------
    resumed, _, resumed_store, _, resumed_bars, resumed_warmup = \
        build_orchestrator(restart_path)

    assert resumed_warmup == warmup

    receipt = resumed._reconcile_morning_startup(
        account_id="fixture-account",
        broker=broker,
    )

    assert receipt["prepared"]
    assert not receipt["admissions_allowed"]
    assert resumed._morning_recovery_prepared
    assert resumed._execution_halted

    # ---------------------------------------------------------
    # D. Certified continuation over the SAME complete dataset
    # ---------------------------------------------------------
    resumed_report = resumed.run(
        {SYMBOL: resumed_bars},
        warmup=resumed_warmup,
    )

    # ---------------------------------------------------------
    # E. Core deterministic equivalence
    # ---------------------------------------------------------
    assert resumed_report["trades"] == baseline_report["trades"]
    assert resumed.completed_trades == baseline.completed_trades
    assert resumed.open_trades == baseline.open_trades

    # Independent paper replays legitimately generate different UUID order
    # IDs and wall-clock submission/fill timestamps. Compare deterministic
    # execution semantics rather than those process-local identifiers.
    def broker_semantics(broker):
        snap = broker.snapshot()
        paper = snap["paper_state"]

        orders = sorted(
            (
                row["symbol"], row["side"], row["quantity"],
                row["order_type"], row["state"],
                row["filled_quantity"], row["filled_price"],
            )
            for row in paper["orders"].values()
        )
        fills = sorted(
            (
                row["symbol"], row["side"], row["quantity"],
                row["price"], row["market_price"],
            )
            for row in paper["fills"]
        )
        costs = sorted(row["cost"] for row in paper["cost_ledger"])

        return {
            "positions": paper["positions"],
            "orders": orders,
            "fills": fills,
            "realized_pnl": paper["realized_pnl"],
            "booked_costs": paper["booked_costs"],
            "costs": costs,
            "conversions": paper["conversions"],
            "protection": paper["protection"],
            "gtts": paper["gtts"],
            "slippage_fraction": paper["slippage_fraction"],
        }

    assert broker_semantics(resumed.broker) == broker_semantics(baseline.broker)

    assert resumed._resume_processed_bar_indices == \
        baseline._resume_processed_bar_indices
    assert resumed._resume_entry_bar_index == \
        baseline._resume_entry_bar_index
    assert resumed._resume_ticks_since_reweight == \
        baseline._resume_ticks_since_reweight

    assert resumed._portfolio_weights == baseline._portfolio_weights
    assert resumed._last_close == baseline._last_close
    assert resumed._conviction_rank == baseline._conviction_rank

    assert {
        symbol: {
            name: list(values)
            for name, values in histories.items()
        }
        for symbol, histories in resumed._conviction_history.items()
    } == {
        symbol: {
            name: list(values)
            for name, values in histories.items()
        }
        for symbol, histories in baseline._conviction_history.items()
    }

    assert resumed._equity_curve == baseline._equity_curve
    assert resumed._mtm_equity_curve == baseline._mtm_equity_curve

    resumed_store.close()
    baseline_store.close()


def test_resume_rejects_tampered_pre_cursor_data(tmp_path):
    restart_path = tmp_path / "tamper"
    restart_path.mkdir()

    first, _, first_store, _, bars, warmup = \
        build_orchestrator(restart_path)

    crash = run_until_checkpoint_crash(first, bars, warmup)
    cursor = crash["cursor"][SYMBOL]
    broker = restored_broker(crash["broker_snapshot"])
    first_store.close()

    resumed, _, resumed_store, _, resumed_bars, resumed_warmup = \
        build_orchestrator(restart_path)

    receipt = resumed._reconcile_morning_startup(
        account_id="fixture-account",
        broker=broker,
    )

    assert receipt["prepared"]
    assert resumed._morning_recovery_prepared
    assert resumed._execution_halted

    # Change data already certified by the durable checkpoint.
    tampered = resumed_bars.copy(deep=True)
    tamper_idx = max(0, cursor - 1)
    tampered.iloc[tamper_idx, tampered.columns.get_loc("close")] += 1.0

    broker_before = deepcopy(resumed.broker.snapshot())

    with pytest.raises(RuntimeError, match="Certified resume data mismatch"):
        resumed.run(
            {SYMBOL: tampered},
            warmup=resumed_warmup,
        )

    # Rejection must remain fail-closed and must not execute anything new.
    assert resumed._morning_recovery_prepared
    assert resumed._execution_halted
    assert resumed.broker.snapshot() == broker_before

    resumed_store.close()


def test_resume_preserves_durable_execution_halt(tmp_path):
    restart_path = tmp_path / "halt"
    restart_path.mkdir()

    first, _, first_store, _, bars, warmup = \
        build_orchestrator(restart_path)

    # Advance to a genuine certified execution state first.
    crash = run_until_checkpoint_crash(first, bars, warmup)

    # Simulate a real safety halt becoming active, then persist that exact
    # condition through the normal morning-recovery checkpoint path.
    first._execution_halted = True
    first._checkpoint_morning_recovery = \
        first._checkpoint_morning_recovery.__closure__[0].cell_contents \
        if getattr(first._checkpoint_morning_recovery, "__closure__", None) \
        else first._checkpoint_morning_recovery

    # The crash harness replaced the method, so use the durable capture API
    # through a clean engine method binding from the class.
    type(first)._checkpoint_morning_recovery(
        first,
        account_id="fixture-account",
        timestamp="HALTED-CHECKPOINT",
    )

    broker = restored_broker(first.broker.snapshot())
    first_store.close()

    resumed, _, resumed_store, _, resumed_bars, resumed_warmup = \
        build_orchestrator(restart_path)

    receipt = resumed._reconcile_morning_startup(
        account_id="fixture-account",
        broker=broker,
    )

    assert receipt["prepared"]
    assert resumed._morning_recovery_prepared
    assert resumed._execution_halted
    assert resumed._resume_execution_halted is True

    # Certification of the unchanged dataset must preserve the durable halt;
    # it must never turn a genuine source safety halt into permission to trade.
    resumed.run(
        {SYMBOL: resumed_bars},
        warmup=resumed_warmup,
    )

    assert resumed._execution_halted is True

    resumed_store.close()
