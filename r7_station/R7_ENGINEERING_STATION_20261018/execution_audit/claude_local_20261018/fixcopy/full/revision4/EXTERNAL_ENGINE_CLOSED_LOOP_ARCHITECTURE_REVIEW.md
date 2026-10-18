# External Engine Closed-Loop Architecture Review

## Decision

Do not add another PID controller yet.  The external engine already has useful
feedback mechanisms, but it currently mixes three different ideas under the
word “PID”:

1. a confidence-deviation throttle at entry;
2. a genuinely stateful exit-protection controller; and
3. a delayed chart-study performance reweighter.

Only the second is a direct control loop around a state which the controller
can change.  The first should be renamed and measured before it is tuned; the
third should remain a separate, delayed-learning mechanism.  Grid/Nifty/VIX
must remain a timestamp-sealed observation source until it proves incremental
value.

## Present System: What Is Connected

```text
1-minute stock bars ─► PA ─► ID ─► MPC plan ─► safety/portfolio limits ─► P01D ─► paper fill
                       │          │                                                │
                       │          └─ confidence-deviation throttle                │
                       │                                                           ▼
                       └─ Chart Studies ─► exit-controller study track      position / trade ledger
                                                                          │            │
15-minute Nifty + VIX ─► Grid shadow observation ───────────────────────┘            │
                                                                                       ▼
                                                    completed trade / realised P&L / costs
```

### What works now

| Component | Measurement | Current output | Assessment |
|---|---|---|---|
| MPC entry PID | ID confidence versus a per-symbol rolling baseline | 0.3–1.0 entry timing/size multiplier and a tiny entry-price adjustment | Bounded adaptive throttle, not a true closed loop. |
| Continuous exit controller | PA confidence, Chart confidence, ATR, price extreme and time held | Monotonic trailing-stop ratchet and saturation exit | A real position-state feedback controller. |
| Chart-study controller | Delayed hit rates for Ichimoku, Bollinger, Stochastic and VWAP | Relative study weights | Causal delayed learning, provided the outcome horizon remains fixed and logged. |
| Grid provider | Causally prior Nifty/VIX bars plus symbol prices | Shadow observation | Correctly passive. September 2023 SUNPHARMA: 210 available observations, only 3 synchronized. |
| Safety controls | Drawdown, exposure, trading window, broker/fill assumptions | Reject, cap or halt | Must remain outside every adaptive controller. |

## The Central Correction

A control loop needs all four links below:

```text
setpoint ─► controller ─► actuator ─► plant/state ─► measurement ─┐
     └─────────────────────────────────────────────────────────────┘
```

The current entry PID measures confidence and adjusts the next order's size,
but an order-size adjustment does not cause the PA/ID model to produce a
higher future confidence.  It therefore has no demonstrated causal return
path from actuator to measured confidence.  Calling it an “entry-quality
closed loop” overstates what it is.

The existing exit controller is closer: its output changes the stop boundary;
the later position state can then trigger that boundary.  Even there, the
confidence baselines are references for *tightening*, not economic profit
targets.  That is acceptable if it is explicitly treated as a protective,
one-way actuator.

## Correct Future Data Flow

```text
At decision time t (only data known at t)
  sealed bars + causal 15m context
      -> PA prediction
      -> ID confidence / uncertainty
      -> MPC plan: entry, stop, target, hold horizon, planned risk R
      -> Portfolio risk-budget throttle
      -> immutable safety interlocks
      -> order intent

At fill time t+1
  actual fill + costs + slippage
      -> execution-quality control / Gate16 remediation
      -> position state

For every held bar
  price, ATR, elapsed bars, realised R, PA and Chart Studies
      -> exit-protection controller
      -> only tighten / reduce risk; never widen a stop

At position close
  net P&L, R multiple, MAE/MFE, costs, slippage, exit reason
      -> immutable completed-trade ledger
      -> delayed scorecards / model diagnostics
      -> future calibration dataset, never same-trade intervention
```

The ordering mirrors modular engines: alpha creates a forecast, portfolio
construction creates a target, risk may only reduce/replace that target, and
execution acts on the risk-adjusted result.  This is the useful design lesson
from [QuantConnect LEAN’s Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview),
not a reason to copy its portfolio-rebalancing models into an intraday
trade-plan strategy.  Its documented event ordering similarly places risk
adjustment before execution. [LEAN engine event flow](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)

## Three Controllers Worth Having

### 1. Portfolio risk-budget throttle — build first, in shadow

This is the only new controller recommended initially.

| Item | Definition |
|---|---|
| Controlled variable | `committed_stop_risk / current_equity`, summed over open positions and pending orders. |
| Setpoint | A governance-approved risk budget, for example 1–2% of equity. It is **not** capital deployed and is never ₹400/day. |
| Actuator | Cap the proposed quantity of a new order; output range `[0, 1]`. |
| Safety rule | It may reduce or reject a new order, never enlarge one. Hard exposure/drawdown caps still override it. |
| Feedback timing | Every timestamp after fills, exits, cancellations and mark-to-market. |

This is a genuine loop: reduced quantity alters committed risk, which is then
measured at the next clock tick.  Treat it initially as a bounded proportional
throttle, not a full PID.  Integral action is dangerous here because a quiet
market would accumulate “underexposure” and later pressure the engine to take
weak trades.

### 2. Exit-protection trajectory — shadow before changing exits

The correct exit reference is a planned **R-multiple protection path**, not a
rupee-profit goal:

```text
R(t) = signed (current price - entry price) / initial stop distance
protected_R(h) = pre-declared nondecreasing function of bars held h
```

The controller can compare realised `R(t)` with a conservative planned path
and tighten only when protection is behind plan.  It cannot move a stop away
from price, extend `max_hold_bars`, or override an exit safety gate.  This is
conceptually related to time/risk trade-offs in
[Almgren–Chriss](https://web.stanford.edu/~ashlearn/RLForFinanceBook/chapter9.pdf),
but that model is for executing a known inventory; it must **not** be claimed
as a proof that an intraday alpha exit will be profitable.

### 3. Delayed model-quality supervisor — not a bar-by-bar PID

For PA, ID and Chart Studies, the useful feedback is calibration quality:

```text
predicted confidence bucket -> later realised hit rate / net R after costs
```

Update this only after the preset evaluation horizon or a trade close.  Its
output can lower the future eligibility budget for an unreliable regime or
symbol; it must not rewrite historical confidence or boost a weak signal into
an entry.  This preserves the existing rule that Chart Studies may not be
averaged into PA confidence.

## Grid Controller: Explicitly Separate It

The real Grid adapter is now causally aligned and sealed, but the September
2023 SUNPHARMA shadow run found only 3 synchronized observations out of 210
available candidates.  It has no demonstrated stable relationship to net R,
slippage, drawdown or exit quality.

For now Grid should emit only a **regime tag**:

```text
{available, synchronized, reason, phase_delta, Nifty close, VIX close}
```

The next comparison is conditional and out-of-sample:

| Comparison | Minimum evidence before promotion |
|---|---|
| Grid synchronized versus not synchronized | Enough completed trades in both groups; compare net R after costs and drawdown. |
| High VIX versus normal VIX | Same strategy and parameters; no threshold tuning during comparison. |
| Phase bucket versus outcome | Predefined phase buckets; report counts, not only the best bucket. |

Until then, do not use Grid to reject entries, widen/tighten stops, or change a
PID setpoint.

## Ten-Step Implementation Sequence

1. **Freeze a baseline.** Tag current external-engine configuration, dataset
   hashes, safety contract and execution model.  No PID or Grid parameter
   change during baseline collection.
2. **Create one authoritative event schema.** Record decision, plan, risk
   budget, order, fill, stop update, exit, cancellation and completed trade;
   each event must carry timestamp, symbol and run identity.
3. **Add controller telemetry only.** At each controller update record
   setpoint, measurement, error, P/I/D terms, output, clamp/saturation state
   and the exact action taken.  This is observation-only.
4. **Rename the current entry PID in reports.** Call it a
   `confidence_deviation_throttle` until a measurable plant and causal return
   path are proven.  Do not remove it yet.
5. **Implement the risk-budget throttle in shadow.** Calculate proposed
   quantity versus allowed quantity from stop-risk, but submit the original
   quantity.  Report what it would have changed.
6. **Implement the R-path exit reference in shadow.** Calculate the proposed
   ratchet only; retain the present exit controller.  Compare exit reasons,
   MAE/MFE and net R on the same sealed periods.
7. **Build delayed outcome scorecards.** For every symbol/regime/confidence
   bucket record sample count, hit rate, average net R, cost and slippage.
   Do not use scores with too few observations.
8. **Run train/validation/test comparisons.** Select at most one controller
   promotion based on training; confirm it once on validation; leave the test
   set untouched until the design is frozen.
9. **Promote one controller only if it passes.** Required: exact
   reconciliation, no safety regression, bounded behaviour, and improvement
   after costs on validation—not merely better training P&L.
10. **Only then calibrate gains.** Calibrate the gains around a fixed,
   documented setpoint. Never calibrate safety caps, daily-profit targets, or
   the decision to enable Grid.

## Required Acceptance Tests Before Any Promotion

- A time-sealed test proves every controller uses only values available at its
  timestamp.
- A monotonicity test proves exit protection cannot widen a stop.
- A risk test proves the portfolio throttle can never increase a proposed
  order quantity.
- A state-isolation test proves one symbol’s PID history cannot affect another
  symbol.
- An event-replay test reproduces controller outputs from the ledger exactly.
- A safety test proves Gate16, cross-session rejection and drawdown halts
  override every controller output.

## What Not To Implement

- Do not use ₹400/day as any controller setpoint.
- Do not use capital utilisation alone as the controlled variable.
- Do not combine PA and Chart confidence into one number.
- Do not give Grid a hard gate, stop multiplier or target multiplier now.
- Do not allow a PID to increase risk after losing trades or an idle period.
- Do not tune controller gains before telemetry demonstrates unsaturated,
  meaningful movement.

## Source Notes

1. [QuantConnect LEAN Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview): separation of alpha, portfolio construction, risk management and execution.
2. [QuantConnect LEAN engine event flow](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine): data update, alpha, construction, risk, then execution sequencing.
3. [NautilusTrader concepts](https://nautilustrader.io/docs/latest/concepts/): event-driven simulation, execution reconciliation, event sourcing and deterministic simulation as useful engineering targets.
4. [Almgren & Chriss, Optimal Execution of Portfolio Transactions](https://web.stanford.edu/~ashlearn/RLForFinanceBook/chapter9.pdf): risk/time execution trade-off; applicable as an analogy for a predeclared protection trajectory, not an alpha proof.
