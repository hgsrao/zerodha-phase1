"""Tests for v34_bridge_runner_entrypoint.py (the runner's polling daemon).

Uses the real kiteconnect.exceptions.NetworkException (the package is
actually installed in this environment - test_harness_v34_b1_
classification.py already relies on this) rather than a hand-rolled fake,
so the 429-detection tests prove something about the real exception
shape, not about a fake that might have drifted from it.

No real time.sleep() anywhere - run_forever()'s sleep_fn is always a
list-recording stub, and max_iterations always bounds the loop, so these
tests run in milliseconds and terminate deterministically.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import kiteconnect.exceptions as ke
import pytest

from institutional_engine_v34_p02_multipos_candidate import PositionStatus
from test_v34_p02_lifecycle_integration import make_context, make_stack
from test_v34_p02_multipos_engine import FakeBroker, cnc_position, flat_running_state, sample_ctx
from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import (
    LegStatus,
    RebalancePlanStatus,
    RebalancePlanStore,
    create_rebalance_plan,
)
from v34_bridge_rebalance_transitions import approve_plan
from v34_bridge_runner_entrypoint import (
    MultipleActivePlansError,
    POLL_INTERVAL_SECONDS,
    RATE_LIMIT_BACKOFF_SECONDS,
    _is_rate_limit_exception,
    find_active_plan,
    run_forever,
    run_one_iteration,
)
from v34_bridge_target_portfolio import build_target_portfolio

SIGNAL_DATE = date(2026, 8, 14)
# Regenerated 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
# own fixture - see that file's docstring for why (50-symbol universe).
ALL_LIVE_QUOTES = {
    "NSE:RELIANCE": {"last_price": 2500.0},
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
}


def _approve(plan, *, current_trading_day=SIGNAL_DATE):
    approve_plan(plan, current_trading_day=current_trading_day, now=datetime.now(timezone.utc))
    return plan


def _build_real_stack(*, state, raw_broker, max_simultaneous_positions=6):
    from portfolio_brain_v9 import SECTORS as REAL_SECTORS
    from v34_p02_accounting import initial_checkpoint
    from v34_p02_state import Config
    raw_broker.place_order_fn = lambda **kwargs: f"ENTRY-{kwargs['tradingsymbol']}"
    raw_broker.submit_emergency_exit_fn = lambda **kwargs: f"EXIT-{kwargs['symbol']}"
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"), max_simultaneous_positions=max_simultaneous_positions)
    context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))
    return make_stack(state=state, raw_broker=raw_broker, cfg=cfg, context=context)


class TestFindActivePlan:
    def test_no_plans_returns_none(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        assert find_active_plan(store) is None

    def test_one_non_terminal_plan_is_found(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        store = RebalancePlanStore(tmp_path)
        store.save(plan)

        found = find_active_plan(store)
        assert found is not None
        assert found.target_id == plan.target_id

    def test_a_terminal_plan_is_ignored(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        plan.status = RebalancePlanStatus.COMPLETE
        plan.approved_at = datetime.now(timezone.utc)
        for symbol in plan.enter_status:
            plan.enter_status[symbol] = LegStatus.CONFIRMED
        store = RebalancePlanStore(tmp_path)
        store.save(plan)

        assert find_active_plan(store) is None

    def test_a_halted_plan_is_ignored_same_as_terminal(self, tmp_path):
        # Deliberately distinct from resolve_or_create_plan()'s own
        # "REFUSED_HALTED" treatment (R0 §10) - that is a bridge-side,
        # create-a-new-plan decision. For the runner's own "does this need
        # driving" question, HALTED needs no more automatic cycles, same
        # as COMPLETE/PARTIAL (drive_plan_one_cycle() already no-ops on it).
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        plan.status = RebalancePlanStatus.HALTED
        store = RebalancePlanStore(tmp_path)
        store.save(plan)

        assert find_active_plan(store) is None

    def test_two_non_terminal_plans_raises_rather_than_pick_one(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        target_a = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff_a = compute_rebalance_diff(current_portfolio={}, target=target_a)
        plan_a = create_rebalance_plan(diff_a, target=target_a, current_trading_day=SIGNAL_DATE)
        store.save(plan_a)

        target_b = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=date(2026, 8, 13), positions=1)
        diff_b = compute_rebalance_diff(current_portfolio={}, target=target_b)
        plan_b = create_rebalance_plan(diff_b, target=target_b, current_trading_day=date(2026, 8, 13))
        store.save(plan_b)

        with pytest.raises(MultipleActivePlansError, match="Found 2 non-terminal plans"):
            find_active_plan(store)


class TestIsRateLimitException:
    def test_a_real_429_network_exception_is_detected(self):
        assert _is_rate_limit_exception(ke.NetworkException("Too many requests", code=429)) is True

    def test_a_503_network_exception_is_not_a_rate_limit(self):
        # 502/503/504 are P02's own "transient" bucket
        # (_is_transient_observation_exception) - a different concern,
        # already handled at P02's own layer, not this daemon's.
        assert _is_rate_limit_exception(ke.NetworkException("Gateway unavailable", code=503)) is False

    def test_an_unrelated_exception_is_not_a_rate_limit(self):
        assert _is_rate_limit_exception(RuntimeError("boom")) is False

    def test_a_token_exception_is_not_a_rate_limit(self):
        assert _is_rate_limit_exception(ke.TokenException("Token expired", code=403)) is False


class TestRunOneIterationMutualExclusivity:
    def test_no_active_plan_calls_background_step_directly(self, tmp_path):
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)

        step_calls = []
        original_step = engine.step
        engine.step = lambda: (step_calls.append(1), original_step())[1]

        run_one_iteration(engine=engine, store=store)
        assert len(step_calls) == 1

    def test_a_created_plan_is_found_active_but_driving_it_is_a_no_op(self, tmp_path):
        # CREATED is the human-in-the-loop gate's waiting state - it's
        # still "active" (find_active_plan() must not silently skip it in
        # favor of background step()), but drive_plan_one_cycle() does
        # nothing to it until a human approves it externally. Proving
        # step_calls == 0 here shows run_one_iteration chose the "drive
        # the plan" branch (which happens to do nothing yet), not
        # "background step AND maybe drive the plan too".
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        assert plan.status == RebalancePlanStatus.CREATED

        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)
        store.save(plan)

        step_calls = []
        original_step = engine.step
        engine.step = lambda: (step_calls.append(1), original_step())[1]

        run_one_iteration(engine=engine, store=store)
        reloaded = store.load(plan.target_id)
        assert reloaded.status == RebalancePlanStatus.CREATED  # untouched - awaiting approval
        assert len(step_calls) == 0  # ...and background step() was not invoked either

    def test_an_approved_plan_is_driven_instead_of_background_stepping(self, tmp_path):
        # APPROVED -> EXITING only calls request_exit() (intent creation) -
        # it never calls engine.step() at all. Proving step_calls == 0
        # here shows run_one_iteration chose the "drive the plan" branch,
        # not "background step AND maybe drive the plan too".
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        _approve(plan)
        assert plan.status == RebalancePlanStatus.APPROVED

        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)
        store.save(plan)

        step_calls = []
        original_step = engine.step
        engine.step = lambda: (step_calls.append(1), original_step())[1]

        run_one_iteration(engine=engine, store=store)
        reloaded = store.load(plan.target_id)
        assert reloaded.status == RebalancePlanStatus.EXITING  # the plan WAS driven
        assert len(step_calls) == 0  # ...and background step() was not separately invoked

    def test_an_active_plan_causes_exactly_one_step_not_two(self, tmp_path):
        # A plan already in EXITING (not CREATED) drives through
        # drive_plan_one_cycle()'s own internal engine.step() call - prove
        # that happens exactly once, not once internally plus once more
        # from run_one_iteration's own "background" branch.
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        _approve(plan)
        plan.status = RebalancePlanStatus.EXITING
        plan.exit_status["RELIANCE"] = LegStatus.SUBMITTED

        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)
        store.save(plan)

        step_calls = []
        original_step = engine.step
        engine.step = lambda: (step_calls.append(1), original_step())[1]

        run_one_iteration(engine=engine, store=store)
        assert len(step_calls) == 1


class TestRunForever:
    def test_drives_a_full_plan_to_completion_across_ticks_with_fake_sleep(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=2)
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        _approve(plan)  # the human-in-the-loop gate - nothing drives before this

        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)
        store.save(plan)

        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            # Cooperate with in-flight orders exactly like
            # test_v34_bridge_runner_core.py's happy-path test does:
            # append-only position bookkeeping, and - found by running
            # this exact test once without the restriction below -
            # strictly scoped to this plan's own legs. RELIANCE (an EXIT
            # leg here) carries a stale historical entry_order_id
            # ("OID-1", from sample_ctx's defaults, long predating this
            # plan) that has nothing to do with this rebalance; treating
            # every active_trade's entry_order_id as "a pending entry to
            # resolve" mistook that leftover for a fresh fill and appended
            # a bogus duplicate RELIANCE position, which then made
            # reconciliation see two matching positions and halt - a test
            # bug, not a production one.
            for symbol in plan.diff.exits:
                ctx = engine.state.active_trades.get(symbol)
                if ctx and ctx.exit_order_id and ctx.exit_order_id not in raw_broker.order_details:
                    raw_broker.order_details[ctx.exit_order_id] = {
                        "order_id": ctx.exit_order_id, "status": "COMPLETE",
                        "filled_quantity": ctx.filled_qty, "quantity": ctx.filled_qty,
                    }
                    raw_broker.positions = [p for p in raw_broker.positions if p["tradingsymbol"] != symbol]
            for symbol in plan.diff.enters:
                ctx = engine.state.active_trades.get(symbol)
                if ctx and ctx.entry_order_id and ctx.entry_order_id not in raw_broker.order_details:
                    qty = ctx.target_qty
                    raw_broker.order_details[ctx.entry_order_id] = {
                        "order_id": ctx.entry_order_id, "status": "COMPLETE",
                        "filled_quantity": qty, "quantity": qty,
                        "average_price": str(ALL_LIVE_QUOTES[f"NSE:{symbol}"]["last_price"]),
                    }
                    raw_broker.positions.append(cnc_position(symbol, qty))

        run_forever(engine=engine, store=store, sleep_fn=fake_sleep, max_iterations=40)

        final = store.load(plan.target_id)
        assert final.status in (RebalancePlanStatus.COMPLETE, RebalancePlanStatus.PARTIAL)
        assert all(s == POLL_INTERVAL_SECONDS for s in sleeps)  # never the rate-limit backoff
        assert len(sleeps) == 40

    def test_a_429_is_caught_backed_off_and_the_daemon_survives(self, tmp_path):
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)  # empty - every tick is a background step()

        calls = {"n": 0}
        original_step = engine.step

        def flaky_step():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ke.NetworkException("Too many requests", code=429)
            return original_step()

        engine.step = flaky_step
        sleeps = []
        events = []

        run_forever(engine=engine, store=store, sleep_fn=sleeps.append, on_event=events.append, max_iterations=3)

        assert sleeps[0] == RATE_LIMIT_BACKOFF_SECONDS  # the 429 tick backed off, not the normal cadence
        assert sleeps[1] == POLL_INTERVAL_SECONDS
        assert sleeps[2] == POLL_INTERVAL_SECONDS
        assert any("RATE_LIMITED" in e for e in events)
        assert calls["n"] == 3  # the failed attempt still counted as a tick, then two more succeeded

    def test_a_non_rate_limit_exception_is_not_swallowed(self, tmp_path):
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)

        def boom():
            raise RuntimeError("something genuinely unexpected")
        engine.step = boom

        with pytest.raises(RuntimeError, match="something genuinely unexpected"):
            run_forever(engine=engine, store=store, sleep_fn=lambda s: None, max_iterations=5)

    def test_a_503_network_exception_is_also_not_swallowed_by_this_layer(self, tmp_path):
        # 502/503/504 are P02's own transient bucket - if one ever escapes
        # engine.step() uncaught (its own retry budget exhausted), this
        # daemon must not mistake it for a rate limit and mask a real,
        # already-escalated engine failure behind a routine-looking backoff.
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)

        def boom():
            raise ke.NetworkException("Gateway unavailable", code=503)
        engine.step = boom

        with pytest.raises(ke.NetworkException):
            run_forever(engine=engine, store=store, sleep_fn=lambda s: None, max_iterations=5)


class TestRunForeverStopsWhenHalted:
    """Phase 3.6 fix: run_forever() must not keep polling a halted engine
    forever - see module docstring's own Phase 3.6 note."""

    def test_a_halt_already_present_before_the_first_iteration_stops_immediately(self, tmp_path):
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        engine.terminator.halt("pre-existing halt from a prior process")
        store = RebalancePlanStore(tmp_path)

        calls = {"n": 0}
        def counting_step():
            calls["n"] += 1
            return {"__engine__": "HALTED"}
        engine.step = counting_step

        with pytest.raises(RuntimeError, match="pre-existing halt"):
            run_forever(engine=engine, store=store, sleep_fn=lambda s: None, max_iterations=5)
        assert calls["n"] == 0  # never even reached the first iteration

    def test_a_halt_triggered_mid_tick_stops_the_loop_after_that_tick_not_before(self, tmp_path):
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)

        calls = {"n": 0}
        def halting_step():
            calls["n"] += 1
            engine.terminator.halt(f"halted on tick {calls['n']}")
            return {"__engine__": "HALTED"}
        engine.step = halting_step

        sleeps = []
        with pytest.raises(RuntimeError, match="halted on tick 1"):
            run_forever(engine=engine, store=store, sleep_fn=sleeps.append, max_iterations=5)
        assert calls["n"] == 1  # exactly one tick ran - the loop stopped, not spun forever
        assert sleeps == []  # never slept after the halt either - stopped immediately, not after one more cycle

    def test_a_halt_logs_via_on_event_before_raising(self, tmp_path):
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        engine, raw_broker, *_ = _build_real_stack(state=state, raw_broker=raw_broker)
        store = RebalancePlanStore(tmp_path)

        def halting_step():
            engine.terminator.halt("operator-visible reason")
            return {"__engine__": "HALTED"}
        engine.step = halting_step

        events = []
        with pytest.raises(RuntimeError):
            run_forever(engine=engine, store=store, sleep_fn=lambda s: None, on_event=events.append, max_iterations=5)
        assert any("HALTED" in e and "operator-visible reason" in e for e in events)
