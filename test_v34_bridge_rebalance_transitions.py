"""Tests for v34_bridge_rebalance_transitions.py.

The adversarial tests are the point: they try to shortcut exit-before-
entry sequencing directly through the API and confirm each attempt raises
IllegalTransitionError with the plan's status left untouched - proving
the invariant is structurally unbreakable through this module, not just
true in the happy path.
"""

from datetime import date, datetime, timezone

import pytest

from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import (
    LegStatus,
    RebalancePlanStatus,
    RebalancePlanStore,
    StaleTargetError,
    create_rebalance_plan,
)
from v34_bridge_rebalance_transitions import (
    MAX_ENTER_RETRIES,
    IllegalTransitionError,
    advance_to_exits_complete,
    all_enter_legs_terminal,
    all_exit_legs_terminal,
    approve_plan,
    begin_entering,
    begin_exiting,
    finalize_plan,
    halt_plan,
    mark_enter_leg,
    mark_exit_leg,
    retry_enter_leg,
)
from v34_bridge_target_portfolio import build_target_portfolio

# Regenerated 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
# own fixture - see that file's docstring for why (50-symbol universe).
YESTERDAYS_REAL_QUOTES = {
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
    "NSE:HINDALCO": {"last_price": 974.5},
    "NSE:ADANIENT": {"last_price": 3009.2},
}
SIGNAL_DATE = date(2026, 8, 14)


@pytest.fixture(scope="module")
def real_target():
    return build_target_portfolio(quotes=YESTERDAYS_REAL_QUOTES, signal_date=SIGNAL_DATE)


def _mixed_plan(real_target):
    # RELIANCE: pure EXIT. LAURUSLABS: RESIZE (exit+enter). HINDALCO: KEEP.
    # SHRIRAMFIN, ADANIENT: pure ENTER.
    current = {"RELIANCE": 50, "LAURUSLABS": 10, "HINDALCO": 25}
    diff = compute_rebalance_diff(current_portfolio=current, target=real_target)
    return create_rebalance_plan(diff, target=real_target, current_trading_day=SIGNAL_DATE)


def _approve(plan, *, current_trading_day=SIGNAL_DATE):
    approve_plan(plan, current_trading_day=current_trading_day, now=datetime.now(timezone.utc))
    return plan


def _approved_mixed_plan(real_target):
    """The common setup for every test whose actual subject is the
    exit/entry lifecycle mechanics, not the CREATED->APPROVED boundary
    itself - approval is the now-mandatory first step, not the thing
    under test in those cases."""
    return _approve(_mixed_plan(real_target))


class TestHappyPathFullLifecycleComplete:
    def test_every_leg_confirmed_reaches_complete(self, real_target):
        plan = _approved_mixed_plan(real_target)
        assert plan.status == RebalancePlanStatus.APPROVED

        begin_exiting(plan)
        assert plan.status == RebalancePlanStatus.EXITING
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)

        advance_to_exits_complete(plan)
        assert plan.status == RebalancePlanStatus.EXITS_COMPLETE

        begin_entering(plan)
        assert plan.status == RebalancePlanStatus.ENTERING
        for symbol in plan.enter_status:
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)

        finalize_plan(plan)
        assert plan.status == RebalancePlanStatus.COMPLETE


class TestHappyPathPartial:
    def test_one_declined_enter_leg_reaches_partial_not_complete(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)
        begin_entering(plan)

        symbols = list(plan.enter_status)
        mark_enter_leg(plan, symbols[0], LegStatus.DECLINED)
        for symbol in symbols[1:]:
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)

        finalize_plan(plan)
        assert plan.status == RebalancePlanStatus.PARTIAL

    def test_a_failed_exit_leg_still_allows_progress_to_exits_complete(self, real_target):
        # FAILED is terminal too (R0 §5's "CONFIRMED ... or FAILED") -
        # an exit that definitively failed doesn't block sequencing, it's
        # a resolved outcome like a decline is for enters.
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        symbols = list(plan.exit_status)
        mark_exit_leg(plan, symbols[0], LegStatus.FAILED)
        for symbol in symbols[1:]:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)  # must not raise
        assert plan.status == RebalancePlanStatus.EXITS_COMPLETE


class TestExitBeforeEntrySequencingCannotBeShortcut:
    def test_advancing_to_exits_complete_with_a_pending_exit_leg_raises(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        symbols = list(plan.exit_status)
        mark_exit_leg(plan, symbols[0], LegStatus.CONFIRMED)
        # symbols[1] (if it exists) is left PENDING - deliberately not resolved.
        assert len(symbols) >= 2
        with pytest.raises(IllegalTransitionError, match="not yet.*terminal"):
            advance_to_exits_complete(plan)
        assert plan.status == RebalancePlanStatus.EXITING  # unchanged - the attempt did not partially succeed

    def test_advancing_to_exits_complete_with_a_submitted_exit_leg_raises(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.SUBMITTED)  # in flight, not yet terminal
        with pytest.raises(IllegalTransitionError, match="not yet.*terminal"):
            advance_to_exits_complete(plan)
        assert plan.status == RebalancePlanStatus.EXITING

    def test_begin_entering_cannot_skip_exits_complete_directly_from_exiting(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        # Deliberately skip advance_to_exits_complete() and try to jump straight to entering.
        with pytest.raises(IllegalTransitionError, match="expected EXITS_COMPLETE"):
            begin_entering(plan)
        assert plan.status == RebalancePlanStatus.EXITING

    def test_begin_entering_from_created_raises(self, real_target):
        plan = _mixed_plan(real_target)
        with pytest.raises(IllegalTransitionError, match="expected EXITS_COMPLETE"):
            begin_entering(plan)
        assert plan.status == RebalancePlanStatus.CREATED

    def test_mark_enter_leg_is_refused_while_plan_is_still_created(self, real_target):
        plan = _mixed_plan(real_target)
        symbol = next(iter(plan.enter_status))
        with pytest.raises(IllegalTransitionError, match="expected ENTERING"):
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)
        assert plan.enter_status[symbol] == LegStatus.PENDING  # untouched

    def test_mark_enter_leg_is_refused_while_plan_is_exiting(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        symbol = next(iter(plan.enter_status))
        with pytest.raises(IllegalTransitionError, match="expected ENTERING"):
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)
        assert plan.enter_status[symbol] == LegStatus.PENDING

    def test_mark_enter_leg_is_refused_while_plan_is_exits_complete_but_not_yet_entering(self, real_target):
        # The exact razor's-edge case: all exits are done, but
        # begin_entering() hasn't been called yet - an ENTER leg still
        # must not be mutable in this window.
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)
        assert plan.status == RebalancePlanStatus.EXITS_COMPLETE

        symbol = next(iter(plan.enter_status))
        with pytest.raises(IllegalTransitionError, match="expected ENTERING"):
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)
        assert plan.enter_status[symbol] == LegStatus.PENDING

    def test_mark_exit_leg_declined_is_always_refused(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        symbol = next(iter(plan.exit_status))
        with pytest.raises(IllegalTransitionError, match="never gated"):
            mark_exit_leg(plan, symbol, LegStatus.DECLINED)
        assert plan.exit_status[symbol] == LegStatus.PENDING

    def test_mark_exit_leg_after_exiting_phase_has_closed_raises(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)
        symbol = next(iter(plan.exit_status))
        with pytest.raises(IllegalTransitionError, match="expected EXITING"):
            mark_exit_leg(plan, symbol, LegStatus.FAILED)

    def test_unknown_symbol_is_rejected_for_both_leg_types(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        with pytest.raises(IllegalTransitionError, match="not an exit leg"):
            mark_exit_leg(plan, "NOTINPLAN", LegStatus.CONFIRMED)

    def test_begin_exiting_from_created_without_approval_raises(self, real_target):
        # The actual proof the approval gate exists: begin_exiting()
        # requires APPROVED now, not CREATED - a plan the trigger just
        # wrote, with no human action on it yet, cannot reach EXITING
        # through this API no matter what.
        plan = _mixed_plan(real_target)
        assert plan.status == RebalancePlanStatus.CREATED
        with pytest.raises(IllegalTransitionError, match="expected APPROVED"):
            begin_exiting(plan)
        assert plan.status == RebalancePlanStatus.CREATED


class TestApprovePlan:
    """The human-in-the-loop gate itself. begin_exiting()'s own adversarial
    proof (test_begin_exiting_from_created_without_approval_raises, above)
    is the other half of this - together they show approval is both
    reachable in the happy path and impossible to bypass."""

    def test_happy_path_sets_status_and_approved_at_together(self, real_target):
        plan = _mixed_plan(real_target)
        assert plan.status == RebalancePlanStatus.CREATED
        assert plan.approved_at is None

        now = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
        approve_plan(plan, current_trading_day=SIGNAL_DATE, now=now)
        assert plan.status == RebalancePlanStatus.APPROVED
        assert plan.approved_at == now

    def test_approving_a_plan_that_is_not_created_raises(self, real_target):
        plan = _approved_mixed_plan(real_target)  # already APPROVED
        with pytest.raises(IllegalTransitionError, match="expected CREATED"):
            approve_plan(plan, current_trading_day=SIGNAL_DATE, now=datetime.now(timezone.utc))

    def test_approving_a_stale_plan_raises_and_does_not_mutate(self, real_target):
        # R0 §7 re-check at the moment of approval, not just at creation -
        # a plan can sit unapproved across a trading-day rollover.
        plan = _mixed_plan(real_target)
        next_day = date(2026, 8, 15)
        with pytest.raises(StaleTargetError, match="Refusing to approve"):
            approve_plan(plan, current_trading_day=next_day, now=datetime.now(timezone.utc))
        assert plan.status == RebalancePlanStatus.CREATED  # unchanged
        assert plan.approved_at is None  # unchanged - the refused attempt left no trace


class TestPureEnterOnlyRebalanceVacuousExitGuard:
    def test_zero_exit_legs_still_passes_through_exits_complete_correctly(self, real_target):
        diff = compute_rebalance_diff(current_portfolio={}, target=real_target)  # nothing held -> all ENTER
        plan = create_rebalance_plan(diff, target=real_target, current_trading_day=SIGNAL_DATE)
        assert plan.exit_status == {}
        _approve(plan)

        begin_exiting(plan)
        assert all_exit_legs_terminal(plan) is True  # vacuously true, nothing to resolve
        advance_to_exits_complete(plan)  # must not raise
        begin_entering(plan)
        assert plan.status == RebalancePlanStatus.ENTERING


class TestRetryEnterLegStructuralCap:
    """R0 §6: retry exactly once, then terminally decline. The cap is
    enforced by retry_enter_leg() itself refusing a second call for the
    same symbol - not by any counter the caller has to remember to check."""

    def test_first_retry_resets_to_pending_and_increments_count(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)
        begin_entering(plan)
        symbol = next(iter(plan.enter_status))
        mark_enter_leg(plan, symbol, LegStatus.SUBMITTED)

        retry_enter_leg(plan, symbol)
        assert plan.enter_status[symbol] == LegStatus.PENDING
        assert plan.enter_retry_count[symbol] == 1

    def test_second_retry_for_the_same_symbol_is_structurally_refused(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)
        begin_entering(plan)
        symbol = next(iter(plan.enter_status))
        mark_enter_leg(plan, symbol, LegStatus.SUBMITTED)
        retry_enter_leg(plan, symbol)  # first retry: allowed
        mark_enter_leg(plan, symbol, LegStatus.SUBMITTED)  # resubmitted, declined again

        with pytest.raises(IllegalTransitionError, match="already used its bounded retry"):
            retry_enter_leg(plan, symbol)
        # The refused attempt must not have partially mutated anything.
        assert plan.enter_retry_count[symbol] == MAX_ENTER_RETRIES
        assert plan.enter_status[symbol] == LegStatus.SUBMITTED

    def test_retry_enter_leg_requires_entering_status(self, real_target):
        plan = _mixed_plan(real_target)
        symbol = next(iter(plan.enter_status))
        with pytest.raises(IllegalTransitionError, match="expected ENTERING"):
            retry_enter_leg(plan, symbol)
        assert plan.enter_retry_count[symbol] == 0

    def test_retry_enter_leg_rejects_unknown_symbol(self, real_target):
        plan = _approved_mixed_plan(real_target)
        begin_exiting(plan)
        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)
        begin_entering(plan)
        with pytest.raises(IllegalTransitionError, match="not an enter leg"):
            retry_enter_leg(plan, "NOTINPLAN")


class TestHaltHasNoPrecondition:
    # 0=CREATED (never approved), 1=APPROVED, 2=EXITING, 3=EXITS_COMPLETE,
    # 4=ENTERING - halt_plan() must succeed from every one of them,
    # including before any human has approved anything.
    @pytest.mark.parametrize("setup_steps", [0, 1, 2, 3, 4])
    def test_halt_plan_succeeds_from_any_reachable_status(self, real_target, setup_steps):
        plan = _mixed_plan(real_target)
        if setup_steps >= 1:
            _approve(plan)
        if setup_steps >= 2:
            begin_exiting(plan)
        if setup_steps >= 3:
            for symbol in plan.exit_status:
                mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
            advance_to_exits_complete(plan)
        if setup_steps >= 4:
            begin_entering(plan)
        halt_plan(plan, reason="integrity ambiguity for test")
        assert plan.status == RebalancePlanStatus.HALTED


class TestIntegrationWithStore:
    def test_full_lifecycle_persists_correctly_at_every_phase(self, tmp_path, real_target):
        store = RebalancePlanStore(tmp_path)
        plan = _mixed_plan(real_target)
        store.save(plan)

        _approve(plan)
        store.save(plan)
        reloaded_after_approval = store.load(plan.target_id)
        assert reloaded_after_approval.status == RebalancePlanStatus.APPROVED
        assert reloaded_after_approval.approved_at is not None

        begin_exiting(plan)
        store.save(plan)
        reloaded = store.load(plan.target_id)
        assert reloaded.status == RebalancePlanStatus.EXITING

        for symbol in plan.exit_status:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
        advance_to_exits_complete(plan)
        begin_entering(plan)
        for symbol in plan.enter_status:
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)
        finalize_plan(plan)
        store.save(plan)

        final = store.load(plan.target_id)
        assert final.status == RebalancePlanStatus.COMPLETE
        assert all_enter_legs_terminal(final)
        assert all_exit_legs_terminal(final)
