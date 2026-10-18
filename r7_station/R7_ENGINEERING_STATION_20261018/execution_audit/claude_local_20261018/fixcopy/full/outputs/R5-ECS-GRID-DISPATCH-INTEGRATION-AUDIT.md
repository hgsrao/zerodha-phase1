# R5 ECS / Grid / Dispatch integration audit

Branch: `codex/r5-plant-control-shadow` — base `89ee76615416b995c19a366e83fe7a918a70494a`
(`codex/r5-integration-bb01-bb10`, BB01–BB10 integrated, 612 passed / 0 failed).
Mode: **SHADOW only.** No calibration, no Optuna, no paper actuation, no live mode.

**Update (branch `codex/r5-paper-apply-blocker-closure`, from frozen checkpoint `425024c`):** both
PAPER_APPLY blockers recorded in section 10 are now **CLOSED**. See section 11 for the closure
record; sections 1–9 below are the original SHADOW-certification audit and are left as originally
written except where section 11 explicitly supersedes them (section 9's test counts and section
7's two closed-blocker bullets).

## 1. Chain and authority

```
Nifty / VIX (causal, as-of)
   -> PlantGridSynchronizer    -> immutable PlantGridState
   -> ECSPlantSupervisor       -> ECSPlantOutput (plant_demand_reference_pu 0..1)
   -> SectorDispatchController -> five bay references (uses DynamicBayLoadDispatcher merit weights)
   -> GovernorDispatchReference (typed, INFORMATION_ONLY, applied=False)
   -> GTG1 / GTG2 / CSTG1 / CSTG2 / BPSTG governors  (final local ENTRY/HOLD/EXIT authority)
   -> BB07 safety -> BB08 sizing -> BB09 order construction -> BB10 execution/reconciliation
```

| Layer | Authority | Explicitly cannot |
|---|---|---|
| PlantGridSynchronizer | plant-wide grid state from market data | select stocks, place orders |
| ECSPlantSupervisor | one-way bounded plant demand: HOLD, reduce, restore within bounds and rate | widen risk, override protection, choose a stock, place an order |
| SectorDispatchController | allocate ECS demand across the five bays | replace DynamicBayLoadDispatcher, exceed a bay ceiling silently, dispatch to a tripped bay |
| Governors | final local ENTRY/HOLD/EXIT (unchanged, not modified) | — |
| ClosedLoopSupervisor | **CLOSED-LOOP SUPPORTING SUBSYSTEM**; its portfolio derate is a one-way *input* to ECS | it is NOT the ECS Plant Supervisor |

`revision5/plant_control.py` has no broker, order, execution, gates, governor or 25A-synchronizer imports (AST-tested).

## 2. Components

* **`PlantGridSynchronizer`** — new, and it **composes** the existing grid implementations (see section 8): the causal
  data contract is `SealedGridContextProvider.causal_context` and the VIX band test is
  `MacroGridSynchronizer.check_frequency`. The per-stock synchronizer and `observe()` remain local diagnostics with
  unchanged behaviour. Plant states: `SYNCHRONIZED`, `DERATED`,
  `UNSYNCHRONIZED`, `ISLANDED_SAFE`. Frequency = India VIX band; stress = VIX level / VIX rate-of-rise / Nifty deviation
  from its EMA. Phase alignment is per-stock and deliberately not part of the plant state.
  * Causal: only bars with timestamp strictly earlier than the decision time (owned by `causal_context`); a bar stamped at
    the decision time is excluded.
  * Fail closed to `ISLANDED_SAFE` (no neutral Nifty/VIX ever synthesized): not-yet-available, timestamp mismatch,
    stale, warm-up insufficient. VIX outside the operating band -> `UNSYNCHRONIZED`. Reason codes match the existing
    `SealedGridContextProvider` (parity test). Naive decision times are read as Asia/Kolkata (engine convention).
* **`ECSPlantSupervisor`** — demand = grid target (1.0 / `ecs_derate_demand_pu` / 0.0) `min`-ed with the supporting
  derate (clipped to [0, 1], so it can only reduce) and exposure headroom (`1 - exposure/limit`, existing
  `max_gross_exposure_fraction`); plant protection trip or no available bay -> 0. Reductions are immediate; increases are
  rate-limited by `ecs_demand_restore_step_pu`. Output: `operating_mode` (NORMAL/DERATED/HOLD),
  `plant_demand_reference_pu`, `plant_derate` (= 1 - demand), `bay_availability_mask`, `reasons`, `plant_protection_connected` (False in SHADOW) and
  `bay_availability_source` (`SYMBOL_TRIPS_ONLY`). Startup state is HOLD (demand 0): the plant ramps up from zero.
* **`SectorDispatchController`** — reuses `DynamicBayLoadDispatcher.weights` (merit) and `.max_ceiling` (bay ceiling).
  Deterministic water-filling in `BAY_IDS` order. Invariants (property-tested over 300 random cases): unavailable bay = 0,
  no negative dispatch, no bay above its ceiling, references sum to demand when feasible; otherwise
  `feasible=False`, `unallocated_pu` and `INFEASIBLE_CAPACITY_DEMAND_EXCEEDS_AVAILABLE_BAY_CEILINGS` are reported and the
  ceiling is never violated.
* **Governor hand-off** — `GovernorDispatchReference` (frozen; `applied=False`, `INFORMATION_ONLY`) plus a
  `DispatchReferenceConsumer` protocol as the future PAPER_APPLY boundary. No governor code was modified and nothing
  consumes the references in SHADOW.
* **`PlantControlChain`** — orchestrates the above. `PlantControlMode` = `SHADOW` (default, only enabled value) and
  `PAPER_APPLY` (refused until separately approved). There is no live mode.

## 3. Orchestrator wiring (SHADOW)

`Revision2ExternalEngineOrchestrator(plant_control_mode="SHADOW")` evaluates the chain once per timestamp after the
mark-to-market update. Inputs: the grid provider's Nifty/VIX frames (empty frames when no provider -> `ISLANDED_SAFE`),
bay status from `symbol_tripped` (a bay is tripped only when it has universe symbols and all are tripped), exposure from
the existing helpers, and `ClosedLoopSupervisor.observe_portfolio_risk` as the supporting derate. Results are recorded on
state change only (`plant_control_snapshots`) with counts in `report["plant_control_shadow"]`. Only the chain's own
`PlantControlError` is contained and recorded; any other exception propagates. The chain never touches admission,
sizing, orders or the ledger: no-grid, synchronized and fully-blocking (UNSYNCHRONIZED, demand 0) runs produce an
**identical** trade ledger and net P&L (`test_shadow_mode_leaves_the_replay_trade_ledger_unchanged`).

## 4. Parameter ownership (audited before adding)

Existing values reused: bay ceiling and merit weights (`DynamicBayLoadDispatcher`), `max_gross_exposure_fraction`
(safety contract), `cl_portfolio_soft_budget_fraction`. Structural (not parameters): the [0, 1] demand range and the
sum/ceiling invariants. `grid_context.py`'s 20-minute staleness and 63-bar warm-up were constructor literals, not
registry-owned, so they are now registry parameters for the plant synchronizer.

Eleven new registry parameters, black box `PlantControl`, `ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED`, FIXED,
non-calibratable, EXTERNAL-only: `grid_vix_operating_min`, `grid_vix_operating_max`, `grid_max_staleness_seconds`
(also `NEVER_CALIBRATE_SAFETY`), `grid_vix_derate_start`, `grid_vix_slope_bars`, `grid_vix_slope_derate_fraction`,
`grid_nifty_ema_period`, `grid_nifty_deviation_derate_fraction`, `grid_min_aligned_bars`, `ecs_derate_demand_pu`,
`ecs_demand_restore_step_pu`. An AST test forbids any other numeric literal in the module.

## 5. Registry accounting

| Measure | Before (integration) | After |
|---|---|---|
| total targets | 141 | 152 |
| fixed targets | 33 | 44 |
| safety parameters | 22 | 22 |
| optimizer-eligible union | 108 | 108 |
| in-house surface | 46 | 46 |
| external surface | 108 | 108 |
| shared / in-house-only / external-only eligible | 46 / 0 / 62 | 46 / 0 / 62 |
| EXTERNAL-only names (fixed + eligible) | 72 | 83 |

Arithmetic: 152 = 44 + 108. Frozen identity `b00b5299815753477037c181c548545c10254f18869314b3e22852ba88bef360`
(previous `965184f2…`). `PlantControl` was added to `Revision2ParameterManifest.black_boxes()`.

## 6. Tests

`tests/test_revision5_plant_control.py` (see section 9 for the final count): causal handling, future-bar rejection, at-decision-bar exclusion,
stale/missing/warm-up/misaligned fail closed, parity with the existing grid context, synchronized/derated/unsynchronized
demand, ECS one-way bounds/rate limit/protection/exposure, tripped-bay zero, deterministic redistribution, ceilings and
sums (property test), infeasible capacity reported, no broker/order/execution API in ECS/dispatcher (AST + attribute
audit), governor unchanged and final, BB07 blocks a governor-approved admission, native 25A synchronizer separate,
`ClosedLoopSupervisor` is not `ECSPlantSupervisor`, SHADOW-only mode (PAPER_APPLY/live refused), registry ownership, and
the SHADOW ledger-unchanged replay test. Broad-suite results are in the accompanying report.

## 7. Known debt / not done

* PAPER_APPLY (consuming references in a governor or admission path) is intentionally not implemented.
* **[CLOSED by section 11, BLOCKER 1]** Bay availability can now be read from a real, attached
  `CentralPlantMasterDCS` (`revision5/protection_snapshot.py`). The replay engine still does not
  instantiate one itself by default: with no `real_plant_dcs` argument (the unchanged default),
  bay availability still comes from symbol trips only, and the report still states
  `plant_protection_connected=False`, `bay_availability_source=SYMBOL_TRIPS_ONLY` -- an explicit,
  never-faked fallback, not a claim of physical-plant protection integration.
* The plant demand does not yet gate admissions; that is the future, separately approved PAPER_APPLY step.
* **[CLOSED by section 11, BLOCKER 2]** `DynamicBayLoadDispatcher.register_trade()` is now fed the
  authoritative realized-R outcome of every external-replay close, exactly once per close, within
  one process/replay run. Durable cross-process receipt recovery is still not implemented (see
  section 11's explicit remaining limitations).
* Thresholds are engineering initial values and have not been calibrated or validated.

## 8. Pre-commit review

### 8A. The 11 parameters

All: black box `PlantControl`, target registry, `applicable_engines=EXTERNAL`, `calibratable=False`
(non-optimizer), `ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED` (three also `NEVER_CALIBRATE_SAFETY`).
Consumers are in `revision5/plant_control.py` (registered as consumed by `revision2_external/orchestrator.py`).

| name | default | bounds | type | doctrine class | calibration status | exact runtime consumer | basis of default |
|---|---|---|---|---|---|---|---|
| `grid_vix_operating_min` | 10.0 | 5.0–15.0 | float | FIXED_SAFETY_ENVELOPE | NEVER_CALIBRATE_SAFETY | `PlantGridSynchronizer.configure` -> `MacroGridSynchronizer(vix_operating_band)` -> `check_frequency` (l.130) | existing per-stock default (10) — an inherited **engineering assumption** |
| `grid_vix_operating_max` | 30.0 | 20.0–40.0 | float | FIXED_SAFETY_ENVELOPE | NEVER_CALIBRATE_SAFETY | same | existing per-stock default (30) — inherited **engineering assumption** |
| `grid_vix_derate_start` | 24.0 | 15.0–30.0 | float | ACTIVE_DYNAMIC (fixed for now) | NOT_CALIBRATED | `_measure` derate test (l.185) | **new engineering assumption** (about 80% of the band); no data supports it |
| `grid_vix_slope_bars` | 5 | 3–10 | int | ACTIVE_DYNAMIC | NOT_CALIBRATED | `_measure` (VIX rise lookback) | 5 grid bars (75 min); note `observe()` uses `iloc[-5]` = 4 intervals, this uses 5 intervals (documented semantic difference) |
| `grid_vix_slope_derate_fraction` | 0.10 | 0.05–0.25 | float | ACTIVE_DYNAMIC | NOT_CALIBRATED | `_measure` (l.185) | **new engineering assumption**: a 10% VIX rise in 75 minutes is a large move |
| `grid_nifty_ema_period` | 50 | 20–100 | int | ACTIVE_DYNAMIC | NOT_CALIBRATED | `_measure` EMA span; also sizes the minimum warm-up | equals the existing 50 used by `observe()`/`MacroGridSynchronizer` |
| `grid_nifty_deviation_derate_fraction` | 0.03 | 0.01–0.06 | float | ACTIVE_DYNAMIC | NOT_CALIBRATED | `_measure` (l.185) | **new engineering assumption**: |Nifty/EMA − 1| ≥ 3% is unusually stretched for 15-minute bars |
| `grid_max_staleness_seconds` | 1200 | 900–3600 | int | FIXED_SAFETY_ENVELOPE | NEVER_CALIBRATE_SAFETY | passed to `SealedGridContextProvider.causal_context` (l.160) | equals the provider's existing 20 min (one 15-minute bar + 5 minutes grace); parity test |
| `grid_min_aligned_bars` | 63 | 30–200 | int | ACTIVE_DYNAMIC | NOT_CALIBRATED | `causal_context` warm-up (raised to `max(., ema+1, slope+1)`) | equals the provider's existing 63; parity test |
| `ecs_derate_demand_pu` | 0.5 | 0.1–0.9 | float | ACTIVE_DYNAMIC | NOT_CALIBRATED | `ECSPlantSupervisor.evaluate` DERATED target (l.269) | **new engineering assumption**: halve demand under grid stress |
| `ecs_demand_restore_step_pu` | 0.10 | 0.02–0.5 | float | ACTIVE_DYNAMIC | NOT_CALIBRATED | `ECSPlantSupervisor.evaluate` restore limit (l.290) | **new engineering assumption**: full restoration takes 10 evaluations (about 10 minutes at 1-minute replay) |

STRUCTURAL (deliberately not parameters): the plant demand range [0, 1]; demand = 0 for UNSYNCHRONIZED / ISLANDED_SAFE
(fail-closed); the sum/ceiling/non-negativity invariants; the numerical tolerance `_EPS`. Dispatch has **no smoothing of
its own**: the merit smoothing (0.85/0.15), floor (0.08) and ceiling (0.35) are constructor literals inside the existing
`DynamicBayLoadDispatcher`, not registry-owned. They are pre-existing, out of scope here, and recorded as ownership debt;
the controller only reads `.weights` and `.max_ceiling`.

### 8B. Grid-logic reuse: was REIMPLEMENTS, corrected to COMPOSES

The first cut duplicated timestamp normalization, as-of selection, timestamp alignment, staleness, warm-up and the VIX
band test. Corrected:

| Concern | Single owner now |
|---|---|
| tz-aware timestamp / strictly-earlier selection / alignment / stale / warm-up | `SealedGridContextProvider.causal_context` (new, extracted from `observe`, which now calls it; behaviour and reason codes unchanged) |
| VIX operating band | `MacroGridSynchronizer.check_frequency` (new one-line method; `check_synchronization` calls it) |
| per-stock direction-aligned trend, phase alignment, 15° tolerance | `MacroGridSynchronizer` only; not used at plant level |
| naive-replay-timestamp rule | `PlantGridSynchronizer.decision_utc` (the only tz rule in the plant module) |
| plant aggregation: derate thresholds, Nifty deviation magnitude | `PlantGridSynchronizer._measure` (semantics that exist nowhere else) |

`observe()` is O(log n) + cached merge instead of an O(n) filter per call. Tests: composition spies, an AST/source check
that the plant module has no `to_datetime`/`searchsorted`/`.merge(`/VIX-band code, an `observe`-uses-`causal_context`
check, and provider-default/registry parity (staleness 1200, warm-up 63, band 10–30, EMA 50).

### 8C. Causality and time zones

Rule: a tz-aware decision time keeps its instant (converted to UTC); a tz-naive replay time is read as Asia/Kolkata
(no DST, so never ambiguous or non-existent). The data contract itself stays strict (naive input raises in
`causal_context`/`observe`). Bars are UTC instants selected with `timestamp < decision`. Tests: naive = IST wall clock;
aware instants unchanged across UTC/IST/New York/London/Sydney; identical state for the same instant in any zone; the
bar stamped at the decision instant excluded; a bar one second in the future excluded in every zone; the US DST
spring-forward instant (`2024-03-10 07:00Z`) with decisions at −1s/0/+1s expressed in New York time.

### 8D. ECS state equations

With `p` = previous demand (initial 0), `s` = `ecs_demand_restore_step_pu`:

```
grid_target = 1.0 (SYNCHRONIZED) | ecs_derate_demand_pu (DERATED) | 0 (UNSYNCHRONIZED, ISLANDED_SAFE)
if protection tripped or no bay available: grid_target = 0
target = min(grid_target, clip(supporting_derate, 0, 1), headroom)
demand = target                     if target <= p          # immediate derate / HOLD
demand = min(target, p + s)         if target >  p          # restoration
```

Restoration is monotonic non-decreasing while the target is at or above `p`, bounded by `target` (never above requested
demand, and `target` already carries a continuing grid/protection derate), and never above 1.0. HOLD is `demand == 0`.
Startup from zero (a change from the first cut, which started from "unknown" and could jump to full demand). The default
`s = 0.10` is an engineering assumption and stays NOT_CALIBRATED.

### 8E. Exposure headroom

`headroom = max(0, 1 − gross_exposure_fraction / max_gross_exposure_fraction)`; `target = min(target, headroom)`.
A `min` can only lower demand, so the ECS never widens the BB08/safety exposure envelope (`max_gross_exposure_fraction`
is read from the safety contract, not modified). The `ClosedLoopSupervisor` portfolio derate is also an exposure-derived
cap, but both enter through `min` (no product), so exposure is not double-counted inside the ECS. Nothing consumes the
ECS demand in SHADOW, so no trade is sized twice; in a future PAPER_APPLY the demand would only be an upper cap applied
before BB07/BB08 (tighter only).

### 8F. Bay availability

Symbol trips only, and recorded as such: `plant_protection_connected=False`, `bay_availability_source=SYMBOL_TRIPS_ONLY`
and reason `PLANT_PROTECTION_NOT_CONNECTED` on every ECS output; the same fields are in `report["plant_control_shadow"]`.
Acceptable SHADOW limitation because it is explicit; `CentralPlantMasterDCS` state is not faked. A connected
`plant_protection_tripped` (True/False) is already accepted by the ECS input.

### 8G. DynamicBayLoadDispatcher reuse

`SectorDispatchController` holds only `merit_source` (asserted: `vars(controller) == {"merit_source"}`), reads
`merit_source.weights` and `merit_source.max_ceiling` on every dispatch, and contains none of the merit algorithm (no
downside deviation, smoothing or floor; source-scanned). `register_trade()` is deliberately not called in SHADOW: it
mutates the dispatcher from realized R-multiples, and wiring performance feedback into the plant is a separate approval.
A test builds the controller first, feeds trades to the same dispatcher, and shows the dispatch changes without
rebuilding it, so the later wiring is one `register_trade(bay, R)` call at trade close.

### 8H. Governor hand-off

`GovernorDispatchReference(bay_id, dispatch_reference_pu, plant_demand_reference_pu, mode, applied=False,
authority="INFORMATION_ONLY")`, produced by `build_governor_references` inside `PlantControlChain.evaluate`. No
`BayTurbineClosedLoopGovernor` code changed, dispatch never calls `evaluate_entry_request`, and the module has no
broker/order/governor import (AST test). Proposed future PAPER_APPLY hand-off (not implemented): each bay governor
implements `DispatchReferenceConsumer.accept_dispatch_reference`, and the reference acts only as an additional upper cap
on that bay's new-entry admission, evaluated before BB07 and never overriding the governor's own ENTRY/HOLD/EXIT decision.

### 8I. Registry ownership of the three NEVER_CALIBRATE_SAFETY values

Deliberately target parameters, not `safety_params`. `safety_params` (22) are the immutable `SafetyContract` consumed
by the BB07 gates (its hash and count are checked by both engines); BB07's own additions (`max_broker_offline_seconds`,
`force_close_time`) went there because a gate reads them. The VIX band and staleness limit are consumed by the plant
synchronizer, a control layer above the gates, and follow the BB08 precedent (fixed target parameters). They are still
non-optimizer (fixed, refused by `validate_calibration_payload`) and labelled `NEVER_CALIBRATE_SAFETY`.

### 8J. Numeric-literal rule

The first rule allowed a bare set of numbers implicitly. It now encodes the doctrine: identities 0, 1, 2 are allowed;
any other numeric literal must be a named module-level STRUCTURAL constant, and the only such constant must be `_EPS`.
Booleans, strings and enum values are not numeric. It forces no mathematical identity into the registry.

## 9. Final results (observed after all review corrections)

* `tests/test_revision5_plant_control.py`: 55 tests, all passing (includes the SHADOW ledger-unchanged replay).
* Existing grid tests (`tests_external/test_grid_context.py`, `test_curve_synchronizer_shadow.py`): 10 passed, `observe()` behaviour unchanged.
* Broad suite (same exclusions as before: `tests/legacy_quarantine`, `test_broker_adapter_kite.py`,
  `test_data_loader_arctic.py`): **667 passed, 0 failed** (612 integration baseline + 55 plant-control tests).
* Registry: 152 targets / 44 fixed / 22 safety / 108 optimizer-eligible (46 shared + 62 external-only; in-house 46,
  external 108), identity `b00b5299815753477037c181c548545c10254f18869314b3e22852ba88bef360`.

## 10. PAPER_APPLY blockers -- CLOSED (see section 11)

PAPER_APPLY BLOCKER 1 -- CLOSED:
CentralPlantMasterDCS plant/bay protection state can now be connected to ECS bay availability through
an immutable `PlantProtectionSnapshot` (`revision5/protection_snapshot.py`). Not attached by default.

PAPER_APPLY BLOCKER 2 -- CLOSED:
DynamicBayLoadDispatcher.register_trade() is now wired to the authoritative external-replay
realized-R trade-close feedback, exactly once per close, separately from local governor feedback.

Both were originally documented and visible in the outputs (`plant_protection_connected=False`,
`bay_availability_source=SYMBOL_TRIPS_ONLY`, static initial merit weights), acceptable for the SHADOW
checkpoint because nothing consumed the plant references. Section 11 records how each was closed and
proven, and the remaining prerequisites before PAPER_APPLY can be approved.

## 11. PAPER_APPLY blocker closure (branch `codex/r5-paper-apply-blocker-closure`)

Base: frozen SHADOW checkpoint `425024cb644fcad9fa87fd9612d73c9c077d999e` (this branch's only ancestor
beyond that commit is the work described here). Still SHADOW-only: PAPER_APPLY remains refused at
`PlantControlChain` construction; no broker/order/live-trading code was touched or added.

### 11A. BLOCKER 1 -- real protection -> ECS bay availability

Architecture (read-only, exactly as specified):

```
real R5 plant (CentralPlantMasterDCS)
        |
build_plant_protection_snapshot()     -> immutable PlantProtectionSnapshot   (revision5/protection_snapshot.py)
        |
derive_bay_status_and_master_block()  -> (bay_status, plant_protection_tripped, source)
        |
ECSPlantSupervisor.evaluate()         -> bay availability mask / plant demand   (revision5/plant_control.py, unchanged core logic)
```

Source-audited authoritative state (existing, native R5; nothing re-implemented or re-evaluated):

| State | Owner | Read via |
|---|---|---|
| Master/MiCOM block | `CentralPlantMasterDCS.grid_relay` (`MasterGridProtectionMiCOM`), `CentralPlantMasterDCS.electrical_network` | `grid_relay.master_breaker_open`, `electrical_network.grid_connected` |
| Bay trip/lockout | `TurbineBayPanel.tripped_offline`, `PlantElectricalNetwork`'s `CircuitBreaker.lockout_86`/`unit_available()` | direct read, no re-evaluation |
| Cooldown | `TurbineBayPanel.cooldown_remaining(bar_index)` | called with the plant's OWN `current_bar_index` (0 before its first bar) -- an external replay bar/timestamp is never accepted, so external replay can never advance/expire/clear native cooldown |
| Startup/dispatch-ready, mechanical trip | `TurbineStartupSequencer.state` (`DISPATCH_READY`, `TRIPPED`) via `TurbineBayPanel.startup_sequencer` (optional; `None` -> both reported `Optional[bool]=None`, "unknown", never assumed healthy or tripped) | direct read |

`protection_snapshot.py` contains none of the protection logic that decided this state -- it is a
translation/read layer only, source-audited against `ccpp_unified_plant.py`, `ccpp_protection_cubicles.py`,
`machine_dynamics.py` and `startup_synchronization.py`. Per-bay reason priority (first match wins,
matching the real plant's own precedence in `TurbineBayPanel.evaluate_admission`/`run_synchronizing_control`):
`ANSI_86_LOCKOUT` > `UNIT_TRIPPED_OFFLINE` > `GEN_BREAKER_OPEN` > `BAY_COOLDOWN` >
`MECHANICAL_PROTECTION_TRIP` > `UNIT_NOT_DISPATCH_READY` > healthy (`available=True`).

**External replay fallback.** `Revision2ExternalEngineOrchestrator` gained one new, optional,
default-`None` constructor argument, `real_plant_dcs`. With no real plant supplied (the unchanged
default -- the external engine still does not instantiate a `CentralPlantMasterDCS`),
`build_plant_protection_snapshot(None)` returns the explicit fallback
(`connected=False, source="SYMBOL_TRIPS_ONLY"`), `derive_bay_status_and_master_block` passes the
existing `_plant_bay_status()` (symbol-trip) result through **unchanged**, and
`plant_protection_tripped=None` (never assumed healthy). Nothing about `CentralPlantMasterDCS` state
is fabricated. `ECSPlantOutput.plant_protection_connected`/`bay_availability_source` are now driven by
this real telemetry (`ECSPlantSupervisor.evaluate` and `PlantControlChain.evaluate` gained one new,
backward-compatible, default-preserving keyword argument, `bay_availability_source`) instead of a
hardcoded string, and the orchestrator's `report["plant_control_shadow"]` reads the same live fields
instead of a hardcoded `False`/`"SYMBOL_TRIPS_ONLY"`.

**Authority is structurally one-directional and never mixed.** A connected snapshot
(`snapshot.connected is True`) ignores the fallback bay status entirely; a disconnected snapshot
never reads real-plant state. This is what makes "a healthy real plant cannot override an existing
symbol-trip fallback" and "a false legacy trip flag cannot clear a real master block" true by
construction, not by an ad hoc precedence rule: the two inputs are never merged, so neither can ever
override the other's own path. ECS's own `plant_protection_tripped=True` handling (pre-existing,
unmodified) still forces plant demand to 0 and is still the sole place a master block acts; ECS
cannot clear a trip and creates no trade, unchanged from the original audit.

### 11B. BLOCKER 2 -- authoritative realized-R close -> `DynamicBayLoadDispatcher`

Source-audited: the native R5 close path (`CentralPlantMasterDCS.on_trade_closed`) already calls both
`bay.register_outcome()` (-> `BayTurbineClosedLoopGovernor.register_trade`) and
`self.dispatcher.register_trade(bay_id, pnl_r)` -- untouched, not disturbed. The external-replay close
path (`Revision2ExternalEngineOrchestrator._execute_exit`) did neither. The realized-R equation reused
verbatim (not reinvented) is `ContinuousExitController._r_multiple`: `signed_move / initial_risk` where
`initial_risk = abs(entry_price - initial_stop_price)` and `signed_move` is signed by trade side --
called as `self.exit_controller._r_multiple(state, filled_exit_price)`.

Wiring added, all inside `revision2_external/orchestrator.py`:

* `self._bay_governors: Dict[bay_id, BayTurbineClosedLoopGovernor]` -- five dedicated local-governor
  feedback objects (frozen specs, same `BAY_GOVERNOR_SPECS` as native R5), owned by the orchestrator,
  independent of any attached `real_plant_dcs`.
* `_execute_exit`, immediately before `del self.open_trades[symbol]` (after the authoritative fill
  `result["passed"]`, after `completed_trades`/closed-loop bookkeeping, i.e. after the trade is truly
  closed): computes `realized_r` via the equation above, resolves `bay_id = bay_for_symbol(symbol)`
  (the real topology function; a symbol outside the certified 48-symbol universe -- a synthetic
  test-only symbol -- is skipped exactly like the native plant's own `UNMAPPED_SYMBOL` path, never
  given an invented bay), and calls `_register_realized_r_close_feedback`.
* `_register_realized_r_close_feedback` feeds **both**, separately, once each:
  `self.plant_control.dispatch_controller.merit_source.register_trade(bay_id, realized_r)` (plant-level
  merit/dispatch feedback -- the SAME `DynamicBayLoadDispatcher` instance `SectorDispatchController`
  already reads) and `self._bay_governors[bay_id].register_trade(realized_r)` (local governor
  feedback). Neither replaces the other.

**Exactly-once receipt.** `self._close_feedback_receipts: Dict[key, "PENDING"|"DONE"]`, keyed by
`trade_id` (real trades always carry one) or, when absent (a minimal test fixture), by the `open_trades`
record's own object identity. State machine: absent -> `PENDING` -> `DONE`. A key already present
(`PENDING` or `DONE`) is a silent no-op -- never fed twice. If either `register_trade` call raises
after the key is marked `PENDING`, the receipt is left at `PENDING` forever (never advanced, never
removed) and the exception still propagates unmodified -- no broad exception swallowing anywhere in
this path. Scope, exactly as specified: one process/replay run only; no persistent restart recovery
was invented.

**Position-reconciliation precondition (new, source-proved necessary by the fixture failures below).**
`_verify_broker_position_reconciles`, called first inside `_execute_exit`, raises
`PositionReconciliationError` (never caught/swallowed by replay code) when the broker's actual
position for the symbol does not have the same sign and at least the same magnitude as the ledger's
`open_trades[symbol]` expects to close -- i.e. before any order is submitted, not after. This is what
makes "authoritative close" a real precondition for the close-feedback call, and it is what a genuine
retried/duplicate `_execute_exit` call hits (the position is already flat after the first, real close),
so a duplicate external close is rejected before it could ever reach the dispatcher a second time.

### 11C. Governor feedback vs dispatcher feedback

`self._bay_governors[bay_id]` (`BayTurbineClosedLoopGovernor.register_trade`) and
`self.plant_control.dispatch_controller.merit_source` (`DynamicBayLoadDispatcher.register_trade`) are
two distinct, independently-constructed objects (asserted directly in tests: `governor is not
dispatcher`). Both receive the identical `realized_r` value from the same close, each exactly once;
neither call can substitute for the other, and neither is rebuilt afterward
(`test_dispatcher_merit_weights_change_causally_via_real_closes_without_rebuilding_controller`).

### 11D. Fixture corrections

Three pre-existing external-engine test fixtures constructed `open_trades[symbol]` directly without a
matching broker position. The new `_verify_broker_position_reconciles` precondition correctly rejects
the resulting fake exit (it would open/flip a position, not close one) -- source-proved to be the
correct, not-to-be-weakened behaviour, not a bug in the new check:

* `tests_external/test_trade_ledger_holding_time.py::test_completed_trade_ledger_includes_causal_holding_time`
* `tests_external/test_orchestrator_end_to_end.py::test_exit_stop_ratchet_armed_from_a_close_is_not_retroactively_checked_inside_that_bar`
* `tests_external/test_orchestrator_end_to_end.py::test_regime_stressed_exit_fires_once_minimum_hold_is_met`

Each now also seeds `orch.broker.positions[symbol] = {"quantity": ..., "avg_price": ...}` matching the
ledger's `open_trades[symbol]`, consistent with every other fixture in the suite (e.g.
`tests_external/test_audit_remediation.py`'s `open_position()` helper, which already submits a real
`broker.place_order` entry and was never broken). A fourth pre-existing test
(`tests_external/test_audit_remediation.py::test_s4_simultaneous_candidates_compete_as_batch_not_symbol_order`)
uses synthetic, non-topology symbols (`"AAA"`, `"BBB"`) and required no fixture change -- it is exactly
the case the `bay_for_symbol` `KeyError` guard (11B) exists to skip cleanly.

### 11E. Focused tests

* `tests/test_r5_paper_apply_blocker_closure.py` (new, 20 tests): requirements 1-19 from the closure
  brief, one-to-one (two tests cover requirement 2's lockout and mechanical-trip halves separately).
* `tests/test_revision5_plant_control.py`: 55 tests, still passing unchanged (backward-compatible
  additive changes only), including the real-data SHADOW-ledger-unchanged replay.
* `tests_external/test_trade_ledger_holding_time.py`, `tests_external/test_orchestrator_end_to_end.py`,
  `tests_external/test_audit_remediation.py`, `tests_external/test_compact_replay_telemetry.py`:
  62 tests, all passing (fixture corrections above; includes the synthetic-symbol test).

### 11F. Broad regression and `git diff --check`

`pytest tests/ tests_external/ -q --ignore=tests/legacy_quarantine
--ignore=tests_external/test_broker_adapter_kite.py --ignore=tests_external/test_data_loader_arctic.py`
(same exclusions as section 9, no new exclusions added): **687 passed, 0 failed** in 483s
(612 pre-existing baseline + 55 pre-existing plant-control tests + 20 new blocker-closure tests).
`git diff --check`: clean (no whitespace errors).

### 11G. Remaining prerequisites before PAPER_APPLY (unchanged from before this closure)

* External replay without an attached native R5 plant still uses the explicit `SYMBOL_TRIPS_ONLY`
  fallback (attaching `real_plant_dcs` is available but not the default; the replay engine still does
  not instantiate a `CentralPlantMasterDCS` itself).
* Exactly-once dispatch/governor close feedback is process/replay-run scoped only; durable
  cross-process receipt/state recovery across a restart is not implemented.
* `PAPER_APPLY` remains refused at `PlantControlChain` construction; nothing here consumes a
  `GovernorDispatchReference` or gates an admission.
* No live broker actuation is enabled anywhere in this branch.
* No calibration/Optuna/profitability tuning was performed; all new logic is either structural
  (bounded floats/enums) or read-only telemetry.
