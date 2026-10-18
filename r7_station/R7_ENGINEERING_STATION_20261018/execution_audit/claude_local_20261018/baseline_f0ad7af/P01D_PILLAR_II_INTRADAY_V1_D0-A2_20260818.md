# Pillar II Intraday V1 — D0-A2

**STATUS: FROZEN. Appended to, does not rewrite, D0 or D0-A1.** Closes
three remaining pre-D1 items: a candidate-selection-order ambiguity, a
missing fixed-R collision rule, and a zero/non-finite Z-score edge case.
No performance numbers appear here. D1 candidates are still not named.

## 1. Viability-first candidate selection — resolved

**The ambiguity**: D0-A1 §2 fixed the accounting (no double-counted
fees) but left open which candidate wins if the 0bps primary-metric
leader fails the 2bps viability margin while a lower-ranked candidate
survives it.

**Frozen selection order, correcting the ambiguity**:
```
1. VIABILITY FILTER (applied first, per discovery sub-block, never pooled):
   a candidate is eligible only if net P&L/trade is positive at BOTH
   0bps and 2bps, independently in BOTH the May-Jun sub-block AND the
   Jun-Jul sub-block. Per-block evaluation, per D0's own standing rule
   that evidence is never rescued by pooling the two sub-blocks together.

2. RANKING (applied only among candidates that survive step 1):
   the frozen 0bps primary metric + existing tie-break (D0 §10) selects
   the winner among the viable candidates.

3. If ZERO candidates survive step 1: D1 = FAIL. No D1-FREEZE exists.
   No candidate is promoted by relaxing the viability filter, and no
   sixth candidate is added to try to produce a survivor.
```
This is decided now, before any candidate's numbers exist, specifically
so a result shaped like "C1 wins 0bps but fails 2bps while C2 survives
both" has one predetermined answer (C2 wins) rather than becoming an
argument after the fact.

## 2. Fixed-R target collision — explicit, matching existing convention

D0-A1 §3 specified stop-first collision handling for the VWAP-reclaim
exit family only. Pillar II also allows a fixed-R-multiple target family
(D0 §7(a)); that family needs the identical rule stated, not inferred:

**If a single 1-minute execution bar's range touches both the hard stop
and the fixed-R target, and ordering within that bar cannot be
established from OHLC alone, the stop is credited first.** Same
conservative, already-established project convention (never credit the
favorable outcome when ordering is ambiguous), made explicit for this D0
rather than assumed to carry over silently from the VWAP-reclaim rule.

## 3. Zero/non-finite Z-score denominator — explicit fail-closed rule

**If the trailing return standard deviation (D0 §4's Z-score denominator)
is zero or non-finite** — a flat trailing window, or any other condition
producing an undefined ratio — **the Z-score is unavailable, and no
qualifying signal is generated for that symbol on that bar.** No epsilon
substitution, no fallback denominator, no silent default. An
implementation that invents a small constant to avoid division-by-zero
would be inventing behavior this preregistration never authorized.

## Note on the 2bps margin's provenance — wording correction, no rule change

D0-A1 §2 described 2bps as the project's "established reference." More
precisely: R9/R11 tested a 0/1/2/5bps stress grid without designating any
one value as *the* reference level. **2bps is selected from that
pre-existing grid and frozen here as Pillar II's own viability margin** —
a reasonable, already-used stress level, not a previously-declared
project standard being reused. This corrects the citation; it does not
change the frozen 2bps threshold itself.

## Release boundary

No code written. No data loaded. No experiment run. `LIVE_TRADING_ENABLED`
untouched.
