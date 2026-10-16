# Original 312 parameter closure

All original keys retained. Eight new registry envelopes are separate. Active means implemented and proven under the stated mode/input conditions, not enabled by every default configuration.

| Parameter | Before | After | Mode / prerequisite |
|---|---|---|
| avr_excitation_max_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_excitation_max_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_excitation_max_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_excitation_min_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_excitation_min_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_excitation_min_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_integral_clamp_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_integral_clamp_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_integral_clamp_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_kd_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_kd_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_kd_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_ki_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_ki_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_ki_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_kp_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_kp_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_kp_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_loading_fraction_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_loading_fraction_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_mvar_limit_base_pu | inactive | dynamic | avr_voltage_mode='measured', avr_mvar_limiter_mode='required', supplied measurement |
| avr_mvar_limit_min_pu | inactive | constant_or_coefficient | avr_voltage_mode='measured', avr_mvar_limiter_mode='required', supplied measurement |
| avr_mvar_limit_stress_gain | inactive | constant_or_coefficient | avr_voltage_mode='measured', avr_mvar_limiter_mode='required', supplied measurement |
| avr_oel_capital_fraction | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_oel_fraction_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_overvoltage_trip_base_pu | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_overvoltage_trip_min_pu | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_overvoltage_trip_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_synchronous_reactance_pu | constant_or_coefficient | constant_or_coefficient | avr_voltage_mode='measured' |
| avr_uel_min_notional_inr | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_uel_multiplier_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_uel_multiplier_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_undervoltage_trip_base_pu | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_undervoltage_trip_max_pu | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_undervoltage_trip_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_vol_scalar_max_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_vol_scalar_max_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_vol_scalar_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_voltage_filter_alpha | constant_or_coefficient | constant_or_coefficient | avr_voltage_mode='measured' |
| avr_voltage_reference_pu | constant_or_coefficient | constant_or_coefficient | avr_voltage_mode='measured' |
| avr_z_strength_cap_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_z_strength_cap_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_z_strength_denominator_base | dynamic | dynamic | always (PAPER_APPLY native plant) |
| avr_z_strength_denominator_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| avr_z_strength_denominator_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| cl_confidence_offset_gain | constant_or_coefficient | constant_or_coefficient | always |
| cl_entry_derate_max | new | constant_or_coefficient | closed_loop_mode=active_paper for actuation |
| cl_entry_derate_min | new | constant_or_coefficient | closed_loop_mode=active_paper for actuation |
| cl_gain_scale_max | new | advisory | n/a |
| cl_gain_scale_min | new | advisory | n/a |
| cl_persistence_max | new | constant_or_coefficient | closed_loop_mode=active_paper for actuation |
| cl_persistence_min | new | constant_or_coefficient | closed_loop_mode=active_paper for actuation |
| disp_allocation_ceiling | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_allocation_floor | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_downside_dev_default | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_downside_dev_floor | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_downside_min_count | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_ema_retention | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_ema_update | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_merit_window | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_min_samples | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_neutral_score | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_score_base | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| disp_score_floor | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_risk_derate_drawdown_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_risk_derate_lower_bound | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_risk_derate_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_stress_chop_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_stress_excess_vol_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_stress_turbulent_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_vol_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| dyn_vol_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| ecs_demand_restore_step_pu | constant_or_coefficient | constant_or_coefficient | always |
| gov_bpstg_atr_barrier | advisory | advisory | n/a |
| gov_bpstg_base_z | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_capital_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_bpstg_droop_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_dynamic_offset_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_dynamic_offset_min | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_feedback_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_bpstg_grid_droop_gain | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_grid_droop_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_integral_clamp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_kd | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_ki | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_kp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_outcome_window | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_target_m | advisory | advisory | n/a |
| gov_bpstg_target_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_bpstg_z_exit | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_cstg1_atr_barrier | advisory | advisory | n/a |
| gov_cstg1_base_z | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_capital_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_cstg1_droop_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_dynamic_offset_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_dynamic_offset_min | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_feedback_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_cstg1_grid_droop_gain | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_grid_droop_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_integral_clamp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_kd | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_ki | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_kp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_outcome_window | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_target_m | advisory | advisory | n/a |
| gov_cstg1_target_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg1_z_exit | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_cstg2_atr_barrier | advisory | advisory | n/a |
| gov_cstg2_base_z | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_capital_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_cstg2_droop_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_dynamic_offset_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_dynamic_offset_min | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_feedback_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_cstg2_grid_droop_gain | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_grid_droop_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_integral_clamp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_kd | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_ki | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_kp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_outcome_window | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_target_m | advisory | advisory | n/a |
| gov_cstg2_target_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_cstg2_z_exit | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_gtg1_atr_barrier | advisory | advisory | n/a |
| gov_gtg1_base_z | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_capital_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_gtg1_droop_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_dynamic_offset_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_dynamic_offset_min | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_feedback_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_gtg1_grid_droop_gain | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_grid_droop_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_integral_clamp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_kd | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_ki | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_kp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_outcome_window | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_target_m | advisory | advisory | n/a |
| gov_gtg1_target_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg1_z_exit | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_gtg2_atr_barrier | advisory | advisory | n/a |
| gov_gtg2_base_z | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_capital_weight | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_gtg2_droop_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_dynamic_offset_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_dynamic_offset_min | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_feedback_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_gtg2_grid_droop_gain | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_grid_droop_max | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_integral_clamp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_kd | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_ki | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_kp | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_outcome_window | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_target_m | advisory | advisory | n/a |
| gov_gtg2_target_r | dynamic | dynamic | always (PAPER_APPLY native plant) |
| gov_gtg2_z_exit | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gov_path_error_sigma | constant_or_coefficient | constant_or_coefficient | always |
| gov_v3_base_gap_r | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_gamma_fast | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_gamma_slow | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_grace_bars | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_kappa | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_mfe_activation_r | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_minimum_gap_r | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_noise_floor_mult | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| gov_v3_tau_error_multiplier | constant_or_coefficient | constant_or_coefficient | governor_position_mode='v3' |
| grid_vix_derate_start | constant_or_coefficient | constant_or_coefficient | always |
| gsch_base_z_lower_offset | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_base_z_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_base_z_trend_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_base_z_upper_offset | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_gain_max_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_gain_min_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_gain_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_max_max_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_max_min_scale | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_max_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_r_min_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_droop_r_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_integral_error_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_integral_max_scale | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_integral_min_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_integral_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kd_error_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kd_scale_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kd_scale_min | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kd_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_ki_divisor_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_ki_divisor_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_ki_error_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_ki_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kp_error_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kp_scale_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kp_scale_min | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_kp_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_offset_min_lower_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_offset_min_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_offset_min_upper_scale | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_target_max_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_target_min_scale | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_target_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_target_trend_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_window_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_window_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| gsch_window_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_correlation_window | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_max_steam_pressure_pu | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_min_correlation_samples | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_min_penalty_factor | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_min_steam_pressure_pu | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_pressure_time_constant_bars | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_recovery_fraction | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| hrsg_rho_crit | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| micom_crash_min_pct | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| micom_crash_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| micom_drawdown_min_pct | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| micom_drawdown_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| micom_max_daily_fleet_drawdown_pct | dynamic | dynamic | always (PAPER_APPLY native plant) |
| micom_max_regime_vol_z | dynamic | dynamic | always (PAPER_APPLY native plant) |
| micom_nifty_crash_rate_pct | dynamic | dynamic | always (PAPER_APPLY native plant) |
| micom_overfrequency_ratio | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| micom_vol_z_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| micom_vol_z_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| mpc_schedule_kd_gain | constant_or_coefficient | constant_or_coefficient | always |
| mpc_schedule_ki_gain | constant_or_coefficient | constant_or_coefficient | always |
| mpc_schedule_kp_gain | constant_or_coefficient | constant_or_coefficient | always |
| mv_fsr_entry_threshold | constant_or_coefficient | constant_or_coefficient | always |
| mv_fsr_exit_threshold | constant_or_coefficient | constant_or_coefficient | always |
| mv_fsra_slope | constant_or_coefficient | constant_or_coefficient | always |
| mv_fsrt_slope | constant_or_coefficient | constant_or_coefficient | always |
| pid_kd_entry | dynamic | dynamic | always |
| pid_kd_exit | dynamic | dynamic | always |
| pid_ki_entry | dynamic | dynamic | always |
| pid_ki_exit | dynamic | dynamic | always |
| pid_kp_entry | dynamic | dynamic | always |
| pid_kp_exit | dynamic | dynamic | always |
| plant_bay_lockout_consecutive_stops | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_loss_cooldown_base_bars | dynamic | dynamic | always (PAPER_APPLY native plant) |
| plant_loss_cooldown_max_bars | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_loss_cooldown_min_bars | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_loss_cooldown_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_loss_cooldown_stress_offset | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_stop_floor_price_fraction | advisory | advisory | n/a |
| plant_target_cooldown_base_bars | dynamic | dynamic | always (PAPER_APPLY native plant) |
| plant_target_cooldown_max_bars | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_target_cooldown_min_bars | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_target_cooldown_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_target_cooldown_stress_offset | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| plant_target_min_reward_risk | advisory | advisory | n/a |
| psch_concurrent_positions_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_daily_loss_multiplier_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_data_age_min_seconds | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_data_age_stress_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_dispatch_ceiling_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_dispatch_floor_ceiling_ratio | inactive | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_dispatch_floor_max | inactive | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_dispatch_floor_min | inactive | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_dispatch_floor_stress_gain | inactive | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_drawdown_halt_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_drawdown_halt_stress_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_entry_conf_chop_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_entry_conf_max | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_entry_conf_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_entry_conf_stress_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_gross_exposure_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_max_hold_max | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_max_hold_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_max_hold_stress_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_max_hold_trend_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_min_hold_chop_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_min_hold_max | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_min_hold_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_min_hold_turbulent_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_min_rr_max | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_min_rr_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_min_rr_stress_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_order_timeout_max_seconds | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_order_timeout_min_seconds | inactive | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_order_timeout_stress_gain | inactive | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_slippage_max_fraction | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_slippage_min_fraction | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_slippage_vol_gain | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| psch_symbol_exposure_min | constant_or_coefficient | constant_or_coefficient | dynamic_profile_mode='consume' |
| sel_adverse_min_bars | constant_or_coefficient | constant_or_coefficient | unit_protection_mode='pre_sync_and_in_flight' |
| sel_adverse_stress_gain | constant_or_coefficient | constant_or_coefficient | unit_protection_mode='pre_sync_and_in_flight' |
| sel_max_adverse_bars | dynamic | dynamic | unit_protection_mode='pre_sync_and_in_flight' |
| sel_max_slippage_fraction | dynamic | dynamic | unit_protection_mode='pre_sync_and_in_flight' |
| sel_max_spread_fraction | dynamic | dynamic | always (PAPER_APPLY native plant) |
| sel_slippage_min_fraction | constant_or_coefficient | constant_or_coefficient | unit_protection_mode='pre_sync_and_in_flight' |
| sel_slippage_stress_gain | constant_or_coefficient | constant_or_coefficient | unit_protection_mode='pre_sync_and_in_flight' |
| sel_spread_min_fraction | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| sel_spread_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| sel_stale_tick_limit_seconds | dynamic | dynamic | always (PAPER_APPLY native plant) |
| sel_stale_tick_min_seconds | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| sel_stale_tick_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| sel_vol_surge_min_mult | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| sel_vol_surge_mult | dynamic | dynamic | always (PAPER_APPLY native plant) |
| sel_vol_surge_stress_gain | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| studies_pid_kd | constant_or_coefficient | constant_or_coefficient | always |
| studies_pid_ki | constant_or_coefficient | constant_or_coefficient | always |
| studies_pid_kp | constant_or_coefficient | constant_or_coefficient | always |
| studies_pid_output_clamp | constant_or_coefficient | constant_or_coefficient | always |
| studies_weight_max | new | constant_or_coefficient | shared study signal evaluation |
| studies_weight_min | new | constant_or_coefficient | shared study signal evaluation |
| symbol_loss_cooldown_bars | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| symbol_trip_consecutive_losses | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier1_atr_floor_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier1_atr_floor_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier1_normalized_vol_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier1_normalized_vol_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier1_slippage_scale_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier1_slippage_scale_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier3_kd_scale_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier3_kd_scale_min | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier3_ki_divisor_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier3_ki_divisor_min | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier3_kp_scale_max | constant_or_coefficient | constant_or_coefficient | always (PAPER_APPLY native plant) |
| tier3_kp_scale_min | inactive | constant_or_coefficient | always (PAPER_APPLY native plant) |
