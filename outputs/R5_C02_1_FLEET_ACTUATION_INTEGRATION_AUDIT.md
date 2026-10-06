# R5-C02.1 Fleet actuation integration audit

Date: 2026-10-06. Test-only activity. The only intentional source addition is `tests/test_r5_c02_fleet_actuation_integration.py`; this report is the only other artifact.

## Repository identity and tree state
- Repository: `/home/srinivas/projects/zerodha-r5-governor-refactor`; branch `feature/engine-ab-handoff-lifecycle`; HEAD `411712afaecaeb44a6899a12db94ac547fef0186`.
- Pre/post `git status --short`, `git diff --name-status`, `git diff --stat`, `git diff --check`: identical except one added untracked line, `?? tests/test_r5_c02_fleet_actuation_integration.py`. `git diff --check` clean (exit 0) both times. The tree already carried 20 modified tracked files and many untracked entries before this activity; none were touched.
- Pre-activity snapshot: `/tmp/r5c21/pre_git.txt`; post: `/tmp/r5c21/post_git.txt`.

## Production hashes (SHA256, 215 `.py` files under `revision2_external`, `revision5`, `scripts`, plus `canonical_parameter_registry.py` and `calibration_config.py`)
Before = after for all files. PRODUCTION HASHES UNCHANGED = YES. Key files:
- `revision5/fleet_loading_controller.py` `77a3f158ba09e23a12d3e1e2e05760fa0aae1de94c35d3ff9c4a44d7e93fcf2b`
- `revision5/plant_control.py` `57fa04eda11434ae98b02df68908d4099dbf6c5e78bfb7ce9e1eefe45e2233e2`
- `revision2_external/orchestrator.py` `7a0e7ad71590c28f09d14733d88eec72aeb0e630d1430a24996470c17f0cf96b`
- New test: `ca8524a864a579203c275aa14c60e4903b361084afdaaccd38c1a31762938658`
- The D01 fixture files still match `tests/fixtures/r5_d01/PROVENANCE.json` hashes. Trial-000 parameters: untouched. Historical data: untouched (not read).

## Gate 2: exact current integration path (verified on the current tree)
```
test -> run_scenario -> build() [normal Revision2ExternalEngineOrchestrator constructor, PAPER_APPLY, fleet_loading_policy=...]
  orchestrator._plant_control_shadow_step(ts, limit)                       orchestrator.py:~535-585
    -> self.plant_control.evaluate(ts, bay_status, gross_exposure_fraction=gross/equity, ...)   orchestrator.py:564
      -> PlantControlChain.evaluate: ecs.evaluate, then (fleet active)                         plant_control.py:~505-531
         reference = ecs.plant_demand_reference_pu * limit
         telemetry = fleet_loading.update(reference, actual, dt, capacity_pu=reference)         plant_control.py:525
         ecs = replace(ecs, plant_demand_reference_pu=telemetry.allowed_capacity_pu/limit)
      -> dispatch_controller.dispatch(ecs) -> build_governor_references(dispatch)               plant_control.py:532
    -> self._paper_plant_snapshot = snapshot                                                    orchestrator.py:574
  orchestrator._paper_plant_entry_limit(symbol, quantity, price, ts)                            orchestrator.py:643
    -> BayTurbineClosedLoopGovernor.cap_dispatch_entry(reference, ..., bay_notional, gross_notional)   governor.py:239-266
         remaining = max(0, min(budget*dispatch_ref - bay_notional, budget*plant_demand_ref - gross_notional))
    -> returned quantity assigned to `quantity` in the entry path (orchestrator.py:2100 / :2178)
```
Not exercised: order construction and submission, the later gross/sector/safety gates, and a full `run()`.
The C02.0 call graph is still correct on the current tree. `FleetLoadingController.update` is reached only through `PlantControlChain.evaluate`; the test never calls it directly in the actuation assertions.

## Fixture classification and design
**CONSTRUCTED_OVEREXPOSURE_FIXTURE.** Not historical, calibration or profitability evidence. The engine is built with the unmodified production constructor; the TITAN fixture bars are used only to obtain a valid exchange timestamp (2024-02-13 09:55 IST).
- Equity 1,000,000; `max_gross_exposure_fraction` 0.5 (budget 500,000).
- The engine's own start-of-run ECS ramp gives demand 0.1, so reference = 0.1 x 0.5 = 0.05 pu. Control and treatment are identical on this input.
- Existing exposure sits in another bay (GTG2): INFY 450 x 100 = 45,000 and HCLTECH 150 x 100 = 15,000. Sampled gross at the start of the timestamp = 60,000 (0.06 pu).
- Same-timestamp exit: HCLTECH is removed from `open_trades` before the entry is sized, so live gross = 45,000 (0.045 pu). The broker exit leg is not exercised.
- Candidate: TITAN (bay CSTG2, no existing bay exposure), price 100, requested quantity 100 (10,000 notional). Side plays no role.

## Why the condition is fleet-binding (and why the control is still admissible)
- The production fleet policy (defaults, unmodified) binds only when sampled gross exposure exceeds the reference: `allowed < capacity` iff `actual > reference`, because capacity = reference.
- Sampled exposure 0.06 > reference 0.05, so the controller outputs below capacity.
- The plant is sampled once per timestamp (documented in `plant_control.py`), while the executor uses live gross. The exit lowers live gross to 0.045 before the entry is sized, so the control still has headroom.
- Design finding (not changed): at a single instant, fleet binding and the executor plant term coincide, because both use the same reference. A binding fleet limit then already faces an exhausted executor plant term. Fleet derating of an otherwise admissible entry therefore arises only when gross falls between plant sampling and entry sizing (same-timestamp exits), or through the bay-reference scaling. This is part of why Block 1 saw no natural binding and should inform the C02 design review.

## Control arithmetic (fleet disabled)
- ECS demand 0.1; total capacity 0.05 pu of equity (= budget 500,000 x 0.1 = 50,000 notional plant cap).
- Executor terms: bay term 500,000 x 0.018 = 9,000; plant term 50,000 - 45,000 = 5,000. Binder: plant term, positive.
- Headroom 0.05 - 0.045 = 0.005 pu = 5,000 notional. Admitted quantity = 5,000 / 100 = **50** (requested 100).

## Treatment arithmetic (fleet enabled)
- Fleet inputs: reference 0.05, actual (sampled) 0.06, error -0.01. raw = 0.05 + 0.25 x (-0.01) + 0.01 x I(-0.01) = 0.0474. Output capped at capacity 0.05, so allowed total capacity 0.0474 pu (below capacity; binding). ECS demand becomes 0.0948.
- Executor terms: bay term 500,000 x 0.017064 = 8,532; plant term 500,000 x 0.0948 - 45,000 = 2,400. Binder: plant term.
- Headroom 0.0474 - 0.045 = 0.0024 pu = 2,400 notional. Admitted quantity = **24**.

## Final downstream effect
Quantity returned to the order path: control 50, treatment 24 (requested 100). Nonzero derating of 52%. DOWNSTREAM ACTUATION CHANGED = YES. All non-fleet inputs (equity, limit, sampled/live gross, bay availability, request, price, candidate) are identical; only the fleet-owned demand differs. The difference is not caused by another cap: in both cases the plant term is the smaller executor term, and the treatment plant term is lowered solely by the fleet output.

## Double-subtraction proof (`test_actual_exposure_is_subtracted_exactly_once`)
- Fleet output is total capacity, not headroom: 0.0474 pu, which exceeds actual 0.045 pu.
- Executor subtracts live gross once: quantity = (capacity x equity - actual notional) / price = (47,400 - 45,000) / 100 = 24 (treatment) and (50,000 - 45,000) / 100 = 50 (control). Both equal the independently derived values and `int(new_risk_headroom_pu(capacity, actual) x equity / price)`.
- A second subtraction of actual exposure would give 0 in both cases, which the test asserts the real quantity is not.
- Observation: production does not call `new_risk_headroom_pu` (it is referenced nowhere outside the controller module); the single subtraction is performed inside `cap_dispatch_entry` (`governor.py:259-262`). The arithmetic matches the helper, but the module docstring's "via new_risk_headroom_pu" does not describe the production path.

## Disabled compatibility and protection authority
- Disabled policy: `fleet_loading` telemetry is `None`, ECS authority unchanged, quantity equals the existing executor formula (50, positive). No independent safety cap was altered.
- Protection: on the real chain owned by the real engine, `plant_protection_tripped=True` gives `protection_tripped`, allowed capacity 0.0 and dispatch allocated 0. The test is intentionally narrow; existing protection tests are not replaced.

## Gate 10: counterfactual
`test_counterfactual_ignoring_fleet_output_removes_the_effect` patches `FleetLoadingController.update` with `monkeypatch` (test-local, auto-reverted) so its output is replaced by the capacity it was handed. Treatment quantity then equals control (50), so the key treatment assertion (quantity below control) would fail. No working-tree source was edited.

## Tests and results (venv `/home/srinivas/.venvs/zerodha-phase1-r5`; `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `-W ignore`)
| Command | Result |
|---|---|
| `pytest -q tests/test_r5_c02_fleet_actuation_integration.py` | 5 passed, 0 failed (0.91 s) |
| `pytest -q tests/test_fleet_loading_controller.py tests/test_fleet_loading_integration.py` | 80 passed, 0 failed |
| `pytest -q tests/test_revision5_plant_control.py tests/test_r5_paper_apply_blocker_closure.py tests/test_revision5_three_controller_parameterization.py` | 108 passed, 0 failed |
| `pytest -q tests/test_r5_d01_product_reconciliation.py` | 10 passed (D01 component PASS) |
| `pytest -q tests/test_r5_d01_orchestrator_run_integration.py` | 2 passed (D01.1 real `Orchestrator.run()` PASS) |
| focused group (the 19 files from the verification activity + `test_revision5_plant_control.py` + `test_r5_paper_apply_blocker_closure.py` + new test) | 354 passed, 0 failed, 254.36 s |
No broad regression was run. No 48-symbol replay. No live broker or API. No network.

## Limitations
- The test calls two private orchestrator methods (`_plant_control_shadow_step`, `_paper_plant_entry_limit`); it does not run `run()` end to end through order submission, and the position exit is modelled by removing the position from `open_trades`.
- The scenario depends on the plant being sampled once per timestamp; it does not show that Block 1 naturally binds (it did not).
- Values rely on the engine's start-of-run ECS ramp (demand 0.1), which is a real state of this engine but a narrow one.
- The fleet's lowered capacity here rests on exposure sampled before the exit (0.06) rather than the live exposure (0.045); that is the production behavior as written, recorded as an observation for the design review, not changed.
- The disabled-compatibility check is relative to the existing executor formula, not to a historical ledger.

## C02 status
CLOSED_VERIFIED for the stated scope (real fleet controller, real plant chain, real executor cap; same opportunity; binding fleet; quantity changed; exposure subtracted once; no source change). The natural-Block-1-binding question stays NOT_DEMONSTRATED and the design observations above go to the audit.
