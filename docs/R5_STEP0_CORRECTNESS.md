# R5 correctness candidate on the diagnostic lineage

Base: PR #6, `86a7fcaa433aa066eae08d3b342661d35bd50028`.
Branch: `research/r5-step0-correctness`.

This candidate implements three correctness fixes and hardens the passive
entry-funnel observer. It changes replay behavior and is not the sealed V2
or V3 engine. The existing protocol JSON files, registry identity, original
commits and tags are retained as references. The original sealed executors
must reject this candidate's code drift; this branch does not reseal or
qualify an experiment and does not claim profitability.

## Implemented behavior

- Gate12 rejects non-finite confidence, reward/risk and thresholds. The
  ratio comparison uses an absolute numerical tolerance of `1e-9`, with
  zero relative tolerance. This accepts bracket-price arithmetic rounding
  at the intended hurdle, but rejects a shortfall of `1e-7`.
- `HMMIntelligentDiscriminationBox.observe_bar` advances the model once per
  completed replay event, before upstream admission, trading-window and
  held-position checks. Warmup events use the same observation API. Equal
  prices on different bars remain distinct observations; duplicate event
  times do not append again; out-of-order events are rejected. Recalibration
  clears the symbol's cached model, posterior, classification and event key.
- Entry evaluation with `latest_close=None` and exit evaluation read the
  cached classification. Supplying an explicit close to the standalone ID
  API retains its historical behavior. Returned posterior telemetry is a
  defensive deep copy. The terminal reconciliation/fill sentinel remains
  outside the replay event clock; tests count processed events, not all
  rows in the supplied frame.
- The PR #7 advisory minimum-hold rule is ported into the V3-compatible
  `position_decision` API. Both ordinary and optionally confirmed sustained
  FSRN exits defer while `elapsed_bars < min_hold_bars`. The optional
  sustained policy is not enabled in the replay orchestrator by this port.
  Inner EXIT, invalid inputs, exhaust trips and every independent limiter
  retain precedence, including simultaneous breaches hidden below FSRN.
  `conviction_exit_deferred` is retained in governor decision telemetry.
- Gate and decision observers receive primitive values or defensive copies
  of nested measurements. Observer failures are counted and recorded, while
  the engine continues its own decisions. Funnel reporting refuses evidence
  with observer faults so an incomplete diagnostic cannot look successful.

## Validation

`tests/test_r5_step0_correctness.py` covers numerical boundaries, HMM event
ordering, cache reset and defensive reads, replay observation continuity,
simultaneous limiter breaches, both legacy/V3 controllers, sustained policy,
minimum-hold boundaries, hard stops, targets, maximum hold, exhaust trips,
invalid inputs, and faulting/mutating observers. No protection test skips.

The comparative run is `tests/` and `tests_external/`, excluding only
`tests/legacy_quarantine`, with the same local interpreter and dependencies
on the exact base and candidate. No external executor, live broker call or
calibration is launched. Dataset-backed tests are local compatibility checks,
not fresh out-of-sample strategy evidence. Results are recorded in the PR
description after the runs finish.

Local results on 2026-10-14, using the same Python 3.12 environment:

| tree | passed | failed | skipped | passed subtests |
| --- | ---: | ---: | ---: | ---: |
| exact PR #6 base | 829 | 0 | 7 | 151 |
| correctness candidate | 907 | 0 | 7 | 151 |

The 78 added test cases all pass, and skipped test identities match the base.
The focused Gate12/V3/funnel/correctness run passed 121 tests; the local
mocked executor-control run passed 12. These counts are overlapping suites,
not additive totals. Existing NumPy/pandas deprecation warnings remain.

## Larger audit boundary

The Gemini report supplied on 2026-10-14 is not a patch specification.
Several purportedly verified locations and causal explanations are wrong:

- The active external PA implementation is `indicators_talib.py`, whose
  signed sum adds the volatility and volume terms. Multiplying a directional
  value by a positive scalar does not itself reverse polarity. Separating
  direction and confidence is a new signal experiment, not a rounding fix.
- The allocation concern is long-only PyPortfolioOpt weights derating
  shorts, in `position_sizing_pyportfolioopt.py`. Absolute stop distance
  and notional concentration bounds are applicable to both long and short
  paper positions. Invented borrow costs and margin haircuts are not a fix.
- PR #5 already implements a price-path V3 inner controller with clamped
  integral, derivative reset and a one-way protective floor. A saturation
  diagnosis must identify the actual controller and an execution trace;
  it cannot be inferred from an unrelated confidence PID.
- The committed PR #6 inventory reports 384 inline literals, 35 default
  arguments and 121 named constants over the scanned engine files. A static
  literal count is not proof that every value is an active operating policy
  or should be made searchable.
- AVR, HRSG, dispatcher and synchro-check implementations exist. Native
  protection and capital/allocation paths are connected to PAPER_APPLY;
  claiming the whole plant is decoupled from execution is incorrect. Exact
  missing physics and parameter-provenance obligations need separate tracing.

The PA, confidence-threshold, sizing, ATR-horizon, parameter-provenance and
plant-engineering work is not claimed complete by this candidate. These need
separate reviewed implementation batches. The existing diagnostic
preregistration continues to govern economic conclusions and research
selection; this patch does not weaken its TRAIN/validation quarantine.
