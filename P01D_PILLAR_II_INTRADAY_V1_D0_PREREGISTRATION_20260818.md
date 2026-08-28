# Pillar II Intraday V1 — D0 Preregistration

**STATUS: FROZEN BEFORE ANY TEST IS RUN.** Hypothesis, data boundaries,
causal timing, costs, entry/exit definition, hurdle methodology, and
pass/fail/inconclusive criteria only — no performance results, no code
run, no data touched. `PILLAR_II_INTRADAY_V1` is a new identity, entirely
independent of `PILLAR_I_INTRADAY_V1` (closed, FAIL — see
`P01D_PILLAR_I_INTRADAY_V1_CLOSURE_MANIFEST_20260818.txt`). This
document reuses the laboratory (universe, cost model, causal discipline)
built for Pillar I, not its failed hypothesis or its entry structure —
Pillar II bets on reversion of an exhausted move, not continuation of
strength, a genuinely different economic mechanism.

Every specification-integrity lesson from Pillar I's review is applied
here upfront rather than corrected after the fact: exact non-overlapping
discovery/confirmation calendar split, bounded search space, and an
implementable forward-quarantine counter — all frozen in this single
document, not split across addenda.

## 1. Economic hypothesis

**An objectively extreme, idiosyncratic intraday price dislocation — a
downside move large relative to the symbol's own recent volatility, not
merely tracking a broad market-wide selloff, and accompanied by climactic
volume — exhibits sufficient subsequent reversion toward its
pre-dislocation reference level to overcome realistic NSE intraday
trading costs.**

Distinct mechanism from Pillar I: this is a bet on mean-reversion after
exhaustion, not trend persistence. No shared assumption, no shared
feature family, no shared entry direction (Pillar I enters on strength;
Pillar II enters against a sharp drop).

## 2. Universe — frozen, reused for laboratory comparability only

Same 19-symbol universe as Pillar I (not because it's the "right"
universe — reused so the infrastructure, not the hypothesis, is shared):
`AXISBANK, BAJFINANCE, BHARTIARTL, HDFCBANK, HINDUNILVR, ICICIBANK, INFY,
ITC, KOTAKBANK, LAURUSLABS, LT, MARUTI, NTPC, RELIANCE, SBIN, SUNPHARMA,
TATASTEEL, TCS, ZYDUSLIFE`, plus NIFTY 50 for the idiosyncratic-vs-
systematic filter (§5).

## 3. Causal timing — frozen, same discipline as Pillar I

- **Decision clock**: completed 5-minute bars only, no same-bar look-ahead.
- **Execution clock**: 1-minute. Entry at the first available 1-minute
  bar open at-or-after a qualifying completed 5-minute dislocation bar.
- **One event per symbol per day**: first qualifying dislocation only.
- **Hard stop always active** from entry.

## 4. Dislocation definition — frozen family, value chosen at D1

```
5-min bar return Z-score = (bar_return - trailing_mean_return)
                            / trailing_stdev_return
```
computed over a trailing window of the symbol's own completed 5-minute
returns (window length is a D1 parameter — see §7). **Dislocation
qualifies when Z <= a fixed extreme negative threshold** (D1 parameter).
This is the intraday analog of V2-C's `event_z20 < -2.0` daily-dislocation
trigger, at 5-minute resolution — related in spirit, computed
independently; Pillar II does not consume V2-C's classifier or its
outcome-resolution work, and V2-C does not consume Pillar II's dislocation
definition. Two separate lanes (R0 §1), not a shared component.

## 5. Context features — frozen family, values chosen at D1

- **Idiosyncratic filter**: the stock's 5-minute return minus NIFTY 50's
  concurrent 5-minute return must itself be an extreme negative excess
  move (a fixed threshold, D1 parameter) — excludes dislocations that are
  simply the whole market dropping, keeping the hypothesis about
  stock-specific exhaustion, not systematic risk.
- **Climactic volume**: the dislocation bar's volume vs. trailing median
  volume at the same 5-minute time-of-day slot (same time-of-day-adjusted
  method as Pillar I, trailing 10 prior occurrences, fixed — not a new
  search parameter) exceeds a threshold (D1 parameter).
- **Distance from session VWAP**: price must be below session VWAP by at
  least a minimum margin (D1 parameter) — confirms a real dislocation,
  not noise near a reference level with nothing to revert to.

**Frozen rule, matching Pillar I's discipline**: all three context
features plus the dislocation trigger combine via a single frozen
combination form (AND-gate across all four, or a scored-sum requiring
3-of-4) — chosen once per D1 candidate, not swept.

## 6. Entry — frozen

**LONG** at the first 1-minute bar open at-or-after the qualifying
dislocation bar (betting on reversion/bounce, opposite direction logic
from Pillar I's trend-continuation entry). No chase, no delay — R6/R7
already established that delaying a valid causal entry destroys value
relative to taking it immediately.

## 7. Reversion destination and stop geometry — frozen family, D1 chooses

- **Stop**: below the dislocation bar's low, offset by a volatility-scaled
  buffer (`stop_mult x ATR14`, same mechanism as Pillar I, D1 parameter)
  — protects against the "continuing deterioration" failure mode this
  hypothesis explicitly risks.
- **Target — two candidate families, D1 picks one per named variant**:
  - **(a) Fixed R-multiple** — same mechanism as Pillar I, for direct
    comparability.
  - **(b) Session-VWAP reclaim** — an economically-motivated destination
    specific to this hypothesis: exit when price closes back at or above
    the session VWAP at the time of entry (captured once, not
    recalculated forward, matching P02 spec's "prior_close_equity"
    capture-once discipline for anchored reference values). If VWAP is
    never reclaimed, exits via the stop or the time exit (§8), whichever
    comes first.
  D1's five named variants may mix (a) and (b) across candidates — this
  is the equivalent of Pillar I's "combination form" free choice, applied
  to the target instead.

## 8. Time exit — frozen, same as Pillar I

Maximum holding period to end-of-session on entry day. No overnight
carry.

## 9. Risk convention and cost model — frozen, reused unchanged

₹20,000 capital, 0.5% risk fraction, `qty = min(risk_qty, capital_qty)`,
the real Zerodha/NSE fee model (`zerodha_nse_intraday_costs()`), 0/1/2/5
bps one-way slippage stress — identical to Pillar I, for direct
comparability and because it's already validated infrastructure.
**Carried forward from Pillar I's closure**: report the capital-bound vs.
risk-bound trade fraction explicitly in D1 output — a real portfolio-
economics finding from Pillar I, not a reason to change ₹20,000 here.

## 10. Search freedom — bounded, frozen now

**Maximum 5 preregistered named variants**, chosen and listed before any
of them is run, one-factor-at-a-time from a single baseline (same
discipline as Pillar I's D0-A1, folded in upfront here rather than
requiring a correction pass). Every evaluated variant counts as a
research trial. Exactly one winner, by the frozen metric below, becomes
D1-FREEZE. No new feature, no sixth variant, no small tweak to any of the
five after seeing a result.

**Selection metric**: net expectancy per trade after real fees, 0bps
slippage, on the D1 discovery allocation only (§11).
**Tie-break** (within ₹5/trade): lower variance between the two discovery
sub-blocks, then AND-gate over scored-sum, then fixed-R-multiple target
over VWAP-reclaim (the simpler mechanism), then shorter lookback windows.

The five named candidates themselves are declared in a separate,
timestamped candidate manifest before D1 runs — the same pattern as
`PILLAR_I_D1_CANDIDATES_20260818.md` — not invented inside this D0.

## 11. Discovery / confirmation / D2 / D3 — frozen, exact dates, non-circular from the start

```
D1 DISCOVERY (5-variant comparison and D1-FREEZE selection):
  May-Jun block: 2026-05-15 .. 2026-06-12  (20 trading days)
  Jun-Jul block: 2026-06-15 .. 2026-07-14  (21 trading days)

CONFIRMATION (reserved, zero role in candidate selection, evaluated
  exactly once after D1-FREEZE, no tuning permitted):
  Jul-Aug block: 2026-07-15 .. 2026-08-13  (22 trading days)

D2 internal robustness (non-blind, informative only, never proof):
  2026-03-02 .. 2026-05-14

D3 true forward holdout:
  2026-08-18 onward — the same forward window Pillar I's D3 would have
  used. Running two independent hypotheses' quarantined forward counters
  over the same calendar period is not a conflict — each has its own
  frozen rule, evaluated independently, exactly as V11/P02's multiple
  live tracks already run in parallel over one shared forward period.
```

**All of Mar 2 - Aug 14 is previously-exposed historical research data**
(same honest classification as Pillar I's corrected D0) — suitable for
D1/confirmation/D2, never a blind holdout regardless of outcome. Only D3
can produce a final validated PASS.

## 12. D3 quarantine — the blind eligibility counter, correct from the start

After D1-FREEZE, a narrow counter may run continuously on post-2026-08-18
data, computing and exposing **only**:
```
- number of elapsed eligible NSE trading days since 2026-08-18
- number of qualifying Pillar-II-Intraday-V1 entry signals fired
```
**Never exposed until both thresholds are met**: trade P&L, exit result,
target/reversion outcome, MFE, MAE, win/loss, future price path beyond
the entry bar, aggregate expectancy, equity curve, per-symbol performance.
Status display, identical shape to Pillar I's:
```
D3 ACCRUAL STATUS
Trading days:       0 / 60
Qualifying entries: 0 / 15
D3 remains SEALED
```
**Minimum-evidence requirement, fixed now**: `days >= 60 AND entries >= 15`
(the later of the two governs) before D3 may be evaluated for a verdict.
Same numbers as Pillar I's, for consistency across the RRME program — not
re-derived per hypothesis without reason.

## 13. Pass / Fail / Inconclusive criteria — frozen

- **Discovery/confirmation/D2 (non-blind) PASS**: net expectancy clears
  the hurdle-rate threshold (computed the same way as R11/Pillar I: mean
  real fee + mean slippage per trade from the sample's own realized
  turnover) on discovery, replicates on confirmation, replicates on D2 —
  evaluated per-block, never pooled. Result: **eligible for D1-FREEZE**,
  not yet validated.
- **FAIL**: net expectancy negative or fails the hurdle on confirmation
  or D2. Candidate closed — no rescue, matching Pillar I's and rs15's
  precedent exactly.
- **D3 PASS/FAIL/INCONCLUSIVE**: the only stage that can validate Pillar
  II. INCONCLUSIVE (too few qualifying trades) is an accepted, named
  outcome. The minimum-evidence bar (§12) is never lowered because early
  D3 sessions look promising or discouraging.

**No threshold rescue at any stage after its data is opened.**

## Release boundary

No code written. No data loaded. No experiment run. `LIVE_TRADING_ENABLED`
untouched. D1 begins only when explicitly opened, with its own named
5-candidate manifest written first.
