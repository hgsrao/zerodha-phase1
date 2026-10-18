# R5 controller inventory (at cb10fac)

Engines B and C subclass the Engine A orchestrator and override only execution seams, so every controller below is the same code and the same instances in all three engines.

| ID | Controller | Location | Scope | Status | Actuator |
|---|---|---|---|---|---|
| C01 | Box 6 entry PID | `revision2_external/pid_controller.py:202` | symbol | ACTIVE | execution_market_price = next_open * (1 + adj * mpc_entry_price_gain) -> paper fill price, stop and target levels (Engine A); B/C submit market orders, the plan levels follow the adjusted reference |
| C02 | Box 6 exit PID | `revision2_external/pid_controller.py:207` | symbol | ACTIVE | plan stop and target distances *= exit_tightness (symmetric in the sign of the error because of abs()) |
| C03 | Continuous PA-confidence exit PID (Track 1) | `revision2_external/continuous_exit_controller.py:378` | symbol (position) | ACTIVE (advisory under full governor authority) | ratcheted trailing stop = favorable_extreme -/+ ATR * trailing_stop_atr_mult * min(pa, studies, time tightness); saturation_exit after saturation_exit_bars at +clamp. Actuating when governor_authority='advisory' with closed_loop_mode='active_paper'; ADVISORY under governor_authority='full' (orchestrator uses governor_stop_price) |
| C04 | Continuous chart-studies exit PID (Track 2) | `revision2_external/continuous_exit_controller.py:390` | symbol (position) | ACTIVE (advisory under full governor authority) | same trailing-stop min() law as C03; saturation_exit_studies; feeds FinalExecutionController.exit_decision (C15) which actuates only with final_exit_authority=True |
| C05 | Studies-weight PIDs (four per symbol) | `revision2_external/composite_study_signal.py:318` | study x symbol | ACTIVE | composite studies confidence and direction -> FinalExecutionController entry admission (direction must agree), FSRN conviction (governor entry/exit), exit Track 2 |
| C06 | Tier-3 MPC gain schedule | `revision2_external/dynamic_parameter_controller.py:117` | symbol (per call) | ACTIVE | entry PID gains (C01) |
| C07 | Five bay governor outer loops (realized-R PID) | `revision5/governor.py:268` | bay | ACTIVE | dynamic_z -> entry comparator (C08) -> governor ENTRY/NO_ACTION -> order admission (full authority) and plant admission (PAPER_APPLY) |
| C08 | Governor entry comparator / droop | `revision5/governor.py:908` | bay | ACTIVE | governor entry authority (full); plant admission (PAPER_APPLY) uses nifty_15m_ret while the authority uses snapshot.grid.nifty_deviation (two different droop inputs) |
| C09 | Governor inner position loop (legacy V2) | `revision5/governor.py:648` | position | ACTIVE (full authority) | EXIT (target, hard stop, ratchet floor, max hold, path error) armed for the next open; HOLD moves governor_stop_price to the ratchet floor (full authority) |
| C10 | Mark V limiters (FSRN/FSRT/FSRA/FSRS/FSRM, minimum value gate) | `revision5/governor_authority.py:213` | symbol/bay | ACTIVE | entry admission + plant quantity * FSR; EXIT below exit threshold |
| C11 | Bay AVR (excitation PID + lot sizing) | `revision5/ccpp_protection_cubicles.py:510` | bay | ACTIVE (lot size); PID INEFFECTIVE (zero error); excitation only ratchets down via excitation_max | calculate_lot_size: notional = capital * loading * excitation * vol_scalar * z_strength, UEL/OEL clamp -> plant quantity in _plant_entry_gate -> order quantity (LIVE) |
| C12 | Dynamic bay load dispatcher (merit EMA) | `revision5/ccpp_unified_plant.py:91` | plant | ACTIVE (misclassified PL-03 simulation-only) | get_allocation -> HRSG balance -> bay effective_allocation -> AVR capital and _plant_entry_gate headroom (order quantity); weights + max_ceiling -> SectorDispatchController references -> PAPER_PLANT admission cap |
| C13 | ECS supervisor and sector dispatch | `revision5/plant_control.py:223` | plant | ACTIVE (PAPER_APPLY) | PAPER_APPLY: cap_dispatch_entry quantity cap (order quantity) |
| C14 | HRSG thermal lag and capital coupling | `revision5/hrsg.py:541` | plant | ACTIVE (misclassified HR-01 simulation-only); correlation damper INERT (no snapshots recorded in replay) | steam bay effective allocation (order quantity headroom); HRSG_PRESSURE_COLLAPSE plant trip exit; GTG recovery 0.60 of curtailed capital |
| C15 | Final execution controller | `revision2_external/final_execution_controller.py:24` | symbol | ACTIVE (entry); ADVISORY (exit) | entry: admission; exit: telemetry only unless final_exit_authority=True (uncommitted at cb10fac) |
| C16 | Closed-loop supervisor: entry quality, path loop, portfolio derate | `revision2_external/closed_loop_control.py:366` | symbol / portfolio | ACTIVE | active_paper: entry size derate, confidence offset, path stop tightening/exit (advisory under full governor authority) |
| C17 | HMM risk hysteresis | `revision2_external/closed_loop_control.py:61` | symbol | SHADOW | none (HMM_REGIME_RISK_SHADOW telemetry) |
| C18 | Native R5 dynamic parameter controller (governor/AVR/protection schedule) | `revision5/dynamic_parameters.py:360` | plant (applied per bay) | ACTIVE (misclassified DP-01 simulation-only) | governor runtime values (C07-C09), AVR profile (C11), SEL-300G and MiCOM limits, bay cooldowns; Kp/Ki/Kd ratio scales onto Box 6 PIDs (C01-C04) |
| C19 | Bay cooldown / ANSI 86 lockout | `revision5/ccpp_unified_plant.py:1181` | bay | ACTIVE | plant admission BAY_COOLDOWN / ANSI_86_LOCKOUT; HRSG unavailability |
| C20 | SEL-300G unit protection and MiCOM grid protection | `revision5/ccpp_protection_cubicles.py:755` | bay / plant | ACTIVE (protection) | entry rejection, unit breaker trip, forced protective exit, grid intertie open |
| C21 | Symbol loss cooldown latch (orchestrator) | `revision2_external/orchestrator.py:94` | symbol | ACTIVE | entry admission |
| C22 | R5 bay gain scale onto Box 6 PIDs | `revision2_external/orchestrator.py:721` | bay -> symbol | ACTIVE | C01-C04 tunings (PAPER_APPLY) |
| C23 | Mark V executive controller (ccpp_unified_plant) | `revision5/ccpp_unified_plant.py:2719` | n/a | DEAD (superseded by revision5/governor_authority.py) | NONE: used only by tests |
| C24 | Tier-2 supervisory bridge regime schedule | `revision5/supervisory_bridge.py:493` | n/a | DEAD | NONE: called only from tests |
| C25 | Governor position policy (offline experiment) | `revision5/governor_position_policy.py:7` | position | OFFLINE EXPERIMENT (inactive by default; superseded by Protocol V3) | trailing stop when injected by offline scripts only |
| C26 | Machine dynamics / startup synchronization / relay coordination | `revision5/machine_dynamics.py:41` | bay | SIMULATION ONLY | NONE in replay (startup_sequencer not configured, run_machine_scan/run_unit_synchronization never called) |

## C01 Box 6 entry PID

- Source: `revision2_external/pid_controller.py:202` (SimplePIDModelPredictiveControlBox.build_plan (entry_pid))
- Instances: one simple_pid.PID per symbol
- Scope: symbol
- Input: ID decision confidence of the approved candidate
- Setpoint: rolling mean of the symbol's prior confidences (pid_integral_window_bars)
- Error: setpoint - confidence
- P: Kp_e * e
- I: Ki_e * sum(e) (simple-pid, unbounded sum, output-limited)
- D: Kd_e * (-d confidence) (derivative on measurement)
- Clamp: output_limits = +/- pid_integral_max_clamp
- Output limits: [-pid_integral_max_clamp, +pid_integral_max_clamp]
- Reset: never reset; persists per symbol for the run
- Update frequency: once per approved candidate (not per bar)
- State persistence: per symbol, whole run
- Runtime actuator: execution_market_price = next_open * (1 + adj * mpc_entry_price_gain) -> paper fill price, stop and target levels (Engine A); B/C submit market orders, the plan levels follow the adjusted reference
- Registry parameters: pid_kp_entry, pid_ki_entry, pid_kd_entry, pid_integral_max_clamp, pid_integral_window_bars, mpc_entry_price_gain, mpc_schedule_kp_gain, mpc_schedule_ki_gain, mpc_schedule_kd_gain
- Hardcoded values: _ENTRY_TIMING_FLOOR = 0.3 (feeds entry_timing_multiplier, which is telemetry only); pid_derivative_smoothing is registered but has no consumer (simple-pid derivative is single-step)
- Tests: tests_external/test_pid_controller*, tests/test_revision5_bb05_bb06_parameterization.py
- Status: **ACTIVE**
- Note: Gains are Tier-3 scheduled (C06) and multiplied by the R5 bay gain scale in PAPER_APPLY.

## C02 Box 6 exit PID

- Source: `revision2_external/pid_controller.py:207` (SimplePIDModelPredictiveControlBox.build_plan (exit_pid))
- Instances: one simple_pid.PID per symbol
- Scope: symbol
- Input: ID decision confidence
- Setpoint: same rolling confidence baseline as C01
- Error: setpoint - confidence
- P: Kp_x * e
- I: Ki_x * sum(e)
- D: Kd_x * (-d confidence)
- Clamp: +/- pid_integral_max_clamp
- Output limits: exit_tightness = clip(1 - |adj|, 0.5, 1)
- Reset: never
- Update frequency: once per approved candidate
- State persistence: per symbol, whole run
- Runtime actuator: plan stop and target distances *= exit_tightness (symmetric in the sign of the error because of abs())
- Registry parameters: pid_kp_exit, pid_ki_exit, pid_kd_exit, pid_integral_max_clamp
- Hardcoded values: _EXIT_TIGHTNESS_FLOOR = 0.5 (FIXED_SAFETY_ENVELOPE, annotated in code); abs(): the response is symmetric (revision2_external/pid_controller.py, exit_tightness line)
- Tests: tests_external/test_pid_controller*
- Status: **ACTIVE**

## C03 Continuous PA-confidence exit PID (Track 1)

- Source: `revision2_external/continuous_exit_controller.py:378` (ContinuousExitController.update (pid))
- Instances: one PID per symbol
- Scope: symbol (position)
- Input: PA exit confidence each held bar
- Setpoint: rolling mean of the symbol's own PA exit confidence
- Error: baseline - confidence
- P: pid_kp_exit * e
- I: pid_ki_exit * sum(e)
- D: pid_kd_exit * (-d confidence)
- Clamp: +/- pid_integral_max_clamp
- Output limits: tightness = clip(1 - |adj|, 0.5, 1)
- Reset: PID persists per symbol across positions; position state (stop, saturation streak) is per position
- Update frequency: every held bar
- State persistence: per symbol
- Runtime actuator: ratcheted trailing stop = favorable_extreme -/+ ATR * trailing_stop_atr_mult * min(pa, studies, time tightness); saturation_exit after saturation_exit_bars at +clamp. Actuating when governor_authority='advisory' with closed_loop_mode='active_paper'; ADVISORY under governor_authority='full' (orchestrator uses governor_stop_price)
- Registry parameters: pid_kp_exit, pid_ki_exit, pid_kd_exit, pid_integral_max_clamp, pid_integral_window_bars, trailing_stop_atr_mult, saturation_exit_bars, mpc_time_decay_gain
- Hardcoded values: _EXIT_TIGHTNESS_FLOOR = 0.5 (FIXED_SAFETY_ENVELOPE); initial setpoint 0.5 (overwritten on the first call)
- Tests: tests_external/test_continuous_exit_controller*.py
- Status: **ACTIVE (advisory under full governor authority)**
- Note: Shares the pid_k*_exit gains with C02 and C04.

## C04 Continuous chart-studies exit PID (Track 2)

- Source: `revision2_external/continuous_exit_controller.py:390` (ContinuousExitController.update (studies_pid))
- Instances: one PID per symbol
- Scope: symbol (position)
- Input: composite chart-studies confidence
- Setpoint: rolling mean of the symbol's own studies confidence
- Error: baseline - confidence
- P: pid_kp_exit * e
- I: pid_ki_exit * sum(e)
- D: pid_kd_exit * (-d confidence)
- Clamp: +/- pid_integral_max_clamp
- Output limits: tightness = clip(1 - |adj|, 0.5, 1)
- Reset: as C03
- Update frequency: every held bar
- State persistence: per symbol
- Runtime actuator: same trailing-stop min() law as C03; saturation_exit_studies; feeds FinalExecutionController.exit_decision (C15) which actuates only with final_exit_authority=True
- Registry parameters: pid_kp_exit, pid_ki_exit, pid_kd_exit, pid_integral_max_clamp, pid_integral_window_bars, saturation_exit_bars
- Hardcoded values: _EXIT_TIGHTNESS_FLOOR = 0.5
- Tests: tests_external/test_continuous_exit_controller*.py
- Status: **ACTIVE (advisory under full governor authority)**

## C05 Studies-weight PIDs (four per symbol)

- Source: `revision2_external/composite_study_signal.py:318` (CompositeStudySignal.evaluate)
- Instances: 4 PIDs (ichimoku, bollinger, stochastic, session_vwap) per symbol
- Scope: study x symbol
- Input: study hit rate over studies_hit_rate_window_bars graded votes
- Setpoint: cross-study mean hit rate
- Error: mean - hit_rate
- P: studies_pid_kp * e
- I: studies_pid_ki * sum(e)
- D: studies_pid_kd * (-d hit_rate)
- Clamp: +/- studies_pid_output_clamp
- Output limits: weight = clip(0.25 - u, MIN_WEIGHT 0.05, MAX_WEIGHT 0.60)
- Reset: never (configure() keeps PID memory)
- Update frequency: every evaluated bar
- State persistence: per symbol
- Runtime actuator: composite studies confidence and direction -> FinalExecutionController entry admission (direction must agree), FSRN conviction (governor entry/exit), exit Track 2
- Registry parameters: studies_pid_kp, studies_pid_ki, studies_pid_kd, studies_pid_output_clamp, studies_grading_horizon_bars, studies_hit_rate_window_bars
- Hardcoded values: MIN_WEIGHT 0.05 / MAX_WEIGHT 0.60 (FIXED_SAFETY_ENVELOPE); indicator periods (Ichimoku 9/26/52, Bollinger 20, stochastic 5/3/3) are study definitions
- Tests: tests/test_*studies*, tests_external/test_composite_study_signal*.py
- Status: **ACTIVE**

## C06 Tier-3 MPC gain schedule

- Source: `revision2_external/dynamic_parameter_controller.py:117` (DynamicParameterController.get_tier3_pid_schedule)
- Instances: stateless
- Scope: symbol (per call)
- Input: |confidence - baseline|
- Setpoint: n/a (feed-forward schedule)
- Error: n/a
- P: Kp * clip(1 + mpc_schedule_kp_gain * |e|, 0.5, 2.5)
- I: Ki / clip(1 + mpc_schedule_ki_gain * |e|, 1, 4)
- D: Kd * clip(1 + mpc_schedule_kd_gain * |e|, 0.5, 2)
- Clamp: _KP/_KI/_KD_SCALE_ENVELOPE
- Output limits: as clamp
- Reset: n/a
- Update frequency: per entry plan
- State persistence: none
- Runtime actuator: entry PID gains (C01)
- Registry parameters: mpc_schedule_kp_gain, mpc_schedule_ki_gain, mpc_schedule_kd_gain
- Hardcoded values: _KP_SCALE_ENVELOPE (0.5, 2.5), _KI_SCALE_ENVELOPE (1, 4), _KD_SCALE_ENVELOPE (0.5, 2) (FIXED, annotated)
- Tests: tests/test_revision5_bb05_bb06_parameterization.py
- Status: **ACTIVE**

## C07 Five bay governor outer loops (realized-R PID)

- Source: `revision5/governor.py:268` (BayTurbineClosedLoopGovernor.register_trade)
- Instances: 5 (one per bay); PAPER_APPLY uses the plant's bay governors
- Scope: bay
- Input: realized R of each authoritative close
- Setpoint: runtime_target_r (scheduled from spec.target_r)
- Error: target_r - mean(last outcome_window R)
- P: Kp * e
- I: Ki * clip(sum e, +/- integral_clamp)
- D: Kd * (e - e_prev) (per trade)
- Clamp: +/- runtime_integral_clamp (re-clamped on every profile refresh)
- Output limits: u unbounded; consumed through dynamic_z feedback_offset = clip(-0.25 * u, offset_min, offset_max)
- Reset: never (R history survives sessions); window trimmed on refresh
- Update frequency: per realized close
- State persistence: whole run
- Runtime actuator: dynamic_z -> entry comparator (C08) -> governor ENTRY/NO_ACTION -> order admission (full authority) and plant admission (PAPER_APPLY)
- Registry parameters: none
- Hardcoded values: BAY_GOVERNOR_SPECS literals revision5/governor.py:59 (droop_r, kp, ki, kd, base_z, z_exit, atr_barrier, target_m, capital_weight per bay); BayGovernorSpec defaults target_r 0.30, outcome_window 6, integral_clamp 3.0, offsets -0.45/0.15, grid droop 0.05/0.25; feedback gain -0.25 (dynamic_z)
- Tests: tests/test_revision5_governor.py, tests/test_r5_governor_*.py
- Status: **ACTIVE**

## C08 Governor entry comparator / droop

- Source: `revision5/governor.py:908` (BayTurbineClosedLoopGovernor.dynamic_z / evaluate_entry_request)
- Instances: 5
- Scope: bay
- Input: causal close z-score (gov_z_window_bars), grid droop input
- Setpoint: dynamic_z = base_z + feedback_offset - droop_penalty
- Error: signed z - limit
- P: n/a (comparator)
- I: n/a
- D: n/a
- Clamp: droop penalty <= grid_droop_max
- Output limits: ENTRY / NO_ACTION
- Reset: n/a
- Update frequency: per candidate
- State persistence: none
- Runtime actuator: governor entry authority (full); plant admission (PAPER_APPLY) uses nifty_15m_ret while the authority uses snapshot.grid.nifty_deviation (two different droop inputs)
- Registry parameters: gov_z_window_bars
- Hardcoded values: base_z, droop_r, grid_droop_gain/max per bay (C07 table); trend_overspeed mirror 2.0 * base_z (structural symmetry)
- Tests: tests/test_revision5_governor.py
- Status: **ACTIVE**

## C09 Governor inner position loop (legacy V2)

- Source: `revision5/governor.py:648` (BayTurbineClosedLoopGovernor.evaluate_position_control)
- Instances: one inner state per open position per bay (orchestrator key trade id; plant key ('PLANT', id))
- Scope: position
- Input: measured R at the completed close
- Setpoint: reference path expected R (TradeReferencePath)
- Error: reference_r - measured_r
- P: runtime_kp * e
- I: runtime_ki * clip(sum e, +/- integral_clamp)
- D: runtime_kd * (e - e_prev)
- Clamp: +/- runtime_integral_clamp
- Output limits: u used ONLY by GOVERNOR_PATH_ERROR (u >= max(|target_r|, |Ki*clamp|) and lag beyond gov_path_error_sigma noise envelopes)
- Reset: begin_position on fill; dropped on close
- Update frequency: every held bar
- State persistence: per position
- Runtime actuator: EXIT (target, hard stop, ratchet floor, max hold, path error) armed for the next open; HOLD moves governor_stop_price to the ratchet floor (full authority)
- Registry parameters: gov_path_error_sigma
- Hardcoded values: ratchet step |target_r| / outcome_window and trailing gap max(|offset_min|, |target_r|) are derived from scheduled values (no literal)
- Tests: tests/test_r5_governor_authority.py
- Status: **ACTIVE (full authority)**

## C10 Mark V limiters (FSRN/FSRT/FSRA/FSRS/FSRM, minimum value gate)

- Source: `revision5/governor_authority.py:213` (limiters / minimum_value_gate / entry_decision / position_decision)
- Instances: stateless (per decision)
- Scope: symbol/bay
- Input: conviction rank, MTM drawdown, velocity, session bar
- Setpoint: mv_fsr_entry_threshold / mv_fsr_exit_threshold
- Error: FSR_selected vs thresholds
- P: n/a (limiter)
- I: n/a
- D: n/a
- Clamp: each limiter clipped to [0, 1]; FSR_effective >= mv_fsr_min_floor
- Output limits: size_multiplier = FSR_selected (derate only)
- Reset: n/a
- Update frequency: per candidate / per held bar
- State persistence: none
- Runtime actuator: entry admission + plant quantity * FSR; EXIT below exit threshold
- Registry parameters: mv_*, gov_telemetry_atr_bars, gov_z_window_bars
- Hardcoded values: none
- Tests: tests/test_r5_ccpp_mark_v.py, tests/test_r5_governor_authority.py
- Status: **ACTIVE**

## C11 Bay AVR (excitation PID + lot sizing)

- Source: `revision5/ccpp_protection_cubicles.py:510` (BayExcitationAVR.evaluate_control / calculate_lot_size)
- Instances: 5 (one per bay)
- Scope: bay
- Input: terminal_voltage_pu (BUS_VOLTAGE mode)
- Setpoint: bus_voltage_reference_pu
- Error: V_ref - V_t (always 0: the orchestrator never passes a voltage, both default 1.0)
- P: runtime_kp * e
- I: runtime_ki * clip(sum e, +/- clamp)
- D: runtime_kd * (e - e_prev)
- Clamp: +/- runtime_integral_clamp
- Output limits: excitation in [excitation_min, excitation_max]; ANSI 27/59 trips at 0.90/1.10 pu (cannot fire with V_t = 1)
- Reset: never
- Update frequency: once per evaluate_admission AND per evaluate_position_control call (per call, not per bar)
- State persistence: whole run
- Runtime actuator: calculate_lot_size: notional = capital * loading * excitation * vol_scalar * z_strength, UEL/OEL clamp -> plant quantity in _plant_entry_gate -> order quantity (LIVE)
- Registry parameters: none
- Hardcoded values: BayExcitationAVR.__init__ runtime literals (loading 0.15, kp 0.8, ki 0.15, kd 0.10, clamp 0.5, 0.90/1.10 trips, mvar 0.9, excitation 0.10/1.00); AVRRuntimeProfile schedule (dynamic_parameters.py:79-181, 775-824), control_stress = 1 - loading/0.15; TurbineBayPanel UEL 15,000 rupees, OEL 0.40 of bay capital
- Tests: tests/test_revision5_ccpp_plant.py
- Status: **ACTIVE (lot size); PID INEFFECTIVE (zero error); excitation only ratchets down via excitation_max**

## C12 Dynamic bay load dispatcher (merit EMA)

- Source: `revision5/ccpp_unified_plant.py:91` (DynamicBayLoadDispatcher.register_trade)
- Instances: 1 per plant (PAPER_APPLY: the plant's dispatcher is also the sector-dispatch merit source)
- Scope: plant
- Input: realized R per bay (last 20)
- Setpoint: merit-proportional allocation
- Error: n/a (merit allocation)
- P: score = max(0.1, 1 + mean_R / max(0.2, downside_dev))
- I: EMA w = 0.85 w + 0.15 target
- D: n/a
- Clamp: target clipped to [0.08, 0.35] BEFORE normalisation and EMA
- Output limits: final weight NOT bounded by the ceiling (clamp -> normalise -> EMA -> normalise)
- Reset: never
- Update frequency: per realized close
- State persistence: whole run
- Runtime actuator: get_allocation -> HRSG balance -> bay effective_allocation -> AVR capital and _plant_entry_gate headroom (order quantity); weights + max_ceiling -> SectorDispatchController references -> PAPER_PLANT admission cap
- Registry parameters: none
- Hardcoded values: EMA 0.85/0.15, floor 0.08, ceiling 0.35, window 20, min 3 samples, downside default 0.5, floor 0.2, score floor 0.1
- Tests: tests/test_revision5_ccpp_plant.py, tests/test_revision5_plant_control.py
- Status: **ACTIVE (misclassified PL-03 simulation-only)**

## C13 ECS supervisor and sector dispatch

- Source: `revision5/plant_control.py:223` (ECSPlantSupervisor.evaluate / SectorDispatchController.dispatch)
- Instances: 1 chain per orchestrator
- Scope: plant
- Input: grid state, bay availability
- Setpoint: plant demand reference (1.0 / ecs_derate_demand_pu / 0)
- Error: target - previous demand
- P: n/a
- I: rate limit: restore by <= ecs_demand_restore_step_pu, reductions immediate
- D: n/a
- Clamp: demand in [0, 1]; per-bay reference <= dispatcher max_ceiling (water-filling)
- Output limits: references sum <= demand
- Reset: starts at HOLD (0)
- Update frequency: per portfolio timestamp
- State persistence: whole run
- Runtime actuator: PAPER_APPLY: cap_dispatch_entry quantity cap (order quantity)
- Registry parameters: grid_*, ecs_derate_demand_pu, ecs_demand_restore_step_pu
- Hardcoded values: per-bay cap reads DynamicBayLoadDispatcher.max_ceiling (C12 literal)
- Tests: tests/test_revision5_plant_control.py
- Status: **ACTIVE (PAPER_APPLY)**

## C14 HRSG thermal lag and capital coupling

- Source: `revision5/hrsg.py:541` (HeatRecoverySteamGenerator.step_bar / balance_capital)
- Instances: 1 per plant
- Scope: plant
- Input: GTG mass flow x exhaust temperature (median of gas-bay members)
- Setpoint: heat input (steady pressure)
- Error: steady - pressure
- P: n/a
- I: first order lag: p += (steady - p) / tau
- D: n/a
- Clamp: steady in [0, 1.5]
- Output limits: steam capacity factor = clip((p - 0.35) / 0.65, 0, 1); collapse below 0.35
- Reset: drum starts at 1.0 pu; never reset
- Update frequency: every bar (PAPER_APPLY)
- State persistence: whole run
- Runtime actuator: steam bay effective allocation (order quantity headroom); HRSG_PRESSURE_COLLAPSE plant trip exit; GTG recovery 0.60 of curtailed capital
- Registry parameters: none
- Hardcoded values: tau 5 bars, collapse 0.35 pu, max 1.50 pu, recovery 0.60, rho_crit 0.65, min penalty 0.70, correlation window 30/6 (constructor defaults)
- Tests: tests/test_revision5_hrsg*.py
- Status: **ACTIVE (misclassified HR-01 simulation-only); correlation damper INERT (no snapshots recorded in replay)**

## C15 Final execution controller

- Source: `revision2_external/final_execution_controller.py:24` (FinalExecutionController.entry_decision / exit_decision)
- Instances: 1
- Scope: symbol
- Input: ID approval, studies direction, exit PID telemetry, path observation
- Setpoint: BOOTSTRAP_MPC target/max hold
- Error: n/a (decision table)
- P: n/a
- I: n/a
- D: n/a
- Clamp: n/a
- Output limits: ADMIT/DEFER/CANCEL; EXIT/EXIT_NEXT_BAR/PROTECT/HOLD
- Reset: n/a
- Update frequency: per candidate / per held bar
- State persistence: none
- Runtime actuator: entry: admission; exit: telemetry only unless final_exit_authority=True (uncommitted at cb10fac)
- Registry parameters: none
- Hardcoded values: none
- Tests: tests_external/test_final_execution_controller*.py
- Status: **ACTIVE (entry); ADVISORY (exit)**

## C16 Closed-loop supervisor: entry quality, path loop, portfolio derate

- Source: `revision2_external/closed_loop_control.py:366` (ClosedLoopSupervisor / TradeReferencePath / CausalOutcomeLedger / SymbolDynamicsProfiler)
- Instances: 1 supervisor; per-symbol profiles
- Scope: symbol / portfolio
- Input: realized outcomes, trade path, gross exposure
- Setpoint: reference path expected R; soft budget
- Error: expected_r - actual_r (path)
- P: confidence offset gain cl_confidence_offset_gain
- I: outcome ledger (counts)
- D: n/a
- Clamp: entry derate in [0.5, 1]; offset <= cl_confidence_offset_max
- Output limits: derate only
- Reset: never
- Update frequency: per candidate / per held bar
- State persistence: whole run
- Runtime actuator: active_paper: entry size derate, confidence offset, path stop tightening/exit (advisory under full governor authority)
- Registry parameters: cl_*
- Hardcoded values: _ENTRY_DERATE_BOUNDS (0.5, 1.0), persistence bounds, gain-scale bounds (FIXED_SAFETY / TELEMETRY_ONLY, annotated); TradeReferencePath.curve_gamma = 1.0 (applied once in progress(); the a695913 double-application fix is already present at cb10fac)
- Tests: tests_external/test_closed_loop_*.py, tests/test_revision5_closed_loop_unit_controls.py
- Status: **ACTIVE**

## C17 HMM risk hysteresis

- Source: `revision2_external/closed_loop_control.py:61` (HMMRiskHysteresis.update)
- Instances: 1 per symbol
- Scope: symbol
- Input: HMM stress posterior
- Setpoint: enter/exit probabilities
- Error: filtered - threshold
- P: n/a (latch)
- I: EMA filter cl_hmm_smoothing_alpha
- D: n/a
- Clamp: derate in [0, 1]
- Output limits: suggested derate
- Reset: never
- Update frequency: per candidate
- State persistence: whole run
- Runtime actuator: none (HMM_REGIME_RISK_SHADOW telemetry)
- Registry parameters: cl_hmm_*
- Hardcoded values: none
- Tests: tests_external/test_closed_loop_*.py
- Status: **SHADOW**

## C18 Native R5 dynamic parameter controller (governor/AVR/protection schedule)

- Source: `revision5/dynamic_parameters.py:360` (Revision5DynamicParameterController.evaluate)
- Instances: 1 per plant
- Scope: plant (applied per bay)
- Input: R5EnvironmentState (vol ratio, HMM stress, trend share, drawdown, mean governor error)
- Setpoint: n/a (gain schedule)
- Error: control_error_delta = mean governor last_error
- P: see schedule
- I: n/a
- D: n/a
- Clamp: every output clipped (0.6/2.2 Kp, 1/3.5 Ki divisor, 0.6/2.0 Kd, ...)
- Output limits: immutable DynamicParameterSnapshot
- Reset: n/a
- Update frequency: every bar (PAPER_APPLY)
- State persistence: none (snapshot)
- Runtime actuator: governor runtime values (C07-C09), AVR profile (C11), SEL-300G and MiCOM limits, bay cooldowns; Kp/Ki/Kd ratio scales onto Box 6 PIDs (C01-C04)
- Registry parameters: none
- Hardcoded values: all coefficients; strategy/execution/safety profiles, dispatcher_min_floor/max_ceiling and risk_derate_factor have NO consumer (dead outputs)
- Tests: tests/test_revision5_dynamic_parameters.py, tests/test_revision5_native_dynamic_controls.py
- Status: **ACTIVE (misclassified DP-01 simulation-only)**

## C19 Bay cooldown / ANSI 86 lockout

- Source: `revision5/ccpp_unified_plant.py:1181` (TurbineBayPanel.register_outcome)
- Instances: 5
- Scope: bay
- Input: realized R and exit reason
- Setpoint: n/a
- Error: n/a
- P: n/a
- I: consecutive stop counter
- D: n/a
- Clamp: lockout at 2 consecutive stops
- Output limits: cooldown bars (scheduled 8..30 / 2..8)
- Reset: reset_session at each trading date
- Update frequency: per close
- State persistence: session
- Runtime actuator: plant admission BAY_COOLDOWN / ANSI_86_LOCKOUT; HRSG unavailability
- Registry parameters: none
- Hardcoded values: LOSS_COOLDOWN_BARS = 15, TARGET_COOLDOWN_BARS = 3 (ccpp_unified_plant.py:73-74); consecutive_stops >= 2
- Tests: tests/test_revision5_ccpp_plant.py
- Status: **ACTIVE**

## C20 SEL-300G unit protection and MiCOM grid protection

- Source: `revision5/ccpp_protection_cubicles.py:755` (BayUnitProtectionSEL300G / MasterGridProtectionMiCOM)
- Instances: 5 SEL + 1 MiCOM
- Scope: bay / plant
- Input: spread band, tick age, bar range/ATR; Nifty 15m return, vol z, fleet drawdown
- Setpoint: protection limits
- Error: n/a
- P: n/a
- I: ANSI 32R adverse counter
- D: n/a
- Clamp: limits tighten only with stress
- Output limits: trip
- Reset: MiCOM drawdown latch per session
- Update frequency: per entry / held bar; MiCOM per timestamp
- State persistence: session
- Runtime actuator: entry rejection, unit breaker trip, forced protective exit, grid intertie open
- Registry parameters: micom_nifty_vol_z_window
- Hardcoded values: SEL base 120 s / 0.005 / 3.5 / 0.0035 / 4 and schedule gains; MiCOM base 0.020 / 0.015 / 3.0 and schedule gains
- Tests: tests/test_revision5_relay_coordination.py, tests/test_r5_ccpp_mark_v.py
- Status: **ACTIVE (protection)**

## C21 Symbol loss cooldown latch (orchestrator)

- Source: `revision2_external/orchestrator.py:94` (_execute_exit symbol_cooldown_until_bar)
- Instances: 1 latch per symbol
- Scope: symbol
- Input: net losing exit
- Setpoint: n/a
- Error: n/a
- P: n/a
- I: n/a
- D: n/a
- Clamp: n/a
- Output limits: entry blocked until exit bar + 15
- Reset: session rollover
- Update frequency: per close
- State persistence: session
- Runtime actuator: entry admission
- Registry parameters: none
- Hardcoded values: BAY_LOSS_COOLDOWN_BARS = 15 (F11 fixed policy)
- Tests: tests_external/test_orchestrator_*.py
- Status: **ACTIVE**

## C22 R5 bay gain scale onto Box 6 PIDs

- Source: `revision2_external/orchestrator.py:721` (_plant_bar_step _dynamic_gain_scale)
- Instances: 5 tuples
- Scope: bay -> symbol
- Input: snapshot governor gains / spec gains
- Setpoint: n/a
- Error: n/a
- P: Kp scale
- I: Ki scale
- D: Kd scale
- Clamp: inherits C18 clips
- Output limits: multipliers
- Reset: n/a
- Update frequency: every bar
- State persistence: latest
- Runtime actuator: C01-C04 tunings (PAPER_APPLY)
- Registry parameters: none
- Hardcoded values: none
- Tests: tests/test_revision5_native_dynamic_controls.py
- Status: **ACTIVE**

## C23 Mark V executive controller (ccpp_unified_plant)

- Source: `revision5/ccpp_unified_plant.py:2719` (MarkVExecutiveController)
- Instances: 0 in replay
- Scope: n/a
- Input: MarkVTelemetryInputs
- Setpoint: 0.60/0.25
- Error: n/a
- P: n/a
- I: n/a
- D: n/a
- Clamp: 0.15 flameout floor
- Output limits: n/a
- Reset: n/a
- Update frequency: never in replay
- State persistence: none
- Runtime actuator: NONE: used only by tests
- Registry parameters: none
- Hardcoded values: SPEEDTRONIC_FSRMIN_FLAMEOUT_FLOOR 0.15, SPEEDTRONIC_UNWIND_IDLE_CUTOFF 0.25, 0.60
- Tests: tests/test_r5_simulation_isolation.py
- Status: **DEAD (superseded by revision5/governor_authority.py)**

## C24 Tier-2 supervisory bridge regime schedule

- Source: `revision5/supervisory_bridge.py:493` (Revision5SupervisoryBridge.evaluate)
- Instances: 0 in replay
- Scope: n/a
- Input: MarketEnvironmentState
- Setpoint: n/a
- Error: n/a
- P: n/a
- I: n/a
- D: n/a
- Clamp: 0.42/0.70, 2/6, 12/60, 8/30
- Output limits: n/a
- Reset: n/a
- Update frequency: tests only
- State persistence: none
- Runtime actuator: NONE: called only from tests
- Registry parameters: none
- Hardcoded values: get_tier2_regime_parameters coefficients (dynamic_parameter_controller.py)
- Tests: tests/test_revision5_supervisory_bridge.py
- Status: **DEAD**

## C25 Governor position policy (offline experiment)

- Source: `revision5/governor_position_policy.py:7` (GovernorPositionPolicy)
- Instances: 0 by default (orchestrator.governor_position_policy is None)
- Scope: position
- Input: MFE, PID u, conviction
- Setpoint: n/a
- Error: n/a
- P: pid_alpha_r * clip(u)
- I: n/a
- D: n/a
- Clamp: pid_u_max
- Output limits: trailing gap >= minimum_gap_r
- Reset: per trade
- Update frequency: per held bar when set by a script
- State persistence: per trade
- Runtime actuator: trailing stop when injected by offline scripts only
- Registry parameters: none
- Hardcoded values: dataclass defaults 0.45/0.10/2.0/0.15/0.30/0.20/0.10/2
- Tests: tests/test_r5_governor_refactor.py
- Status: **OFFLINE EXPERIMENT (inactive by default; superseded by Protocol V3)**

## C26 Machine dynamics / startup synchronization / relay coordination

- Source: `revision5/machine_dynamics.py:41` (machine dynamics, startup sequencer, synchronizer)
- Instances: installed per bay at plant construction; never stepped in replay
- Scope: bay
- Input: n/a in replay
- Setpoint: n/a
- Error: n/a
- P: n/a
- I: n/a
- D: n/a
- Clamp: n/a
- Output limits: n/a
- Reset: n/a
- Update frequency: never in replay
- State persistence: n/a
- Runtime actuator: NONE in replay (startup_sequencer not configured, run_machine_scan/run_unit_synchronization never called)
- Registry parameters: none
- Hardcoded values: machine archetype tables (simulation)
- Tests: tests/test_revision5_machine_dynamics.py, tests/test_revision5_startup_synchronization.py
- Status: **SIMULATION ONLY**
