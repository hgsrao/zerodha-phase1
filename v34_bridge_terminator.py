"""Phase 3.4B — terminator.

New module. Traced every terminator.* usage in the frozen engine (not
sampled) before writing this:

- `.halted` is read constantly - __init__, request_entry(), request_exit(),
  step(), and three _observe()-adjacent branches - always as a plain
  boolean gate that short-circuits to a "HALTED"/"NO_ACTION" return
  value. It never itself causes the engine to raise or exit anything.
- `.halt(reason)` is called from exactly two places: __init__ (on finding
  an already-halted persisted BotState) and trigger_hard_halt(). Both
  MUST see it return normally - trigger_hard_halt()'s own body has
  nothing after its terminator.halt(reason) call, and every caller up
  the chain (_step_entry_submit and its siblings) depends on
  trigger_hard_halt() itself returning cleanly so THEY can return their
  own "HALTED" string. If halt() raised, that whole chain of clean
  returns would break, and the exception would eventually be re-caught
  by step()'s own per-symbol handler and turned into a SECOND
  trigger_hard_halt() call - exactly the kind of recursive-halt loop
  audit.log()/alert.send() are already carefully shielded from at that
  same call site. halt() raising would reintroduce that risk somewhere
  the frozen engine never expected it.
- clear_halt_and_reconcile() sets `.halted = False` / `.reason = None` as
  PLAIN ATTRIBUTE ASSIGNMENTS (guarded by hasattr, defensively tolerant
  of a terminator that lacks them) - confirming these must be ordinary
  mutable fields a caller can reset directly, not read-only properties
  or something reset only via a method.

CONCLUSION: Terminator.halt() must set .halted/.reason and return
normally - never raise, never exit the process, never touch a broker or
the filesystem. This is deliberately a thin, purely in-memory, per-
process object, not a durable store: the durable "should this resume
automatically" signal is BotState.status/clearance_required (already
sealed in v34_bridge_botstate_store.py, Phase 3.2) - Terminator is
correctly re-derived fresh from durable BotState at every new engine
construction, not something that needs its own persistence. Building it
as a store would be over-engineering past what the trace supports.

raise_if_halted() is the one thing this module adds beyond the frozen
engine's own narrow contract: an explicit, deliberately-named helper for
the (not yet built) runner's own outer loop to call after each cycle,
so a halted engine can actually stop the daemon rather than being
called again forever, uselessly - a real gap found while tracing this
(v34_bridge_runner_entrypoint.run_forever() currently never inspects
terminator.halted or step()'s "__engine__": "HALTED" return value at
all). P02 itself never calls raise_if_halted() - only Phase 3.6's future
wiring would. Building it now, unwired, is exactly "the primitive Phase
3.6 will wire together," not solving the integration gap itself.

Idempotent on repeated halt() calls, matching how trigger_hard_halt()
itself already treats a repeat: BotState.halt_reason is unconditionally
overwritten by the frozen engine's own code, not merged or protected -
this module mirrors that exact policy rather than inventing a different
one (e.g. "first reason wins"), so the two stay consistent if halt() is
ever called twice in one process lifetime.
"""

from __future__ import annotations

from typing import Optional


class EngineHaltedError(RuntimeError):
    """Raised only by raise_if_halted() - never by halt() itself, and
    never by the frozen engine. A deliberate, explicit signal for a
    caller (the runner's own outer loop) that has chosen to stop on a
    halt, not something P02's own control flow depends on."""


class Terminator:
    """Thin, in-memory, per-process halt flag. See module docstring for
    why .halt() must never raise, and why this object is deliberately
    NOT a durable store."""

    def __init__(self) -> None:
        self.halted: bool = False
        self.reason: Optional[str] = None

    def halt(self, reason: str) -> None:
        """Never raises. Idempotent: a second call overwrites .reason,
        matching trigger_hard_halt()'s own unconditional-overwrite policy
        on BotState.halt_reason."""
        self.halted = True
        self.reason = reason

    def raise_if_halted(self) -> None:
        """Not called anywhere in the frozen engine. For a runner's own
        outer loop (Phase 3.6, not built here) to call deliberately after
        each cycle, so a halt actually stops the daemon instead of being
        silently polled forever."""
        if self.halted:
            raise EngineHaltedError(self.reason or "Engine is halted (no reason recorded).")
