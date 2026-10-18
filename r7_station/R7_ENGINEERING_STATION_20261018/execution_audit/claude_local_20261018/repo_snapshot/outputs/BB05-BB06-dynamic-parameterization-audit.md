# BB05 / BB06 dynamic parameterization audit

Branch: codex/r5-bb05-bb06-claude — base bf091fda11cb9be02f7536059dc204f7ec9600af
Scope: BB05 and BB06 only. BB07–BB10 untouched. No Optuna/Ray, strategy calibration, profit optimization or live run.
All new values are `ENGINEERING_INITIAL_VALUE` / `NOT_CALIBRATED`. Nothing here claims calibration, profitability, production or live readiness.

## 1. Proven identities

Authority: numbering in the docstring of `revision2_external/orchestrator.py` lines 5–9 of the box list (box 5 IntelligentDiscrimination → Gaussian HMM; box 6 ModelPredictiveControl → simple-pid), confirmed by constructors at `orchestrator.py` (`self.id_box`, `self.mpc`, `self.exit_controller`, `self.pa`).

| | BB05 | BB06 |
|---|---|---|
| module / class | `revision2_external/regime_id_box.py::HMMIntelligentDiscriminationBox`, `regime_hmm.py::GaussianHMM` | `revision2_external/pid_controller.py::SimplePIDModelPredictiveControlBox`, `continuous_exit_controller.py::ContinuousExitController`, `dynamic_parameter_controller.py::{MarketEnvironmentState,DynamicParameterController}` |
| constructor | `HMMIntelligentDiscriminationBox(config=self.config)` | `SimplePIDModelPredictiveControlBox(pid_enabled=…)`; `ContinuousExitController(kp,ki,kd,clamp,atr_droop_mult,baseline_window,saturation_exit_bars,config=self.config)` |
| runtime caller | `orchestrator`: `id_box.calibrate(symbol, bars[:0], config)` at run start; `id_box.evaluate(signal, config, close)` each entry bar; `id_box._current_regime(...)` each open-position bar | `mpc.build_plan(signal, decision, next_open, atr, config)` per approved entry; `exit_controller.configure(config)` then `.update(...)` per open bar |
| inputs | close series (features: % return, rolling vol), PASignal (confidence, volatility, band, direction) | IDDecision, PASignal.exit_confidence, chart-studies confidence, ATR, close, bars (for range proxy) |
| outputs | regime label calm/stressed/unknown, posterior (telemetry), `IDDecision` | `TradePlan`, `pid_info` telemetry, `ExitControllerState` (trailing stop, saturation streaks, shadow stop) |
| retained state | `_bar_history`, `_cached_model`, `_cached_posterior`, `_cached_stressed_state`, `_bars_since_refit`, `_last_regime_observation` (per symbol) | entry/exit `simple_pid.PID` objects + `_confidence_history` (per symbol); exit controller's two PIDs + baselines + saturation streaks |
| consumer | orchestrator entry gate, `regime_stressed_exit`, HMM posterior shadow adapter (shadow-only) | orchestrator plan → safety gates/sizing/execution path; exit levels in the orchestrator's replay |
| authority type | **strategic discrimination** (admit/veto) plus regime *information* (posterior telemetry, shadow-only). Not final trading authority. | **PID/MPC control** (plan geometry) and **continuous exit control** (trailing stop, saturation exit) inside the external Revision-2 replay engine. Not the R5 governor. |

Distinction of authorities: safety/protection remains in `SafetyContract`/gates (untouched); governor/final authority remains R5 (`revision5/*`, untouched); execution is `CostedPaperBrokerAdapter` (paper only). BB05/BB06 add no parallel authority: the only new R5-side object is an `INFORMATION_ONLY` frozen snapshot (§6).

## 2. Operational parameter inventory (29 registry parameters)

Owner for all: `canonical_parameter_registry.py` (`black_box` ID = BB05, MPC = BB06). Doctrine class ACTIVE_DYNAMIC unless stated. Calibration status all NOT_CALIBRATED; "optimizer" = present in `trading_search_space()`.

| parameter | default (= bf091fd literal) | min–max | consumer | update semantics | optimizer |
|---|---|---|---|---|---|
| id_feature_window | 200 | 100–400 | `_current_regime`, `calibrate`, history deque | read each evaluation; deque re-sized in place, data kept; cached model invalidated | yes |
| id_refit_every_bars | 20 | 10–40 | refit scheduler | read each evaluation; no invalidation | yes |
| id_min_history_bars | 60 | 30–100 | "unknown" gate | each evaluation | yes |
| id_volatility_window / id_volatility_min_samples | 10 / 3 | 5–20 / 2–5 | feature construction | each evaluation; invalidates model | yes |
| id_hmm_iterations / id_hmm_tolerance | 20 / 1e-4 | 10–40 / 1e-6–1e-3 | `GaussianHMM.fit` | at each refit; invalidates model on change | yes |
| id_min_state_occupancy | 0.05 | 0.01–0.15 | `valid_state_mask_` | at each refit | yes |
| id_variance_ratio | 2.5 | 1.5–4.0 | distinct-stressed-state test | at each refit | yes |
| id_slippage_cap / id_slippage_gain | 0.2 / 2.0 | 0.15–0.3 / 1–3 | admission slippage estimate | each evaluation | yes |
| id_reward_floor / id_reward_gain / id_risk_floor / id_risk_gain | 0.05 / 4 / 0.1 / 2 | see registry | admission reward:risk | each evaluation | yes |
| id_variance_floor | 1e-8 | 1e-10–1e-6 | HMM numerical regularization | at each refit | **no (fixed)** |
| id_initial_variance_regularizer | 1e-6 | 1e-8–1e-4 | HMM init regularization | at each refit | **no (fixed)** |
| mpc_entry_price_gain | 0.001 | 0.0005–0.002 | plan entry price | each `build_plan` | yes |
| mpc_base_slippage_fraction | 0.0005 | 0.0003–0.001 | plan entry price and paper-broker fill (shared) | plan: each call; broker: run start (see §8) | **no (fixed)** |
| mpc_time_decay_gain | 0.5 | 0.25–0.75 | exit time tightness | each `update` | yes |
| mpc_shadow_r_gamma | 0.65 | 0.4–1.0 | shadow-only R reference | each shadow update | **no (fixed)** |
| mpc_schedule_kp/ki/kd_gain | 1.2 / 2.0 / 0.8 | see registry | tier-3 entry PID gain scheduling | each `build_plan` | yes |
| mpc_environment_lookback / mpc_range_atr_period / mpc_range_fallback_fraction | 50 / 14 / 0.01 | see registry | range-proxy volatility state | each bar | yes |
| mpc_atr_floor_gain | 0.005 | 0.0025–0.01 | tier-1 ATR floor (before fixed envelope) | each bar | yes |
| mpc_slippage_vol_gain | 0.5 | 0.25–1.0 | tier-1 `dynamic_slippage` (read only by the informational bridge) | each call | **no (fixed)** |

Why the five are non-optimizer (source proof, verified this session): none existed in the registry at bf091fd (literals only). `mpc_shadow_r_gamma` — shadow-only, documented at bf091fd as "fixed, disclosed and non-calibratable"; `mpc_slippage_vol_gain` — output has no plan/fill consumer; `id_variance_floor`, `id_initial_variance_regularizer` — numerical stability; `mpc_base_slippage_fraction` — changes real plan/fill prices, so this is a **judgment call**: fill cost is `slippage_cost_multiplier × base_fraction`, the multiplier is already calibratable, and giving an optimizer a second cost knob invites optimistic-cost drift. Mechanism: added to `FIXED_TARGET_NAMES`; still runtime-configurable via EffectiveConfig. Reversible if the owner disagrees.

Pre-existing controls read by BB06 and left owned where they were: `pid_kp/ki/kd_entry|exit`, `pid_integral_max_clamp`, `pid_integral_window_bars`, `trailing_stop_atr_mult`, `saturation_exit_bars`, `stop_loss_atr_mult`, `profit_target_atr_mult`, holds, `slippage_cost_multiplier` — no duplicate owner created.

## 3. Structural / fixed constants deliberately retained

| constant | location | class |
|---|---|---|
| 2 HMM states, calm/stressed labeling, diagonal covariance, crc32 per-symbol seed | regime_id_box.py / regime_hmm.py | STRUCTURAL_NOT_PARAMETER (constructor rejects ≠2) |
| `_ENTRY_TIMING_FLOOR = 0.3` | pid_controller.py | FIXED_SAFETY_ENVELOPE |
| `_EXIT_TIGHTNESS_FLOOR = 0.5` | pid_controller.py **and** continuous_exit_controller.py | FIXED_SAFETY_ENVELOPE (defined twice; identical, documented duplicate) |
| normalized-vol (0.5–2.5), ATR-floor (0.0025–0.012), slippage-scale (0.6–3.0), Kp/Ki/Kd scale clips | dynamic_parameter_controller.py | FIXED_SAFETY_ENVELOPE |
| `* 100` return scaling; `stressed` argmax-of-variance rule | regime_id_box.py | STRUCTURAL |

## 4. Stale / cached runtime propagation defects fixed

Each fixed with controller memory preserved (no per-bar rebuild):

1. Exit controller: kp/ki/kd, clamp, ATR-droop multiplier, baseline window, saturation bars were frozen at construction. New `configure(config)` (called each open-position bar) refreshes them; existing `PID` objects get new `tunings` and `output_limits`; baseline deque re-sized in place.
2. Exit PID output limits were set only at first PID construction; now refreshed every call.
3. Entry/exit PID `output_limits` in `SimplePIDModelPredictiveControlBox._get_pid` were only set at construction (tunings were refreshed, clamp was not); now refreshed.
4. `_confidence_history` deques kept the first window forever; now resized in place preserving data (both PID boxes).
5. HMM box: history length and refit/feature/model controls were module constants; now config-owned, `configure()` invalidates cached model/posterior only when a feature/model-shaping control changes (cadence/threshold/admission changes preserve the causal posterior). Failed validation leaves the previous config in force.
6. Tier-1/tier-3 helpers and `MarketEnvironmentState.from_bars` accept and validate the effective config; the orchestrator passes it.
7. Broker fill slippage base was a literal; now registry-backed.

## 5. Duplicate / dead-placeholder findings

- `bb05_bb06_parameters.py` (new, untracked → committed): thin adapter. `default_config()` derives from registry defaults and `require()` validates type/bounds; **no second defaults table**. Legitimate, not a second owner.
- Duplicate `_EXIT_TIGHTNESS_FLOOR` (see §3).
- **Pre-existing debt kept**: `pid_derivative_smoothing` is read in the external `build_plan` only for coverage bookkeeping; simple-pid does not consume it (listed in `INACTIVE_CALIBRATION_PARAMETERS`). Removing the read made `tests_external/test_orchestrator_end_to_end.py` fail, so it was restored unchanged from bf091fd. Not fixed here; recommend a separate change.
- `DynamicParameterController.get_tier2_regime_parameters` (regime→threshold/hold/cooldown tables) contains ~15 anonymous literals. It is called only by the informational R5 bridge `evaluate()`, never by the external orchestrator. **Not parameterized** (out of scope; informational). The bridge's legacy `evaluate()` also calls tier-1/tier-3 without a config, so it reports canonical defaults, not a run's effective values.
- Untracked, not touched: `0]`, `audit_constants.py`, `orchestrator.py.bak_clean`, `revision5/r5_plant_state.sqlite3`, `run_monday_48.py`, `tests/test_revision5_fec_plant_bridge.py`, `tests/test_revision5_orchestrator_integration.py`, `work/`.

## 6. Supervisory bridge

`revision5/supervisory_bridge.py` change **belongs to BB05/06**: `BB05BB06SupervisorySnapshot` (frozen dataclass, `authority="INFORMATION_ONLY"`) and `snapshot_bb05_bb06()`, which only copies fields of an existing `IDDecision`/`TradePlan` after finiteness checks, rejects a plan attached to a non-approved decision, and calls no plant, governor or execution function. The orchestrator stores it in `bb05_bb06_supervisory_by_symbol` (telemetry). Test: `test_bb05_bb06_snapshot_is_information_only`.

## 6b. Registry counts and identity

| | bf091fd | prior uncommitted work | final |
|---|---|---|---|
| target count | 85 | 114 | **114** |
| fixed (non-optimizer) targets | 22 | 22 | **27** |
| safety | 20 | 20 | **20** |
| calibratable | 63 | 92 | **87** |
| frozen identity | `42d9b0a6fa8f82b3fb060be21ca5aa71a43f88dc6f23738c8fbf889b3d854bf1` | `841d582337ba6727c755596fd2ff9272550e25ed4bc271ad9dd748390a4bea7c` | `f499047bfe77e8b12feb650555702c81d0a9fef971a572ead235f94470b83a8b` |

The optimizer surface widens by 24 (63 → 87) — an explicit, reviewed widening, all NOT_CALIBRATED. `trading_search_space()` currently yields 84 names. Older configs must take the new canonical defaults/identity.
Manifest note: `calibration_config.py::revision2_35()` adds names only (historical method name); values are owned by the registry.

## 7. Default-behavior equivalence

All 29 defaults equal the removed literals (`OLD_LITERALS` in the focused test, asserted against the registry). Default `build_plan` fill and default ID reward:risk are asserted against the pre-change formulas. Existing `tests_external` HMM/regime/PID/exit/shadow/end-to-end suites pass unchanged.

## 8. Remaining limitations

- Orchestrator config is built once at construction (`self.config`); "runtime propagation" means controllers now follow whatever `EffectiveConfig` the orchestrator holds each bar. The paper broker's `slippage_fraction` is still set once at construction from that config.
- `id_*`/`mpc_*` parameters appear in the shared registry, so the legacy in-house Revision-2 sensitivity tests (which sweep every calibratable ID/MPC/PA parameter against the *in-house* boxes) fail for external-only parameters — same mechanism as the BB04 PA debt (§9).
- Tier-2 literals and bridge `evaluate()` defaults (§5). `pid_derivative_smoothing` coverage-only read (§5).
- No calibration was run; no claim of profitability, production or live readiness.

## 9. Tests

Focused: `tests/test_revision5_bb05_bb06_parameterization.py` — 34 tests: canonical ownership/labels/bounds; counts/identity/search-space membership; helper validation (bounds, int type, bool, NaN); AST check for no duplicate literals in HMM sources; default preservation; every BB05 parameter reaching the real HMM constructor/consumer (spy) or changing local behavior (min history boundary, window, refit count, volatility features vs pandas, variance ratio boundary, admission slippage/reward:risk boundaries, HMM floors/regularizer/iterations); state/cache correctness (window resize retains data, invalidation only on model-shaping change, failed configure atomic); BB06 entry price gain, base slippage, tier-3 gains and config propagation, environment/tier-1 controls, envelope still clips; PID clamp/window follow config without losing controller identity; exit `configure` propagation; time-decay boundary; shadow gamma changes telemetry but not the live stop; bridge is information-only. No test is registry-presence-only.

Regression (`tests` + `tests_external`, excluding `tests/legacy_quarantine` and two files needing uninstalled `kiteconnect`/`arcticdb`, plus `blocks`/`ecs_runtime_v2` import errors — environmental): **500 passed, 8 failed.**

| failing test | class | evidence |
|---|---|---|
| test_revision2_calibration_supervisor::…test_learning_rate_excluded_from_trading_search_space | PRE-EXISTING AT bf091fd | fails in clean detached worktree |
| test_revision2_causal_sensitivity::…test_every_calibratable_parameter_changes_the_real_trade_ledger | PRE-EXISTING (failing set widens) | fails at bf091fd |
| test_revision2_optimizer::TestSearchSpace::…46_numeric… | PRE-EXISTING (list widens) | fails at bf091fd |
| test_revision2_pipeline::…test_full_parameter_coverage_on_real_data | PRE-EXISTING | fails at bf091fd |
| test_revision2_portfolio::…test_shared_equity_curve_not_independent_sums | PRE-EXISTING | fails at bf091fd |
| test_revision2_sensitivity::test_pa_parameters_are_sensitive | PRE-EXISTING (BB04 PA params) | fails at bf091fd |
| test_revision2_sensitivity::test_id_parameters_are_sensitive | **NEW REGRESSION** | passes at bf091fd; fails on `id_feature_window` — an external-HMM-only parameter swept against the in-house box |
| test_revision2_sensitivity::test_mpc_parameters_are_sensitive | **NEW REGRESSION** | passes at bf091fd; fails on `mpc_atr_floor_gain` — external-only, swept against in-house MPC |

The two new failures are structural: the shared registry labels external-engine parameters `ID`/`MPC`, and the legacy test sweeps all calibratable parameters of those labels against the in-house Revision-2 engine, which never reads them. Same mechanism as the accepted BB04 PA failure. **Not weakened or edited**; resolving them needs a decision (scope the legacy sweep to parameters the in-house engine consumes, or otherwise) that belongs to the owner. BB04 test edits were limited to surface totals (85→114, 63→87); its `== 16` assertion was preserved by correcting a note's wording.
