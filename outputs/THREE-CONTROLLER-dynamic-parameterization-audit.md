# THREE-CONTROLLER dynamic parameterization audit

Branch: codex/r5-three-controller-parameterization — base 55a80696f023dae52e50155f7f4674b761ab878a (uncommitted).
Scope: Studies PID (BB04b), the closed-loop supporting subsystem, and engine-applicability metadata.
All new values are `ENGINEERING_INITIAL_VALUE` / `NOT_CALIBRATED`. No Optuna/Ray, calibration or live run.

## 1. Architecture status (source-proved)

- **ClosedLoopSupervisor = CLOSED-LOOP SUPPORTING SUBSYSTEM** (outcome ledger, symbol dynamics, portfolio derate, HMM
  shadow hysteresis). It is not the ECS Plant Supervisor.
- **ECS Plant Supervisor = NOT YET IMPLEMENTED.** ARCHITECTURAL GAP — ECS PLANT SUPERVISOR NOT YET IMPLEMENTED.
- **Sector Dispatch Controller = NOT YET IMPLEMENTED.** ARCHITECTURAL GAP — SECTOR DISPATCH CONTROLLER NOT YET IMPLEMENTED.
  `CentralPlantMasterDCS` / `DynamicBayLoadDispatcher` are plant-model building blocks not wired to the orchestrator; the
  dispatcher allocates capital from R-multiples, not sector demand.
- Grid reference: Nifty/VIX frames in `SealedGridContextProvider` (SHADOW). Synchronizer: `MacroGridSynchronizer` (SHADOW);
  the orchestrator grid gate is `if False` (DEAD). Governors receive references from 25A synchronizer pulses and a
  caller-supplied `load_reference_pu`; nothing upstream derives them from Nifty or sector demand.

## 2. Engine applicability model

`ParameterSpec.applicable_engines` in {IN_HOUSE, EXTERNAL, BOTH}, independent of `calibratable`.
Optimizer surface = calibratable AND applicable to the optimizer's engine. `applicable_engines` is part of
`asdict(spec)` and therefore of `identity_payload()` / `FROZEN_IDENTITY_SHA256`
(test: `test_identity_covers_applicability` — changing only `applicable_engines` changes the hash).

| Scope | Count | Names |
|---|---|---|
| EXTERNAL-only | 67 (62 eligible + 5 fixed) | 16 BB04 PA, 29 BB05/BB06, 6 `studies_*`, 16 `cl_*` |
| IN_HOUSE-only | 1, FIXED | `learning_rate_exploration_factor` |
| BOTH | remainder | shared |
| FIXED regardless of engine | 28 | 22 base + 5 external cost/regularization/shadow + 1 diagnostic |

### External-only consumer proof (none has any occurrence in `revision2/`)

| Consumer (external) | Parameters |
|---|---|
| `indicators_talib.py::TALibPredictiveAnalyticsBox` | 16: `momentum_normalization_divisor`, `pa_*` (atr floor/fallback, persistence x4, direction activation, vwap/volume normalization, low/high vol boundary, green/amber/red multipliers, auto warmup) |
| `regime_id_box.py` | 12: `id_feature_window`, `id_refit_every_bars`, `id_min_history_bars`, `id_volatility_*`, `id_variance_ratio`, `id_slippage_*`, `id_reward_*`, `id_risk_*` |
| `regime_hmm.py` + `regime_id_box.py` | 5: `id_hmm_iterations`, `id_hmm_tolerance`, `id_min_state_occupancy`, `id_variance_floor`, `id_initial_variance_regularizer` |
| `dynamic_parameter_controller.py` + `orchestrator.py` | 5: `mpc_environment_lookback`, `mpc_range_atr_period`, `mpc_range_fallback_fraction`, `mpc_atr_floor_gain`, `mpc_slippage_vol_gain` |
| `pid_controller.py` | `mpc_entry_price_gain`; `mpc_schedule_kp/ki/kd_gain` (with `dynamic_parameter_controller.py`); `mpc_base_slippage_fraction` (with both) |
| `continuous_exit_controller.py` + `orchestrator.py` | `mpc_time_decay_gain`, `mpc_shadow_r_gamma` |
| `composite_study_signal.py` + `orchestrator.py` | 6 `studies_*` |
| `closed_loop_control.py` | 16 `cl_*` |

Enforced by `test_every_external_only_parameter_has_an_external_consumer_and_no_in_house_consumer`.

`momentum_normalization_divisor` verdict: **case A** (in-house sweep wrongly scoped). The in-house
`PredictiveAnalyticsBox` never reads it or any of the 16 BB04 PA controls (behavioural test at min/max), while
`TALibPredictiveAnalyticsBox` does. The failure pre-dates this work (present at base `55a8069`).

### `learning_rate_exploration_factor` (reclassified)

Only consumer: `UnifiedExecutionBox.check_window` computes `exploration_bias = phase1 * phase2 * learning_rate`.
Its callers (`revision2/orchestrator.py:351`, `revision2/portfolio_orchestrator.py:388`, `revision4/ten_box_integration.py:116`)
discard the value (`_exploration_bias`, `_`, `_bias`). It never gates or sizes a trade. It is diagnostic only and is
now FIXED, consistent with its already-fixed `phase1/phase2` siblings; it stays IN_HOUSE-applicable (its only reader is
in-house) but is in no optimizer surface. It was already excluded from the trading search space by
`CALIBRATION_CONTROL_PARAMS` (kept as defence in depth).

## 3. Hysteresis envelope (`cl_hmm_stress_enter` / `cl_hmm_stress_exit`)

| | min | max | default |
|---|---|---|---|
| enter, previous | 0.6 | 0.9 | 0.75 |
| enter, now | 0.7 | 0.9 | 0.75 |
| exit, previous | 0.3 | 0.7 | 0.55 |
| exit, now | 0.3 | 0.65 | 0.55 |

Invariant (`HMMRiskHysteresis._validate`): `0 <= exit < enter <= 1`. With the previous overlapping ranges independent
optimizer draws (e.g. enter 0.6, exit 0.7) raised at construction. The repository's optimizers (CMA-ES, TPE, random,
Optuna/Ray) sample each parameter independently and have no relational-constraint mechanism, so a relational
constraint would need new machinery in every optimizer path. The retained solution is a disjoint envelope
(the same pattern as `pa_low_vol_ratio_boundary` [0.5, 0.9] vs `pa_high_vol_ratio_boundary` [1.1, 2.0]): every point of
the search box satisfies exit < enter, including when only one of the two is overridden. The bounds are an
**engineering stability envelope, NOT calibrated** (stated in both registry notes). A test checks every corner.
This excludes valid combinations such as enter 0.65 / exit 0.5 from search; that is accepted debt.

## 4. Studies PID ownership

Runtime chain: `CanonicalParameterRegistry -> EffectiveConfig -> CompositeStudySignal -> per-study simple_pid.PID`.
The only production construction is `CompositeStudySignal(config=self.config)` in `revision2_external/orchestrator.py`
(asserted by AST test); `configure(self.config)` is called before each warmup and live evaluation. Explicit
`kp/ki/kd/clamp` constructor overrides exist only for isolated tests (one legacy test and one new test); research
scripts call `CompositeStudySignal()` and receive registry defaults through `default_config()`, not another literal owner.
`configure()` preserves PID object identity, `_integral`, `_last_input`, weights and the newest causal history
(`test_reconfiguration_preserves_pid_objects_and_state`, `test_orchestrator_refresh_keeps_pid_state_while_changing_tunings`).
Weight floor/ceiling (0.05/0.60) remain a fixed safety envelope (`MIN_WEIGHT`/`MAX_WEIGHT`), not parameters.

## 5. Optimizer / calibration caller audit

`calibratable_names(engine=None)` (union) remains for reporting only. Every optimizer, search-space and payload-validation
call selects an engine:

| Caller | Engine |
|---|---|
| `revision2/optimizer.py` `SearchSpace.from_registry` (mandatory keyword) | passed by caller |
| `revision2/optimizer.py` optimizer class, `revision2/calibration_supervisor.py` | IN_HOUSE |
| `revision2/orchestrator.py`, `revision2/portfolio_orchestrator.py` payload validation | IN_HOUSE |
| `revision2_external/orchestrator.py` payload validation | EXTERNAL |
| `scripts/run_external_sizing_calibration.py`, `scripts/run_external_horizon_paper_48symbol.py` | EXTERNAL |
| `revision4/ray_optuna_v3_calibration.py`, `v3_intraday_calibration.py`, `validate_48symbol_sealed.py`, `canonical_config.py` | IN_HOUSE |
| `calibration_config.calibratable_45`, `registry.calibratable_45` | IN_HOUSE |
| `revision5/supervisory_bridge.py` | EXTERNAL (payload is only `entry_confidence_threshold`, a BOTH parameter) |
| `runtime/contract_validator.py` | **defect found and fixed**: it validated with the union, so an engine-specific name (e.g. `studies_pid_kp` in an in-house runtime) would have been admitted. `ContractValidator(engine=...)` is now mandatory and applied. |
| `parameter_blackbox_bindings.py`, registry `__main__`/`validate_contract` | union, reporting only (allow-listed) |

Structural tests: `test_every_optimizer_and_search_space_caller_selects_an_explicit_engine` (AST scan of tracked source),
`test_no_tracked_source_reads_spec_calibratable_outside_the_registry_and_reports`.

## 6. Registry accounting (arithmetic checked)

| Measure | Count |
|---|---|
| total targets | 136 |
| fixed targets | 28 (= 22 + 5 + 1) |
| safety parameters | 20 (outside the 136) |
| optimizer-eligible union | 108 (= 136 - 28) |
| shared eligible | 46 |
| in-house-only eligible | 0 |
| external-only eligible | 62 |
| in-house surface | 46 (= 46 + 0) |
| external surface | 108 (= 46 + 62) |
| union | 108 (= 46 + 0 + 62) |

Frozen identity: `e103019e83e384f7ddc9b410abdc860d3753386ba0d3450524d517519f0fc2ed`.

## 7. Known debt

- Shared-parameter proof is textual (name referenced by both engines), not per-parameter behavioural.
- `tests/legacy_quarantine/test_runtime_startup_gate.py` (does not collect: `ecs_runtime_v2` missing) still calls
  `ContractValidator()` without `engine`; update if that file is revived.
- `revision2_external/closed_loop_dry_run.py` and research scripts construct closed-loop objects with registry defaults or explicit dry-run values.
- Disjoint hysteresis envelope excludes some valid enter/exit pairs.
- ECS Plant Supervisor and Sector Dispatch Controller remain unimplemented.
