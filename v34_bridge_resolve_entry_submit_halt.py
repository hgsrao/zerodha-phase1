"""V11 -> P02 Rebalance Bridge — operator CLI to resolve a stuck
ENTRY_SUBMIT halt caused by a PROVEN-never-submitted order.

Built 2026-08-17, directly in response to what v34_bridge_clear_halt.py's
own test suite found: clearing a halt caused by a "known outcome" entry-
submission failure (institutional_engine_v34_p02_multipos_candidate.py's
_step_entry_submit(), the trigger_hard_halt(source="ENTRY_SUBMIT") branch)
does NOT actually fix anything on its own - the stuck position (still
sitting in active_trades, never given an order_id) gets re-checked against
real broker orders every ~5s poll, will NEVER find a match (there is
nothing to find), and re-halts on retry-budget exhaustion ~15s later.
Fixing this for real requires removing the stale position itself, not
just the halt latch.

WHY THIS IS SAFE TO DO PROGRAMMATICALLY, not just plausible: traced from
the engine's OWN classification, not a new judgment call layered on top
of it. _step_entry_submit()'s except-block only reaches trigger_hard_halt
(source="ENTRY_SUBMIT") when _is_transient_submission_exception(exc) is
False - and that function delegates to _is_transient_observation_exception,
which explicitly excludes HTTP 429 (kiteconnect.exceptions.NetworkException
with code=429) from its transient set ({502, 503, 504} only). This is a
deliberate, already-existing design decision (also documented in
v34_bridge_runner_core.py's own 429-handling comment): a 429 during
submission is treated as a KNOWN, definite, pre-acceptance rejection - not
an ambiguous "might have gone through" case (that's ENTRY_UNKNOWN via the
transient path, a genuinely different, softer classification this script
deliberately refuses to touch - see the entry_order_id/fingerprint checks
below). This script does not invent a new safety judgment; it completes
the one the engine's own exception classification already made.

DEFENSE IN DEPTH - every one of these must hold, or this script refuses
and touches nothing:
  1. Engine must be halted, specifically with halt_source == "ENTRY_SUBMIT"
     (this script's own narrow scope - refuses any other halt class).
  2. --symbol must name a position actually present in active_trades.
  3. That position's status must be ENTRY_SUBMITTING or ENTRY_UNKNOWN -
     the only statuses this classification concerns.
  4. entry_order_id must be EMPTY - if an order_id was ever assigned, a
     real order may exist; this script refuses outright and defers to
     ordinary reconciliation instead of guessing.
  5. entry_submission_fingerprint must be present (needed for step 6).
  6. A FRESH, REAL, read-only broker.get_orders() call is made right now
     (never trusted from the stale halt record) and independently
     re-checked against the position's fingerprint using the SAME frozen,
     pure order_matches_entry_fingerprint() the engine's own reconciliation
     logic uses (v34_p02_state.py) - not a reimplementation that could
     silently disagree with it. If reality has changed since the halt and
     a live matching order now exists, this script refuses loudly and
     leaves everything untouched rather than deleting real state.

Only if all six hold: removes the position from active_trades (audited,
with the note and every fact checked, durably logged), then reuses
v34_bridge_clear_halt.py's own tested clear_halt_and_reconcile() call to
also clear the halt latch in the same action - the two are one atomic
operator decision here, not two separate scripts to run in sequence,
specifically because leaving them split created exactly the every-poll
re-halt loop this script exists to close.

Same lock-release discipline as v34_bridge_clear_halt.py.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional, TextIO

from v34_p02_state import PositionStatus, order_matches_entry_fingerprint


def run_resolve(engine, *, symbol: str, note: str, assume_yes: bool, out: TextIO, err: TextIO,
                 input_fn=input) -> int:
    if not engine.terminator.halted:
        print("Engine is not halted - nothing to resolve.", file=out)
        return 0

    if engine.state.halt_source != "ENTRY_SUBMIT":
        print(
            f"Refusing: halt_source={engine.state.halt_source!r}, not 'ENTRY_SUBMIT'. "
            "This script only handles a stuck entry-submission halt - use "
            "v34_bridge_clear_halt.py (or manual review) for any other halt class.",
            file=err,
        )
        return 1

    ctx = engine.state.active_trades.get(symbol)
    if ctx is None:
        print(f"Refusing: no active position for symbol {symbol!r} - nothing to resolve.", file=err)
        return 1

    if ctx.status not in (PositionStatus.ENTRY_SUBMITTING, PositionStatus.ENTRY_UNKNOWN):
        print(
            f"Refusing: {symbol} status is {ctx.status.value!r}, not ENTRY_SUBMITTING/ENTRY_UNKNOWN. "
            "This script only handles that specific stuck-entry class.",
            file=err,
        )
        return 1

    if ctx.entry_order_id:
        print(
            f"Refusing: {symbol} already has entry_order_id={ctx.entry_order_id!r} - a real order may "
            "exist. This script only ever touches entries PROVEN to have no order_id at all. This needs "
            "manual review, not this script.",
            file=err,
        )
        return 1

    if not ctx.entry_submission_fingerprint:
        print(f"Refusing: {symbol} has no entry_submission_fingerprint recorded - cannot independently verify.", file=err)
        return 1

    print(f"Target: {symbol} status={ctx.status.value} qty={ctx.target_qty} price={ctx.entry_price} tag={ctx.entry_tag}", file=out)
    print(f"Halt reason on record: {engine.state.halt_reason!r}", file=out)
    print("Making a fresh, real, read-only broker.get_orders() call to independently re-verify no order exists now...", file=out)
    try:
        open_orders = engine.broker.get_orders()
    except Exception as exc:
        print(f"Refusing: live broker.get_orders() call failed ({exc!r}) - cannot verify, so refusing to proceed. Fail closed.", file=err)
        return 1

    if not isinstance(open_orders, list):
        print(f"Refusing: broker.get_orders() returned {type(open_orders).__name__}, not a list - cannot verify.", file=err)
        return 1

    live_matches = [o for o in open_orders if order_matches_entry_fingerprint(o, ctx.entry_submission_fingerprint)]
    if live_matches:
        print(
            f"REFUSING - LIVE MATCHING ORDER FOUND: order_id={live_matches[0].get('order_id')!r}. "
            f"Reality has changed since the halt - a real order for {symbol} may now exist. "
            "NOT abandoning this position. Let ordinary reconciliation (a plain restart, or "
            "v34_bridge_clear_halt.py) pick this up instead - do not use this script here.",
            file=err,
        )
        return 1

    print(f"Confirmed: {len(open_orders)} live order(s) checked, zero match {symbol}'s fingerprint. Safe to abandon.", file=out)

    if not assume_yes:
        answer = input_fn(
            f"Abandon this stale {symbol} entry and clear the halt, with operator note {note!r}? [y/N] "
        ).strip().lower()
        if answer != "y":
            print("Not resolved.", file=out)
            return 1

    prior_status = ctx.status.value
    prior_failures = ctx.entry_reconciliation_failures
    del engine.state.active_trades[symbol]
    engine.audit.log(
        "OPERATOR_ABANDONED_STALE_ENTRY", symbol=symbol, note=note, prior_status=prior_status,
        prior_reconciliation_failures=prior_failures, halt_reason=engine.state.halt_reason,
        entry_submission_fingerprint=ctx.entry_submission_fingerprint, live_orders_checked=len(open_orders),
    )
    engine.store.save(engine.state)
    print(f"{symbol} removed from active_trades. Now clearing the halt latch (clear_halt_and_reconcile)...", file=out)

    cleared = engine.clear_halt_and_reconcile(note)
    if not cleared:
        print(
            "Position was abandoned, but clear_halt_and_reconcile() itself refused - see audit.jsonl "
            "(OPERATOR_CLEARANCE_REJECTED). The stale position is gone, but the halt latch is still set; "
            "investigate before restarting.",
            file=err,
        )
        return 1

    print("Halt latch cleared. Durable state now: status=STARTUP, clearance_required=False.", file=out)
    print("Next step (separate, deliberate action - not run by this script):", file=out)
    print("  python v34_bridge_runner_main.py", file=out)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve a stuck ENTRY_SUBMIT halt by abandoning a PROVEN-never-submitted entry, then clearing the halt."
    )
    parser.add_argument("--symbol", required=True, help="Exact symbol the stuck entry is for (e.g. SUNPHARMA).")
    parser.add_argument("--note", required=True, help="Operator attestation note - required, non-empty.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt (non-interactive use only).")
    args = parser.parse_args(argv)

    if not args.note or not args.note.strip():
        print("Refused: --note must be non-empty.", file=sys.stderr)
        return 1
    if not args.symbol or not args.symbol.strip():
        print("Refused: --symbol must be non-empty.", file=sys.stderr)
        return 1

    from v34_bridge_runner_main import _build_engine

    engine = _build_engine(on_event=print)
    try:
        return run_resolve(engine, symbol=args.symbol.strip().upper(), note=args.note, assume_yes=args.yes, out=sys.stdout, err=sys.stderr)
    finally:
        engine.lock_provider.release()


if __name__ == "__main__":
    sys.exit(main())
