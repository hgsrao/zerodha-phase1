# Remaining hardcoded values, with justification

Enforced by `tests/test_r5_parameter_authority.py::test_no_active_controller_consumes_an_unregistered_tuning_literal`
against the register in `tests/r5_remaining_literals.py`. Any new numeric literal (or one more occurrence of a
listed value) in the native controller modules fails that test until it is registered or justified there.
0 and 1 (identities, unit bounds, counters) are not listed.

## Native controller modules (enforced)

| File | Values | Why they are not registry parameters |
|---|---|---|
| `revision5/governor.py` | `1e-12` | Tolerance of the capital-weight sum check on the registry default view (numerical guard). |
| | `2.0` | `trend_overspeed` limit = threshold − 2 × base_z: mirrors the BUY threshold about zero (structural symmetry, not a tuning choice). |
| `revision5/dynamic_parameters.py` | `3.0` ×5 (3 × prior 1/3) | `R5EnvironmentState` uninformative prior over the three regimes. |
| | 0.0005 … 120.0 (the strategy / execution / safety profiles and `plant.dispatcher_min_floor` / `dispatcher_max_ceiling`, and `risk_derate_factor`'s exposure) | Snapshot outputs with **no runtime consumer** in Engines A, B or C (audited: no reader in the orchestrator, plant, gates or sizing). Registering them would claim an authority they do not have. If a consumer is ever wired, the coefficients must be registered first (the literal test will force it). |
| `revision5/ccpp_protection_cubicles.py` | `100.0` | AVR lot volatility scalar 1 / (ATR as a percentage of price) (unit conversion) and % formatting of trip messages. |
| | `2` ×3 | MiCOM ANSI 21 needs ≥ 2 returns and window + 2 closes for a z-score (structural). |
| | `1e-9` | Spread division guard (numerical). |
| `revision5/hrsg.py` | `1_000_000.0` | Constructor default plant capital; the plant always passes the run's capital. |
| | `0.15`, `0.60`, `0.45`, `0.50` | `superheat_headroom_factor()` and `max_superheat_boost`: no runtime caller (dead path). |
| | `2` ×2, `1e-15` ×2, `1e-6` ×2 | Pearson correlation needs ≥ 2 samples; zero-variance guard; capital-conservation tolerance (structural / numerical). |
| `revision5/ccpp_unified_plant.py` | `1_000_000.0` ×2 | Constructor default capitals; runners pass the run capital. |
| | `2` ×2 | Plant stop / take-profit rounded to paise (advisory outputs). |
| | `1e-15`, `1e-12`, `1e-9`, `1e-6` | Bounded projection, bounds check and conservation tolerances (numerical). |
| | `0.15`, `0.25`, `0.60` ×3, `4` ×5 | `MarkVExecutiveController` and `SPEEDTRONIC_*`: used only by tests (dead in A/B/C; superseded by `revision5/governor_authority.py`, whose values are registered). |

## Existing controllers outside this programme's scope (already annotated in code; not changed)

The programme brief says the studies-weight PID, Box 6, closed-loop and Mark V work must be extended, not redone.
Their remaining literals are fixed envelopes that the code already labels as such:

| Location | Values | Status |
|---|---|---|
| `revision2_external/pid_controller.py` `_ENTRY_TIMING_FLOOR`, `_EXIT_TIGHTNESS_FLOOR` | 0.3, 0.5 | `FIXED_SAFETY_ENVELOPE` (annotated). The entry timing multiplier is telemetry only. |
| `revision2_external/continuous_exit_controller.py` `_EXIT_TIGHTNESS_FLOOR`, initial setpoint | 0.5, 0.5 | `FIXED_SAFETY_ENVELOPE`; the setpoint is overwritten on the first call. |
| `revision2_external/composite_study_signal.py` `MIN_WEIGHT`, `MAX_WEIGHT`, indicator periods | 0.05, 0.60; 9/26/52, 20, 5/3/3 | `FIXED_SAFETY_ENVELOPE`; indicator periods are study definitions. |
| `revision2_external/dynamic_parameter_controller.py` Tier-2 coefficients, regime priors | 0.45/0.58/0.75 …; 0.7/0.2/0.1 … | Tier-2 is called only from `supervisory_bridge` tests (dead in A/B/C). The Tier-1 / Tier-3 envelopes are now registry-owned (`tier1_*`, `tier3_*`). |
| `revision2_external/closed_loop_control.py` `_ENTRY_DERATE_BOUNDS` etc. | (0.5, 1.0), … | `FIXED_SAFETY_ENVELOPE` / `TELEMETRY_ONLY` (annotated). |
| `revision5/governor_position_policy.py` `GovernorPositionPolicy` defaults | 0.45, 0.10, 2.0, 0.15, 0.30, 0.20, 0.10, 2 | Offline experiment, inactive by default (`governor_position_policy is None`); superseded by Protocol V3 (`gov_v3_*`, registered). |
| `revision5/machine_archetypes.py`, `machine_dynamics.py`, `startup_synchronization.py`, `relay_coordination.py` | simulation tables | Simulation only: installed at plant construction, never stepped by the replay engines. |

## Registered but not actuating (visible, not hidden)

* **ADVISORY (12):** `gov_<bay>_atr_barrier`, `gov_<bay>_target_m`, `plant_stop_floor_price_fraction`,
  `plant_target_min_reward_risk` — the plant admission's stop/take-profit suggestion; Engines A/B/C use the MPC plan's.
* **INACTIVE (15):** `gov_<bay>_z_exit` (no consumer); `avr_mvar_limit_*` (no runtime path supplies a measured
  MVAR); `gsch_integral_max_scale`, `gsch_kp_scale_min`, `gsch_kd_scale_min`, `gsch_offset_min_upper_scale`,
  `gsch_droop_max_min_scale`, `tier3_kp_scale_min`, `tier3_kd_scale_min` (structurally unreachable clips, with the
  proof in each row's description). The SEL-300G 87G / 32R rows are live with `unit_protection_mode="pre_sync_and_in_flight"`.

The sensitivity sweep asserts these classes: an ADVISORY change moves only the advisory output, an INACTIVE change
moves nothing.
