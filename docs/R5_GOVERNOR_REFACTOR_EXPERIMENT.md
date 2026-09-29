# Isolated governor position-policy experiment

This branch implements an opt-in offline experiment. It does not modify the sealed registry,
protocol, trial state, live routing, or the active calibration checkout. `--position-policy`
enables the new behavior; omission preserves the original controller (except the single-gamma
correction, which is behavior-preserving at the replay's gamma=1). The parameters below are
explicit starting assumptions, not calibrated values or an endorsement of prior claims that
rank-based exits are random or the original PID gate was dead code.

## Checkout and isolation

The worktree was created from `7ded8417074b828312eeb77210826ecc0cd6a07d` (the existing trace-tool
commit, one commit after the active checkout). Exact setup, for a fresh environment:

```bash
git -C ~/projects/zerodha-phase1 worktree add -b r5-governor-closed-loop \
  ~/projects/zerodha-r5-governor-refactor 7ded8417074b828312eeb77210826ecc0cd6a07d
cd ~/projects/zerodha-r5-governor-refactor
source ~/.venvs/zerodha-phase1-r5/bin/activate
```

The worktree and branch already exist after this task; do not rerun their creation command.
The shared virtual environment is used without installing/upgrading dependencies. Dataset
manifests and their certified data are read through `--data-root`; there is no symlink to
Stage-A output or state. The replay plant uses an in-memory database. Worktrees share Git
objects, so branch creation updates Git metadata but does not switch the active checkout.
The app worktree tool was attempted first and returned "Not a git repository" for the
projectless chat; Git was used to create the explicitly requested path.

## Stop actuation

Configuration lives in `experiments/governor_position_policy.json`, separate from the sealed
parameter registry. Positive error still means the position lags its reference.

```
bounded_u = clamp(u, 0, 2.0)
gap_R = max(0.15, 0.45 - 0.10 * bounded_u)
active = previously_active OR MFE_R >= 0.30
floor_R = max(previous_floor_R, initial_stop_R, MFE_R - gap_R)  # only if active
```

Before activation, the initial -1R floor is retained. Activation latches for that position.
The 0.45R in Action 3 is the BASE trailing gap; Action 1 reduces that same gap. Treating both
as independently fixed stop distances would contradict the requested PID modulation.
The chosen alpha/u_max gives a reachable gap of 0.25–0.45R; the configurable 0.15R minimum
is an additional bound. Negative PID output does not loosen a previously secured floor.

The anchor is the best favorable price, expressed relative to entry in fixed initial-risk
units. For BUY: stop = entry + floor_R * initial_risk. For SELL: stop = entry - floor_R *
initial_risk. Existing price-level max/min guards enforce the same one-way constraint.
The legacy +0.05R step and binary path-error test are bypassed only in experimental mode.
Target, hard-stop, ratchet crossing and max-hold exits remain.

The completed bar first encounters the stop set by an earlier bar. A new HOLD stop applies
from the NEXT bar; it is not tested against the high/low that generated it. If the new floor
is already crossed at the close, the existing ratchet exit rule arms the next-open exit when
minimum hold allows it. Armed discretionary exits retain next-open execution. Protective
stops retain existing gap-open and intrabar-stop execution; they are not all market-at-open
orders. Existing higher-priority safety/protection behavior is unchanged.

## Absolute conviction and persistence

At the entry decision, freeze the side-aligned minimum of raw PA `exit_confidence` and raw
studies `confidence`. Do not compare entry PA `confidence` with position `exit_confidence`.
During the trade, use the same two raw measures and direction masks. Opposite direction
contributes zero; neutral direction retains its value. Inputs are validated before masking.
Entry eligibility continues to use the original percentile-ranked FSRN.

A position is deteriorating only when BOTH conditions hold:

- current absolute conviction < 0.20;
- entry conviction - current conviction >= 0.10.

Two consecutive evaluated bars must deteriorate. A healthy bar resets the counter;
duplicate timestamps do not advance it. The state belongs to the trade, so new positions
start fresh. The baseline is causal decision-time information, not a future fill-bar signal.
An already-low entry baseline alone cannot satisfy the drop condition; this is an explicit
consequence of requiring relative deterioration. Other protective limiters still act.

FSRN logs the actual raw side-aligned conviction. Its exit is separately gated by persistence;
FSRT/FSRA/FSRS/FSRM low-limit exits, exhaust trips and inner safety exits are not delayed.
Missing/invalid entry/current measurements fail closed. Fuel-cut confirmation arms
`FSRN_SUSTAINED_DETERIORATION` for next-open execution.

## Replay commands

Run sequentially at reduced priority. Use a new output directory each time; the runner rejects
nonempty directories. `--params FILE` selects a specific trial payload; omission uses protocol
defaults (trial-0-style parameters), not whichever trial Stage A is currently evaluating.

```bash
cd ~/projects/zerodha-r5-governor-refactor
source ~/.venvs/zerodha-phase1-r5/bin/activate
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1

# Fresh baseline, if desired; the delivered comparison uses the existing verified traces.
nice -n 15 python scripts/r5_governor_trace.py --block 1 \
  --bays GTG1_HEAVY_INDUSTRY,GTG2_TECH_TELECOM --authority full \
  --data-root ~/projects/zerodha-r5-trace \
  --output-dir outputs/governor_comparison/before

# Experimental policy. Add --verify-passthrough to repeat every case without instrumentation.
nice -n 15 python scripts/r5_governor_trace.py --block 1 \
  --bays GTG1_HEAVY_INDUSTRY,GTG2_TECH_TELECOM --authority full \
  --data-root ~/projects/zerodha-r5-trace \
  --position-policy experiments/governor_position_policy.json \
  --output-dir outputs/governor_comparison/after

python scripts/r5_compare_governor_trace.py \
  outputs/governor_comparison/before outputs/governor_comparison/after \
  --output outputs/governor_comparison/comparison.json
```

The comparator checks protocol ID, complete parameter payload, sessions, symbols and data-slice
hash before reporting trade count, hold durations and exit reasons. It distinguishes computed
gap modulation, incremental PID floor changes, and actual stop-price updates attributable to
PID tightening over the same-bar no-PID proposal. Historical future outcomes cannot be
attributed to one component from this joint intervention.

## Tests

```bash
python -m pytest -q tests/test_r5_governor_refactor.py tests/test_r5_governor_trace.py \
  tests/test_revision5_governor.py tests/test_r5_governor_authority.py \
  tests_external/test_closed_loop_control.py
```

Tests cover gamma 0.5/1/2, MFE activation, bounds, monotonic stops, independent position state,
conviction direction masks, persistence/reset, invalid data, other protective limiters,
BUY/SELL anchoring and synthetic traced/untraced equality. Existing next-open execution tests
are retained. Historical replay is restricted to the two requested gas-turbine bays.
