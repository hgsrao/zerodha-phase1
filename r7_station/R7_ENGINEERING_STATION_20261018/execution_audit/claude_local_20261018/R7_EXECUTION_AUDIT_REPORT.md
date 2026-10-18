# R7 Execution Audit Report

Audit date: 2026-10-18
Repository: `/home/srinivas/projects/zerodha-r6-no-adaptation`, branch `r7-governor-corrections`, HEAD `7c08ad586dd2854e36a0a80ddde6605e3a8b136a`
Working tree: 4 uncommitted modified files (diff sha256 `ff765ed2bd7e7577a16f60f6cdd7b494d0d67caa267b689ee9959aaac2b2882e`, unchanged at the end of the audit).
Companion files: `R7_EXECUTION_TEST_RESULTS.md` (commands and raw results), `R7_EXECUTION_EVIDENCE_MANIFEST.sha256`, `R7_PROPOSED_FIXES.patch`.
Evidence IDs (E10, E40, …) refer to the numeric prefix of files under `evidence/`.

**Status key:** PASS = the stated requirement was exercised by a command that ran and recorded output. FAIL = exercised and not met. BLOCKED = could not run because of a gate, a missing definition, or a missing capability. UNVERIFIED = not exercised, or only partly exercised.

## 1. Executive summary

1. **No production-code defect found in the R7 source.** The governor, allocator, FSM, features, AVR and checkpoint logic held under the 137 focused tests and the additional probes (E40–E43).
2. **One real defect, in a test file.** The uncommitted edit to `test_r7_engine_integration.py` broke `test_only_explicit_discretionary_native_exit_is_advisory` with a `NameError` and removed the native-path admission check from `test_core_without_native_plant_cannot_bypass_r7_candidate_admission` (F-01, E10). A patch is provided and verified in a scratch copy (137 / 137 pass, E11). It was **not** applied to the repo.
3. **Both replays are BLOCKED.**
   - The "fixed one-month dump replay" is not defined anywhere I could find.
   - The one-hour 48-symbol replay was not run because its preflight gate is red (F-01, F-05).
   - **Engine B/C runtime integration of R7 is therefore UNVERIFIED end to end.** The tests exercise the shared core orchestrator and only check that `r7_classes()` subclasses correctly.
4. **The Engine A golden master is red (17 / 17 scenarios), and it was already red at baseline `f0ad7af`.** It is not caused by R7, but it makes the Engine C regression run incomplete (F-05).
5. **The 48-symbol dataset verifies byte for byte** against its manifest: 48 / 48 files, and the manifest hash matches (E20).
6. **Decision-memory bound is real but narrower than it looks.** `r7_decisions` is capped at 1000. The native `controller_telemetry` still retains one full record per decision in `telemetry_mode="full"`, which the replay runners use (F-04, E42).
7. Nothing was run live. No credentials were touched. No replay was running or disturbed. The main repo was not modified.

## 2. Scope notes and sources of the checklists

- **25-point matrix.** The stable list R7-01…R7-25 is in `~/Documents/Codex/2026-10-16/…/outputs/r7_proposal/R7_REQUIREMENTS_AND_VERIFICATION.md`. It is not in the repository. The 57-point acceptance workbook in `R7_AUDIT_BUNDLE_20261018_v2/audit/` was read for context only; it was not re-audited.
- **11 governor parameters.** No standalone specification with source citations was found. The definition used is the repo's own `revision7_avr/test_r7_governor_parameters.py` (headers P1–P11) together with `revision7_avr/README.md`. "Exact source references" below are `file:line` anchors in the audited HEAD. An authoritative external citation for each parameter is UNVERIFIED because none exists to check against.
- **Prior audit.** `R7_AUDIT_REPORT_20261017.md` reported "36 passed" and "88 passed, 6 blocked by missing talib". Those limits came from a venv without `talib` and `backtrader`. This audit used a complete venv, so results are not comparable one for one.

## 3. 25-point audit matrix

| ID | Requirement (short) | Status | Evidence and notes |
|---|---|---|---|
| R7-01 | AVR, governor and coordinator connected; setpoint formed once | **PASS** | `r7_coordinated_controller.py:87-103` (`prepare`) and `:106-` (`commit`). Tests `test_p1_*`, `test_handoff_studies_once_signed_exposure_and_native_cap` (E10). |
| R7-02 | Real liquidity from prior baseline; bounded history | **PASS** | `r7_features.py` baseline taken before append. `test_volume_history_baseline_and_spike`, `test_zero_volume_degraded_and_bounded_history`. |
| R7-03 | Every completed bar processed, including warmup and non-candidates | **PASS** | `test_actual_native_engine_warmup_and_completed_bars`, `test_real_studies_called_once_even_when_no_candidates`. Core orchestrator with synthetic bars only. |
| R7-04 | Signed notional normalised by symbol budget; overrun not clamped | **PASS** | `r7_engine.py:99-105`. `test_engine_actual_exposure_unclamped`, `test_p4_*`. The `equity <= 0` raise (`:101`) was verified by reading only. |
| R7-05 | Side-aware entries; short policy preserved | **PASS** | `r7_engine.py:344-361`. `test_native_short_candidate_and_explicit_short_policy`. |
| R7-06 | Config affects runtime; hold and cooldown independent | **PASS** | `test_release_threshold_and_hold_independent_of_cooldown`, `test_p9_*`, `test_cooldown_counts_release_bar[1,2,3]`. |
| R7-07 | One exposure-tracking controller; native Mark V authority kept | **PASS** | `r7_market_governor.py:22-`. `test_p6_*`, `test_p7_*`, and the normal-exit tests (protective and unknown exits stay native). |
| R7-08 | Governor checkpoint complete; atomic restore | **PASS** | `test_invalid_restore_atomic` ×5 and my five mutation probes, all atomic (E40). Config is compared by equality rather than by a hash fingerprint, which is functionally equivalent. Minor gap F-06. |
| R7-09 | Market AVR uses the real PI API | **PASS** | `r7_market_avr.py:10`. `test_actual_r6_pi_keyword_api_and_tuple`, `test_avr_bounded_convergent_and_bad_data_decay`. R6 suite 121 passed (E30). |
| R7-10 | Independent symbol state; bay totals reconcile | **PASS** | `test_all_five_bays_and_unmapped_symbol`, `test_engine_atomic_checkpoint_includes_bays_and_physical`. |
| R7-11 | Pulse reversal and emergency policy; no deadlock | **PASS** | Fuzz: 1,588 flips, 0 violations (E40). Alternating ±0.35 yields no direct flip (E41, E11 test). Residual note F-03. |
| R7-12 | Pulse age, cooldown and reset boundaries | **PASS** | `test_pulse_visible_for_exactly_hold_bars[1,2,4]`, `test_cooldown_counts_release_bar`, `test_max_pulse_age_release_when_unsupported`. |
| R7-13 | Release thresholds executable; no calibration claim | **PASS** | `test_release_threshold…`, `test_hysteresis_and_invalid_input`. Thresholds remain provisional (`r7_state.py:52-56`). Whether the hunting seen in historical replays has gone is **UNVERIFIED**, because the replays are blocked. |
| R7-14 | Same-bar idempotency; conflicts raise | **PASS** | `test_same_bar_immutable_context_and_no_double_integration`, `test_same_bar_conflict_and_gap`, probes (E40). |
| R7-15 | Unmapped and duplicate symbols rejected | **PASS** | `r7_engine.py:45-50`. `test_all_five_bays_and_unmapped_symbol`. The duplicate-symbol branch was verified by reading only. |
| R7-16 | Causal OHLCV features; invalid input rejected | **PASS** | `test_features_causal_and_distinct`, `test_bad_ohlcv_rejected[×3]`, and my prefix-invariance probe (E40). |
| R7-17 | Signed studies consumed once; Nifty provenance and freshness | **UNVERIFIED** | Studies are consumed once (`test_real_studies_called_once…`). Missing grid is visible and fail-closed: 0 eligible, 0 pulses over 200 bars (E43). **Freshness** is delegated to the native grid provider and was not exercised. |
| R7-18 | Target and actual in one unit; no unapproved droop formula | **PASS** | `test_p4_*`, `test_p5_*`, `test_p2_governor_and_controller_take_no_price_speed_input`. |
| R7-19 | Native safety, sizing and reservations preserved | **UNVERIFIED** | R7 can only reduce quantity (`r7_engine.py:361`, `max(0, min(int(quantity), native))`). Quantity delegation is tested. **Ledger reconciliation** against a native Engine B/C run was not executed (replay blocked). The README says the same. |
| R7-20 | Five-bay topology and budgets; subset invariance | **PASS** | Registry check: 48 symbols, bays 14/6/11/12/5, budgets sum to 1.0. `test_p3_budgets_from_registry_and_subset_does_not_reallocate`. Physical R6 units fixed at R = 0.04 (`test_five_physical_units_have_fixed_r_and_real_swing`). Spec drift F-09. |
| R7-21 | Rejection and admission reasons distinguishable | **PASS** | `r7_engine.py:344-381` counters (`REJECTED_BY_R7_PULSE`, `REJECTED_BEFORE_NATIVE_ADMISSION`, …). E43 shows them populated. Reconciliation over a full run is UNVERIFIED. |
| R7-22 | Causal warmup before the first candidate | **PASS** | `test_actual_native_engine_warmup_and_completed_bars`. |
| R7-23 | Native protective exits remain effective | **PASS** | `test_r7_normal_exits.py` (23 cases): hard limits, stop gap precedence, 13 protective and unknown reasons stay native, and only `governor_exit:GOVERNOR_Z_EXIT` is advisory. Z_EXIT source confirmed at `revision5/governor_authority.py:333,393` and `revision2_external/orchestrator.py:1379`. |
| R7-24 | Engine checkpoint complete; restart-equivalent | **PASS (R7 state only)** | `r7_snapshot` / `r7_restore` (`r7_engine.py:501-546`). `test_restore_continues_decision_chain`, `test_checkpoint_size_bounded_and_external_log_complete`, `test_checkpoint_restore_reproduces_future_decisions`, my continuation probe. Native journal persistence is not implemented (README) and is listed as **B-01** below. |
| R7-25 | Dedicated tests plus harness replay; synthetic is not native evidence | **BLOCKED** | Tests ran and are recorded (E10, E11). The harness replay is BLOCKED (see §6). |

Tally: 22 PASS (R7-24 is PASS for R7 state only), 2 UNVERIFIED (R7-17, R7-19), 1 BLOCKED (R7-25), 0 FAIL in production behaviour.

## 4. Eleven governor parameters

The status for each parameter applies to the audited HEAD plus the uncommitted diff. Tests are in `test_r7_governor_parameters.py`.

| # | Parameter | Source anchors (audited tree) | Status | Evidence |
|---|---|---|---|---|
| P1 | Reference `E_ref = clamp(0.5·AVR + 0.3·study + 0.2·dispatch)` | `r7_coordinated_controller.py:101`; weights `r7_state.py:61-63`; sum-to-one check `:85-86` | **PASS** | `test_p1_*` |
| P2 | Common measurement U = filled gross / authorised limit; not clamped; pending counted; no price-speed input | `r7_allocator.py:25-47`; `r7_engine.py:107-128,154-171` | **PASS** | `test_p2_*` ×3 |
| P3 | Settable droop R (0.02–0.10), registry bay values, weights `w = B/R`, water-filling | `r7_state.py:25,41,73,89-99`; `r7_allocator.py:51-56,73-101` | **PASS** | `test_p3_*`; registry values re-read (0.04, 0.04, 0.055, 0.055, 0.075); allocator fuzz (E40) |
| P4 | Actual exposure preserved, error signed | `r7_market_governor.py:30` | **PASS** | `test_p4_*` |
| P5 | Error is an exposure adjustment | `r7_market_governor.py:30-32` | **PASS** | `test_p5_*` |
| P6 | Kp independent of R | `r7_state.py:45` | **PASS** | `test_p6_kp_independent_of_droop` |
| P7 | Integral conditionally anti-windup; frozen while a native order is working | `r7_market_governor.py:39,47-55`; `r7_engine.py:173-175` | **PASS** | `test_p7_*` ×2, `test_engine_records_allocation_and_freezes_on_working_order` |
| P8 | Derivative on measurement; optional filter; validation profile Kd = 0 | `r7_market_governor.py:33-38`; `r7_state.py:47-48,102` | **PASS** | `test_p8_*` ×3 |
| P9 | Slew in seconds; overnight counts as one bar | `r7_market_governor.py:47-48,56-58`; `r7_features.py:31-33` | **PASS** | `test_p9_*` ×2 |
| P10 | Response lag monotone and bounded | `r7_market_governor.py:59` | **PASS** | `test_p10_*` |
| P11 | No swing equation in the trading governor | `r7_market_governor.py:1-8` | **PASS** | `test_p11_*` |

Exact external source citations for each parameter: **UNVERIFIED** (no citable spec exists in the repo or the bundles). The README's reference to `revision5/governor_authority.py` for the discretionary exit was verified.

## 5. AVR requirements and mappings

| Item | Status | Evidence |
|---|---|---|
| Market AVR is a closed loop through an internal participation model | **PASS** | `r7_market_avr.py:13-24`; `test_avr_bounded_convergent_and_bad_data_decay` |
| Uses the actual R6 `PositionalPI` (keyword `step`, returns a tuple) | **PASS** | `r7_market_avr.py:10`, `r6_avr_core.py:75`; `test_actual_r6_pi_keyword_api_and_tuple` |
| Degraded, gap or not-ready bars decay state instead of integrating | **PASS** | `r7_market_avr.py:14-18`; same test |
| R6 physical AVR and turbine preserved | **PASS** | R6 suite: 121 passed, 1 strict xfail (documented OPEN limit cycle at dt = 3 s) (E30) |
| R5/R6/Engine B/C sources unchanged since baseline `f0ad7af` | **PASS** | `git diff f0ad7af HEAD -- revision5 revision6_avr engine_b engine_c revision2_external` is empty. This compares committed state only. The 4 uncommitted files are all in `revision7_avr/`. |
| Market AVR has no Q-V droop; the 8 % AVR droop is not in the R7 market AVR | **UNVERIFIED** | Taken from the 57-point workbook (M09). I did not locate an independent source. |

## 6. Findings

| ID | Severity | Class | Finding | Evidence | Action |
|---|---|---|---|---|---|
| F-01 | Medium | FAIL (test integrity) | The uncommitted edit split `test_core_without_native_plant_cannot_bypass_r7_candidate_admission`. The orphaned tail is now inside `test_only_explicit_discretionary_native_exit_is_advisory`, causing `NameError`. The original test no longer checks that the native path is reached once the pulse is valid. | E10, E11 | `R7_PROPOSED_FIXES.patch` moves the five lines back. Verified: 137 / 137. |
| F-02 | Low | Process | The uncommitted `r7_engine.py` changes (`r7_classes()` factory, `R7_DECISION_MEMORY_LIMIT`) and the integration-test changes are not in the prior audit's "Files changed" list and not in the README. The README also does not mention the two-sample emergency debounce. | `00_uncommitted.diff` | Record them, or commit with a message that says so. |
| F-03 | Low | Observation | Direct emergency reversal is reachable only in a pulse's first `hold_bars` bars. After that, an emergency sample hits the `release` branch first, so the path is BUY→HOLD→SELL, with one HOLD bar. The old docstring said "flips immediately". | E41 | The patch corrects the docstring. Whether the delay is acceptable is a design decision. |
| F-04 | Medium | FAIL (resource bound) | `r7_decisions` is bounded at 1000. In `telemetry_mode="full"`, which both `engine_b/runner.py:30` and `engine_c/runner.py:48` use, every decision is also appended to `controller_telemetry` as an `R7_COMPLETED_BAR` event. The probe retained 389 of 389 with `r7_decision_memory=5`. Memory grows with replay length, so a multi-year 48-symbol run cannot be bounded. | E42, `revision2_external/orchestrator.py:1690-1699` | Not patched. It is a design choice, and `run_r7_replay._extended` reads those events. Options: emit the full record only to the JSONL log, or add a compact event. |
| F-05 | Medium | FAIL (pre-existing) | The Engine A golden master mismatches in 17 of 17 scenarios (sections: trades, controller_telemetry, mtm_equity_curve, entry_candidate_observations, position_sizing_events). The one scenario run on pristine baseline `f0ad7af` mismatches identically. Cause not investigated. The environment (numpy 2.5.2, pandas 2.3.3) is the pinned set. | `golden/*.txt`, E34 | Triage separately. It is the regression oracle for the Engine B extraction. It makes the Engine C test file's last test unable to pass. |
| F-06 | Low | FAIL (hardening) | `SymbolController.restore` has no upper bound on `emergency_age` (`10**9` accepted), unlike `active_age` and `hold_age`. Harmless to behaviour. | E40 | Optional one-line bound. Not included in the patch. |
| F-07 | Low | Observation | `LIVE_TRADING_ENABLED` was the string `False` in the environment but is not read by any R7 or Engine B/C code. The safety property rests on no live client being constructed. Engine B/C `place_order` is `_refuse`. `revision2_external/broker_adapter_kite.py` exists and imports `KiteConnect`, but nothing in Engine B/C or R7 references it. | session scan | Consider an explicit assertion at engine construction. |
| F-08 | Low | Documentation | `file:line` anchors in the 57-point matrix are stale. For example M03 points to `r7_engine.py:289`, which is now `r7_close_decision_log`. Anchors in §3–§4 of this report are current. | session | Refresh the workbook. |
| F-09 | Medium | Spec drift | The 2026-10-16 requirements record lists "Fixed operator-set droop R = 0.04 for every bay" as non-negotiable. The implemented allocation uses per-bay registry droop (0.04/0.04/0.055/0.055/0.075) as sharing weights, and P3 makes R settable. Physical R6 units stay at 0.04. This may be an authorised later decision, but I found no written sign-off. | `r7_state.py:25-41,89-99`; requirements doc | Operator to confirm. |
| F-10 | Medium | UNVERIFIED | The tests drive `Revision7Engine` (shared core with test seams). They do not construct `R7EngineBOrchestrator` or `R7EngineCOrchestrator` and run a market. Integration with Nautilus (B) and Backtrader (C) is untested here. | `12_r7_test_inventory.txt` | Needs the blocked replay. |

## 7. Additional boundaries (outside the 25 / 11)

| ID | Item | Status | Note |
|---|---|---|---|
| B-01 | Native crash-journal persistence of the R7 checkpoint | **BLOCKED** | Not implemented. The README says it is not certified. |
| B-02 | Pending fills and partial-fill reservations | **PASS** (unit) | `test_08`, `test_09`, `test_engine_b_pending_entry_counts_conservatively`. Not verified against Nautilus or Backtrader order flow. |
| B-03 | Fail-closed on missing price or measurement | **PASS** | `test_10_missing_valuation_price_fails_closed`, `test_engine_b_pending_entry_without_valid_price_fails_closed`, `test_fail_closed_still_allows_reduction`. |
| B-04 | Allocator bounds and order independence | **PASS** | `test_15`, plus the 3,000-case fuzz (E40). |
| B-05 | Idempotency, causality | **PASS** | Tests plus probes (E40). |
| B-06 | Dataset, 48 symbols | **PASS** | E20 |
| B-07 | Dependencies and environment | **PASS** | E50; all 16 pins match. System Python 3.14 is unusable, so the venv must be named explicitly. |
| B-08 | Fixed one-month dump replay | **BLOCKED** | No definition found. |
| B-09 | One-hour 48-symbol replay | **BLOCKED** | Gate not satisfied (F-01, F-05). Command is in the results file. |
| B-10 | Engine B regression | **PASS** with caveat | 20 passed. The 1 failure is a snapshot artifact and its equivalent real-repo check is clean (E31). |
| B-11 | Engine C regression | **UNVERIFIED** | 49 passed, 1 skipped, 1 test killed at the 8-minute cap (E32, E33). |

## 8. Decisions needed from you

1. **Apply `R7_PROPOSED_FIXES.patch`?** It touches only `test_r7_engine_integration.py` and a docstring in `r7_coordinated_controller.py`. It applies cleanly (`patch --dry-run`). I did not apply it.
2. **Is the red Engine A golden master a gate for the R7 replay?** It fails identically on the pre-R7 baseline. If it is not a gate, the one-hour replay can run right after the patch is applied.
3. **What is the "fixed one-month dump replay"?** I need the file, date range or command.
4. **F-04:** should R7 stop mirroring full decisions into the native telemetry list?
5. **F-09:** confirm that per-bay registry droop supersedes the fixed R = 0.04 rule for allocation.
