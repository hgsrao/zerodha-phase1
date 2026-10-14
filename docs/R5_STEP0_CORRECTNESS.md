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

## Already on this base

- **Min-hold precedence (GOV-4)** came from PR #7 (`440ca5f`) and is unchanged; the merge conflict with
  PR #8's equivalent code was resolved in PR #7's favour.

## Not ported

- PR #8's diagnostic-observer isolation: the gate-funnel observer hooks do not exist on this lineage.
  Its two observer tests and the funnel-script change were dropped.
- V3 position-controller test cases: V3 is not on this lineage, so governor tests run in legacy mode only.
- The replay-observation test uses this branch's engine harness in advisory authority.

## Validation

`tests` + `tests_external`, same environment for base and candidate, excluding
`tests/legacy_quarantine`, `tests_external/test_broker_adapter_kite.py` and
`tests_external/test_data_loader_arctic.py` (packages not installed):

| tree | passed | failed | skipped |
| --- | ---: | ---: | ---: |
| base `440ca5f` | 798 | 13 | 7 |
| candidate | 847 | 13 | 7 |

The 13 failures are identical on both and all need the real market dataset, which is absent in that
environment (the loader returns None). Neither suite fully passes.
