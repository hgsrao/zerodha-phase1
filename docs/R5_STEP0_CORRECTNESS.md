# R5 step-0 correctness fixes on the governor runtime

Base: PR #7 head `440ca5f` (`diagnostic/pid-controller-authority-hierarchy`, on `r5-governor-closed-loop`).
Source of the fixes: PR #8, `bf2c07e` (diagnostic lineage), ported here so the Version Five
governor runtime carries them. This is a research branch; sealed V2 (`revision2/`) and the V3
protocol files are untouched, and nothing here is a profitability claim.

## Ported

- **Gate12 rounding (RG-3).** Reward:risk is compared with an absolute tolerance of `1e-9`, so a plan
  built at exactly the floor is no longer rejected by float rounding; a `1e-7` shortfall still is.
  Non-finite confidence, ratio or thresholds are rejected.
- **HMM fed every bar (ID-3).** `observe_bar(symbol, close, timestamp=...)` advances the regime model once
  per replay event, in warm-up and in the main loop, before admission, trading-window and held-position
  checks. Entry and exit read `latest_regime()` and never append again. Duplicate timestamps do not
  re-append, out-of-order or invalid closes raise, recalibration clears the symbol's cached state.
  The standalone `evaluate(..., latest_close=<float>)` API keeps its old behaviour.
  - Input validation runs first: a non-finite or non-positive close raises without changing state,
    even when its timestamp repeats the last observation.
  - A valid repeat of the last timestamp is idempotent. It returns the cached regime and ignores the
    close, even if the close differs; history, refit counter and posterior do not move.
  - Refit convention (unchanged): with `id_refit_every_bars` = 20 the counter resets to 0 on a fit
    and steps by 1 on each later eligible observation, so fits are 21 eligible observations apart.
    The first fit is on the `id_min_history_bars`-th observation.

## Already on this base

- **Min-hold precedence (GOV-4)** came from PR #7 (`440ca5f`) and is unchanged; the merge conflict with
  PR #8's equivalent code was resolved in PR #7's favour.

## Not ported

- PR #8's diagnostic-observer isolation: the gate-funnel observer hooks do not exist on this lineage.
  Its two observer tests and the funnel-script change were dropped.
- V3 position-controller test cases: V3 is not on this lineage, so governor tests run in legacy mode only.
- PR #8's replay-observation test relied on a harness that mocks the regime model; it is replaced
  by `tests/test_r5_step0_hmm_replay.py`, which drives the real HMM through the real replay loop
  (see below).

## Replay coverage

`tests/test_r5_step0_hmm_replay.py` replays two symbols over two sessions of synthetic bars with the
real HMM (no market data). PA, ID approval, one symbol's upstream admission and the new-entry halt
are controlled so that warm-up, held positions, upstream rejection, halted entries and the session
boundary all occur. It checks that each processed bar is observed exactly once and in order, that
warm-up hands over without a gap or repeat, and that the final fill/reconciliation bar is not
observed. Histories, refit indices and posteriors must match an independent box fed the same
prefixes, entry and exit reads must leave the HMM untouched, and rewriting future closes must not
change earlier posteriors. Moving the observation back behind the admission filter makes it fail.

## Validation

`tests` + `tests_external` on the final tree and on base `440ca5f`, run side by side in the same
environment (Python 3.11.15, pandas 2.3.3, NumPy 2.4.6, pytest 9.1.1), excluding
`tests/legacy_quarantine`, `tests_external/test_broker_adapter_kite.py` and
`tests_external/test_data_loader_arctic.py` (packages not installed):

| tree | passed | failed | skipped | passed subtests |
| --- | ---: | ---: | ---: | ---: |
| base `440ca5f` | 798 | 13 | 7 | 146 |
| final candidate | 873 | 13 | 7 | 146 |

Neither suite fully passes. The same 13 tests fail on both with the same exception. Each one loads
the frozen 48-symbol dataset named in `revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json`, whose
`data_dir` is a desktop path absent from this environment, so `MarketDataLoader._load_symbol_csv`
returns None (`'NoneType' object has no attribute 'tail'`/`'head'`, `not subscriptable`, or
`assert (None is not None)`). These results are not evidence about those 13 tests' behaviour.

Sealed `revision2/`, every protocol/JSON file, and `revision5/governor.py`,
`revision5/governor_authority.py`, `revision5/governor_position_policy.py` are byte-identical to
`440ca5f`.
