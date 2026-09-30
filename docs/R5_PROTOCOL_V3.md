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
- `ENGINE_V3`: the protocol's `engine_parent_commit`, plus the worker's clean-tree drift check.
- `REGISTRY_V3`: `registry_identity_sha256` in the protocol.
- `WORKER_V3_SHA` / executor SHA: stored in the protocol's `identity` block.

## Paired bridge (before any search)

`scripts/protocol_v3/paired_v2_v3_bridge.py` is exit-only. The V2 engine runs normally on the
V2 parameters, so every entry is the V2 entry. `revision5.exit_shadow.ShadowExitBridge` mirrors
each fill and exits it twice, once under `legacy` and once under `v3`, using the engine's own
per-bar inputs.

The bridge **refuses to report** unless:
- the legacy shadow reproduces every real V2 exit exactly (timestamp, reason, fill, net), and
- with `--v2-reference`, the real run matches the V2 result for every block.

Per trade it reports:
- ΔR;
- giveback, `max(0, MFE − R)`;
- MFE capture, computed for trades with MFE > 0;
- ΔMAE;
- exit attribution (PID stop / PID floor / FSR / plan stop / target / time / MiCOM).

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

1. **Wait until the V2 Stage C run has finished.** Don't touch `~/projects/zerodha-phase1` while
   it runs.
2. **Create the isolated worktree:**
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
