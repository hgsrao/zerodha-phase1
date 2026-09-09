# Closed-Loop Trading Controls: Comparative Research and Rev4 Design

## Decision summary

Closed-loop control is a legitimate and widely used idea in quantitative
trading.  The closest established comparators do **not** use a PID controller
to command a daily profit.  They use feedback to control one of three things:

1. execution cost versus a benchmark;
2. inventory/exposure versus a risk budget; or
3. portfolio/position trajectory versus a planned target under transaction
   costs and hard constraints.

For Revision 4, the defensible design is therefore a **hierarchical controller**:
PA and ID estimate opportunity; MPC forms a bounded trade plan; feedback
controllers regulate entry size, exit protection, and portfolio exposure;
safety gates remain non-negotiable overrides.  A ₹400 daily result is a
reporting benchmark, never a controller setpoint.

## What the established comparators actually do

| Comparator | Controlled variable | Feedback inputs | Control action | Relevance to Rev4 |
|---|---|---|---|---|
| Almgren–Chriss execution | inventory liquidation trajectory | time remaining, volatility, market-impact/cost estimates | trading rate | Strong model for execution-cost and urgency control, not alpha generation |
| VWAP/implementation-shortfall execution | fill quality versus benchmark | realised fill, VWAP/arrival price, volume | schedule/participation rate | Direct analogue for Gate16/TCA feedback |
| Avellaneda–Stoikov market making | inventory around desired level | inventory, price risk, order-arrival conditions | quote skew/spread | Direct analogue for exposure derating, but not for directional intraday alpha |
| Regime-switching MPC | target allocation/risk budget | inferred regime, forecasts, covariance, costs | rebalance weights under constraints | Strong analogue for Nifty/grid as a bounded regime modifier |
| Modular algorithm frameworks | target holdings after risk adjustment | alpha, portfolio state, risk state | execution orders | Architectural comparator for the 10-box separation |

Almgren and Chriss formulate execution as a trade-off between transaction cost
and volatility risk, rather than as a profit-chasing loop.  Avellaneda and
Stoikov dynamically skew quotes in response to inventory risk.  Both are
feedback-control precedents, but neither establishes that a PID can create
predictive edge from price noise.

## Closest public architecture comparator

QuantConnect's Algorithm Framework separates Universe Selection, Alpha,
Portfolio Construction, Execution, and Risk Management.  Its flow is:

```text
market data → alpha/insight → target holdings → risk-adjusted targets → execution
```

This is materially similar to Rev4:

```text
Data Input → PA → ID → MPC → Position Manager/Risk → P01D → Broker
                 ↘ Chart/Grid/Performance feedback ↗
```

The comparative lesson is separation of authority:

- an alpha signal can propose an opportunity;
- a portfolio layer sets target exposure;
- risk adjusts or vetoes target exposure;
- execution seeks the approved target;
- feedback measures outcome without allowing execution or performance to
  override risk.

## What exists in Rev4 today

`ModelPredictiveControlBox` already contains two bounded PIDs.  Their current
measurement is `IDDecision.confidence`; their existing setpoint is a rolling
mean of *prior* confidence values for the same symbol.  This was deliberately
chosen over a fixed ID approval threshold: after ID approval, a fixed threshold
would produce mostly one-signed PID error and risk saturation.

Current PID outputs are intentionally bounded:

- entry PID: a `0.30–1.00` size/timing multiplier;
- exit PID: a `0.50–1.00` tightening factor for the entry-time stop and target
  distances.

This is a valid stability loop, but it is not yet an explicit market-regime or
position-progress controller.

## Recommended control topology

Use three separate loops.  Do not blend variables with different units into a
single PID error.

### Loop A — Entry-quality controller

**Purpose:** decide whether an otherwise-approved idea should receive normal
or reduced size.

```text
measurement y_Q = current ID confidence
setpoint r_Q    = symbol confidence baseline, conditioned on regime
output u_Q      = bounded sizing multiplier only
```

Derive the regime-conditioned setpoint causally:

```text
r_Q = clip(
    rolling_median(prior approved ID confidences for this symbol)
    + k_grid × grid_alignment
    + k_index × Nifty_alignment
    − k_vol × volatility_stress,
    lower_bound,
    upper_bound
)
```

`grid_alignment` is a normalized 48-symbol breadth/sector-consensus score.
`Nifty_alignment` is a signed Nifty trend score aligned with the candidate
direction.  `volatility_stress` comes from Chart Studies/ATR.  All components
must be computed from data at or before the decision bar.

The output may reduce size or reject an entry through a separate gate.  It must
never enlarge position size beyond the pre-existing Position Manager cap.

### Loop B — Exit-progress controller

**Purpose:** avoid the observed cost-heavy time exits and contain stop-hit
losses without widening risk.

Normalize the live trade by its initial risk:

```text
R_t = direction × (mark_price_t − entry_price) / initial_stop_distance
```

MPC supplies initial stop, target, and maximum-hold-bars.  Chart Studies and
Grid/Nifty regime may set the *expected progress path*:

```text
r_R(t) = target_R × progress_schedule(bars_held / max_hold_bars, regime)
e_R(t) = r_R(t) − R_t
```

The exit PID output may only:

- tighten an existing trailing stop;
- reduce the remaining holding horizon; or
- request an exit through the Exit Decision box.

It must never widen the original stop, postpone an EOD flatten, or override a
safety exit.  The target price remains an MPC plan, not a promise the PID may
force the market to deliver.

### Loop C — Portfolio-exposure controller

**Purpose:** control aggregate risk, not individual trade profit.

```text
measurement y_E = gross exposure / equity, sector exposure, drawdown
setpoint r_E    = allowable exposure from Position Manager and regime state
output u_E      = new-entry scaling or entry freeze
```

This is conceptually closest to inventory-aware market making: when inventory
or correlated exposure is already high, the controller becomes more
conservative.  It cannot relax immutable drawdown, daily-loss, cash, or
concentration limits.

## Nifty and grid: what they should and should not do

Nifty is a useful market-regime reference, but it should be treated as an
**exogenous state input**, not as a target price for the 48 equities.  A useful
minimum Nifty state is:

- 1-minute return normalized by its own ATR/realized volatility;
- price relative to session VWAP;
- realised-volatility bucket; and
- directional alignment with the proposed trade.

The 48-symbol grid can supply an internal state even before Nifty is added:

```text
breadth = confidence-weighted signed PA directions across active symbols
sector_alignment = agreement inside the candidate's sector
```

Use Nifty only after it is loaded through the same manifest, timestamp,
warm-up, and hash-verification discipline as the stock data.  Do not fabricate
or back-fill missing index bars.

## Why ₹400/day must not be a setpoint

Daily P&L is affected by chance, fills, and realized loss sequence.  Driving a
PID from `₹400 − current_daily_P&L` would create the wrong feedback: a losing
morning increases urgency, size, or holding time.  This is exactly when the
system should become more selective.  The ₹400 figure remains a research
benchmark used after the session to evaluate the strategy.

## Evidence from the latest sealed Rev4 validation

The two validation finalists had positive gross P&L but negative after-cost P&L:

| Finalist | Gross P&L | Transaction costs | Net P&L |
|---|---:|---:|---:|
| 1 | ₹212.87 | ₹715.90 | −₹503.03 |
| 2 | ₹287.92 | ₹576.66 | −₹288.74 |

Target-hit exits were profitable; stop hits were the principal loss source;
time exits were close to cost-neutral.  This evidence supports first applying
the proposed loops as **shadow telemetry**, then measuring whether they
separate target-hit trades from stop-hit and time-exit trades.  It does not
support immediately enabling an adaptive controller in live or paper trading.

## Safe implementation sequence

1. Add a manifest-verified Nifty 1-minute stream, if available; otherwise use
   only internal 48-symbol breadth.
2. Create an immutable `RegimeSnapshot` at each decision timestamp, recording
   inputs, scores, and data identities.
3. Run the three proposed loops in shadow mode: record their proposed size,
   trailing-stop, and exposure actions, but do not alter orders.
4. Compare proposed actions against the sealed completed-trade ledger.  Primary
   measures: stop-hit count, time-exit net P&L after costs, target-hit retention,
   turnover, drawdown, and reconciliation.
5. If train-only evidence improves, promote one bounded change at a time to a
   separate training configuration.
6. Validate unchanged on the held-out validation period.  Keep the test period
   sealed until a configuration is selected.

## What not to do

- Do not turn grid/Nifty into an unbounded target multiplier.
- Do not let any PID output change immutable safety parameters.
- Do not set a daily P&L target as the PID reference.
- Do not tune all PID gains, regime weights, stops, targets, and timing filters
  together; that creates an un-auditable overfitting surface.
- Do not use future Nifty/grid observations when computing a current setpoint.

## Sources

1. Plessen, M. G., and A. Bemporad. “[Stock Trading via Feedback Control:
   Stochastic Model Predictive or Genetic?](https://arxiv.org/abs/1708.08857)”
   2017.
2. Dombrovskii, V., and T. Obyedko. “[Dynamic Investment Portfolio
   Optimization under Constraints in the Financial Market with Regime Switching
   using Model Predictive Control](https://arxiv.org/abs/1410.1136)” 2014.
3. Bielecki, T. R., and I. Cialenco. “[Robo-Advising in Motion: A Model
   Predictive Control Approach](https://arxiv.org/abs/2601.09127)” 2026.
4. Avellaneda, M., and S. Stoikov. “[High-frequency Trading in a Limit Order
   Book](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf)”
   *Quantitative Finance*, 2008.
5. Almgren, R., and N. Chriss. “[Optimal Execution of Portfolio
   Transactions](https://web.stanford.edu/~ashlearn/RLForFinanceBook/chapter9.pdf)”
   *Journal of Risk*, 2000.
6. QuantConnect. “[Algorithm Framework
   Overview](https://www.quantconnect.com/docs/v1/algorithm-framework/overview)”
   and “[Execution](https://www.quantconnect.com/docs/v1/algorithm-framework/execution)”
   documentation, accessed September 2026.
