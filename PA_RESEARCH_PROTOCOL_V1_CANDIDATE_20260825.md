# PA Research Protocol V1

## Status

**CANDIDATE / NOT FROZEN**

## Purpose

Establish the research rules before fitting any real PA model.

## Data

The primary microstructure research dataset must be prospectively
collected and certified top-5 L2 data.

Historical one-minute OHLCV may not be rebranded as L2 and may not be
used to synthesize a historical order book.

## Preregistration

Before the first model is fitted, a separate target-and-split
preregistration must freeze:

- target family
- prediction horizons
- label/barrier parameters
- chronological split dates
- purge/embargo rules
- preprocessing
- feature schema
- model-search budgets

## Required partitions

1. TRAIN
2. CALIBRATION
3. VALIDATION
4. FINAL HOLDOUT

The final holdout is one-time and may not be used for model selection.

## Model ladder

PA0 — NULL

PA1 — logistic / linear

PA2 — gradient-boosted tree

PA3 — simple MLP

PA4 — five-level DeepLOB

PA5 — DeepLOB reference

PA6 — TLOB

Complexity must beat the best eligible simpler model out of sample.

Ties go to the simpler model.

## Primary statistical metric

**Multiclass log loss**

Secondary metrics include macro F1, balanced accuracy, Brier score,
per-class precision/recall, confusion matrix and calibration curves.

## Economic gate

Statistical eligibility is not sufficient for real promotion.

A surviving PA must later demonstrate:

- edge relative to spread
- transaction-cost survival
- turnover feasibility
- liquidity feasibility
- stability across symbols
- stability across regimes
- usefulness to MPC

The expected-return bridge and real NSE cost model remain unfrozen.

## No rescue

After validation failure, the same hypothesis may not be rescued by:

- changing horizons
- changing labels
- changing thresholds
- removing bad symbols
- removing bad dates
- changing the primary metric
- sign flipping
- retuning on the consumed validation set

A materially new idea requires a new preregistered research branch.

## Current state

Certified PA L2 training dataset: **NONE**

Target: **NONE**

Prediction horizons: **NONE**

Selected model: **NONE**

Promoted PA model: **NONE**

Final holdout consumed: **FALSE**

Execution authority: **FALSE**

Broker authority: **NONE**

Production: **FALSE**

P01D remains sovereign.
