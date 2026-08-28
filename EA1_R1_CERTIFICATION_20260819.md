# EA1-R1 Certification Record

**Status: EA1-R1 CERTIFIED, 2026-08-19.**

Lineage, explicit, per the owner-reviewed diagnosis that authorized this
program:

```
EA1-original (2026-08-15/16, sealed Phase 1-3.6)
    -> real incident, 2026-08-17 (SUNPHARMA 429) + 2026-08-19 (SBIN,
       SHRIRAMFIN 429s; Terminal B silent LTP failure)
    -> EA1: DEFECT FOUND / NOT PASSED
       (safety boundary demonstrated; liveness/recovery/observability
       failed - see EA1_INCIDENT_EVIDENCE_20260817_20260819/)
    -> defects D1..D6 identified, remediation program authorized
       ("Let's do it")
    -> EA1-R1 (this record) - all six defects fixed, certified below
```

## The one fact this whole certification rests on

**The sealed P02 engine was never touched.**

```
institutional_engine_v34_p02_multipos_candidate.py
sha256: cedb510b69776306c3b1bd109875e0a8a3ea04d2fbeb1300f14fb6b96fb82280
```

Identical to the hash recorded at every prior seal checkpoint throughout
this project's history (Phase 1, Phase 2, every Phase 3.x sub-seal, EA-1
build). All six defects below were fixed entirely in the bridge
orchestration layer - files that were never individually hash-frozen,
because the actual defects were never in the engine's own logic (which
was already correct) but in how the bridge drove and recovered from it.
No fork of the engine file was ever needed; "EA1-R1" names the certified
state of the surrounding bridge code, not a new engine variant.

## Defects D1-D6, verdict on each

| # | Defect | Severity | Fix | Where |
|---|---|---|---|---|
| D1 | Plan halt reason discarded (`halt_plan()`) | High | `PlanIncidentSidecar` - real reason durably persisted, keyed to `target_id` | `v34_bridge_plan_incident_sidecar.py`, `v34_bridge_rebalance_transitions.py` |
| D2 | Plan `HALTED` had no reconciliation transition/tool | Critical operational | `v34_bridge_reconcile_halted_plan.py` - narrow, defense-in-depth, reuses `run_resolve()`'s own verified-safe pattern | new module |
| D3 | Raw broker LTP call bypassed the audited fault boundary | High | Phase-aware + exception-type-aware classification (429 during a pure read is safe to auto-degrade; a genuine `NetworkException` mid-submission stays `ENTRY_UNKNOWN`-class, never softened) | `v34_bridge_runner_core.py` |
| D4 | Health UI could show `RUNNING` while the plan was dead | Critical observability | `compute_overall_readiness()` - a `HALTED` plan is always `BLOCKED`, independent of engine/process state. **Found and fixed a real live instance of exactly this bug** in `observatory_web.render_v11_dcs()`'s own output-box coloring while wiring this in. | `V34_Observatory_v1/observatory_readiness.py` + both renderers |
| D5 | Transient broker faults mapped too coarsely into permanent halt semantics | High | Submission-phase: automatic recovery via the same forensic check `resolve_entry_submit_halt.py` already used manually, for the `ENTRY_SUBMIT` class specifically (no new judgment invented). Read-phase: reuses the frozen engine's own `_is_transient_observation_exception` classifier, extended with the 429 case (safe only because it's pre-mutation) | `v34_bridge_runner_core.py` |
| D6 | No demonstrated cross-terminal/API-key rate governor | High until disproven, **confirmed** (owner verified A/B share one `KITE_API_KEY`) | `kite_request_governor.py` - file-backed, cross-process, sliding-window, wired into every real Kite call site found (adapter, accounting read client, shadow broker client's own internal calls, P02's live scan, R1-C's client) | new module + wiring across 7 files |

One item from the original review deliberately **not** built as stated:
"turn every 429 into a graceful decline" was explicitly rejected as too
coarse (a genuine `NetworkException` mid-submission is NOT safe to treat
the same way a 429 is) - see D3/D5 above for what was actually built
instead, and the owner's own correction that drove this.

## Staleness gate (owner-authorized rule)

Asked directly: R0 §7's literal same-day `is_target_stale()` formula
vs. a monthly-cycle-aware rule for resuming a halted plan. Owner chose
"stale once superseded by a newer rebalance" - implemented in
`v34_bridge_reconcile_halted_plan.py` via `_superseding_plan()` (checks
`RebalancePlanStore.list_all_plans()`, a new method, for a strictly
later `signal_date` already on disk - an objective fact, not a computed
calendar boundary). A superseded plan is formally marked `PLAN_ABANDONED`
in the sidecar and left `HALTED` permanently; a not-yet-superseded plan
proceeds to ordinary reconciliation unaffected.

## Real defects found ONLY by building this (not hypothesized in advance)

- Windows' `msvcrt.locking(LK_LOCK)` has its own internal 10-attempt/
  10-second cap and silently drops a thread under real contention -
  caught by the governor's own real-concurrency test (15 real threads).
  Fixed with an outer bounded retry loop.
- `observatory_web.render_v11_dcs()`'s existing output-box coloring
  already had the D4 defect live in it - a `HALTED` plan with a healthy
  engine rendered green. Found while wiring in the new readiness field,
  not hypothesized - confirmed against real current Terminal A/B data.
- The R1-C hash-freeze chain (`r1c_live_kite_client.py`/`r1c_live_
  observer.py`, manifest v3) was broken mid-session - edited for governor
  wiring without a SUPERSEDED_ copy first. Exhaustively searched for a
  backup (separate sandbox, OneDrive, Claude Code's own backup folder,
  the full D:\ drive) - none existed. Documented honestly in
  `P01D_V2B_REGIME_TWO_PILLAR_20260816/R1C_LIVE_OBSERVER_HASH_MANIFEST_
  20260819_v4.json` (`PROCESS_GAP_DISCLOSED` field) rather than silently
  patched over. A full project backup now exists at
  `D:\Zerodha_live_bot_3.4_ENTRY_UNKNOWN_BACKUP_20260819\` specifically
  so this can't recur.

## Regression evidence

**Full combined suite: 650 passed, 2 xfailed, 0 failed, 0 errors.**

The 2 xfailed are `test_ea1_incident_reproduction_20260817.py`'s own two
original bug-reproduction tests, deliberately kept and marked
`xfail(strict=True)` rather than deleted or rewritten - they document
that the original defects were real, and would turn into hard failures
(`XPASS`) if either defect ever regressed.

Suites covered: every `test_v34_bridge_*.py` at project root, `test_ea1_
incident_reproduction_20260817.py`, `test_kite_request_governor.py`,
`test_v34_p02_multipos_engine.py`, `test_v34_p02_lifecycle_integration.py`,
`test_v34_p02_broker_adapter_governor.py`, `test_v34_bridge_kite_read_
only_client_governor.py`, `test_v34_bridge_shadow_broker_client_
governor.py`, `test_v34_bridge_runner_core_quote_degrade.py`, `test_v34_
bridge_runner_core_auto_resolve.py`, `test_v34_bridge_runner_core_
sidecar_integration.py`, R1-C's own two frozen test files (`P01D_V2B_
REGIME_TWO_PILLAR_20260816/TESTS/`), and `V34_Observatory_v1/test_
observatory_readiness.py`.

## New artifacts this program produced

| File | SHA-256 |
|---|---|
| `kite_request_governor.py` | `cf71c9e60502e461e00d884d80ca1a2a45ef08b682fae2ddbb3da47776822e2a` |
| `v34_bridge_plan_incident_sidecar.py` | `a25ca1eebb845d755fb706409197d3386c946363fad7b9cc2aa5474931b0048d` |
| `v34_bridge_reconcile_halted_plan.py` | `ab734858f635de8fffcf55e0bd42aad936aeb752ca79a71e4d4fbe399c5256a2` |
| `V34_Observatory_v1/observatory_readiness.py` | `b340a9a13ebf842e79ec54a88a27a2568d1d5b78da334b8809d9d4c158e37ac3` |

Plus modifications to: `v34_p02_broker_adapter.py`, `v34_bridge_kite_
read_only_client.py`, `v34_bridge_shadow_broker_client.py`, `v34_bridge_
runner_startup.py`, `v34_bridge_runner_main.py`, `v34_bridge_runner_
core.py`, `v34_bridge_rebalance_transitions.py`, `v34_bridge_runner_
entrypoint.py`, `v34_bridge_rebalance_plan.py`, `P02_QUANT_LAB_20260816/
run_p02_live_scan.py`, `r1c_live_kite_client.py` (v3->v4, disclosed gap),
`r1c_live_observer.py` (v3->v4, disclosed gap), and the three
`V34_Observatory_v1/observatory*.py` renderers. None of these were
individually hash-frozen before this session except the R1-C pair,
handled as documented above.

## What EA1-R1 certifies, and what it does not

Certifies: the specific six defects the 2026-08-17/2026-08-19 incidents
exposed are fixed, tested, and proven not to have touched the sealed
engine. The bridge can now recover automatically from the exact fault
classes that actually occurred, make the exact failure that previously
had zero audit trail visible, and will never again display a healthy-
looking dashboard while a plan sits silently dead.

Does **not** certify: that these are the only fault classes that could
ever occur (only that the ones actually observed are handled), that
strategy alpha exists (a separate, unrelated question - see [[p01d-v2c-master-record]]),
or that EA-2 (real execution authority) is authorized. `LIVE_TRADING_
ENABLED` remains hardcoded `False` throughout - grepped every file this
program touched: the only assignment anywhere is `LIVE_TRADING_ENABLED
= False` (`v34_bridge_runner_main.py`); every other match is a comment,
docstring, or a read of the already-`False` value, never a `True`
assignment.

**EA-2 remains NO-GO.**

## EA-1.5 - corrected, 2026-08-19

EA-1.5 was originally scoped as two halves: deterministic replay of the
captured incidents, plus a Kite Connect sandbox exercise before waiting
for the next real monthly rebalance. **The sandbox half rests on a false
premise, checked directly against Zerodha's own current documentation,
not assumed**: [Zerodha's own support page](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/api-sandbox)
states verbatim, as of this session (2026-08-19): *"No, Zerodha does not
offer an API sandbox environment, but Zerodha offers Kite Connect API."*
A 2019 [developer-forum thread](https://kite.trade/forum/discussion/6424/sandbox-environment)
has Zerodha staff saying a sandbox was "on our list" - it was never
shipped. There is no demo-credentialed, simulated-order-lifecycle
environment to exercise. The earlier external review that proposed this
step cited it as if it were documented fact; it was not - the same class
of citation error this project's own diagnostic work has caught before
(see [[p01d-v2c-master-record]]).

**Deterministic replay half - already satisfied**, no further work
needed: `test_ea1_incident_reproduction_20260817.py` (both real
incidents, reproduced against the unfixed build, now `xfail(strict=True)`
regression traps) plus every fix-proving suite above (`test_v34_bridge_
runner_core_quote_degrade.py`, `test_v34_bridge_runner_core_auto_
resolve.py`, `test_v34_bridge_reconcile_halted_plan.py`'s `TestStaleness
Gate`) collectively ARE the deterministic replay this step called for -
they run the exact captured fault conditions through the repaired code
on every test run, not just once.

**Corrected next step, replacing the sandbox half**: since no sandbox
exists, shadow mode itself is Zerodha's own real substitute - real
broker connectivity, real market data, real reconciliation, zero
capital risk (every order declines before submission, same property a
sandbox would have offered). The repaired EA1-R1 build should be
exercised via a **fresh real EA-1 shadow-mode run** (same as the
original 2026-08-17 session, now with all six defects fixed) rather than
a sandbox that was never real. This IS the next real EA-1 shadow
observation mentioned below - EA-1.5 as a separate sandbox stage is
retired, not deferred; the deterministic-replay evidence above plus a
clean fresh shadow run together satisfy what EA-1.5 was meant to
provide.

Next: a fresh real EA-1 shadow-mode run (both terminals, next real
trading day or the next monthly rebalance, whichever the owner
prefers) to confirm the repair holds under real market conditions -
only after that, EA-2 is ever discussed again.
