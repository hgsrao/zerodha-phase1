# R5 controller parameter authority (Engines A, B and C)

Branch `feature/r5-parameter-authority` (local only, not pushed), worktree `~/projects/zerodha-r5-parameter-authority`,
started from cb10fac (current Engine C; not the stale protocol parent c0b4672).
Final commit, registry identity and protocol hash: see [Results](#results).

| Deliverable | Where |
|---|---|
| 1-2. Branch, commit | below / `PROTOCOL_RESEAL_MANIFEST.json` |
| 3-4. Changed files, unified diffs | `CHANGED_FILES.txt`, `patches/*.patch` (one per file, against cb10fac) |
| 5. Controller inventory | `controller_inventory.json` / `.md` (26 controllers, lines at cb10fac) |
| 6-7. Ownership matrix, registry additions | `PARAMETER_OWNERSHIP_MATRIX.csv` / `.md`, `REGISTRY_ADDITIONS.json` |
| 8. Code changes per replaced value | `patches/` + the former-literal column of the ownership matrix + [Phase 3](#phase-3--hardcoded-owners-replaced) |
| 9-13. Governor, V3, AVR, dispatcher, HRSG | sections below |
| 14. Closure register | `superseding/` (originals untouched) |
| 15. Protocol hash | `PROTOCOL_RESEAL_MANIFEST.json`, `revision5/step5_sealed_calibration_protocol_v4_parameter_authority.json` |
| 16. Sensitivity results | `TEST_RESULTS.json`, [Phase 9](#phase-9--sensitivity-tests) |
| 17. Engine A/B/C real-data results | `PHASE10_THREE_ENGINE_REPORT.json` / `.md` |
| 18. Remaining hardcoded values | `REMAINING_HARDCODED_VALUES.md` |
| 19. Incomplete items | [below](#incomplete-items) |
| 20. Behaviour changes requiring approval | `BEHAVIOUR_CHANGES_REQUIRING_APPROVAL.md` |
| Phase 0 baseline | `PHASE0_BASELINE_MANIFEST.json` |

## Design

* **One owner.** `revision5/controller_parameters.py` holds the table of every native R5 controller value
  (291 rows: name, type, default, bounds, safety class, calibration status, owner, consumer). The canonical
  registry registers every row as an EXTERNAL-only target parameter and puts the full metadata in its identity
  payload (`controller_authority`), so the registry identity changes whenever any value, bound or classification
  changes. Defaults are the former literals, value for value.
* **One read path.** `R5ControllerParameters.from_config(EffectiveConfig)` reads every name with
  `EffectiveConfig.require` (no `get`, no fallback), checks type and bounds, validates cross-parameter
  relationships (e.g. capital weights sum to 1, EMA α + β = 1, n·floor ≤ 1 ≤ n·ceiling, AVR voltage reference
  strictly inside the ANSI 27/59 corridor, every clip min ≤ max) and returns immutable groups.
* **Explicit injection.** The orchestrator builds the image once per run and passes it to the bay governors, the
  sector-dispatch merit source and the attached native plant (`CentralPlantMasterDCS.apply_controller_parameters`),
  which forwards it to the governors, AVRs, SEL-300G relays, MiCOM relay, dispatcher, HRSG, dynamic schedule and
  cooldown controllers. Each component's `configure`/`reconfigure` is **state-preserving**: R-history, integrators,
  derivative memory, inner loops, excitation command, relay counters, dispatcher outcomes and weights, drum pressure
  and correlation history are kept; integrators and windows are re-clamped to the new limits.
* **Startup report / runtime telemetry.** `orchestrator.controller_parameter_startup_report()` (every effective value,
  their hash, the non-default set, registry identity, modes) is written by all three runners into
  `report["r5_authority"]`. With `authority_telemetry=True` the run records the applied values (per-value
  min / max / last every bar, the full set at every session open) and dispatcher / AVR / HRSG events.
* **Classes.** 163 TUNABLE (optimizer-eligible in principle; NOT_CALIBRATED), 67 FIXED_SAFETY (protection envelopes,
  never optimizer-eligible), 12 ADVISORY, 15 INACTIVE. Static vs dynamic: `phase2_parameter_classification.md` — see `REMAINING_HARDCODED_VALUES.md`.

## Phase 3 — hardcoded owners replaced

| Former literal (cb10fac) | Now | State on refresh |
|---|---|---|
| `BAY_GOVERNOR_SPECS` table and `BayGovernorSpec` defaults (`governor.py:47-125`), dynamic_z `-0.25` (`:921`) | `gov_<bay>_*` (17 per bay) incl. `feedback_gain`; `BAY_GOVERNOR_SPECS` is now the registry-default view | `BayTurbineClosedLoopGovernor.reconfigure`: spec replaced; history, integrators, inner loops kept; runtime values follow the spec only before the first dynamic profile |
| Governor schedule coefficients and clips (`dynamic_parameters.py:613-758`) | `gsch_*` (38) | stateless schedule |
| Stress index / risk derate / vol clip (`:348`, `:380-394`) | `dyn_*` (8) | stateless |
| `LOSS_COOLDOWN_BARS`, `TARGET_COOLDOWN_BARS`, cooldown schedule, `consecutive_stops >= 2` | `plant_*cooldown*`, `plant_bay_lockout_consecutive_stops` | latches kept |
| `BayExcitationAVR.__init__` runtime literals, plant 15 000 / 0.40, `AVRRuntimeProfile` | `avr_*` (45) | `BayExcitationAVR.configure`: PID memory and excitation kept, re-clamped |
| SEL-300G envelopes and schedule; MiCOM envelopes, schedule, 81O `1.5×` | `sel_*` (15), `micom_*` (10) | trip counters / breaker latch kept |
| Box 6 Tier-1 / Tier-3 envelopes (`dynamic_parameter_controller.py:13-18`) | `tier1_*` (6), `tier3_*` (6) | stateless |
| `DynamicBayLoadDispatcher` literals | `disp_*` (12) | outcome history (trimmed to the window) and weights kept |
| `HeatRecoverySteamGenerator` defaults | `hrsg_*` (8) | drum pressure and snapshot history kept |
| Orchestrator `BAY_LOSS_COOLDOWN_BARS = 15`, symbol trip `>= 2` | `symbol_loss_cooldown_bars`, `symbol_trip_consecutive_losses` | latches kept |
| (new) Protocol V3 inner loop | `gov_v3_*` (9) | per-position inner state |

## Governor outer loop (deliverable 9)

Each bay's governor is built from its own configured `BayGovernorSpec` (per-bay names, no collapsing of distinct
values). `register_trade` runs the outer PID on the realized-R moving average with the runtime (scheduled) Kp/Ki/Kd,
target, window and integral clamp; `dynamic_z = base_z + clip(−feedback_gain·u, offset_min, offset_max) − droop`,
with the scheduled droop terms. The schedule (`Revision5DynamicParameterController(parameters)`) uses the registry
`gsch_*`/`dyn_*` coefficients on each bay's own configured base. Integrals are bounded by the runtime clamp and
re-clamped on every profile and refresh. Derivative: per-trade first difference of the outer error (no smoothing);
inner loop per-bar first difference (V3: zero on the first sample and on the activation sample). Safety precedence is
unchanged: BB07 safety gates → plant protection (ANSI 86, SEL-300G, MiCOM, HRSG collapse, AVR trip) → governor →
advisors; a governor EXIT is armed for the next open and cannot override a stop/target gap or a protection trip.

## Governor V3 (deliverable 10)

Reviewed a695913 and the three branches named in the brief (`research/r5-step0-correctness`,
`feature/protocol-v3-hardened-closed-loop`, `diagnostic/entry-and-loss-decomposition`). The only later engine changes
to the governor files (bf2c07e minimum-hold precedence) are already in cb10fac. Ported: `PositionControlV3` (frozen,
validated: κ ∈ (0, 1), 0 < γ_fast ≤ γ_slow ≤ 1, 0 < min_gap ≤ base_gap, grace ≥ 0), the V3 inner-loop branch of
`evaluate_position_control`, the `position_control` pass-through in `governor_authority.position_decision`, and the
orchestrator switch `governor_position_mode="legacy"|"v3"` (V3 requires full authority and is exclusive with the
offline `GovernorPositionPolicy`). **Not ported:** the `TradeReferencePath.curve_gamma` hunk (cb10fac already applies
γ once) and the protocol / bridge tooling (out of scope). The 9 `gov_v3_*` values keep a695913's names, defaults,
bounds and fixed/eligible split.

## AVR voltage wiring (deliverable 11)

Legacy (`avr_voltage_mode="legacy"`, default): unchanged — `evaluate_control` runs per call with the default 1.0 pu
inputs, so the error is 0 (documented defect; the lot size is still live).

Measured (`avr_voltage_mode="measured"`): once per bar, before any admission or position decision,
`CentralPlantMasterDCS.regulate_bay_voltages` → `TurbineBayPanel.regulate_terminal_voltage`:

```
V_bus  = 1 + nifty_deviation         grid synchronizer's causal Nifty-vs-EMA measurement for THIS timestamp
                                     (bars strictly before the decision time; None -> invalid)
I_bay  = bound notional / AVR capital  positions bound to the bay (fill price x quantity) / the bay's AVR-rated capital
V_t    = V_bus - X_s * I_bay           X_s = avr_synchronous_reactance_pu (0.05)
V_meas = V_meas + alpha * (V_t - V_meas)   alpha = avr_voltage_filter_alpha (1.0 = unfiltered)
error  = V_ref - V_meas                V_ref = avr_voltage_reference_pu (1.0)
PID    -> excitation command (UEL/OEL clamp); ANSI 27/59 trip on V_meas
```

No future bar and no unavailable data are used: V_bus is the same as-of grid value the governor droop already uses,
and I_bay is the plant's own bound-position state at the start of the bar. A missing / non-finite bus voltage or
invalid loading **fails closed**: the bay's result is `tripped` (`AVR_VOLTAGE_MEASUREMENT_INVALID`), so admissions are
rejected and open positions exit as an `AVR_TRIP` plant trip; a decision without this bar's regulation is
`AVR_VOLTAGE_NOT_MEASURED_THIS_BAR`. Each regulation records reference, bus and terminal voltage (raw and filtered),
loading, error, P/I/D terms, command before, requested and after limits. Actuator: the excitation command multiplies
the AVR lot notional → plant quantity → order quantity (proved in Engines A, B, C).

## Dispatcher (deliverable 12)

`DynamicBayLoadDispatcher` takes its 12 `disp_*` values from the image. Legacy (default) keeps the original order
(merit → clamp → normalise → EMA → normalise, which can exceed the ceiling). `dispatcher_allocation_mode="bounded"`:
raw merit → normalise → EMA (α·w + β·target) → deterministic water-filling projection onto {floor ≤ w ≤ ceiling,
Σw = 1} → HRSG balance → bay effective allocation; the sector dispatch caps each bay reference at the same ceiling.
`last_update` records raw merit, downside risk, previous / new EMA, raw allocation, floor / ceiling clamp flags,
normalised and final allocation and the bounds check; with `authority_telemetry` each update becomes a
`DISPATCHER_UPDATE` event with the trade quantity, and every admission records the dispatcher weight, allocation and
bay effective allocation next to the plant quantity.

## HRSG and plant dynamics (deliverable 13)

`HeatRecoverySteamGenerator(parameters=...)`: thermal lag (`hrsg_pressure_time_constant_bars`), collapse threshold and
pressure ceiling (FIXED_SAFETY), recovery fraction, correlation threshold / window / samples and the minimum penalty
(FIXED_SAFETY). The drum is stepped every PAPER_APPLY bar; low pressure derates steam-bay capital and a collapse exits
steam-bay positions. `hrsg_correlation_mode="causal_bay_returns"` makes the correlation damper live. Dispatch-rate
limiting is the existing `ecs_demand_restore_step_pu`; dynamic stress limits are `dyn_*`.

## Phase 9 — sensitivity tests

* `tests/test_r5_parameter_authority.py`: registry metadata and identity, no-fallback read path, bounds and
  relationships, startup report from EffectiveConfig, **per-parameter sweep** (one test per plant parameter: a
  TUNABLE / FIXED_SAFETY change must move an actuator in the deterministic plant scenario
  `tests/r5_authority_harness.py`; ADVISORY only the advisory output; INACTIVE nothing), governor Kp / Ki / Kd /
  target / window / offsets / droop, each V3 parameter, AVR Kp / Ki / Kd and limits, measured voltage reaching the
  order quantity and failing closed, dispatcher EMA / merit / floor / ceiling and the bounded projection reaching bay
  capital, HRSG lag and collapse, studies-PID gains changing study weights, Box 6 gains changing stop / target /
  reference, state preserved on refresh (and an identical refresh is a no-op), state reset between positions /
  symbols / sessions, and the literal register.
* `tests/test_r5_parameter_authority_engines.py` (Engine A, synthetic GTG2 bay): registry changes move orders
  (base_z, AVR loading, amplified outer-loop feedback); governor Kp and Kd move orders through the outer loop;
  Box 6 entry Ki / Kd and exit Ki move orders; the measured AVR changes order sizes; authority telemetry changes no
  order; the measured-AVR voltage path is recorded; the symbol latches; **causality** with every new mode on.
* `tests_engine_b/test_engine_b_parameter_authority.py`, `tests_engine_c/test_engine_c_parameter_authority.py`:
  the same proofs through Nautilus / Backtrader orders, and the documented entry-PID finding.

## Results

* Engine commit and protocol: `PROTOCOL_RESEAL_MANIFEST.json` (written by `scripts/r5_reseal_protocol.py`).
* Registry identity `a4bdda2d7df63f838be49fc7e1ad08525e768eaedfec2af537c6abd764371f46` (supersedes `12b700d6...`):
  427 targets (170 + 257 controller rows), 285 optimizer-eligible (in-house 46, external 285), 142 fixed, 22 safety (`REGISTRY_ADDITIONS.json`).
* Tests: `TEST_RESULTS.json` (JUnit totals, failed tests, parameter-sweep counts, golden gates).
* Real data: `PHASE10_THREE_ENGINE_REPORT.md` (default modes) and `PHASE10_THREE_ENGINE_REPORT_ALL_OPT_IN_MODES.md`
  (every opt-in behaviour change on). All 48 frozen symbols, last 1,200 bars, real NIFTY/VIX grid, full authority,
  full telemetry, final exit authority: 11 trades per engine; Engine B 54,790 and Engine C 55,953 reconciliation
  checks with 0 breaks; all three engines report the same controller-parameter value hash.

## Findings (pre-existing, documented and pinned by tests)

1. **Engine A plans on the next bar's open** (its documented execution model): the plan levels and plant sizing of
   a decision at bar t use the open of bar t+1. Engines B and C plan on the decision bar's close.
2. **Box 6 entry PID has no actuator in Engines B and C by default**: they re-anchor stop/target at the venue fill
   with the planned distances. Fixed once in the shared core (`anchor_plan_at_fill`, opt-in
   `entry_fill_anchor_mode="box6_levels"`).
3. **Legacy AVR PID is ineffective** (zero error) and its excitation only ratchets down; fixed by the opt-in
   measured mode.
4. **Legacy dispatcher can exceed its ceiling**; fixed by the opt-in bounded mode.
5. **SEL-300G `check_in_flight_protection` (ANSI 87G / 32R) had no runtime caller** (now live with the opt-in
   `unit_protection_mode="pre_sync_and_in_flight"`); **the AVR MVAR limiter cannot engage** (measured MVAR is always 0); **`BayGovernorSpec.z_exit` has no consumer**; the dynamic snapshot's
   strategy / safety / dispatcher-ceiling outputs are consumed only with `dynamic_profile_mode="consume"` (see
   [Dynamization](#dynamization)); the dispatcher-floor output has no actuator, and the order-timeout schedule can tighten only through its max clip.
6. **The stored golden master is not byte-reproducible on this machine** (HMM last-digit float drift at untouched
   cb10fac); decisions are identical.

## Dynamization

Every value declared dynamic is recomputed every native-plant bar (`plant.step_bar` → `Revision5DynamicParameterController.evaluate`)
from the causal `R5EnvironmentState` of completed bars, and its runtime consumer reads the value of that bar.
Proofs (independently re-implemented formulas, registry default coefficients): `tests/test_r5_dynamization.py`.

| Dynamic value | Formula source | Cadence | Consumer | Proof |
|---|---|---|---|---|
| Governor target_r, window, integral clamp, droop gain/max, Kp/Ki/Kd, base_z, droop_r, offset min/max (per bay) | `dynamic_parameters.py` governor schedule, `gsch_*` | every bar | `dynamic_z`, outer PID, Box 6 gain scale | `test_governor_runtime_values_*`, `test_governor_consumer_*`, `test_r5_gain_scale_reaches_the_box6_pid_tunings_every_bar` |
| AVR loading/OEL/vol-scalar/z-strength, PID gains, clamp, 27/59 trips, excitation limits | AVR profile, `avr_*` | every bar | `calculate_lot_size`, measured AVR loop | `test_avr_runtime_values_and_lot_sizing_*` |
| SEL-300G stale/spread/surge/slippage/adverse; MiCOM drawdown/crash/vol-z | SEL / MiCOM profiles, `sel_*`, `micom_*` | every bar | pre-sync / in-flight / grid relays | `test_protection_and_cooldown_limits_*` |
| Loss / target cooldown bars | plant profile, `plant_*_cooldown_*` | every bar | `register_outcome` | same |
| Entry confidence, min R:R, min/max hold | strategy profile, `psch_*` | every bar | Box 4, Box 5, Box 6 plan (consume mode) | `test_runtime_profiles_reach_their_consumers_each_bar` |
| Drawdown halt, daily-loss multiplier, concurrent positions, gross / symbol exposure, slippage, data age | safety / execution profiles, `psch_*` | every bar | entry gates (tighten-only), sizing, drawdown exit (consume mode) | same + gate actuation |
| Dispatcher max ceiling | plant profile, `psch_dispatch_ceiling_min`, `disp_allocation_ceiling` | every bar | `SectorDispatchController.dispatch` via `dispatch_ceiling` (consume mode) | same |

Missing / invalid environment fails closed (ValueError); warm-up is the neutral prior; earlier snapshots cannot
change with later data; schedule refresh keeps controller state; checkpoint / resume is identical in consume mode.

## Incomplete items

1. **Phase 10 at the required scope is not done.** The programme requires the complete July 2023 – August 2026
   history (48 symbols × ~291,000 bars ≈ 14 million symbol-bars per engine). Measured on this machine:
   57,600 symbol-bars take 24–30 minutes per engine (≈ 0.026 s per symbol-bar) and 2.3 GB peak RSS with full
   telemetry, so the full history would take about 4 days per engine and, with `telemetry_mode="full"` keeping every
   event in memory, on the order of hundreds of GB of RAM (this machine has 30 GB). What was run instead, clearly
   labelled: the last 1,200 bars of all 48 symbols for A, B and C, with default modes and with every opt-in mode.
2. **No calibration** has been run (by instruction: not before the reseal passes); 163 parameters are TUNABLE but
   NOT_CALIBRATED; the sealed search space is unchanged.
3. Governor outer-loop **Ki** and the **V3** inner loop move orders in the plant-level and governor-level proofs and
   in the all-modes real-data run (V3 `stop` exits), but not in the short synthetic Engine A end-to-end run (too few
   closes to accumulate integral; V3 floor not reached before the target / FSR exit).
4. Engine A/B/C full-history runs launched by the operator before this programme (from the engine-c worktree):
   Engine A was still running (7+ GB RSS); Engines B and C ended without writing a report.
