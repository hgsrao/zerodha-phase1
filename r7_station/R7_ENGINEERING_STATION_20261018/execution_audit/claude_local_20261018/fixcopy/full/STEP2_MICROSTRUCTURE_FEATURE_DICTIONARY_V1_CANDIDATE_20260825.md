# Step 2 — Microstructure Feature Dictionary V1

## Status

**CANDIDATE / NOT FROZEN**

This artifact defines features only.

It does **not** claim that any feature predicts future returns.

### Immediately computable from top-5 displayed depth

- mid price
- spread bps
- static L1 imbalance
- static equal-weight L1-L5 imbalance
- microprice
- VAMP-style L1-L5 adjusted price
- total bid/ask displayed depth
- displayed order-count imbalance
- sequential displayed-depth changes

### Explicit distinction

Static displayed-book imbalance is not automatically classified as
true exchange-event Order Flow Imbalance.

True event-level OFI requires richer event information describing
adds, cancels and executions.

### Explicitly unfrozen

- feature selection
- feature weights
- distance weighting
- normalization windows
- smoothing
- thresholds
- predictive signs
- prediction horizons
- model usage

No feature has trading authority.
