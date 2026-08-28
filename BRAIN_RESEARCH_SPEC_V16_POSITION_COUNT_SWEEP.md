# V16 — Position Count Sweep

Question: before rewriting the engine to hold more than one position at a
time, how many concurrent positions actually makes sense? Answered
empirically - the same validated 17-fold anchored walk-forward, real
Zerodha delivery costs, run once per candidate position count - not from
portfolio-theory folklore.

**Note on methodology:** the first run of this sweep used the wrong data
loader (`external_model_comparison.load_universe()`, the original
2023-2026/20-symbol set) against the 17-fold extended windows, which need
the 2016-2026/19-symbol set. Most folds had no real data underneath them
and the result looked like a real, very different (and wrong) answer
instead of erroring loudly. Caught before being reported, fixed to use
`run_extended_walk_forward.load_extended_universe()`, and pinned with a
regression test that reproduces V14's already-validated 11/17 result
exactly at positions=4 - this specific mistake cannot recur silently again.

## Hard constraint, checked first

The validated universe is 19 symbols across **11 distinct sectors**
(POLYCAB's exclusion removes the 12th, ELECTRICAL). A sector-controlled
position count above 11 is mathematically impossible without duplicating a
sector. 15 or 20 positions, as discussed, cannot happen with this universe
at all.

## Result (2026-08-14, real 2018-2026 data, real costs)

| Positions | Capital/position | Profitable folds | Mean daily return | Bootstrap 95% CI | P(non-positive) |
|---|---|---|---|---|---|
| 2 | ₹50,000 | 11/17 | **+0.093%** | [0.63, 3.25] | 0.12% |
| 3 | ₹33,333 | 11/17 | +0.093% | [0.70, 3.23] | 0.10% |
| **4 (validated)** | ₹25,000 | 11/17 | +0.083% | [0.58, 2.89] | 0.06% |
| 5 | ₹20,000 | 11/17 | +0.076% | [0.50, 2.69] | 0.16% |
| 6 | ₹16,667 | 11/17 | +0.066% | [0.34, 2.40] | 0.32% |
| 8 | ₹12,500 | 12/17 | +0.065% | [0.42, 2.30] | 0.20% |
| 10 | ₹10,000 | 12/17 | +0.058% | [0.33, 2.14] | 0.36% |
| 11 (sector ceiling) | ₹9,091 | 12/17 | +0.055% | [0.27, 2.04] | 0.54% |

Every single count tested stayed profitable with an entirely positive
bootstrap CI - the edge does not collapse or flip negative anywhere in
this range. That's the reassuring part.

## The answer, and it's the opposite of "go bigger"

**Mean daily return declines monotonically as position count rises** -
roughly 0.093% at 2 positions down to 0.055% at 11. More positions doesn't
strengthen the strategy here; it dilutes it. The reason is mechanical: with
only 19 names to rank, going past the top 4-6 forces the strategy to
include progressively weaker-momentum names just to fill slots - and this
sweep already includes real transaction costs, so the added drag from
smaller per-position allocations (established in V14: smaller trades pay a
proportionally larger flat DP fee) is honestly reflected in the numbers,
not hidden.

Position count does buy a small amount of consistency - 12/17 profitable
folds at 8+ positions versus 11/17 at 4 or fewer - but that's a modest
smoothing effect purchased at the cost of a materially lower average
return and much smaller, cost-vulnerable individual positions.

## Recommendation

**Do not go to 10, 15, or 20 - the data doesn't support it, and 15/20 are
impossible with this universe anyway.** The already-validated **4 positions**
sits close to the best point in this tradeoff: near the top of the return
range, a tight positive bootstrap interval, and individual position sizes
(₹25,000) still comfortably above where the flat-fee cost drag starts
biting hard. Going to 2-3 positions shows a marginally *better* raw number
but concentrates ₹33,000-50,000 into a single name each - a real
single-stock risk this daily-return-based metric doesn't fully capture on
its own. If anything is worth reconsidering, it's whether 4 should become
5-6 for a bit more consistency, not larger - not a case for a 10+ position
rewrite target.

## Release boundary

A research/sizing question, not a production readiness result. Does not
authorize enabling live trading, unlocking Gate 4, or building the engine
rewrite - it answers one input to that decision. `LIVE_TRADING_ENABLED`
remains false; no broker calls were made producing this report.
