# Intraday research protocol V1

## Objective

Find whether any **cost-adjusted, time-separated paper-replay edge** exists.
This is not an objective to locate one profitable trade or one profitable
month.  A single positive outcome is expected by chance after enough searches.

## Invariants

- Manifest-verified OHLCV only; no look-ahead.
- Conservative next-bar fills and fixed cost model.
- Gate16, cross-session policy, position/risk caps, and reconciliation stay
  outside the research search space.
- The train window may choose at most one frozen specification for a concept.
  Validation and test are not used to adjust it.
- A controller can derate entry risk, tighten a stop, or halt new risk. It
  cannot chase a daily P&L target, loosen stops, or enlarge base risk.

## Control architecture

| Loop | Causal feedback | Dynamic output | Prohibited output |
|---|---|---|---|
| Entry quality | Completed earlier outcomes, per symbol/side with pooled fallback | `0..1` admission/size derate | Raising risk above base |
| Trade path | Completed non-terminal held bars and frozen entry geometry | One-way stop ratchet | Stop loosening or terminal-bar ordering assumption |
| Portfolio risk | Current exposure, reserves, drawdown | New-risk derate/halt | Forced deployment when underexposed |

## Predeclared experiment queue

The experiments are sequential: later work starts only after the preceding
diagnostic establishes the causal question.

1. **Path decomposition (running):** classify the rejected breakout rule's
   losses as immediate adverse selection, stalled paths, or near-target
   reversal.  It changes no trade rule.
2. **If immediate adverse selection dominates:** test one fixed *entry timing*
   hypothesis, such as a completed-bar pullback/retest after breakout.  It must
   use the same stop, target, cost, and window protocol.
3. **If stalled paths dominate:** test one fixed *time/volatility feasibility*
   hypothesis, such as whether ATR-normalized target geometry is reachable
   before the allowed hold time.  It begins as an exit shadow, never a live
   stop rewrite.
4. **If near-target reversals dominate:** test one fixed break-even/ratchet
   shadow, using only causal bars.  Promote only if it improves untouched net
   results without choking target winners.
5. **If no entry hypothesis passes all windows:** stop strategy development
   for this signal family and report that no edge was found.  Do not calibrate
   gains to force a result.

## Promotion criteria

For each fixed hypothesis, all train, validation, and untouched test windows
must have at least 30 resolved candidates, positive gross outcome, and
positive net outcome under the same cost model.  Passing authorizes a separate
closed-loop *shadow* test only; it does not authorize live trading.

## Search accounting

Every attempted hypothesis, data windows, and result—positive or negative—is
recorded.  Any later aggregate performance claim must account for the number
of attempted variants before an out-of-sample claim is made.
