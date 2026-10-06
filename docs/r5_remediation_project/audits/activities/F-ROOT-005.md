# Activity audit: F-ROOT-005

Status: **VERIFIED_PARTIAL**. This is a scoped verification result, not whole-system acceptance.

## Change
Close-fill ledger and in-memory removal now precede durable close; persistence failures halt entries and propagate. Outcome telemetry follows authoritative feedback.

## Identity and isolated diff
Branch HEAD at start: `411712afaecaeb44a6899a12db94ac547fef0186`. Pre-existing dirty work was preserved. Source identities are recorded in [before.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-005/before.json) and [after.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-005/after.json).

Changed source files in this activity: revision2_external/orchestrator.py. Review [isolated.patch](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-005/isolated.patch), which excludes earlier changes.

## Reproduction and verification
Before fix: **1 failed in 0.67s**. [Reproduction log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-005/reproduction_before.log).

After fix: **8 passed, 64 warnings in 9.72s**. [Verification log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-005/verification_after.log).

Command: `/home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q tests/test_r5_close_persistence_fault.py tests/test_r5_d02_product_reentry.py tests/test_r5_d01_orchestrator_run_integration.py`. Additional targeted reproductions are retained alongside these logs where noted in the scope.

## Scope and remaining gates
A disk failure can leave the durable row stale. Full transaction/recovery acceptance remains open; the receipt explicitly records this limit.

Full focused verification and exact real-fixture ledger parity are linked from the project execution report. Frozen parameters, sealed V3 and raw master/reference outputs were not edited. No live account was contacted, no Claude edit was authorized, and no commit/push was performed.

Activity dependencies and milestone acceptance remain explicit; a component result does not automatically release a prerequisite or certify production/live use.
