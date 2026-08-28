# Risk Head V1 — Final Holdout Closure
## 25 August 2026

**STATUS: CLOSED — PRIMARY NO-GO / SECONDARY TAIL-RISK PARTIAL PASS**

### Frozen candidate

- Feature family: `B_INTRADAY_DAILY_SUMMARY`
- Model: `Ridge regression`
- Ridge alpha: `1000.0`
- Frozen features: `25`
- Target: `adverse_1d=max(0,-mae_1d)`
- Secondary event: `adverse_1d >= 0.020`
- Dataset SHA256: `2f51f697982063233ee120f0fbd93989d523efe868f810564efa26ef8099c888`
- Frozen candidate SHA256: `b018eaaccc8096c0ceb749e8ae087f8cd8348e2ebbe8827383c978a48fe61adc`

### 2025 validation

The candidate earned the one-time holdout exam on the frozen primary
risk-ranking metric. Exact validation values are preserved in the JSON
closure record directly from the Model 0 source artifact.

### One-time 2026 holdout exam

Holdout window: `{'start': '2026-01-01', 'end': '2026-07-27', 'n_dates': 139, 'n_rows': 1112}`

Primary Risk Spearman:

- Estimate: `+0.028098`
- 95% CI: `[-0.045072, +0.102050]`

2% adverse-event ROC-AUC:

- Estimate: `0.570657`
- 95% CI: `[0.521589, 0.613791]`

2% event average precision:

- Estimate: `0.256477`
- 95% CI: `[0.214303, 0.317317]`
- Actual holdout event rate: `20.323741%`

Highest predicted-risk quartile excess adverse excursion:

- Estimate: `+20.00 bp`
- 95% CI: `[+3.92, +34.25] bp`

### Final classification

**Primary result: NO-GO.**

The predeclared primary metric remained positive in point estimate but
its 95% confidence interval crossed zero on the untouched holdout.
Therefore general continuous risk-ranking ability was not independently
confirmed.

**Secondary tail-risk result: PARTIAL PASS.**

The 2% adverse-event classifier retained discrimination above random,
and the highest predicted-risk quartile retained greater realized
adverse excursion. These are genuine secondary findings, but they do
not replace the failed primary criterion.

### Production decision

**NOT AUTHORIZED FOR LIVE EXPOSURE CONTROL.**

No second attempt on the 2026 holdout is permitted. No sign inversion,
feature retuning, threshold change, target change, or model rescue may
be justified using the holdout results.

A future tail-risk-filter hypothesis may be researched only using
genuinely new unseen data or prospective shadow observations.

P01D remains sovereign.

### Closure provenance

- Model 0 SHA256: `1afa31b11821141a32410b41f5778221a6ff71bb8f7f8d6186892f00349193d0`
- Model 1 SHA256: `8d4468a3506aecab1a36f630119a0f201cf14e104dadf4dd1804682c2671956e`
- Holdout result SHA256: `17036b15175734e18498265aea060e797b6676e549b24875785dfca49263341b`
- Closure JSON SHA256: `5aa6c821f7eaee040fa38cedf634eb2a9e6f6b7289ddb33d28dcc3bcd0cd1ea0`
