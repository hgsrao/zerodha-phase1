"""V11 -> P02 Rebalance Bridge — operator CLI to reconcile a HALTED
RebalancePlan back to a resumable state.

Built 2026-08-19, in direct response to a real gap found while
investigating why Terminal A/B's daily audit logs went completely silent
for the rest of 2026-08-17 despite the underlying P02 engine coming back
healthy (RUNNING) at 05:57:55 UTC that same morning:

v34_bridge_clear_halt.py and v34_bridge_resolve_entry_submit_halt.py both
clear a halt on the ENGINE (institutional_engine_v34_p02_multipos_
candidate.py's own terminator/status). Neither one touches the PLAN
(v34_bridge_rebalance_plan.RebalancePlan) that the bridge itself drives
through v34_bridge_runner_core.drive_plan_one_cycle(). Those are two
separate objects in two separate directories (PLAN_STORE_DIR vs
RUNNER_DATA_DIR). halt_plan() (v34_bridge_rebalance_transitions.py) is
reachable from _drive_entering() on TWO distinct paths - an engine-level
RECONCILIATION_HALT observed after engine.step() (runner_core.py line
~212), and a bare, engine-independent quote-fetch exception in
_fetch_live_price() (runner_core.py line ~164) that never touches the
engine's own audit.jsonl at all. Once a plan is HALTED,
resolve_or_create_plan()'s own docstring is explicit: "requires deliberate
operator reconciliation, never auto-resumed" - and drive_plan_one_cycle()
silently no-ops on a HALTED plan every single cycle
(NO_FURTHER_ACTION_PLAN_STATUSES). No tool implementing that deliberate
reconciliation was ever built. The result, confirmed against the real
2026-08-17 audit.jsonl/plan files for both terminals: clearing the engine
halt did nothing for the plan, which sat HALTED, silently, for the rest
of the day - and would keep sitting HALTED through the next monthly
rebalance, since V11 only computes a fresh target_id then.

WHY THIS IS SAFE TO DO PROGRAMMATICALLY, not just plausible - same
defense-in-depth discipline as v34_bridge_resolve_entry_submit_halt.py,
applied one level up (the plan's enter legs, not one engine position):

  1. Plan must actually be HALTED - else nothing to do.
  2. Every exit leg must already be terminal (all_exit_legs_terminal) -
     R0 §5 step 1 guarantees a real rebalance never opens ENTERING with
     an unresolved exit; a halt discovered with a live exit leg is a
     different, more dangerous case this script explicitly refuses and
     leaves for manual review.
  3. There must be at least one non-terminal enter leg - otherwise this
     plan doesn't need HALTED->ENTERING reconciliation, it needs
     finalize_plan() directly (not this script's job).
  4. Every non-terminal enter leg must be PENDING or SUBMITTED - any
     other leg status is unrecognized for this halt class and refused.
  5. For every SUBMITTED leg, a fresh, real, read-only check against
     engine.state.active_trades (never trusted from the stale plan
     file) decides its true resolved outcome:
       - ctx is None (vanished - the ordinary outcome of an asynchronous
         ENTRY_LOCK/EA1_SHADOW_MODE decline, exactly like every sibling
         leg that resolved normally the same day) -> treated as
         resolved-and-declined. If its R0 §6 bounded retry has not yet
         been used (enter_retry_count < MAX_ENTER_RETRIES), it is given
         that one retry (reset to PENDING) via the plan module's own
         retry_enter_leg() - never a hand-rolled reset - so the ordinary
         runner resubmits it next cycle exactly as if the bridge had
         never gotten stuck. If the retry is already spent, it is marked
         DECLINED via the plan module's own mark_enter_leg().
       - ctx exists and ctx.status == MANAGING -> a real (shadow-book)
         position actually exists; the leg is marked CONFIRMED via
         mark_enter_leg(). This script never fabricates a CONFIRMED
         outcome - only mark_enter_leg() sets it, off a real observed
         ctx.status.
       - ctx exists, ctx.status == ENTRY_SUBMIT, and ctx.entry_order_id is
         None -> provably never submitted (request_entry() always creates
         a position at exactly this status/order_id combination; nothing
         else ever sets an order_id before status moves past ENTRY_SUBMIT)
         - typically means engine.step() aborted (a hard halt on a
         DIFFERENT leg) before ever reaching this one this cycle. Left
         completely as-is (no leg mutation - the plan already correctly
         says SUBMITTED). Not this script's own judgment call: the frozen
         engine's OWN startup reconciliation (_reconcile_one_position(),
         institutional_engine_v34_p02_multipos_candidate.py) has this
         exact case built in and already ran a fresh, real broker check
         for it moments earlier, as part of constructing this very
         engine - if that check had found any ambiguity the engine would
         already have hard-halted and this script would never have
         reached a running engine to even get here.
       - ctx exists with any other status (still resolving - e.g.
         ENTRY_PENDING/PARTIAL_POSITION/PROTECTION/PROTECTION_PENDING) ->
         genuinely ambiguous. Refuses outright, touches nothing. This is
         not this script's decision to make.
  6. PENDING legs are left exactly as PENDING - the ordinary runner
     submits them normally once ENTERING resumes.

Only if every check above passes: transitions plan.status HALTED ->
ENTERING (the one genuinely new transition this script performs - no
existing function in v34_bridge_rebalance_transitions.py offers this path,
by that module's own deliberate design; see halt_plan()'s docstring),
applies each leg's resolution through the plan module's OWN tested
mark_enter_leg()/retry_enter_leg() functions (never a raw field
assignment), finalizes the plan if every leg is now terminal, and durably
saves it. Every reconciliation this script performs is logged to the
engine's own audit.jsonl (OPERATOR_RECONCILED_HALTED_PLAN) so it is
visible in the same place every other operator action in this project is
visible - nothing here is a silent mutation.

Deliberately does NOT drive the plan any further itself (no call to
drive_plan_one_cycle(), no engine.step()) - mirrors v34_bridge_clear_halt.py
and v34_bridge_resolve_entry_submit_halt.py's own precedent of staying a
narrow, single-purpose gate. The next ordinary `python
v34_bridge_runner_main.py` poll cycle picks the now-ENTERING plan up and
drives it forward completely normally.

Same lock-release discipline as the two precedent scripts: engine
construction acquires the real OS runner lock; released in a finally
block on every exit path.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional, TextIO

from v34_bridge_rebalance_plan import (
    LegStatus,
    RebalancePlanStatus,
    RebalancePlanStore,
)
from v34_bridge_rebalance_transitions import (
    MAX_ENTER_RETRIES,
    TERMINAL_LEG_STATUSES,
    all_exit_legs_terminal,
    finalize_plan,
    mark_enter_leg,
    retry_enter_leg,
)
from v34_p02_state import PositionStatus


def _superseding_plan(plan: RebalancePlan, store: RebalancePlanStore) -> Optional[RebalancePlan]:
    """EA1-R1 step 6 (staleness gate). Owner-authorized rule, 2026-08-19:
    a HALTED plan stays REARM-eligible for the rest of ITS monthly cycle -
    it only becomes stale once a NEWER monthly rebalance has already been
    computed for this same terminal (a strictly later signal_date already
    on disk). Deliberately NOT R0 §7's literal same-day is_target_stale()
    formula (current_trading_day != signal_date) - that formula gates
    plan CREATION/APPROVAL, a different question ("is this signal too
    old to start acting on") from this one ("has this already-approved,
    already-partially-executed plan's opportunity been superseded").
    Under R0 §7's literal formula, today's real SBIN/SUNPHARMA/SHRIRAMFIN
    fixes (all completed 1-2 days after signal_date) would have failed
    staleness and had to be abandoned - correctly rejected as the wrong
    rule for this case; the owner confirmed this reading explicitly.

    Checks disk directly (store.list_all_plans(), not just active ones -
    a plan that already reached COMPLETE/PARTIAL still proves a rebalance
    happened) rather than computing a calendar boundary - an objective,
    verifiable fact ("did the trigger actually run again") beats guessing
    when the next rebalance is due from a formula that could drift out of
    sync with the trigger's real schedule. Returns the superseding plan
    (for reporting), or None if this plan is still current."""
    newer = [
        p for p in store.list_all_plans()
        if p.target_id != plan.target_id and p.signal_date > plan.signal_date
    ]
    if not newer:
        return None
    return max(newer, key=lambda p: p.signal_date)


def run_reconcile(engine, plan, store, *, note: str, assume_yes: bool, out: TextIO, err: TextIO,
                   input_fn=input, sidecar=None) -> int:
    """Pure-ish core: takes an already-constructed engine and already-
    loaded plan (real in production, fake/isolated in tests) so this
    logic is exercised identically either way.

    `sidecar` (EA1-R1, 2026-08-19): optional PlanIncidentSidecar, defaults
    to None - zero effect on any existing caller/test. When supplied,
    records PLAN_RECONCILIATION_STARTED once every check has passed and
    the resolution plan is about to be shown, and PLAN_REARMED once the
    transition actually happens - completing the lifecycle vocabulary
    started by halt_plan()'s own PLAN_HALTED record. Also records
    PLAN_ABANDONED when the staleness gate below refuses to resume a
    superseded plan."""
    if plan.status != RebalancePlanStatus.HALTED:
        print(f"Plan {plan.target_id} is {plan.status.value} - not HALTED, nothing to reconcile.", file=out)
        return 0

    superseding = _superseding_plan(plan, store)
    if superseding is not None:
        print(
            f"Plan {plan.target_id} (signal_date={plan.signal_date}) has been SUPERSEDED - a newer "
            f"monthly rebalance already exists for this terminal: {superseding.target_id} "
            f"(signal_date={superseding.signal_date}, status={superseding.status.value}). "
            "This plan's opportunity is stale; it will not be resumed.",
            file=out,
        )
        if not assume_yes:
            answer = input_fn(
                f"Formally abandon plan {plan.target_id} (stays HALTED - no further action ever taken on "
                f"it, recorded in the incident sidecar) with operator note {note!r}? [y/N] "
            ).strip().lower()
            if answer != "y":
                print("Not abandoned - plan remains HALTED, untouched.", file=out)
                return 1
        if sidecar is not None:
            sidecar.log_abandoned(
                plan.target_id,
                reason=f"superseded by {superseding.target_id} (signal_date {superseding.signal_date} "
                       f"> {plan.signal_date}) - monthly cycle has moved on",
                operator_note=note,
            )
        print(f"Plan {plan.target_id} formally marked ABANDONED. It stays HALTED permanently - "
              "this is the correct terminal state for a stale opportunity, not an error.", file=out)
        return 0

    if not all_exit_legs_terminal(plan):
        non_terminal_exits = {s: st.value for s, st in plan.exit_status.items() if st not in TERMINAL_LEG_STATUSES}
        print(
            f"Refusing: plan {plan.target_id} has non-terminal exit leg(s) {non_terminal_exits}. "
            "This script only handles a halt discovered during ENTERING (R0 §5 step 1 guarantees "
            "exits are always resolved before entering begins) - a halt with an unresolved exit needs "
            "manual review, not this script.",
            file=err,
        )
        return 1

    non_terminal_enters = {s: st for s, st in plan.enter_status.items() if st not in TERMINAL_LEG_STATUSES}
    if not non_terminal_enters:
        print(
            f"Refusing: plan {plan.target_id} has no non-terminal enter legs - nothing to reconcile here "
            "(it may just need finalize_plan() directly).",
            file=err,
        )
        return 1

    unrecognized = {s: st.value for s, st in non_terminal_enters.items()
                    if st not in (LegStatus.PENDING, LegStatus.SUBMITTED)}
    if unrecognized:
        print(
            f"Refusing: plan {plan.target_id} has enter leg(s) in an unrecognized non-terminal status "
            f"for this halt class: {unrecognized}. Needs manual review, not this script.",
            file=err,
        )
        return 1

    print(f"Plan {plan.target_id}: {len(non_terminal_enters)} non-terminal enter leg(s) found.", file=out)
    print("Making a fresh, real, read-only check of engine.state.active_trades for each SUBMITTED leg...", file=out)

    resolutions: dict[str, str] = {}
    for symbol, status in non_terminal_enters.items():
        if status == LegStatus.PENDING:
            resolutions[symbol] = "LEAVE_PENDING"
            continue
        ctx = engine.state.active_trades.get(symbol)
        if ctx is None:
            used = plan.enter_retry_count.get(symbol, 0)
            resolutions[symbol] = "DECLINE" if used >= MAX_ENTER_RETRIES else "RETRY"
        elif ctx.status == PositionStatus.MANAGING:
            resolutions[symbol] = "CONFIRMED"
        elif ctx.status == PositionStatus.ENTRY_SUBMIT and ctx.entry_order_id is None:
            # Provably never submitted, not just "probably" - request_entry()
            # always creates a position at exactly this status with
            # entry_order_id=None, and the ONLY place that ever sets an
            # order_id is _step_entry_submit()'s own broker.place_order()
            # call, which only runs once status has already moved past
            # ENTRY_SUBMIT. A position still sitting here means engine.step()
            # never even reached it - zero broker interaction, ever, for
            # this leg this round. This is not this script's own judgment
            # call: the frozen engine's OWN startup reconciliation
            # (_reconcile_one_position(), institutional_engine_v34_p02_
            # multipos_candidate.py) already has this exact case built in -
            # "safe to resume only when broker has zero activity for this
            # symbol" - and it already ran a fresh, real broker check for
            # this exact position moments ago, as part of _build_engine()
            # constructing this very engine. If that check had found any
            # ambiguity it would have hard-halted right there and this
            # script would never have reached RUNNING to even get here.
            # No leg mutation needed - the plan already correctly says
            # SUBMITTED (an intent really was created); the ordinary
            # runner's own per-symbol loop already does the right thing
            # for a SUBMITTED leg whose ctx exists but isn't MANAGING yet
            # ("leave SUBMITTED, observe again next cycle") - this leg just
            # needs the plan unlatched so that ordinary path can run.
            resolutions[symbol] = "LEAVE_SUBMITTED"
        else:
            print(
                f"REFUSING - {symbol} has an active position with status={ctx.status.value!r} - still "
                "resolving, not clearly settled either way. This needs manual review, not an automatic "
                "reconciliation. Fail closed; nothing touched.",
                file=err,
            )
            return 1

    print("Resolution plan (nothing applied yet):", file=out)
    for symbol, action in resolutions.items():
        print(f"  {symbol}: {action}", file=out)

    if sidecar is not None:
        sidecar.log_reconciliation_started(plan.target_id, operator_note=note, resolutions=resolutions)

    if not assume_yes:
        answer = input_fn(
            f"Apply this reconciliation to plan {plan.target_id} with operator note {note!r}? [y/N] "
        ).strip().lower()
        if answer != "y":
            print("Not reconciled.", file=out)
            return 1

    # The one genuinely new transition this script performs - no sealed
    # function offers HALTED -> ENTERING (see module docstring above).
    # Only reached after every check above has passed.
    plan.status = RebalancePlanStatus.ENTERING
    for symbol, action in resolutions.items():
        if action == "CONFIRMED":
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)
        elif action == "RETRY":
            retry_enter_leg(plan, symbol)
        elif action == "DECLINE":
            mark_enter_leg(plan, symbol, LegStatus.DECLINED)
        # LEAVE_PENDING / LEAVE_SUBMITTED: leg status already matches
        # reality, no call needed - the ordinary runner picks it up.

    if all(st in TERMINAL_LEG_STATUSES for st in plan.enter_status.values()):
        finalize_plan(plan)

    store.save(plan)
    engine.audit.log(
        "OPERATOR_RECONCILED_HALTED_PLAN", target_id=plan.target_id, note=note,
        resolutions=resolutions, plan_status_after=plan.status.value,
    )
    engine.store.save(engine.state)
    if sidecar is not None:
        sidecar.log_reconciliation_proved_safe(plan.target_id, resolutions=resolutions)
        sidecar.log_rearmed(plan.target_id, plan_status_after=plan.status.value)

    print(f"Reconciled. Plan {plan.target_id} status now: {plan.status.value}.", file=out)
    print("This does NOT drive the plan any further itself - it only unlatches it.", file=out)
    print("Next step (separate, deliberate action - not run by this script):", file=out)
    print("  python v34_bridge_runner_main.py", file=out)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile a HALTED RebalancePlan back to ENTERING after independently re-verifying "
                    "each stuck enter leg's true outcome against the real engine state (operator gate)."
    )
    parser.add_argument("--target-id", required=True, help="Exact target_id of the halted plan (e.g. V11_2026-08-17_10440cc4f4044c2f).")
    parser.add_argument("--note", required=True, help="Operator attestation note - required, non-empty.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt (non-interactive use only).")
    args = parser.parse_args(argv)

    if not args.note or not args.note.strip():
        print("Refused: --note must be non-empty.", file=sys.stderr)
        return 1
    if not args.target_id or not args.target_id.strip():
        print("Refused: --target-id must be non-empty.", file=sys.stderr)
        return 1

    from v34_bridge_runner_main import _build_engine
    from v34_bridge_plan_incident_sidecar import PlanIncidentSidecar

    engine = _build_engine(on_event=print)
    try:
        plans_dir = Path(os.environ.get("PLAN_STORE_DIR", "/data/plans"))
        store = RebalancePlanStore(plans_dir)
        sidecar = PlanIncidentSidecar(plans_dir / "plan_events.jsonl")
        plan = store.load(args.target_id.strip())
        if plan is None:
            print(f"Refused: no plan found for target_id={args.target_id.strip()!r} in {plans_dir}.", file=sys.stderr)
            return 1
        return run_reconcile(engine, plan, store, note=args.note, assume_yes=args.yes, out=sys.stdout, err=sys.stderr, sidecar=sidecar)
    finally:
        engine.lock_provider.release()


if __name__ == "__main__":
    sys.exit(main())
