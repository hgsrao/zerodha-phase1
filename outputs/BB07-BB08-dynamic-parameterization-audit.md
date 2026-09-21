# BB07 / BB08 Dynamic-Parameterization Audit (Revision 5)

Branch `codex/r5-bb07-bb08-manual`, base `bf091fda11cb9be02f7536059dc204f7ec9600af`.
Scope: BB07 = SafetyGates, BB08 = PositionManager / PyPortfolioOpt sizing.
BB09/BB10 untouched. No Optuna / calibration / P&L tuning / live trading.

This document records engineering parameterization and behavioral verification
only. It makes no claim of profitability, live readiness or production
certification.

## 1. Proven identities (from current source)

| | BB07 | BB08 |
|---|---|---|
| Authoritative class | `SafetyGates` family: `Gate01..Gate18`, assembled by `EntryDecisionEngine` | `PyPortfolioOptPositionManagerBox` + `compute_portfolio_weights()` |
| Module | `gates_framework.py` | `revision2_external/position_sizing_pyportfolioopt.py` |
| Constructor | `EntryDecisionEngine(config=SafetyGateConfig)` (`orchestrator.py:201`) | `PyPortfolioOptPositionManagerBox()` (stateless apart from telemetry) |
| Caller | `Revision2ExternalEngineOrchestrator.__init__` builds config via `_build_safety_gate_config()` | orchestrator `run()` loop: weight refit; per-entry `size()` |
| Inputs | `SafetyContract.values` (frozen, from `registry.safety_params`) | `EffectiveConfig` (registry `params` + overrides), causal minute-bar closes, ATR plan |
| Outputs | `GateDecision` per gate; `Gate17` action (`ALLOW_ENTRY`/`NO_ENTRY`/`FORCE_CLOSE`) | `{symbol: weight}`; integer quantity + sizing telemetry |
| Retained state | `SafetyGateConfig` on engine and each gate | `orch._portfolio_weights`; `last_sizing_telemetry` |
| Downstream consumer | orchestrator entry admission; `_maybe_exit` force-close | order construction → execution → broker |
| Authority type | safety permission / protection | sizing / allocation (risk **derater** only) |

Distinctions preserved: strategy authority (PA/ID/MPC) is untouched; BB07 only
permits/blocks/force-closes; BB08 only sizes and can only *reduce* risk. Neither
became a second trading strategy.

## 2. Caller → consumer paths

### BB07
```
CanonicalParameterRegistry.safety_params  (owner, FIXED_SAFETY_ENVELOPE)
 → SafetyContract.from_registry()         (frozen MappingProxy)
 → Orchestrator._build_safety_gate_config()   orchestrator.py:277-278
 → SafetyGateConfig.max_broker_offline_seconds / force_close_time
 → Gate18CircuitBreaker.evaluate   (`offline_seconds >  limit` and not connected → trip)
 → Gate17MarketClose.evaluate      (`now >= force_close` → FORCE_CLOSE)
 → Orchestrator._maybe_exit        (`HH:MM >= entry_decision_engine.config.force_close_time` → exit "force_close_time")
```

### BB08
```
registry.params  (owner PositionManager, fixed, NOT_CALIBRATED)
 → EffectiveConfig
 → orchestrator.run():  portfolio_weight_refit_bars, _lookback_minute_bars,
                        _min_15min_observations, _optimizer_risk_free_rate
     → compute_portfolio_weights(price_history, min_observations=…, risk_free_rate=…)
         → EfficientFrontier(mu, cov, weight_bounds=(0,1)).max_sharpe(risk_free_rate=…)
 → PyPortfolioOptPositionManagerBox.size():  portfolio_aggressive_scale
     ATR base budget → × conviction_derate (≤1.0) → lot round → safety notional cap (authoritative)
```

## 3. Parameters

### BB07 (safety envelope; not calibratable)
| Parameter | Default | Classification | Calibration status | Owner | Real consumer |
|---|---|---|---|---|---|
| `max_broker_offline_seconds` | 300 | FIXED_SAFETY_ENVELOPE | NEVER_CALIBRATE_SAFETY | `registry.safety_params` (SafetyGates) | `Gate18CircuitBreaker` |
| `force_close_time` | "15:25" | FIXED_SAFETY_ENVELOPE | NEVER_CALIBRATE_SAFETY | `registry.safety_params` (SafetyGates) | `Gate17MarketClose`, orchestrator `_maybe_exit` |

### BB08 (explicit runtime parameters, outside the search surface)
| Parameter | Default | Bounds | Classification | Calibration status | Real consumer |
|---|---|---|---|---|---|
| `portfolio_weight_refit_bars` | 500 | 60 .. 2000 | ACTIVE_DYNAMIC | NOT_CALIBRATED (`ENGINEERING_INITIAL_VALUE`) | orchestrator refit counter |
| `portfolio_weight_lookback_minute_bars` | 2000 | 500 .. 10000 | ACTIVE_DYNAMIC | NOT_CALIBRATED | orchestrator window `.tail()` and completeness check |
| `portfolio_min_15min_observations` | 100 | 30 .. 500 | ACTIVE_DYNAMIC | NOT_CALIBRATED | `compute_portfolio_weights` fallback threshold |
| `portfolio_aggressive_scale` | 1.5 | 1.0 .. 2.0 | ACTIVE_DYNAMIC | NOT_CALIBRATED | `PyPortfolioOptPositionManagerBox.size` (aggressive mode only) |
| `portfolio_optimizer_risk_free_rate` | 0.0 | -0.05 .. 0.20 | ACTIVE_DYNAMIC | NOT_CALIBRATED | `EfficientFrontier.max_sharpe(risk_free_rate=…)` |

Operational classification and calibration status are recorded separately. None
of the seven is calibratable, none is on the Optuna search surface, and
`validate_calibration_payload` rejects overrides of the five BB08 values.

## 4. Structural constants retained (STRUCTURAL_NOT_PARAMETER)
`TRADING_DAYS_PER_YEAR=252`, `PORTFOLIO_15MIN_PERIODS_PER_DAY=25`,
`INTRADAY_15MIN_PERIODS_PER_YEAR=252*25`, `PORTFOLIO_RESAMPLE_RULE="15min"`,
`MIN_PORTFOLIO_ASSETS=2`, `PYPORTFOLIOOPT_WEIGHT_BOUNDS=(0.0,1.0)`,
`PYPORTFOLIOOPT_CLEAN_CUTOFF=0.0001`, `PYPORTFOLIOOPT_CLEAN_ROUNDING=5`,
`MAX_CONVICTION_DERATE=1.0`. These are mathematical identities, library API
semantics or design invariants (e.g. `MAX_CONVICTION_DERATE=1.0` *is* the
one-way "never amplify" rule) and were deliberately not parameterized.

## 5. Duplicate / dead ownership findings

| Finding | Before | After |
|---|---|---|
| Refit cadence / lookback module constants in `orchestrator.py` | `PORTFOLIO_WEIGHT_REFIT_EVERY_BARS=500`, `PORTFOLIO_WEIGHT_LOOKBACK_MINUTE_BARS=2000` | removed; registry owns, `config.require()` consumes |
| `MIN_15MIN_PRICE_OBSERVATIONS=100` in sizing module | module constant | removed; explicit keyword `min_observations` |
| Hardcoded `1.5` aggressive scale in `PyPortfolioOptPositionManagerBox.size` | literal | `portfolio_aggressive_scale` via `req()` |
| `max_broker_offline_seconds`, `force_close_time` in `gates_framework.SafetyGateConfig` | silent dataclass defaults; external orchestrator left them unwired | canonical registry owns; external orchestrator wires them into `SafetyGateConfig` |
| `EfficientFrontier` weight bounds / `max_sharpe` / `clean_weights` args implicit | library defaults | explicit and asserted in tests |
| Hidden default arguments on `compute_portfolio_weights` | positional defaults would recreate second owner | `min_observations`, `risk_free_rate` are required keyword-only (test asserts `TypeError`) |

Residual, intentional: `SafetyGateConfig` dataclass defaults (300, "15:25") in
`gates_framework.py` still exist as the framework's standalone default (used by
`test_gates_framework.py` and any caller constructing a bare config). They
equal the canonical values (asserted by test) and the external orchestrator now
overrides them from the registry; they are not read by that path. No dead registry entries were found: each of the seven has a real
consumer proven by a behavioral test. No parameter consumption was faked for
coverage; the four BB08 orchestrator-side values are added to
`consumed_parameters` only after `config.require()` actually returns them, and
the aggressive scale is recorded through the sizing box's own `ParameterUse`
trace.

## 6. Behavioral evidence
`tests/test_revision5_bb07_bb08_parameterization.py` (20 tests):
- `max_broker_offline_seconds`: contract → `SafetyGateConfig` → Gate18; passes at 299 s and at exactly 300 s (source comparison is strict `>`), trips at 301 s, never trips while connected; overriding the contract to 10 s moves the trip point.
- `force_close_time`: contract → config → Gate17 (15:24 `NO_ENTRY`, 15:25 and 15:26 `FORCE_CLOSE`); orchestrator `_maybe_exit` force-closes at the configured time and not one minute earlier.
- `portfolio_weight_refit_bars`: real `run()` with a spy on `compute_portfolio_weights`; R=60 vs R=120 change the invocation count and the invocations are exactly R bars apart.
- `portfolio_weight_lookback_minute_bars`: histories supplied have exactly N bars, equal the trailing N causal bars, and contain nothing after the tick timestamp.
- `portfolio_min_15min_observations`: forwarded value asserted in the run; boundary test: requiring `n+1` completed observations when `n` exist → equal-weight and optimizer never invoked; requiring exactly `n` → optimizer executes.
- `portfolio_optimizer_risk_free_rate`: value reaches `EfficientFrontier.max_sharpe(risk_free_rate=…)` (0.0, 0.0625, −0.02); forwarded by the orchestrator.
- `portfolio_aggressive_scale`: no effect in `equal` mode; linear effect on base risk budget in `aggressive` mode; scale 1.0 equals equal sizing.
- One-way derating: an optimizer weight above equal never raises quantity or budget; below equal derates; zero weight → zero quantity.
- Final safety exposure cap remains authoritative at scale 2.0 (raw quantity exceeds cap; final equals cap).
- Default equivalence, single ownership, non-calibratable status, registry counts/identity, absence from `trading_search_space`, structural constants retained.

## 7. Registry contract (unchanged where required)
| | Before | After |
|---|---|---|
| Target parameters | 85 | **90** |
| Safety parameters | 20 | **22** |
| Fixed target | 22 | **27** |
| Calibratable | 63 | **63** (unchanged) |
| Frozen identity | `42d9b0a6…854bf1` | `7f3616f8b948e821caa2ceb715f8a71b065e4e1db713c6b0f3623bb6136779ff` |

`hardcoded_20()` / `calibratable_45()` keep their historical names although they
now return 22 / 63 values; renamed nowhere for cosmetics.

## 8. Default-behavior equivalence
refit 500 bars, lookback 2000, min observations 100, aggressive scale 1.5,
risk-free 0.0, offline 300 s, forced close 15:25 — identical to the values
previously hardcoded (asserted by `test_default_behavior_equivalence_and_single_owner`).
The explicit PyPortfolioOpt arguments equal the library defaults of the installed
version (pypfopt 1.6.0: `weight_bounds=(0,1)`, `max_sharpe(risk_free_rate=0.0)`,
`clean_weights(cutoff=0.0001, rounding=5)`), so making them explicit does not change
optimizer output. They are pinned so a library-default change cannot silently alter
sizing.

## 9. Regression results
| Group | Result |
|---|---|
| Focused `tests/test_revision5_bb07_bb08_parameterization.py` | **20 passed** (20 collected) |
| 12-test contract group | 12 passed |
| SafetyGate (`test_gates_framework.py`) | 19 passed |
| Position sizing (`test_position_sizing_pyportfolioopt.py`, `test_external_sizing_calibration.py`) | 10 passed |
| External orchestrator (`test_orchestrator_end_to_end.py`, `test_audit_remediation.py`) | 60 passed |

Final broad run (`tests`, `tests_external`, `test_gates_framework.py`, excluding
`tests/legacy_quarantine`, `test_broker_adapter_kite.py`, `test_data_loader_arctic.py`),
observed after the BB04 count fix: **6 failed, 501 passed**. The six failures are
exactly the pre-existing set below; no new failures.

An earlier broad run (before the fix) had 7 failures; the seventh,
`test_revision5_bb04_dynamic_parameterization.py::test_registry_identity_metadata_and_bounds`,
asserted `len(registry.params) == 85` and was invalidated only by the intentional
5-parameter BB08 target-surface expansion; the assertion is now `== 90`.

Pre-existing failures (all fail at pristine `bf091fd`; none modified):
- Verified by the user (4): `test_pa_parameters_are_sensitive`, `test_shared_equity_curve_not_independent_sums`, `test_full_parameter_coverage_on_real_data`, `test_every_calibratable_parameter_changes_the_real_trade_ledger`.
- Reproduced in this work at pristine `bf091fd` (2): `test_learning_rate_excluded_from_trading_search_space` (expects 47 calibratable, actual 63) and `test_space_covers_the_46_numeric_calibratable_parameters` (expects 46, actual 62); stale BB04-era counts.

- **Not collected (environment):** `tests_external/test_broker_adapter_kite.py`, `tests_external/test_data_loader_arctic.py` (no `arcticdb`), and 3 modules under `tests/legacy_quarantine` (import errors).

## 10. Remaining limitations
- L1. The five BB08 values are engineering initial values, not calibrated; their bounds are design bounds, not evidence-derived.
- L2. Equivalence of the explicit optimizer arguments to library defaults was verified against pypfopt 1.6.0 only; a different installed version could have different defaults, which is exactly why the values are now explicit.
- L3. The Optuna search surface still excludes all seven; whether any should be calibrated is a separate decision.
- L4. `SafetyGateConfig` dataclass defaults remain as standalone framework defaults (see §5).
- L5. Four legacy Revision-2 tests fail identically at baseline `bf091fd` (see §9); they are not addressed here.
- L6. Collection-time environment gaps (no `arcticdb`, broken `tests/legacy_quarantine` imports) prevent some tests from being collected in this environment.
- L6b. Out of scope, deliberately not changed: the legacy in-house engine (`revision2/boxes.py`, `revision2/orchestrator.py`) still hardcodes an aggressive scale of 1.5 and leaves `max_broker_offline_seconds` / `force_close_time` at `gates_framework` defaults. These are a residual duplicate-ownership finding in a legacy sibling engine (values equal the canonical defaults); the external engine is the authoritative BB07/BB08 path.
- L7. No profitability, live-readiness or production-certification claim is made.
