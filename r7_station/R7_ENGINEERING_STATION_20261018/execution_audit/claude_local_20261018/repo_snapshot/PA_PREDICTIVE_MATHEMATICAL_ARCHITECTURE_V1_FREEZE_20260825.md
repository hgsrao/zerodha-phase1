# PA Predictive Mathematical Architecture V1 Freeze

## Status

**FROZEN / PASS**

## Architecture

STEP2 -> PA -> ID -> MPC -> P01D

PA owns directional prediction.

PA does not decide TAKE/PASS.

PA does not size positions.

PA does not authorize execution.

## Input

Only certified Step-2 information may enter PA.

Top-5 feed geometry must be respected.

Uncertified raw-L2 bypass is prohibited.

## Output

A real PA must eventually produce multi-horizon:

- P(DOWN)
- P(FLAT)
- P(UP)

The probability-to-expected-return bridge remains unfrozen.

P(UP)-P(DOWN) is not an authorized default expected-return formula.

## Promotion ladder

1. NULL / FLAT
2. logistic / linear
3. gradient-boosted tree
4. simple MLP
5. five-level DeepLOB
6. DeepLOB benchmark
7. TLOB

Complexity must earn promotion.

## Validation

Temporal validation is mandatory.

Purging and embargo are required when applicable.

Random train/test splitting is not authorized.

Classification accuracy alone cannot promote PA.

Economic validation is mandatory.

## Explicitly unfrozen

- model family
- label family
- prediction horizons
- sequence length
- sampling cadence
- normalization
- architecture hyperparameters
- training hyperparameters
- expected-return mapping

## Current real state

Selected PA model: **NONE**

Promoted PA model: **NONE**

Selected horizons: **NONE**

Selected labels: **NONE**

Expected-return mapping: **NONE**

Broker authority: **NONE**

Execution authority: **FALSE**

Production: **FALSE**

P01D: **SOVEREIGN**

## Hashes

PA code:

80d3b12b7e968635dcd13a2f264af6c5dddeca4b89933ffd0ae869a3211d2c44

Candidate JSON:

4f0cb9cfde18bd1173c0c3acaa1853c6a227a8691b699002944d87ac94dbcdaf

Candidate MD:

04b0db811550c5181d680f7c60aa89306f42186ce5629048d4aebe752465e916

Freeze JSON:

bb7ec8b778a374b54f6afcc08260fc9e51939f4e7694a26349bc57ca4136e526
