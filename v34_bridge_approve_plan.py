"""V11 -> P02 Rebalance Bridge — human-in-the-loop approval CLI.

Phase 1 default (gated, not fully automatic - see this session's regular-
vs-managed design discussion): a RebalancePlan the trigger writes sits in
CREATED, untouched by the runner (v34_bridge_runner_core._drive_created()
is a deliberate no-op), until a human explicitly approves it here. Nothing
reaches request_exit()/request_entry() without this script having run
first - begin_exiting() itself requires APPROVED, not CREATED
(v34_bridge_rebalance_transitions.py).

Prints the plan's actual EXIT/ENTER/KEEP breakdown before asking for
confirmation - a bare target_id + "y/n" prompt would defeat the entire
purpose of a human-in-the-loop gate. The operator has to see what they
are approving, not just attest to a string.

No broker/network calls. Reads and writes exactly one RebalancePlan file
through the same RebalancePlanStore the trigger and runner already use -
no new IPC mechanism.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from v34_bridge_rebalance_plan import RebalancePlan, RebalancePlanStatus, RebalancePlanStore
from v34_bridge_rebalance_transitions import approve_plan


def _print_plan_summary(plan: RebalancePlan) -> None:
    print(f"Plan target_id: {plan.target_id}")
    print(f"Signal date:    {plan.signal_date.isoformat()}")
    print(f"Status:         {plan.status.value}")
    print()
    print("EXIT (sell):")
    if not plan.diff.exits:
        print("  (none)")
    for symbol, qty in sorted(plan.diff.exits.items()):
        print(f"  SELL {symbol:<12} qty={qty}")
    print()
    print("ENTER (buy):")
    if not plan.diff.enters:
        print("  (none)")
    for symbol, qty in sorted(plan.diff.enters.items()):
        print(f"  BUY  {symbol:<12} qty={qty}")
    print()
    print("KEEP (unchanged):")
    if not plan.diff.keep:
        print("  (none)")
    for symbol in sorted(plan.diff.keep):
        print(f"  {symbol}")
    print()


def main(argv: Optional[List[str]] = None, *, today: Optional[date] = None) -> int:
    """`today` exists purely for tests to inject a fixed date instead of
    the real clock - production invocation always leaves it at None,
    which defaults to date.today()."""
    parser = argparse.ArgumentParser(description="Approve a pending V11->P02 rebalance plan (human-in-the-loop gate).")
    parser.add_argument("target_id", help="target_id of the plan to approve")
    parser.add_argument("--plans-dir", default=None, help="plan store directory (default: $PLAN_STORE_DIR or /data/plans)")
    parser.add_argument("--yes", action="store_true", help="skip the interactive confirmation prompt (non-interactive use only)")
    args = parser.parse_args(argv)

    plans_dir = Path(args.plans_dir or os.environ.get("PLAN_STORE_DIR", "/data/plans"))
    store = RebalancePlanStore(plans_dir)

    plan = store.load(args.target_id)
    if plan is None:
        print(f"No plan found for target_id {args.target_id!r} in {plans_dir}", file=sys.stderr)
        return 1
    if plan.status != RebalancePlanStatus.CREATED:
        print(f"Plan {args.target_id} is {plan.status.value}, not CREATED - nothing to approve.", file=sys.stderr)
        return 1

    _print_plan_summary(plan)

    if not args.yes:
        answer = input("Approve this rebalance? [y/N] ").strip().lower()
        if answer != "y":
            print("Not approved.")
            return 1

    current_trading_day = today if today is not None else date.today()
    try:
        approve_plan(plan, current_trading_day=current_trading_day, now=datetime.now(timezone.utc))
    except Exception as exc:
        print(f"Refused to approve: {exc}", file=sys.stderr)
        return 1

    store.save(plan)
    print(f"Approved. approved_at={plan.approved_at.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
