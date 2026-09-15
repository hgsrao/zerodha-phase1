"""V11 -> P02 Rebalance Bridge — operator halt-clearance CLI.

Built 2026-08-17, the first time this project has ever needed it for
real: a genuine CRITICAL P0 HARD_HALT fired on Terminal A (source
ENTRY_SUBMIT, "Too many requests" from Kite while submitting SUNPHARMA's
entry). Nothing in the bridge previously wired the frozen engine's own
`clear_halt_and_reconcile(operator_note)` method (institutional_engine_
v34_p02_multipos_candidate.py) to anything runnable — plan-approval has
v34_bridge_approve_plan.py; halt-clearance had no equivalent. This fills
exactly that gap, reusing the frozen engine's own method rather than
reimplementing any clearing logic.

Deliberately does ONLY ONE THING and stops: clears the durable
`clearance_required` latch (after the frozen method's own real,
read-only broker-reachability check) and persists status back to
STARTUP. It does NOT call engine.step(), does NOT re-run reservation/
daily-accounting reconciliation, and does NOT call run_forever() itself.

Why stop there, deliberately, rather than trying to finish the resume in
this same script: build_production_engine() (v34_bridge_runner_startup.py)
already contains the complete, already-tested steps 7-13 sequence (fresh
broker observations, reservation reconciliation, engine.step() while
status==STARTUP, daily-accounting rollover) for exactly this situation —
but it only runs that sequence when engine.terminator.halted is False
right after construction. Since clear_halt_and_reconcile() persists the
cleared state to disk (not just this process's memory), the clean way to
get that full sequence is to let it happen exactly where it already
lives: a fresh `python v34_bridge_runner_main.py` invocation, which will
now load the cleared state, NOT self-halt on load, and run the complete
proven sequence itself. Re-implementing any slice of that here would
risk silently drifting from the one already-tested path. Mirrors
v34_bridge_approve_plan.py's own precedent of staying a narrow,
single-purpose gate rather than reaching into the runner daemon's job.

Requires a non-empty --note (an operator attestation string — exactly
what clear_halt_and_reconcile() itself refuses to run without) and real
Kite credentials via the environment, via the SAME _build_engine() wiring
v34_bridge_runner_main.py uses (same DP-charge parsing, same shadow-mode
flag, same credential loading) — this script can never construct a
differently-configured engine than the real runner would.

If the loaded engine is not actually halted, or is halted for a reason
OTHER than clearance_required (clear_halt_and_reconcile() silently
no-ops and returns True when clearance_required is already False — see
its own docstring/tests), this script refuses to proceed rather than
risk a false "cleared" report.

LOCK SAFETY: engine construction acquires the real OS runner lock (same
one the runner daemon uses). Released in a finally block on every exit
path, so a subsequent `python v34_bridge_runner_main.py` in another
window is never wrongly refused with RunnerLockHeldError because this
script forgot to let go.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional, TextIO


def run_clear_halt(engine, *, note: str, assume_yes: bool, out: TextIO, err: TextIO,
                    input_fn=input) -> int:
    """Pure-ish core: takes an already-constructed engine (from
    _build_engine() in production, or a fake/isolated one in tests) so
    this logic is exercised identically either way. Does not release the
    lock — that stays the caller's job (matches build_production_engine()/
    v34_bridge_runner_main.py's own established lock-ownership split)."""
    if not engine.terminator.halted:
        print("Engine is not halted - nothing to clear.", file=out)
        return 0

    print(f"Engine is halted. reason={engine.terminator.reason!r}", file=out)
    print(
        f"Persisted state: status={engine.state.status.value} "
        f"clearance_required={engine.state.clearance_required} "
        f"halt_reason={engine.state.halt_reason!r}",
        file=out,
    )

    if not engine.state.clearance_required:
        print(
            "clearance_required is False - clear_halt_and_reconcile() would "
            "silently no-op here without actually resetting this halt "
            "(status is RECONCILIATION_HALT for some other reason). Refusing "
            "to proceed automatically; this needs manual investigation, not "
            "this script.",
            file=err,
        )
        return 1

    if not assume_yes:
        answer = input_fn(f"Clear this halt with operator note {note!r}? [y/N] ").strip().lower()
        if answer != "y":
            print("Not cleared.", file=out)
            return 1

    cleared = engine.clear_halt_and_reconcile(note)
    if not cleared:
        print(
            "Refused - see audit.jsonl (OPERATOR_CLEARANCE_REJECTED) for the "
            "exact reason (empty note, or the broker-reachability check "
            "itself failed).",
            file=err,
        )
        return 1

    print("Cleared. Durable state now: status=STARTUP, clearance_required=False.", file=out)
    print("This does NOT resume trading by itself - it only unlatches the halt.", file=out)
    print("Next step (separate, deliberate action - not run by this script):", file=out)
    print("  python v34_bridge_runner_main.py", file=out)
    print(
        "That startup sequence re-verifies everything against real broker "
        "state on its own (the already-tested steps 7-13 path) before "
        "resuming - if a real inconsistency remains (e.g. the SUNPHARMA "
        "position genuinely can't be reconciled), it will halt again there, "
        "safely, exactly as before.",
        file=out,
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clear a durable RECONCILIATION_HALT/clearance_required halt on a V11 bridge runner (operator gate)."
    )
    parser.add_argument("--note", required=True, help="Operator attestation note - required, non-empty.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt (non-interactive use only).")
    args = parser.parse_args(argv)

    if not args.note or not args.note.strip():
        print("Refused: --note must be non-empty.", file=sys.stderr)
        return 1

    from v34_bridge_runner_main import _build_engine

    engine = _build_engine(on_event=print)
    try:
        return run_clear_halt(engine, note=args.note, assume_yes=args.yes, out=sys.stdout, err=sys.stderr)
    finally:
        engine.lock_provider.release()


if __name__ == "__main__":
    sys.exit(main())
