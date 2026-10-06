# C04 terminal retention and recovery package

Scope: isolated `/home/srinivas/projects/zerodha-codex-recovery-retention` snapshot. No primary checkout edits, broker calls, commits, frozen-parameter changes, holdout changes, or reference-output modifications.

## Implemented

- `CombinedCycleRuntime.close` accepts only CLOSED lifecycle records. It writes a terminal snapshot excluding that position's Engine B management cache and bar history. Only after successful durable `store.save` does it evict the in-memory B state and runtime history. A failed save preserves active management state.
- Runtime restore restricts aggregate historical management snapshots to IDs currently reported open by the authoritative lifecycle store, preventing a surviving position's older aggregate receipt from resurrecting a closed position. Active IDs remain subject to existing Engine B schema/decision validation.
- Morning recovery validates the entire Engine B receipt in a temporary controller before pruning it against current open IDs. State is installed only after existing broker reconciliation and protection verification. Admissions remain halted as before.
- `_compact_close_feedback_receipts` explicitly returns zero and retains all receipt guards. This is conservative non-compaction, not a completed memory-bounding solution.

## Why feedback receipts are retained

`PaperStateJournal.bind` requires fresh paper engine books and derives a source/config/input identity. Recovery reconstructs controllers by replaying every historical input. Existing durable feedback rows validate reconstruction; they do not certify that an arbitrary hydrated controller instance has already consumed the event. Consulting those rows to skip feedback would break reconstruction. Deleting DONE after CLOSED would still permit a retry to update merit/governor twice. No bounded retry window is established.

Future safe compaction requires a distinct consumption API: stable source/account/run-bound event identity and reconstruction epoch; validated durable controller-state receipt; atomic state plus per-epoch applied-event insertion; duplicate lookup before any controller mutation; rollback on failed insertion; restoration of the consumption epoch when hydrating the corresponding state, while a fresh replay obtains a fresh epoch and applies historical events once. Epoch transport must remain outside canonical economic/ledger state. This schema/protocol extension is deliberately not improvised here. With no such durable pre-mutation suppression API, guards remain retained with or without a journal.

## Tests

New `tests/test_r5_retention_recovery.py`: **8 passed**. Covers successful terminal eviction, failed persistence retention, nonterminal close rejection, stale aggregate restore exclusion, empty-store stale cache exclusion, no-journal and journal-present duplicates after conservative compaction, and actual morning hydration preserving active B state while removing terminal cache.

Existing close-feedback rollback, close-persistence failure, durable close outcome and B restore-validation bundle: **28 passed**, 32 pre-existing grid timedelta deprecation warnings, 5.08 seconds.

Native crash/replay, recovery hardening, morning boot and state recovery bundle: **50 passed**, 96 existing timedelta warnings, 47.48 seconds. This run preceded the eighth new morning-cache test, which passed in the independent eight-test run. Existing subprocess crash/replay tests preserve exact ledger/controller reconstruction. Logs: `C04_RECOVERY_TESTS.log`, `C04_FOCUSED_TESTS.log`, `C04_RETENTION_TESTS.log`.

## Integration files

`outputs/C04_IMPLEMENTATION.patch` contains bounded production hunks against the primary source as observed before supervisor integration. Add new test file and this audit separately. Production files changed: `revision2_external/orchestrator.py`, `revision5/engine_b_management.py`, `revision5/combined_cycle_runtime.py`, `revision5/morning_recovery.py`. No changes to paper/state recovery journal implementation.

## Disposition

Terminal management-state retention: implemented and tested. Feedback guard memory bounding: explicitly deferred behind a concrete durable consumed-event protocol; duplicate suppression remains safe. This package does not certify direct hydrated execution, live recovery, guaranteed GTT fill, or unrestricted live/CNC admissions.
