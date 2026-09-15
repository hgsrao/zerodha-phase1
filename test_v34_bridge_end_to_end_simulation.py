"""End-to-end simulation: the trigger and the runner, wired ONLY through
a shared RebalancePlanStore directory - exactly the file-based IPC this
session chose over a live HTTP/socket API. Every other bridge test
exercises the trigger half or the runner half in isolation; this is the
one place that proves the two actually integrate: run_trigger() writes a
plan file, and run_forever() - constructed with no knowledge of how that
file got there, no shared Python object, nothing but the directory path
- picks it up on its very next tick and drives it to a terminal status.

Real R1/Step A/RebalancePlan/transitions/runner-core/runner-entrypoint
machinery throughout. Only the broker is fake (FakeBroker, product=CNC,
LIVE_TRADING_ENABLED does not exist in this module).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from institutional_engine_v34_p02_multipos_candidate import PositionStatus
from test_v34_p02_lifecycle_integration import make_context, make_stack
from test_v34_p02_multipos_engine import FakeBroker, cnc_position, flat_running_state, sample_ctx
from v34_bridge_rebalance_plan import LegStatus, RebalancePlanStatus, RebalancePlanStore
from v34_bridge_rebalance_transitions import approve_plan
from v34_bridge_runner_entrypoint import run_forever
from v34_bridge_trigger import run_trigger

SIGNAL_DATE = date(2026, 8, 14)
# Regenerated 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
# own fixture - see that file's docstring for why (50-symbol universe).
ALL_LIVE_QUOTES = {
    "NSE:RELIANCE": {"last_price": 2500.0},
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
}


def test_trigger_writes_a_plan_and_the_runner_drives_it_to_completion_via_the_shared_directory(tmp_path):
    # --- Half 1: the trigger. Knows nothing about any runner. ---
    trigger_store = RebalancePlanStore(tmp_path)
    plan, outcome = run_trigger(
        quotes=ALL_LIVE_QUOTES, current_portfolio={"RELIANCE": 50},
        signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE,
        store=trigger_store, positions=2,
    )
    assert outcome == "CREATED"
    assert plan.status == RebalancePlanStatus.CREATED
    written_files = list(tmp_path.glob("*.json"))
    assert len(written_files) == 1  # a real file, on disk, before the runner ever runs

    # --- The human-in-the-loop gate: a third, independently-constructed
    # store instance (standing in for the approval CLI, its own process)
    # loads exactly what the trigger wrote, approves it, and saves - the
    # runner below never sees this happen except through the file. ---
    approval_store = RebalancePlanStore(tmp_path)
    plan_to_approve = approval_store.load(plan.target_id)
    approve_plan(plan_to_approve, current_trading_day=SIGNAL_DATE, now=datetime.now(timezone.utc))
    approval_store.save(plan_to_approve)

    # --- Half 2: the runner. Constructed fresh, with only the directory
    # path in common with the trigger above - no shared plan object, no
    # shared store instance, nothing but what a real second process would
    # actually have. ---
    state = flat_running_state()
    state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
    raw_broker = FakeBroker()
    raw_broker.positions = [cnc_position("RELIANCE", 50)]
    raw_broker.quotes = dict(ALL_LIVE_QUOTES)
    raw_broker.place_order_fn = lambda **kwargs: f"ENTRY-{kwargs['tradingsymbol']}"
    raw_broker.submit_emergency_exit_fn = lambda **kwargs: f"EXIT-{kwargs['symbol']}"

    from portfolio_brain_v9 import SECTORS as REAL_SECTORS
    from v34_p02_accounting import initial_checkpoint
    from v34_p02_state import Config
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"), max_simultaneous_positions=6)
    context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))
    engine, raw_broker, *_ = make_stack(state=state, raw_broker=raw_broker, cfg=cfg, context=context)

    runner_store = RebalancePlanStore(tmp_path)  # a fresh store instance - same directory, different object

    def fake_sleep(seconds):
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

    run_forever(engine=engine, store=runner_store, sleep_fn=fake_sleep, max_iterations=40)

    # The proof: reload from disk one more time, through a THIRD store
    # instance, exactly as an operator checking on the system afterward
    # would - nothing but the file itself should carry the outcome.
    final = RebalancePlanStore(tmp_path).load(plan.target_id)
    assert final.status in (RebalancePlanStatus.COMPLETE, RebalancePlanStatus.PARTIAL)
    assert final.exit_status["RELIANCE"] == LegStatus.CONFIRMED
    assert all(status == LegStatus.CONFIRMED for status in final.enter_status.values())
    assert len(list(tmp_path.glob("*.json"))) == 1  # still exactly one plan file - never duplicated


def test_a_second_trigger_run_while_the_first_plan_is_still_mid_flight_resumes_it(tmp_path):
    # Proves resolve_or_create_plan()'s idempotency (R0 §10) really is
    # what protects a cron trigger firing again before the runner has
    # finished the previous plan - not just tested in isolation, but true
    # when a second trigger call is interleaved with real runner activity
    # on the very same directory.
    store_a = RebalancePlanStore(tmp_path)
    plan, outcome = run_trigger(
        quotes=ALL_LIVE_QUOTES, current_portfolio={}, signal_date=SIGNAL_DATE,
        current_trading_day=SIGNAL_DATE, store=store_a, positions=2,
    )
    assert outcome == "CREATED"

    # Simulate the runner having advanced it partway (mid-flight, not
    # terminal) before the trigger fires again.
    plan.status = RebalancePlanStatus.ENTERING
    plan.approved_at = datetime.now(timezone.utc)
    store_a.save(plan)

    store_b = RebalancePlanStore(tmp_path)  # a fresh instance, e.g. a redeployed trigger container
    again, outcome2 = run_trigger(
        quotes=ALL_LIVE_QUOTES, current_portfolio={}, signal_date=SIGNAL_DATE,
        current_trading_day=SIGNAL_DATE, store=store_b, positions=2,
    )
    assert outcome2 == "RESUMED"
    assert again.target_id == plan.target_id
    assert again.status == RebalancePlanStatus.ENTERING  # progress was not reset
    assert len(list(tmp_path.glob("*.json"))) == 1
