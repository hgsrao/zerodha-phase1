"""V11 -> P02 Rebalance Bridge — Step B: the runner's orchestration core.

Implements the refined division of labor this session's IPC decision
forced (see the approved Step B plan): only the process holding the real
`TradingEngineV34P02` instance can call `request_entry()`/`request_exit()`
- that process is the runner, not the bridge. `drive_plan_one_cycle()` is
that runner's per-cycle decision logic: given a RebalancePlan and a live
P02 engine, submit whatever legs are ready, observe outcomes, advance the
plan's phase via the already-built guarded transitions
(`v34_bridge_rebalance_transitions.py`), and persist after every mutation.

Frozen rules this module enforces, not invents:
- Exit-before-entry, no interleaving (R0 §5) - structurally guaranteed by
  the guarded transitions this module calls, not by this module's own
  discipline.
- Enter legs submitted strictly one at a time (R0 §5) - never more than
  one PENDING enter leg is ever acted on in a single cycle.
- On any P02 `RECONCILIATION_HALT` observed during either phase: halt the
  whole plan immediately. No dynamic resizing, no partial quarantine of
  "still affordable" legs - this was frozen as this session's own answer
  to the capital-shortfall question, grounded in the fact that P02's real
  `_step_exit_pending` converts every non-clean exit outcome directly into
  `trigger_hard_halt()` (verified by reading the code, not assumed).
- A synchronous `request_entry()`/`request_exit()` rejection (R0 §11's
  "synchronous rejection at call time" channel: SYMBOL_ALREADY_HELD,
  SIMULTANEOUS_POSITION_LIMIT, market-closed, malformed params) is a
  clean, expected decline - recorded as DECLINED only after R0 §6's one
  bounded retry is spent, never a halt.
- R0 §6's bounded-retry-once policy, for both decline channels (the
  synchronous `request_entry()` raise and the asynchronous vanish-from-
  active_trades observation): on a leg's first decline, retry exactly
  once via `retry_enter_leg()` (structural cap - see
  `v34_bridge_rebalance_transitions.py`); on a second decline for the same
  leg, terminally DECLINE. The retry count is durable
  (`RebalancePlan.enter_retry_count`), so a crash between "decided to
  retry" and "resubmission resolved" can never cause a resumed runner to
  forget the retry already happened and issue a second one.

Known gap, flagged rather than silently patched: R0 §6 says a declined
leg should be recorded "with P02's exact reason," but R0 §4's frozen
`RebalancePlan` dataclass has no per-leg reason field. This module does
not add one (amending a frozen dataclass shape mid-implementation is not
this pass's call to make) - reasons are only as available as P02's own
audit log, not carried on the plan itself. Worth a deliberate decision
later, not resolved here.

Human-in-the-loop approval gate (this session's Phase-1 default: gated,
not fully automatic - see the regular-vs-managed design discussion):
CREATED is now a deliberate, passive no-op - a plan sits exactly as the
trigger wrote it, visible, until a human runs the approval CLI
(v34_bridge_approve_plan.py) and moves it to APPROVED
(v34_bridge_rebalance_transitions.approve_plan()). Only APPROVED drives
exit submission (_drive_approved(), below - what _drive_created() used
to do before this gate existed). No EXIT leg can reach request_exit()
without that human action having happened first; this module enforces
that by construction (begin_exiting() itself requires APPROVED), not by
this dispatch function remembering to check anything.

Not built this pass: the Docker entrypoint/poll loop, and the bridge's
own trigger script.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Optional

from institutional_engine_v34_p02_multipos_candidate import TradingEngineV34P02
from v34_bridge_plan_incident_sidecar import PlanIncidentSidecar
from v34_bridge_resolve_entry_submit_halt import run_resolve
from v34_bridge_rebalance_plan import (
    NO_FURTHER_ACTION_PLAN_STATUSES,
    LegStatus,
    RebalancePlan,
    RebalancePlanStatus,
    RebalancePlanStore,
)
from v34_bridge_rebalance_transitions import (
    IllegalTransitionError,
    advance_to_exits_complete,
    all_enter_legs_terminal,
    all_exit_legs_terminal,
    begin_entering,
    begin_exiting,
    finalize_plan,
    halt_plan,
    mark_enter_leg,
    mark_exit_leg,
    retry_enter_leg,
)
from v34_p02_state import EngineStatus, PositionStatus


def _fetch_live_price(engine: TradingEngineV34P02, symbol: str) -> Decimal:
    quotes = engine.broker.ltp([symbol])
    quote = quotes.get(f"NSE:{symbol}") or quotes.get(symbol)
    if not isinstance(quote, dict) or quote.get("last_price") is None:
        raise RuntimeError(f"no live quote available for {symbol}")
    return Decimal(str(quote["last_price"]))


def _is_safe_to_degrade_quote_exception(exc: Exception) -> bool:
    """EA1-R1, 2026-08-19 - built after the 2026-08-17 Terminal B incident:
    a bare ltp() failure inside _fetch_live_price() (a pure READ, before
    request_entry() has ever been called for this leg - nothing has been
    mutated, nothing has crossed the submission boundary) used to
    unconditionally halt_plan() the entire plan on ANY exception, no
    matter how transient, with zero audit trail (halt_plan()'s own
    `reason` argument is discarded - see v34_bridge_rebalance_transitions.
    halt_plan()'s docstring). This function is deliberately narrow: it
    answers "is this a pure-read failure with zero possibility of having
    mutated anything," never "should we retry a submission we're not sure
    went through" - that is a completely different, still-conservative
    question (see the ENTRY_SUBMIT/429 auto-recovery logic elsewhere in
    this project, which stays deliberately human-gated for anything less
    than provably safe).

    Reuses the frozen engine's OWN classifier
    (TradingEngineV34P02._is_transient_observation_exception) rather than
    reimplementing it - covers TimeoutError/ConnectionError/ReadTimeout/
    ConnectTimeout and a Kite NetworkException with code in {502,503,504}.
    Extended with ONE more case, specific to this read-only context: a
    Kite NetworkException with code==429 (rate-limited). The frozen
    engine deliberately excludes 429 from ITS OWN transient set for
    SUBMISSION-phase exceptions, because during submission a 429 needs
    the stronger "prove zero live order exists" verification before
    anything is presumed safe (see resolve_entry_submit_halt.py). No such
    ambiguity exists here: this call is a pure quote read, made BEFORE
    request_entry() is ever invoked for this leg - a 429 here means
    nothing more than "try again shortly," identical in kind to the
    already-transient 503/504 cases, not a submission outcome of any
    sort. Treating it any more cautiously than a 503 would be arbitrary,
    not extra safety."""
    if TradingEngineV34P02._is_transient_observation_exception(exc):
        return True
    try:
        from kiteconnect.exceptions import NetworkException
    except ImportError:
        return False
    return isinstance(exc, NetworkException) and getattr(exc, "code", None) == 429


def _drive_created(plan: RebalancePlan, engine: TradingEngineV34P02, store: RebalancePlanStore) -> None:
    """CREATED is a passive, visible waiting state - the human-in-the-loop
    approval gate. Nothing is submitted to P02/the broker from here; the
    plan just sits exactly as the trigger wrote it until a human runs the
    approval CLI and moves it to APPROVED (v34_bridge_rebalance_
    transitions.approve_plan()). This function intentionally does
    nothing - it exists as an explicit, named branch in
    drive_plan_one_cycle()'s dispatch rather than an accidental fallthrough,
    so "waiting for approval" is a deliberate behavior, not a gap."""
    return


def _drive_approved(plan: RebalancePlan, engine: TradingEngineV34P02, store: RebalancePlanStore,
                     *, sidecar: Optional[PlanIncidentSidecar] = None) -> None:
    begin_exiting(plan)
    for symbol, status in list(plan.exit_status.items()):
        if status != LegStatus.PENDING:
            continue
        try:
            engine.request_exit(symbol=symbol)
        except Exception as exc:
            # request_exit() is never gated by policy (R0 §2/§5) - a failure here
            # is an integrity problem (e.g. the symbol isn't actually MANAGING),
            # not a clean decline. Halt the whole plan.
            halt_plan(plan, reason=f"{symbol}: request_exit() failed: {exc}", sidecar=sidecar)
            store.save(plan)
            return
        mark_exit_leg(plan, symbol, LegStatus.SUBMITTED)
    store.save(plan)


def _drive_exiting(plan: RebalancePlan, engine: TradingEngineV34P02, store: RebalancePlanStore,
                    *, sidecar: Optional[PlanIncidentSidecar] = None) -> None:
    engine.step()
    if engine.state.status == EngineStatus.RECONCILIATION_HALT:
        halt_plan(plan, reason=engine.state.halt_reason or "P02 engine halted during EXITING", sidecar=sidecar)
        store.save(plan)
        return
    for symbol, status in list(plan.exit_status.items()):
        if status == LegStatus.SUBMITTED and symbol not in engine.state.active_trades:
            mark_exit_leg(plan, symbol, LegStatus.CONFIRMED)
    if all_exit_legs_terminal(plan):
        advance_to_exits_complete(plan)
    store.save(plan)


def _drive_exits_complete(plan: RebalancePlan, engine: TradingEngineV34P02, store: RebalancePlanStore) -> None:
    # R0 §5 step 3's "recompute available capital from broker cash reality"
    # happens implicitly: the authorizer inside engine.request_entry()/step()
    # always evaluates a fresh snapshot at submission time (P02 spec §9) -
    # no separate recomputation is needed here before opening the ENTERING
    # phase itself; the real capital check happens per-leg, at submission.
    begin_entering(plan)
    store.save(plan)


def _try_auto_resolve_entry_submit_halt(engine: TradingEngineV34P02) -> bool:
    """EA1-R1, 2026-08-19 - built after Terminal A's real HARD_HALT
    incidents (SUNPHARMA 2026-08-17, SBIN 2026-08-19), both a genuine Kite
    429 during actual order submission, both requiring the owner to run
    v34_bridge_resolve_entry_submit_halt.py by hand before the plan could
    ever move again.

    Reuses that script's own run_resolve() UNCHANGED - not a
    reimplementation, not a softened version, and not gated on "was this
    specifically a 429": run_resolve()'s own defense-in-depth (halt_source
    must be exactly ENTRY_SUBMIT; the position's status must be
    ENTRY_SUBMITTING/ENTRY_UNKNOWN; entry_order_id must be empty; a FRESH
    real broker.get_orders() call must independently prove zero match)
    already generalizes safely to the whole ENTRY_SUBMIT halt class, per
    that script's own docstring - not just 429 specifically. This
    function's only job is deciding WHEN to attempt that already-proven-
    safe check automatically instead of waiting for a human to run it,
    and refusing (falling back to the existing halt_plan() path,
    unchanged) the instant there is more than one plausible candidate to
    resolve - an ambiguous multi-position halt stays exactly as
    conservative/human-gated as it was before this function existed.

    Returns True only if run_resolve() itself actually cleared the halt
    (rc == 0) - any refusal, for any reason, returns False and changes
    nothing."""
    if engine.state.halt_source != "ENTRY_SUBMIT":
        return False
    candidates = [
        symbol for symbol, ctx in engine.state.active_trades.items()
        if ctx.status in (PositionStatus.ENTRY_SUBMITTING, PositionStatus.ENTRY_UNKNOWN) and not ctx.entry_order_id
    ]
    if len(candidates) != 1:
        # Zero: nothing this function can act on. More than one: a
        # multi-position halt is a genuinely different, less-clearly-safe
        # situation - deliberately left for a human, not auto-resolved.
        return False
    symbol = candidates[0]
    out, err = io.StringIO(), io.StringIO()
    note = (
        f"AUTO-RESOLVED by v34_bridge_runner_core (EA1-R1): halt_source=ENTRY_SUBMIT, "
        f"halt_reason={engine.state.halt_reason!r}. Same verified-safe check "
        "resolve_entry_submit_halt.py's own operator path performs (fresh real "
        "broker.get_orders() call, zero match required), run automatically."
    )
    rc = run_resolve(engine, symbol=symbol, note=note, assume_yes=True, out=out, err=err)
    if rc == 0:
        engine.audit.log(
            "AUTO_RESOLVED_ENTRY_SUBMIT_HALT", symbol=symbol,
            halt_reason=engine.state.halt_reason, resolve_output=out.getvalue(),
        )
        return True
    engine.audit.log(
        "AUTO_RESOLVE_ENTRY_SUBMIT_HALT_REFUSED", symbol=symbol,
        halt_reason=engine.state.halt_reason, resolve_refusal=err.getvalue(),
    )
    return False


def _drive_entering(plan: RebalancePlan, engine: TradingEngineV34P02, store: RebalancePlanStore,
                     *, sidecar: Optional[PlanIncidentSidecar] = None) -> None:
    pending = [symbol for symbol, status in plan.enter_status.items() if status == LegStatus.PENDING]
    if pending:
        # Strictly one at a time (R0 §5) - never submit a second enter leg
        # in the same cycle as the first, even if more are PENDING.
        symbol = pending[0]
        quantity = plan.diff.enters[symbol]
        try:
            price = _fetch_live_price(engine, symbol)
        except Exception as exc:
            if _is_safe_to_degrade_quote_exception(exc):
                # EA1-R1: a pure-read, pre-mutation failure that's either
                # the frozen engine's own already-tested transient class
                # or a 429 (rate-limited, not ambiguous for a read) - skip
                # this leg THIS cycle only, leave it PENDING (the ordinary
                # poll cadence retries it), and - closing the exact
                # "Terminal B's cause was unrecoverable" defect - make it
                # visible for the first time via the same audit sink
                # every other decision in this project already goes
                # through, instead of the old silent halt_plan(reason=...)
                # whose reason was discarded anyway.
                engine.audit.log(
                    "QUOTE_FETCH_DEGRADED", symbol=symbol,
                    exception_class=type(exc).__name__, exception_message=str(exc),
                )
                return
            # Anything else is genuinely unrecognized for a pre-submission
            # read failure - fail closed exactly as before, not softened.
            halt_plan(plan, reason=f"{symbol}: could not fetch a live quote to submit entry: {exc}", sidecar=sidecar)
            store.save(plan)
            return
        try:
            engine.request_entry(symbol=symbol, quantity=quantity, price=price)
        except Exception as exc:
            # Synchronous decline at request_entry() call time (R0 §11):
            # SYMBOL_ALREADY_HELD, SIMULTANEOUS_POSITION_LIMIT, market-closed,
            # malformed params - a clean, expected decline. R0 §6: retry
            # once (fresh price, fresh engine snapshot, next cycle) before
            # terminally declining. retry_enter_leg() itself refuses once
            # the retry is already spent, so this is a structural cap, not
            # a counter this function has to police.
            #
            # FOUND 2026-08-16, from a real live run: the frozen authorizer's
            # own decline path (v34_p02_authorizer._decline()) is a pure
            # function with no audit side effect - a synchronous decline was
            # completely invisible in the audit trail, unlike the
            # asynchronous channel's own ENTRY_ABANDONED_POLICY_HALT record.
            # engine.audit is a real, stable, already-public attribute of the
            # frozen engine (read-only use here, no frozen file touched) -
            # logging the caught exception's own message closes that gap
            # without changing any decision this function already makes.
            engine.audit.log(
                "ENTRY_SYNC_DECLINED", symbol=symbol, reason=str(exc),
                already_retried=(plan.enter_retry_count.get(symbol, 0) > 0),
            )
            try:
                retry_enter_leg(plan, symbol)
            except IllegalTransitionError:
                mark_enter_leg(plan, symbol, LegStatus.DECLINED)
                # A terminal decline here may have been the last outstanding
                # leg - check in the same cycle rather than leaving the plan
                # stuck in ENTERING for one extra, otherwise-idle cycle
                # (the SUBMITTED-observation branch below already does this
                # same check; this mirrors it for the synchronous path).
                if all_enter_legs_terminal(plan):
                    finalize_plan(plan)
            store.save(plan)
            return
        mark_enter_leg(plan, symbol, LegStatus.SUBMITTED)
        store.save(plan)
        return

    # No PENDING legs left to submit this cycle - observe outcomes of
    # whatever is already SUBMITTED.
    engine.step()
    if engine.state.status == EngineStatus.RECONCILIATION_HALT:
        if _try_auto_resolve_entry_submit_halt(engine):
            # Cleared - deliberately does NOT drive anything further
            # itself (mirrors v34_bridge_resolve_entry_submit_halt.py's
            # own "not run by this script" precedent). engine.state.status
            # is now STARTUP, not RUNNING - but step() ITSELF already
            # transitions STARTUP -> reconcile_startup() -> RUNNING on its
            # own very next call (institutional_engine_v34_p02_multipos_
            # candidate.py's step(), traced - no process restart needed),
            # so simply returning here and letting the ordinary next poll
            # cycle continue is sufficient; the plan is untouched (still
            # ENTERING), so its own vanished-leg retry-once logic below
            # will pick this symbol up completely normally next cycle.
            return
        halt_plan(plan, reason=engine.state.halt_reason or "P02 engine halted during ENTERING", sidecar=sidecar)
        store.save(plan)
        return
    for symbol, status in list(plan.enter_status.items()):
        if status != LegStatus.SUBMITTED:
            continue
        ctx = engine.state.active_trades.get(symbol)
        if ctx is None:
            # Vanished from active_trades without ever reaching MANAGING -
            # either an asynchronous ENTRY_LOCK abandonment
            # (ENTRY_ABANDONED_POLICY_HALT) or a dead-order-no-fill terminal
            # rejection (R0 §11's "asynchronous channel": the caller can't
            # know synchronously, only by observing reconciled reality).
            # Either way this is a clean, resolved decline - resolved, not
            # necessarily final: R0 §6 gets one retry here too, same
            # structural cap as the synchronous channel above.
            try:
                retry_enter_leg(plan, symbol)
            except IllegalTransitionError:
                mark_enter_leg(plan, symbol, LegStatus.DECLINED)
        elif ctx.status == PositionStatus.MANAGING:
            mark_enter_leg(plan, symbol, LegStatus.CONFIRMED)
        # else: still resolving (ENTRY_PENDING/PARTIAL_POSITION/PROTECTION/
        # PROTECTION_PENDING) - leave SUBMITTED, observe again next cycle.
    if all_enter_legs_terminal(plan):
        finalize_plan(plan)
    store.save(plan)


def drive_plan_one_cycle(*, plan: RebalancePlan, engine: TradingEngineV34P02, store: RebalancePlanStore,
                          sidecar: Optional[PlanIncidentSidecar] = None) -> str:
    """Advance `plan` by exactly one orchestration cycle. Returns the
    plan's status (as a string) after whatever happened this cycle - the
    runner's outer poll loop calls this repeatedly until it observes a
    terminal status.

    `sidecar` (EA1-R1, 2026-08-19): optional, defaults to None - zero
    effect on any existing caller/test. When supplied, threaded to every
    halt_plan() call this cycle might make, so the real reason is
    durably persisted - see v34_bridge_plan_incident_sidecar.py."""
    if plan.status in NO_FURTHER_ACTION_PLAN_STATUSES:
        return plan.status.value

    if plan.status == RebalancePlanStatus.CREATED:
        _drive_created(plan, engine, store)
    elif plan.status == RebalancePlanStatus.APPROVED:
        _drive_approved(plan, engine, store, sidecar=sidecar)
    elif plan.status == RebalancePlanStatus.EXITING:
        _drive_exiting(plan, engine, store, sidecar=sidecar)
    elif plan.status == RebalancePlanStatus.EXITS_COMPLETE:
        _drive_exits_complete(plan, engine, store)
    elif plan.status == RebalancePlanStatus.ENTERING:
        _drive_entering(plan, engine, store, sidecar=sidecar)
    else:
        halt_plan(plan, reason=f"drive_plan_one_cycle: unrecognized plan status {plan.status.value!r}.", sidecar=sidecar)
        store.save(plan)

    return plan.status.value
