# Five-bay policy expansion and steam replay

Work only in `~/projects/zerodha-r5-governor-refactor`, branch `r5-governor-closed-loop`,
base `fa9edd98c79ffc6a3bb57575c8d18fd6a01253a1`. Do not apply this patch to the active
`zerodha-phase1` calibration checkout. No auxiliary PID, governor equation, stop mechanic,
protection, droop, authority or registry implementation was changed in this extension.

## Configuration

`experiments/governor_position_policy.json` contains the exact five-bay map supplied by the user.
The loader translates these public names to existing dataclass fields:

| Supplied key | Existing field |
|---|---|
| mfe_activation_hurdle_r | mfe_activation_r |
| trailing_gap_base_r | base_gap_r |
| pid_tighten_gain | pid_alpha_r |
| pid_clip_max | pid_u_max |
| fsrn_min_conviction | conviction_absolute_threshold |
| fsrn_drop_threshold | conviction_drop_threshold |
| fsrn_persistence_bars | conviction_persistence_bars |

Each bay uses 0.30, 0.45, 0.10, 2.0, 0.20, 0.10 and 2 respectively. The existing
minimum_gap_r=0.15 remains unchanged. These profiles are identical and user-specified;
no separate sector calibration was performed. Different native PID gains and droop values
remain intact. Unknown/missing bay names and ambiguous alias collisions fail at loading.
Historical flat policy files remain supported. Omission of `--position-policy` still selects None.

Each replay run is restricted to one bay, as in the original trace tool; the CLI processes
requested bays sequentially and resolves the matching policy before each run. This is not a
single concurrent full-fleet simulation. Plant protection and dispatch retain their canonical
five-bay interfaces, with only the selected bay's market symbols loaded for that run.

## Steam replay

Use a fresh output path. The runner rejects nonempty output directories.

```bash
cd ~/projects/zerodha-r5-governor-refactor
source ~/.venvs/zerodha-phase1-r5/bin/activate
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
nice -n 15 python scripts/r5_governor_trace.py \
  --block 1 --bays CSTG1_BFSI,CSTG2_CONSUMER_AUTO,BPSTG_HEALTHCARE \
  --authority full --data-root ~/projects/zerodha-r5-trace \
  --position-policy experiments/governor_position_policy.json \
  --output-dir outputs/steam_policy/after
```

Add `--verify-passthrough` to repeat each historical case without instrumentation and compare
trade hashes. The delivered historical experiment is a single instrumented run; synthetic
tracer parity is tested separately. This command reads the sealed manifests/data and uses an
in-memory plant database; it does not read or modify active trial output/state. It uses protocol
default parameters unless `--params FILE` is provided.

## All-five default parity regression

```bash
python scripts/r5_verify_default_parity.py --reference fa9edd9 \
  --output outputs/default_parity.json
python -m pytest -q tests/test_r5_steam_policy.py tests/test_r5_governor_refactor.py \
  tests/test_r5_governor_trace.py tests/test_revision5_governor.py \
  tests/test_r5_governor_authority.py tests/test_revision5_plant_control.py \
  tests_external/test_closed_loop_control.py tests_external/test_pid_controller.py \
  tests_external/test_composite_study_signal.py tests_external/test_continuous_exit_controller.py
```

The parity script snapshots the reference commit into a temporary directory under this worktree's
`work/`, imports each code version in a separate process, and compares hashes of full reports and
trace rows for 90 synthetic bars, one representative symbol per canonical bay, with policy=None.
It removes its temporary snapshot. No checkout or branch is switched. This verifies equality on
that fixture; it is not a universal proof for all market histories.

For an optional full historical default-mode trade-list parity check:

```bash
nice -n 15 python scripts/r5_governor_trace.py --block 1 \
  --bays GTG1_HEAVY_INDUSTRY,GTG2_TECH_TELECOM,CSTG1_BFSI,CSTG2_CONSUMER_AUTO,BPSTG_HEALTHCARE \
  --authority full --data-root ~/projects/zerodha-r5-trace \
  --output-dir outputs/five_bay_none
# BASELINE must contain full-authority records for all five bays and matching inputs.
python scripts/r5_compare_governor_trace.py "$BASELINE" outputs/five_bay_none \
  --require-parity --output outputs/five_bay_none_parity.json
```

That optional full historical default replay was not repeated in this task. The supplied
baseline snapshot and existing gas results are reused with their provenance disclosed.

## Metric comparison and audit

```bash
python scripts/r5_compare_governor_trace.py "$BASELINE" outputs/steam_policy/after \
  --bays CSTG1_BFSI,CSTG2_CONSUMER_AUTO,BPSTG_HEALTHCARE \
  --output outputs/steam_policy/comparison.json
# With complete five-bay roots, omit --bays to compare all five.
python scripts/r5_pid_audit.py --output outputs/pid_audit.json
```

The comparison rejects protocol, parameter, session, symbol or data-hash mismatches. It reports
trade counts, hold-time distribution, MFE, detailed exit reasons, activation counts, computed gap
changes and applied incremental PID stop changes. MFE comes from the existing trade `mfe_r` field:
pre-terminal-bar excursion, not realized exit return and not a claim about intrabar execution order.

The audit script inventories PID constructors and performs isolated library/merit-weight probes.
The interpretation and downstream authority findings are in R5_AUXILIARY_PID_AUDIT.md. No auxiliary
controller numerical logic was changed.
