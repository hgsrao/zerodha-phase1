# Activity audit: F-ROOT-012

Status: **VERIFIED_COMPONENT**. This is a scoped verification result, not whole-system acceptance.

## Change
Runtime contract and submission gate reject booleans, float/NaN/infinite/null and nonpositive quantities. Positive integer quantities still use configured size limits.

## Identity and isolated diff
Branch HEAD at start: `411712afaecaeb44a6899a12db94ac547fef0186`. Pre-existing dirty work was preserved. Source identities are recorded in [before.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-012/before.json) and [after.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-012/after.json).

Changed source files in this activity: runtime/operating_mode.py, runtime/contract_validator.py. Review [isolated.patch](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-012/isolated.patch), which excludes earlier changes.

## Reproduction and verification
Before fix: **6 failed, 6 passed in 0.03s**. [Reproduction log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-012/reproduction_before.log).

After fix: **39 passed in 3.82s**. [Verification log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-012/verification_after.log).

Command: `/home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q tests/test_r5_contract_order_quantity.py tests/test_revision5_engine_applicability.py`. Additional targeted reproductions are retained alongside these logs where noted in the scope.

## Scope and remaining gates
The submission gate previously also skipped validation for a present null quantity; that independently reproduced gap was repaired.

Full focused verification and exact real-fixture ledger parity are linked from the project execution report. Frozen parameters, sealed V3 and raw master/reference outputs were not edited. No live account was contacted, no Claude edit was authorized, and no commit/push was performed.

Activity dependencies and milestone acceptance remain explicit; a component result does not automatically release a prerequisite or certify production/live use.
