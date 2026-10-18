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

## Supporting controllers and structural values

Closed-loop and studies limits were promoted into registry ownership by the verified dynamization closure. Other structural/simulation values remain documented below; registry ownership is not calibration evidence.

| Location | Values | Status |
|---|---|---|
| `revision2_external/pid_controller.py` `_ENTRY_TIMING_FLOOR`, `_EXIT_TIGHTNESS_FLOOR` | 0.3, 0.5 | `FIXED_SAFETY_ENVELOPE` (annotated). The entry timing multiplier is telemetry only. |
| `revision2_external/continuous_exit_controller.py` `_EXIT_TIGHTNESS_FLOOR`, initial setpoint | 0.5, 0.5 | `FIXED_SAFETY_ENVELOPE`; the setpoint is overwritten on the first call. |
| `revision2_external/composite_study_signal.py` `MIN_WEIGHT`, `MAX_WEIGHT`, indicator periods | Registry default views; 9/26/52, 20, 5/3/3 | `studies_weight_min/max` own the effective unit-sum share bounds. Indicator periods are study definitions. |
| `revision2_external/dynamic_parameter_controller.py` Tier-2 coefficients, regime priors | 0.45/0.58/0.75 …; 0.7/0.2/0.1 … | Tier-2 is called only from `supervisory_bridge` tests (dead in A/B/C). The Tier-1 / Tier-3 envelopes are now registry-owned (`tier1_*`, `tier3_*`). |
| `revision2_external/closed_loop_control.py` `_ENTRY_DERATE_BOUNDS` etc. | Registry default views | `cl_entry_derate_*`, `cl_persistence_*`, `cl_gain_scale_*` own the limits. Gain-scale suggestions remain advisory. |
| `revision5/governor_position_policy.py` `GovernorPositionPolicy` defaults | 0.45, 0.10, 2.0, 0.15, 0.30, 0.20, 0.10, 2 | Offline experiment, inactive by default (`governor_position_policy is None`); superseded by Protocol V3 (`gov_v3_*`, registered). |
| `revision5/machine_archetypes.py`, `machine_dynamics.py`, `startup_synchronization.py`, `relay_coordination.py` | simulation tables | Simulation only: installed at plant construction, never stepped by the replay engines. |

## Advisory and conditional actuation

The original312 audit is now90 dynamic,210 constant/coefficient,12 advisory,0 inactive implementations. Eight new envelope keys bring the expanded total to320:90 dynamic,216 constant/coefficient,14 advisory. See [DYNAMIZATION_CLOSURE.md](DYNAMIZATION_CLOSURE.md) and [the complete per-key migration](CLOSURE_PARAMETER_MIGRATION.md).

* Original12 advisory: `gov_<bay>_atr_barrier`, `gov_<bay>_target_m`, `plant_stop_floor_price_fraction`, `plant_target_min_reward_risk`. These bound plant stop/target suggestions; actual order levels remain with the order-planning authority.
* Added2 advisory: `cl_gain_scale_min/max`, bounds on a recorded gain suggestion. No actuator is claimed.
* All21 formerly inactive controls have verified conditional consumers:5 side-aware bay z exits,3 MVAR limiter controls,7 reachable governor/Box6 bounds,4 dispatcher-floor coefficients,2 tighten-only timeout coefficients.
* Runtime-profile coefficients require `dynamic_profile_mode="consume"`. Other documented modes remain explicit. Default-off modes do not imply the consumers ran.
* MVAR requires measured AVR and an actual supplied causal signed per-unit measurement per bay/bar. Required mode fails closed without it. Market OHLC is not a physical MVAR measurement; no source availability or live activation is claimed.

Sensitivity tests distinguish actuator outputs from suggestions. The closure tests demonstrate saturation/recovery, binding bounds, budget conservation and real shared consumer paths with synthetic inputs. Implementation acceptance does not qualify production or calibration.
