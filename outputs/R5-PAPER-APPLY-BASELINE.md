# R5 verified baseline and PAPER_APPLY — 2026-09-24

The working baseline is `codex/r5-paper-apply-blocker-closure` at `4963f14fb107adc21909ef8a386758e03c553c72`, plus uncommitted local PAPER_APPLY changes. The canonical workspace is `/home/srinivas/projects/zerodha-phase1`. No commit, push, live broker connection, or calibration was performed.

## What changed

The chain previously refused PAPER_APPLY even after its two telemetry/feedback blockers were closed. It now supports explicit PAPER_APPLY in external offline replay. SHADOW remains the constructor default for compatibility; the new starter command defaults to PAPER_APPLY and `closed_loop_mode=active_paper`.

An attached `CentralPlantMasterDCS` and a causal grid provider are required. The bay governor consumes the dispatch reference as an additional exposure cap after existing pre-sizing safety and sizing, before order construction, and again immediately before submission. It can reduce or reject a request; it cannot create a strategy signal, increase quantity, widen safety limits, or suppress an exit. The native governor's z-score strategy is not substituted for the external engine's PA/ID/final-controller decisions.

The cap treats `equity × existing gross limit × reference` as the total allowed plant/bay notional, subtracts already-held notional, and rounds quantity down using the greater of planned price and the costed replay fill price. This is a conservative total-exposure policy, not a calibrated capital-allocation optimum. Existing exposure above a reduced cap blocks new entries without forcing liquidation. Plant demand already reflects exposure headroom; this total-target interpretation intentionally becomes more restrictive as exposure grows and needs separate economic evaluation before sustained use.

References are valid only for their current replay timestamp. Failed evaluation clears them. Missing protection, blocked bays, master trips, stale/missing grid context, and invalid references prevent new entries. Protection and consumed bay capacity are reread per candidate. PAPER_APPLY checks the concrete offline broker at replay start, admission, and exits. The obsolete disabled positional-grid experiment (including its neutral VIX fallback) was removed.

`report["plant_control"]` records actual cap evaluations, rejections, reductions and whether admission was affected. `plant_control_shadow` remains a legacy report alias. Computed snapshots themselves remain observations; the admission events record consumption.

## Data and repeatable startup

The 48 frozen symbol CSVs are verified against their original manifest. Two additional grid feeds were recovered from the exact manifest paths on the backup and verified against the existing SHA-256 values:

- INDIA VIX 15-minute: 3,385,943 bytes.
- NIFTY 50 15-minute: 1,158,440 bytes.

Local feeds: `data/frozen_grid_15min/`; local manifest: `local_workspace/records/grid-manifest-local.json`. NIFTY ends on 2026-08-13, earlier than the stock dataset. The runner selects the latest common real date and does not extrapolate missing context. It conservatively delays every grid candle by its full 15-minute interval before strict-as-of selection; an unfinished candle can never affect admission, even if the raw feed is open-stamped.

```bash
cd /home/srinivas/projects/zerodha-phase1
source /home/srinivas/.venvs/zerodha-phase1-r5/bin/activate
python scripts/run_r5_paper_replay.py --compare-shadow
```

This runs INFY and TCS, 800 real bars each, with a 60-bar warmup and a fresh in-memory plant. It writes reports to `local_workspace/validation/paper_replay/`. Use `--symbols` and `--bars` for a different explicit sample. It verifies the stock and grid hashes before replay and supports only SHADOW/PAPER_APPLY.

The missing Optuna dependency was restored to the local virtual environment (5.0.0). `uv pip check` passed; the full environment is recorded in `local_workspace/validation/environment.txt`.

## Claude findings reconciled against the active code

| Finding / recovered work | Current evidence and disposition |
|---|---|
| A–C: discarded config, only 9/45 consumers, persistence unit mismatch | These findings target `production_trading_engine.py` / `production_optimizer.py`, which are absent from this checkout. Do not transplant that old simulator. The active external path builds `EffectiveConfig`, validates overrides and has coverage/parameter tests. Parameter reads alone still do not prove economic sensitivity. |
| D: safety imported but not used | Active external replay invokes pre-/post-sizing safety, entry decision gates, `ExecutionGate`, and post-fill checks. Fresh broad regression exercises those paths. This does not rehabilitate the old simulator. |
| E: tautological black-box consumption | Target `ecs_runtime_v2.py` is absent. Active code executes concrete boxes and records `EffectiveConfig` consumption; the old presence-only result is not baseline evidence. |
| F: hardcoded OOS metrics | Current `OOSBacktestRunner` now builds `BacktestMetrics(**metrics)` and returns HOLD for expected missing-data errors. The exact hardcoded-metric finding is superseded, but G/H/M below remain. |
| G/H/M: synthetic OOS score, concatenated market returns, unsealed windows | Still present in legacy `oos_calibration_engine.py`. This module is not imported by the reviewed R5/external replay path. Its scores/reports are inadmissible as calibration or profitability evidence. Replace or retire this path before using it. |
| I: optimizer lacks risk adjustment / accepts zero trades | The current `scripts/run_external_sizing_calibration.py` uses net P&L minus drawdown and rejects fewer than 20 trades; contract tests are included. This does not establish calibration quality or repair every legacy optimizer. |
| J: same-bar fills and fixed costs | Current external pipeline builds the next-bar plan and uses `paper_fill_price` plus booked transaction costs. Active tests cover timing, fill/bracket parity and cost reconciliation. Older claims apply to a different simulator. |
| K: unconditional COMPLETE status | `three_head_assembly_implementation.py` still writes unconditional COMPLETE metadata. It is outside the reviewed active replay imports. Do not treat old assembly reports as readiness certification. |
| L: old failing/flaky tests | Those historic counts do not describe this baseline. Legacy quarantine remains excluded exactly as in the existing R5 audit; current active-suite results are recorded below. |
| BB09/BB10 laptop work `d049c8f` | Superseded by correction `bed3dd5`, integrated at `89ee766`, already in the selected branch. No original-file overwrite needed. |
| HMM determinism finding | Active `regime_id_box.py` uses `zlib.crc32`, not randomized `hash(symbol)`. Real replay repeat comparison is recorded separately. Historical profits/losses and saturation-threshold choices are not calibration evidence. |
| Extra `test_r5_signal_to_paper_integration.py` | It targets in-house `Revision2Orchestrator`; conditional fill assertions can pass without an order. Retained as a historical reference. New PAPER_APPLY tests require positive fills in the permissive fixture and zero orders in the blocked fixture. |
| Architecture/profit-feedback conversations | Implementation proposals and older declarations are not accepted as completed work merely because Claude said so. R5 already has separate governor and dispatcher realized-R feedback. Durable recovery and automatic native-state coupling remain open. |
| Qwen 3.8 reference | The recovered September 22 Claude session concerns local model installation, not a verified newer R5 implementation. It does not supersede the code baseline. |

## Verification

Pre-change active offline baseline: **687 passed, 139 warnings, 142 subtests passed**, 407.73 seconds, after restoring Optuna. Log: `local_workspace/validation/r5-baseline-before.log`.

New focused PAPER_APPLY and candle-availability tests: **18 passed**. They include valid/invalid dispatch caps, absent/stale references, invalid evaluation, master/bay/ANSI-86 trips, cumulative bay usage, unknown symbols, broker replacement, both long/short exits during HOLD, and positive/blocked pipeline execution. Synthetic feeds are explicit unit fixtures; operational replay uses only verified real feeds.

Broad suite command, matching the existing R5 audit's scope:

```bash
python -m pytest tests/ tests_external/ -q \
  --ignore=tests/legacy_quarantine \
  --ignore=tests_external/test_broker_adapter_kite.py \
  --ignore=tests_external/test_data_loader_arctic.py
```

This includes every active R5 test. The historical quarantine and optional Kite/Arctic adapter suites are not part of this baseline; no new failing test was excluded.

Real-data sample, both with existing closed-loop paper control active:

| Mode | Closed trades | Net P&L | Reported post-fill safety violations |
|---|---:|---:|---:|
| SHADOW plant references | 7 | −₹1,497.43 | 0 |
| PAPER_APPLY plant cap | 7 | −₹1,109.00 | 0 |

The paper run recorded 80 cap evaluations (including submission rechecks), 9 rejection evaluations, and 64 quantity-reduction evaluations. These are evaluation counts, not counts of distinct trades. This is a functional demonstration on a small losing sample, not profitability or production certification.

## Remaining milestones

1. Preserve this verified offline baseline and review the local diff; no commit has been made.
2. Extend replay to all 48 symbols, multiple dates and stress scenarios; review capital utilization and cap semantics without tuning to this sample.
3. Drive native plant protection/startup/cooldown state from a defined causal replay event adapter. Today the attached plant's actual stored state is read, but the runner does not simulate every native physical-control transition or feed every native sensor automatically.
4. Implement durable close-feedback receipts, state restoration, and interrupted-run reconciliation before sustained/restartable paper operation. Exactly-once feedback is currently per process/run; failed fan-out can leave PENDING receipts.
5. Use sealed training/validation/untouched-test windows for calibration and economic evaluation; retire misleading legacy OOS/assembly reports. New plant thresholds remain engineering initial values, NOT_CALIBRATED.
6. Only after these gates: sustained paper operation and a separate readiness decision. No live actuation is enabled here.

## Independent-process repeat check

Two fresh PAPER_APPLY processes produced identical trade ledgers, fills, net P&L, safety-violation counts, and plant-control reports. Trade-ledger SHA-256: `c40d74769bac825ed4689807ca5666dd176d6b09556e14ea867718615c3e9545`. Evidence: `local_workspace/validation/paper-replay-repeat-verification.json`.

## Post-change regression

**704 passed, 182 warnings, 142 subtests passed**, 425.52 seconds. Log: `local_workspace/validation/r5-baseline-after.log`. The subsequently added candle-availability regression also passed (included in the final 18-test focused run), bringing the active test inventory to 705 unique tests. Its isolated log is `local_workspace/validation/grid-availability-test.log`. No executable core engine logic changed after the broad run began; the final runner timing change was verified by the additional regression and real-data replay. `git diff --check` is clean.
