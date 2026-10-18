# R5 governor audit deliverables

The repository and running calibration were left unchanged. `01-single-gamma.patch` is a reviewable, unapplied git diff. It preserves `progress()` and all signatures; consumers use its already-shaped value directly. At gamma=1 behavior is unchanged. Non-unit gamma intentionally changes both expected and lower-bound trajectories.

## Apply separately when ready

```bash
cd /home/srinivas/projects/zerodha-r5-trace
git apply --check /home/srinivas/Documents/Codex/2026-09-29/continue-the-r5-governor-investigation-in-3/outputs/01-single-gamma.patch
git apply /home/srinivas/Documents/Codex/2026-09-29/continue-the-r5-governor-investigation-in-3/outputs/01-single-gamma.patch
```

## Offline diagnostic replay

Run with the project virtual environment. The runner imports the existing trace script and instruments only its newly constructed objects. It accepts that script's options, including `--params` for a specific Stage-A trial payload. Without `--params`, it uses protocol defaults, not the currently running trial. Use a fresh output directory; trace files are overwritten by the underlying runner.

```bash
source /home/srinivas/.venvs/zerodha-phase1-r5/bin/activate
python /home/srinivas/Documents/Codex/2026-09-29/continue-the-r5-governor-investigation-in-3/outputs/r5_audit_diagnostics.py --repo /home/srinivas/projects/zerodha-r5-trace --block 1 --bays GTG1_HEAVY_INDUSTRY --authority full --verify-passthrough --output-dir /home/srinivas/Documents/Codex/2026-09-29/continue-the-r5-governor-investigation-in-3/outputs/historical_audit
```

Historical replay was NOT launched as part of delivery. Existing saved summaries lack the raw confidence histories and branch-eligibility inputs needed for exact reconstruction.

## PID interpretation

`pid_gate.jsonl` records A, B, eligibility, gains, inputs and every `pid_veto`. B uses the actual runtime threshold, max(abs(target_r), abs(ki * integral_clamp)), not a hardcoded 0.30. All-bar quadrants are descriptive. Eligible quadrants exclude min-hold restrictions and earlier target/hard-stop/ratchet/max-hold exits.

The recommended observational metric is `eligible AND A AND NOT B`; its denominator is eligible evaluations. This counts exits vetoed by PID relative to an envelope-only inner rule. `NOT A AND B` is separately counted, but cannot change the AND rule's decision. The runner asserts that eligible A AND B exactly agrees with GOVERNOR_PATH_ERROR. No production logging mutation is needed.

`summary.json` adds `pid_gate_audit`, including final HOLD vetoes: inner vetoes that were not overridden by another Mark V exit. Joins use trade ID and timestamp, not array position. A decision difference is not proof of different fills or P&L.

Use `summarize_pid_gate.py TRACE_DIRECTORY` to recount; `--gains 1.0 0.05 0.3` evaluates another gain tuple on the recorded error trajectory and the same integral clamp. This is conditional sensitivity only. To measure alternate-policy outcomes, run complete separate replays because changed exits affect future state and entry availability.

## FSRN interpretation

`fsrn_components.jsonl` records entry and position observations, trade ID for positions, raw PA entry/exit confidence, PA direction, raw studies confidence/direction, both ranks, prior windows, unmasked minimum and actual side-aligned FSRN. The code's studies score consumed here is `composite_result['confidence']`.

For consecutive position rows of the same trade:

1. Check changes in `studies_opposed` and `pa_opposed`. Opposition means the direction equals the opposite of the trade; neutral direction is not automatically zeroed.
2. `direction_mask_changed_value=true` proves a mask lowered this bar's FSRN relative to the same ranks without direction masking. A new mask with a simultaneous rank decline can be a mixed cause.
3. If neither mask applies, FSRN equals min(PA exit rank, studies rank). Inspect which rank fell and its raw value.
4. To separate raw-input movement from rolling-window churn, for each component compute rank(previous window, current raw value) and rank(current window, previous raw value), alongside the actual ranks. The first freezes history; the second freezes the signal. Effects can interact; do not force exclusive labels.
5. Recompute ranks with the repository's causal_percentile_rank: fraction of PRIOR window values <= current value, including ties. Missing warmup is recorded as an error, not rank zero.

Compare LT entry with its first position row carefully: PA uses `confidence` at entry and `exit_confidence` in position. A 1-to-0 transition across that boundary is not automatically temporal churn in one signal. Entry rows can be linked to candidate events by symbol/timestamp; position rows join directly by trade ID/timestamp.

## Validation

`test_r5_audit.py` checks gamma 0.5, 1 and 2 at boundaries and intermediate times, a synthetic sequential PID veto with gain-sensitive actuation, and an instrumented/uninstrumented synthetic engine replay with matching trade hashes and authority results. It recomputes logged ranks from captured prior windows and checks direction masking.

Run: `R5_REPO=/home/srinivas/projects/zerodha-r5-trace /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q test_r5_audit.py` from this deliverables directory.

Synthetic reachability proves that the gate can matter. Historical frequency remains unmeasured until the diagnostic replay runs.
