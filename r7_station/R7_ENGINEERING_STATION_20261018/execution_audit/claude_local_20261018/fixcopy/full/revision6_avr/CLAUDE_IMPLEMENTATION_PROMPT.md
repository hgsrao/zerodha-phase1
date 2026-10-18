# CLAUDE — IMPLEMENT PROVIDED R6 AVR CODE, DO NOT REINVENT ITS CONTROL LOGIC

You are receiving a CODE PACKAGE with `r6_avr_core.py`, `test_r6_avr_core.py`, `run_r6_avr_bench.py`, and this instruction file. The source code is authoritative for **the standalone prototype**; inspect it critically, preserve its intended contracts, and report any verified safety flaw rather than silently inventing alternate algorithms. Do not claim production qualification on the strength of these unit tests.

Repository: `/home/srinivas/projects/zerodha-r5-parameter-authority` (preserved at `63dee10`). The forensic no-adaptation worktree was reported as `/home/srinivas/projects/zerodha-r6-no-adaptation` at `459a085`. First verify both paths, branches, statuses and commits. **Never modify the preserved original branch.**

## Mandatory implementation boundaries

1. Copy the delivered source and its tests unchanged into an isolated, clearly named R6 development module/worktree; run standalone tests first. Preserve sha256 of each supplied file so the delivered implementation stays auditable.
2. Do NOT replace `BayExcitationAVR` in `revision5/ccpp_protection_cubicles.py`, change existing Mark V/GTG/CSTG governor code, modify B/C execution peripherals, or alter sealed risk and protection contracts.
3. Do NOT import/use `FullR5Adaptation` in the new isolated R6 harness. Do not restore the global pressure adaptation bundle. Use explicit local registry-owned parameters for AVR and approved exposure setpoints; no hidden defaults from mutable global pressure.
4. Understand the TWO DIFFERENT modules:
   - `VoltageAVR`: genuine independently modeled/measured voltage + field feedback, optional **real signed normalized Q**, physical-equivalent engineering bench. No OHLC/NIFTY/churn masquerading as voltage or MVAR.
   - `ExposureAVR`: financial control from actual fills and acknowledged outstanding order notional (per bay, all symbols). No physical voltage assumption. The outer fill-utilization PI and inner committed-utilization PI are independently sampled and both respond to actual execution-state changes.
5. `UnitMaster` must receive a correctly converted Mark V `Vote`, the AVR `Vote`, and the independent safety permit **from the same causal bar, bay, symbol and position state**. Do not assume Mark V's native governor return matches `Vote`: write a thin explicit validated adapter and record every field conversion. Both voters must use agreed total-held-quantity semantics.
6. Do not allow ENTRY unless governor, AVR and safety explicitly permit. Do not interpret HOLD as ENTRY. Authorized EXIT and mandatory TRIP are unilateral. REDUCE quantity means shares to CLOSE, not remaining shares. Pass intent only to the existing final order/execution authority, with deterministic idempotent ledger and partial-fill logic.
7. Apply commercial minimum notional once, **after** all authorized maximum position constraints. Never increase quantity to overcome the minimum. Keep physical UEL/OEL separate from the financial commercial lockout. Preserve MiCOM, SEL300G, ANSI27/59 and other hard protections outside the discretionary AVR capacity controller.
8. Preserve measured data provenance, bar monotonicity, trip latch, authorized reset and checkpoint state; session transition must not quietly clear protection history. Test checkpoint/replay equality including execution state.
9. Experimental Q droop: GTG1/GTG2 7.5%, CSTG1/CSTG2/BPSTG 10%. Compare 0%, 3–5%, 7.5% and 10% on deterministic plant bench before approving any default. Do not treat 4% turbine governor speed droop as equivalent to AVR Q droop.
10. Never activate network order routing/Kite or live trading. Environment: `LIVE_TRADING_ENABLED=False`, `EA1_SHADOW_MODE=True`. Offline historical replay only.

## Work phases

**Phase A — Copy and verify.** Check the supplied code and unit tests; run `python -m unittest -v test_r6_avr_core.py` and the bench. Inspect every existing interface before making adapter edits. Document assumptions about quantities, statuses, timestamps, bay funds, order acknowledgment and ledger provenance.

**Phase B — Isolated R6 adapter/harness.** Create a separate offline R6 runner. Wire existing controller return schemas through an adapter; do not overwrite existing controller implementation. Controller state must advance exactly once per portfolio timestamp. The same snapshot must reach both governor and AVR. Ensure a filled share enters subsequent utilization measurements only via real fill hooks. An unacknowledged/unknown pending order is NOT an accepted order and should cause an explicit hold, not additional capacity.

**Phase C — Bench and unit proof.** Validate step response, stable-state convergence, measurement loss, Q polarity, droop, saturation, rate limits, anti-windup, mandatory trip and recovery, no duplicate intents, and persistence/restart. Preserve old golden R5 behavior in the legacy path.

**Phase D — Replay integration.** Reproduce frozen data controls and the INFY SELL signal on 2026-08-06 at 13:25. Compare pre-adaptation baseline, old R6 with full adaptation ON, and isolated R6 implementation. Use Engines B/C, actual order/partial-fill/exit records, independent ledger checks and meaningful risk budgets. Do not force trades, bypass protection, or claim success from 0-execution accounting. Explain any order/P&L differences and reconcile them against timestamps, slippage and fees.

**Phase E — Review gate.** Return a patch diff, versioned schema contracts, tests with counts and exact command output, baseline golden comparison, checkpoint-restart proofs, risk mapping and explicit blockers. **Stop before integrating into the production branch.**

## Critical safety caveat

The code is **tested here only as a standalone implementation**. It is not production-qualified and there are still integration-specific concerns (e.g., external partial order acknowledgments, actual ledger event taxonomy, correct conservative behavior on uncertain fill states, and portfolio-consistent risk allocations). If the delivered code contains a reproducible defect, fix it in an isolated development copy with a minimal test and send a diff for approval; never silently substitute Gemini's prior single-PID formulation or modify the safety envelope.
