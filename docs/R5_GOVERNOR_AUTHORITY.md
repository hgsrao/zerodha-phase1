# R5 governor authority, Mark V gate and MiCOM protection

This note describes how the bay governor became the entry and discretionary-exit authority of
the replay engine (`revision2_external/orchestrator.py`), and what is still open.
It covers offline paper replay only. Nothing here enables live order routing.

## Authority matrix (top wins)

| rank | layer | owner | may |
|---|---|---|---|
| 1 | Safety | BB07 gates, kill switch, drawdown halt, force-close time, MIS session close, max-hold backstop, hard stop and target brackets | block entry, force exit |
| 2 | Protection | ANSI 86 lockout, unit trips, cooldown, MiCOM grid relay (ANSI 67 / 81U / 81O / 21) | block entry, force exit |
| 3 | Governor | `revision5/governor_authority.py` with the per-bay `BayTurbineClosedLoopGovernor` | the only ENTRY / HOLD / discretionary EXIT |
| 4 | Advisors | PA, ID, chart studies, MPC plan, exit PID, path loop, final execution controller, HMM regime | inputs and audit events only (in `full` mode) |

`governor_authority="advisory"` is the engine default, which keeps existing callers unchanged.
In that mode the governor's decision is recorded (`GOVERNOR_ENTRY_DECISION`,
`GOVERNOR_POSITION_DECISION`) next to the existing controllers.

`governor_authority="full"` requires `closed_loop_mode="active_paper"` and a real grid context
provider. `scripts/run_r5_paper_replay.py` defaults to `--governor-authority full`.

## Governor ENTRY (full mode)

The PA, ID and MPC chain still generates the candidate: its side, plan stop, target and hold
limits. Every candidate is then decided by the governor after the pre-sizing safety check.

1. **Comparator: overspeed limit.** PA candidates follow momentum, so the governor runs in
   `trend_overspeed` mode. It admits unless the side-signed z already exceeds
   `-base_z + feedback_offset - droop`, which is about +1.9σ in the trade direction at rest.
   The feedback offset comes from the outer realized-R PID, and the droop from the NIFTY deviation
   from its EMA. Poor realized R and an adverse grid both lower the limit. If the grid reference
   is unavailable, the result is NO_ACTION.

   The earlier mean-reversion comparator admitted only prices at least 1.9σ *against* the trade.
   On real data it vetoed every momentum candidate (block 1, trial 0: 0 of 3559).
   `mean_reversion` remains the governor's default for the native plant.
2. **Mark V minimum value gate.** FSR_selected = min(FSRN, FSRT, FSRA, FSRS, FSRM):
   - FSRN: the side-aligned minimum of PA and chart-studies conviction. Each is taken as its causal
     percentile rank over the preceding `gov_z_window_bars` bars. At entry PA uses `confidence`;
     in position it uses `exit_confidence`.
     - Why ranks: raw confidences sit around 0.15–0.4, below the 0.60 hurdle, so raw values
       blocked almost everything. A rank of 0.60 means the top 40% of recent conviction.
     - ID confidence is dropped from the minimum because it is the PA confidence itself.
   - FSRT: 1 - (portfolio MTM drawdown / span) × slope
   - FSRA: base - slope × velocity, where velocity = |ΔClose| / ATR
   - FSRS: session warm-up ramp from the causal session-bar index
   - FSRM: operator limit
3. **Vibration damper.** The entry hurdle is raised by gain × max(0, vibration - start), where
   vibration = (range - body) / ATR.
4. **Exhaust spread.** A bay's cross-member return dispersion, measured in ATR units. At or above
   the hold level, entry is held.
5. **ENTRY** multiplies the approved size by FSR_selected (≤ 1): the governor can only derate.

The `final_execution_controller` veto and the `entry_quality` hold become audit-only.
Sizing derates (entry quality, portfolio risk) still apply, and so do all caps and gates.

## Governor HOLD / EXIT (full mode)

Every completed bar of an open position is handled as follows:

- **Inner loop.** A per-position inner loop, keyed by `trade_id`, has its own PID state and
  one-way R-floor ratchet. The inputs are:
  - measured R from the close;
  - the reference R from the closed-loop reference path;
  - MFE from the bar's favourable extreme;
  - target R from the plan.
- **EXIT sources.** An EXIT comes from any of these:
  - the inner loop;
  - an exhaust-spread trip;
  - FSR below the exit threshold;
  - any invalid input, which fails closed.
- **Execution.** An EXIT is armed and executed at the **next bar's open**; the current bar's
  intrabar order is unknown. It is labelled `governor_exit:<reason>`.
- **HOLD.** The protective stop moves to entry + R-floor × 1R, one way only. FSR between the
  exit and entry thresholds is reported as `load_shed`. The engine carries one indivisible
  position per symbol, so it cannot partially unload.
- **Advisory in this mode.** The exit PID saturation exit, the HMM `regime_stressed` exit and
  the path-loop exit arming only emit `ADVISORY_EXIT_NOT_ACTUATED`.
- **Closing.** On close, `confirm_position_closed(trade_id)` releases the inner-loop slot, so a
  new position never inherits a ratchet.

## MiCOM (PAPER_APPLY with the native plant)

The relay is evaluated at every portfolio timestamp with measured inputs:

| code | input |
|---|---|
| ANSI 67 | daily fleet MTM drawdown against the day-start equity; latched for the session and reset at the session boundary in `CentralPlantMasterDCS.begin_bar` |
| ANSI 81U / 81O | latest completed NIFTY 15-minute return (causal grid prefix) |
| ANSI 21 | that return divided by the standard deviation of the preceding `micom_nifty_vol_z_window` returns |

- **On a trip:** the relay opens the grid intertie, which blocks admissions through the
  protection snapshot, and forces the protective exit (`micom_trip:<code>`) of open positions.
- **On recovery:** a healthy evaluation recloses an intertie that MiCOM itself opened.
- **Missing inputs:** unavailable grid inputs keep the intertie open (fail closed) without
  forcing exits.

## Parameters

There are 17 new registry values: 13 calibratable offline and 4 fixed. Their identity is
`482544d5…853d5`.

`docs/R5_PARAMETER_INVENTORY.md` is generated by `scripts/r5_parameter_inventory.py`. It lists:

- every registry parameter, in the classes STRUCTURAL, FIXED_SAFETY, CALIBRATABLE_OFFLINE,
  DYNAMIC_SCHEDULED and FIXED_ENGINEERING;
- every numeric literal still embedded in engine code. This is the honest list of operating
  values that are not yet registry-owned.

## Diagnostics

`scripts/r5_loss_attribution.py REPORT.json` breaks a replay's net result down in several ways:

- gross edge versus costs;
- exit reason, bay, side, entry hour and holding period;
- losers that reached 1R (an exit problem) versus losers that never went in favour (an entry
  problem).

Run it on the Stage A reports before any further calibration.

## Still open (not done in this change)

- **ID and MPC gate candidate generation.** ID approval and the MPC plan still decide whether a
  candidate exists, because the plan supplies side, stop and target. The governor decides every
  candidate that exists.
- **Unowned literals.** The literals listed in the inventory are not yet registry-owned. The
  largest groups are `revision5/dynamic_parameters.py` and `revision5/machine_archetypes.py`.
  The governor's `dynamic_z` feedback coefficient (0.25) and the MiCOM 81O ratio (1.5) are
  among them.
- **Deferred physics work:**
  - AVR P/Q/Vt mapping and the AVR velocity-PID double integration;
  - HRSG wiring;
  - the dispatcher capped-simplex fix;
  - unit-to-bus synchro-check;
  - SEL 24 V/Hz;
  - the SEL 40 / 60FL / 87G relays, which need bid/ask data.
- **Scheduled environment not passed.** The dynamic-parameter environment is not yet passed to
  `CentralPlantMasterDCS.begin_bar` from the replay engine.
- **Recalibration needed.** Stage A of the sealed Step 5 protocol ran on the pre-Mark-V engine.
  Stage A must be re-run on this engine, with a new protocol SHA over the new registry identity,
  before Stage B.
