# Behaviour changes that require explicit approval

The default engine is decision-identical to the baseline (`scripts/r5_golden_master_local.py`: byte-identical on
every behavioural report section against a cb10fac reference captured on this machine; identical decisions
against the stored 500abab golden master). Nothing below is active unless a caller opts in. None of these modes
is sealed into the re-sealed protocol (`engine.opt_in_modes_not_sealed`).

| # | Switch | What changes | Why | Evidence |
|---|---|---|---|---|
| 1 | `governor_position_mode="v3"` (full authority only) | The bay governor's inner position loop becomes Protocol V3 (`PositionControlV3`, 9 `gov_v3_*` parameters): two-rate leaky integral, no derivative kick, PID-modulated trailing gap `base_gap·(1−κ·tanh u)` floored by one bar of noise, activation after `grace_bars` and `mfe_activation_r`, one-way floor armed for the next bar; the binary path-error exit is not used. Mark V, exhaust-spread and protection precedence are unchanged. | V2's `u` acts only through the binary `GOVERNOR_PATH_ERROR` exit. | `test_each_v3_parameter_changes_the_inner_loop_floor`; sweep rows `gov_v3_*`. Ported from a695913 **without** its `curve_gamma` hunk (already present at cb10fac, so it would be a no-op). |
| 2 | `avr_voltage_mode="measured"` (PAPER_APPLY plant) | The AVR regulates on a causal terminal-voltage measurement once per bar (`TurbineBayPanel.regulate_terminal_voltage`); admission and position control reuse that bar's result and **fail closed** (`AVR_TRIP`) on a missing, invalid or out-of-corridor (ANSI 27/59) voltage. The excitation command now moves both ways and scales every lot. | In legacy mode no voltage is ever supplied: the error is always 0, the PID never acts, ANSI 27/59 cannot fire and excitation only ratchets down through the stress-scheduled ceiling. | `test_nonzero_measured_voltage_error_changes_excitation_and_reaches_order_quantity`, `test_measured_avr_fails_closed_...`, `test_measured_avr_mode_changes_order_sizes` (A), Engine B/C mode tests. |
| 3 | `dispatcher_allocation_mode="bounded"` | Merit → EMA → bounded projection onto {floor ≤ w ≤ ceiling, Σw = 1}; the final applied allocation always satisfies the bounds. | Legacy clamps **before** the EMA and renormalises after, so a final weight can exceed the 0.35 ceiling (probe: 0.522; test: > 0.35). | `test_legacy_dispatcher_can_exceed_the_ceiling_and_bounded_mode_never_does`, `test_bounded_dispatch_reaches_the_bay_capital_actuator`. |
| 4 | `hrsg_correlation_mode="causal_bay_returns"` | Each bar the mean completed-bar return of each bay's measured members is fed to the HRSG economizer, so the correlation damper (`hrsg_rho_crit`, `hrsg_min_penalty_factor`) can derate correlated bays. | Replay never recorded return snapshots, so the damper was inert. | sweep rows `hrsg_rho_crit`, `hrsg_min_penalty_factor`, `hrsg_correlation_window`, `hrsg_min_correlation_samples`; causality test with the mode on. |
| 5 | `final_exit_authority=True` (carried from the engine-c worktree, commit c55bc65) **and its fix** (004c6c1) | The final execution controller's EXIT / EXIT_NEXT_BAR arms a next-open exit. The fix removes an early `return` that skipped the arming bar's intrabar stop/target, session close, max hold, plant protection and governor step. | User's uncommitted change; the fix restores protection precedence. | Engine A/B/C runners accept `--final-exit-authority`; causality test with the flag on. |

## Not a behaviour change

* `authority_telemetry=True` records `CONTROLLER_PARAMETER_STARTUP`, `CONTROLLER_APPLIED_VALUES` (session open),
  `DISPATCHER_UPDATE`, `AVR_VOLTAGE_REGULATION`, `HRSG_RETURN_SNAPSHOT` and extra `PLANT_DCS_ENTRY` fields, plus a
  per-value min/max/last summary. `test_authority_telemetry_is_telemetry_only` (A) and the B/C equivalents prove the
  orders are identical with it on.
* Registering the former literals changes `config_hash` and `parameter_coverage` in run reports (identity, not
  behaviour); every other report field is byte-identical (local gate).

## Classification changes awaiting acceptance

* 12 parameters classified ADVISORY and 19 INACTIVE (see `REMAINING_HARDCODED_VALUES.md`); 163 TUNABLE are
  optimizer-eligible in principle but NOT_CALIBRATED; the sealed search space is unchanged (5 parameters).
* Engines B/C: the Box 6 entry PID (`pid_k*_entry`, `mpc_entry_price_gain`, `mpc_schedule_*_gain`) has no actuator,
  because those engines re-anchor the plan at the venue fill. This is pre-existing design, now documented and
  pinned by tests; whether it should actuate there is a decision for you.
