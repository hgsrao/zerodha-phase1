# Brain Research Lab — Version 10 Anchored Walk-Forward Validation

Version 10 does not change any V9 trading logic. It changes how variant
selection is validated. V9's own report picked `TOP4_SECTOR` by looking at
that variant's full-period (2023-2026) result - the same data used to
produce the number being judged. This is not out-of-sample evidence.

## Method

Five anchored/expanding folds, using the same 6-month boundaries as
`walk_forward_v5.py`:

1. **Training.** Every variant (`TOP1`, `TOP2_SECTOR`, `TOP4_SECTOR`) is
   re-run with new-entry decisions restricted to `[start_of_history,
   fold_start)` - a window that only grows across folds and never touches a
   bar from, or after, the fold under test.
2. **Selection.** The variant with the best training-slice net return is
   chosen for that fold, subject to a minimum of 5 training trades. Folds
   that don't clear the minimum fall back to the pre-registered default
   (`TOP4_SECTOR`) rather than an ad hoc choice.
3. **Test.** The selected variant is re-run with new-entry decisions
   restricted to `[fold_start, fold_end)` only. Its trades are that fold's
   out-of-sample result.

All three runs call the unmodified `portfolio_brain_v9.simulate()` through a
`decision_window` gate added for this purpose - there is no second,
divergent implementation of the trading logic.

## Result (2026-08-14 run, real 2023-08-14 to 2026-08-13 data)

| Fold | Selected | Walk-forward efficiency | Test net P&L (₹) |
|---|---|---|---|
| 2024-02 – 2024-08 | TOP4_SECTOR | 62.48 | −6,123 |
| 2024-08 – 2025-02 | TOP1 | −0.45 | +1,110 |
| 2025-02 – 2025-08 | TOP1 | 0.57 | −888 |
| 2025-08 – 2026-02 | TOP4_SECTOR | 0.32 | +240 |
| 2026-02 – 2026-08 | TOP4_SECTOR | −0.07 | −133 |

- Profitable folds: **2 of 5**.
- Aggregate out-of-sample: **289 trades, net P&L −₹5,795, net return
  −5.79% on ₹100,000, win rate 35.3%, profit factor 0.85.**
- Bootstrap (5,000 resamples) on the aggregate out-of-sample trade P&L:
  95% CI **[−₹15,193, +₹4,434]**; probability the resampled total is
  non-positive: **88.2%**.
- Walk-forward efficiency swings from −0.45 to +62.5 across folds with no
  consistent sign - training-period performance has essentially no
  predictive relationship to test-period performance. That instability is
  itself the finding: it is what an absent, non-generalizing edge looks
  like under this method, not evidence of a real signal that happens to be
  noisy.

## Conclusion

The V9 full-period "+1.8% best variant" result does not survive honest
out-of-sample evaluation. The anchored walk-forward result is a loss with a
sub-1.0 profit factor and an 88% bootstrap probability of non-positive
aggregate P&L. This corroborates, with a harder methodology, the same
conclusion V9's own spec already stated in writing: no variant from this
research history should be selected for production.

## Known limitations of this validation itself

- Five folds is a very small number of independent out-of-sample periods;
  fold-count-derived confidence is weak regardless of the bootstrap result.
- The bootstrap resamples individual trades as if independent; same-day
  cross-sectional trades share market exposure, so the reported CI is
  optimistic (narrower than the true uncertainty).
- Early folds train on as little as ~5-6 months of history; their variant
  selection is the least reliable of the five.
- The 20-symbol universe and sector map are today's constituents, not a
  point-in-time investable universe; survivorship bias is not corrected for
  here, in V9, or in the external comparison.

## Release boundary

This is a validation methodology upgrade, not a production readiness
result. It does not authorize enabling live trading, unlocking Gate 4,
connecting a production execution runner, or placing, modifying, or
cancelling broker orders. `LIVE_TRADING_ENABLED` remains false; no broker
calls were made producing this report.
