# R5 closure execution plan

Scope: the active R5 external orchestrator and combined-cycle lifecycle. Paper completion and live enablement are separately evidenced. Historical V3/V4 engines, alternate Nautilus strategies and experimental controller models are not silently counted as active R5 implementations. Every existing project finding needs an implemented/tested, superseded, or explicitly deferred disposition before overall project closure.

## Actual starting state

Worktree /home/srinivas/projects/zerodha-r5-governor-refactor; feature/engine-ab-handoff-lifecycle; HEAD 411712afaecaeb44a6899a12db94ac547fef0186 with substantial tracked and untracked implementation work. Preserve it. Comparable suite: 1187 passed, 12 skipped; this is scoped verification, not complete system certification.

The external paper execution engine exists. Combined-cycle ownership/conversion, Engine B management and recovery modules exist. Worker opt-in injection exists. The sealed worker refuses the modified source, correctly. Trial zero at /tmp is missing, but the existing stage-A-v2 params/trial_000.json in zerodha-phase1 matches the v2 surface and registry bounds; input_discovery.json records it. The stock manifest exists here and the grid manifest exists in zerodha-phase1; file identities and complete slices still require loader validation before execution. No tuning or substitute Trial 007 is necessary.

Claude fleet loading controller is a separately tested prototype in zerodha-claude-fleet-loading. No import/call exists in the active R5 engine. It is not yet delivered engine functionality. The dispatcher/ECS chain must retain one executor exposure-headroom subtraction and bay availability/ceiling invariants.

Paper prefix reconstruction gives exact ledger parity on its named fixture. Direct startup hydration is still admission-blocked without certified live cursor/controller recovery. The current orchestrator is paper-only; the Kite adapter is not a drop-in execution contract. Live readiness is not established.

## Chosen harness

Use one external experimental lifecycle runner invoking Revision2ExternalEngineOrchestrator.run and the production R5 plant, governor, runtime, handoff and Engine B components. Reuse the existing worker data loader, causal grid provider, registry validation and metrics. Freeze current loaded source including dirty and untracked modules into an identified immutable execution snapshot. A separate experimental manifest replaces only the inappropriate sealed-parent assertion for this new entrypoint; it does not alter the sealed worker protocol or identity checks.

Keep internal tests for targeted invariants/faults and the existing four-arm subprocess harness for hold/PA research. Do not use source-patching arms as proof of final lifecycle integration, and do not build another trading engine inside a test harness.

## Ordered work and completion receipts

| Activity | Owner | Required implementation / acceptance |
|---|---|---|
| C01 Execution contract and inputs | Codex; Claude isolated runner package after snapshot | New explicit experimental paper entrypoint; source/data/Trial000 hashes; separate stock/grid roots; Block1 fixed 48-symbol/session selection; isolated output/WAL; holdout inaccessible; stale existing WAL refused. One real constructor/run path, no sealed guard bypass. |
| C02 Fleet loading and active controller disposition | Claude isolated integration package; Codex merge/test | Import the verified prototype into selected R5 plant path with explicit opt-in policy and telemetry. Test total capacity vs new-risk headroom, one exposure subtraction, availability/ceilings, trip override, signed error, anti-windup recovery, dt units and exact serialized continuity. Enabled math/policy identified; baseline defaults unchanged. Existing active-path signal/authority findings receive explicit corrective or retained-design dispositions. |
| C03 Complete integrated lifecycle | Codex | Filled A position -> correlated transfer -> broker CNC acknowledgement -> B owner -> next-session management -> exit -> both merit and bay feedback once. Verify product/quantity, stop continuity, timeout/rejection handling and no duplicate fill/fees. Use a labeled constructed qualification fixture if Trial000 historical bars contain no qualifying BUY; report absence honestly. Paper carry mode must explicitly select PERSIST and verified resume, not accidentally end-of-run CLOSE. |
| C04 Recovery and runtime safety composition | Codex; Claude may implement isolated schema/state helpers | Restore every actually instantiated PID/ECS/merit/MTM/cooldown/electrical/history and execution cursor dependency. Reconcile lifecycle and fleet snapshots with broker truth. Restart before/after fill, conversion ACK, stop update and close feedback; original/restarted ledger/state parity, unknown outcomes halt new risk, protection retained. Default healthy feed/broker assumptions replaced by explicit runtime telemetry for operational mode. |
| C05 Full Block1 paper acceptance | Codex | Run frozen identified Trial000, 48 symbols and exact Block1 sessions in fresh process/state using final integrated entrypoint. Save gross/net P&L, fees/slippage convention, marked-to-market drawdown, trade sides/holds, ID/governor/Gate12 accounting, owner transitions, protection and feedback receipts, input/source manifests. Validate accounting and rerun/resume parity. Do not advance to six blocks from constructor tests or one-symbol evidence. |
| C06 Live execution boundary | Codex | Explicit broker execution/fill adapter composition, actual-account authorization and holdings/positions allocation, asynchronous order/fill/quantity reconciliation, durable conversion/GTT intents and readback, verified protective continuity and certified cursor/controller boot. Offline contract/fault tests first; account read-only verification when access is available. No blanket GTT fill guarantee or boolean authorization bypass; no real orders in this development task. Until evidence exists, live admissions remain blocked. |
| C07 Release and project closure | Codex | Review and commit all required implementation files, exclude generated/raw reference outputs, pin source identity, run focused and comparable acceptance checks, resolve remaining finding dispositions and publish one release/audit receipt and final push. Report paper acceptance, live blockers and unexecuted coverage separately. |

Claude edits must run in a separate isolated snapshot containing the current required untracked modules; a checkout of HEAD alone is stale. Claude owns bounded files and supplies diffs/audit with tests NOT RUN unless independently executed. Codex executes tests, reviews and integrates changes, and owns final commit/push. No competing orchestrator edits in the same checkout.

## Delegation status

Bridge installed and account status reports authenticated. First read-only closure job 40d1e9b6-4c3a-4fd5-8e93-a56493dd4c59 failed before model work with OAuth refresh contention. The retry job 11260a29-926f-4ef8-9a02-46a84b59307d completed exit 0. No Claude modification is authorized on the shared dirty checkout. CLAUDE_REVIEW.md preserves Claude's review. Codex cross-checked it against the actual source and corrected six assumptions in CLAUDE_SUPERVISOR_CHECK.md; the latter controls integration choices. The runner recommendation is agreed. No engine edits or release actions occurred in this discussion turn.


## Added retention requirement from attached audit

C04 includes terminal Engine B/runtime-history cleanup and safe processed-feedback retention/compaction, preserving pre-mutation exactly-once suppression and restart consistency. The supplied unconditional DONE-receipt pop is rejected by an executed counterexample; see ATTACHED_AUDIT_CROSSCHECK.md and feedback_eviction_counterexample.json. No blind cleanup patch or production readiness declaration was applied.
