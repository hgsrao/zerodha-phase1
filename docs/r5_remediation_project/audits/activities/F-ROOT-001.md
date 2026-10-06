# Activity audit: F-ROOT-001

Status: **VERIFIED_REPLAY_FIXTURE**. This is a scoped verification result, not whole-system acceptance.

## Change
Real fills are recorded with actual quantity, lifecycle and protective state before post-fill/research/dynamics/bridge observers. Unexpected faults propagate and halt entries; held positions remain closable.

## Identity and isolated diff
Branch HEAD at start: `411712afaecaeb44a6899a12db94ac547fef0186`. Pre-existing dirty work was preserved. Source identities are recorded in [before.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-001/before.json) and [after.json](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-001/after.json).

Changed source files in this activity: revision2_external/orchestrator.py. Review [isolated.patch](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-001/isolated.patch), which excludes earlier changes.

## Reproduction and verification
Before fix: **4 failed, 8 warnings in 1.62s**. [Reproduction log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-001/reproduction_before.log).

After fix: **11 passed, 72 warnings in 10.85s**. [Verification log](/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/r5_remediation/activities/F-ROOT-001/verification_after.log).

Command: `/home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q tests/test_r5_post_fill_observer_faults.py tests/test_r5_d01_orchestrator_run_integration.py tests/test_r5_d02_product_reentry.py`. Additional targeted reproductions are retained alongside these logs where noted in the scope.

## Scope and remaining gates
Four fault injections are applied to a real TITAN one-session replay. Exact no-fault trade-ledger/P&L parity is independently verified, not full 48-symbol Block 1 parity.

Full focused verification and exact real-fixture ledger parity are linked from the project execution report. Frozen parameters, sealed V3 and raw master/reference outputs were not edited. No live account was contacted, no Claude edit was authorized, and no commit/push was performed.

Activity dependencies and milestone acceptance remain explicit; a component result does not automatically release a prerequisite or certify production/live use.
