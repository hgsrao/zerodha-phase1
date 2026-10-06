**Confirmations (from the files read and two greps)**
- **Isolation harness:** it is strategy-only. It spawns `block1_arm_worker.py` per arm and builds a standalone zero-alpha null. A grep of `scripts/diagnostics` for `combined_cycle`, `handoff` and `fleet_loading` found nothing. I did not read `block1_arm_worker.py` or `isolation_lib.py`, so I haven't verified that they never touch the broker or lifecycle. The harness's own docstring says it is diagnostic only.
- **Fleet-loading prototype:** it is not integrated. No `.py` file outside `outputs/r5_remediation/phase1_flat_bar/sources/*` references `fleet_loading`, and those are snapshot copies. The supervisor receipt says the same: three new files in a separate checkout, verified at 73 passed, with integration still open.
- **Sealed baseline:** `verify_engine_identity` still fails on the modified tree (`ENGINE_PARENT_DRIFT`). The NOTE-05 audit says no sealed run succeeded, and that is still true.

**Recommendation**
Add one new script, `scripts/run_r5_paper_lifecycle.py`. It should import `build_paper_combined_cycle_runtime`, `prepare_block` and `execute_block` from `run_r5_step5_candidate.py`. It should not reuse `main()` or its sealed `CANDIDATE_EVALUATION` result mode. Output goes to a new directory, labelled `EXPERIMENTAL_PAPER_LIFECYCLE` and carrying the actual `git_head`, a dirty-tree source hash and the protocol hash. `ENGINE_PARENT_DRIFT` is recorded as a field and never suppressed. It never writes sealed result paths.

**Missing plumbing**
1. `morning_recovery.identity` reads `engine.combined_cycle_runtime.config`, so it fails on a runtime-less engine. `capture` and `prepare` have no caller in the worker, and the disposition is hardcoded to `CLOSE`.
2. Nothing drives multi-session `PERSIST`, a checkpoint at session end, or a fresh-engine `prepare` at session start. The cursor guard requires `next_timestamp`.
3. `execute_block` supports only one block, one state file and a one-symbol fixture. The 48-symbol Block 1 is NOT RUN.
4. Fleet loading has no hook into the engine.
5. The runtime needs a paper broker that implements `ensure_protection`, `request_product_conversion`, `conversion_receipt` and `ensure_cnc_gtt`. `CostedPaperBrokerAdapter` must be checked for the last of these, since `morning_recovery` calls it.

**Ordered closure activities**
1. **Entrypoint and labelling.** Acceptance: `pytest tests/test_r5_paper_lifecycle_entry.py` asserts the output label, an unmodified sealed protocol SHA, and that a pre-existing state file is refused.
2. **Baseline non-contamination.** Acceptance: the default `execute_block` call (no runtime) gives a byte-identical `block_fingerprint` before and after the change.
3. **PERSIST plus checkpoint and prepare across a session boundary.** Acceptance: a two-session fixture with an end-of-session `capture`, then a new process that calls `prepare` and resumes. `admissions_allowed` stays False until the cursor advances, and the ledger matches an uninterrupted run.
4. **Broker contract.** Acceptance: a contract test shows the paper adapter exposes every method named in the plumbing list, and a missing method raises instead of being skipped.
5. **48-symbol Block 1 run.** Acceptance: it exits 0 and writes a receipt with zero safety violations. It also reports lifecycle rows equal to completed trades, and open-position count zero under `CLOSE`.

**Claude work packages (isolated)**
- **WP-A:** `scripts/run_r5_paper_lifecycle.py` and its tests (activities 1, 2 and 5). Write scope is those files only. It must not edit `run_r5_step5_candidate.py`.
- **WP-B:** the recovery plumbing (activities 3 and 4). Write scope is a new `revision5/session_driver.py`, the identity null-safety in `morning_recovery.py`, and the broker contract tests. Fleet loading stays out of scope.

**Limitations**
- Claude cannot execute commands, so all acceptance runs fall to Codex.
- Nothing here establishes profitability or live readiness. Live admission remains blocked per NOTE-05.
