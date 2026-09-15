"""V11 -> P02 Rebalance Bridge — RebalancePlan transition guards (R0 §5, §6).

The exit-before-entry invariant (R0 §5) is enforced structurally, not by
convention: `plan.status` is the only key that unlocks ENTER-leg
mutation, and that key can only be produced by a chain of guarded
transitions, each of which re-checks its own precondition against the
plan's actual leg statuses rather than trusting the caller sequenced
things correctly.

    CREATED --approve_plan--> APPROVED --begin_exiting--> EXITING -->
    advance_to_exits_complete --> EXITS_COMPLETE --begin_entering-->
    ENTERING --finalize_plan--> COMPLETE | PARTIAL

`approve_plan()` is the human-in-the-loop gate this session added
(Phase 1 default: gated, not fully automatic - see the regular-vs-managed
design discussion). It is the ONLY path into APPROVED, and
`begin_exiting()` below now requires APPROVED rather than CREATED - so no
EXIT leg can ever reach `request_exit()` (v34_bridge_runner_core.py)
without a human having explicitly approved the plan first. The checkpoint
matters as much as its existence: gating any later transition (e.g.
`advance_to_exits_complete()`) would let every EXIT leg execute
unapproved, since that transition only fires *after* exits are already
CONFIRMED/FAILED at the broker. `approve_plan()` also re-checks R0 §7
staleness at the moment of approval, not just at creation - a plan can
sit unapproved across a trading-day rollover, and a delayed human click
must not inject an expired target into the runner.

`advance_to_exits_complete()` is the load-bearing guard: it refuses
(`IllegalTransitionError`) unless every exit leg is already `CONFIRMED`
or `FAILED`. `begin_entering()` can only be called from
`EXITS_COMPLETE` - there is no other path to `ENTERING`. `mark_enter_leg()`
- the function that would eventually record an ENTER leg's outcome -
itself refuses unless `plan.status == ENTERING`. A caller (the future
runner, Step B) cannot submit an ENTER leg's outcome early through this
API; it gets an exception, not a wrong answer, at the exact point it
would have violated the sequencing.

R0 §6's bounded-retry-once policy for a declined ENTER leg is modeled
here as `retry_enter_leg()`. It is deliberately a guarded transition, not
a runner-side counter check, for the same reason exit-before-entry
sequencing is: a cap that only the caller's own discipline enforces is
conventional, not structural. `retry_enter_leg()` refuses
(`IllegalTransitionError`) once a symbol's one retry is already spent -
Step B (the runner) never needs to know `MAX_ENTER_RETRIES` itself; it
just tries to retry and falls back to `mark_enter_leg(..., DECLINED)` if
refused. The retry count lives on `RebalancePlan.enter_retry_count`
(added as a deliberate, versioned R0 §4 schema extension once this gap
was found - not a silent patch) precisely because it is control state:
losing it across a crash would let a resumed runner forget a retry
already happened and issue a second one, which is the exact failure mode
this function exists to make impossible.

No broker/network calls. No P02 import.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from v34_bridge_plan_incident_sidecar import PlanIncidentSidecar
from v34_bridge_rebalance_plan import (
    TERMINAL_LEG_STATUSES,
    LegStatus,
    RebalancePlan,
    RebalancePlanStatus,
    StaleTargetError,
    is_target_stale,
)


class IllegalTransitionError(RuntimeError):
    """A RebalancePlan status or leg-status transition was attempted whose
    precondition does not hold. Always fail closed - raise, never silently
    no-op or allow an out-of-sequence mutation."""


def all_exit_legs_terminal(plan: RebalancePlan) -> bool:
    """Vacuously True when there are no exit legs at all (a pure-ENTER
    rebalance) - intentional, not a special case: R0's sequencing rule is
    "no ENTER before every EXIT is resolved," which is trivially satisfied
    when there was nothing to exit."""
    return all(status in TERMINAL_LEG_STATUSES for status in plan.exit_status.values())


def all_enter_legs_terminal(plan: RebalancePlan) -> bool:
    return all(status in TERMINAL_LEG_STATUSES for status in plan.enter_status.values())


def _require_status(plan: RebalancePlan, expected: RebalancePlanStatus, action: str) -> None:
    if plan.status != expected:
        raise IllegalTransitionError(
            f"Cannot {action}: plan {plan.target_id} is {plan.status.value}, expected {expected.value}."
        )


def approve_plan(plan: RebalancePlan, *, current_trading_day: date, now: datetime) -> None:
    """CREATED -> APPROVED. The human-in-the-loop gate (Phase 1 default -
    see module docstring). The only path into APPROVED; begin_exiting()
    below requires APPROVED, so this is the sole checkpoint through which
    every rebalance's exit side must pass before touching P02/the broker
    at all.

    Re-checks R0 §7 staleness at the moment of approval (not just at
    plan-creation time, via the same is_target_stale() formula
    create_rebalance_plan() uses) - refuses (StaleTargetError) rather
    than approve a plan whose signal is no longer valid for today. A plan
    can sit unapproved across a trading-day rollover; a delayed human
    click must not inject an expired target into the runner.

    Sets status and approved_at together, in this one function - the two
    fields RebalancePlan.__post_init__ requires to always agree with each
    other."""
    _require_status(plan, RebalancePlanStatus.CREATED, "approve")
    if is_target_stale(plan.signal_date, current_trading_day=current_trading_day):
        raise StaleTargetError(
            f"Refusing to approve plan {plan.target_id}: signal_date "
            f"{plan.signal_date.isoformat()} != current trading day "
            f"{current_trading_day.isoformat()} (R0 §7)."
        )
    plan.status = RebalancePlanStatus.APPROVED
    plan.approved_at = now


def begin_exiting(plan: RebalancePlan) -> None:
    """APPROVED -> EXITING. Per R0 §5 step 1: all EXIT legs may be
    submitted concurrently once this transition happens - they don't
    compete with each other for capital/sector slots the way entries do.

    Requires APPROVED, not CREATED - this is the load-bearing half of the
    approval gate: it is unreachable without approve_plan() having
    already succeeded, which makes "no EXIT leg without human approval"
    a structural property of this API, not a convention callers have to
    remember to honor."""
    _require_status(plan, RebalancePlanStatus.APPROVED, "begin exiting")
    plan.status = RebalancePlanStatus.EXITING


def mark_exit_leg(plan: RebalancePlan, symbol: str, status: LegStatus) -> None:
    """Record an EXIT leg's observed outcome. Only legal while the plan is
    EXITING - guards against a stray update before submission or after
    the phase has already closed."""
    if symbol not in plan.exit_status:
        raise IllegalTransitionError(f"{symbol!r} is not an exit leg of plan {plan.target_id}.")
    _require_status(plan, RebalancePlanStatus.EXITING, f"update exit leg {symbol!r}")
    if status == LegStatus.DECLINED:
        raise IllegalTransitionError(
            f"Cannot mark exit leg {symbol!r} DECLINED - exits are never gated by P02 (R0 §2/§5)."
        )
    plan.exit_status[symbol] = status


def advance_to_exits_complete(plan: RebalancePlan) -> None:
    """EXITING -> EXITS_COMPLETE. The load-bearing guard: refuses unless
    every exit leg has already reached CONFIRMED or FAILED. This is the
    single check that makes exit-before-entry structural rather than
    conventional - nothing downstream of this function can reach ENTERING
    without passing through here first."""
    _require_status(plan, RebalancePlanStatus.EXITING, "advance to EXITS_COMPLETE")
    if not all_exit_legs_terminal(plan):
        pending = {s: st.value for s, st in plan.exit_status.items() if st not in TERMINAL_LEG_STATUSES}
        raise IllegalTransitionError(
            f"Cannot advance plan {plan.target_id} to EXITS_COMPLETE: exit leg(s) not yet "
            f"terminal: {pending}. No ENTER may be submitted until every EXIT leg is "
            "CONFIRMED or FAILED (R0 §5)."
        )
    plan.status = RebalancePlanStatus.EXITS_COMPLETE


def begin_entering(plan: RebalancePlan) -> None:
    """EXITS_COMPLETE -> ENTERING. The only path to ENTERING - and
    therefore the only path to a state where mark_enter_leg() will accept
    a call - runs through advance_to_exits_complete()'s guard above."""
    _require_status(plan, RebalancePlanStatus.EXITS_COMPLETE, "begin entering")
    plan.status = RebalancePlanStatus.ENTERING


def mark_enter_leg(plan: RebalancePlan, symbol: str, status: LegStatus) -> None:
    """Record an ENTER leg's terminal outcome (CONFIRMED, DECLINED, or
    FAILED - see module docstring re: retry bookkeeping living in Step B,
    not here). Only legal while the plan is ENTERING - this is the
    concrete enforcement point: it is unreachable unless begin_entering()
    already succeeded, which is unreachable unless advance_to_exits_complete()
    already succeeded, which is unreachable unless every exit leg was
    already terminal. The chain cannot be shortcut through this API."""
    if symbol not in plan.enter_status:
        raise IllegalTransitionError(f"{symbol!r} is not an enter leg of plan {plan.target_id}.")
    _require_status(plan, RebalancePlanStatus.ENTERING, f"update enter leg {symbol!r}")
    plan.enter_status[symbol] = status


MAX_ENTER_RETRIES = 1  # R0 §6: retry exactly once, then terminally decline.


def retry_enter_leg(plan: RebalancePlan, symbol: str) -> None:
    """Consume `symbol`'s single bounded retry (R0 §6) and reset it to
    PENDING so the runner resubmits it next cycle. Only legal while the
    plan is ENTERING. Refuses once the retry is already spent - this is
    the structural cap: a caller cannot retry the same leg twice through
    this API no matter what it does, exactly as `mark_enter_leg()` cannot
    be called before `begin_entering()` succeeds. Increments the count and
    resets the status in one mutation so the two can never drift apart
    (a retry recorded with no matching resubmission, or a resubmission
    with no matching count)."""
    if symbol not in plan.enter_status:
        raise IllegalTransitionError(f"{symbol!r} is not an enter leg of plan {plan.target_id}.")
    _require_status(plan, RebalancePlanStatus.ENTERING, f"retry enter leg {symbol!r}")
    used = plan.enter_retry_count.get(symbol, 0)
    if used >= MAX_ENTER_RETRIES:
        raise IllegalTransitionError(
            f"Cannot retry enter leg {symbol!r} of plan {plan.target_id}: "
            f"already used its bounded retry ({used}/{MAX_ENTER_RETRIES}) (R0 §6)."
        )
    plan.enter_retry_count[symbol] = used + 1
    plan.enter_status[symbol] = LegStatus.PENDING


def finalize_plan(plan: RebalancePlan) -> None:
    """ENTERING -> COMPLETE (every enter leg CONFIRMED) or PARTIAL (at
    least one terminally DECLINED/FAILED) - R0 §6: PARTIAL is a normal,
    expected, closed status, never a fault."""
    _require_status(plan, RebalancePlanStatus.ENTERING, "finalize")
    if not all_enter_legs_terminal(plan):
        pending = {s: st.value for s, st in plan.enter_status.items() if st not in TERMINAL_LEG_STATUSES}
        raise IllegalTransitionError(
            f"Cannot finalize plan {plan.target_id}: enter leg(s) not yet terminal: {pending}."
        )
    all_confirmed = all(status == LegStatus.CONFIRMED for status in plan.enter_status.values())
    plan.status = RebalancePlanStatus.COMPLETE if all_confirmed else RebalancePlanStatus.PARTIAL


def halt_plan(plan: RebalancePlan, reason: str, *, sidecar: Optional[PlanIncidentSidecar] = None) -> None:
    """Any state -> HALTED (R0 §4: "any state -> HALTED, integrity
    ambiguity, fail-closed per §2.5"). Unlike every other transition here,
    this one has no precondition on the current status - an integrity
    failure can be discovered at any point in the sequence, and must
    always be reachable.

    EA1-R1, 2026-08-19: `reason` used to be discarded entirely - the
    single defect the 2026-08-17/2026-08-19 incidents exposed most
    clearly (Terminal B's exact cause could never be proven, only
    inferred, because nothing here ever persisted it). `sidecar` is
    optional and defaults to None (every existing call site/test that
    doesn't pass one sees zero behavior change) - when supplied, the real
    reason is durably recorded via v34_bridge_plan_incident_sidecar,
    completely independent of whether the engine layer also happened to
    log something for the same incident."""
    plan.status = RebalancePlanStatus.HALTED
    if sidecar is not None:
        sidecar.log_halted(plan.target_id, reason=reason)
