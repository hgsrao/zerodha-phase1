# Pillar I Intraday V1 — D0-A1 Methodological Addendum

**STATUS: FROZEN. Appended to, does not modify, the frozen D0.** Bounds
D1's search freedom and defines the true forward holdout (D3) quarantine.
No performance numbers appear in this document either. Read alongside
`P01D_PILLAR_I_INTRADAY_V1_D0_PREREGISTRATION_20260818.md` (§5/§9
corrected 2026-08-18 to match this addendum's evidence ladder).

## Corrected evidence ladder

```
D0        = preregistration (frozen 2026-08-18)
D0-A1     = this document — bounded D1 search protocol + quarantine rule
D1        = discovery on previously-exposed historical data (non-blind)
D1-FREEZE = exact final Pillar-I-Intraday-V1 rule, chosen once
D2        = internal robustness check on the old rs15-holdout blocks
            (useful signal, explicitly NON-BLIND — informative, not proof)
D3        = forward holdout, 2026-08-18 onward, quarantined during D1/D2
            → the only stage that can produce a final PASS/FAIL/INCONCLUSIVE
```

## 1. Exact permitted feature set — no additions after D1 begins

Only the four families frozen in D0 §4 are permitted inputs to the entry
rule:

1. Relative strength vs. NIFTY 50 (short window + longer window)
2. Volatility-normalized momentum (trailing return / trailing ATR)
3. Participation (completed-bar volume vs. trailing median for the same
   intraday time-of-day slot)
4. Breakout/persistence structure (price vs. trailing high/range)

**No fifth feature, no interaction term, no feature not on this list may
be introduced at any point after D1 begins** — not after a discovery
result looks weak, not after confirmation, not ever for this D0/D1 cycle.
A genuinely new feature idea is a new D0, not an amendment to this one.

## 2. What D1 is allowed to choose — the only free parameters

- The exact lookback bar-counts for the two relative-strength windows
  (one predeclared short-window choice, one predeclared longer-window
  choice — see §3's bounded grid).
- The volatility-normalization lookback for ATR.
- The participation threshold (volume ratio cutoff).
- The breakout/persistence lookback and threshold.
- **The exit R-multiple pair** (stop distance multiple, reward:risk
  target multiple) — D0 §6 left this open; this addendum answers it: it
  **is** a D1 parameter, chosen from the bounded grid below, not swept
  freely.
- How the four feature families combine into one entry rule: a simple,
  predeclared combination form only — either (a) all four must
  simultaneously qualify (AND-gate), or (b) a fixed point-scoring sum
  with a fixed pass threshold. **D1 picks one of these two combination
  forms before looking at any result**, as part of the same single
  candidate-selection pass in §3 — not both, not a third form invented
  later.

## 3. Bounded candidate/search space — the actual anti-overfit mechanism

D1 does not iteratively tune. It evaluates a small, fully-enumerated,
predeclared grid **exactly once**, on the discovery block only, and locks
the winner before touching confirmation or D2 data:

```
Short RS window:      {6 bars, 12 bars}                      (2 choices)
Long RS window:        {30 bars, 60 bars}                     (2 choices)
Volatility ATR lookback: {14 bars}                             (1 choice — fixed, not searched)
Participation threshold: {1.5x, 2.0x median}                   (2 choices)
Breakout lookback:      {20 bars, 40 bars}                     (2 choices)
Combination form:       {AND-gate, scored-sum}                 (2 choices)
Exit stop multiple:     {1.0x ATR, 1.5x ATR}                   (2 choices)
Exit reward:risk:       {1.5R, 2.0R}                            (2 choices)
```

Maximum candidate variants: the full cross-product is capped by
construction at **≤ 128** (2×2×1×2×2×2×2×2), but **D1 does not evaluate
all of them** — D1 selects and freezes ONE reasoned starting combination
before running anything, matching D0's original "one frozen rule, not 27
swept combinations" instruction. The grid above exists to bound what
"reasoned" is allowed to mean if any comparison is made at all: if more
than one combination is actually run and compared, **the maximum is 5
named variants**, chosen and listed before any of them is run, evaluated
once each, winner selected by §4's single metric — never an open-ended
sweep, never "try a few more and see."

## 4. Selection metric and tie-break — frozen

**Primary metric**: net expectancy per trade after real fees (the same
`zerodha_nse_intraday_costs()` model as D0 §7), at 0bps slippage stress,
on the discovery block only.

**Tie-break** (only if two candidates' net expectancy is within noise of
each other, defined as within ₹5/trade): prefer the candidate with the
**smaller** cross-regime variance across the three discovery sub-blocks
(May-Jun/Jun-Jul/Jul-Aug) — matching the consistency criterion that
mattered most in the R7 exit-comparison work, not raw pooled P&L alone.
If still tied, prefer the candidate using fewer free parameters (AND-gate
over scored-sum, shorter lookbacks over longer).

## 5. Quarantine rule for D3 — frozen

From 2026-08-18 forward:
- Raw 1-minute/5-minute OHLCV and NIFTY 50 index data **may** be
  collected as trading days occur (a manual, deliberate pull each time a
  session picks this up — via the existing read-only historical
  downloader, same as every other data pull in this project; **not** an
  autonomous scheduled/background process started without separate,
  explicit authorization).
- **No Pillar-I-Intraday-V1 entry rule, threshold, or hypothetical
  outcome may be computed or inspected against any post-2026-08-18 data**
  until D1-FREEZE has happened and this addendum's minimum-evidence
  requirement (§6) has been met on calendar time actually elapsed.
- Collecting raw price data is not the same as looking at what Pillar I
  would have done with it — the quarantine is on the latter.

## 6. Predeclared minimum-evidence requirement for D3 — frozen now, not later

D3 may not be evaluated for a verdict until **both** conditions are met:
- **At least 60 NSE trading days** have elapsed since 2026-08-18, and
- **At least 15 qualifying entries** (across the 19-symbol universe) have
  occurred in that window under the D1-FREEZE rule.

Whichever condition is satisfied later governs. If 60 trading days pass
with fewer than 15 qualifying entries, D3 remains INCONCLUSIVE until the
trade-count bar is also met — the calendar bar does not override the
evidence-volume bar. **This number is fixed now, before any forward data
exists, specifically so it cannot be quietly lowered later because early
sessions look promising or discouraging.**

## Release boundary

No code written. No data loaded. No experiment run. `LIVE_TRADING_ENABLED`
untouched. This addendum authorizes D1 to begin as its own explicit next
step — it does not itself start D1.
