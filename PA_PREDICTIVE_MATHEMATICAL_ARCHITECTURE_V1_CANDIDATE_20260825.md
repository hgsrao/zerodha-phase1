# PA Predictive Mathematical Architecture V1

## Status

**CANDIDATE / NOT FROZEN**

## Authoritative path

STEP 2 -> PA -> ID -> MPC -> P01D

PA owns directional prediction.

PA does not decide TAKE/PASS.

PA does not size positions.

PA has no execution authority.

## Input boundary

PA may consume only certified Step-2 information.

Two representation families are admitted:

1. tabular Step-2 feature vectors
2. certified temporal LOB tensors

Sequence length, cadence, channel ordering, normalization, padding and
missing-depth handling remain unfrozen.

Published 10-level, 20-level or 100-snapshot configurations are not
automatically inherited.

## Target

Candidate target families include:

- fixed-horizon three-class
- volatility-scaled three-class
- triple-barrier directional

No target family is selected.

No prediction horizon is selected.

No barrier or class threshold is selected.

## Output

For every approved horizon, PA must produce:

- P(DOWN)
- P(FLAT)
- P(UP)

A real MPC requires multi-horizon information.

The conversion from class probabilities to expected returns remains
unfrozen.

P(UP) - P(DOWN) is not an authorized default expected-return formula.

## Promotion ladder

Mandatory baselines:

- NULL / FLAT
- logistic / linear
- gradient-boosted tree
- simple MLP

Deep challengers:

- five-level DeepLOB adaptation
- DeepLOB benchmark
- TLOB

Complexity must earn promotion against simpler baselines.

## External implementations

DeepLOB is an established benchmark.

Genesis2025 is admitted only as an implementation reference. Its
crypto-specific geometry, sequence length, checkpoint, thresholds and
reported accuracy are not inherited.

TLOB is admitted as an advanced challenger and reference framework.

## Validation

Random train/test splitting is not authorized.

Temporal validation is mandatory.

Purging and embargo are required when labels overlap.

Normalization may not be fitted using future information.

Consumed holdouts may not be used for rescue tuning.

## Promotion

Statistical classification quality alone is insufficient.

Economic relevance must also be demonstrated, including spread,
transaction costs, turnover, liquidity feasibility and MPC usefulness.

## Current real state

Selected PA model: **NONE**

Promoted PA model: **NONE**

Prediction horizons: **NONE**

Selected label family: **NONE**

Expected-return mapping: **NONE**

Broker authority: **NONE**

Execution authority: **FALSE**

Production: **FALSE**

P01D remains sovereign.
