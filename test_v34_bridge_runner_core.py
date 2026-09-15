"""Tests for v34_bridge_runner_core.py (Step B orchestration core).

Drives a real TradingEngineV34P02 + FakeBroker (the exact pattern
test_v34_p02_lifecycle_integration.py already uses) through full
CREATED -> ... -> COMPLETE/PARTIAL/HALTED cycles.

One test deliberately uses a REAL basket from R1's golden fixture (not a
contrived example) sized to naturally include a real sector clash.

REGENERATED 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
own fixture (50-symbol universe - see that file's docstring). The real
top-4 by momentum no longer shares a sector (LAURUSLABS/PHARMA,
SHRIRAMFIN/FINANCE, HINDALCO/METALS, ADANIENT/INDUSTRIAL all differ) -
the sector-clash test below was widened to the real top-7, where
BAJFINANCE (rank 7, FINANCE) genuinely clashes with SHRIRAMFIN (rank 2,
FINANCE) - still real, ranked data, not an invented pairing.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from institutional_engine_v34_p02_multipos_candidate import PositionStatus
from test_v34_p02_lifecycle_integration import make_context, make_stack
from test_v34_p02_multipos_engine import FakeBroker, cnc_position, flat_running_state, sample_ctx
from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import LegStatus, RebalancePlanStatus, RebalancePlanStore, create_rebalance_plan
from v34_bridge_rebalance_transitions import approve_plan
from v34_bridge_runner_core import drive_plan_one_cycle
from v34_bridge_target_portfolio import build_target_portfolio
from v34_p02_state import EngineStatus

SIGNAL_DATE = date(2026, 8, 14)


def _approve(plan, *, current_trading_day=SIGNAL_DATE):
    """The now-mandatory human-in-the-loop step, applied directly (not
    through a drive_plan_one_cycle() call) - CREATED itself is a passive
    no-op in the runner (see TestCreatedIsAPassiveNoOp below), so nothing
    about driving cycles can approve a plan; only this can."""
    approve_plan(plan, current_trading_day=current_trading_day, now=datetime.now(timezone.utc))
    return plan


ALL_LIVE_QUOTES = {
    "NSE:RELIANCE": {"last_price": 2500.0},
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
    "NSE:HINDALCO": {"last_price": 974.5},
    "NSE:ADANIENT": {"last_price": 3009.2},
}

# Real top-7 by momentum, with quotes - used only by the sector-clash test
# below, which needs to reach rank 7 (BAJFINANCE) to hit a real clash.
TOP7_LIVE_QUOTES = {
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
    "NSE:HINDALCO": {"last_price": 974.5},
    "NSE:ADANIENT": {"last_price": 3009.2},
    "NSE:TITAN": {"last_price": 4885.5},
    "NSE:BAJAJ-AUTO": {"last_price": 11519.0},
    "NSE:BAJFINANCE": {"last_price": 1141.0},
}


class TestCreatedIsAPassiveNoOp:
    """The runner-side half of the approval gate's proof
    (v34_bridge_rebalance_transitions.py's own adversarial test covers
    begin_exiting() refusing CREATED directly) - here, through the actual
    dispatch a real poll loop uses: a CREATED plan just sits, untouched,
    across as many cycles as the runner cares to drive, until something
    external (the approval CLI) changes its status."""

    def test_driving_a_created_plan_touches_nothing(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=2)
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        assert plan.status == RebalancePlanStatus.CREATED

        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
        context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))
        engine, raw_broker, *_ = make_stack(state=state, raw_broker=raw_broker, cfg=cfg, context=context)
        plan_store = RebalancePlanStore(tmp_path)
        plan_store.save(plan)

        for _ in range(5):
            result = drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
            assert result == "CREATED"
        assert plan.status == RebalancePlanStatus.CREATED
        assert plan.approved_at is None
        assert all(status == LegStatus.PENDING for status in plan.exit_status.values())
        assert len(raw_broker.place_order_calls) == 0
        # engine.state.active_trades["RELIANCE"] is still exactly as it
        # started - request_exit() was never called.
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.MANAGING


class TestHappyPathNoSectorClash:
    def test_full_cycle_reaches_complete(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=2)
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        assert set(diff.enters) == {"LAURUSLABS", "SHRIRAMFIN"}  # no sector clash

        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        # Unique, predictable order IDs per symbol - FakeBroker's default
        # returns the SAME id ("ORD-DEFAULT") for every call, which would
        # make two simultaneously in-flight entries collide on lookup.
        raw_broker.place_order_fn = lambda **kwargs: f"ENTRY-{kwargs['tradingsymbol']}"
        raw_broker.submit_emergency_exit_fn = lambda **kwargs: f"EXIT-{kwargs['symbol']}"

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
        context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=state, raw_broker=raw_broker, cfg=cfg, context=context,
        )
        plan_store = RebalancePlanStore(tmp_path)
        plan_store.save(plan)

        # CREATED is a passive no-op (TestCreatedIsAPassiveNoOp proves
        # this directly) - a human has to approve before anything drives.
        _approve(plan)
        plan_store.save(plan)

        # Cycle 1: APPROVED -> submits RELIANCE exit -> EXITING.
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert plan.status == RebalancePlanStatus.EXITING

        # Cycle 2: engine.step() processes the exit to completion (mocked
        # broker cooperates: emergency exit order completes immediately).
        exit_order_id = engine.state.active_trades["RELIANCE"].exit_order_id if "RELIANCE" in engine.state.active_trades else None
        # The first step() inside cycle 1's EXITING branch may already have
        # advanced RELIANCE toward EXIT_PENDING; drive additional cycles
        # until the exit resolves, mirroring how the real poll loop works.
        for _ in range(5):
            if plan.status != RebalancePlanStatus.EXITING:
                break
            if "RELIANCE" in engine.state.active_trades:
                ctx = engine.state.active_trades["RELIANCE"]
                if ctx.exit_order_id and ctx.exit_order_id not in raw_broker.order_details:
                    raw_broker.order_details[ctx.exit_order_id] = {
                        "order_id": ctx.exit_order_id, "status": "COMPLETE",
                        "filled_quantity": 50, "quantity": 50,
                    }
                    raw_broker.positions = [p for p in raw_broker.positions if p["tradingsymbol"] != "RELIANCE"]
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert plan.status == RebalancePlanStatus.EXITS_COMPLETE
        assert "RELIANCE" not in engine.state.active_trades

        # Cycle: EXITS_COMPLETE -> ENTERING.
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert plan.status == RebalancePlanStatus.ENTERING

        # Drive entries: one symbol submitted per cycle, then resolved via
        # engine.step() once its order is marked COMPLETE in the fake broker.
        for _ in range(20):
            if plan.status != RebalancePlanStatus.ENTERING:
                break
            for symbol in ("LAURUSLABS", "SHRIRAMFIN"):
                ctx = engine.state.active_trades.get(symbol)
                if ctx and ctx.entry_order_id and ctx.entry_order_id not in raw_broker.order_details:
                    qty = ctx.target_qty
                    raw_broker.order_details[ctx.entry_order_id] = {
                        "order_id": ctx.entry_order_id, "status": "COMPLETE",
                        "filled_quantity": qty, "quantity": qty, "average_price": str(ALL_LIVE_QUOTES[f"NSE:{symbol}"]["last_price"]),
                    }
                    raw_broker.positions.append(cnc_position(symbol, qty))
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)

        assert plan.status == RebalancePlanStatus.COMPLETE
        assert plan.enter_status["LAURUSLABS"] == LegStatus.CONFIRMED
        assert plan.enter_status["SHRIRAMFIN"] == LegStatus.CONFIRMED
        assert plan.exit_status["RELIANCE"] == LegStatus.CONFIRMED
        assert terminator.halted is False


class TestSequentialEntryAndRealSectorClashProducesPartial:
    # Real ranked order (2026-08-16 data): LAURUSLABS(1,PHARMA),
    # SHRIRAMFIN(2,FINANCE), HINDALCO(3,METALS), ADANIENT(4,INDUSTRIAL),
    # TITAN(5,CONSUMER), BAJAJ-AUTO(6,AUTO), BAJFINANCE(7,FINANCE) - the
    # real top-4 no longer shares a sector, so this test was widened to
    # the real top-7, where BAJFINANCE (rank 7) genuinely clashes with
    # SHRIRAMFIN (rank 2) on FINANCE - still real, ranked data.
    RANKED_SYMBOLS = ("LAURUSLABS", "SHRIRAMFIN", "HINDALCO", "ADANIENT", "TITAN", "BAJAJ-AUTO", "BAJFINANCE")

    def test_full_real_basket_declines_the_second_finance_name_sequentially(self, tmp_path):
        target = build_target_portfolio(quotes=TOP7_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=7)
        diff = compute_rebalance_diff(current_portfolio={}, target=target)  # nothing held -> all seven are pure ENTERs
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        assert set(diff.enters) == set(self.RANKED_SYMBOLS)
        assert list(plan.enter_status) == list(self.RANKED_SYMBOLS)  # score-descending order

        state = flat_running_state()  # nothing held at all
        raw_broker = FakeBroker()
        raw_broker.quotes = dict(TOP7_LIVE_QUOTES)
        # Unique, predictable order IDs per symbol - FakeBroker's default
        # returns the SAME id ("ORD-DEFAULT") for every call, which would
        # make two simultaneously in-flight entries collide on lookup.
        raw_broker.place_order_fn = lambda **kwargs: f"ENTRY-{kwargs['tradingsymbol']}"
        raw_broker.submit_emergency_exit_fn = lambda **kwargs: f"EXIT-{kwargs['symbol']}"

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"), max_simultaneous_positions=10)
        context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=state, raw_broker=raw_broker, cfg=cfg, context=context,
        )
        plan_store = RebalancePlanStore(tmp_path)
        plan_store.save(plan)
        _approve(plan)
        plan_store.save(plan)

        # No exits at all - APPROVED -> EXITING -> (vacuously) EXITS_COMPLETE -> ENTERING.
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # APPROVED -> EXITING
        assert plan.status == RebalancePlanStatus.EXITING
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # EXITING -> EXITS_COMPLETE (no exits to wait on)
        assert plan.status == RebalancePlanStatus.EXITS_COMPLETE
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # -> ENTERING
        assert plan.status == RebalancePlanStatus.ENTERING

        # Cycle: submit LAURUSLABS (first, highest momentum score) - exactly one leg this cycle.
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert plan.enter_status["LAURUSLABS"] == LegStatus.SUBMITTED
        for symbol in self.RANKED_SYMBOLS[1:]:
            assert plan.enter_status[symbol] == LegStatus.PENDING  # untouched this cycle - sequential proof

        # Resolve each leg to CONFIRMED via the fake broker as its order
        # appears, letting the remaining legs submit/resolve one at a time
        # across further cycles - BAJFINANCE (last, rank 7) is expected to
        # be declined for SECTOR_ALREADY_HELD (SHRIRAMFIN, rank 2, already
        # holds FINANCE), so it's deliberately excluded from this
        # resolution loop - there is no order to resolve for a decline.
        for _ in range(40):
            if plan.status != RebalancePlanStatus.ENTERING:
                break
            for symbol in self.RANKED_SYMBOLS[:-1]:
                ctx = engine.state.active_trades.get(symbol)
                if ctx and ctx.entry_order_id and ctx.entry_order_id not in raw_broker.order_details:
                    qty = ctx.target_qty
                    raw_broker.order_details[ctx.entry_order_id] = {
                        "order_id": ctx.entry_order_id, "status": "COMPLETE",
                        "filled_quantity": qty, "quantity": qty, "average_price": str(TOP7_LIVE_QUOTES[f"NSE:{symbol}"]["last_price"]),
                    }
                    raw_broker.positions.append(cnc_position(symbol, qty))
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)

        assert plan.status == RebalancePlanStatus.PARTIAL
        for symbol in self.RANKED_SYMBOLS[:-1]:
            assert plan.enter_status[symbol] == LegStatus.CONFIRMED
        assert plan.enter_status["BAJFINANCE"] == LegStatus.DECLINED  # FINANCE sector already held by SHRIRAMFIN
        assert terminator.halted is False  # a clean decline is not a halt


class TestHaltOnExitFailure:
    def test_a_definitively_failed_exit_halts_the_whole_plan(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=2)
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)

        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        # Unique, predictable order IDs per symbol - FakeBroker's default
        # returns the SAME id ("ORD-DEFAULT") for every call, which would
        # make two simultaneously in-flight entries collide on lookup.
        raw_broker.place_order_fn = lambda **kwargs: f"ENTRY-{kwargs['tradingsymbol']}"
        raw_broker.submit_emergency_exit_fn = lambda **kwargs: f"EXIT-{kwargs['symbol']}"

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
        context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=state, raw_broker=raw_broker, cfg=cfg, context=context,
        )
        plan_store = RebalancePlanStore(tmp_path)
        plan_store.save(plan)
        _approve(plan)
        plan_store.save(plan)

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # APPROVED -> EXITING (request_exit() called, intent only)
        assert plan.status == RebalancePlanStatus.EXITING
        assert engine.state.active_trades["RELIANCE"].exit_order_id is None  # not yet submitted to the broker

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # EXITING: step() actually submits the emergency exit
        ctx = engine.state.active_trades["RELIANCE"]
        assert ctx.exit_order_id is not None  # now populated - the broker call actually happened

        # The exchange definitively rejects the emergency exit order - the
        # real P02 path for this converts straight into RECONCILIATION_HALT.
        raw_broker.order_details[ctx.exit_order_id] = {
            "order_id": ctx.exit_order_id, "status": "REJECTED", "filled_quantity": 0, "quantity": 50,
        }

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)

        assert plan.status == RebalancePlanStatus.HALTED
        assert terminator.halted is True
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        # No enter leg was ever touched - the plan stopped dead, no resizing,
        # no partial quarantine of the entry side.
        assert all(status == LegStatus.PENDING for status in plan.enter_status.values())

        # Re-running the cycle after HALTED is a pure no-op - never
        # resubmits, never tries to recover automatically.
        result = drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert result == "HALTED"
        assert len(raw_broker.place_order_calls) == 0  # no entry was ever attempted


class TestBoundedRetryOnceSurvivesACrash:
    """R0 §6 + the crash-window this session's design discussion identified:
    a declined enter leg gets exactly one retry, and that retry budget is
    durable - a simulated process crash between "decided to retry" and
    "retry resolved" must not let a resumed runner forget the retry
    already happened and issue a second one.

    Uses SIMULTANEOUS_POSITION_LIMIT (max_simultaneous_positions=1, with
    RELIANCE - outside the plan entirely - already occupying the one slot)
    as a deterministic, permanent synchronous decline: LAURUSLABS can never
    successfully enter no matter how many times it's tried, so any second
    attempt proves whether the retry cap actually held.
    """

    def test_retry_count_survives_reload_and_a_second_decline_terminally_declines(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={}, target=target)
        assert set(diff.enters) == {"LAURUSLABS"}
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
        assert plan.enter_retry_count == {"LAURUSLABS": 0}

        state = flat_running_state()
        # RELIANCE occupies the only simultaneous-position slot and is
        # never exited - the bridge doesn't even know about it (it isn't
        # part of this plan at all), exactly as a manually-held or
        # otherwise-external position would look to the engine.
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        raw_broker.place_order_fn = lambda **kwargs: f"ENTRY-{kwargs['tradingsymbol']}"
        raw_broker.submit_emergency_exit_fn = lambda **kwargs: f"EXIT-{kwargs['symbol']}"

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"), max_simultaneous_positions=1)
        context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=state, raw_broker=raw_broker, cfg=cfg, context=context,
        )
        plan_store = RebalancePlanStore(tmp_path)
        plan_store.save(plan)
        _approve(plan)
        plan_store.save(plan)

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # APPROVED -> EXITING (no exits)
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # EXITING -> EXITS_COMPLETE (vacuous)
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # -> ENTERING
        assert plan.status == RebalancePlanStatus.ENTERING

        # First attempt: request_entry() raises SIMULTANEOUS_POSITION_LIMIT
        # (RELIANCE already occupies the one slot) - the first decline gets
        # a retry, not a terminal DECLINED.
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert plan.status == RebalancePlanStatus.ENTERING
        assert plan.enter_status["LAURUSLABS"] == LegStatus.PENDING  # reset for resubmission, not declined
        assert plan.enter_retry_count["LAURUSLABS"] == 1
        assert len(raw_broker.place_order_calls) == 0  # request_entry() never got far enough to touch the broker

        # --- Simulate a crash: discard the in-memory plan object entirely
        # and load a *fresh* RebalancePlan through a *fresh*
        # RebalancePlanStore pointed at the same directory - the only thing
        # a genuinely new process would have to go on. (The engine itself
        # is left in place: P02's own restart/reconciliation durability is
        # proven elsewhere; this test isolates the retry-count property.) ---
        del plan
        crash_store = RebalancePlanStore(tmp_path)
        resumed_plan = crash_store.load(diff.target_id)
        assert resumed_plan is not None
        assert resumed_plan.enter_retry_count["LAURUSLABS"] == 1  # the crash did not erase it
        assert resumed_plan.enter_status["LAURUSLABS"] == LegStatus.PENDING

        # The resumed runner tries again, exactly as its poll loop would.
        # RELIANCE still occupies the only slot, so this fails identically
        # - but the retry budget is already spent, so this must terminally
        # decline, not silently retry a second time.
        drive_plan_one_cycle(plan=resumed_plan, engine=engine, store=crash_store)
        assert resumed_plan.enter_status["LAURUSLABS"] == LegStatus.DECLINED
        assert resumed_plan.enter_retry_count["LAURUSLABS"] == 1  # never became 2
        assert resumed_plan.status == RebalancePlanStatus.PARTIAL  # a clean bounded decline, not a halt
        assert terminator.halted is False
        assert len(raw_broker.place_order_calls) == 0  # LAURUSLABS never once reached the broker

        # Re-driving a terminal plan is a pure no-op - the retry machinery
        # is never re-armed by re-running a finished plan.
        result = drive_plan_one_cycle(plan=resumed_plan, engine=engine, store=crash_store)
        assert result == "PARTIAL"
        assert resumed_plan.enter_retry_count["LAURUSLABS"] == 1


class TestSynchronousDeclineIsAudited:
    """Found from a real live run, 2026-08-16: a synchronous decline at
    request_entry() call time used to be completely invisible in the
    audit trail - v34_p02_authorizer._decline() (frozen) is a pure
    function with no audit side effect, unlike the asynchronous channel's
    own ENTRY_ABANDONED_POLICY_HALT record. Fixed in _drive_entering()
    itself (not a frozen file) by logging the caught exception's own
    message via engine.audit (a real, already-public attribute of the
    frozen engine - read-only use, no frozen file touched)."""

    def test_a_synchronous_decline_produces_an_entry_sync_declined_audit_record(self, tmp_path):
        target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=1)
        diff = compute_rebalance_diff(current_portfolio={}, target=target)
        assert set(diff.enters) == {"LAURUSLABS"}
        plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)

        state = flat_running_state()
        # Same deterministic synchronous-decline setup as the class above:
        # RELIANCE occupies the only simultaneous-position slot.
        state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 50)]
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"), max_simultaneous_positions=1)
        context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))

        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=state, raw_broker=raw_broker, cfg=cfg, context=context,
        )
        plan_store = RebalancePlanStore(tmp_path)
        plan_store.save(plan)
        _approve(plan)
        plan_store.save(plan)

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # APPROVED -> EXITING (no exits)
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # EXITING -> EXITS_COMPLETE (vacuous)
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # -> ENTERING

        assert not audit.has("ENTRY_SYNC_DECLINED")  # not yet - no decline has happened

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # the synchronous decline itself

        matching = [kwargs for event, kwargs in audit.events if event == "ENTRY_SYNC_DECLINED"]
        assert len(matching) == 1
        record = matching[0]
        assert record["symbol"] == "LAURUSLABS"
        assert "SIMULTANEOUS_POSITION_LIMIT" in record["reason"]
        assert record["already_retried"] is False  # this was the first decline

        # Drive to the terminal second decline - a second ENTRY_SYNC_DECLINED
        # record, this time flagged as already having used its retry.
        for _ in range(3):
            if plan.status == RebalancePlanStatus.PARTIAL:
                break
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert plan.status == RebalancePlanStatus.PARTIAL

        matching = [kwargs for event, kwargs in audit.events if event == "ENTRY_SYNC_DECLINED"]
        assert len(matching) == 2
        assert matching[1]["already_retried"] is True
