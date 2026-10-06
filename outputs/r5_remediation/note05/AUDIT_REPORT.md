# NOTE-05 / F-ROOT-002 runtime wiring audit

Status: VERIFIED_PARTIAL. Direct live/CNC admissions remain blocked.

## Repository and scope

Worktree: /home/srinivas/projects/zerodha-r5-governor-refactor.
Branch: feature/engine-ab-handoff-lifecycle; HEAD 411712afaecaeb44a6899a12db94ac547fef0186.
Existing dirty work was retained. No commit, push or live broker request was made.
The latest directive explicitly requested a candidate-worker extension; this supersedes the earlier plan's unchanged-worker wording. Default execution still supplies no combined-cycle runtime.

## Implementation

The worker accepts --paper-combined-cycle-state only for Stage B Block 1 execution. An explicit factory constructs CombinedCycleStore (SQLite WAL), enabled HandoffManager and EngineBController, and injects CombinedCycleRuntime into the real orchestrator constructor. It exclusively reserves a new state file: existing state is refused rather than overwritten or silently resumed. The store closes on constructor or replay failure and normal completion. Runtime policy is included in the opted-in block fingerprint and result; baseline fingerprints retain their prior structure. Prototype policies are recorded and not inserted into the sealed registry. end_of_run_disposition remains CLOSE: this option does not preserve carry after the end of the replay.

The sealed protocol hash, parent identity, calibration surface, sampling loader and holdout checks remain intact. The current modified engine tree therefore still fails the sealed CLI parent check. This audit does not claim a sealed worker run succeeded, and does not authorize bypassing that check. The integration fixture invokes the actual execute_block path with only its data loader substituted by the recorded one-symbol derived real fixture.

No sealed V3, frozen trading parameters, holdout data or master reference files were modified.

## Actual execution evidence

Focused command: /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest tests/test_r5_worker_runtime_wiring.py revision4/tests/test_intraday_trade_ledger.py revision4/tests/test_orchestrator_cross_session.py revision4/tests/test_validator_integration.py revision4/tests/test_validator_reconciliation_failure.py tests_external/test_broker_adapter_kite.py -q

Result: 23 passed, 64 warnings in 10.10s (focused.log).

The real TITAN one-session fixture, frozen Trial 007 parameters and NIFTY/VIX feeds produce one SELL trade in both worker modes. Ledgers match exactly; the enabled worker creates a CLOSED lifecycle row through actual fill/reconciliation/close processing. Existing-state refusal and preserved parent-drift rejection are tested. A separate execution receipt and both ledgers are saved here. Ledger JSON SHA-256: a63a3c0f8b16b291d56627f23861ba035b7f0145dcbdee5db265724582fb42ce.

Trade: quantity 5, held 3 bars, gross -51.00100000000111, costs 16.373347395499998, net -67.3743473955011. Exit: governor_exit:FSR_BELOW_EXIT:FSRN. No BUY conversion occurred in this historical fixture. Full 48-symbol Block 1: NOT RUN. Other blocks: NOT RUN.

## Exact failure triage

The requested --lf --continue-on-collection-errors rerun reproduced 13 failures and four collection errors (../note05_lastfailed_before.log). None was a WAL directory/mock or e-DIS exception failure.

* Nine failures: Revision 4's Gate 09 caller returned a (decision, quantity) tuple and then accessed .passed on it. Fixed the production boundary to unpack the decision consistently with Gate 10/11. Gate math and engine decision parameters are untouched. This is a real API mismatch, not a fixture issue. This narrow fix does not certify Revision 4 sizing: its pre-submission interface returns only pass/reason and does not propagate adjusted quantities from Gate 09/10/11. That existing contract limitation remains outside this R5 wiring activity.
* Three failures: obsolete Kite adapter tests expected minimal success receipts or blind retries after ambiguous network writes. Updated tests to require ACCEPTED/unfilled receipts and a single submission with ambiguous/no-retry disposition. Production placement logic was not changed in this activity.
* One failure: obsolete immutable-parameter error regex. The test still requires rejection and the exact protected parameter name; only intervening wording is allowed.

| Collection file | Actual cause | Disposition |
|---|---|---|
| tests/legacy_quarantine/test_oos_runner.py | transitive import of absent ecs_runtime_v2 through oos_calibration_engine | explicit archived-dependency skip |
| tests/legacy_quarantine/test_runtime_startup_gate.py | direct absent ecs_runtime_v2 | explicit archived-dependency skip |
| tests/legacy_quarantine/test_risk_manager_corrected.py | absent blocks.block_5_risk_manager | explicit archived-dependency skip |
| revision4/tests/test_ray_optuna_v3_calibration.py | absent optional ray dependency | explicit optional-dependency skip |

Skipped coverage has NOT passed. No historical modules were fabricated and no persistence mocks were added to disguise the failures.

## Live admission disposition

Not unblocked. The orchestrator constructs CostedPaperBrokerAdapter and explicitly requires that exact offline broker for PAPER_APPLY. The Kite adapter is not a drop-in implementation of its execution/fill contract. A DDPI/e-DIS boolean would not establish broker authorization, fill reconciliation or verified protection. No such bypass flag was added.

Direct hydration also retains its certified-resume-cursor guard. Full broker-compatible live composition, complete direct live recovery, verified account authorization/protection and a qualified BUY transfer on the selected integrated path remain unverified. Constructor wiring and identical paper ledgers do not establish universal crash safety.

## Wider verification

Comparable wider-suite command (same targets as the prior 13-failure report): /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q --continue-on-collection-errors tests tests_external revision4/tests test_gates_framework.py
Result: exit 0; **1187 passed, 12 skipped, 777 warnings, 146 subtests passed in 537.07s (0:08:57)**; see comparable_suite.log. No failures or collection errors in this command. The skip total includes four missing-dependency modules; skipped coverage is not certified.

An initial unrestricted invocation without explicit targets was interrupted after 688.23s: 6 failed, 475 passed, 12 skipped, 27 errors, 146 subtests passed. This is an incomplete exploratory run, NOT a green result. It collected standalone scripts (including scripts/test_48symbol_calibration.py, whose import executed calibration and failed on an empty common timestamp intersection) and loaded revision5 from /home/srinivas/projects/zerodha-r5-trace/revision5/__init__.py. The collection-time path mutation is explicit in docs/experiment_outputs/test_r5_audit.py:12–14: R5_REPO defaults to the trace worktree and is inserted at sys.path[0]. That wrong-worktree import caused many missing current modules and controller mismatch failures. scripts/test_revision3_single_symbol_diagnostic.py also has callable signatures unsuitable as pytest fixtures. full_suite.log retains the errors and interrupted summary; these are separate from the four original missing-dependency errors. No standalone scripts or reference outputs were repaired or deleted to conceal this outcome.

Source identities and dirty-tree receipt: source_receipt.json and git_status.txt.
