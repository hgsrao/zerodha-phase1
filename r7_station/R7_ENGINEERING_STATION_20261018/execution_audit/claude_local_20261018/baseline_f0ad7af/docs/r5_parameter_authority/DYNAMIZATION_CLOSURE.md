# R5 dynamization closure — verified implementation

Scope: one shared R5 implementation, based on b177c9d. Claude implemented the first patch but exited without a final receipt; Codex reviewed, corrected the studies projection and registry bookkeeping, and verified the result. This is code-level acceptance with explicit input/mode prerequisites, not financial calibration or production qualification.

## Exact accounting

| Original 312 | Before | After |
|---|---:|---:|
| Dynamic runtime values | 89 | 90 |
| Deliberate constants / formula coefficients | 190 | 210 |
| Advisory | 12 | 12 |
| Inactive implementation | 21 | 0 |
| Total original keys | 312 | 312 |

One formerly inactive value, avr_mvar_limit_base_pu, is a scheduled dynamic safety limit; the other twenty are functional coefficients, bounds or thresholds, not newly dynamic variables. All original keys remain. Eight new registry-owned envelope keys add six fixed safety limits and two advisory gain-scale bounds. The expanded audit is 320 = 90 dynamic + 216 constants/coefficient values + 14 advisory. This audit surface includes 21 pre-existing legacy controller keys alongside the native R5 registry. The entire canonical registry is a different, larger surface: 469 targets, 310 tunable and 159 fixed (including advisory); these counts must not be confused with the original312 audit.

## Six corrected areas

1. **MVAR limiter (3 formerly inactive keys):** the per-bar measured AVR accepts a signed per-unit measurement for each bay. None is explicitly NOT_EVALUATED, never a fabricated zero. Positive/negative saturation prevents further excitation in the saturated direction and permits recovery. Required mode rejects startup without a provider and fails closed on absent or invalid values. Use avr_voltage_mode='measured', avr_mvar_limiter_mode='required', mvar_measurement_provider(timestamp, bar_index, bay_id). Provider observations must be causal and use a documented signed per-unit base. Actual physical MVAR cannot be inferred from OHLC; production input availability is not claimed. Tests supply controlled measurements to verify actuation.
2. **Closed-loop and study envelopes:** eight former literal limits are now owned by the registry, validated together and read from the run configuration. Derates never add risk; persistence stays strictly inside (0,1). Final effective study shares, rather than merely raw weights, are projected to a unit sum within the configured bounds. Audit records preserve pre-projection and final weights. This corrects a real safety-envelope defect and can change composite decisions; blanket byte-identical/default-decision equivalence is not claimed.
3. **Five bay z-exit thresholds:** causal entry z arms a side-aware mean-reversion exit. Hard exits, spread trips and Mark V safety exits keep precedence; minimum hold defers this discretionary exit. Entry state is per symbol/position, not borrowed from another symbol.
4. **Seven previously unreachable clips:** five governor clips and two Box6 clips now have admissible domains where they actually bind. Numeric defaults are retained; inverted cross-bounds are rejected. Independent corner tests show changed runtime gains/limits reaching consumers. Range changes are documented, not claimed calibrated.
5. **Four dispatcher-floor coefficients:** the per-bar floor reaches sector references. Unavailable bays receive zero; feasible floors are respected; infeasible floors are projected under the per-bay ceiling and shared budget, with explicit reason telemetry. No capital is created.
6. **Two timeout coefficients:** stress now shortens, rather than lengthens, the timeout. Bounds and the original execution contract cap the result. Tests cover calm/stressed/warmup states and the orchestrator consumer.

## Advisory recheck

These original twelve influence the plant's suggested stop/target only. The order-planning authority owns actual levels. They remain honestly advisory rather than silently creating a competing exit authority:

- gov_bpstg_atr_barrier
- gov_bpstg_target_m
- gov_cstg1_atr_barrier
- gov_cstg1_target_m
- gov_cstg2_atr_barrier
- gov_cstg2_target_m
- gov_gtg1_atr_barrier
- gov_gtg1_target_m
- gov_gtg2_atr_barrier
- gov_gtg2_target_m
- plant_stop_floor_price_fraction
- plant_target_min_reward_risk

The two added cl_gain_scale_min/max keys bound a recorded suggestion and are also advisory. All fourteen are non-optimizer rows. Registry ownership does not make a suggestion an actuator.

## Every formerly inactive key

Each row below has independent closure evidence in tests/test_r5_dynamization_closure.py; the complete migration file preserves all 312 original rows plus the eight additions.

| Key | Status | Specific closure proof |
|---|---|---|
| avr_mvar_limit_base_pu | dynamic (conditional consumer) | test_mvar_limit_parameters_move_the_limiter_and_the_stress_schedule_follows_its_formula; test_supplied_measurement_reaches_the_per_bar_avr_saturates_and_returns_to_range |
| avr_mvar_limit_min_pu | constant_or_coefficient (conditional consumer) | test_mvar_limit_parameters_move_the_limiter_and_the_stress_schedule_follows_its_formula; test_supplied_measurement_reaches_the_per_bar_avr_saturates_and_returns_to_range |
| avr_mvar_limit_stress_gain | constant_or_coefficient (conditional consumer) | test_mvar_limit_parameters_move_the_limiter_and_the_stress_schedule_follows_its_formula; test_supplied_measurement_reaches_the_per_bar_avr_saturates_and_returns_to_range |
| gov_bpstg_z_exit | constant_or_coefficient (conditional consumer) | test_each_bay_z_exit_threshold_changes_the_exit_decision; test_z_exit_needs_a_prior_crossing_and_respects_the_protection_and_min_hold_hierarchy |
| gov_cstg1_z_exit | constant_or_coefficient (conditional consumer) | test_each_bay_z_exit_threshold_changes_the_exit_decision; test_z_exit_needs_a_prior_crossing_and_respects_the_protection_and_min_hold_hierarchy |
| gov_cstg2_z_exit | constant_or_coefficient (conditional consumer) | test_each_bay_z_exit_threshold_changes_the_exit_decision; test_z_exit_needs_a_prior_crossing_and_respects_the_protection_and_min_hold_hierarchy |
| gov_gtg1_z_exit | constant_or_coefficient (conditional consumer) | test_each_bay_z_exit_threshold_changes_the_exit_decision; test_z_exit_needs_a_prior_crossing_and_respects_the_protection_and_min_hold_hierarchy |
| gov_gtg2_z_exit | constant_or_coefficient (conditional consumer) | test_each_bay_z_exit_threshold_changes_the_exit_decision; test_z_exit_needs_a_prior_crossing_and_respects_the_protection_and_min_hold_hierarchy |
| gsch_droop_max_min_scale | constant_or_coefficient (conditional consumer) | test_formerly_unreachable_gsch_clip_binds_at_the_neutral_corner_and_reaches_the_governor |
| gsch_integral_max_scale | constant_or_coefficient (conditional consumer) | test_formerly_unreachable_gsch_clip_binds_at_the_neutral_corner_and_reaches_the_governor |
| gsch_kd_scale_min | constant_or_coefficient (conditional consumer) | test_formerly_unreachable_gsch_clip_binds_at_the_neutral_corner_and_reaches_the_governor |
| gsch_kp_scale_min | constant_or_coefficient (conditional consumer) | test_formerly_unreachable_gsch_clip_binds_at_the_neutral_corner_and_reaches_the_governor |
| gsch_offset_min_upper_scale | constant_or_coefficient (conditional consumer) | test_formerly_unreachable_gsch_clip_binds_at_the_neutral_corner_and_reaches_the_governor |
| psch_dispatch_floor_ceiling_ratio | constant_or_coefficient (conditional consumer) | test_psch_dispatch_floor_coefficients_each_move_the_runtime_floor_in_a_feasible_corner; test_dispatch_floor_reaches_the_sector_dispatch_references_in_consume_mode |
| psch_dispatch_floor_max | constant_or_coefficient (conditional consumer) | test_psch_dispatch_floor_coefficients_each_move_the_runtime_floor_in_a_feasible_corner; test_dispatch_floor_reaches_the_sector_dispatch_references_in_consume_mode |
| psch_dispatch_floor_min | constant_or_coefficient (conditional consumer) | test_psch_dispatch_floor_coefficients_each_move_the_runtime_floor_in_a_feasible_corner; test_dispatch_floor_reaches_the_sector_dispatch_references_in_consume_mode |
| psch_dispatch_floor_stress_gain | constant_or_coefficient (conditional consumer) | test_psch_dispatch_floor_coefficients_each_move_the_runtime_floor_in_a_feasible_corner; test_dispatch_floor_reaches_the_sector_dispatch_references_in_consume_mode |
| psch_order_timeout_min_seconds | constant_or_coefficient (conditional consumer) | test_order_timeout_tightens_under_stress_with_registry_bounds_and_warmup_behaviour |
| psch_order_timeout_stress_gain | constant_or_coefficient (conditional consumer) | test_order_timeout_tightens_under_stress_with_registry_bounds_and_warmup_behaviour |
| tier3_kd_scale_min | constant_or_coefficient (conditional consumer) | test_tier3_kp_kd_floor_binds_in_the_box6_gain_schedule_and_reaches_the_entry_pid |
| tier3_kp_scale_min | constant_or_coefficient (conditional consumer) | test_tier3_kp_kd_floor_binds_in_the_box6_gain_schedule_and_reaches_the_entry_pid |

## Verification and integration contract

481 distinct cases have final passing evidence, zero unresolved failures in the recorded scoped runs. The earlier broad run produced235 passes and eight registry-bookkeeping failures; all eight were corrected and rerun. A second all-parameter run produced356 passes and two stale-identity failures; both were corrected and passed on rerun. The evidence records earlier failures rather than deleting them. The standalone closure cases cover all21 old inactive controls and new envelopes. The broader seven-minute run additionally exercised dynamic formulas, causal changes to actual synthetic orders, checkpoint equivalence and existing parameter consumers. No claim is made that the entire repository suite or a new full-history replay was run.

Registry SHA256: `d7f4e89718a38d724d57f4e70b8bdbd93bb8ccb9385b4940e896efee906d5b55`. Protected indicator bytes match the baseline. Real market data, TRAIN/holdout boundaries, calibration, live brokers and unrelated dirty work were not changed. Existing explicit mode gates remain visible: dynamic_profile_mode='consume', governor_position_mode='v3', measured AVR, bounded dispatcher and their documented prerequisites. A default configuration with these modes off does not run those consumers.

Gemini upload encountered a new terms/rights consent before any source attachment and was cancelled. No Gemini verdict is claimed; Codex used the authorized review fallback. New source and test artifacts are retained for later independent review.

Engine commit is created before resealing. The subsequent protocol metadata commit binds that exact engine commit and this registry. Resealing permits neither calibration nor production trading. Push acceptance is recorded separately in the platform receipt after the remote HEAD is verified.
