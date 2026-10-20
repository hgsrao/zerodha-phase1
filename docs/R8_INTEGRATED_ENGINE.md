# R8 integrated engine

Branch `r8/integrated-engine`, based on PR #14 (`a8c2e1d`, which already carries PRs #7, #9–#13).
This is a research branch: it makes no profitability claim, and sealed `revision2/` and the V3
protocol JSON are untouched.

## What is merged (history kept, no copies)

| source | how |
| --- | --- |
| `r8-consensus-governor` | merge |
| `r7-engineering-station` | merge |
| V3 / diagnostic lineage (PR #8 line) | merge `-s ours`, then the diagnostics imported: gate funnel, symbol trace, entry-edge audit, Stage-C TRAIN export, their tests, the black-box audit and pre-registration docs. The `gate_observer` hooks are ported into `revision2_external/orchestrator.py`. |
| 8 vendored R8 modules | replaced by `sys.modules` aliases of the `revision5`/`revision6` originals (−4,544 lines). A test fails if any R8 module becomes a copy again. |

## Behaviour changes

- **Shorts are off by default** (`allow_shorts=False` in `r8_state`, `r8_engine`, `r8_coordinated_controller`).
  A flat four-way BEAR consensus is HOLD. The existing short-blocking reason code is unchanged.
- **Plant mode is explicit**: `run_r8_replay.py --plant-mode {ENGINE_A,ENGINE_B}` (default ENGINE_A,
  intraday). The value is recorded in every output.
- **Plant physics is wired into trading**: `--plant-physics {off,observe,enforce}` (default observe),
  orchestrator parameter `plant_physics_mode`, implemented in `revision5/plant_physics_runtime.py`.

## Plant physics runtime

Before this branch the machine model, startup sequencer, 25/25A synchronizer, relays (51, 87G, 21)
and the 86 lockout existed and were unit-tested but were never called from a replay.
An instrumented replay now shows every one of them running.

Each bar, per bay:
1. **Session start**: reset the previous lockout, then prepare unit startup.
2. **Startup sequence**: OFF → STARTING_MEANS → CRANKING → (GT: FIRING, exhaust-spread hold) → FSNL → SYNC_READY.
3. **Auto-synchronization**: the 25A pulses the governor speed and AVR voltage references until the 25
   permits closing 52G. The sequence then runs SYNCHRONIZED → MINIMUM_LOAD → DISPATCH_READY. The plant
   admits entries only when the unit is DISPATCH_READY.
4. **Machine scan**: 50 sub-steps of 0.1 s. The swing equation is explicit Euler and is unstable at a
   60 s step, so it must be sub-stepped. The inputs are:
   - Pe = bound notional / allocation;
   - vibration = 2 + 4 × wick/ATR mm/s;
   - exhaust spread is scaled to the governor's spread trip;
   - exhaust temperature comes from drawdown / FSRT span;
   - bearing temperature rises first-order with load.
5. **Relays**: 51 on Pe, 87G on broker vs booked notional, 21 on 1/velocity.
   A trip opens 52G, and a lockout trip latches 86.

**Modes**
- **observe** runs all of the above on a shadow plant that reads the live plant's loading. Trades are
  identical to `off` (this is tested).
- **enforce** acts on the live plant.

**Trips exit positions (enforce).** A physics trip on a bay (mechanical protection, 51, 87G, 21) arms
`plant_trip:PHYSICS_<code>:<reason>` on every open position in that bay, through the same
next-open exit path as the native SEL-300G / MiCOM / HRSG trips. In observe, positions are never touched.

**Mark V** (`--mark-v {crosscheck,veto}`, default crosscheck). `MarkVExecutiveController` re-evaluates
the governor's minimum value gate (FSRN/FSRT/FSRA/FSRS/FSRM) and records `MARK_V_CROSSCHECK`
AGREE/DISAGREE. With veto, an ENTRY that Mark V refuses becomes `NO_ACTION` with reason
`MARK_V_VETO:<reason>`. This takes effect where the governor is enforced (`governor_authority="full"`)
and requires plant physics observe or enforce.

**Stack / cross-bay thermal.** The code has no "stack sector". The cross-bay thermal coupling it
could mean is the HRSG's `calculate_cross_bay_correlations` / `compute_economizer_penalties`, which
already run every bar. There are no ANSI 81 or 27/59 relay models in the code; the relays wired here
are 51, 87G, 21 and 86, plus the existing SEL-300G and MiCOM.

## Acceptance (run where the dataset lives)

    python scripts/r8_acceptance.py --data-dir <frozen 48-symbol dir> --out <local dir, never committed>

For each of Engine B and Engine C (R8 mode, ENGINE_A by default), the script runs physics
off / observe / enforce and an enforce + Mark V veto arm. It checks that:
- observe trades are byte-identical to off;
- in observe, every unit synchronized and the machine scan ran;
- every arm reconciles;
- there are no short entries.

It writes `acceptance.json` and exits non-zero on any failure. A `--synthetic` smoke run passed for
Engine C here. Engine B needs `nautilus_trader==1.231.0`, which is not installable in this
environment.

**Settings.** Every relay and synchronizer setting is marked `ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED`
in `settings_report()`. These are starting points, not calibrated values.

## Not merged

- `feature/engine-ab-handoff-lifecycle`: its exit pipeline (`_finalize_exit`, durable intents,
  `combined_cycle_runtime`) conflicts with R8's `_on_exit_order_result`, and 602 of its files are
  `outputs/`. It needs a design decision on which exit path owns the position.
- 12 branches with unrelated history (codex/*, master, main, …): mostly outputs and backups.
- V3's duplicate protocol tooling (`exit_shadow.py`, `scripts/protocol_v3`): R8 already has `PositionControlV3`.

## Open decisions

- Should enforce be the default instead of observe? Should Mark V be veto?
- Should B/C runs default to ENGINE_B?
- Real-data acceptance has to run where the frozen dataset lives.
