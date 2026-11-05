# R7 validation report — 2026-11-05

## 1. Verdict

| Question | Answer |
|---|---|
| Did the fixed one-month dump replay (Test 1) pass? | **No. BLOCKED.** It never ran on historical data, because the frozen 48-symbol dataset is not in this environment. |
| Did the one-hour adaptive replay (Test 2) pass? | **No. BLOCKED** for the same reason. The adaptation path it needed did not exist at `8af68c0`. It has now been implemented and tested in code (§5). |
| Is further paper testing justified? | **Yes, as the next step.** Run Test 1 and Test 2 on the frozen data with the harnesses here. Not before. |
| Do code or dependency issues still block? | **Data only.** No Python dependency is missing. The code blocker for Test 2 is resolved in this change. |

No result in this report comes from fallback data. Synthetic runs are labelled `SYNTHETIC_HARNESS_SMOKE_NOT_EVIDENCE`. They prove only that a harness executes end to end.

## 2. What was tested

| Item | Value |
|---|---|
| Requested | branch `r7-governor-corrections`, commit `7c08ad5` |
| Found on origin | branch `r7-governor-corrections` does not exist. `7c08ad5` is on `r7-engineering-station`. |
| Code under test | `r7-engineering-station` @ `8af68c0a2488478992378b0214e80f0e04d76d30`, plus this change. `7c08ad5` is an ancestor. |
| Why not `7c08ad5` alone | The repair set (FSM anti-churn, allocator bounds, restore atomicity, grid freshness, ledger reconciliation, launcher guard, adaptation guard) and the R5 adapter were committed after it, in `0f1da68`, `af96b8e` and `8af68c0`. |
| Live trading | `LIVE_TRADING_ENABLED=False`. Every launcher refuses to start otherwise. Verified with the variable unset and set to `True` (exit 78, "No engine was constructed"). No broker client was constructed. No Kite connection was made. |
| Environment | Python 3.13.16, nautilus_trader 1.231.0, backtrader 1.9.78.123, numpy 2.5.2, pandas 2.3.3, pandera 0.33.1, TA-Lib 0.7.1, pytest 9.2.0. These match the repo pins (`requirements_external_engine.txt`, `engine_c/requirements_engine_c.txt`). |
| Identity file | `IDENTITY.txt` |

### Dataset (required, not present)

| Field | Value |
|---|---|
| Manifest | `revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json`, file sha256 `2dcb04eb74fc2abec77a838b20dfb5ce6d730c10ba359203a7fc045eccffef66` |
| `manifest_hash` | `f724074ca45eb9380ba6e7cf18ae1da4d9c9d48be0cf78ca51b0faa8d8d8ff16` |
| Symbols | 48. The list is in `IDENTITY.txt`. |
| Range | 2023-07-03 09:15 to 2026-08-24 15:14 IST, about 291,137 rows per symbol, 816,519,056 bytes in total |
| Expected location | `/home/srinivas/projects/zerodha-phase1/data/frozen_48_1min_20230703_20260824/` |
| Grid manifest | `/home/srinivas/projects/zerodha-phase1/local_workspace/records/grid-manifest-local.json` (not present) |
| Result here | Manifest verification: `0 mismatched, 48 missing`. Every launcher refused (logs in `test*/blocked_attempts_missing_data/`). |

## 3. Preflight

All suites below ran against the final tree (`8af68c0` plus this change) with `LIVE_TRADING_ENABLED=False`. JUnit XML and logs are in `preflight_final/`. The `preflight/` directory holds the earlier run on unmodified `8af68c0`: R7 248 passed, launcher guard 72 passed, Engine B 21 passed.

| Required preflight | Suite / tests | Result | Status |
|---|---|---|---|
| Focused R7 tests | `revision7_avr/` (17 files plus the new `test_r7_governor_adaptation.py`) | part of 330 passed (with the guard suite) | **PASS** |
| FSM anti-churn | `test_r7_fsm_antichurn.py` (19), `test_r7_pulse_fsm.py` (14) | passed | **PASS** |
| Governor and allocator | `test_r7_governor_parameters.py` (50), `test_r7_market_governor.py` (6), `test_r7_demand_allocation.py` (18), `test_r7_allocator_bounds.py` (9) | passed | **PASS** |
| Pending-entry and invalid-price fail-closed | `test_engine_b_pending_entry_counts_conservatively`, `test_engine_b_pending_entry_without_valid_price_fails_closed`, `test_fail_closed_still_allows_reduction`, `test_10_missing_valuation_price_fails_closed` | passed | **PASS** (unit) |
| Native protective exits | `test_r7_normal_exits.py` (23) | passed | **PASS** (unit) |
| Causality and same-bar idempotency | `test_r7_features.py`, `test_r7_grid_freshness.py` (18), same-bar tests in `test_r7_market_governor.py` | passed | **PASS** (unit) |
| Checkpoint / restore | `test_r7_checkpoint.py` (6), `test_r7_restore_atomicity.py` (21), adaptive round trip in `test_r7_governor_adaptation.py` | passed | **PASS** (R7 state only) |
| Live-trading launcher guard | `tests/test_launcher_live_trading_guard.py` (72) | passed | **PASS** |
| R6 AVR / turbine (AVR preservation) | `revision6_avr/` | 121 passed, 1 xfailed (documented strict xfail: open limit cycle at dt = 3 s) | **PASS** |
| Engine B integration | `tests_engine_b/` | {{ENGINE_B}} | {{ENGINE_B_STATUS}} |
| Engine C integration | `tests_engine_c/` | {{ENGINE_C}} | {{ENGINE_C_STATUS}} |
| R5 / shared-engine regression | `tests/` (excluding `legacy_quarantine`) | {{R5}} | {{R5_STATUS}} |
| Missing dependencies | — | None. Missing **data**: the frozen dataset and the grid manifest. | reported |

## 4. Test 1: fixed one-month dump replay, adaptation off

**Status: BLOCKED (missing frozen data).** It answers nothing about the question "Does the fixed-parameter one-month dump replay remain deterministic and regression-safe?" on historical data.

What is ready:

- `revision7_avr/run_r7_replay.py` now drives **Engine A** as well as B and C, in both `native` and `r7` modes. `--r7-adaptation` defaults to `off`, so all six required runs are available. Engine A is the shared core orchestrator, built exactly as `scripts/run_engine_a_reference.py` builds it.
- Engine A harness smoke on synthetic bars (`test1_fixed_month_dump/harness_smoke_synthetic_NOT_EVIDENCE/`):
  - native and r7 runs both completed;
  - 5 entries, 5 exits and 5 fills each;
  - identical trade-ledger hash `8ea2e899…`;
  - 9,552 R7 decisions logged.
  - It is not evidence.
- **Regression safety of this change with adaptation off** (`test1_fixed_month_dump/off_mode_regression/`): one script ran the same 4-symbol, 200-bar PAPER_APPLY engine on unmodified `8af68c0` and on the final tree. The complete output was byte-identical, sha256 `1b095898…` for both. It covers the decision count (796), the rolling decision digest `88c8718f…`, all gate counters and the order counts. This shows the adaptation code does not alter fixed-parameter behaviour. The synthetic run placed no orders, so it covers the decision path, not fills.

Command to run Test 1 once the data is present:

```
LIVE_TRADING_ENABLED=False PYTHONPATH=. python revision7_avr/run_r7_replay.py {A|B|C} {native|r7} \
    --symbols ALL --bars <warmup + one month of minutes> --warmup 60 --data-dir <frozen dir> --out <dir>
```

Run time: Engine A native on 48 symbols × 200 synthetic bars took 436 s here. A month is about 7,900 bars per symbol, roughly 40 times that, so plan on several hours per engine-mode. The 2026-10-18 handover also recorded memory pressure on long B/C runs (finding F-04: full telemetry mirrors every R7 decision).

## 5. Test 2: one-hour live-like replay, adaptation on

**Status: BLOCKED (missing frozen data).** The code blocker found at `8af68c0` is resolved by this change.

### 5.1 What was blocking at `8af68c0`

- `R7DynamicParameterAdapter` existed, but nothing in the decision loop called it.
- `r7_snapshot` hard-coded `adaptation: 'off'`.
- `test_r7_adaptation_guard.py` pinned "R7 never runs with runtime adaptation".

So the parameterized values could not have been consumed. A Test 2 run on that commit would have failed its central requirement.

### 5.2 What this change does (approved 2026-11-05)

- **Source of values.** The native PAPER_APPLY plant already evaluates an R5 `DynamicParameterSnapshot` at the top of every tick (`revision2_external/orchestrator.py` `_process_tick` → `_plant_bar_step`), using only completed bars, before exits and admission. R7 reads that snapshot at its batch commit for the same timestamp (`r7_engine.py` `_r7_adaptation_for_batch`, line 279). R7 never calls the evaluator itself, so the native environment's EMA state is not advanced twice.
- **Mapping** (`r7_r5_dynamic_adapter.py` `governor_scales`, line 129). Each adapted parameter is scaled by R5's current value over its commissioned base, the same ratio the native engine applies to its execution PIDs:
  - Kp × kp/kp0
  - Ki × ki/ki0
  - Kd × kd/kd0
  - integral limit × min(1, clamp/clamp0) (tighten-only)
  - command slew × min(1, risk derate) (tighten-only)

  The other six of the 11 parameters have no approved R5 schedule and stay commissioned: derivative filter τ, response τ, droop R (fixed per run by the allocation spec) and the three reference weights.
- **Consumption.** In `paper` mode, `SymbolController.set_governor_config` (`r7_coordinated_controller.py:109`) installs the adapted config on the governor between prepare and commit. Features, AVR, FSM and the commissioned `r7_configs` never change. In `shadow` mode the values are recorded only.
- **Fail-closed.** A missing, repeated (stale) or invalid snapshot inside a tick means commissioned parameters and zero increase capacity, at batch level and per symbol. Reductions and native exits continue. Warmup bars are labelled `WARMUP`.
- **Native authority is unchanged.** R7 can still only reduce native quantity (`r7_engine.py:453`). Native `dynamic_profile_mode='consume'` is still refused, so adaptation cannot touch native safety floors, caps, sizing or protective exits.
- **Telemetry, per decision.** Mode, status, snapshot version, R5 inputs (vol and regime probabilities, stress, derate), the scales, active and proposed values of all 11 parameters, and the active and base parameter SHA-256.
- **Checkpoint.** An adaptive checkpoint declares its mode and the last snapshot version consumed. Restore rejects a mode mismatch.

### 5.3 Proof that the values are consumed, not just logged

From `test_r7_governor_adaptation.py` (9 tests, real native engine with a PAPER_APPLY plant, synthetic bars):

- 59 of 59 live bars: `APPLIED`. 60 warmup bars: `WARMUP`. 0 fail-closed. Every live bar used a distinct snapshot version.
- **Paper vs off:** on the first live bar the governor's P term is 0.35630 against 0.34069 with adaptation off; the integral builds more slowly (Ki is scaled down); and the slew-limited applied output is 0.21660 against 0.23608.
- **Shadow** is decision-identical to off. **Paper** differs.
- The integral limit and slew are never above their commissioned values.

Live-like harness smoke on synthetic bars, Engines A, B and C (10 minutes × 48 symbols, `test2_live_like_adaptive_hour/harness_smoke_synthetic_NOT_EVIDENCE/`). Every check passed except `data_is_frozen_historical`:

- causal order;
- prefix-run reproduction of the first half;
- checkpoint round trip, and restored run = reference run;
- adaptation applied on every live decision, with a fresh snapshot every minute;
- governor running the adapted config;
- limits never relaxed.

The failing check is expected for synthetic data, so the harness correctly reports `FAIL` for synthetic input.

### 5.4 Engine-specific execution difference found

Engine A's native clock never ticks a frame's final bar: it is only the next-open fill price for the last decision minute. Engines B and C deliver every bar as a venue event. The harness accounts for this when choosing the replay window.

Command to run Test 2 once the data is present:

```
LIVE_TRADING_ENABLED=False PYTHONPATH=. python revision7_avr/run_r7_live_like_replay.py {A|B|C} \
    --minutes 60 --warmup 60 --r7-adaptation paper --end-offset-minutes <N> --data-dir <frozen dir> --out <dir>
```

Use `--end-offset-minutes` to place the hour away from the session's closing square-off window. Per-minute records go to `minutes.jsonl`. `summary.json` gives PASS or FAIL per check.

## 6. 25-point audit matrix (R7-01 … R7-25)

The list is the one in `r7_station/…/execution_audit/claude_local_20261018/R7_EXECUTION_AUDIT_REPORT.md` §3. The original requirements document is outside the repository. Line anchors are for the final tree. "Code/test" means source inspection plus tests that ran today. "Replay" means historical replay evidence.

| ID | Requirement | Source | Test evidence (ran today) | Code/test | Replay |
|---|---|---|---|---|---|
| R7-01 | AVR, governor, coordinator connected; setpoint formed once | `r7_coordinated_controller.py:90` prepare, `:118` commit | `test_p1_*`, `test_handoff_studies_once_*` | PASS | BLOCKED |
| R7-02 | Real liquidity from prior baseline; bounded history | `r7_features.py` | `test_volume_history_baseline_and_spike`, `test_zero_volume_degraded_and_bounded_history` | PASS | BLOCKED |
| R7-03 | Every completed bar processed, incl. warmup and non-candidates | `r7_engine.py` `_r7_complete_tick` | `test_actual_native_engine_warmup_and_completed_bars`, `test_real_studies_called_once_even_when_no_candidates` | PASS | BLOCKED |
| R7-04 | Signed notional per symbol budget; overrun not clamped | `r7_engine.py:117` | `test_engine_actual_exposure_unclamped`, `test_p4_*` | PASS | BLOCKED |
| R7-05 | Side-aware entries; short policy preserved | `r7_engine.py:436-453` | `test_native_short_candidate_and_explicit_short_policy` | PASS | BLOCKED |
| R7-06 | Config affects runtime; hold and cooldown independent | `r7_state.py` ControlConfig | `test_release_threshold_and_hold_independent_of_cooldown`, `test_p9_*`, `test_cooldown_counts_release_bar[*]` | PASS | BLOCKED |
| R7-07 | One exposure-tracking controller; native authority kept | `r7_market_governor.py:22` | `test_p6_*`, `test_p7_*`, `test_r7_normal_exits.py` | PASS | BLOCKED |
| R7-08 | Governor checkpoint complete; atomic restore | `r7_engine.py:593`, `:608` | `test_r7_restore_atomicity.py` (21), `test_adaptive_checkpoint_round_trip_and_identity` | PASS | BLOCKED |
| R7-09 | Market AVR uses the real R6 PI API | `r7_market_avr.py` | `test_actual_r6_pi_keyword_api_and_tuple`; R6 suite 121 passed | PASS | N/A |
| R7-10 | Independent symbol state; bay totals reconcile | `r7_engine.py` bay state | `test_all_five_bays_and_unmapped_symbol`, `test_engine_atomic_checkpoint_includes_bays_and_physical` | PASS | BLOCKED |
| R7-11 | Pulse reversal / emergency policy; no deadlock | `r7_coordinated_controller.py:58` (two-sample emergency) | `test_r7_fsm_antichurn.py`, `test_r7_pulse_fsm.py` | PASS | BLOCKED |
| R7-12 | Pulse age, cooldown and reset boundaries | `PulseFSM.step` | `test_pulse_visible_for_exactly_hold_bars[*]`, `test_max_pulse_age_release_when_unsupported` | PASS | BLOCKED |
| R7-13 | Release thresholds executable; no calibration claim | `r7_state.py` thresholds (provisional) | `test_hysteresis_and_invalid_input` | PASS | BLOCKED (has the hunting gone on real data?) |
| R7-14 | Same-bar idempotency; conflicts raise | `SymbolController.step`, `_r7_queue` | `test_same_bar_immutable_context_and_no_double_integration`, `test_same_bar_conflict_and_gap` | PASS | BLOCKED |
| R7-15 | Unmapped / duplicate symbols rejected | `r7_engine.py:47-53` | `test_all_five_bays_and_unmapped_symbol` | PASS | N/A |
| R7-16 | Causal OHLCV features; invalid input rejected | `r7_features.py`, `Bar.__post_init__` | `test_features_causal_and_distinct`, `test_bad_ohlcv_rejected[*]` | PASS | BLOCKED |
| R7-17 | Studies consumed once; Nifty provenance and freshness | `_r7_queue` grid read; native provider | `test_r7_grid_freshness.py` (18; UNVERIFIED in the 10-18 audit) | PASS | BLOCKED |
| R7-18 | Target and actual in one unit; no unapproved droop formula | `r7_market_governor.py:1-8` | `test_p4_*`, `test_p5_*`, `test_p2_governor_and_controller_take_no_price_speed_input` | PASS | N/A |
| R7-19 | Native safety, sizing, reservations preserved | `r7_engine.py:453` | `test_r7_ledger_reconciliation.py` (11 tests), pending-entry tests | PASS (unit) | BLOCKED (no B/C ledger reconciliation on real data) |
| R7-20 | Five-bay topology and budgets; subset invariance | `r7_state.py` `default_configs` | `test_p3_*` | PASS | N/A |
| R7-21 | Rejection / admission reasons distinguishable | `r7_engine.py:436-453` counters | `test_r7_engine_integration.py`; counters populated in harness smokes | PASS | BLOCKED |
| R7-22 | Causal warmup before first candidate | native `_prepare_run`; R7 `WARMUP` label | `test_actual_native_engine_warmup_and_completed_bars`, adaptation warmup count | PASS | BLOCKED |
| R7-23 | Native protective exits remain effective | `_r7_arm_normal_exit` (only `GOVERNOR_Z_EXIT` advisory) | `test_r7_normal_exits.py` (23) | PASS | BLOCKED |
| R7-24 | Engine checkpoint complete; restart-equivalent | `r7_snapshot` / `r7_restore` | checkpoint tests; harness "restored run = reference run" (synthetic) | PASS (R7 state only) | BLOCKED. Native crash-journal persistence is not implemented (B-01). |
| R7-25 | Dedicated tests plus harness replay; synthetic is not native evidence | — | tests ran; harnesses smoke-tested | PASS (tests) | BLOCKED |

Tally: code/test **25 PASS** (R7-19 and R7-24 with the stated scope). Replay: **20 BLOCKED, 5 N/A, 0 PASS**.

## 7. The 11 governor parameters

### 7.1 As requirements P1–P11 (`test_r7_governor_parameters.py`)

| # | Requirement | Source | Tests | Code/test | Adaptation role |
|---|---|---|---|---|---|
| P1 | E_ref = clamp(0.5·AVR + 0.3·study + 0.2·dispatch) | `r7_coordinated_controller.py:104` | `test_p1_*` | PASS | weights fixed |
| P2 | U = filled gross / authorized limit, pending counted, unclamped, no price-speed input | `r7_engine.py:172`; `r7_allocator.py:25` | `test_p2_*` ×3 | PASS | unchanged |
| P3 | Droop R settable (0.02–0.10), registry bay values, w = B/R, water-filling | `r7_allocator.py:51,73` | `test_p3_*` | PASS | R fixed per run |
| P4 | Actual exposure preserved; error signed | `r7_market_governor.py:30` | `test_p4_*` | PASS | unchanged |
| P5 | Error is an exposure adjustment | `r7_market_governor.py:30-32` | `test_p5_*` | PASS | unchanged |
| P6 | Kp independent of R | `r7_market_governor.py` (`c.kp`) | `test_p6_*` | PASS | **Kp adapted** |
| P7 | Conditional anti-windup; integral frozen while a native order works | `r7_market_governor.py:39-55` | `test_p7_*` ×2 | PASS | **Ki and integral limit adapted** (limit tighten-only) |
| P8 | Derivative on measurement; optional filter; validation Kd = 0 | `r7_market_governor.py:33-38` | `test_p8_*` ×3 | PASS | **Kd adapted**; filter τ fixed |
| P9 | Slew in seconds; overnight = one bar | `r7_market_governor.py:47,57` | `test_p9_*` ×2 | PASS | **slew adapted** (tighten-only) |
| P10 | Response lag monotone and bounded | `r7_market_governor.py:59` | `test_p10_*` | PASS | τ fixed |
| P11 | No swing equation in the trading governor | `r7_market_governor.py:1-8` | `test_p11_*` | PASS | — |

### 7.2 As runtime values (`R7_GOVERNOR_PARAMETERS`, `r7_r5_dynamic_adapter.py:122`)

| Parameter | In decision loop | Adaptation (`paper`) |
|---|---|---|
| `kp` | `MarketGovernor.step` P term | × R5 kp ratio |
| `ki_per_second` | integral trial | × R5 ki ratio |
| `kd_seconds` | derivative on measurement | × R5 kd ratio |
| `integral_limit` | integral clamp | × min(1, R5 clamp ratio) |
| `command_slew_per_second` | command and achievable slew | × min(1, R5 risk derate) |
| `derivative_tau_seconds` | derivative filter | fixed |
| `market_response_tau_seconds` | output lag; disabled decay | fixed |
| `droop_r` | allocator weights w = B/R | fixed per run (spec) |
| `avr_weight`, `study_weight`, `dispatch_weight` | E_ref | fixed |

All 11 are read every bar: **PASS** (code/test). Five are adapted and consumed: **PASS** (code/test: `test_paper_mode_consumes_a_fresh_snapshot_every_bar_in_the_native_loop`, `test_paper_changes_governor_output_and_shadow_only_records`). Replay: **BLOCKED**.

## 8. AVR requirements

| Item | Evidence | Status |
|---|---|---|
| Market AVR is a closed loop through an internal participation model | `r7_market_avr.py`; `test_avr_bounded_convergent_and_bad_data_decay` | PASS |
| Uses the actual R6 `PositionalPI` (keyword `step`, tuple return) | `test_actual_r6_pi_keyword_api_and_tuple` | PASS |
| Degraded, gap or not-ready bars decay state instead of integrating | same test | PASS |
| R6 physical AVR and turbine preserved | `revision6_avr/`: 121 passed, 1 strict xfail (documented) | PASS |
| R5/R6/native sources not changed by this work | Diff vs `8af68c0` touches only `revision7_avr/` (8 files) | PASS |
| AVR is not adapted | Adaptation touches only `MarketGovernor` config; AVR, features and FSM keep the commissioned config | PASS |
| No Q-V droop / 8% AVR droop in the market AVR | taken from the 57-point workbook (M09); no independent source located | NOT VERIFIED (no source) |

## 9. Open items carried forward

- **F-04** (2026-10-18 audit): in full telemetry mode every R7 decision is also mirrored into `controller_telemetry`, so memory grows with replay length. This matters for Test 1's month-long run.
- **F-05**: the Engine A golden master mismatch was pre-existing at `f0ad7af`. See the Engine C row in §3.
- **F-09**: per-bay registry droop vs the "fixed R = 0.04" note needs operator sign-off. This change keeps R fixed per run.
- **B-01**: native crash-journal persistence of the R7 checkpoint is not implemented. Restart-equivalence is evidenced for R7 state only.

## 10. Recommendation

1. Make the frozen dataset and grid manifest available to the runner. Push them, or run on the workstation that holds them.
2. Run Test 1: six runs (A/B/C × native/R7, adaptation off) over one complete month, checkpointed or quarter-bounded per the handover advice.
3. Run Test 2: A/B/C with `--r7-adaptation paper` over one mid-session hour. Accept a run only if `summary.json` reports `PASS` with `evidence_class: HISTORICAL_LIVE_LIKE_PAPER`.
4. Do not compare P&L between Test 1 and Test 2. They are different experiments.
