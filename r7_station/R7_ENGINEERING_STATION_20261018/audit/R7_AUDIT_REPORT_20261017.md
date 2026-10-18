# R7 engineering audit

Date: 2026-10-17  
Repository: `/home/srinivas/projects/zerodha-r6-no-adaptation`  
Branch/commit: `r7-governor-corrections` / `7c08ad586dd2854e36a0a80ddde6605e3a8b136a`  
Live trading: `LIVE_TRADING_ENABLED=False`

## Result

The supplied source archive and controller diagnostic match the clean repository at the requested commit. The isolated alternating `+0.35/-0.35` diagnostic is a real FSM safety defect: it produces 11 direct opposing pulse flips in 12 bars because emergency reversal was immediate. The governor diagnostic is not evidence of saturation by itself. `1.08` is the raw target-plus-proportional request; the applied output is `0.23608160417241994` after slew and first-order response lag.

## Change made

`revision7_avr/r7_coordinated_controller.py` now requires two consecutive opposing emergency samples before a pulse reversal. The pending confirmation is cleared on a non-opposing sample, ineligible input, release, or completed reversal. Native protective exits remain outside this FSM and are not delayed or withdrawn. Restore accepts both old three-field pulse checkpoints and the new checkpoint state.

Tests added in `revision7_avr/test_r7_pulse_fsm.py` cover alternating emergency inputs, sustained emergency reversal, and the existing symmetric reversal contract.

## Evidence

- Focused R7 FSM, governor, market AVR, feature, and checkpoint tests: **36 passed**.
- Extended dependency-backed subset: **88 passed, 6 blocked by missing `talib`**, with no failures attributable to the change.
- Full R7/R6 collection was not claimable because the environment lacks `talib` and `backtrader`; the repository test suite was not modified to hide those imports.
- The existing acceptance matrix at `/home/srinivas/Desktop/R7_Final_Acceptance/R7_57_requirement_acceptance.xlsx` and its Markdown export report 55 PASS / 2 BLOCKED before this turn. The two blocked items are native journal checkpoint integration and Gate C’s approved 20-session comparison. Those remain unclosed because no fresh evidence was produced.
- The existing replay summaries are historical artifacts from 2026-08-13 and were not treated as fresh results. No running replay was disturbed and no new full replay was started.

## Requirement disposition

The R7 allocator, pending-entry reservation, fail-closed price handling, native protective-exit authority, 11 governor parameter definitions, and AVR/R6 preservation were inspected against the current source and existing tests. No changes were made to R5/R6/native risk, frozen data, or live-order paths.

The 57-point matrix should retain PASS/RESIDUAL/BLOCKED states until the missing native journal integration and Gate C replay evidence are executed in an environment with the required native dependencies. The FSM item should be updated from “possible hunting” to “mitigated in isolated alternating-emergency test; historical replay revalidation pending.”

## Files changed

- `revision7_avr/r7_coordinated_controller.py`
- `revision7_avr/test_r7_pulse_fsm.py`

No commit was created.
