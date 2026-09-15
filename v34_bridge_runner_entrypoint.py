"""V11 -> P02 Rebalance Bridge — the runner's always-on polling daemon.

This is the outer `while True` the runner's container actually runs. It
does two things, deliberately never in the same tick:

- If the shared plan directory (`RebalancePlanStore`) holds a plan that
  still needs driving, drive it one cycle (`drive_plan_one_cycle()`,
  v34_bridge_runner_core.py - already makes at most one quote-endpoint
  call internally, in whichever phase branch applies).
- Otherwise, call `engine.step()` directly for ongoing background
  position management - the actual operational reason R0 §13 chose a
  separate, always-on runner over a bridge-embedded loop: existing
  MANAGING positions keep getting watched even when no rebalance is in
  flight.

Why "never in the same tick" is load-bearing, not tidiness: Kite's quote/
LTP endpoint is capped at 1 request/second
(https://kite.trade/docs/connect/v3/exceptions/), enforced per API key
across ALL usage of that key, not scoped to this container. Both branches
above can make a quote-endpoint call. If a tick ever ran both - a
"background step() always, plus drive the plan if there is one" loop body
- two quote requests could land in the same wall-clock second regardless
of how long the tick sleeps afterward, since the sleep only paces time
*between* ticks, not calls *within* one. Structuring the two branches as
mutually exclusive (if/else, not if/if) makes that burst structurally
impossible rather than merely unlikely - the same "physics, not promises"
pattern as retry_enter_leg()'s structural retry cap.

With at most one quote call per tick, POLL_INTERVAL_SECONDS = 5 keeps
sustained worst-case load at 0.2 req/sec - 20% of the hard ceiling,
leaving headroom for jitter and any other tool sharing the same API key.

429 defense-in-depth, and why it is not optional: verified by reading
institutional_engine_v34_p02_multipos_candidate.py's
_is_transient_observation_exception - it treats only
kiteconnect.exceptions.NetworkException with code in {502, 503, 504} as
transient/retryable at P02's own layer. A 429 (confirmed via the Kite
Connect developer forum: a 429 raises exactly this NetworkException class
with code=429) is deliberately excluded there and re-raised uncaught
(test_harness_v34_b1_classification.py::test_b28b_kite_network_
exception_429 pins exactly this). Left unhandled, an ordinary rate-limit
bump would propagate straight out of engine.step()/drive_plan_one_cycle()
as an uncaught exception and crash this daemon outright - not a
theoretical edge case, the documented, expected shape of what Kite
returns once the quote ceiling is exceeded. run_forever() catches
exactly this one exception shape, logs it loudly, and backs off for
RATE_LIMIT_BACKOFF_SECONDS before resuming the standard cadence. Any
other exception is deliberately NOT swallowed here: P02's own genuine
halts (trigger_hard_halt()) mutate engine.state.status rather than raise,
so an exception reaching this far means something outside P02's own
fail-closed handling went wrong - crashing and letting the container's
restart policy bring the daemon back up (reloading all state from durable
storage) is the fail-closed behavior, not a bug to paper over with a
catch-all loop.

Not built this pass: the Dockerfile/container packaging (a separate
artifact - see the accompanying Dockerfile) and the bridge's own trigger
script (TargetPortfolio -> RebalanceDiff -> resolve_or_create_plan).

PHASE 3.6 FIX - HALTED-ENGINE GAP: traced and found while building the
production startup sequence (v34_bridge_runner_startup.py): this loop
never inspected `engine.terminator.halted` or `step()`'s own
`{"__engine__": "HALTED"}` return value at all. A halted engine (a real,
already-tested, correct outcome - trigger_hard_halt() sets `terminator.
halted` and returns cleanly, by design) was previously just called again
forever, uselessly - `run_one_iteration()` would keep invoking `engine.
step()`, which keeps returning `{"__engine__": "HALTED"}}` immediately
(institutional_engine_v34_p02_multipos_candidate.py's own step() guard),
and the loop would sleep and repeat that forever, never once telling the
container/process supervisor anything was wrong.

Fixed with `Terminator.raise_if_halted()` (v34_bridge_terminator.py,
Phase 3.4B) - built specifically, unwired, for exactly this call site:
"an explicit, deliberately-named helper for a future runner outer-loop
to call... so a halted engine can actually stop the daemon rather than
being called again forever, uselessly." Checked both BEFORE the very
first iteration (a persisted halt discovered at startup, e.g. via
v34_bridge_runner_startup.build_production_engine() returning an
already-halted engine) and AFTER every iteration (a fresh halt triggered
mid-tick) - deliberately not swallowed or logged-and-continued: matching
this file's own already-established "let it crash, the container
restart policy is the fail-closed behavior" posture for every other
uncaught exception here. A halted daemon that kept running would create
exactly the false appearance of safety this whole project's terminator
design exists to prevent - "termination must never create the appearance
that execution stopped safely when it did not" cuts both ways: a runner
that keeps LOOKING alive while doing nothing useful is its own version
of that same lie.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from v34_bridge_rebalance_plan import RebalancePlan, RebalancePlanStore
from v34_bridge_runner_core import drive_plan_one_cycle

POLL_INTERVAL_SECONDS = 5
RATE_LIMIT_BACKOFF_SECONDS = 60


class MultipleActivePlansError(RuntimeError):
    """More than one non-terminal RebalancePlan was found in the shared
    plan directory at once. R0 never specifies concurrent-plan handling,
    and driving two plans against the same broker portfolio at the same
    time is a genuine correctness risk (e.g. each computing capital
    availability from a snapshot that doesn't account for the other's
    in-flight legs) - not just untidy bookkeeping. Fail closed: surface
    this immediately rather than silently pick one and proceed."""


def _is_rate_limit_exception(exc: BaseException) -> bool:
    """True only for exactly the shape Kite documents for a 429: a
    kiteconnect.exceptions.NetworkException with code == 429. Duck-typed
    on __module__/__name__ (matching institutional_engine_v34_p02_
    multipos_candidate.py's own _is_transient_observation_exception, not
    a new convention) rather than importing kiteconnect directly, so this
    module stays importable even where the kiteconnect package isn't
    installed (e.g. a minimal test/CI image)."""
    cls = type(exc)
    module = getattr(cls, "__module__", "")
    if module.startswith("kiteconnect.exceptions") and cls.__name__ == "NetworkException":
        return getattr(exc, "code", None) == 429
    return False


def find_active_plan(store: RebalancePlanStore) -> Optional[RebalancePlan]:
    """The single plan (if any) this tick should drive. Raises
    MultipleActivePlansError rather than guessing if more than one is
    found - see that class's docstring."""
    active = store.list_active_plans()
    if len(active) > 1:
        ids = [p.target_id for p in active]
        raise MultipleActivePlansError(
            f"Found {len(active)} non-terminal plans at once: {ids}. "
            "Refusing to drive any of them until this is resolved by an operator."
        )
    return active[0] if active else None


def run_one_iteration(
    *, engine, store: RebalancePlanStore, on_event: Callable[[str], None] = lambda msg: None,
    sidecar=None,
) -> None:
    """Exactly one tick. Structurally mutually exclusive - see module
    docstring for why this specific if/else (not if/if) shape is the
    point, not a style choice.

    `sidecar` (EA1-R1, 2026-08-19): optional PlanIncidentSidecar, defaults
    to None - zero effect on any existing caller/test. Threaded straight
    through to drive_plan_one_cycle()."""
    plan = find_active_plan(store)
    if plan is not None:
        new_status = drive_plan_one_cycle(plan=plan, engine=engine, store=store, sidecar=sidecar)
        on_event(f"drove plan {plan.target_id}: {new_status}")
    else:
        engine.step()
        on_event("background step(): no active plan")


def run_forever(
    *,
    engine,
    store: RebalancePlanStore,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    rate_limit_backoff: float = RATE_LIMIT_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_event: Callable[[str], None] = lambda msg: None,
    max_iterations: Optional[int] = None,
    sidecar=None,
) -> None:
    """The daemon's actual loop. `sleep_fn` and `max_iterations` exist
    purely so tests can drive this deterministically, with no real clock
    and no unbounded loop - production never passes `max_iterations` and
    the loop runs until the process is stopped.

    Never stops on its own once a plan reaches a terminal status - that
    is by design: the whole point of a separate, always-on runner (R0
    §13) is that it keeps calling engine.step() for background position
    management indefinitely, not just for the duration of one rebalance.

    DOES stop - by raising `EngineHaltedError` (v34_bridge_terminator.py),
    never silently - the moment `engine.terminator.halted` is true, both
    before the first iteration (a halt already present at startup) and
    after every subsequent one (a halt triggered mid-tick). See module
    docstring's Phase 3.6 fix note for why this is not optional.
    """
    engine.terminator.raise_if_halted()
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        try:
            run_one_iteration(engine=engine, store=store, on_event=on_event, sidecar=sidecar)
        except Exception as exc:
            if _is_rate_limit_exception(exc):
                on_event(
                    f"RATE_LIMITED ({exc}): backing off {rate_limit_backoff}s before resuming "
                    f"the standard {poll_interval}s cadence."
                )
                sleep_fn(rate_limit_backoff)
                iterations += 1
                continue
            raise
        if engine.terminator.halted:
            on_event(f"HALTED ({engine.terminator.reason}): stopping run_forever(), not polling a halted engine.")
            engine.terminator.raise_if_halted()
        iterations += 1
        sleep_fn(poll_interval)
