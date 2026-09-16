# ID Meta-Labeling Mathematical Architecture V1

## Status

**CANDIDATE / NOT FROZEN**

PA owns direction.

ID does not create a second directional alpha model.

ID learns a secondary binary discrimination problem:

- 1 = take / primary opportunity succeeded
- 0 = pass / primary opportunity did not succeed

The labeling implementation may use a preregistered triple-barrier
construction with the PA side supplied as the primary side.

### Probability calibration

Raw classifier probabilities may not automatically be treated as
confidence.

A disjoint calibration procedure is required.

Candidate calibration methods include sigmoid, isotonic and
temperature scaling.

No method is selected yet.

### Financial validation

Generic random cross-validation is not authorized.

Temporal splitting, purge rules and embargo where overlapping labels
require it must be defined before model fitting.

### Explicitly unfrozen

- triple-barrier parameters
- event horizon
- classifier family
- model hyperparameters
- ID feature set
- calibration method
- probability threshold
- take/pass threshold
- class weighting

The 0.90 threshold seen in an external experimental repository is not
an architecture default and is not inherited.

### Authority

ID does not authorize execution.

P01D remains sovereign downstream.
