# Study Layer V2 — Research Roadmap (post-decomposition)

**Date:** 2026-08-25
**Status:** Living document — tracks the multi-session research program agreed after the Pillar Information Decomposition V1 result. Update in place as items complete; do not let this silently go stale.

**Standing rules carried over from the whole day's discipline, binding for every item below:**
- Freeze each component's current definition before testing it. No retuning until something passes.
- Nested walk-forward / untouched holdout for anything with a learned/fit parameter.
- Joint-date block bootstrap + Benjamini-Hochberg FDR for every significance claim.
- Real costs, real execution model, gap-aware eligibility.
- A model doesn't get to overturn a frozen negative result just by assigning a feature nonzero importance — an ablation must show the feature actually mattered out-of-sample.

## Sequence (owner-approved, 2026-08-25)

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | ORB/Map risk-filter test (Head 2) | **DONE same day** | See `risk_filter_test.py` / `RISK_FILTER_TEST_20260825.json`. **ORB: real, robust, FDR-significant on 12/12 tested combinations — but INCREASES tail risk (+7 to +16pp probability of a large adverse move), not a filter in the hoped direction. Usable as a "reduce size / avoid mean-reversion entries here" signal.** **Map+Context: real but narrow risk-REDUCTION effect — 3/15 combinations FDR-significant, all at the 20bp threshold, 10-30min horizons only (-1.6 to -2.3pp); does not extend to larger moves or 60min.** (A units bug in the console summary line, ×10000 instead of ×100 for a probability-point difference, was caught and fixed before reporting — the underlying bootstrap/FDR computation was never wrong, only the display.) |
| 2 | P1/P2 state decomposition | **DONE same day** | `p1_p2_state_decomposition.py` / `p1_p2_state_decomposition_20260825.csv` — 5,944 rows x 31 columns, 8 symbols, 2023-08-25 to 2026-08-24. Reuses `p02_core.compute_indicators`/`generate_entry_signal` unchanged (imported, not reimplemented). P1: trend_slope, trend_strength_90d (=real Momentum90), breakout_distance_atr, invalidation_distance_atr, trend_persistence_days (new). P2: reversion_zscore (=real ZScore), displacement_atr, overshoot, reversion_setup_age_days (new). Both `*_signal_active` exposed as flags, not votes. Plausibility-checked: p1/p2_signal_active coincide with the correct Regime bucket in 100% of cases; p2_overshoot always positive exactly when p2_signal_active. Chandelier distance and reversion-progress were deliberately left out of scope — they're open-position properties, not per-bar state; a v2 territory if needed. Ready as input to item 3. |
| 3a | Daily Multi-Timescale Sensor Fusion panel (Option 2, owner-redesigned merge of items 3+4) | **DONE same day** | `daily_multi_timescale_fusion_panel.py` / `daily_multi_timescale_fusion_panel_20260825.csv` — 5,928 rows x 55 columns, symbol x trading-day, 8 symbols, 2023-08-25 to 2026-08-24. One row = everything known at D's close (P1/P2 continuous state + 9 frozen 7D session summaries [S/L_vwap/M/V last/mean/max/min per the owner's exact list, no more] + ORB/Map descriptive risk-context fields, NOT directional votes) predicting D+1..D+20 absolute return, excess return vs NIFTY, cross-sectional rank, MFE, MAE. **Real data-quality bug found and fixed while building this**: `nifty50_index_historical_2014_2026.csv` is missing 187/743 of SBIN's trading dates, 143 of them (76%) systematically on FRIDAYS across the whole multi-year file — not a holiday pattern, a genuine acquisition defect. That file feeds `map_context_observer.NIFTY_INDEX_PATH`/`classify_index_regime()` used throughout today's Map+Context work (a real, previously-unknown caveat on every regime read made today — likely a minor SMA distortion, not something that overturns the findings, but real and unfixed there, since those results stay frozen as run). This panel switched to the clean 60-min-derived NIFTY source instead (only 7 missing dates, no systematic pattern) — excess-return missingness dropped from 35-47% to 1-4% after the fix. DEVELOPMENT SAMPLE (8 symbols) — not final evidence, per the owner's own explicit caution against tuning repeatedly on it. |
| 3b | Model 0 (regularized linear) / Model 1 (constrained tree/boosting), A/B/C/D input-set comparison | **NOT STARTED, next** | A = P1/P2 only, B = 7D+ORB+Map daily summaries only, C = A+B, D = provenance-aware/regularized subset. Frozen train/validate/holdout split needed before any model runs. Evaluate on 3 tiers per the owner's spec: statistical (rank IC, calibration, uncertainty), economic (gross/net return, turnover, drawdown), stability (year/symbol/regime/fold). Do not select by R^2 alone. |
| 4 | Broader-universe expansion of item 3a/3b (~108-symbol daily universe) | **NOT STARTED, after 3b methodology is frozen** | Only once Model 0/1, features, horizons, and split methodology are frozen on the 8-symbol dev sample — expanding the sample is the real test, not another round of dev-sample tuning. |
| 5 | Three-head self-learning system (Alpha/Risk/Ranking) | **NOT STARTED, depends on 3b/4** | Only assembled once heads have independent out-of-sample evidence. Feeds a future MPC brain — not built until this head demonstrates value. |
| — | MPC optimization | **HOLD** | Until the learned outcome model (item 5) demonstrates real out-of-sample predictive value. Not before. |
| — | PI inner-loop controller | **HOLD** | Until MPC has a credible exposure reference to track. |

## Permanent labels (do not silently overturn)

```
ORB:  standalone_directional_status = NO_DEMONSTRATED_INFORMATION  (2026-08-25, FDR-corrected, 3,075 events, 8 symbols)
      risk_filter_status             = REAL EFFECT, WRONG SIGN - increases P(large adverse move) by 7-16pp,
                                        12/12 tested combinations FDR-significant. Candidate use: reduce-size /
                                        avoid-mean-reversion-entry signal, not a "safer to trade" filter.
MAP:  standalone_directional_status = NO_DEMONSTRATED_INFORMATION  (2026-08-25, FDR-corrected, 11,936 events, 8 symbols)
      risk_filter_status             = REAL BUT NARROW - reduces P(20bp adverse move) by 1.6-2.3pp at 10-30min
                                        horizons only (3/15 combinations FDR-significant); no effect on larger
                                        moves or 60min horizon.
```
If a future model assigns either high feature importance, the question is *why* — ablation/SHAP showing value only through interaction with another state (e.g. P1 + Map + regime) is a real, interesting, admissible finding. Removing the feature and seeing no out-of-sample difference means the importance score was noise, and the label above stands.

## Data inventory correction (verified 2026-08-25, do not re-cite the uncorrected figures)

A cited claim of "159 distinct symbols... 2015 through August 2026" came from a file not present on this filesystem. Verified directly instead: **108 distinct symbols** across `kite_nifty50_data` (48), `kite_midsmallcap_data` (60), `kite_nifty50_data_extended` (48, overlapping with the first). Only `kite_nifty50_data_extended` goes back to 2016 — the non-extended sets span roughly 2023–2026 only. The strategic point (substantial multi-year daily data already exists) still holds; the specific numbers did not match the citation.
