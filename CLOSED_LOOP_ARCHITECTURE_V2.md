# Closed-loop architecture V2

## Purpose

This is a research architecture for proving whether a trading decision has
edge before it is allowed to affect a paper order. It does not promise a
profit. The current evidence is that the tested reversal hypotheses are
negative before costs; therefore the architecture must prevent control loops
from disguising that fact by forcing more trades.

## The three control loops

| Loop | Time scale | Process variable | Setpoint | Actuator | Non-negotiable limit |
|---|---|---|---|---|---|
| Entry quality | Completed outcomes; slow | Conservative target-before-stop probability | Cost-aware break-even probability | Entry derate or shadow veto | Never raises risk; no P&L chasing |
| Trade path | Every held bar | Live R progress versus frozen entry path | Frozen per-trade progress path | Stop ratchet / exit only | Never loosen stop or use terminal-bar ordering |
| Portfolio risk | Every event | Exposure, drawdown, reserve state | Risk budget | New-risk derate / halt | Safety limits override all loops |

## Symbol-specific adaptation without 48 parameter sets

Each symbol receives a causal profile, not hand-tuned constants:

1. Rolling price/volume/phase measurements establish local references.
2. Completed trade outcomes update a symbol-side profile only after exit.
3. Sparse profiles are partially pooled with the side-wide population.
4. A minimum evidence threshold prevents a few trades from changing policy.
5. Each trade freezes its own response time, stop, target, and path at entry.

## Data flow

```text
Completed bars → feature/study sensors → candidate
                                      ↓
completed outcomes → entry-quality probability loop → bounded entry derate
                                      ↓
approved paper trade → frozen trade-path loop → stop tightening / exit
                                      ↓
portfolio state → portfolio-risk loop → bounded new-risk derate / halt
                                      ↓
completed trade → outcome ledger → next candidates only
```

## Why daily ₹50/₹100 is not the entry setpoint

Daily P&L is delayed, noisy, and affected by costs and random outcomes. Using
it to increase risk when below target makes a system chase losses. It belongs
only in supervisory governance: profit lock, loss halt, and reporting.

## Tester contract

The V2 tester verifies:

1. an entry decision cannot read outcomes at or after its timestamp;
2. cost-aware probability is the entry-quality setpoint;
3. entry quality can only derate, never amplify, a position;
4. trade-path control remains separate from entry-quality evidence;
5. portfolio safety remains authoritative.

## Promotion rule

No sensor, phase target, PID gain, or candidate rule is promoted into the
paper execution path until it improves a sealed, time-separated test set on
gross expectancy, cost-adjusted expectancy, and drawdown without violating
the tester contract.
