# R5-C02.0 Fleet effect diagnosis (read-only)

Date: 2026-10-06. Repo `/home/srinivas/projects/zerodha-r5-governor-refactor`, branch `feature/engine-ab-handoff-lifecycle`, HEAD `411712afaecaeb44a6899a12db94ac547fef0186`. No source, test, fixture or parameter edits; no replay rerun; no live broker.

## Conclusion
**Classification D, ACTIVE_BUT_NEVER_BINDING.** The fleet controller is constructed, wired into the active paper path, called on every plant evaluation, and its output replaces the ECS demand that dispatch and entry admission consume. In Block 1 it never produced an output different from the capacity it was handed, so the baseline and fleet runs are identical by construction.

Secondary (structural): as wired (`capacity_pu = reference`), the controller can never raise demand and only lowers it when gross exposure exceeds the reference. Block 1 never got near that condition.
Secondary (instrumentation): the run records no per-evaluation fleet telemetry. The binding analysis below is reconstructed from journal checkpoints.

## Gate 1: provenance
- Pre-activity `git status --short` saved to `/tmp/r5v/c02_status.txt`; 20 tracked files modified.
- Earlier in the session the tree showed 16 modified tracked files (790+/241-). Six tracked files carry mtime 2026-10-06 07:34:20: `orchestrator.py`, `paper_state_journal.py`, `plant_control.py`, `continuous_exit_controller.py`, `final_execution_controller.py`, `tests/test_revision5_three_controller_parameterization.py`.
- The four most likely additions (the other two were already modified): `revision2_external/continuous_exit_controller.py` (+4), `revision2_external/final_execution_controller.py` (+6/-), `revision5/plant_control.py` (+107, the fleet wiring), `tests/test_revision5_three_controller_parameterization.py` (+4/-).
- Classification: **KNOWN CLOSURE WORK** for all four. Each is listed in `outputs/r5_remediation/closure/INTEGRATED_SOURCE_RECEIPT.json` (`changed_existing`) and its current SHA256 matches the receipt. Caveat: the "four additional" identification rests on mtimes and the receipt, not on a saved pre-image of the 16-file list.
- The other 16 modified tracked files date from 2026-10-05/06 05:43 or earlier; not examined further.
- C05 runs finished at 07:52:42 (baseline) and 07:54:11 (fleet), after the 07:34 write. The run manifests record 342 source files; all 342 match the files on disk now. The C05 evidence therefore reflects the current source.

## Gate 2: the controller (`revision5/fleet_loading_controller.py`, 194 lines)
- Interfaces: `FleetLoadingPolicy` (frozen dataclass), `FleetLoadingController.update(reference_pu, actual_exposure_pu, dt_seconds, capacity_pu, protection_tripped)`, `export_state`/`restore_state`, `new_risk_headroom_pu`.
- Policy defaults: `enabled=False`, `kp=0.25`, `ki=0.01`, `kd=0.0`, `integral_limit=10.0`, `max_capacity_pu=1.0`. Source calls them untuned placeholders. The run used `FleetLoadingPolicy(enabled=True)` (identity `3ce0fad2…b62c`).
- Math: `error = reference - actual`; `raw = reference + kp*error + ki*I + kd*dD`; `cap = clamp(min(max_capacity_pu, capacity_pu), 0)`; `allowed = clamp(raw, 0, cap)`. Integrator holds while saturated against its own direction. A protection trip returns capacity 0. A disabled policy returns the supplied capacity unchanged.
- Consequence: when `capacity_pu == reference` and `error > 0`, `raw > reference = cap`, so `allowed = cap = reference`: the output equals the input. `allowed < capacity` only if `error < 0`, i.e. actual gross exposure above the reference.
- `fleet_loading: true` in the manifest means "construct the controller with `enabled=True` and pass it to the orchestrator". It does not mean "the controller changes decisions".

## Gate 3: static wiring trace
```
scripts/run_r5_paper_lifecycle.py:680-688   --fleet-loading -> FleetLoadingPolicy(enabled=True) -> run_extra['fleet_loading_policy']
  -> scripts/diagnostics/r5_block1_acceptance.py:126-131   checks orchestrator signature, passes fleet_loading_policy=
  -> Revision2ExternalEngineOrchestrator(..., fleet_loading_policy)   orchestrator.py:144 -> :348
  -> PlantControlChain(..., fleet_loading_policy)   plant_control.py:448-457 builds FleetLoadingController
  -> orchestrator.py:564  snapshot = self.plant_control.evaluate(timestamp, bay_status, gross_exposure_fraction=gross/equity, ...)
  -> plant_control.py:524-531  telemetry = fleet_loading.update(reference=ecs.plant_demand_reference_pu*limit, actual, dt, capacity_pu=reference)
        demand = telemetry.allowed_capacity_pu / limit ; ecs = replace(ecs, plant_demand_reference_pu=demand, ...)
  -> dispatch_controller.dispatch(ecs) -> build_governor_references(dispatch)   (plant_control.py:532)
  -> orchestrator.py:574  self._paper_plant_snapshot = snapshot
  -> orchestrator.py:640  _paper_plant_entry_limit: cap_dispatch_entry(reference, ...) uses reference.plant_demand_reference_pu and dispatch_reference_pu
  -> min(quantity, result["quantity"]) -> order path
```
- CONTROLLER CONSTRUCTED: YES
- CONTROLLER PASSED TO ACTIVE RUNTIME: YES
- CONTROLLER CALLED: YES (runtime evidence: controller `updates=1875`, equal to `plant_control.evaluations=1875`)
- CONTROLLER RETURN VALUE CONSUMED: YES (replaces `ecs.plant_demand_reference_pu`)
- RETURN VALUE CAN ALTER ENTRY ADMISSION: YES (lower reference reduces `remaining`, can drive quantity to 0)
- RETURN VALUE CAN ALTER POSITION SIZE: YES (same path caps quantity)
- RETURN VALUE CAN ALTER SYMBOL DISPATCH: YES (bay references derive from the replaced demand)

## Gate 4: existing C05 evidence (`outputs/r5_remediation/closure/c05/{baseline,fleet_enabled}`)
SHA256 comparison (12-character prefixes):

| Artifact | baseline | fleet | result |
|---|---|---|---|
| ledger.csv | 311b3a145ab0 | 311b3a145ab0 | IDENTICAL |
| ledger.json (trades) | 4f41759cf8c8 | 4f41759cf8c8 | IDENTICAL |
| SUMMARY.md | 767478c9255f | 767478c9255f | IDENTICAL |
| acceptance/broker_fill_counts.json | 0efb10b68630 | 0efb10b68630 | IDENTICAL |
| acceptance/passive_rejection_accounting.json | 968b0512cb7c | 968b0512cb7c | IDENTICAL |
| acceptance/paper_journal_statistics.json | 7e4fdf8d222c | 7e4fdf8d222c | IDENTICAL |
| acceptance/native_accounting_reconciliation.json | 15dbd0a10c3c | 15dbd0a10c3c | IDENTICAL |
| acceptance/runtime_safety_contract.json | eeeb4da8ddab | eeeb4da8ddab | IDENTICAL |
| result.json | 94927fb12371 | d074c9b94dca | DIFFER |
| run_manifest.json | 9db1252dbc74 | 47101d9feabb | DIFFER |
| acceptance/acceptance_result.json | d416bfa53414 | 8372663ece05 | DIFFER |
| acceptance/native_report.json | 513fe0ae3190 | 0ff4f38b8d5f | DIFFER |
| acceptance/runtime_receipts.json | 7a09decb75dd | c7a0c31fe5a4 | DIFFER |

- The differing files differ only in provenance fields: a `plant_control.fleet_loading` end-state block present only in the fleet run, `block_fingerprint`, and output paths (a structural diff of `result.json` found exactly these 5 differences; the other three differing files were not diffed individually). Trades, fills, rejections and admission counts are identical.
- `ecs_demand_pu` is identical in all 1876 journal checkpoints. Caveat: that field is the ECS internal state, which the fleet override does not write, so it is not proof of the final demand used by dispatch.
- Earliest possible divergence stage: `PlantControlChain.evaluate`, line 524-531, on the first evaluation. It did not diverge there (see Gate 6) or at any later stage.
- BASELINE/FLEET TRADE LEDGER BYTE-IDENTICAL: YES. FILL LEDGER: YES (`broker_fill_counts.json` identical; there is no separate fill ledger file).

## Gate 5: fleet invocation evidence
- Available natively: controller `updates=1875`; final state (integral 0.0, `prev_error_pu` 0.5, timestamp 2023-12-11T15:29:00+05:30).
- Not recorded: per-evaluation candidate counts, allowed/rejected/clamped counts, "fleet limit binding" counts, or per-trade fleet output. `controller_telemetry` is empty (compact mode). **INSTRUMENTATION INSUFFICIENT** for native binding counts.
- Reconstruction used: the paper journal holds 1876 checkpoints, each embedding fleet controller state (`integral`, `prev_error_pu`, `actual_exposure_pu`). Reference = `prev_error + actual_exposure`; allowed was recomputed with the policy formula. This is a reconstruction, not a native count.

## Gate 6: policy mathematics (from journal checkpoints, fleet run)
- Binding condition for the active wiring: `allowed < capacity` iff gross exposure fraction > reference (= ECS demand x 0.5, the `max_gross_exposure_fraction`).
- Observed: reference values {0.0, 0.05, ..., 0.5} (ECS demand 1.0 in 1638 of 1876 checkpoints, 0.0 in 175). Actual gross exposure / equity: min 0.0, max **0.2876**. Closest approach where reference > 0.2: actual/reference = 0.575.
- Integrator: 0.0 at every one of 1876 checkpoints (it never integrated).
- Reconstructable evaluations: 1721 (those with a stored `prev_error`). Reconstructed binding events (allowed < capacity): **0**.
- Negative-error samples: 2 (2023-12-07 10:40 and 10:41, actual 0.042). Both had reference 0.0 (capacity already 0), so output = capacity = 0: not binding.
- Unreconstructable: about 154 evaluations (no stored `prev_error`: first sample plus protection-tripped evaluations). A trip returns allowed 0 by construction; the ECS also drives demand to zero on protection trips, and results are identical, but those evaluations were not individually verified.
- Verdict: **WIRED BUT NEVER BINDING**, not NOT WIRED.
- Observation (not tested): when exposure exceeds the reference, the executor cap `budget*plant_demand_reference - gross_notional` in `cap_dispatch_entry` is already <= 0, so even binding fleet events would partly duplicate an existing limit through the plant term. They would still alter the bay dispatch references.

## Gate 7: dispatch/admission trace (representative trades; fleet run journal checkpoint at the entry bar)
| trade | side | qty / notional | reference pu | actual gross pu | integral | ECS demand |
|---|---|---|---|---|---|---|
| trade-2 LT, 2023-12-05 10:01 | SELL | 37 / 122,004 | 0.5 | 0.1634 | 0.0 | 1.0 |
| trade-52 DRREDDY, 2023-12-11 12:08 | SELL | 100 / 109,015 | 0.5 | 0.1098 | 0.0 | 1.0 |
| trade-14 INFY, 2023-12-05 15:14 | BUY | 94 / 133,841 | 0.5 | 0.1341 | 0.0 | 1.0 |
| trade-15 TITAN, 2023-12-05 15:18 | BUY | 37 / 131,412 | 0.5 | 0.2656 | 0.0 | 1.0 |
- Fleet evaluated: YES (every portfolio timestamp). Fleet output: allowed capacity = reference (0.5). Unconstrained vs constrained admission: identical. Position size before/after fleet: identical (same ledger). Reason code: standard paper-admission path (no fleet reason code recorded).
- The checkpoint at the entry bar may include exposure after the fill; the conclusion does not depend on it (all values far below 0.5).
- Per-trade admission events (`PLANT_CONTROL_PAPER_ADMISSION`) are not retained in the C05 outputs; per-trade fleet telemetry is **INSTRUMENTATION INSUFFICIENT**.
- The 52 trades arise from: governor entry decisions (1516 `ENTRY:GOVERNOR_ENTRY`), then the paper admission cap (1568 evaluations, 1035 rejections, 133 caps), identical in both runs.

## Gate 8: test coverage
- `tests/test_fleet_loading_controller.py`: 34 controller test functions (math, clamps, anti-windup, trip, serialization, validation). **UNIT_ONLY** with failure paths.
- `tests/test_fleet_loading_integration.py` (7 tests):
  - `test_disabled_policy_preserves_actual_run_ledger`: **INTEGRATION_WIRING** (disabled policy leaves ledger unchanged).
  - `test_opt_in_actual_orchestrator_clock_and_feedback`: **INTEGRATION_WIRING** (real orchestrator run, `updates > 1`, telemetry present; no behavioral difference asserted).
  - `test_real_grid_chain_overexposure_clock_protection_and_restore`: **BEHAVIORAL_EFFECT at chain level** (synthetic gross 1.1 lowers `ecs.plant_demand_reference_pu` below 1) plus **FAILURE_PATH**; does not run through an entry decision.
  - `test_journal_policy_identity_and_exact_resume_controller_state`, `test_morning_checkpoint_retains_fleet_but_defers_unverified_measurement`, `test_offline_restore_refuses_broker_claim_unsampled_and_wrong_next_clock`, `test_absent_controller_preserves_report_checkpoint_shape`: **INTEGRATION_WIRING / FAILURE_PATH**.
- Focused fleet coverage: **MIXED**. No test proves that the fleet changes an entry quantity or admission through the full runner -> orchestrator -> `_paper_plant_entry_limit` path. A constructed fixture would prove wiring, not natural Block-1 binding.

## Gate 9: root-cause classification
**D, ACTIVE_BUT_NEVER_BINDING.** Evidence: controller called 1875 times; output consumed in `plant_control.py:524-531`; 0 reconstructed binding events in 1721 non-trip evaluations; integrator 0.0 throughout; actual gross exposure max 0.2876 vs reference 0.5 for 87% of bars; all ledgers and admission accounting identical. Not A/B/C/F (verified wiring, calls, consumption, and `fleet_loading_policy` enabled in the manifest). G is a secondary condition, not the primary cause.

## Gate 10: smallest next repair design (not implemented)
Do not change thresholds or gains.
1. Decide design intent first: should the controller be a pure one-sided limiter (current behavior with `capacity_pu = reference`), or should it be allowed to modulate demand when underloaded? That is an owner decision, not a code defect.
2. Demonstrate legitimate behavior on historical data without altering parameters: find or construct a condition where gross exposure exceeds the ECS reference (ECS demand 0.05-0.4 occurs only 7 times each in Block 1). Preferred minimal evidence: an offline behavioral test through the active orchestrator `run` that drives an overexposed state and asserts a reduced entry quantity versus the disabled-policy baseline (a constructed fixture, labeled as such).
3. If native per-evaluation telemetry is wanted for natural runs, add minimal counters (evaluations, `allowed < capacity`, binding magnitude) behind the existing compact-telemetry mode.
Must preserve: D01, D01.1, paper safety, existing risk limits, current Trial-000 parameters, historical input data.

## Provenance after this activity
- Created only this report. `git status --short` unchanged from the pre-activity snapshot except for this report file. SQLite artifacts were opened read-only with `immutable=1`.

## Limits
- Reconstruction rests on journal checkpoint state, not on native telemetry.
- Unverified: individual trip-evaluation outputs; why ECS demand is 0.05-0.4 in only 7 checkpoints each; whether other Block-1 conditions (e.g. a different parameter or period) would bind.
