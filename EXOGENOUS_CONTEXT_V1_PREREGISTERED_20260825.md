# Exogenous Context V1 — Preregistration
## 25 August 2026

**STATUS: FROZEN BEFORE EXOGENOUS OUTCOME EVALUATION**

### Question

Does a fixed six-feature external market context add next-session
downside-risk information beyond frozen Risk Head V1 Candidate B?

### Target

dverse_1d = max(0, -mae_1d)

Primary metric: pooled predicted-vs-realized adverse-risk Spearman.

### Population

TRAIN:
2023-09-22 through 2024-12-02  
2,336 rows / 292 dates / 8 symbols.

VALIDATION:
2025-01-01 through 2025-12-02  
1,824 rows / 228 dates / 8 symbols.

2026 is excluded.

### Baseline

Frozen Candidate B:

- Ridge regression
- alpha = 1000
- solver = lsqr
- 25 frozen features
- unchanged frozen preprocessing

### Challenger

Baseline plus exactly six exogenous features:

1. xo_vix_log_close
2. xo_vix_log_change_1d
3. xo_nifty_fut_log_return_1d
4. xo_bank_minus_nifty_fut_log_return_1d
5. xo_nifty_fut_log_oi_change_1d
6. xo_nifty_fut_price_oi_interaction

No feature search or hyperparameter tuning is permitted.

### Primary gate

delta_spearman = challenger - baseline

Pass only if:

- challenger Spearman > baseline,
- delta > 0,
- paired date-block bootstrap 95% CI lower bound > 0.

10,000 bootstrap replicates; dates are the resampling unit and all
eight symbols remain together.

Primary failure means **NO-GO**. Secondary metrics cannot rescue it.

### Secondary

2% adverse-event ROC-AUC delta with the same paired date-block
bootstrap.

### Interpretation

2025 is an incremental validation set for these new external features,
but not a pristine final holdout because the target outcomes were used
previously in Risk Head development.

A positive result therefore authorizes only future prospective
confirmation. It does not authorize production.

No 2026 rescue is permitted.

P01D remains unchanged and sovereign.

JSON SHA256: c3a3af9c5d6e7d34c3f0a759243cb801f588b3e350ce21730e9222b11fa7ff34
