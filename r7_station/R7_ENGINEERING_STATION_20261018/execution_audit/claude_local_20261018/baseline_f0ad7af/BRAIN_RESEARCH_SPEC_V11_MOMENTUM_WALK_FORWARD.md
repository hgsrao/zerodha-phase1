# Brain Research Lab — Version 11 Momentum Anchored Walk-Forward

Version 11 applies the same anchored/expanding walk-forward methodology as
V10, but to the two published-paper momentum controls
(`EXTERNAL_CROSS_SECTIONAL_12_1`, `EXTERNAL_TIME_SERIES_12M`) from
`external_model_comparison.py` instead of the hand-tuned V9 family.

## Why this is a different question from V10

V9's parameters were hand-tuned across nine iterations against this
project's own 2023-2026 sample - V10 exists to catch that specific failure
mode. These two models are different: 12-month formation, 1-month skip,
equal-weight top-4 are taken directly from Jegadeesh & Titman (1993) and
Moskowitz, Ooi & Pedersen (2012), not fitted on this dataset. The one
remaining discretionary choice - cross-sectional ranking vs. absolute
time-series trend - is what this walk-forward selects without look-ahead.

## Method

Same five anchored folds as V10. Per fold: both variants are re-run
(fresh capital) with rebalancing restricted to `[start_of_history,
fold_start)`; the better training-window net return is selected (minimum 3
training rebalances, else fall back to `EXTERNAL_CROSS_SECTIONAL_12_1`);
the selected variant is re-run (fresh capital) restricted to `[fold_start,
fold_end)` as the out-of-sample result. Both runs call
`external_model_comparison.monthly_long_only()` unmodified via its
`decision_window` gate.

## Result (2026-08-14 run, real 2023-08-14 to 2026-08-13 data, ₹100,000)

| Fold | Selected | Reason | Walk-forward efficiency | Test net return |
|---|---|---|---|---|
| 2024-02 – 2024-08 | CROSS_SECTIONAL_12_1 | insufficient training data | — | 0.0% |
| 2024-08 – 2025-02 | CROSS_SECTIONAL_12_1 | insufficient training data | — | **−11.2%** |
| 2025-02 – 2025-08 | CROSS_SECTIONAL_12_1 | best training return | −1.96 | **+22.0%** |
| 2025-08 – 2026-02 | CROSS_SECTIONAL_12_1 | best training return | −5.94 | **+10.0%** |
| 2026-02 – 2026-08 | CROSS_SECTIONAL_12_1 | best training return | +1.20 | **+9.8%** |

- Profitable folds: **3 of 5** (vs. V9's 2 of 5).
- `EXTERNAL_CROSS_SECTIONAL_12_1` (rank by momentum regardless of sign) was
  selected in every fold, including the three folds with enough training
  data to actually choose - `EXTERNAL_TIME_SERIES_12M` (positive-momentum-
  only) never won a training comparison.
- Aggregate out-of-sample daily equity-curve return: 458 observations,
  mean **+0.064%/day**. Bootstrap (5,000 resamples) on that daily series:
  95% CI **[−0.147, +0.734]** (cumulative), probability of a non-positive
  cumulative total: **9.2%** (i.e. ~91% of resamples were positive).

## Reading this honestly

Directionally more encouraging than V9's walk-forward: more profitable
folds, a positive aggregate, and a bootstrap sign favoring a positive
result rather than V9's 88%-probability-of-loss. But two real cautions
before treating this as validated:

1. **The bootstrap's 458 observations are daily, not independent
   decisions.** The portfolio only actually changes composition ~5-6 times
   per fold; consecutive days inside the same holding period share the same
   basket and move together. The true independent-decision sample is closer
   to the 5 fold-level outcomes (3 up, 2 down/flat) than to 458 - treat the
   fold pattern as the more honest signal than the tight-looking CI.
2. **Walk-forward efficiency is unstable across folds** (−1.96, −5.94,
   +1.20) - training-period performance still didn't reliably predict
   test-period performance, even though this strategy's parameters weren't
   fitted on this data. That instability is consistent with a real but weak
   and noisy edge, or with a small-universe (20 symbols), survivorship-
   biased sample producing lucky-looking periods either way. Five folds
   cannot distinguish those two explanations.

## Conclusion

More promising than the V9 family, and worth continuing to research - but
"3 of 5 folds, unstable walk-forward efficiency, autocorrelated bootstrap
sample" is evidence to keep investigating, not evidence to size real
capital against. It does not clear the bar V9's own spec set for "future
shadow observation," let alone production.

## Known limitations of this validation itself

- Five folds is a very small number of independent out-of-sample periods.
- The 20-symbol universe and sector map are today's constituents, not a
  point-in-time investable universe; survivorship bias is not corrected for
  here, in V9, or in the original external comparison.
- The published papers' long-short institutional portfolios are not being
  replicated; this is a long-only cash-equity adaptation.

## Release boundary

Same as V10: a validation methodology result, not a production readiness
result. Does not authorize enabling live trading, unlocking Gate 4,
connecting a production execution runner, or placing, modifying, or
cancelling broker orders. `LIVE_TRADING_ENABLED` remains false; no broker
calls were made producing this report.
