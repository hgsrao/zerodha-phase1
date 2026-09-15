"""Tests for v34_bridge_trigger.py.

Pure orchestration - real R1/Step A/RebalancePlan machinery, no broker,
no network, no filesystem beyond a real tmp_path-backed RebalancePlanStore
(the same durable-file IPC mechanism the runner reads from).
"""

from datetime import date, datetime, timezone

import pytest

from v34_bridge_rebalance_plan import (
    LegStatus,
    RebalancePlanStatus,
    RebalancePlanStore,
    StaleTargetError,
)
from v34_bridge_trigger import run_trigger

SIGNAL_DATE = date(2026, 8, 14)
# Regenerated 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
# own fixture - see that file's docstring for why (50-symbol universe).
# Real top-4 by momentum, in rank order: LAURUSLABS, SHRIRAMFIN,
# HINDALCO, ADANIENT - the top-2 (positions=2) is the first two of these.
YESTERDAYS_REAL_QUOTES = {
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
    "NSE:HINDALCO": {"last_price": 974.5},
    "NSE:ADANIENT": {"last_price": 3009.2},
}


class TestRunTriggerHappyPath:
    def test_no_existing_plan_creates_one_from_real_data(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        plan, outcome = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
        )
        assert outcome == "CREATED"
        assert plan.status == RebalancePlanStatus.CREATED
        assert "RELIANCE" in plan.exit_status  # not in the target basket -> pure EXIT
        assert set(plan.enter_status) == {"LAURUSLABS", "SHRIRAMFIN"}  # top-2 by momentum

        # A real file, in the exact directory the runner daemon polls.
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

    def test_zero_current_holdings_is_a_pure_all_enter_plan(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        plan, outcome = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=4,
        )
        assert outcome == "CREATED"
        assert plan.exit_status == {}
        assert set(plan.enter_status) == {"LAURUSLABS", "SHRIRAMFIN", "HINDALCO", "ADANIENT"}


class TestRunTriggerIdempotency:
    """Mirrors resolve_or_create_plan()'s own outcome contract (R0 §10) -
    run_trigger() doesn't reimplement this, it just needs to actually
    surface it correctly through its own return value."""

    def test_calling_twice_with_the_same_inputs_resumes_not_recreates(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        first, first_outcome = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
        )
        assert first_outcome == "CREATED"

        second, second_outcome = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
        )
        assert second_outcome == "RESUMED"
        assert second.target_id == first.target_id
        assert len(list(tmp_path.glob("*.json"))) == 1  # never a second file for the same target

    def test_a_complete_existing_plan_is_a_noop(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        plan, _ = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
        )
        for symbol in plan.exit_status:
            plan.exit_status[symbol] = LegStatus.CONFIRMED
        for symbol in plan.enter_status:
            plan.enter_status[symbol] = LegStatus.CONFIRMED
        plan.status = RebalancePlanStatus.COMPLETE
        plan.approved_at = datetime.now(timezone.utc)
        store.save(plan)

        again, outcome = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
        )
        assert outcome == "NOOP_ALREADY_TERMINAL"
        assert again.status == RebalancePlanStatus.COMPLETE

    def test_a_halted_existing_plan_is_refused_not_auto_resumed(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        plan, _ = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
        )
        plan.status = RebalancePlanStatus.HALTED
        store.save(plan)

        again, outcome = run_trigger(
            quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
            signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
        )
        assert outcome == "REFUSED_HALTED"
        assert again.status == RebalancePlanStatus.HALTED


class TestRunTriggerRefusesStaleSignal:
    def test_a_next_day_trigger_for_an_old_signal_with_no_existing_plan_raises(self, tmp_path):
        # R0 §7: the bridge refuses to *create* a plan from a stale
        # target - hard error, not a warning. run_trigger() doesn't
        # catch this itself; it must propagate unchanged so a cron-fired
        # trigger run fails loudly rather than silently acting on a
        # signal that is no longer valid for today's session.
        store = RebalancePlanStore(tmp_path)
        with pytest.raises(StaleTargetError, match="Refusing to create"):
            run_trigger(
                quotes=YESTERDAYS_REAL_QUOTES, current_portfolio={"RELIANCE": 50},
                signal_date=SIGNAL_DATE, current_trading_day=date(2026, 8, 15),
                store=store, positions=2,
            )
