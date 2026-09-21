# BB09/BB10 dynamic parameterization audit

Repository: /home/shrinivas/blade_complete_backup/ECS_Project_BB09_BB10_REVIEW
Branch: codex/r5-bb09-bb10-review
Starting checkpoint: bf091fda11cb9be02f7536059dc204f7ec9600af
Reviewed checkpoint: d049c8fb5d3f8c6a2ec9312b3e878515c5e7efce (`Parameterize R5 BB09-BB10 controls`)
Review corrections: this revision (see "Review corrections" at the end).

Scope: BB09 and BB10 only. BB01-BB08 are unmodified. No BB05-BB08 work from
other branches was merged. The R5 governor/AVR/HRSG/protection architecture
(revision5/governor.py, revision5/hrsg.py, revision5/ccpp_unified_plant.py,
revision5/ccpp_protection_cubicles.py, revision5/machine_dynamics.py,
revision5/machine_archetypes.py) is untouched. No Optuna/Ray calibration
was run, no P&L was tuned, no live trading was run. Passing tests establish
correctness of wiring, not profitability or live readiness.

## Proven identity

The instructions warned not to trust prior reports about BB09/BB10 and to
prove their identity from current source. The proof:

`revision2_external/orchestrator.py` lines 1-19 (module docstring) is the
authoritative pipeline numbering. It enumerates all 10 boxes of the shared
portfolio pipeline and states explicitly:

```
9. P01D                  -> unchanged, in-house    (revision2.boxes.P01DBox; ...)
10. UnifiedExecution      -> kiteconnect+tenacity  (revision2_external.broker_adapter_kite; ...)
```

This is corroborated independently by:
- `revision2_external/broker_adapter_kite.py` line 1: `"""Box 10 (UnifiedExecution) -- real kiteconnect + tenacity broker adapter.`
- The commit history on this branch's ancestors follows the same numbering
  for the boxes already done: "Parameterize R5 BB01-BB02 controls" and
  "Expose R5 BB03-BB04 supervisory snapshots" match items 1-2
  (StartupCapabilityLock, DataIngestion) and 3-4 (L2DataCertifier,
  PredictiveAnalytics) in the identical numbered list above.

**BB09 = P01D.** Authoritative construction site:
`revision2_external/orchestrator.py:198` — `self.p01d = P01DBox()`, imported
from `revision2.boxes` (`revision2_external/orchestrator.py:42`). The only
runtime call site is `revision2_external/orchestrator.py:1240` —
`order, trace = self.p01d.create_order(symbol, plan, quantity, self.config)`.
`P01DBox.create_order` (`revision2/boxes.py:640-673`) turns an
already-approved `TradePlan` + sized `quantity` into a `ProposedOrder`
(order_type, limit_price, timeout_seconds, max_retries). It is also used,
unmodified, by the in-house `revision2/orchestrator.py` and
`revision2/portfolio_orchestrator.py` engines (same class, same registry).

**BB10 = UnifiedExecution.** This box's real actuator is
`revision2_external/broker_adapter_kite.py:KiteConnectBrokerAdapter`
(explicitly self-labelled "Box 10"), gated behind live trading (not wired
into calibration, per that file's own docstring and `runtime/operating_mode.py`'s
`OperatingMode.LIVE` fail-closed gate). During calibration/paper/backtest
runs — the only mode this branch is permitted to run — its role is played
by `revision2_external/paper_execution.py:CostedPaperBrokerAdapter`
(constructed at `revision2_external/orchestrator.py:199`, called at
`orchestrator.py:1321`) plus the registry-owned trading-window/dedup/
reconciliation surface consumed directly inside
`Revision2ExternalEngineOrchestrator` (`_in_trading_window`,
`ReplayIntentLedger`) and inside `gates_framework.py`'s Gate13-17 (BB07,
untouched, read-only dependency). The in-house engines instead use
`revision2/boxes.py:UnifiedExecutionBox.check_window` (constructed at
`revision2/portfolio_orchestrator.py:121` and `revision2/orchestrator.py:83`).

## Box identity table

| box | authoritative module/class/function | constructor | runtime caller | inputs | outputs | retained state | downstream consumer | authority type |
|---|---|---|---|---|---|---|---|---|
| BB09 (P01D) | `revision2.boxes.P01DBox.create_order` | `revision2_external/orchestrator.py:198` `self.p01d = P01DBox()` (stateless; also constructed at `revision2/portfolio_orchestrator.py:?`, `revision2/orchestrator.py:?`) | `revision2_external/orchestrator.py:1240` | `symbol, TradePlan (side/entry/stop/target/hold bars), quantity, EffectiveConfig` | `ProposedOrder(symbol, side, quantity, order_type, limit_price, timeout_seconds, max_retries)` or `None` if `quantity<=0`; `List[ParameterUse]` trace | none (stateless box) | `gate2 = ExecutionGate().validate_pre_submit(...)`, `self.broker.place_order(...)` (both keyed off `order.side`/`order.order_type` only) | EXECUTION (box); snapshot INFORMATION_ONLY |
| BB10 (UnifiedExecution) | Live: `revision2_external.broker_adapter_kite.KiteConnectBrokerAdapter` (not wired into calibration). Calibration/paper: `revision2_external.paper_execution.CostedPaperBrokerAdapter` + `ReplayIntentLedger` + `Revision2ExternalEngineOrchestrator._in_trading_window` + `gates_framework.py` Gate13/14/15/16/17 (BB07, read-only) | `revision2_external/orchestrator.py:199` (`self.broker = CostedPaperBrokerAdapter(...)`), `:204` (`self._intent_ledger = ReplayIntentLedger(...)`) | `revision2_external/orchestrator.py:1321` (`self.broker.place_order`), `:906` (`self._in_trading_window`), `:1261` (`self._intent_ledger.seen_recent`) | `symbol, side, quantity, order_type, market_price, config, parameter_registry`; dedup keyed on `(symbol, side, timestamp)` | fill dict (`passed`, `filled_quantity`, `filled_price`, ...) or rejection (`passed=False`, `reasons`) | `self.orders`, `self.positions`, `self.realized_pnl`, `self.fills` (broker instance state); `self._submitted` (ledger instance state) | `EntryDecisionEngine.evaluate_post_fill` (BB07 reconciliation gate), trade ledger / open_trades bookkeeping | EXECUTION (adapter); snapshot INFORMATION_ONLY |

BB09/BB10 create and submit the mechanical order only. Side, entry/stop/
target, and whether to trade at all are frozen upstream by BB04 (PA)/BB05
(ID)/BB06 (MPC)/BB07 (SafetyGates) before either box runs — confirmed by
reading `create_order` and `place_order`: neither reads a signal, a
confidence score, or a regime input, and neither can flip a rejected trade
into an accepted one. No strategic ENTRY/HOLD/EXIT authority was found or
added.

## Parameter inventory and classification

All 16 BB09/BB10-owned canonical-registry entries were enumerated
programmatically (`CanonicalParameterRegistry().params`/`.safety_params`
filtered by `black_box in ("P01D", "UnifiedExecution")`) and each one's
actual runtime consumer(s) traced by source. Every one of them was already
present at the starting checkpoint — the registry surface count, the
frozen identity hash, and the calibratable/fixed-target partition are
**unchanged by this branch**.

| box | source | current default / bounds | semantic purpose | classification | canonical owner | duplicate owner found | actual runtime consumer | calibration status | notes |
|---|---|---|---|---|---|---|---|---|---|
| BB09 | `order_type` | `"MARKET"`, fixed | Order routing type | FIXED_SAFETY_ENVELOPE | registry (`FIXED_TARGET_NAMES`) | none | `P01DBox.create_order` -> `ProposedOrder.order_type`; **also** hard-enforced at `Revision2ExternalEngineOrchestrator.__init__` (`orchestrator.py:163-164`), which raises `ValueError` at startup if it is not `"MARKET"` | non-calibratable (verified: `validate_calibration_payload({"order_type":"LIMIT"})` returns errors) | Structural double-lock: registry-fixed AND startup-fail-closed. `test_non_market_order_type_is_rejected_at_startup_fail_closed` proves the startup guard independently of registry validation. |
| BB09 | `limit_order_offset_percent` | 0.02, [0.00, 0.05] | LIMIT-order price offset from plan entry | FIXED_SAFETY_ENVELOPE (dead in this pipeline) | registry (`FIXED_TARGET_NAMES`); `orchestrator.INACTIVE_CALIBRATION_PARAMETERS` | none | `P01DBox.create_order` -> `ProposedOrder.limit_price` only when `order_type=="LIMIT"`, which the external engine's own startup guard forbids; `CostedPaperBrokerAdapter.place_order` additionally rejects any non-MARKET order outright | non-calibratable | Genuinely reaches `ProposedOrder.limit_price` (proven by `test_limit_order_offset_percent_changes_the_computed_limit_price` called directly against `P01DBox`), but has no live actuator in the external engine because order_type is pinned to MARKET. Already correctly flagged inactive before this branch; left unchanged. |
| BB09 | `order_timeout_seconds` | 30, [5, 120] (registry bounds, not a search range) | Fixed execution-timeout policy: broker acknowledgement timeout | FIXED_SAFETY_ENVELOPE, replay-inert | registry (`FIXED_TARGET_NAMES`) | none | `P01DBox.create_order` -> `ProposedOrder.timeout_seconds` (read by nothing downstream); and `orchestrator.py` `_build_safety_gate_config`: `min(order_timeout_seconds_execution, order_timeout_seconds)` -> `SafetyGateConfig.order_timeout_seconds` -> `Gate14OrderTimeout` | non-calibratable, non-optimizer (verified: it is in `FIXED_TARGET_NAMES`, therefore excluded from `APPROVED_CALIBRATABLE`; absent from `trading_search_space()`, `validate_calibration_payload` refuses it) | In the current replay/paper path Gate14 is only ever given `elapsed_seconds = 0.0` (`gates_framework.evaluate_pre_submit`, and `CostedPaperBrokerAdapter` records `ack_elapsed_seconds = 0.0`), so the elapsed-time timeout behaviour is never exercised and changing the fixed value cannot change any replay result. Gate14's own logic is unit-tested separately (`test_gate14_logic_rejects_elapsed_beyond_its_configured_timeout`); the runtime inertness is pinned by `test_replay_wiring_never_reaches_the_order_timeout`. An earlier revision of this document called it ACTIVE_DYNAMIC / calibratable in [5, 120] / APPROVED_CALIBRATABLE; that was wrong. |
| BB09 | `max_retry_attempts` | 2, [0, 5] | Retry attempts on order timeout/reject | FIXED_SAFETY_ENVELOPE (dead in this pipeline) | registry (`FIXED_TARGET_NAMES`); `orchestrator.INACTIVE_CALIBRATION_PARAMETERS` | none | `P01DBox.create_order` -> `ProposedOrder.max_retries`, never read by any downstream code (`CostedPaperBrokerAdapter` does not retry) | non-calibratable | Reaches the order object (proven) but the field is never consumed further; already correctly flagged inactive before this branch. |
| BB09 | `retry_delay_seconds` | 5, [1, 20] | Delay between retries | FIXED_SAFETY_ENVELOPE (dead) | registry (`FIXED_TARGET_NAMES`); `orchestrator.INACTIVE_CALIBRATION_PARAMETERS` | none | none — read via `req()` inside `create_order` for parameter-coverage tracking only, then discarded; no `ProposedOrder` field exists for it | non-calibratable | **Fixed this branch**: the trace entry previously mislabeled its `output_field` as `"max_retries"` (colliding with `max_retry_attempts`'s own trace). Now labeled `"unused"` with a comment explaining why (no external replay actuator). No behavior change; `test_retry_delay_and_slippage_tolerance_are_read_but_do_not_alter_the_order` proves the value cannot change the constructed order. |
| BB09 | `slippage_tolerance_percent` | 0.15, [0.02, 0.20] | Nominally "Gate16 slippage tolerance" per its own registry note | FIXED_SAFETY_ENVELOPE (dead / misleadingly documented) | registry (`FIXED_TARGET_NAMES`) | **`max_slippage_fraction`** (also P01D-owned, fixed, default 0.001) is the value Gate16 actually enforces | none — read via `req()` inside `create_order`, computed into a local variable, discarded | non-calibratable | **Duplicate-ownership finding, documented but not rewired** (see "Duplicate ownership found" below). `test_gate16_slippage_is_governed_by_the_fixed_max_slippage_fraction_not_the_p01d_calibratable_value` and `test_orchestrator_wires_gate16_from_max_slippage_fraction_not_slippage_tolerance_percent` pin the real wiring so a future change can't silently swap it. Trace `output_field` fixed from `"order_type"` (wrong) to `"unused"`. |
| Safety (P01D) | `order_dedup_window_seconds` | 5, fixed | Duplicate-order suppression window | FIXED_SAFETY_ENVELOPE | registry (`safety_params`) | none | `orchestrator.py:204` -> `ReplayIntentLedger(window_seconds=...)` -> `Gate13OrderDuplication` (BB07) via `seen_recent` | non-calibratable (safety surface) | Correctly wired before this branch; confirmed with `test_order_dedup_window_seconds_changes_duplicate_detection`. |
| Safety (P01D) | `order_timeout_seconds_execution` | 30, fixed | Absolute floor on the effective order timeout | FIXED_SAFETY_ENVELOPE | registry (`safety_params`) | none | `orchestrator.py:281-282` (the `min()` floor described above) | non-calibratable | Correctly wired; confirmed with `test_gate14_timeout_is_the_tighter_of_the_calibratable_and_fixed_values`. |
| Safety (P01D) | `max_reconciliation_qty_diff` | 0, fixed | Fill-vs-expected quantity reconciliation tolerance | FIXED_SAFETY_ENVELOPE | registry (`safety_params`) | none | `orchestrator.py:283` -> `SafetyGateConfig.max_reconciliation_qty_diff` -> `Gate15OrderReconciliation` (BB07) | non-calibratable | Correctly wired before this branch; not modified. |
| Safety (P01D) | `max_slippage_fraction` | 0.001, fixed | Real Gate16 slippage enforcement threshold | FIXED_SAFETY_ENVELOPE | registry (`safety_params`) | see `slippage_tolerance_percent` above | `orchestrator.py:272` -> `SafetyGateConfig.slippage_tolerance_percent` -> `Gate16Slippage` (BB07) | non-calibratable | This is the real, sole enforcement point for order slippage. Not modified; its canonical-owner status is now pinned by a regression test. |
| BB10 | `trading_hours_start` | `"09:15"`, fixed | Session open bound | FIXED_SAFETY_ENVELOPE | registry (`FIXED_TARGET_NAMES`) | none | `orchestrator.py:1474` (`_in_trading_window`); also `UnifiedExecutionBox.check_window` for the in-house engines | non-calibratable | Genuinely load-bearing (gates `_in_trading_window`'s boolean); proven with `test_orchestrator_in_trading_window_is_load_bearing` and `test_unified_execution_box_trading_window_is_parameter_sensitive`. |
| BB10 | `trading_hours_end` | `"15:30"`, fixed | Session close bound | FIXED_SAFETY_ENVELOPE | registry (`FIXED_TARGET_NAMES`) | none | same as above; also `orchestrator.py:893` (`end_time`, last-bar-of-session detection) | non-calibratable | Same evidence as `trading_hours_start`. |
| BB10 | `phase1_exploration_intensity` | 50, fixed | Diagnostic optimizer-intensity input to `UnifiedExecutionBox.check_window`'s `exploration_bias` output | FIXED_SAFETY_ENVELOPE (real but non-trading effect) | registry (`FIXED_TARGET_NAMES`) | none | `revision2/boxes.py:UnifiedExecutionBox.check_window` (in-house engines only; **not** called by `revision2_external/orchestrator.py`) | non-calibratable | Pre-existing, correctly classified; not touched. Documented here because it is genuinely BB10-owned. |
| BB10 | `phase2_optimization_intensity` | 250, fixed | Same `exploration_bias` computation | FIXED_SAFETY_ENVELOPE | registry (`FIXED_TARGET_NAMES`) | none | same as above | non-calibratable | Same as above. |
| BB10 | `learning_rate_exploration_factor` | 0.05, [0.01, 0.10] | Same `exploration_bias` computation (diagnostic; every caller discards the value) | **Superseded**: FIXED (diagnostic-only) per frozen checkpoint `b5ff1c26505dee6caa1d1e2ff34b2e14f7b88fc4` | registry; this branch does not modify it | none | `UnifiedExecutionBox.check_window` (in-house engines only) | At this branch's base it is registry-calibratable but excluded from `trading_search_space()`. The earlier ACTIVE_DYNAMIC/calibratable description here is superseded by the three-controller reclassification to FIXED with IN_HOUSE applicability; reconcile when the branches are integrated. Not changed here. |
| Safety (UnifiedExecution) | `no_entry_cutoff_time` | `"15:20"`, fixed | Forced no-new-entry cutoff | FIXED_SAFETY_ENVELOPE | registry (`safety_params`) | none | `orchestrator.py:960` (loop-level no-entry check), `SafetyGateConfig.no_entry_cutoff_time` -> `Gate17MarketClose` (BB07, not currently invoked from the pre-submit gate chain in this engine but present and consistent) | non-calibratable | Correctly wired; not modified. |

### Structural constant found and deliberately left untouched

`gates_framework.py:88` — `SafetyGateConfig.force_close_time: str = "15:25"` is
a **forced square-off time**, hardcoded as a Python dataclass default inside
`gates_framework.py` (BB07, off-limits per the assignment). It is consumed
at `revision2_external/orchestrator.py:511-512` to force-exit open positions.
It is not a canonical-registry parameter at all today. Because parameterizing
it would require editing `gates_framework.py` (a BB07 file this assignment
explicitly forbids touching) or building a second, competing configuration
path into a safety box, it was **left exactly as found** — documented here as
a genuine `STRUCTURAL_NOT_PARAMETER`-adjacent finding that is out of scope
for a BB09/BB10-only remediation, not silently worked around.

## Duplicate ownership found (documented, not rewired)

Two registry entries both nominally describe "slippage tolerance":
- `slippage_tolerance_percent` (P01D, calibratable=False, default 0.15,
  bounds [0.02, 0.20]) — its own registry note claims it is "Intraday
  Gate16 slippage tolerance," but it is fetched-and-discarded inside
  `P01DBox.create_order` and never reaches `SafetyGateConfig`.
- `max_slippage_fraction` (P01D, calibratable=False, default 0.001) — the
  value `orchestrator.py:272` actually feeds into `SafetyGateConfig.slippage_tolerance_percent`,
  which `Gate16Slippage` (BB07) enforces.

Both were **already non-calibratable** at the starting checkpoint (verified
programmatically, not assumed from a label), so this is not an Optuna-surface
defect and rule 3 ("fixed safety/execution constraints must remain
non-calibratable") is not at risk either way. Rewiring `Gate16Slippage` to
read `slippage_tolerance_percent` instead would touch `gates_framework.py`
(BB07, off-limits) and would not change any default behavior (the two
defaults, 0.15 vs 0.001, are 150x apart — swapping them would be a real
behavior change to a safety gate, which rule 2 forbids doing here). The
bounded, in-scope fix taken instead:
1. Corrected the misleading in-code comment and `ParameterUse.output_field`
   label inside `P01DBox.create_order` (`revision2/boxes.py`) so the trace
   no longer implies enforcement happens where it doesn't.
2. Added two regression tests
   (`test_gate16_slippage_is_governed_by_the_fixed_max_slippage_fraction_not_the_p01d_calibratable_value`,
   `test_orchestrator_wires_gate16_from_max_slippage_fraction_not_slippage_tolerance_percent`)
   that pin the real wiring, so any future accidental swap between the two
   parameters fails CI instead of silently changing safety behavior.

No other duplicate ownership was found among the 16 BB09/BB10-owned
registry entries.

## Dead / stale trace-label defects fixed (revision2/boxes.py, P01DBox only)

`P01DBox.create_order`'s `ParameterUse` trace entries for `retry_delay_seconds`
and `slippage_tolerance_percent` carried wrong `output_field` values
(`"max_retries"` and `"order_type"` respectively — both copy/paste
collisions with unrelated fields). `output_field` is pure audit metadata
(verified: no code branches on it anywhere in the repository); the fix
relabels both `"unused"` with an explanatory comment. This is a
documentation/provenance-accuracy fix only — **zero behavior change**,
confirmed by the full regression run below.

## Genuinely new work: BB09/BB10 supervisory hand-off

`revision5/dynamic_parameters.py` (the R5-native, already-certified dynamic
parameter engine used by BB01-BB04's prior work on this repo) states in its
own module docstring: "Consumers will be wired separately after this module
is certified." The precedent this branch follows is the existing
`Revision5SupervisoryBridge.snapshot_bb03_bb04` method
(`revision5/supervisory_bridge.py`), which BB03/BB04's prior work added to
expose upstream box outputs to R5 as a read-only, typed, immutable snapshot
— never a plant call, never a strategic decision.

This branch adds the symmetric **`snapshot_bb09_bb10`** method plus three
frozen dataclasses (`BB09OrderConstructionSnapshot`, `BB10ExecutionSnapshot`,
`BB09BB10SupervisorySnapshot`), all in `revision5/supervisory_bridge.py`,
and wires one call site into `revision2_external/orchestrator.py` immediately
after the real `self.broker.place_order(...)` call
(`orchestrator.py:1321-1335`), storing the result in a new
`self.bb09_bb10_supervisory_by_symbol` dict — the exact pattern
`bb03_bb04_supervisory_by_symbol` already established.

This is **purely additive observability**: it takes the already-computed
`ProposedOrder` and fill-result dict as inputs, validates them, and returns
an immutable record. It does not gate, retry, resize, or reprice anything;
it cannot be reached before the real order decision is made; and it adds no
new registry parameter (so the registry surface count, calibratable count,
and frozen identity hash are all **unchanged**). Authority: both snapshot
records are `INFORMATION_ONLY` (corrected in review; an earlier revision labelled them
`EXECUTION`). The *boxes* keep their execution-mechanics responsibility (see the
identity table), but a supervisory snapshot is telemetry and carries no authority.

`test_bridge_still_has_no_direct_plant_control_dependency` (mirroring the
existing `test_bridge_has_no_direct_plant_control_dependency` in
`tests/test_revision5_bb03_bb04_bridge.py`) confirms the new code did not
introduce a governor/plant import into the bridge module.

## Registry surface: before/after

No canonical-registry parameter was added, removed, renamed, or reclassified
by this branch.

| | before | after |
|---|---|---|
| target parameter count | 85 | 85 (unchanged) |
| fixed safety parameter count | 20 | 20 (unchanged) |
| calibratable parameter count | 63 | 63 (unchanged) |
| frozen identity SHA-256 | `42d9b0a6fa8f82b3fb060be21ca5aa71a43f88dc6f23738c8fbf889b3d854bf1` | `42d9b0a6fa8f82b3fb060be21ca5aa71a43f88dc6f23738c8fbf889b3d854bf1` (unchanged) |

`canonical_parameter_registry.py` was not edited. Verified with
`CanonicalParameterRegistry().identity_sha256() == CanonicalParameterRegistry.FROZEN_IDENTITY_SHA256`
(also exercised by the existing `tests/test_canonical_parameter_registry.py`
suite, which still passes unmodified).

## Calibration status

No new runtime parameter was introduced by this branch, so the
`ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED` labeling rule (mandatory rule 4)
does not apply to new registry entries here — there are none. All 16
pre-existing BB09/BB10-owned entries keep their pre-existing calibration
status (15 non-calibratable, 1 — `learning_rate_exploration_factor` —
registry-calibratable but excluded from the trading search space by
pre-existing code in `revision2/calibration_supervisor.py`, unmodified).

## Files changed

- `revision2/boxes.py` — `P01DBox.create_order` only (BB09): fixed two
  mislabeled `ParameterUse.output_field` values and their misleading
  in-line comment. No other class in this shared file (PA/ID/MPC/SafetyGates/
  PositionManager/DataIngestion/L2DataCertifier — BB01-BB08) was touched.
- `revision2_external/orchestrator.py` — added
  `self.bb09_bb10_supervisory_by_symbol` init and one call to
  `self.supervisory_bridge.snapshot_bb09_bb10(...)` right after the existing
  `self.broker.place_order(...)` call. No existing line was removed or
  reordered; no gate, sizing, or decision logic was changed.
- `revision5/supervisory_bridge.py` — added `ProposedOrder` import, three new
  frozen dataclasses, and the `snapshot_bb09_bb10` method. No existing method
  (`evaluate_upstream_admission`, `snapshot_bb03_bb04`, `evaluate`) was
  changed.
- `tests/test_revision5_bb09_bb10_parameterization.py` — new, 26 focused
  tests (see below).
- `outputs/BB09-BB10-dynamic-parameterization-audit.md` — this report.

`canonical_parameter_registry.py`, `gates_framework.py`,
`revision5/dynamic_parameters.py`, and every BB01-BB08 box implementation
were read for tracing purposes but **not modified**.

## Test evidence

### Focused BB09/BB10 tests (new)

`tests/test_revision5_bb09_bb10_parameterization.py` — 23 tests, all
passing:
- Registry classification: 2 tests (non-calibratable set, calibration-payload
  rejection of fixed values — rule "fixed safety/execution values cannot
  enter the calibration surface").
- BB09 order-construction sensitivity: 5 tests (`order_type`,
  `limit_order_offset_percent`, `order_timeout_seconds`, `max_retry_attempts`
  each proven to change `ProposedOrder`; zero-quantity upstream rejection
  proven un-bypassable).
- Dead-parameter honesty: 1 test proving `retry_delay_seconds`/
  `slippage_tolerance_percent` are read (present in the trace, so
  coverage tooling doesn't misreport them) but do NOT alter the
  constructed order — the "no parameters consumed merely to satisfy
  coverage" requirement checked in both directions at once.
- Duplicate-ownership / fail-closed boundary: 2 tests pinning Gate16's real
  wiring to `max_slippage_fraction`.
- Timeout floor: 1 test proving the calibratable `order_timeout_seconds`
  can only tighten, never loosen, past the fixed `order_timeout_seconds_execution`
  floor.
- BB10 mechanics: 5 tests (dedup window sensitivity, MARKET-only replay
  broker, `UnifiedExecutionBox.check_window` trading-window sensitivity,
  orchestrator-level `_in_trading_window`, constructor-cached broker
  slippage-fraction propagation).
- Structural constraint: 1 test proving non-MARKET `order_type` fails closed
  at orchestrator startup.
- R5 supervisory bridge: 5 tests (filled snapshot, rejected-fill snapshot,
  malformed-order rejection, immutability, no-plant-dependency).
- BB09-BB10 integration: 1 test running a real 1,500-bar INFY backtest
  through the full external engine and asserting the new
  `bb09_bb10_supervisory_by_symbol` snapshot is populated with a real,
  consistent order/fill record after real trades complete.

```
23 passed in ~33s (1 real-data integration test ~31s, 22 unit tests ~1.3s)
```

### Regression evidence (full suites, unmodified except the new test file)

| suite | command | result |
|---|---|---|
| BB01-04 + registry (targeted) | `pytest tests/test_revision5_bb01_bb02_parameterization.py tests/test_revision5_bb03_bb04_bridge.py tests/test_revision5_bb04_dynamic_parameterization.py tests/test_canonical_parameter_registry.py -q` | 39 passed |
| External orchestrator + all external-engine tests | `pytest tests_external/ -q` | 228 passed, 0 failed (7 pre-existing deprecation warnings, unrelated) |
| Existing R5 supervisory bridge + registry | `pytest tests/test_revision5_supervisory_bridge.py tests/test_canonical_parameter_registry.py -q` | 9 passed |
| Safety/execution gates | `pytest test_gates_framework.py -q` | 19 passed |
| Full tracked Revision-5 + repo test suite (excluding the pre-existing, untouched `tests/legacy_quarantine/` package, which fails collection on this checkout with `ModuleNotFoundError: No module named 'ecs_runtime_v2'` — confirmed via `git log`/`git status` to be pre-existing and unrelated to this branch's changes) | `pytest tests/ -q --ignore=tests/legacy_quarantine` | **267 passed, 137 subtests passed, 21 failed** in 191s |
| `git diff --check` | `git diff --check` | clean, exit 0 |

**The 21 failures are pre-existing and unrelated to BB09/BB10.** Verified
directly: `git stash` (removing every change this branch made) and
re-running the two most representative failing tests
(`tests/test_revision2_sensitivity.py::TestRevision2ParameterSensitivity::test_pa_parameters_are_sensitive`,
`tests/test_revision2_portfolio.py::TestPortfolioOrchestrator::test_shared_equity_curve_not_independent_sums`)
against the unmodified `bf091fd` checkout reproduces the identical failures
(same 16 PA-parameter subtest failures, same portfolio-equity-curve
failure), then `git stash pop` restored this branch's changes. All 21
failures are in `test_revision2_calibration_supervisor.py`,
`test_revision2_causal_sensitivity.py`, `test_revision2_optimizer.py`,
`test_revision2_pipeline.py`, `test_revision2_portfolio.py`, and the PA
(BB04) subtests of `test_revision2_sensitivity.py` — files this branch
never opened or edited, none in the BB09/BB10 (P01D/UnifiedExecution)
surface. No test this branch's changes touch, and no BB09/BB10 test, is
among the 21.

## Remaining limitations

- `revision5/dynamic_parameters.py`'s `ExecutionRuntimeProfile`
  (slippage/timeout regime-driven formulas) exists and is certified but is
  **not yet consumed anywhere** — its own docstring says consumers are wired
  "separately," and this branch did not wire it into BB09/BB10's live
  parameter path, because doing so would mean re-deriving `order_timeout_seconds`/
  slippage behavior from a second, formula-driven source alongside the
  existing registry-driven one, i.e. manufacturing a second owner for an
  already-owned value (forbidden by rule 1). The BB09/BB10 supervisory
  bridge added here exposes what BB09/BB10 actually did, not a hypothetical
  R5-formula-driven alternative; wiring `ExecutionRuntimeProfile` into a real
  decision path is a larger, separate change this audit intentionally did
  not attempt.
- `gates_framework.py`'s `force_close_time` (forced square-off time) is a
  hardcoded BB07 literal with real effect on BB09/BB10-adjacent exit timing;
  it was documented, not fixed, because fixing it requires touching BB07.
- `KiteConnectBrokerAdapter` (the real, live BB10 actuator) hardcodes its
  own tenacity retry policy (`stop_after_attempt(4)`, fixed backoff),
  independent of the registry's `max_retry_attempts`/`retry_delay_seconds`.
  This is consistent with those two parameters already being flagged
  inactive/fixed, and the module is explicitly not wired into calibration
  or live trading in this environment (no credentials present, and this
  assignment forbids running live trading), so it was left unmodified and
  is documented here rather than silently rewired.
- This audit and its tests establish that BB09/BB10's wiring is correct and
  regression-safe; they do not establish profitability, calibration
  quality, or live readiness.


## Review corrections (branch `codex/r5-bb09-bb10-review`)

Applied on top of `d049c8f` after a read-only review. No registry, manifest or frozen-identity change.

| # | Correction | Where |
|---|---|---|
| 1 | `order_timeout_seconds` = FIXED / non-calibratable / non-optimizer / **replay-inert** (Gate14 only ever sees `elapsed_seconds = 0.0`; paper ack records `ack_elapsed_seconds = 0.0`). The earlier ACTIVE_DYNAMIC / calibratable [5, 120] claim was wrong. | table above |
| 2 | `learning_rate_exploration_factor`: the old ACTIVE_DYNAMIC/calibratable description is **superseded** by the FIXED (diagnostic-only) classification in `b5ff1c26505dee6caa1d1e2ff34b2e14f7b88fc4`. Not modified here. | table above |
| 3 | BB09 snapshot = `INFORMATION_ONLY`; BB10 snapshot = `INFORMATION_ONLY`. | `revision5/supervisory_bridge.py` |
| 4 | Post-order snapshot failure cannot interrupt authoritative bookkeeping. The bridge raises the dedicated `SupervisorySnapshotError` (a `ValueError`) for invalid order/fill data and never repairs it. The orchestrator catches **only** that class after `broker.place_order`, appends a diagnostic record to `bb09_bb10_observer_failures`, and always continues fill/position/ledger reconciliation. The observer has no veto, no rollback and no strategic authority; any other exception still propagates. | `revision2_external/orchestrator.py` |
| 5 | The tautological timeout test was replaced. Gate14 logic and replay wiring are now tested separately. | tests |
| 6 | Header path/branch corrected. | this file |

Authority boundaries after the corrections: BB09 P01D = mechanical order construction (cannot decide whether to trade or pick side/entry/stop/target);
BB10 = execution/replay/paper mechanics (not strategy or governor authority); the supervisory bridge = information-only observer boundary
(no broker, plant, governor or protection authority). No ECS Plant Supervisor, grid-synchronizer activation, sector dispatch or governor wiring is added.

Unchanged out-of-scope items: `force_close_time` remains BB07-owned (`gates_framework.py`); the live `KiteConnectBrokerAdapter`
tenacity constants (`stop_after_attempt(4)`, `wait_exponential(multiplier=0.5, min=0.5, max=8)`) remain live-cert debt (adapter not wired into any engine).

Remaining debt: cosmetic `calibratable=True` literals on fixed `ParameterSpec` entries (overridden by `APPROVED_CALIBRATABLE`, hash unaffected) are left as-is;
`bb09_bb10_supervisory_by_symbol` keeps only the latest snapshot per symbol.
