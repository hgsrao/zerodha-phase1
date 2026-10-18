# R7 Execution Test Results

Audit date: 2026-10-18. Every row below names the command that ran and the evidence file holding its raw output.
Evidence files are under `evidence/` in this directory and are hashed in `R7_EXECUTION_EVIDENCE_MANIFEST.sha256`.

## 0. Baseline and environment

| Item | Value | Evidence |
|---|---|---|
| pwd | `/home/srinivas/projects/zerodha-r6-no-adaptation` | `evidence/00_baseline.txt` |
| branch | `r7-governor-corrections` | same |
| HEAD | `7c08ad586dd2854e36a0a80ddde6605e3a8b136a` | same |
| `git status --short` | 4 modified files: `revision7_avr/{r7_coordinated_controller.py, r7_engine.py, test_r7_engine_integration.py, test_r7_pulse_fsm.py}`; no untracked files | same |
| Uncommitted diff | sha256 `ff765ed2…2882e`, identical at start and end of audit (main repo not modified) | `00_uncommitted.diff` |
| `LIVE_TRADING_ENABLED` | The environment variable was the string `False` at audit start. R7 and Engine B/C source never read it (only `run_r7_replay.py` and `run_r6_native_replay.py` set or assert it), so it is not enforced in code on the R7 path. See finding F-07. | `00_baseline.txt` |
| Interpreter | `/home/srinivas/.venvs/zerodha-phase1-r5` (Python 3.12.14). System Python 3.14.4 lacks pytest, numpy and pandas, so it was not used. | `50_dependencies.txt` |
| Dependencies | All 16 pins in `requirements_external_engine.txt` match installed versions exactly. `pytest 9.1.1`, `backtrader 1.9.78.123` and `nautilus_trader 1.231.0` are installed but not pinned in that file. | `50_dependencies.txt` |
| Running replay | None found. No `run_r7_replay` or `run_engine_*` process existed. | `ps` output in session |
| Credentials | No broker or API variables in the environment. Engine B/C `place_order` is `_refuse`. `broker_adapter_kite.py` (imports `KiteConnect`) exists in `revision2_external` but is not referenced by Engine B/C or R7. | session scan |

**Isolation.** All tests ran against `repo_snapshot/`, an rsync copy of the working tree without `.git` or caches, with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. The only commands run inside the main repo were read-only `git` queries and `patch --dry-run`. Proposed fixes were applied only in `fixcopy/full/`.

## 1. Focused R7 tests

| # | Command (cwd = snapshot or fixcopy) | Result | Evidence |
|---|---|---|---|
| 1a | `pytest revision7_avr -p no:cacheprovider -q` on the **as-found working tree** | **136 passed, 1 failed** in 12.0 s. The failure is `test_r7_engine_integration.py::test_only_explicit_discretionary_native_exit_is_advisory` (`NameError: name 'e' is not defined`). | `10_r7_focused.txt` |
| 1b | Same command with `R7_PROPOSED_FIXES.patch` applied in the scratch copy | **137 passed** in 11.6 s | `11_r7_with_proposed_patch.txt` |
| 1c | `patch -p1 --dry-run < R7_PROPOSED_FIXES.patch` against the snapshot and against the main repo | Both clean (dry run only, nothing written) | `13_patch_dry_run_*.txt` |

Cause of 1a (finding F-01): the uncommitted edit inserted three new tests into the middle of `test_core_without_native_plant_cannot_bypass_r7_candidate_admission`. Its last five lines were orphaned into the new test, which references variables it never defines. That left the original test with its native-path admission check removed and one test broken.

Test inventory for the 137 tests: `12_r7_test_inventory.txt`.

## 2. Behavioural probes (outside the repo suites)

Scripts are in `tools/`. Raw output is in `evidence/`.

| Probe | Result | Evidence |
|---|---|---|
| FSM fuzz: 300 runs × 200 random steps | 1,588 direct BUY↔SELL flips, **0** without two consecutive opposing emergency samples | `40_probes.txt` |
| FSM: ineligible input resets `emergency_age` | PASS | same |
| FSM: "non-emergency sample breaks the streak" | The probe's expected value was wrong, not the code. After `hold_bars` an emergency sample satisfies `release` and the pulse goes to HOLD. Checked precisely in `41_fsm_reversal_reachability.txt`. | `40_probes.txt`, `41_…` |
| FSM reachability (finding F-03) | A fresh pulse reverses BUY→SELL after two emergency samples. A pulse older than `hold_bars` goes BUY→HOLD→SELL, with one HOLD bar between. Alternating ±0.35 never produces a direct flip. | `41_fsm_reversal_reachability.txt` |
| Allocator fuzz: 3,000 random cases | Total ≤ headroom, per-symbol ≤ demand, FITS means full grant, OVERSUBSCRIBED exhausts headroom, **order-independent**. 0 violations. | `40_probes.txt` |
| `approve_target`: 5,000 random cases | No sign flip, flat on opposing reference, 0 violations | same |
| `Utilization` rejects negative, zero-limit and NaN inputs; overrun not clamped | PASS | same |
| Causality: 80-bar run equals the first 80 decisions of a 120-bar run | PASS | same |
| Idempotency: same bar and input returns the same object; conflicting context or OHLCV raises | PASS | same |
| Checkpoint: restore reproduces 30 future decisions exactly | PASS | same |
| Restore rejects NaN integral, negative or float `emergency_age`, bad pulse, wrong schema, **atomically** (state unchanged) | PASS ×5 | same |
| Restore accepts a pre-`emergency_age` (legacy 3-field) checkpoint | PASS | same |
| Restore bounds `emergency_age` above | **Not bounded**: `10**9` is accepted. Harmless, since only `>= 2` matters, but inconsistent with the other clock checks (F-06). | same |
| Droop outside 0.02–0.10 is rejected | PASS | same |
| Decision memory (390-bar native run, `r7_decision_memory=5`) | `r7_decisions` window = 5 (bounded), but **389** `R7_COMPLETED_BAR` full records are retained in the native `controller_telemetry` (`telemetry_mode="full"`) (F-04) | `42_decision_memory_probe.txt` |
| Missing grid context (200 bars) | 0 eligible decisions, 0 non-HOLD pulses, 139 candidates rejected before native admission, zero demand | `43_missing_grid_fail_closed.txt` |

## 3. Dataset verification (48 symbols vs `revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json`)

Command: `python3 -I tools/verify_dataset.py <manifest> /home/srinivas/projects/zerodha-phase1/data/frozen_48_1min_20230703_20260824 …`. This is an independent stdlib implementation. It checks file name, size, data-row count, first and last timestamp, and SHA-256. Read-only.

- **48 / 48 PASS, 0 FAIL, 0 missing.**
- The recomputed `manifest_hash` equals the stored one.
- The directory also holds 3 non-manifest files (`acquisition_manifest.json`, `acquisition_manifest.json.sha256`, `acquisition_summary.json`). They were listed but not content-verified.
- Evidence: `20_dataset_verification.{json,txt}`.

## 4. Regression suites

| Suite | Command | Result | Evidence |
|---|---|---|---|
| R6 | `pytest revision6_avr -q` | **121 passed, 1 xfailed** in 49.5 s. The xfail is `strict=True` with reason "OPEN: closed-loop limit cycle at dt=3 s…", an existing documented open item. | `30_regress_revision6_avr.txt` |
| Engine B | `pytest tests_engine_b -q` | **20 passed, 1 failed** in 303.9 s. The failure, `test_protected_engine_a_files_unchanged`, is an artifact of the snapshot having no `.git` (`git diff` exit 129). | `30_regress_tests_engine_b.txt` |
| Engine B, protected-files check re-run read-only in the real repo | `git diff --name-only HEAD -- revision2 revision2_external/paper_execution.py revision5/ccpp_unified_plant.py runtime/operating_mode.py` | Empty output, exit 0. The equivalent check passes. | `31_…realrepo.txt` |
| Engine C, parameter authority | `pytest tests_engine_c/test_engine_c_parameter_authority.py` | **12 passed** in 224.5 s | `32_engine_c_test_engine_c_parameter_authority.txt` |
| Engine C, backtrader file | `pytest tests_engine_c/test_engine_c_backtrader.py` (timeout 470 s) | 37 passed, 1 skipped, then **killed by the 8-minute cap** (exit 124) on the 39th test, `test_engine_a_golden_master_still_passes`. It was killed twice, once with other jobs running and once alone. No end-of-run summary was produced. The skip is probably line 541, "Engine B base commit not in this checkout" (no `.git` in the snapshot). | `30_regress_tests_engine_c.txt`, `32_…backtrader.txt`, `33_…alone.txt` |
| Engine A golden master, split per scenario with `--only` | `scripts/capture_r5_golden_master.py --check --only <scenario>` ×17 | **17 / 17 FAIL** ("MISMATCH … trades, controller_telemetry, mtm_equity_curve, entry_candidate_observations, position_sizing_events") | `golden/*.txt` |
| Same check, pristine export of baseline `f0ad7af` | one scenario, `probe_no_plant_seed14` | **Same MISMATCH**, so the failure predates the R7 governor corrections. The other 16 scenarios were not run on the baseline. | `34_golden_baseline_f0ad7af.txt` |

Not run: top-level `tests/` and `tests_external/` (R5 and earlier suites, outside the R6/R7/Engine B/C scope).

## 5. Offline replays

| Replay | Status | Reason |
|---|---|---|
| Fixed one-month dump replay | **BLOCKED** | No artifact, flag or document in the repo or the Codex outputs defines a "one-month dump". `run_r7_replay.py` has `--bars N` only. I did not guess a definition. |
| One-hour 48-symbol replay | **BLOCKED** (not run) | The task says to run only if all preflight checks pass. They do not: the as-found tree fails 1 focused test, and the Engine A golden master fails on both the tree and the baseline. "Permitted configuration" is also not defined. The replay would be offline and read-only on data. Run it once the gate is resolved: `PYTHONPATH=<snapshot> python revision7_avr/run_r7_replay.py {B,C} r7 --symbols ALL --bars 60 --warmup 60 --out <new dir>`. |

No replay, live order, or credential access was performed or attempted.
