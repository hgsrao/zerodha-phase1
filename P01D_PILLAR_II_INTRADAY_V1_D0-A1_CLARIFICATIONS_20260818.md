# Pillar II Intraday V1 — D0-A1 Clarifications

**STATUS: FROZEN. Appended to, does not rewrite, the frozen D0.** Resolves
five items found on line-by-line review before D1's candidates are named:
one real sequencing contradiction, one fee double-counting risk, one
underspecified exit mechanism, two warm-up ambiguities. No performance
numbers appear here. D1 candidates are still not named — that happens
only after this addendum, as its own explicit next step.

## 1. D1-FREEZE sequencing — corrected, no contradiction

**The bug**: D0 §10 said the discovery winner becomes D1-FREEZE; §13 said
discovery+confirmation+D2 replication makes a candidate "eligible for
D1-FREEZE." Those can't both be true — confirmation can't determine
eligibility for a freeze that already happened before confirmation opens.

**Corrected sequence, frozen**:
```
D1 discovery (5 named variants, May-Jun + Jun-Jul)
        ↓
select ONE winner by the frozen metric (§2 below)
        ↓
D1-FREEZE — exact rule, parameters, code hash, locked immediately
        ↓
CONFIRMATION (Jul-Aug) — evaluated once, no tuning, on the frozen rule only
        ↓
D2 (Mar-May) — evaluated once, no tuning, on the frozen rule only
        ↓
BOTH confirmation and D2 pass  → D3-ELIGIBLE
EITHER confirmation or D2 fails → candidate CLOSED, never modified
```
D0 §13's "eligible for D1-FREEZE" language is replaced: discovery PASS
produces D1-FREEZE directly; confirmation/D2 PASS produces **D3
eligibility**, a different and later gate. A FAIL at confirmation or D2
closes the already-frozen candidate exactly as it stands — it is not
sent back for a different discovery winner, and D1 is not reopened.

## 2. Hurdle accounting — one framework, no double-counting, numerical margin

**The bug**: the primary selection metric was net expectancy after real
fees (already fee-net), but §13 additionally required that same net
figure to clear a hurdle defined as mean-fee-plus-slippage — subtracting
fees a second time from an already fee-net number.

**Corrected, net framework only (B), frozen**:
```
Primary D1 selection metric (unchanged):
    mean net P&L/trade, real fee model, 0bps slippage stress.

Viability requirement (the numerical margin, fixed now):
    that same net figure must be POSITIVE at 0bps, AND must remain
    POSITIVE when the identical real fee model is combined with 2bps
    one-way slippage stress (applied to entry+exit notional, same
    convention as R9/R11).

    2bps is the frozen margin threshold — not an arbitrary new number:
    it is the "realistic modest stress" level already used as the
    project's own reference point in R9/R11's slippage-stress work,
    reused here rather than invented for this document.

Reported, not gating: net expectancy at 1bps and 5bps, for context only.
```
No fee or slippage cost is subtracted twice anywhere in this accounting.
"With margin" is no longer an adjective decided after seeing a result —
it is exactly "survives 2bps slippage stress on top of real fees,"
fixed before D1 runs.

## 3. VWAP-reclaim exit — exact causal execution semantics, frozen

- **VWAP reference**: session VWAP computed from completed data only,
  through the qualifying 5-minute signal bar, **captured once and frozen**
  at that point — never recalculated forward, same "capture once" rule
  D0 already used for the reference recovery level's discipline.
- **VWAP formula**: session-reset cumulative `Σ(HLC3 x volume) / Σ(volume)`,
  reset at 09:15 each trading day — the identical formula and reset
  convention already used in the existing P01D research harness's own
  session-VWAP computation, reused for consistency, not reinvented.
- **Reclaim clock**: evaluated on **completed 5-minute bar closes**, not
  1-minute closes — consistent with D0's own decision clock (every other
  decision in this D0 is made on completed 5-minute bars; the exit
  decision uses the same clock rather than a finer one smuggled in for
  just this one mechanism). Once a completed 5-minute close is at or
  above the frozen VWAP reference, execution occurs at the next available
  1-minute bar open — identical execution-lag convention to entry.
- **Stop/target collision**: the hard stop is touch-based (intrabar low),
  the VWAP-reclaim is close-based (5-minute bar close) — if a stop-touch
  and a same-window reclaim condition are both plausible, **stop-first
  priority applies**, the same conservative convention already used
  throughout this project (never credit the favorable exit when ordering
  can't be proven).

## 4. Warm-up rules — explicit, no silent partial windows

- **Z-score (dislocation trigger)**: trailing mean/stdev are computed from
  the prior N completed 5-minute returns, **excluding the current
  (dislocation-candidate) bar's own return**. The current bar is scored
  against that historical reference, never included in the reference
  distribution it's being compared to.
- **Climactic volume**: unchanged 10-prior-same-slot requirement, with
  the fallback made explicit: **fewer than 10 valid prior observations
  at that time-of-day slot → feature unavailable → no qualifying signal
  that day for that symbol.** No partial-window median, no relaxed
  window size.

## 5. D1 is an independent-event expectancy experiment, not a portfolio curve

**Frozen classification**: D1 evaluates each qualifying dislocation event
independently for expectancy. Concurrent qualifying events across
different symbols on the same day **do not imply simultaneous
deployability of multiple ₹20,000 positions** — each trade is sized and
evaluated as if it were the only position, matching every prior R6-R11/
Pillar-I convention. **Aggregate D1 P&L is a sum of independently-
evaluated hypothetical trades, not a realizable one-position portfolio
equity curve.** The question D1 answers is narrower and more useful at
this stage: does the dislocation-reversion event itself carry
expectancy? Which symbol actually gets to trade on a day with multiple
qualifying events is a cross-sectional selection question deferred to
the real P01D single-position occupancy/authorizer, not solved here —
introducing a ranking rule now would answer a question D1 isn't asking
yet.

## Freeze certificate

D0 and this addendum (D0-A1) together are hashed as the Pillar II
preregistration package once both are finalized — same pattern as
Pillar I's manifest. D1's five named candidates are declared next, as
their own timestamped document, before any of them is run.

## Release boundary

No code written. No data loaded. No experiment run. `LIVE_TRADING_ENABLED`
untouched. D1 remains on hold pending review of this addendum.
