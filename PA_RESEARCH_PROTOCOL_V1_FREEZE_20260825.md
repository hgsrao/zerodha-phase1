# PA Research Protocol V1 Freeze

## Status

**FROZEN / PASS**

## Architecture

STEP2 -> PA -> ID -> MPC -> P01D

## Primary research question

Can certified Step-2 information predict future directional market
outcomes out of sample?

A second question asks whether more complex models add reproducible
information beyond simpler models.

## Data

Primary PA microstructure data:

**PROSPECTIVE CERTIFIED TOP-5 L2**

Historical one-minute OHLCV may not be rebranded as L2.

Synthetic historical L2 reconstruction from OHLCV is prohibited.

The PA dataset must be certified before model fitting.

## Target

Target family: **UNFROZEN**

Prediction horizons: **UNFROZEN**

Triple-barrier parameters: **UNFROZEN**

These must be frozen before the first model fit.

Every model must use the same frozen target.

## Temporal partitions

1. TRAIN
2. CALIBRATION
3. VALIDATION
4. FINAL HOLDOUT

Random train/test splitting is prohibited.

Calibration must be disjoint from model fitting.

Purging is required when labels overlap.

Embargo is required when applicable.

CPCV is permitted for development robustness analysis, but may not
replace the untouched final holdout.

Final holdout access is one-time.

## Model ladder

PA0 — NULL

PA1 — logistic / linear

PA2 — gradient boosted tree

PA3 — simple MLP

PA4 — five-level DeepLOB

PA5 — DeepLOB reference

PA6 — TLOB

Complexity must earn promotion.

Ties go to the simpler model.

## Primary statistical metric

**MULTICLASS LOG LOSS**

Lower is better.

Accuracy is not the primary metric.

## Economic gate

Statistical eligibility alone is insufficient.

Before real PA promotion, a model must survive:

- spread
- turnover
- transaction costs
- liquidity
- symbol stability
- regime stability
- downstream MPC usefulness

Expected-return mapping remains unfrozen.

The NSE cost model remains unfrozen.

## No rescue

Once validation is consumed, the same hypothesis may not be rescued by:

- retuning
- changing horizons
- changing labels
- changing thresholds
- deleting bad symbols
- deleting bad dates
- changing the primary metric
- sign flipping

A materially new hypothesis requires a new preregistered branch.

## Current state

Certified PA L2 dataset: **NONE**

Target: **NONE**

Horizons: **NONE**

Selected PA model: **NONE**

Promoted PA model: **NONE**

Final holdout consumed: **FALSE**

Broker authority: **NONE**

Execution authority: **FALSE**

Production: **FALSE**

P01D: **SOVEREIGN**

## Hashes

Protocol code:

7db71398a4649e531e97531e547ea47b346e9f248aed2b328e9e6c9c6350da32

Candidate JSON:

6d2a89465ff6143f3fe725ae31c333903a3563e63c1133e9a810eb422c6326f4

Candidate MD:

b957dcbb7d0cccfc5026cafc34384a51338fcd8234c1b2b5e197dcf9c66ceba6

Freeze JSON:

cf8c135df6bba2d1696030ec3920f322fa527e5e8bdc50d8d88fa1b275e5896d
