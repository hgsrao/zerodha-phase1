"""Full-pipeline sandbox demonstration — REAL captured V11 signal data,
driven through the REAL bridge trigger, REAL human-approval transition,
and the REAL EA-1 shadow-mode runner orchestration — against a FAKE Kite
connection. NOT a real Zerodha connection anywhere in this file.

WHY THIS EXISTS: v34_bridge_ea1_sandbox_demo.py (the earlier EA-1 script)
proves the shadow-mode mechanism itself, in isolation, with one
hardcoded RELIANCE example. This script answers a different question -
"run a sandbox with the available data and see how it performs" - by
reusing data that is genuinely ALREADY in this repository, not invented
for this script:

    YESTERDAYS_REAL_QUOTES / EXPECTED_YESTERDAY_BASKET, from
    test_v34_bridge_target_portfolio.py - REGENERATED 2026-08-16 alongside
    that file's own fixture, once the universe grew from 20 to 50 real
    Nifty symbols this session (see that file's docstring, and the
    p01d-and-v11-bridge-status memory, for the full record). Feeding
    these real formation-price-derived quotes through the real
    evaluate_external_momentum()/build_target_portfolio() pipeline
    reproduces V11's real current top-4 selection on the expanded
    universe exactly: LAURUSLABS (13 sh), SHRIRAMFIN (23 sh), HINDALCO
    (25 sh), ADANIENT (8 sh) - real V11 output, not fabricated numbers.

This script drives that real basket through the FULL bridge pipeline,
each stage using the real, already-tested production function - nothing
reimplemented or stubbed:

    v34_bridge_trigger.run_trigger()          (real V11 -> RebalancePlan)
            |  plan written to a shared RebalancePlanStore directory
            v
    v34_bridge_rebalance_transitions.approve_plan()   (simulated human click)
            |
            v
    v34_bridge_runner_core.drive_plan_one_cycle()     (real runner orchestration,
            |                                          called repeatedly exactly as
            |                                          run_forever()'s poll loop does)
            v
    build_production_engine(shadow_mode=True)          (EA-1: real engine, FAKE
                                                          broker, LIVE_TRADING_
                                                          ENABLED=False, WOULD_SUBMIT
                                                          audit trail, zero real
                                                          order calls)

Two SEPARATE RebalancePlanStore instances (one for the "trigger side",
one for the "runner side") are pointed at the SAME on-disk directory and
the runner side re-loads the plan from disk rather than reusing the
trigger's in-memory object - proving the real file-based IPC this bridge
actually uses in production (v34_bridge_trigger.py's own docstring),
not a shortcut only possible because this script happens to run single-
process.

Uses a fresh, disposable sandbox directory under the system temp
directory (never the real production RUNNER_DATA_DIR/PLAN_STORE_DIR) and
deletes any prior run's directory first.

USAGE:
    python v34_bridge_full_pipeline_sandbox_demo.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from v34_bridge_rebalance_plan import NO_FURTHER_ACTION_PLAN_STATUSES, RebalancePlanStore
from v34_bridge_rebalance_transitions import approve_plan
from v34_bridge_runner_core import drive_plan_one_cycle
from v34_bridge_runner_startup import ProductionRunnerPaths, build_production_engine
from v34_bridge_trigger import run_trigger
from v34_p02_state import Config

SANDBOX_DIR = Path(tempfile.gettempdir()) / "v34_bridge_full_pipeline_sandbox_demo_run"

# Regenerated 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
# own fixture - REAL V11 top-4 for the current expanded (50-symbol)
# universe, each symbol's own real formation price used as its quote -
# real and deterministic, not invented for this script.
REAL_QUOTES = {
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
    "NSE:HINDALCO": {"last_price": 974.5},
    "NSE:ADANIENT": {"last_price": 3009.2},
}
SIGNAL_DATE = date(2026, 8, 14)  # a real Friday - matches the captured telemetry's own date


class FixedClock:
    """A known-good NSE trading moment (Friday 2026-08-14, 10:30 IST) -
    deterministic, so this demo's output doesn't depend on when it
    happens to be run."""

    def now(self) -> datetime:
        return datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)


def _leg_statuses(plan) -> dict:
    return {symbol: status.value for symbol, status in plan.enter_status.items()}


def main() -> None:
    if SANDBOX_DIR.exists():
        shutil.rmtree(SANDBOX_DIR)
    plans_dir = SANDBOX_DIR / "plans"
    runner_data_dir = SANDBOX_DIR / "runner"

    print("=== v34 bridge full-pipeline sandbox demo ===")
    print("REAL captured V11 signal data. FAKE Kite connection. No real credentials, no real network call.\n")

    # ---- STEP 1: the bridge trigger - real V11 signal pipeline --------
    print("=== STEP 1: bridge trigger (v34_bridge_trigger.run_trigger) ===")
    trigger_store = RebalancePlanStore(plans_dir)
    plan, outcome = run_trigger(
        quotes=REAL_QUOTES,
        current_portfolio={},  # starting flat, matching the EA-1 sandbox demo's convention
        signal_date=SIGNAL_DATE,
        current_trading_day=SIGNAL_DATE,
        store=trigger_store,
    )
    print(f"trigger outcome = {outcome!r}")
    print(f"plan.target_id = {plan.target_id}")
    print(f"plan.diff.enters (symbol -> quantity, from REAL V11 output) = {dict(plan.diff.enters)}")
    print(f"plan.diff.exits = {dict(plan.diff.exits)}  (empty - starting flat)")
    print(f"plan.status (after trigger) = {plan.status.value}")

    # ---- STEP 2: human-in-the-loop approval ----------------------------
    print("\n=== STEP 2: human-in-the-loop approval (simulated operator action) ===")
    approve_plan(plan, current_trading_day=SIGNAL_DATE, now=datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc))
    trigger_store.save(plan)
    print(f"plan.status (after approval) = {plan.status.value}")

    # ---- STEP 3: build the real production runner stack ---------------
    print("\n=== STEP 3: build the real production runner stack (shadow_mode=True) ===")
    kite = FakeKiteConnectWithOrders()  # FAKE - not a real Zerodha connection
    kite.positions_response = {"net": [], "day": []}
    kite.orders_response = []
    kite.ltp_response = dict(REAL_QUOTES)
    kite.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]

    from portfolio_brain_v9 import SECTORS

    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    engine = build_production_engine(
        paths=ProductionRunnerPaths(data_dir=runner_data_dir), kite=kite, live_trading_enabled=False,
        cfg=cfg, sector_lookup=SECTORS, dp_charge_per_symbol=Decimal("15.34"),
        clock=FixedClock(), shadow_mode=True, on_event=print,
    )
    print(f"engine.broker.raw_broker class = {engine.broker.raw_broker.__class__.__name__}")
    print(f"engine.state.status (before driving the plan) = {engine.state.status.value}")

    # ---- STEP 4: the runner side re-loads the plan from disk -----------
    # Proves real file-based IPC (v34_bridge_trigger.py's own documented
    # design) - the runner never touches the trigger's in-memory object.
    runner_store = RebalancePlanStore(plans_dir)
    runner_plan = runner_store.load(plan.target_id)
    assert runner_plan is not None, "runner-side store could not find the plan the trigger just wrote to disk"
    print(f"\nplan re-loaded from disk by the runner side: status={runner_plan.status.value}")

    # ---- STEP 5: drive the plan forward, exactly as run_forever() would -
    print("\n=== STEP 5: drive_plan_one_cycle(), called repeatedly (same function run_forever()'s poll loop calls) ===")
    MAX_CYCLES = 60
    for cycle in range(1, MAX_CYCLES + 1):
        status = drive_plan_one_cycle(plan=runner_plan, engine=engine, store=runner_store)
        print(f"cycle {cycle:>2}: plan.status={status:<15} enter_status={_leg_statuses(runner_plan)}")
        if runner_plan.status in NO_FURTHER_ACTION_PLAN_STATUSES:
            print(f"\nplan reached a terminal status after {cycle} cycle(s) - stopping.")
            break
    else:
        print(f"\nNOTE: plan did not reach a terminal status within {MAX_CYCLES} cycles - reporting current state honestly below, not hiding this.")

    engine.lock_provider.release()

    # ---- FINAL STATE, printed in full, not summarized ------------------
    print("\n=== FINAL STATE ===")
    print(f"plan.status = {runner_plan.status.value}")
    print(f"plan.enter_status = {_leg_statuses(runner_plan)}")
    print(f"engine.terminator.halted = {engine.terminator.halted}")
    print(f"engine.state.status = {engine.state.status.value}")
    print(f"kite.place_order_calls (REAL broker write calls - must be empty) = {kite.place_order_calls}")

    print("\n=== Raw WOULD_SUBMIT audit records, exactly as written to disk ===")
    audit_path = runner_data_dir / "audit.jsonl"
    if audit_path.exists():
        count = 0
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["event_type"] == "WOULD_SUBMIT":
                count += 1
                print(json.dumps(record, indent=2))
        print(f"\n{count} WOULD_SUBMIT record(s) written.")
    else:
        print("(no audit.jsonl found)")


if __name__ == "__main__":
    main()
