# R5 Protocol V3: hardened closed-loop position control

V3 is an incremental experiment on top of the sealed V2 framework. The data, blocks, seed, trial
count, scheduler, objective, gates and tie-breaks are all V2's. Only the governor's position
control changes, and the five V2 trading parameters are frozen to the V2 Stage-B winner (Stage A
trial 7). The optimizer searches four controller parameters only. This keeps the result
attributable to the controller.

## Control law (`revision5.governor.PositionControlV3`, engine mode `closed_loop_v3`)

Per completed bar k of one position, with all quantities in R. `noise_r = ATR / initial risk` is
one bar's typical move.

| step | law |
|---|---|
| error | `e_k = reference_r − measured_r` (the reference path applies `curve_gamma` once) |
| leak | `γ_k = gamma_fast` if `|e_k| > tau_error_multiplier · noise_r`, else `gamma_slow` |
| integral | `I_k = clip(γ_k · I_{k−1} + e_k, ±I_max)`, where `I_max` is the bay's integral clamp; plain error is accumulated, so Ki is applied only once |
| derivative | `D_k = 0` on the first sample and on the first trailing-active sample; otherwise `e_k − e_{k−1}` |
| control | `u_k = Kp·e_k + Ki·I_k + Kd·D_k`, using the bay's V2 gains unchanged |
| gap | `gap_k = max(base_gap · (1 − κ·tanh(u_k)), noise_floor_mult · noise_r, minimum_gap)` |
| activation | trailing becomes eligible (and stays so) once `elapsed ≥ grace_bars` and `MFE ≥ mfe_activation_r` |
| floor | `floor_k = max(floor_{k−1}, plan stop, MFE_k − gap_k)` (a one-way ratchet) |
| causality | the floor becomes the protective stop from the **next** bar; there is no intrabar look-ahead |

- **Lagging and leading:** a trade lagging its reference path (`u > 0`) narrows the gap; a trade
  ahead of it widens the gap, by up to `κ`.
- **Path-error exit:** the binary path-error exit is not used. The controller acts only through
  the floor.
- **Order-flow input:** there is none. The data is 1-minute OHLCV, so order flow can't be measured.
- **Unchanged:** FSRN, Mark V, MiCOM, target, max hold and the forced-close exits.

`legacy` (the default) is the V2 inner loop, byte for byte. Every V1/V2 caller is unchanged.

## Parameters (registry-owned, EXTERNAL-only; registry identity `81760179…`)

| parameter | default | V3 search range | role |
|---|---|---|---|
| `gov_v3_mfe_activation_r` | 0.30 | 0.10–0.30 | searched |
| `gov_v3_kappa` | 0.25 | 0.10–0.40 | searched |
| `gov_v3_gamma_fast` | 0.85 | 0.50–0.90 | searched |
| `gov_v3_tau_error_multiplier` | 1.0 | 0.5–3.0 | searched |
| `gov_v3_gamma_slow` | 0.98 | — | frozen |
| `gov_v3_base_gap_r` | 0.45 | — | frozen |
| `gov_v3_minimum_gap_r` | 0.15 | — | frozen |
| `gov_v3_noise_floor_mult` | 1.0 | — | frozen |
| `gov_v3_grace_bars` | 2 | — | frozen |

The bay Kp/Ki/Kd, droop and integral clamps stay at their V2 values.

## Identity chain

- `PROTOCOL_V3_SHA`: SHA-256 of `revision5/step5_sealed_calibration_protocol_v3.json`. It is
  printed by `scripts/protocol_v3/build_protocol_v3.py` and recorded in the commit that adds the
  file. A file cannot contain its own hash.
- `ENGINE_V3`: the protocol's `engine_parent_commit` (`frozen_parent`, tag
  `r5-step5-v3-engine-20260930`), plus the worker's clean-tree drift check. The V2 engine appears
  only as lineage under `supersedes`. The builder refuses to seal a protocol that names the V2
  engine anywhere else, and it seals only from a clean checkout whose HEAD is the engine commit.
- `REGISTRY_V3`: `registry_identity_sha256` in the protocol.
- `WORKER_V3_SHA` / executor SHA: stored in the protocol's `identity` block.

## Paired bridge (before any search)

`scripts/protocol_v3/paired_v2_v3_bridge.py` is exit-only. The V2 engine runs normally on the
V2 parameters, so every entry is the V2 entry. `revision5.exit_shadow.ShadowExitBridge` mirrors
each fill and exits it twice, once under `legacy` and once under `v3`, using the engine's own
per-bar inputs.

`--v2-reference` is required. It must be the sealed V2 worker's result for the same stage and the
same five parameters, produced without the shadow observer. The bridge checks its mode, V2
protocol SHA, stage, parameters and block sessions before loading any data.

The bridge **writes no `bridge.json`** unless every block passes all three gates:
- `V2_REFERENCE_MISMATCH`: the observed run's trade ledger must hash identically to the
  reference ledger, and its metrics must match. This is the engine-neutrality check.
- `LEGACY_SHADOW_MISMATCH`: the legacy shadow must reproduce every real V2 exit exactly (entry and
  exit time, reason, quantity, fills, gross, costs and net).
- `SENTINEL_RECONCILIATION_VIOLATION`: no trade in the real, legacy or V3 ledger may be closed by
  `end_of_run_reconciliation`. That exit uses a block's final row, which is a non-tradeable
  boundary sentinel.

Per trade it reports:
- ΔR;
- giveback, `max(0, MFE − R)`;
- MFE capture, computed for trades with MFE > 0;
- ΔMAE;
- exit attribution (PID stop / PID floor / FSR / plan stop / target / time / MiCOM);
- the inner-loop state on the bar the V3 actuator first becomes eligible (the activation latch).
  This is the bumpless-transfer diagnostic, stored as flat fields on each trade:
  - `activation_integral_error` (`I_t`);
  - `activation_control_u`;
  - `activation_mfe_r`;
  - `activation_noise_r`;
  - `activation_gap_r`.

  All five are null when the actuator never became eligible. The full state is also stored:
  `v3_activation` holds the latch bar, and `first_stop_move` holds the state when the stop first
  moved. Both are summarized per block and overall.

The output is strict JSON.

Inputs that depend on the portfolio (FSRT drawdown, the drawdown halt) are the real V2 values,
as in any exit-only comparison.

## Holdout quarantine

- Nothing under `outputs/r5_step6_verification_state/` is read by any V3 tool. The tools refuse
  such paths.
- All V3 output goes to `outputs/protocol_v3_state/`. It is git-ignored, and so are `*.jsonl`
  traces.
- If the V2 Step-6 validation was executed or observed, the 2025-H2 window is no longer a pristine
  V3 holdout. The V3 verdict then uses fresh post-freeze out-of-sample data (see the protocol's
  `validation.holdout_eligibility`).

## Runbook (desktop is the reference machine)

1. **Leave the V2 tree alone.** Don't modify `~/projects/zerodha-phase1` while the V2 Stage C run
   is active. Adding a worktree is safe.
2. **Create the isolated worktree** on both machines, at the same path. The protocol's
   `distributed_execution.remote_worker_root` is `~/projects/zerodha-protocol-v3`. The Stage-A
   executor and coordinator run the laptop worker and protocol from that worktree; `--remote-root`
   overrides it. The engine check runs on both trees (the laptop's and the desktop's own). HEAD
   must descend from the engine commit (`git merge-base --is-ancestor`). Only the sealed protocol
   JSON files and `docs/` may differ from it; any other file, including Step-5 scripts, aborts the
   run. The tracked tree must be clean. Job
   files in `/tmp` are namespaced by protocol SHA, so a V2 leftover is never harvested.
   ```bash
   cd ~/projects/zerodha-phase1
   git fetch origin feature/protocol-v3-hardened-closed-loop
   git worktree add ../zerodha-protocol-v3 origin/feature/protocol-v3-hardened-closed-loop
   ln -s ~/projects/zerodha-phase1/local_workspace ../zerodha-protocol-v3/local_workspace
   cd ../zerodha-protocol-v3 && source ~/.venvs/zerodha-phase1-r5/bin/activate
   python -m pytest -q tests/test_r5_protocol_v3.py tests/test_r5_governor_authority.py
   ```
3. **Run the bridge**, for trial 0 and for trial 7, on the Stage-A blocks:
   ```bash
   python scripts/protocol_v3/paired_v2_v3_bridge.py --stage A --label trial_000 \
     --params <V2 state>/params/trial_000.json --v2-reference <V2 state>/results/trial_000.json
   python scripts/protocol_v3/paired_v2_v3_bridge.py --stage A --label trial_007 \
     --params <V2 state>/params/trial_007.json --v2-reference <V2 state>/results/trial_007.json
   ```
4. **Review the bridge results, then run Stage A** with the existing executor and the V3
   protocol, V3 worker SHA and a new state directory under `outputs/protocol_v3_state/`.
5. **Run Stage B,** on the desktop.
6. **Compare** with `scripts/protocol_v3/compare_baseline_vs_v3.py` against the V2 results for the
   same stage.
7. **Stage C and validation:** only after the V3 winner is frozen, and only under the holdout rule
   above.
