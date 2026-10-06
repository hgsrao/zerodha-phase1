# Stale-CNC re-entry activity audit (D02 working label / F-N02)

## Scope and identity
The inspected records do not explicitly alias D02 to F-N02. Following the user's instruction to implement the discussed project, this activity implements the stale-CNC defect identified in the preceding inspection. No other remediation is marked complete.

Branch: `411712afaecaeb44a6899a12db94ac547fef0186` on `feature/engine-ab-handoff-lifecycle`. Existing dirty work was preserved.

## Reproduction
Actual paper adapter BUY -> MIS-to-CNC conversion -> full SELL close -> new BUY or SELL fill. Both new-entry lifecycle registrations failed with `ValueError: Paper protection does not match position`. Before-fix result: **2 failed, 2 passed**. See `reproduction_before.log`. No broker-product mutation, mocks or runtime bypass were used. Lifecycle trade inputs are synthetic component fixtures; this is not market-replay evidence.

## Implementation
Only `revision2_external/paper_execution.py` changed among the 77 engine source files hashed before and after. A successful fill starting from zero quantity now establishes MIS, replacing stale CNC. Fills against a nonzero position retain the existing product. Rejected fills leave product unchanged. Fill pricing, costs, sizing, parameters, gate logic and runtime reconciliation checks were not changed. The isolated diff is `isolated_fix.patch`; it excludes pre-existing modifications.

## Verification
**51 passed, 32 warnings in 5.28s** across the new D02 component tests, D01 component and real TITAN replay tests, paper adapter, runtime, handoff, reconciliation and Engine B suites. Exact commands are the pytest module lists used in the session; logs: `verification_after.log`. `git diff --check`: PASS.

New regression coverage: BUY and SELL re-entry register/reconcile as MIS; partial CNC closes preserve CNC; rejected orders preserve flat product; each genuine fill books one cost entry. Existing D01 coverage additionally checks adding to an open CNC position and fail-closed mismatches.

## Unsuccessful additional fixture attempt
A TITAN replay seeded with actual pre-session conversion/close trades reached new entry, then failed the final gross-PnL/closed-trade invariant: seeded broker trades were not orchestrator ledger trades. That fixture was removed from the permanent tests, without changing production accounting. Its complete failure log remains in `replay_setup_attempt.log`. D02 end-to-end market replay is **NOT VERIFIED**. The existing clean D01 SELL replay passes.

## Disposition
**F-N02: implementation and component verification complete; D02 end-to-end replay remains pending.** No full Block 1 parity claim, overnight protection certification, live broker execution, holdout run, commit or push. No Claude modification was authorized. No sealed V3, raw reference output or frozen parameter file was edited. Other project milestones remain pending.

## Continuation verification
The identical new real replay regression fails against an isolated copy of the pre-fix paper adapter with `Paper protection does not match position`; it passes on current code. The synthetic pre-session conversion/close is now booked through the engine's actual `_execute_exit` path, so both gross P&L and friction close. Subsequent SELL admission, fill, ownership registration, reconciliation and close originate in `run()`. This does not claim replay-generated handoff or full 48-symbol Block 1 parity. Evidence: `R5_D02_evidence/real_replay_before.log` and `replay_verification.log`.

The earlier D02 end-to-end-pending statement is superseded for this explicitly scoped fixture only. No broader readiness claim is made.
