"""Integration tests: drive_plan_one_cycle() actually persists a real
halt reason via PlanIncidentSidecar (EA1-R1 step 5) - the direct fix for
the defect the owner called out as more serious than the 429 itself.
"""
from datetime import date
from decimal import Decimal

from test_v34_bridge_runner_core import ALL_LIVE_QUOTES, _approve
from test_v34_p02_lifecycle_integration import make_context, make_stack
from test_v34_p02_multipos_engine import FakeBroker, flat_running_state
from v34_bridge_plan_incident_sidecar import PlanIncidentSidecar
from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import RebalancePlanStatus, RebalancePlanStore, create_rebalance_plan
from v34_bridge_runner_core import drive_plan_one_cycle
from v34_bridge_target_portfolio import build_target_portfolio

SIGNAL_DATE = date(2026, 8, 14)


def test_a_real_halt_during_exiting_is_durably_recorded_with_its_real_reason(tmp_path):
    target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=2)
    diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=target)
    plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
    plan_store = RebalancePlanStore(tmp_path / "plans")
    plan_store.save(plan)
    _approve(plan)
    plan_store.save(plan)

    from institutional_engine_v34_p02_multipos_candidate import PositionStatus
    from test_v34_p02_multipos_engine import cnc_position, sample_ctx

    state = flat_running_state()
    state.active_trades["RELIANCE"] = sample_ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, filled_qty=50, stop_order_id=None)
    raw_broker = FakeBroker()
    raw_broker.positions = [cnc_position("RELIANCE", 50)]
    raw_broker.quotes = dict(ALL_LIVE_QUOTES)
    raw_broker.submit_emergency_exit_fn = lambda **kwargs: f"EXIT-{kwargs['symbol']}"

    from portfolio_brain_v9 import SECTORS as REAL_SECTORS
    from v34_p02_accounting import initial_checkpoint
    from v34_p02_state import Config
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
    context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))
    engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
        state=state, raw_broker=raw_broker, cfg=cfg, context=context,
    )

    sidecar = PlanIncidentSidecar(tmp_path / "plans" / "plan_events.jsonl")

    drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store, sidecar=sidecar)  # APPROVED -> EXITING (intent)
    assert plan.status == RebalancePlanStatus.EXITING
    drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store, sidecar=sidecar)  # submits the real exit
    ctx = engine.state.active_trades["RELIANCE"]
    assert ctx.exit_order_id is not None

    raw_broker.order_details[ctx.exit_order_id] = {
        "order_id": ctx.exit_order_id, "status": "REJECTED", "filled_quantity": 0, "quantity": 50,
    }
    drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store, sidecar=sidecar)

    assert plan.status == RebalancePlanStatus.HALTED

    # --- The actual fix: the real reason is now durably recoverable from
    # the plan's own target_id, with no dependency on whether the engine
    # layer also happened to log something. ---
    events = sidecar.events_for(plan.target_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "PLAN_HALTED"
    assert "RELIANCE" in events[0]["fields"]["reason"]
    assert "REJECTED" in events[0]["fields"]["reason"] or "rejected" in events[0]["fields"]["reason"].lower()


def test_no_sidecar_supplied_is_a_complete_no_op(tmp_path):
    """Every OTHER existing test in this project already calls
    drive_plan_one_cycle() without a sidecar and passes unchanged - this
    just makes the claim explicit and checks no file gets created."""
    target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=2)
    diff = compute_rebalance_diff(current_portfolio={}, target=target)
    plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
    plan_store = RebalancePlanStore(tmp_path / "plans")
    plan_store.save(plan)
    _approve(plan)
    plan_store.save(plan)

    raw_broker = FakeBroker()
    raw_broker.quotes = dict(ALL_LIVE_QUOTES)

    from portfolio_brain_v9 import SECTORS as REAL_SECTORS
    from v34_p02_accounting import initial_checkpoint
    from v34_p02_state import Config
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
    context = make_context(sector_lookup=REAL_SECTORS, checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital))
    engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
        state=flat_running_state(), raw_broker=raw_broker, cfg=cfg, context=context,
    )

    drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # no sidecar kwarg at all
    assert not (tmp_path / "plans" / "plan_events.jsonl").exists()
