# Tail-Risk Filter V1 — Preregistration
## 25 August 2026

**STATUS: PREREGISTERED BEFORE POST-HOLDOUT OUTCOME INSPECTION**

### Frozen source model

- Feature set: B_INTRADAY_DAILY_SUMMARY
- Model: Ridge
- Alpha: 1000
- Frozen features: 25
- Fit policy: original TRAIN only
- Risk Head freeze SHA256: `b018eaaccc8096c0ceb749e8ae087f8cd8348e2ebbe8827383c978a48fe61adc`
- Risk Head closure SHA256: `5aa6c821f7eaee040fa38cedf634eb2a9e6f6b7289ddb33d28dcc3bcd0cd1ea0`

### New unseen evaluation population

- 2026-07-28 through 2026-08-21
- 19 dates
- 8 symbols
- 152 symbol-days
- 4 standard 75-bar dates / 32 rows
- 15 CAS 72-bar dates / 120 rows
- 2026-08-24 excluded because next-day outcome is unavailable

### Primary hypothesis

Within each date, rank all eight symbols by the frozen risk score.

The **top two symbols** are the fixed top-risk quartile.

Primary estimate:

`mean adverse_1d(top 2) - mean adverse_1d(other 6)`

Primary PASS requires:

- estimate > 0; and
- two-sided 95% date-block bootstrap CI lower bound > 0.

### Secondary hypothesis

Binary event:

`adverse_1d >= 2%`

Secondary PASS requires:

- ROC-AUC > 0.50; and
- two-sided 95% date-block bootstrap CI lower bound > 0.50.

### Inference

10,000 bootstrap iterations.

The bootstrap resamples **whole trading dates**, retaining all eight symbols
together so same-day cross-symbol dependence is preserved.

### Classification

- FULL PASS: primary and secondary both pass.
- PARTIAL PASS: exactly one passes.
- NO-GO: neither passes.

Even FULL PASS remains research evidence only because there are only 19 dates.
It does not authorize live exposure control.

### Locked prohibitions

No feature changes, alpha changes, score inversion, threshold search, bucket
search, symbol/date exclusions, refitting on later data, or post-result change
of primary metric.

P01D remains unchanged and sovereign.

### Preregistration JSON SHA256

`052b5b07ebcb1b4434342bccee698bde00f043102605dca0fcb824ce89beaf28`
