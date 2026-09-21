# BB04 dynamic parameterization audit

Repository: /home/shrinivas/blade_complete_backup/ECS_Project
Branch: codex/revision5-ccpp-unified
Starting checkpoint: 6de77a1a8ff1ed26cfb7487bcdd0f8ac79ccbe5e

Scope: BB04 only. BB01–BB03 remain closed. BB05+ untouched.
The R5 supervisory bridge remains INFORMATION_ONLY, without action/entry authority.
No Optuna/Ray calibration, P&L optimization, PID tuning, production runner, or live runner was performed.
Calling the existing calibrate() preprocessing method in unit tests is not optimizer calibration.
Passing tests do not establish profitability or live readiness.

## Exact intended files

- /home/shrinivas/blade_complete_backup/ECS_Project/calibration_config.py
- /home/shrinivas/blade_complete_backup/ECS_Project/canonical_parameter_registry.py
- /home/shrinivas/blade_complete_backup/ECS_Project/revision2_external/indicators_talib.py
- /home/shrinivas/blade_complete_backup/ECS_Project/revision2_external/orchestrator.py
- /home/shrinivas/blade_complete_backup/ECS_Project/scripts/trace_boxes_1_to_5_multi_signal.py
- /home/shrinivas/blade_complete_backup/ECS_Project/tests/test_canonical_parameter_registry.py
- /home/shrinivas/blade_complete_backup/ECS_Project/tests/test_revision5_bb04_dynamic_parameterization.py
- /home/shrinivas/blade_complete_backup/ECS_Project/outputs/BB04-dynamic-parameterization-audit.md

## Ownership and compatibility

The canonical registry alone owns defaults, bounds, types and eligibility. The existing manifest adds names only, not a duplicate value registry. No R5 registry was introduced.
All 16 new controls have calibratable=True, consistent with the existing PA surface, but are ENGINEERING_INITIAL_VALUE / NOT_CALIBRATED. Labels use existing ParameterSpec.notes and this audit, not a new runtime metadata source.
The contract expands from 69 to 85 target parameters and from 47 to 63 eligible parameters; the fixed 22 targets and 20 safety parameters remain unchanged. The frozen identity and its regression expectations are updated to:
42d9b0a6fa8f82b3fb060be21ca5aa71a43f88dc6f23738c8fbf889b3d854bf1
Older incomplete configs must obtain the new canonical defaults and new identity before startup validation.

Calibration and evaluation now share atr_calculation_period (default 20, bounds 10–30).
The two-argument calibrate(symbol, bars) API is retained for existing tracked callers/tests and reads the registry default, never 14. The external orchestrator and tracked tracing script pass their effective config explicitly; the tracing script was not executed.
Other revision2/revision4 call sites instantiate the separate in-house PredictiveAnalyticsBox and were left alone.
If evaluation changes the ATR horizon, BB04 recalculates scales from its saved original warmup frame, avoiding future-bar leakage. Short histories cap the effective period to available observations with TA-Lib's structural minimum 1, the same policy as runtime.

## Literal audit

All source references below are in revision2_external/indicators_talib.py; checkpoint means the starting commit.
Test names refer to tests/test_revision5_bb04_dynamic_parameterization.py unless stated otherwise.

| literal/source line | semantic role | classification | canonical parameter | default | min | max | provenance | calibration status | real consumer | sensitivity test | disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ATR 14; checkpoint L58 | Calibration ATR horizon | ACTIVE_DYNAMIC / CANONICALIZED | atr_calculation_period | 20 | 10 | 30 | EXISTING_CANONICAL_REGISTRY | No calibration performed here | calibrate and evaluate | test_atr_one_owner_default_explicit_and_runtime_change | Removed independent 14; one owner, default compatibility and horizon changes tested |
| momentum_z / 3.0; checkpoint L115 → current L120,158 | Momentum amplitude | ACTIVE_DYNAMIC | momentum_normalization_divisor | 3.0 | 1.5 | 6.0 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[momentum_normalization_divisor] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| ATR 0.001 (comparison and max); checkpoint L131 → current L121,174,175 | Absolute ATR fallback trigger and floor | ACTIVE_DYNAMIC | pa_atr_absolute_floor | 0.001 | 0.0001 | 0.01 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_atr_fallback_real_volatility[pa_atr_absolute_floor] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| close * 0.005; checkpoint L132 → current L122,175 | Price-relative ATR fallback | ACTIVE_DYNAMIC | pa_atr_fallback_price_fraction | 0.005 | 0.0005 | 0.01 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_atr_fallback_real_volatility[pa_atr_fallback_price_fraction] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| persistence_requirement / 2.0; checkpoint L165 → current L123,209 | Persistence qualification normalization | ACTIVE_DYNAMIC | pa_persistence_threshold_divisor | 2.0 | 1.0 | 3.0 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_persistence_threshold_divisor] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| 0.1 * min(...); checkpoint L166 → current L124,210 | Persistence strength gain | ACTIVE_DYNAMIC | pa_persistence_bonus_gain | 0.1 | 0.0 | 0.25 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_persistence_bonus_gain] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| min(requirement, 2.0); checkpoint L166 → current L125,210 | Persistence bonus input ceiling | ACTIVE_DYNAMIC | pa_persistence_bonus_cap | 2.0 | 1.0 | 2.5 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_persistence_bonus_cap] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| entry_threshold * 0.2; checkpoint L181 → current L126,225 | Directional activation fraction | ACTIVE_DYNAMIC | pa_direction_activation_fraction | 0.2 | 0.0 | 1.0 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_direction_activation_fraction] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| VWAP / 3.0; checkpoint L122 → current L127,165 | VWAP amplitude | ACTIVE_DYNAMIC | pa_vwap_normalization_divisor | 3.0 | 1.5 | 6.0 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_vwap_normalization_divisor] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| volume / 3.0; checkpoint L138 → current L128,181 | Volume confirmation amplitude | ACTIVE_DYNAMIC | pa_volume_normalization_divisor | 3.0 | 1.5 | 6.0 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_volume_normalization_divisor] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| vol_ratio < 0.7; checkpoint L141 → current L129,184 | Low volatility regime boundary | ACTIVE_DYNAMIC | pa_low_vol_ratio_boundary | 0.7 | 0.5 | 0.9 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_low_vol_ratio_boundary] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| vol_ratio < 1.5; checkpoint L143 → current L130,186 | High volatility regime boundary | ACTIVE_DYNAMIC | pa_high_vol_ratio_boundary | 1.5 | 1.1 | 2.0 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_high_vol_ratio_boundary] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| history[-5:]; checkpoint L163 → current L131,202,207 | Persistence observation window | ACTIVE_DYNAMIC | pa_persistence_lookback | 5 | 1 | 10 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_persistence_lookback] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| confidence * 1.10; checkpoint L171 → current L132,215 | Green band confidence gain | ACTIVE_DYNAMIC | pa_green_confidence_multiplier | 1.1 | 1.0 | 1.25 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_green_confidence_multiplier] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| confidence * 0.85; checkpoint L174 → current L133,218 | Amber band confidence gain | ACTIVE_DYNAMIC | pa_amber_confidence_multiplier | 0.85 | 0.7 | 1.0 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_amber_confidence_multiplier] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| confidence * 0.5; checkpoint L177 → current L134,221 | Red band confidence gain | ACTIVE_DYNAMIC | pa_red_confidence_multiplier | 0.5 | 0.25 | 0.75 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_real_consumer_sensitivity[pa_red_confidence_multiplier] | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| max(30, min(n, 60)); checkpoint L105 → current L135,145 | Automatic calibration warmup length | ACTIVE_DYNAMIC | pa_auto_warmup_bars | 60 | 30 | 120 | ENGINEERING_INITIAL_VALUE | NOT_CALIBRATED | TALibPredictiveAnalyticsBox.evaluate | test_auto_warmup_real_consumer_sensitivity | Canonical registry read, ParameterUse trace, bounded and output-sensitive |
| 1e-6; checkpoint L53,55,60,61,63 | Undefined/zero-scale denominator safeguard | STRUCTURAL_NOT_PARAMETER | — | 1e-6 | — | — | Numerical domain guard | NOT_APPLICABLE | calibrate scale computation | test_numerical_safeguards_are_finite (1, 2, 10 bars) | Named private epsilon, allow-listed only at declaration; not calibratable |
| /100.0; checkpoint L113 | TA-Lib ROC percent-to-fraction conversion | STRUCTURAL_NOT_PARAMETER | — | 100 | — | — | Unit conversion | NOT_APPLICABLE | evaluate raw momentum | test_no_anonymous_operating_literals | Allow-listed only at ROC conversion expression |
| -1/0/1, 1.0, n-1, indices, clipping and valid-period minimum | Sign, identity, bounds and array/TA-Lib domain structure | STRUCTURAL_NOT_PARAMETER | — | Unchanged | — | — | Mathematical structure | NOT_APPLICABLE | calibrate/evaluate | test_no_anonymous_operating_literals | No tunable parameters |
| max(...,20); checkpoint L159 | History storage reserve | STRUCTURAL_NOT_PARAMETER | — | 20 | — | — | Storage capacity | NOT_APPLICABLE | evaluate history deque | test_no_anonymous_operating_literals | Exceeds smoothing maxima 8/4 and persistence maximum 10; cannot truncate a configured window; allow-listed only in exact max expression |
| max(30,min(n,60)); checkpoint L105, redundant 30/min(n) portion | Slice bounds | STRUCTURAL_NOT_PARAMETER for redundant portion | pa_auto_warmup_bars owns the real 60 | — | — | — | Python slicing semantics | NOT_APPLICABLE | evaluate automatic warmup | test_auto_warmup_real_consumer_sensitivity | bars[:60] is equivalent for every nonempty history; removed redundant slice arithmetic |
| 1e-3,1.0,1e-3; checkpoint L66 | Unreachable uncalibrated scale fallback | REMOVED_UNREACHABLE | — | — | — | — | Source-grounded call graph | NOT_APPLICABLE | _scale_for called after guaranteed calibration | focused and external TA-Lib tests | Direct calibrated scale lookup; no hidden fallback defaults |

The epsilon fallback supplies a finite denominator when variance or ATR history is undefined, or price is zero. The baseline max guard prevents tiny/zero reference denominators; it is a numerical conditioning rule rather than an ATR output floor. It remains fixed, unlike the explicit absolute/price-relative ATR output controls.
The existing float(std) or epsilon expression did not catch NaN (NaN is truthy); explicit finite/positive guards now preserve the intended numerical safeguard for short/flat fixtures. Valid positive scales and the signal formulas are otherwise preserved.

## Conservative engineering bounds

These are engineering limits, not empirical recommendations; no performance data or optimizer was used.

- Momentum, VWAP and volume divisors: 1.5–6.0, strictly positive and half to twice the current 3.0. They scale different inputs independently; sharing an owner would couple separate components.
- ATR absolute floor: 0.0001–0.01 in price units, a bounded decimal neighborhood of 0.001, strictly positive. Units are instrument-dependent; these bounds are not a claim of universal suitability.
- ATR relative fallback: 0.0005–0.01 (0.05%–1% of price), positive with an upper limit twice the current 0.5%.
- Persistence threshold divisor: 1–3, positive and covering the existing requirement range. It sets qualification, while the 1–2.5 bonus cap limits magnitude. Combining them would couple qualification and amplification.
- Persistence gain: 0–0.25, allowing disablement and limiting maximum amplification to 1 + 0.25 × 2.5 = 1.625.
- Direction fraction: 0–1, bounded to the canonical entry threshold. Zero retains the formula's existing equality/sign convention; the test uses nonzero signal strength.
- Regime boundaries: low 0.5–0.9 and high 1.1–2.0 keep the three regimes ordered for every allowed configuration.
- Persistence lookback: 1–10 bars, from one observation to twice the previous five; below the structural 20-item history reserve.
- Green gain: 1–1.25; amber: 0.7–1; red: 0.25–0.75. These preserve each band's amplification/attenuation role, with existing clipping unchanged.
- Automatic warmup: 30–120 bars, half to twice the previous 60, and at least the maximum canonical ATR period. Explicit caller-provided warmup remains authoritative. This parameter is consumed when evaluate first auto-calibrates a symbol; later changes do not replace an established warmup dataset.

## Validation

- New focused BB04 module: 28 passing tests, including real-output sensitivity for all 16 new parameters.
- Focused BB03/BB04 plus external TA-Lib group: 34 passed.
- BB01–BB04 bridge group: 14 passed.
- Canonical registry and external startup validation: 7 passed.
- Full tracked R5 regression plus the intended new BB04 module: 172 passed across 19 modules.
- The two unrelated untracked R5 test files were excluded by selecting tracked paths explicitly.
- AST allow-list permits structural 0/1 and context-specific epsilon/percent conversion/storage reserve; six mutation examples must be flagged.
- Sensitivity fixtures run real evaluate and TA-Lib, with controlled calibration state/history where necessary to isolate branches. ATR owner and automatic warmup tests use actual calibration output without injected scales.
- Direction test explicitly demonstrates directional 1 versus neutral 0 on identical bars/config except activation fraction.
- Existing bridge tests verify INFORMATION_ONLY and absence of action; supervisory_bridge.py is unchanged.

Commands, run from the repository:
- python3 -m pytest -q tests/test_revision5_bb04_dynamic_parameterization.py tests/test_revision5_bb03_bb04_bridge.py tests_external/test_indicators_talib.py
- python3 -m pytest -q tests/test_revision5_bb01_bb02_parameterization.py tests/test_revision5_bb03_bb04_bridge.py tests/test_revision5_supervisory_bridge.py
- python3 -m pytest -q tests/test_canonical_parameter_registry.py tests_external/test_startup_validation.py
- Full suite: select git ls-files tests/test_revision5*.py and pass exactly those paths to python3 -m pytest -q (including the new intended module).

## Protected untracked files

Left untracked and not edited, staged, reset, stashed or removed:
- 0]
- audit_constants.py
- revision2_external/orchestrator.py.bak_clean
- revision5/r5_plant_state.sqlite3
- run_monday_48.py
- tests/test_revision5_fec_plant_bridge.py
- tests/test_revision5_orchestrator_integration.py
- work/

BB04 work stops here. BB05+ untouched; no optimizer calibration or PID tuning performed.
