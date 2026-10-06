# R5 post-Codex test verification

Date: 2026-10-06. Scope: verify the current test state without modifying source. No fleet diagnosis, no repair, no C03-C07 work.

## Provenance
- Repository: `/home/srinivas/projects/zerodha-r5-governor-refactor`
- Branch: `feature/engine-ab-handoff-lifecycle`
- HEAD: `411712afaecaeb44a6899a12db94ac547fef0186` (matches expected)
- Pre-test: 20 tracked files modified (1061 insertions, 251 deletions), 63 further status entries (mostly untracked), `git diff --check` clean (no output).
- Note: an earlier look in this session showed 16 modified tracked files. The tree changed between sessions, before this activity began.
- After testing: `git status --short`, `git diff --stat` and `git diff --check` are identical to before (diffed).
- SHA256 recorded before and after for 442 files (`revision2_external`, `revision5`, `scripts`, `runtime`, `tests`, `tests_external`, `revision4`; `.py`/`.json`/`.csv`, plus `test_gates_framework.py` and `canonical_parameter_registry.py`). Result: UNCHANGED (diff empty).
- Pytest cache/bytecode: runs used `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`. `.pytest_cache` (mtime 2026-09-29) and the 33 `__pycache__` directories already existed, are git-ignored, and were not changed by this activity.

## Focused test list and source
No single "focused list" is recorded. The list was assembled from files the closure documents name:
- `outputs/r5_remediation/closure/INTEGRATED_SOURCE_RECEIPT.json` (new and changed test files)
- `C03_ACCEPTANCE_AUDIT.md`
- `C06_EXECUTION_BOUNDARY_AUDIT.md`
- `C01_CLAUDE_IMPLEMENTATION_AUDIT.md`
- the suggested command in `CLAUDE_FINAL_STATIC_REVIEW.md`
- the two D01 tests required by this activity

The earlier transcript's "73 tests" came from `c01/integrated_focused.log` (73 passed on two files). The exact file list behind it is not recorded, and nothing records which files made up the figure.

19 files (all exist):
```
tests/test_r5_paper_lifecycle_entry.py   tests/test_r5_worker_runtime_wiring.py
tests/test_r5_lifecycle_acceptance.py    tests/test_r5_d01_orchestrator_run_integration.py
tests/test_r5_d01_product_reconciliation.py   tests/test_r5_live_execution_boundary.py
tests/test_r5_feedback_compaction.py     tests/test_r5_retention_recovery.py
tests/test_r5_terminal_sentinel.py       tests/test_r5_block1_acceptance_counters.py
tests/test_fleet_loading_controller.py   tests/test_fleet_loading_integration.py
tests/test_fsig011_sustained_studies.py  tests/test_revision5_three_controller_parameterization.py
tests/test_r5_morning_startup.py         tests/test_combined_cycle_runtime.py
tests/test_engine_b_management.py        tests/test_r5_close_feedback_recovery.py
tests/test_r5_close_persistence_fault.py
```

## Pre-run safety classification
Static screen for writes outside tmp. Every write site in these 19 files targets a `tmp_path`-derived location (`env.out`, `grid_root`, `view`, `repo`, `root/...`) or a temporary directory. Reads of `ROOT`/`FIXTURE` are read-only. `test_r5_paper_lifecycle_entry.py` integration runs a single-symbol TITAN fixture through a substituted loader, not a 48-symbol replay. No network or live-broker use.
- All 19 files: **WRITES_ONLY_TMP_PATH**.
- None classified UNSAFE_OR_UNRESOLVED.

This classification is a heuristic screen, not proof.

## Focused run
- Command: `PYTHONDONTWRITEBYTECODE=1 /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q -p no:cacheprovider <the 19 files above> -rA`
- Exit code: **0**
- PASSED=**274** FAILED=0 SKIPPED=0 XFAILED=0 XPASSED=0
- Duration: 161.54 s (0:02:41)
- Warnings: 981, all `DeprecationWarning` for NumPy generic timedelta unit (`revision2_external/grid_context.py:176`, `pd.Timedelta(seconds=...)`). No other warning categories seen in the summary.
- Failure tracebacks: none.

## D01 protection
- `tests/test_r5_d01_product_reconciliation.py`: **PASS** (10 passed)
- `tests/test_r5_d01_orchestrator_run_integration.py`: **PASS** (2 passed)
- D01 remains CLOSED_VERIFIED; no contradicting evidence. No D01 code touched.

## Broad suite claim
- Claimed: 1187 passed / 12 skipped.
- Where the claim came from: `outputs/r5_remediation/note05/AUDIT_REPORT.md:56`. It records the command as `python -m pytest -q --continue-on-collection-errors tests tests_external revision4/tests test_gates_framework.py` (exit 0, 537 s), run in the NOTE-05 activity, before the Codex changes.
- Exact original command recovered: **YES**, apart from interpreter path details (same venv).
- Safety: the command targets offline test directories only. It was executed under `bwrap --ro-bind / / --dev /dev --proc /proc --bind /tmp /tmp --unshare-net --die-with-parent` (repository read-only, network blocked), plus `-p no:cacheprovider`.
- Result: exit 0; **1382 passed, 12 skipped**, 146 subtests passed, 1593 warnings, 665.45 s (0:11:05); 0 failed, 0 errors.
- Claim status: **NOT_REPRODUCED** as stated. The skip count matches (12). The pass count differs by +195, consistent with the new tests added after NOTE-05 (the Codex files) but not established as the only cause. Whether the 12 skips are the same 12 was not checked.
- The count is evidence, not a target. The current tree has no failing tests in the targeted suite.

## Fleet
- Baseline and fleet-enabled Block-1 ledgers are reportedly byte-identical (this session's JSON comparison agrees).
- Fleet effectiveness is **UNDEMONSTRATED**. No cause is inferred.

## Handoff
- Constructed test coverage does not equal natural historical handoff evidence.
- The natural 48-symbol Block-1 runs (baseline and fleet-enabled WALs): 52 positions, 0 conversion rows, 0 ENGINE_B-owned positions. Natural historical handoff: **NOT_DEMONSTRATED**.
- C03 historical acceptance is not claimed.

## Result
- PRODUCTION SOURCE CHANGED BY THIS ACTIVITY = NO
- TEST SOURCE CHANGED BY THIS ACTIVITY = NO
- Live broker/API used = NO

## Limits
- The focused list is reconstructed, not a recorded one.
- Static write screen is heuristic; the broad run's safety rests mainly on the read-only sandbox.
- Not checked: that the 12 skips are the same as NOTE-05's, stability across repeat runs, or any coverage beyond these suites.
