# Tail-Risk Filter V1 — One-Time Preregistered Result
## 25 August 2026

**CLASSIFICATION: NO_GO**

**PRODUCTION AUTHORIZED: NO**

### Evaluation population

- Dates: 19
- Symbol-days: 152
- Symbols: 8
- Standard 75-bar rows: 32
- CAS 72-bar rows: 120

### Frozen-model reproduction

2025 validation Risk Spearman:

`+0.095245788824`

Frozen expected:

`+0.095245788824`

2025 validation ROC-AUC:

`0.642458012624`

Frozen expected:

`0.642458012624`

**Reproduction: PASS**

### Primary — top-risk quartile excess adverse excursion

Estimate:

`-0.27 bp`

95% whole-date bootstrap CI:

`[-41.37, +51.81] bp`

PASS:

`False`

### Secondary — 2% adverse-event ROC-AUC

ROC-AUC:

`0.482996`

95% whole-date bootstrap CI:

`[+0.319237, +0.686622]`

PASS:

`False`

### Average precision

Average precision:

`0.112634`

Observed event rate:

`0.105263`

Lift versus event-rate baseline:

`1.070x`

### CAS 72-bar sensitivity

Dates:

`15`

Primary excess adverse:

`-20.93 bp`

95% CI:

`[-54.09, +10.00] bp`

ROC-AUC:

`0.439971`

95% CI:

`[+0.281798, +0.627839]`

### Standard 75-bar subset

Only four dates are available.

These values are descriptive only.

Primary excess adverse:

`+77.22 bp`

ROC-AUC:

`0.747126`

### Decision rule

- FULL PASS = primary and secondary both pass.
- PARTIAL PASS = exactly one passes.
- NO-GO = neither passes.

Whatever the classification, this 19-date pilot does **not**
authorize live exposure control.

No post-result feature changes, threshold search, bucket search,
sign inversion, symbol exclusions, or date exclusions are permitted.

P01D remains unchanged and sovereign.
