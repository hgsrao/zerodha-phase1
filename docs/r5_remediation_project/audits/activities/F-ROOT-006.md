# Activity audit: F-ROOT-006

Status: **VERIFIED_COMPONENT**. This is a scoped verification result, not whole-system acceptance.

## Change
Both ordinary A square-off and unresolved-transfer square-off now compare Asia/Kolkata time, preserving the instant of UTC-aware inputs.

## Identity and isolated diff
Branch HEAD at start: `411712afaecaeb44a6899a12db94ac547fef0186`. Pre-existing dirty work was preserved. Source identities are recorded in [before.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-006/before.json) and [after.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-006/after.json).

Changed source files in this activity: revision2_external/orchestrator.py, revision5/combined_cycle_runtime.py. Review [isolated.patch](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-006/isolated.patch), which excludes earlier changes.

## Reproduction and verification
Before fix: **2 failed, 2 passed in 0.70s**. [Reproduction log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-006/reproduction_before.log).

After fix: **17 passed, 32 warnings in 4.96s**. [Verification log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-006/verification_after.log).

Command: `/home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q tests/test_r5_squareoff_exchange_clock.py tests/test_r5_unknown_conversion_protection.py tests/test_r5_d01_orchestrator_run_integration.py`. Additional targeted reproductions are retained alongside these logs where noted in the scope.

## Scope and remaining gates
No cutoff value changed. Native plant intent remains separate from the external runner cutoff.

Full focused verification and exact real-fixture ledger parity are linked from the project execution report. Frozen parameters, sealed V3 and raw master/reference outputs were not edited. No live account was contacted, no Claude edit was authorized, and no commit/push was performed.

Activity dependencies and milestone acceptance remain explicit; a component result does not automatically release a prerequisite or certify production/live use.
