# Risk Head V1 — Candidate B, Frozen

**Frozen:** 2026-08-25T10:21:01.614483+00:00
**Freeze record SHA256:** `b018eaaccc8096c0ceb749e8ae087f8cd8348e2ebbe8827383c978a48fe61adc`

## Candidate
- Model: Ridge regression, alpha = **1000.0**
- Feature set: `B_INTRADAY_DAILY_SUMMARY` (25 features, 7D + ORB + Map daily summaries)
- Target: `adverse_1d = max(0, -mae_1d)`
- Secondary event: `adverse_1d >= 0.020`

## Validation (2025, never touching HOLDOUT)
- Risk Spearman: **+0.09525**, 95% CI [+0.03730, +0.14600]
- 2% event ROC-AUC: **0.6425**
- 2% event average precision: 0.1731
- Event rate in validation: 10.691%

## Why nonlinear (Model 1) was rejected
- Nonlinear B Spearman improvement: +0.00132, CI [-0.03979, +0.04824] — crosses zero.
- Nonlinear B ROC-AUC: 0.5870 (down from 0.6425) — deteriorated.
- C and D did not significantly outperform B (both paired-increment CIs cross zero).

## Status
- `holdout_touched: false`
- 2026 HOLDOUT (2026-01-01 to 2026-07-27, 139 dates, 1,112 rows) remains sealed.
- No production runner started. No trades placed. `LIVE_TRADING_ENABLED` unchanged. P01D sovereign.

## Prohibited after the holdout opens
- No feature addition, removal, or reordering-with-semantic-change to the B feature list.
- No re-selection of Ridge alpha - stays frozen at the value recorded above.
- No change to the primary target (adverse_1d) or secondary event threshold (2.0%).
- No retraining on TRAIN+VALIDATION combined before the holdout read.
- No second attempt on the 2026 HOLDOUT with a modified model, regardless of the first result.
- No inspection of HOLDOUT rows for any purpose (EDA, distribution checks, feature engineering) before the single authorized evaluation run.