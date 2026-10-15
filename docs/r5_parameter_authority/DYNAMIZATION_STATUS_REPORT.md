# R5 dynamization status: every parameter

Generated from `phase2_parameter_classification.json` (registry fc04a433…, commit b177c9d). It covers 312 parameters: 291 R5 controller rows and 21 pre-existing registry rows they extend.

## What "dynamic" means here

A parameter is DYNAMIC when the value the controller actually uses is recomputed every plant bar (or every Box 6 plan) from live, causal inputs. A COEFFICIENT is a fixed number inside one of those formulas, such as a gain or a clip, and is constant by design: the formula's output moves every bar, the coefficient does not. Registering a constant in the registry does NOT make it dynamic. Registration only means one owner, no hidden literal, fail-closed startup and a sensitivity test.

| Status | Count | Value used at runtime changes every bar? |
|---|---|---|
| A. Dynamic base: per-bar schedule (governor, AVR, cooldowns) | 69 | YES |
| A. Dynamic safety envelope: per-bar, tighten-only (AVR, SEL-300G, MiCOM) | 14 | YES |
| A. Dynamic Box 6 / exit PID gains (per plan, per bar) | 6 | YES |
| B. Coefficient of a per-bar formula | 134 | NO, constant by design (it shapes a dynamic output) |
| C. Static controller constant: no dynamic formula specified | 40 | NO, not dynamized (no spec) |
| C. Static safety limit: hard limit, no dynamic formula specified | 16 | NO, not dynamized (no spec) |
| D. Registered but no actuator (ADVISORY / INACTIVE) | 33 | NO, does nothing at runtime |
| **Total** | 312 | |

Also newly dynamic in `dynamic_profile_mode="consume"` (these are not controller rows, so they are not counted above): the run's `entry_confidence_threshold`, `min_risk_reward_ratio`, `min_hold_bars` and `max_hold_bars`. The safety-contract `safety_drawdown_halt_threshold`, `max_daily_loss_rupees` (×multiplier), `max_concurrent_positions`, `max_gross_exposure_fraction`, `max_exposure_per_symbol_fraction`, slippage tolerance and market-data age now tighten per bar. The order timeout is NOT dynamic (see D).


## A. Dynamic: recomputed every bar, consumed

| # | Parameter | Default | Per-bar formula | Inputs | Consumer / effect |
|---|---|---|---|---|---|
| 1 | `avr_integral_clamp_base` | 0.5 | clip(base - gain*control_stress, avr_integral_clamp_min, base) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 2 | `avr_kd_base` | 0.1 | clip(base + avr_kd_stress_gain*control_stress, base, avr_kd_max) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 3 | `avr_ki_base` | 0.15 | clip(base/(1 + avr_ki_stress_gain*control_stress), avr_ki_min, base) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 4 | `avr_kp_base` | 0.8 | clip(base + avr_kp_stress_gain*control_stress, base, avr_kp_max) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 5 | `avr_vol_scalar_max_base` | 2.0 | vol_scalar_max = clip(base*risk_derate, avr_vol_scalar_max_min, base) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 6 | `avr_z_strength_cap_base` | 1.5 | clip(base*risk_derate, avr_z_strength_cap_min, base) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 7 | `avr_z_strength_denominator_base` | 1.8 | clip(base*(1 + gain*stress), base, avr_z_strength_denominator_max) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 8 | `gov_bpstg_base_z` | -1.8 | clip(base + gsch_base_z_trend_gain*trend_bias - gsch_base_z_stress_gain*stress, base - lower_offset, base + upper_offset) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 9 | `gov_bpstg_droop_r` | 0.075 | clip(base*(1 - gsch_droop_r_stress_gain*stress), base*gsch_droop_r_min_scale, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 10 | `gov_bpstg_dynamic_offset_max` | 0.15 | clip(base*risk_derate, 0, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 11 | `gov_bpstg_dynamic_offset_min` | -0.45 | clip(base*(1 + gsch_offset_min_stress_gain*stress), base*lower_scale, base*upper_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 12 | `gov_bpstg_grid_droop_gain` | 0.05 | clip(base*vol*(1 + gsch_droop_gain_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 13 | `gov_bpstg_grid_droop_max` | 0.25 | clip(base*(1 + gsch_droop_max_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 14 | `gov_bpstg_integral_clamp` | 3.0 | clip(base/(1 + gsch_integral_stress_gain*stress + gsch_integral_error_gain*\|e\|), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 15 | `gov_bpstg_kd` | 0.2 | base*clip(1 + gsch_kd_error_gain*\|e\| + gsch_kd_stress_gain*stress, gsch_kd_scale_min, gsch_kd_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 16 | `gov_bpstg_ki` | 0.04 | base/clip(1 + gsch_ki_error_gain*\|e\| + gsch_ki_stress_gain*stress, gsch_ki_divisor_min, gsch_ki_divisor_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 17 | `gov_bpstg_kp` | 0.8 | base*clip(1 + gsch_kp_error_gain*\|e\| + gsch_kp_stress_gain*stress, gsch_kp_scale_min, gsch_kp_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 18 | `gov_bpstg_outcome_window` | 6 | round(clip(base*(1 + gsch_window_stress_gain*stress), gsch_window_min, gsch_window_max)) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 19 | `gov_bpstg_target_r` | 0.3 | clip(base*(1 + gsch_target_trend_gain*trend_bias - gsch_target_stress_gain*stress), base*gsch_target_min_scale, base*gsch_target_max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 20 | `gov_cstg1_base_z` | -1.85 | clip(base + gsch_base_z_trend_gain*trend_bias - gsch_base_z_stress_gain*stress, base - lower_offset, base + upper_offset) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 21 | `gov_cstg1_droop_r` | 0.055 | clip(base*(1 - gsch_droop_r_stress_gain*stress), base*gsch_droop_r_min_scale, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 22 | `gov_cstg1_dynamic_offset_max` | 0.15 | clip(base*risk_derate, 0, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 23 | `gov_cstg1_dynamic_offset_min` | -0.45 | clip(base*(1 + gsch_offset_min_stress_gain*stress), base*lower_scale, base*upper_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 24 | `gov_cstg1_grid_droop_gain` | 0.05 | clip(base*vol*(1 + gsch_droop_gain_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 25 | `gov_cstg1_grid_droop_max` | 0.25 | clip(base*(1 + gsch_droop_max_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 26 | `gov_cstg1_integral_clamp` | 3.0 | clip(base/(1 + gsch_integral_stress_gain*stress + gsch_integral_error_gain*\|e\|), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 27 | `gov_cstg1_kd` | 0.15 | base*clip(1 + gsch_kd_error_gain*\|e\| + gsch_kd_stress_gain*stress, gsch_kd_scale_min, gsch_kd_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 28 | `gov_cstg1_ki` | 0.12 | base/clip(1 + gsch_ki_error_gain*\|e\| + gsch_ki_stress_gain*stress, gsch_ki_divisor_min, gsch_ki_divisor_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 29 | `gov_cstg1_kp` | 0.9 | base*clip(1 + gsch_kp_error_gain*\|e\| + gsch_kp_stress_gain*stress, gsch_kp_scale_min, gsch_kp_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 30 | `gov_cstg1_outcome_window` | 6 | round(clip(base*(1 + gsch_window_stress_gain*stress), gsch_window_min, gsch_window_max)) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 31 | `gov_cstg1_target_r` | 0.3 | clip(base*(1 + gsch_target_trend_gain*trend_bias - gsch_target_stress_gain*stress), base*gsch_target_min_scale, base*gsch_target_max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 32 | `gov_cstg2_base_z` | -1.95 | clip(base + gsch_base_z_trend_gain*trend_bias - gsch_base_z_stress_gain*stress, base - lower_offset, base + upper_offset) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 33 | `gov_cstg2_droop_r` | 0.055 | clip(base*(1 - gsch_droop_r_stress_gain*stress), base*gsch_droop_r_min_scale, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 34 | `gov_cstg2_dynamic_offset_max` | 0.15 | clip(base*risk_derate, 0, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 35 | `gov_cstg2_dynamic_offset_min` | -0.45 | clip(base*(1 + gsch_offset_min_stress_gain*stress), base*lower_scale, base*upper_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 36 | `gov_cstg2_grid_droop_gain` | 0.05 | clip(base*vol*(1 + gsch_droop_gain_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 37 | `gov_cstg2_grid_droop_max` | 0.25 | clip(base*(1 + gsch_droop_max_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 38 | `gov_cstg2_integral_clamp` | 3.0 | clip(base/(1 + gsch_integral_stress_gain*stress + gsch_integral_error_gain*\|e\|), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 39 | `gov_cstg2_kd` | 0.25 | base*clip(1 + gsch_kd_error_gain*\|e\| + gsch_kd_stress_gain*stress, gsch_kd_scale_min, gsch_kd_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 40 | `gov_cstg2_ki` | 0.07 | base/clip(1 + gsch_ki_error_gain*\|e\| + gsch_ki_stress_gain*stress, gsch_ki_divisor_min, gsch_ki_divisor_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 41 | `gov_cstg2_kp` | 1.0 | base*clip(1 + gsch_kp_error_gain*\|e\| + gsch_kp_stress_gain*stress, gsch_kp_scale_min, gsch_kp_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 42 | `gov_cstg2_outcome_window` | 6 | round(clip(base*(1 + gsch_window_stress_gain*stress), gsch_window_min, gsch_window_max)) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 43 | `gov_cstg2_target_r` | 0.3 | clip(base*(1 + gsch_target_trend_gain*trend_bias - gsch_target_stress_gain*stress), base*gsch_target_min_scale, base*gsch_target_max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 44 | `gov_gtg1_base_z` | -1.9 | clip(base + gsch_base_z_trend_gain*trend_bias - gsch_base_z_stress_gain*stress, base - lower_offset, base + upper_offset) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 45 | `gov_gtg1_droop_r` | 0.04 | clip(base*(1 - gsch_droop_r_stress_gain*stress), base*gsch_droop_r_min_scale, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 46 | `gov_gtg1_dynamic_offset_max` | 0.15 | clip(base*risk_derate, 0, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 47 | `gov_gtg1_dynamic_offset_min` | -0.45 | clip(base*(1 + gsch_offset_min_stress_gain*stress), base*lower_scale, base*upper_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 48 | `gov_gtg1_grid_droop_gain` | 0.05 | clip(base*vol*(1 + gsch_droop_gain_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 49 | `gov_gtg1_grid_droop_max` | 0.25 | clip(base*(1 + gsch_droop_max_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 50 | `gov_gtg1_integral_clamp` | 3.0 | clip(base/(1 + gsch_integral_stress_gain*stress + gsch_integral_error_gain*\|e\|), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 51 | `gov_gtg1_kd` | 0.3 | base*clip(1 + gsch_kd_error_gain*\|e\| + gsch_kd_stress_gain*stress, gsch_kd_scale_min, gsch_kd_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 52 | `gov_gtg1_ki` | 0.05 | base/clip(1 + gsch_ki_error_gain*\|e\| + gsch_ki_stress_gain*stress, gsch_ki_divisor_min, gsch_ki_divisor_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 53 | `gov_gtg1_kp` | 1.2 | base*clip(1 + gsch_kp_error_gain*\|e\| + gsch_kp_stress_gain*stress, gsch_kp_scale_min, gsch_kp_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 54 | `gov_gtg1_outcome_window` | 6 | round(clip(base*(1 + gsch_window_stress_gain*stress), gsch_window_min, gsch_window_max)) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 55 | `gov_gtg1_target_r` | 0.3 | clip(base*(1 + gsch_target_trend_gain*trend_bias - gsch_target_stress_gain*stress), base*gsch_target_min_scale, base*gsch_target_max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 56 | `gov_gtg2_base_z` | -2.0 | clip(base + gsch_base_z_trend_gain*trend_bias - gsch_base_z_stress_gain*stress, base - lower_offset, base + upper_offset) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 57 | `gov_gtg2_droop_r` | 0.04 | clip(base*(1 - gsch_droop_r_stress_gain*stress), base*gsch_droop_r_min_scale, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 58 | `gov_gtg2_dynamic_offset_max` | 0.15 | clip(base*risk_derate, 0, base) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 59 | `gov_gtg2_dynamic_offset_min` | -0.45 | clip(base*(1 + gsch_offset_min_stress_gain*stress), base*lower_scale, base*upper_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 60 | `gov_gtg2_grid_droop_gain` | 0.05 | clip(base*vol*(1 + gsch_droop_gain_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 61 | `gov_gtg2_grid_droop_max` | 0.25 | clip(base*(1 + gsch_droop_max_stress_gain*stress), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 62 | `gov_gtg2_integral_clamp` | 3.0 | clip(base/(1 + gsch_integral_stress_gain*stress + gsch_integral_error_gain*\|e\|), base*min_scale, base*max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 63 | `gov_gtg2_kd` | 0.6 | base*clip(1 + gsch_kd_error_gain*\|e\| + gsch_kd_stress_gain*stress, gsch_kd_scale_min, gsch_kd_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 64 | `gov_gtg2_ki` | 0.08 | base/clip(1 + gsch_ki_error_gain*\|e\| + gsch_ki_stress_gain*stress, gsch_ki_divisor_min, gsch_ki_divisor_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 65 | `gov_gtg2_kp` | 1.5 | base*clip(1 + gsch_kp_error_gain*\|e\| + gsch_kp_stress_gain*stress, gsch_kp_scale_min, gsch_kp_scale_max) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 66 | `gov_gtg2_outcome_window` | 6 | round(clip(base*(1 + gsch_window_stress_gain*stress), gsch_window_min, gsch_window_max)) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 67 | `gov_gtg2_target_r` | 0.3 | clip(base*(1 + gsch_target_trend_gain*trend_bias - gsch_target_stress_gain*stress), base*gsch_target_min_scale, base*gsch_target_max_scale) | stress (HMM turbulent / chop share, excess normalized vol), trend_bias, \|mean governor error\|, risk_derate (stress, fleet drawdown) | governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit |
| 68 | `plant_loss_cooldown_base_bars` | 15 | round(clip(base*(offset + gain*stress), min, max)) | stress | bay cooldown -> entry admission, HRSG availability -> capital |
| 69 | `plant_target_cooldown_base_bars` | 3 | round(clip(base*(offset + gain*stress), min, max)) | stress | plant admission target suggestion (advisory, not actuated) |
| 70 | `avr_excitation_max_base` | 1.0 | clip(base - gain*control_stress, min, base) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 71 | `avr_excitation_min_base` | 0.1 | clip(base + gain*control_stress, base, max) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 72 | `avr_loading_fraction_base` | 0.15 | loading = clip(base*risk_derate, avr_loading_fraction_min, base); control_stress = 1 - loading/base | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 73 | `avr_oel_capital_fraction` | 0.4 | oel_fraction = clip(base*risk_derate, avr_oel_fraction_min, base) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 74 | `avr_overvoltage_trip_base_pu` | 1.1 | clip(base - gain*control_stress, min, base) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 75 | `avr_undervoltage_trip_base_pu` | 0.9 | clip(base + gain*control_stress, base, max) | risk_derate, stress (control_stress = 1 - loading/base) | AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit |
| 76 | `micom_max_daily_fleet_drawdown_pct` | 0.02 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | MiCOM trip -> grid intertie, entry block, protective exit |
| 77 | `micom_max_regime_vol_z` | 3.0 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | MiCOM trip -> grid intertie, entry block, protective exit |
| 78 | `micom_nifty_crash_rate_pct` | 0.015 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | MiCOM trip -> grid intertie, entry block, protective exit |
| 79 | `sel_max_adverse_bars` | 4 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | SEL-300G trip -> entry rejection, unit breaker, protective exit |
| 80 | `sel_max_slippage_fraction` | 0.0035 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | SEL-300G trip -> entry rejection, unit breaker, protective exit |
| 81 | `sel_max_spread_fraction` | 0.005 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | SEL-300G trip -> entry rejection, unit breaker, protective exit |
| 82 | `sel_stale_tick_limit_seconds` | 120.0 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | SEL-300G trip -> entry rejection, unit breaker, protective exit |
| 83 | `sel_vol_surge_mult` | 3.5 | clip(base*(1 - gain*stress), min, base) (tighten only) | stress | SEL-300G trip -> entry rejection, unit breaker, protective exit |
| 84 | `pid_kd_entry` | 0.08 | Kd*clip(1 + mpc_schedule_kd_gain*\|e\|, tier3) x R5 Kd scale | same | same |
| 85 | `pid_kd_exit` | 0.06 | gain x R5 Kd scale | R5 snapshot | same |
| 86 | `pid_ki_entry` | 0.05 | Ki/clip(1 + mpc_schedule_ki_gain*\|e\|, tier3) x R5 Ki scale | same | same |
| 87 | `pid_ki_exit` | 0.04 | gain x R5 Ki scale | R5 snapshot | same |
| 88 | `pid_kp_entry` | 0.15 | per Box 6 plan: Kp*clip(1 + mpc_schedule_kp_gain*\|conf - baseline\|, tier3 envelope) x R5 bay gain scale (per PAPER_APPLY bar) | confidence error, R5 snapshot kp/spec ratio | order reference price (A); booked stop/target with entry_fill_anchor_mode='box6_levels' (B/C) |
| 89 | `pid_kp_exit` | 0.12 | gain x R5 bay gain scale (per PAPER_APPLY bar) | R5 snapshot | plan stop/target distances (all engines); trailing stop (advisory under full authority) |

## B. Coefficients: constant by design, each shapes a dynamic output

| # | Parameter | Default | Class | Role in the per-bar formula | Mode |
|---|---|---|---|---|---|
| 1 | `avr_excitation_max_min` | 0.75 | FIXED_SAFETY | OEL excitation ceiling lower clip | always (PAPER_APPLY native plant) |
| 2 | `avr_excitation_max_stress_gain` | 0.25 | FIXED_SAFETY | OEL excitation ceiling stress gain | always (PAPER_APPLY native plant) |
| 3 | `avr_excitation_min_max` | 0.2 | FIXED_SAFETY | UEL excitation floor upper clip | always (PAPER_APPLY native plant) |
| 4 | `avr_excitation_min_stress_gain` | 0.1 | FIXED_SAFETY | UEL excitation floor stress gain | always (PAPER_APPLY native plant) |
| 5 | `avr_integral_clamp_min` | 0.3 | TUNABLE | AVR integral clamp lower clip | always (PAPER_APPLY native plant) |
| 6 | `avr_integral_clamp_stress_gain` | 0.2 | TUNABLE | AVR integral clamp stress gain | always (PAPER_APPLY native plant) |
| 7 | `avr_kd_max` | 0.2 | TUNABLE | AVR Kd upper clip | always (PAPER_APPLY native plant) |
| 8 | `avr_kd_stress_gain` | 0.1 | TUNABLE | AVR Kd stress gain | always (PAPER_APPLY native plant) |
| 9 | `avr_ki_min` | 0.075 | TUNABLE | AVR Ki lower clip | always (PAPER_APPLY native plant) |
| 10 | `avr_ki_stress_gain` | 1.0 | TUNABLE | AVR Ki stress divisor gain | always (PAPER_APPLY native plant) |
| 11 | `avr_kp_max` | 1.4 | TUNABLE | AVR Kp upper clip | always (PAPER_APPLY native plant) |
| 12 | `avr_kp_stress_gain` | 0.6 | TUNABLE | AVR Kp stress gain | always (PAPER_APPLY native plant) |
| 13 | `avr_loading_fraction_min` | 0.05 | TUNABLE | loading = clip(base * risk_derate, min, base) | always (PAPER_APPLY native plant) |
| 14 | `avr_oel_fraction_min` | 0.15 | TUNABLE | OEL fraction = clip(base * risk_derate, min, base) | always (PAPER_APPLY native plant) |
| 15 | `avr_overvoltage_trip_min_pu` | 1.05 | FIXED_SAFETY | ANSI 59 trip lower clip | always (PAPER_APPLY native plant) |
| 16 | `avr_overvoltage_trip_stress_gain` | 0.05 | FIXED_SAFETY | ANSI 59 stress tightening gain | always (PAPER_APPLY native plant) |
| 17 | `avr_uel_min_notional_inr` | 15000.0 | FIXED_SAFETY | UEL minimum commercial notional (rupees); smaller lots lock out | always (PAPER_APPLY native plant) |
| 18 | `avr_uel_multiplier_max` | 1.75 | TUNABLE | UEL multiplier upper clip | always (PAPER_APPLY native plant) |
| 19 | `avr_uel_multiplier_stress_gain` | 0.75 | TUNABLE | UEL multiplier = clip(1 + gain * stress, 1, max) | always (PAPER_APPLY native plant) |
| 20 | `avr_undervoltage_trip_max_pu` | 0.95 | FIXED_SAFETY | ANSI 27 trip upper clip | always (PAPER_APPLY native plant) |
| 21 | `avr_undervoltage_trip_stress_gain` | 0.05 | FIXED_SAFETY | ANSI 27 stress tightening gain | always (PAPER_APPLY native plant) |
| 22 | `avr_vol_scalar_max_min` | 0.75 | TUNABLE | vol scalar upper clip lower bound | always (PAPER_APPLY native plant) |
| 23 | `avr_vol_scalar_min` | 0.2 | TUNABLE | lot volatility scalar lower clip | always (PAPER_APPLY native plant) |
| 24 | `avr_z_strength_cap_min` | 0.75 | TUNABLE | z strength cap lower clip | always (PAPER_APPLY native plant) |
| 25 | `avr_z_strength_denominator_max` | 2.7 | TUNABLE | z strength denominator upper clip | always (PAPER_APPLY native plant) |
| 26 | `avr_z_strength_denominator_stress_gain` | 0.5 | TUNABLE | z strength denominator stress gain | always (PAPER_APPLY native plant) |
| 27 | `dyn_risk_derate_drawdown_gain` | 0.25 | TUNABLE | risk_derate drawdown gain | always (PAPER_APPLY native plant) |
| 28 | `dyn_risk_derate_lower_bound` | 0.4 | FIXED_SAFETY | risk_derate floor | always (PAPER_APPLY native plant) |
| 29 | `dyn_risk_derate_stress_gain` | 0.45 | TUNABLE | risk_derate = clip(1 - g_s * stress - g_d * drawdown, floor, 1) | always (PAPER_APPLY native plant) |
| 30 | `dyn_stress_chop_weight` | 0.25 | TUNABLE | stress chop weight | always (PAPER_APPLY native plant) |
| 31 | `dyn_stress_excess_vol_weight` | 0.2 | TUNABLE | stress excess-volatility weight | always (PAPER_APPLY native plant) |
| 32 | `dyn_stress_turbulent_weight` | 0.55 | TUNABLE | stress = clip(w_t * turbulent + w_c * chop + w_v * excess_vol, 0, 1) | always (PAPER_APPLY native plant) |
| 33 | `dyn_vol_max` | 2.5 | FIXED_SAFETY | normalized volatility upper clip | always (PAPER_APPLY native plant) |
| 34 | `dyn_vol_min` | 0.5 | FIXED_SAFETY | normalized volatility lower clip | always (PAPER_APPLY native plant) |
| 35 | `gsch_base_z_lower_offset` | 0.5 | TUNABLE | base_z lower clip: base - offset | always (PAPER_APPLY native plant) |
| 36 | `gsch_base_z_stress_gain` | 0.25 | TUNABLE | base_z stress gain | always (PAPER_APPLY native plant) |
| 37 | `gsch_base_z_trend_gain` | 0.1 | TUNABLE | base_z += trend_gain * trend_bias - stress_gain * stress | always (PAPER_APPLY native plant) |
| 38 | `gsch_base_z_upper_offset` | 0.1 | TUNABLE | base_z upper clip: base + offset | always (PAPER_APPLY native plant) |
| 39 | `gsch_droop_gain_max_scale` | 2.0 | TUNABLE | grid_droop_gain upper clip (fraction of base) | always (PAPER_APPLY native plant) |
| 40 | `gsch_droop_gain_min_scale` | 0.5 | TUNABLE | grid_droop_gain lower clip (fraction of base) | always (PAPER_APPLY native plant) |
| 41 | `gsch_droop_gain_stress_gain` | 0.5 | TUNABLE | grid_droop_gain *= vol * (1 + gain * stress) | always (PAPER_APPLY native plant) |
| 42 | `gsch_droop_max_max_scale` | 1.6 | TUNABLE | grid_droop_max upper clip (fraction of base) | always (PAPER_APPLY native plant) |
| 43 | `gsch_droop_max_stress_gain` | 0.6 | TUNABLE | grid_droop_max *= 1 + gain * stress | always (PAPER_APPLY native plant) |
| 44 | `gsch_droop_r_min_scale` | 0.6 | TUNABLE | droop_r lower clip (fraction of base); upper clip is the base (never widened) | always (PAPER_APPLY native plant) |
| 45 | `gsch_droop_r_stress_gain` | 0.25 | TUNABLE | droop_r *= 1 - gain * stress | always (PAPER_APPLY native plant) |
| 46 | `gsch_integral_error_gain` | 0.5 | TUNABLE | integral_clamp error divisor gain | always (PAPER_APPLY native plant) |
| 47 | `gsch_integral_min_scale` | 0.5 | TUNABLE | integral_clamp lower clip as a fraction of the bay base | always (PAPER_APPLY native plant) |
| 48 | `gsch_integral_stress_gain` | 0.8 | TUNABLE | integral_clamp /= 1 + stress_gain * stress + error_gain * \|e\| | always (PAPER_APPLY native plant) |
| 49 | `gsch_kd_error_gain` | 0.7 | TUNABLE | Kd *= clip(1 + error_gain * \|e\| + stress_gain * stress, min, max) | always (PAPER_APPLY native plant) |
| 50 | `gsch_kd_scale_max` | 2.0 | TUNABLE | Kd schedule upper clip | always (PAPER_APPLY native plant) |
| 51 | `gsch_kd_stress_gain` | 0.5 | TUNABLE | Kd schedule stress gain | always (PAPER_APPLY native plant) |
| 52 | `gsch_ki_divisor_max` | 3.5 | TUNABLE | Ki divisor upper clip | always (PAPER_APPLY native plant) |
| 53 | `gsch_ki_divisor_min` | 1.0 | TUNABLE | Ki divisor lower clip (>= 1: the schedule can only bleed Ki) | always (PAPER_APPLY native plant) |
| 54 | `gsch_ki_error_gain` | 1.5 | TUNABLE | Ki /= clip(1 + error_gain * \|e\| + stress_gain * stress, min, max) | always (PAPER_APPLY native plant) |
| 55 | `gsch_ki_stress_gain` | 1.0 | TUNABLE | Ki schedule stress gain | always (PAPER_APPLY native plant) |
| 56 | `gsch_kp_error_gain` | 1.0 | TUNABLE | Kp *= clip(1 + error_gain * \|e\| + stress_gain * stress, min, max) | always (PAPER_APPLY native plant) |
| 57 | `gsch_kp_scale_max` | 2.2 | TUNABLE | Kp schedule upper clip | always (PAPER_APPLY native plant) |
| 58 | `gsch_kp_stress_gain` | 0.25 | TUNABLE | Kp schedule stress gain | always (PAPER_APPLY native plant) |
| 59 | `gsch_offset_min_lower_scale` | 1.5 | TUNABLE | dynamic_offset_min clip: base * lower_scale (more negative) | always (PAPER_APPLY native plant) |
| 60 | `gsch_offset_min_stress_gain` | 0.25 | TUNABLE | dynamic_offset_min *= 1 + gain * stress | always (PAPER_APPLY native plant) |
| 61 | `gsch_target_max_scale` | 1.25 | TUNABLE | target_r upper clip as a fraction of the bay base | always (PAPER_APPLY native plant) |
| 62 | `gsch_target_min_scale` | 0.7 | TUNABLE | target_r lower clip as a fraction of the bay base | always (PAPER_APPLY native plant) |
| 63 | `gsch_target_stress_gain` | 0.15 | TUNABLE | target_r stress reduction gain | always (PAPER_APPLY native plant) |
| 64 | `gsch_target_trend_gain` | 0.15 | TUNABLE | target_r *= 1 + gain * trend_bias - stress_gain * stress | always (PAPER_APPLY native plant) |
| 65 | `gsch_window_max` | 12 | TUNABLE | outcome_window upper clip (trades) | always (PAPER_APPLY native plant) |
| 66 | `gsch_window_min` | 3 | TUNABLE | outcome_window lower clip (trades) | always (PAPER_APPLY native plant) |
| 67 | `gsch_window_stress_gain` | 0.8 | TUNABLE | outcome_window *= 1 + gain * stress | always (PAPER_APPLY native plant) |
| 68 | `micom_crash_min_pct` | 0.008 | FIXED_SAFETY | crash limit lower clip | always (PAPER_APPLY native plant) |
| 69 | `micom_crash_stress_gain` | 0.25 | FIXED_SAFETY | crash limit stress tightening gain | always (PAPER_APPLY native plant) |
| 70 | `micom_drawdown_min_pct` | 0.01 | FIXED_SAFETY | drawdown limit lower clip | always (PAPER_APPLY native plant) |
| 71 | `micom_drawdown_stress_gain` | 0.35 | FIXED_SAFETY | drawdown limit = clip(base * (1 - gain * stress), min, base) | always (PAPER_APPLY native plant) |
| 72 | `micom_vol_z_min` | 1.8 | FIXED_SAFETY | vol z limit lower clip | always (PAPER_APPLY native plant) |
| 73 | `micom_vol_z_stress_gain` | 0.3 | FIXED_SAFETY | vol z limit stress tightening gain | always (PAPER_APPLY native plant) |
| 74 | `mpc_schedule_kd_gain` | 0.8 | TUNABLE | coefficient of the per-plan Kd schedule | always |
| 75 | `mpc_schedule_ki_gain` | 2.0 | TUNABLE | coefficient of the per-plan Ki schedule | always |
| 76 | `mpc_schedule_kp_gain` | 1.2 | TUNABLE | coefficient of the per-plan Kp schedule | always |
| 77 | `mv_fsra_slope` | 0.25 | TUNABLE | FSRA = base - slope*velocity (per bar) | always |
| 78 | `mv_fsrt_slope` | 0.5 | TUNABLE | FSRT = 1 - drawdown/span*slope (per bar) | always |
| 79 | `plant_loss_cooldown_max_bars` | 30 | TUNABLE | loss cooldown upper clip | always (PAPER_APPLY native plant) |
| 80 | `plant_loss_cooldown_min_bars` | 8 | TUNABLE | loss cooldown lower clip | always (PAPER_APPLY native plant) |
| 81 | `plant_loss_cooldown_stress_gain` | 0.9 | TUNABLE | loss cooldown stress gain | always (PAPER_APPLY native plant) |
| 82 | `plant_loss_cooldown_stress_offset` | 0.8 | TUNABLE | loss cooldown = base * (offset + gain * stress) | always (PAPER_APPLY native plant) |
| 83 | `plant_target_cooldown_max_bars` | 8 | TUNABLE | target cooldown upper clip | always (PAPER_APPLY native plant) |
| 84 | `plant_target_cooldown_min_bars` | 2 | TUNABLE | target cooldown lower clip | always (PAPER_APPLY native plant) |
| 85 | `plant_target_cooldown_stress_gain` | 0.7 | TUNABLE | target cooldown stress gain | always (PAPER_APPLY native plant) |
| 86 | `plant_target_cooldown_stress_offset` | 0.8 | TUNABLE | target cooldown = base * (offset + gain * stress) | always (PAPER_APPLY native plant) |
| 87 | `psch_concurrent_positions_min` | 1.0 | FIXED_SAFETY | concurrent positions = round(clip(base*risk_derate, min, base)) | dynamic_profile_mode='consume' |
| 88 | `psch_daily_loss_multiplier_min` | 0.4 | FIXED_SAFETY | daily loss limit multiplier = clip(risk_derate, min, 1) | dynamic_profile_mode='consume' |
| 89 | `psch_data_age_min_seconds` | 10.0 | FIXED_SAFETY | max market-data age lower clip | dynamic_profile_mode='consume' |
| 90 | `psch_data_age_stress_gain` | 0.75 | FIXED_SAFETY | max market-data age = clip(base/(1 + gain*stress), min, base) | dynamic_profile_mode='consume' |
| 91 | `psch_dispatch_ceiling_min` | 0.15 | FIXED_SAFETY | dispatch ceiling = clip(base*risk_derate, min, base); consumed as the sector-dispatch per-bay cap | dynamic_profile_mode='consume' |
| 92 | `psch_drawdown_halt_min` | 0.1 | FIXED_SAFETY | drawdown halt lower clip | dynamic_profile_mode='consume' |
| 93 | `psch_drawdown_halt_stress_gain` | 0.3 | FIXED_SAFETY | drawdown halt = clip(base*(1 - gain*stress), min, base) | dynamic_profile_mode='consume' |
| 94 | `psch_entry_conf_chop_gain` | 0.025 | TUNABLE | entry confidence chop gain | dynamic_profile_mode='consume' |
| 95 | `psch_entry_conf_max` | 0.35 | TUNABLE | entry confidence upper clip | dynamic_profile_mode='consume' |
| 96 | `psch_entry_conf_min` | 0.02 | TUNABLE | entry confidence lower clip | dynamic_profile_mode='consume' |
| 97 | `psch_entry_conf_stress_gain` | 0.1 | TUNABLE | entry confidence = clip(base + gain*stress + chop_gain*chop, min, max) | dynamic_profile_mode='consume' |
| 98 | `psch_gross_exposure_min` | 0.2 | FIXED_SAFETY | gross exposure = clip(base*risk_derate, min, base) | dynamic_profile_mode='consume' |
| 99 | `psch_max_hold_max` | 120.0 | TUNABLE | max hold upper clip (bars) | dynamic_profile_mode='consume' |
| 100 | `psch_max_hold_min` | 20.0 | TUNABLE | max hold lower clip (bars) | dynamic_profile_mode='consume' |
| 101 | `psch_max_hold_stress_gain` | 20.0 | TUNABLE | max hold = round(clip(base - stress_gain*stress + trend_gain*trend, min, max)) | dynamic_profile_mode='consume' |
| 102 | `psch_max_hold_trend_gain` | 10.0 | TUNABLE | max hold trend gain | dynamic_profile_mode='consume' |
| 103 | `psch_min_hold_chop_gain` | 2.0 | TUNABLE | min hold = round(clip(base + chop_gain*chop + turb_gain*turbulent, min, max)) | dynamic_profile_mode='consume' |
| 104 | `psch_min_hold_max` | 8.0 | TUNABLE | min hold upper clip (bars); the registry bounds of min_hold_bars also apply | dynamic_profile_mode='consume' |
| 105 | `psch_min_hold_min` | 1.0 | TUNABLE | min hold lower clip (bars) | dynamic_profile_mode='consume' |
| 106 | `psch_min_hold_turbulent_gain` | 3.0 | TUNABLE | min hold turbulent gain | dynamic_profile_mode='consume' |
| 107 | `psch_min_rr_max` | 3.0 | TUNABLE | min reward:risk upper clip | dynamic_profile_mode='consume' |
| 108 | `psch_min_rr_min` | 1.0 | TUNABLE | min reward:risk lower clip | dynamic_profile_mode='consume' |
| 109 | `psch_min_rr_stress_gain` | 0.6 | TUNABLE | min reward:risk = clip(base + gain*stress, min, max) | dynamic_profile_mode='consume' |
| 110 | `psch_order_timeout_max_seconds` | 60.0 | FIXED_SAFETY | order timeout upper clip; at its default it lies above the contract base (inert), set below the base it is the only way the scheduled timeout tightens the gate | dynamic_profile_mode='consume' |
| 111 | `psch_slippage_max_fraction` | 0.002 | FIXED_SAFETY | slippage tolerance upper clip; at its default it lies above the contract base (inert), set below the base it tightens the gate | dynamic_profile_mode='consume' |
| 112 | `psch_slippage_min_fraction` | 0.0005 | FIXED_SAFETY | slippage tolerance lower clip | dynamic_profile_mode='consume' |
| 113 | `psch_slippage_vol_gain` | 0.75 | FIXED_SAFETY | slippage tolerance = clip(base*(1 + gain*(vol - 1)), min, max), applied tighten-only | dynamic_profile_mode='consume' |
| 114 | `psch_symbol_exposure_min` | 0.05 | FIXED_SAFETY | symbol exposure = clip(base*risk_derate, min, base) | dynamic_profile_mode='consume' |
| 115 | `sel_adverse_min_bars` | 2 | FIXED_SAFETY | adverse bars lower clip (live only with unit_protection_mode='pre_sync_and_in_flight') | unit_protection_mode='pre_sync_and_in_flight' |
| 116 | `sel_adverse_stress_gain` | 0.5 | FIXED_SAFETY | adverse bars stress tightening gain (live only with unit_protection_mode='pre_sync_and_in_flight') | unit_protection_mode='pre_sync_and_in_flight' |
| 117 | `sel_slippage_min_fraction` | 0.002 | FIXED_SAFETY | slippage limit lower clip (live only with unit_protection_mode='pre_sync_and_in_flight') | unit_protection_mode='pre_sync_and_in_flight' |
| 118 | `sel_slippage_stress_gain` | 0.3 | FIXED_SAFETY | slippage stress tightening gain (live only with unit_protection_mode='pre_sync_and_in_flight') | unit_protection_mode='pre_sync_and_in_flight' |
| 119 | `sel_spread_min_fraction` | 0.0025 | FIXED_SAFETY | spread limit lower clip | always (PAPER_APPLY native plant) |
| 120 | `sel_spread_stress_gain` | 0.4 | FIXED_SAFETY | spread limit stress tightening gain | always (PAPER_APPLY native plant) |
| 121 | `sel_stale_tick_min_seconds` | 45.0 | FIXED_SAFETY | stale limit lower clip | always (PAPER_APPLY native plant) |
| 122 | `sel_stale_tick_stress_gain` | 0.5 | FIXED_SAFETY | stale limit = clip(base * (1 - gain * stress), min, base) | always (PAPER_APPLY native plant) |
| 123 | `sel_vol_surge_min_mult` | 2.0 | FIXED_SAFETY | vol surge lower clip | always (PAPER_APPLY native plant) |
| 124 | `sel_vol_surge_stress_gain` | 0.3 | FIXED_SAFETY | vol surge stress tightening gain | always (PAPER_APPLY native plant) |
| 125 | `tier1_atr_floor_max` | 0.012 | FIXED_SAFETY | Tier-1 ATR floor multiplier upper clip | always (PAPER_APPLY native plant) |
| 126 | `tier1_atr_floor_min` | 0.0025 | FIXED_SAFETY | Tier-1 ATR floor multiplier lower clip | always (PAPER_APPLY native plant) |
| 127 | `tier1_normalized_vol_max` | 2.5 | FIXED_SAFETY | Tier-1 normalized volatility upper clip | always (PAPER_APPLY native plant) |
| 128 | `tier1_normalized_vol_min` | 0.5 | FIXED_SAFETY | Tier-1 normalized volatility lower clip | always (PAPER_APPLY native plant) |
| 129 | `tier1_slippage_scale_max` | 3.0 | FIXED_SAFETY | Tier-1 slippage multiplier upper clip | always (PAPER_APPLY native plant) |
| 130 | `tier1_slippage_scale_min` | 0.6 | FIXED_SAFETY | Tier-1 slippage multiplier lower clip | always (PAPER_APPLY native plant) |
| 131 | `tier3_kd_scale_max` | 2.0 | FIXED_SAFETY | Tier-3 Box 6 Kd schedule upper clip | always (PAPER_APPLY native plant) |
| 132 | `tier3_ki_divisor_max` | 4.0 | FIXED_SAFETY | Tier-3 Box 6 Ki divisor upper clip | always (PAPER_APPLY native plant) |
| 133 | `tier3_ki_divisor_min` | 1.0 | FIXED_SAFETY | Tier-3 Box 6 Ki divisor lower clip (>= 1: the schedule only bleeds Ki) | always (PAPER_APPLY native plant) |
| 134 | `tier3_kp_scale_max` | 2.5 | FIXED_SAFETY | Tier-3 Box 6 Kp schedule upper clip | always (PAPER_APPLY native plant) |

## C. Static: NOT dynamized, and why

| # | Parameter | Default | Class | What it is | Why not dynamized |
|---|---|---|---|---|---|
| 1 | `avr_synchronous_reactance_pu` | 0.05 | TUNABLE | terminal voltage sag per unit of bay loading: V_t = V_bus - X * I_bay | Physical constant of the voltage-sag model V_t = V_bus − X·I. No spec says X varies. |
| 2 | `avr_voltage_filter_alpha` | 1.0 | TUNABLE | first-order PT filter on V_t (1.0 = unfiltered) | Filter constant (1.0 = unfiltered). No adaptive-filter spec. |
| 3 | `cl_confidence_offset_gain` | 0.1 | TUNABLE | constant gain on the outcome ledger | Closed-loop gain on the outcome ledger. No schedule is specified. |
| 4 | `disp_downside_dev_default` | 0.5 | TUNABLE | downside deviation when fewer than downside_min_count losing outcomes | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 5 | `disp_downside_dev_floor` | 0.2 | TUNABLE | downside deviation floor in the merit ratio | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 6 | `disp_downside_min_count` | 2 | TUNABLE | losing outcomes needed to measure downside deviation | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 7 | `disp_ema_retention` | 0.85 | TUNABLE | EMA alpha: weight retained from the previous allocation | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 8 | `disp_ema_update` | 0.15 | TUNABLE | EMA beta: weight of the new merit target (alpha + beta = 1) | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 9 | `disp_merit_window` | 20 | TUNABLE | realized-R outcomes kept per bay | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 10 | `disp_min_samples` | 3 | TUNABLE | outcomes before a bay's merit departs from neutral | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 11 | `disp_neutral_score` | 1.0 | TUNABLE | merit score of a bay below min_samples | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 12 | `disp_score_base` | 1.0 | TUNABLE | merit = base + mean_R / max(downside_floor, downside_dev) | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 13 | `disp_score_floor` | 0.1 | TUNABLE | merit score lower clip | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 14 | `ecs_demand_restore_step_pu` | 0.1 | FIXED | rate limit of the per-step demand | Rate limit; constant by design. |
| 15 | `gov_bpstg_capital_weight` | 0.12 | TUNABLE | BPSTG_HEALTHCARE initial bay capital weight (dispatcher seed and initial AVR capital); the five must sum to 1 | Initial seed only. Live allocation is dynamic via the dispatcher. No spec for a dynamic seed. |
| 16 | `gov_bpstg_feedback_gain` | 0.25 | TUNABLE | BPSTG_HEALTHCARE dynamic_z feedback offset gain: offset = clip(-feedback_gain * u, min, max) | Gain on the governor PID output u, which is itself dynamic. No schedule for this gain is specified (unlike Kp/Ki/Kd). |
| 17 | `gov_cstg1_capital_weight` | 0.25 | TUNABLE | CSTG1_BFSI initial bay capital weight (dispatcher seed and initial AVR capital); the five must sum to 1 | Initial seed only. Live allocation is dynamic via the dispatcher. No spec for a dynamic seed. |
| 18 | `gov_cstg1_feedback_gain` | 0.25 | TUNABLE | CSTG1_BFSI dynamic_z feedback offset gain: offset = clip(-feedback_gain * u, min, max) | Gain on the governor PID output u, which is itself dynamic. No schedule for this gain is specified (unlike Kp/Ki/Kd). |
| 19 | `gov_cstg2_capital_weight` | 0.18 | TUNABLE | CSTG2_CONSUMER_AUTO initial bay capital weight (dispatcher seed and initial AVR capital); the five must sum to 1 | Initial seed only. Live allocation is dynamic via the dispatcher. No spec for a dynamic seed. |
| 20 | `gov_cstg2_feedback_gain` | 0.25 | TUNABLE | CSTG2_CONSUMER_AUTO dynamic_z feedback offset gain: offset = clip(-feedback_gain * u, min, max) | Gain on the governor PID output u, which is itself dynamic. No schedule for this gain is specified (unlike Kp/Ki/Kd). |
| 21 | `gov_gtg1_capital_weight` | 0.25 | TUNABLE | GTG1_HEAVY_INDUSTRY initial bay capital weight (dispatcher seed and initial AVR capital); the five must sum to 1 | Initial seed only. Live allocation is dynamic via the dispatcher. No spec for a dynamic seed. |
| 22 | `gov_gtg1_feedback_gain` | 0.25 | TUNABLE | GTG1_HEAVY_INDUSTRY dynamic_z feedback offset gain: offset = clip(-feedback_gain * u, min, max) | Gain on the governor PID output u, which is itself dynamic. No schedule for this gain is specified (unlike Kp/Ki/Kd). |
| 23 | `gov_gtg2_capital_weight` | 0.2 | TUNABLE | GTG2_TECH_TELECOM initial bay capital weight (dispatcher seed and initial AVR capital); the five must sum to 1 | Initial seed only. Live allocation is dynamic via the dispatcher. No spec for a dynamic seed. |
| 24 | `gov_gtg2_feedback_gain` | 0.25 | TUNABLE | GTG2_TECH_TELECOM dynamic_z feedback offset gain: offset = clip(-feedback_gain * u, min, max) | Gain on the governor PID output u, which is itself dynamic. No schedule for this gain is specified (unlike Kp/Ki/Kd). |
| 25 | `gov_path_error_sigma` | 2.0 | TUNABLE | tolerance = sigma*ATR/risk*sqrt(bars): the tolerance is dynamic, sigma is constant | Sigma in tolerance = sigma·ATR/risk·√bars. The tolerance is dynamic; sigma is the formula's constant. |
| 26 | `gov_v3_gamma_fast` | 0.85 | TUNABLE | inner integral leak when \|error\| > tau_error | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 27 | `gov_v3_kappa` | 0.25 | TUNABLE | trailing-gap modulation depth: gap = base_gap * (1 - kappa * tanh(u)) | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 28 | `gov_v3_mfe_activation_r` | 0.3 | TUNABLE | favourable excursion (R) at which the PID-modulated trailing stop becomes eligible | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 29 | `gov_v3_tau_error_multiplier` | 1.0 | TUNABLE | fast-leak error boundary in bars of noise (ATR / initial risk) | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 30 | `hrsg_correlation_window` | 30 | TUNABLE | synchronous return snapshots kept for correlation | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 31 | `hrsg_min_correlation_samples` | 6 | TUNABLE | snapshots before a pair correlation is measured | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 32 | `hrsg_pressure_time_constant_bars` | 5.0 | TUNABLE | drum pressure first-order lag (bars) | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 33 | `hrsg_recovery_fraction` | 0.6 | TUNABLE | fraction of curtailed GTG capital routed to healthy steam bays | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 34 | `hrsg_rho_crit` | 0.65 | TUNABLE | pairwise bay return correlation at which economizer damping begins | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 35 | `mv_fsr_entry_threshold` | 0.6 | TUNABLE | constant threshold on the per-bar limiter minimum | Mark V FSR threshold on a per-bar limiter. The limiter is dynamic; no threshold schedule is specified. |
| 36 | `mv_fsr_exit_threshold` | 0.25 | TUNABLE | constant threshold | Mark V FSR threshold on a per-bar limiter. The limiter is dynamic; no threshold schedule is specified. |
| 37 | `studies_pid_kd` | 0.05 | TUNABLE | constant gain | Studies-weight PID gain. Out of the programme brief ("extend, not redo"). No gain schedule is specified. |
| 38 | `studies_pid_ki` | 0.05 | TUNABLE | constant gain | Studies-weight PID gain. Out of the programme brief ("extend, not redo"). No gain schedule is specified. |
| 39 | `studies_pid_kp` | 0.15 | TUNABLE | constant gain (no schedule) | Studies-weight PID gain. Out of the programme brief ("extend, not redo"). No gain schedule is specified. |
| 40 | `studies_pid_output_clamp` | 0.15 | TUNABLE | constant clamp | Studies-weight PID gain. Out of the programme brief ("extend, not redo"). No gain schedule is specified. |
| 41 | `avr_voltage_reference_pu` | 1.0 | FIXED_SAFETY | AVR voltage reference (per unit of the synchronized bus design point) | Hard safety limit or envelope. It is deliberately constant; making it move needs an approved spec (it would loosen or tighten protection). |
| 42 | `disp_allocation_ceiling` | 0.35 | FIXED_SAFETY | maximum bay allocation fraction; also the sector-dispatch per-bay cap | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 43 | `disp_allocation_floor` | 0.08 | FIXED_SAFETY | minimum bay allocation fraction | Dispatcher merit/EMA constant. The allocation it drives is already dynamic (merit and EMA update on every outcome). No spec defines a per-bar schedule for the constant itself. |
| 44 | `gov_v3_base_gap_r` | 0.45 | FIXED_SAFETY | unmodulated trailing gap behind the favourable extreme (R) | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 45 | `gov_v3_gamma_slow` | 0.98 | FIXED_SAFETY | inner integral leak when \|error\| <= tau_error | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 46 | `gov_v3_grace_bars` | 2 | FIXED_SAFETY | bars after the fill before the trailing stop may engage | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 47 | `gov_v3_minimum_gap_r` | 0.15 | FIXED_SAFETY | smallest commanded trailing gap (R) | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 48 | `gov_v3_noise_floor_mult` | 1.0 | FIXED_SAFETY | trailing gap noise floor in bars of noise | Protocol V3 inner-loop constant; V3 defines it as a constant. The trailing gap it shapes is dynamic per bar. |
| 49 | `grid_vix_derate_start` | 24.0 | FIXED | constant threshold | Hard safety limit or envelope. It is deliberately constant; making it move needs an approved spec (it would loosen or tighten protection). |
| 50 | `hrsg_max_steam_pressure_pu` | 1.5 | FIXED_SAFETY | drum pressure ceiling (pu) | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 51 | `hrsg_min_penalty_factor` | 0.7 | FIXED_SAFETY | deepest correlation derate of a bay allocation | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 52 | `hrsg_min_steam_pressure_pu` | 0.35 | FIXED_SAFETY | pressure-collapse threshold (pu); steam bays have zero capacity below it and exit | HRSG model constant (window, lag, threshold, ratio). The pressure and correlation it drives are dynamic. No spec for scheduling it. |
| 53 | `micom_overfrequency_ratio` | 1.5 | FIXED_SAFETY | ANSI 81O melt-up trip = ratio * crash limit | Hard safety limit or envelope. It is deliberately constant; making it move needs an approved spec (it would loosen or tighten protection). |
| 54 | `plant_bay_lockout_consecutive_stops` | 2 | FIXED_SAFETY | consecutive stopped/losing outcomes that latch the ANSI 86 bay lockout for the session | Hard safety limit or envelope. It is deliberately constant; making it move needs an approved spec (it would loosen or tighten protection). |
| 55 | `symbol_loss_cooldown_bars` | 15 | FIXED_SAFETY | orchestrator per-symbol entry block after a net losing exit (former BAY_LOSS_COOLDOWN_BARS) | Orchestrator latch count / bar count. A hard safety latch; no schedule is specified. |
| 56 | `symbol_trip_consecutive_losses` | 2 | FIXED_SAFETY | orchestrator: consecutive net losing exits that trip a symbol for the session (former literal 2) | Orchestrator latch count / bar count. A hard safety latch; no schedule is specified. |

## D. Registered but no actuator

| # | Parameter | Default | Class | Why it does nothing |
|---|---|---|---|---|
| 1 | `avr_mvar_limit_base_pu` | 0.9 | INACTIVE | No runtime path supplies measured MVAR (always 0), so the limiter can never engage. It needs an MVAR measurement source (missing spec). |
| 2 | `avr_mvar_limit_min_pu` | 0.6 | INACTIVE | No runtime path supplies measured MVAR (always 0), so the limiter can never engage. It needs an MVAR measurement source (missing spec). |
| 3 | `avr_mvar_limit_stress_gain` | 0.3 | INACTIVE | No runtime path supplies measured MVAR (always 0), so the limiter can never engage. It needs an MVAR measurement source (missing spec). |
| 4 | `gov_bpstg_atr_barrier` | 1.4 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 5 | `gov_bpstg_target_m` | 2.5 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 6 | `gov_bpstg_z_exit` | 0.4 | INACTIVE | Dead field: nothing reads BayGovernorSpec.z_exit. It needs a consumer spec (exit rule). |
| 7 | `gov_cstg1_atr_barrier` | 1.5 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 8 | `gov_cstg1_target_m` | 2.8 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 9 | `gov_cstg1_z_exit` | 0.3 | INACTIVE | Dead field: nothing reads BayGovernorSpec.z_exit. It needs a consumer spec (exit rule). |
| 10 | `gov_cstg2_atr_barrier` | 1.65 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 11 | `gov_cstg2_target_m` | 2.9 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 12 | `gov_cstg2_z_exit` | 0.4 | INACTIVE | Dead field: nothing reads BayGovernorSpec.z_exit. It needs a consumer spec (exit rule). |
| 13 | `gov_gtg1_atr_barrier` | 1.8 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 14 | `gov_gtg1_target_m` | 3.0 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 15 | `gov_gtg1_z_exit` | 0.35 | INACTIVE | Dead field: nothing reads BayGovernorSpec.z_exit. It needs a consumer spec (exit rule). |
| 16 | `gov_gtg2_atr_barrier` | 2.1 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 17 | `gov_gtg2_target_m` | 3.2 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 18 | `gov_gtg2_z_exit` | 0.5 | INACTIVE | Dead field: nothing reads BayGovernorSpec.z_exit. It needs a consumer spec (exit rule). |
| 19 | `gsch_droop_max_min_scale` | 0.8 | INACTIVE | Structurally unreachable clip: the formula can never hit this bound (the proof is in the row). |
| 20 | `gsch_integral_max_scale` | 1.25 | INACTIVE | Structurally unreachable clip: the formula can never hit this bound (the proof is in the row). |
| 21 | `gsch_kd_scale_min` | 0.6 | INACTIVE | Structurally unreachable clip: the formula can never hit this bound (the proof is in the row). |
| 22 | `gsch_kp_scale_min` | 0.6 | INACTIVE | Structurally unreachable clip: the formula can never hit this bound (the proof is in the row). |
| 23 | `gsch_offset_min_upper_scale` | 0.75 | INACTIVE | Structurally unreachable clip: the formula can never hit this bound (the proof is in the row). |
| 24 | `plant_stop_floor_price_fraction` | 0.0065 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 25 | `plant_target_min_reward_risk` | 1.5 | ADVISORY | ADVISORY: feeds the plant's suggested stop/target, but the engine uses the MPC plan's. Dynamizing it would change nothing. |
| 26 | `psch_dispatch_floor_ceiling_ratio` | 0.5 | INACTIVE | No consumer contract: no spec says how a per-bar dispatcher floor should be applied (raising a merit floor is neither tightening nor specified). |
| 27 | `psch_dispatch_floor_max` | 0.1 | INACTIVE | No consumer contract: no spec says how a per-bar dispatcher floor should be applied (raising a merit floor is neither tightening nor specified). |
| 28 | `psch_dispatch_floor_min` | 0.05 | INACTIVE | No consumer contract: no spec says how a per-bar dispatcher floor should be applied (raising a merit floor is neither tightening nor specified). |
| 29 | `psch_dispatch_floor_stress_gain` | 0.2 | INACTIVE | No consumer contract: no spec says how a per-bar dispatcher floor should be applied (raising a merit floor is neither tightening nor specified). |
| 30 | `psch_order_timeout_min_seconds` | 15.0 | INACTIVE | The schedule base·(1+g·stress) is always ≥ the contract timeout, so a tighten-only consumer never moves. Only the FIXED_SAFETY max clip can tighten it. |
| 31 | `psch_order_timeout_stress_gain` | 0.5 | INACTIVE | The schedule base·(1+g·stress) is always ≥ the contract timeout, so a tighten-only consumer never moves. Only the FIXED_SAFETY max clip can tighten it. |
| 32 | `tier3_kd_scale_min` | 0.5 | INACTIVE | Structurally unreachable clip: the formula can never hit this bound (the proof is in the row). |
| 33 | `tier3_kp_scale_min` | 0.5 | INACTIVE | Structurally unreachable clip: the formula can never hit this bound (the proof is in the row). |
