# Market engine benchmark and remediation

## Decision

There is no independently verifiable “top intraday trading bot” whose trade
rules can responsibly be copied into this engine. A claim that a bot produces
successful or assured intraday trades would be both unsupported by evidence and
inconsistent with Indian regulatory warnings about performance claims.

The useful comparison is with mature **trading-engine architectures**, not with
advertised bot returns. QuantConnect LEAN and NautilusTrader provide the most
relevant public references: they separate alpha generation, portfolio targets,
risk control, execution, and event reconciliation. Hummingbot is a useful
reference for inventory-aware market making, but it is built for crypto venue
microstructure and is not a template for an NSE one-minute directional equity
strategy.

## What the evidence says about the current engine

The 48-symbol, 1 September 2023 shadow test rejects the current reversal
hypothesis as an entry rule.

| Nested hypothesis | Candidates | Target-first rate | Net P&L/share |
|---|---:|---:|---:|
| Studies | 8,278 | 20.65% | -₹27,401.52 |
| Studies + price rate | 3,077 | 20.60% | -₹10,429.92 |
| Studies + price + volume rate | 1,205 | 21.66% | -₹4,013.87 |
| Studies + price + volume + phase | 1,060 | 20.66% | -₹3,578.34 |

The hypotheses are negative **before costs**. Therefore, neither PID tuning,
Newton convergence, nor a phase gate can honestly be presented as the route to
profitability. The only small positive observation is that price-plus-volume
reduced candidate count and modestly improved the hit rate. It is a research
lead, not a deployable rule.

## Benchmark comparison

| Capability | LEAN / Nautilus pattern | Current engine | Correction |
|---|---|---|---|
| Alpha | Emits a direction/confidence independent of sizing and execution | PA, studies, phase, and control experiments overlap | Produce one immutable `Candidate` event with features only |
| Portfolio construction | Converts alpha to target risk/weight | MPC, PyPortfolioOpt, and dynamic controls overlap | Build one target-risk object after alpha passes evidence checks |
| Risk | Alters targets before execution; has final authority | Safety logic exists but research controls are dispersed | Safety remains an independent, immutable supervisor |
| Execution | Uses an order lifecycle and fill/reconciliation events | Paper broker controls and shadow ledgers coexist | One event ledger must be authoritative for orders/fills/exits/costs |
| Feedback | Uses execution/fill/portfolio events; strategy feedback is time-sealed | Several PID experiments use different feedback definitions | V2 defines outcome-only entry feedback and bar-by-bar trade/portfolio feedback |
| Reality model | Fees, slippage, fill semantics are explicit | Cost model exists, but many studies use one-share overlapping outcomes | Every promotion requires shared-portfolio, quantity-aware, cost-inclusive replay |

LEAN explicitly routes Alpha → Portfolio Construction → Risk Management →
Execution, with risk-adjusted targets passed to execution.^1 NautilusTrader
uses an event-driven Data/Risk/Execution path with accepted, filled, cancelled,
rejected, and expired order events flowing through execution state.^2 These are
the parts to borrow.

## Architecture to retain

```text
Market data (sealed bars / later L1-L2 data)
  → Feature snapshot (PA, studies, dP/dt, dV/dt, phase telemetry)
  → Alpha candidate: immutable, no quantity, no broker side effects
  → Entry-quality evidence loop: cost-aware probability, completed outcomes only
  → Portfolio construction: target risk / quantity
  → Immutable safety supervisor
  → Execution state machine and paper broker
  → Event ledger and reconciliation
  → Completed outcome ledger
  └──────────────→ future candidates only
```

### Loop ownership

1. **Entry-quality loop — slow statistical feedback.**
   Its process variable is conservative target-before-stop probability; its
   setpoint is the cost-aware break-even probability. This is not a normal
   fast PID because outcomes are binary and delayed. It may veto or derate;
   it may never amplify a position.

2. **Trade-path loop — fast protective feedback.**
   Its process variable is current R progress against an entry-time frozen
   path. Its only actuators are a one-way stop ratchet or exit. It cannot
   loosen a stop, revise a historical fill, or use terminal-bar OHLC order.

3. **Portfolio-risk loop — fast supervisory feedback.**
   Its process variables are exposure, drawdown, pending reservations, and
   safety state. Its actuators are one-way new-risk derating, cancellation,
   quarantine, and halt. Daily P&L is a governance boundary, never an entry
   target that compels risk-taking.

4. **Phase / curve loop — sensor research only.**
   The Newton estimator can learn a per-symbol, per-direction phase reference
   from confirmed past turns. It must remain telemetry until it increases
   out-of-sample, cost-adjusted expectancy. It has not done so yet.

## Corrected implementation sequence

### Phase A — consolidate the event model

Create a single authoritative event schema:

`BAR_CLOSED`, `CANDIDATE`, `ENTRY_EVIDENCE`, `TARGET_RISK`, `ORDER_SUBMITTED`,
`FILL`, `STOP_RATCHET`, `EXIT`, `CANCEL`, `SAFETY_VIOLATION`, `RECONCILIATION`,
and `COMPLETED_OUTCOME`.

Every report must derive from these events, not from a mixture of broker state,
shadow objects, and recalculated summaries. The existing hash-linked Gate16
audit work provides the foundation.

### Phase B — establish an alpha research protocol

Do not tune a broad parameter grid. For every candidate family, run one
predeclared experiment across time-separated train, validation, and untouched
test periods. Require:

- positive gross expectancy;
- positive net expectancy after canonical costs/slippage;
- adequate sample count and confidence interval;
- no reconciliation or safety violation;
- stable result across symbols and months, not one-day pooled P&L.

The known danger is backtest overfitting: selecting among many historical
configurations can underperform when deployed.^3

### Phase C — promote only one evidence-backed alpha

The current phase/reversal family fails Phase B. Keep it observational.
The next alpha candidate should be expressed as a narrow, testable hypothesis,
for example **price/volume impulse continuation** or **price/volume reversal**,
not both at once. It needs a declared entry condition, stop geometry, target
geometry, and cost model before any PID attaches to it.

### Phase D — connect V2 only after alpha passes

`revision2_external/closed_loop_v2.py` is the new test contract. Wire it into
the paper orchestrator only after an alpha is shown to have out-of-sample gross
edge. At that point:

- outcome feedback derives the entry derate;
- partial pooling gives symbols individual behavior without 48 manual
  parameter files;
- the trade-path loop protects the open trade;
- the portfolio loop controls aggregate risk.

## Data gap

One-minute OHLCV cannot identify signed order flow, queue position, bid/ask
spread, or order-book imbalance. Therefore `dV/dt` is only a volume-change
proxy, not an electrical-current analogue or institutional order-flow signal.
Do not represent it otherwise. A future microstructure alpha requires
timestamp-aligned L1/L2 data and a new fill model; it should not be inferred
from OHLC bar ordering.

## What not to do

- Do not promise a fixed ₹50/₹100 daily target.
- Do not let a PID increase entry size after losses.
- Do not activate phase or Newton targets as a hard gate based on one day.
- Do not use a crypto market-making bot as an NSE directional-equity strategy.
- Do not calibrate safety limits or choose a winning configuration on the test
  period.
- Do not begin live execution until the paper engine passes sealed,
  event-derived, shared-portfolio validation.

## Immediate next build

Build **one shared event ledger adapter** for the external orchestrator and
connect the V2 entry-quality decision as `shadow_only`. Then replay a fixed
three-month training period, one-month validation period, and untouched
one-month test period for a single declared alpha hypothesis. The deliverable
is an evidence report, not an optimizer leaderboard.

## Sources

1. QuantConnect, [Algorithm Framework Overview](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview), accessed September 2026.
2. NautilusTrader, [Architecture](https://nautilustrader.io/docs/nightly/concepts/architecture/) and [Execution](https://nautilustrader.io/docs/nightly/concepts/execution/), accessed September 2026.
3. Peter Carr and Marcos López de Prado, [Determining Optimal Trading Rules without Backtesting](https://arxiv.org/abs/1408.1159), 2014.
4. SEBI, [Safer participation of retail investors in Algorithmic trading](https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html), February 2025; implementation timeline extension effective framework date noted in [September 2025 circular](https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html).
5. Zoltan Eisler and Johannes Muhle-Karbe, [Optimizing Broker Performance Evaluation through Intraday Modeling of Execution Cost](https://arxiv.org/abs/2405.18936), 2024.
