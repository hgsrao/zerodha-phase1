# Step 6 — MPC Mathematical Architecture V1

## Status

**CANDIDATE IMPLEMENTED / NOT YET FROZEN**

## Authoritative intelligence path

STEP 2 -> PA -> ID -> MPC -> P01D

MPC has exactly two direct inputs:

1. ID-qualified multi-horizon forecast
2. deterministic MPC constraint state

Direct PA -> MPC bypass: **PROHIBITED**

## Established optimization foundation

- cvxportfolio MultiPeriodOptimization
- CVXPY formulation layer
- OSQP for QP formulations
- CLARABEL for general convex formulations
- SCS fallback

Solver selection follows the mathematical formulation.
The economic model must never be distorted merely to fit a solver.

## Receding-horizon rule

The optimizer may plan multiple future actions.

Only the **first** optimized action may leave MPC.

All later planned actions are discarded and recomputed at the next
decision cycle.

## Forecast rule

The real optimizer must receive explicit ID-qualified PA forecasts.

Library-default historical return forecasts are prohibited for the
real policy.

A real multi-period MPC requires a multi-horizon forecast contract.

## Objective

Conceptually:

EXPECTED RETURN
- RISK
- TRANSACTION COST
- FORECAST / MODEL UNCERTAINTY

No economic coefficient is frozen in this architecture.

## Risk

Reference implementations may include:

- FullCovariance
- FactorModelCovariance
- ReturnsForecastError
- RiskForecastError
- WorstCaseRisk

The exact approved risk/uncertainty structure remains unfrozen.

## Transaction costs

Reference implementation:

StocksTransactionCost

Real NSE calibration is mandatory.

No default economic cost parameters may silently enter the real policy.

## Constraints

Reference optimizer constraints include:

- LongOnly
- LeverageLimit
- MaxWeights / MinWeights
- MaxHoldings / MinHoldings
- MaxTradeWeights / MinTradeWeights
- MaxTrades / MinTrades
- TurnoverLimit
- ParticipationRateLimit

Constraint values must come from approved policy configuration and
current deterministic MPC constraint state.

## P01D

MPC produces a proposal only.

MPC execution authority: **FALSE**

P01D safety authority: **SOVEREIGN**

Mirroring a P01D limit inside MPC never replaces P01D's independent
safety check.

## Numerical normalization

Solver residue and continuous-notional outputs must be normalized
before handoff.

This normalization may not become an alpha threshold.

Tolerance, executable-unit rules and rounding remain unfrozen.

## Explicitly not frozen

- decision cadence
- planning horizon
- horizon timestamps
- gamma risk
- gamma transaction cost
- gamma uncertainty
- risk-model choice
- covariance estimator
- transaction-cost calibration
- participation limit
- turnover limit
- position limits
- trade limits
- numerical tolerance

## Current real state

Real PA model: **NONE**

Real ID model: **NONE**

Real MPC policy: **NONE**

Real NSE transaction-cost calibration: **NONE**

Production: **FALSE**

Broker authority: **NONE**

Execution authority: **FALSE**
