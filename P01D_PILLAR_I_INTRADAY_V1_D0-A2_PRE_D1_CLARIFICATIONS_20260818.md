# Pillar I Intraday V1 — D0-A2 Pre-D1 Clarifications

**STATUS: FROZEN. Final clarification record before D1 opens.** Resolves
three items found on line-by-line review of D0 and D0-A1: one wording
inconsistency, one genuine calendar-allocation gap, one real logical
circularity in the D3 quarantine. No performance numbers appear here
either. After this document, D0 + D0-A1 + D0-A2 together are the complete
preregistration package — see the freeze certificate at the bottom.

## 1. "One rule" vs. "five variants" — resolved, A1 controls

**D0-A1 supersedes D0 §4 on this narrow point only**: D1 may evaluate at
most **five preregistered named variants**, chosen and listed before any
of them is run. Every evaluated variant counts as a research trial — none
of the five are "free looks." Exactly one winner, selected by D0-A1 §4's
frozen metric and tie-break, becomes D1-FREEZE. No 128-cell sweep of the
full grid. No sixth candidate. No small tweak to any of the five after
seeing its result. D0's original "exactly one rule" language is
understood as "exactly one rule *survives* D1," not "only one rule may
ever be evaluated" — D0-A1's bounded five-variant comparison is the
operative mechanism.

## 2. Discovery vs. confirmation calendar allocation — frozen, exact dates

D0 previously called all of 2026-05-15–2026-08-13 "discovery +
confirmation" while D0-A1's tie-break already used variance across all
three sub-blocks for candidate selection — using the same blocks to both
choose and confirm a candidate is circular. Corrected, with exact dates
pulled from the real data inventory (not invented):

```
D1 DISCOVERY (used for the 5-variant comparison and D1-FREEZE selection):
  May-Jun block: 2026-05-15 .. 2026-06-12  (20 trading days)
  Jun-Jul block: 2026-06-15 .. 2026-07-14  (21 trading days)
  -> D0-A1 §4's tie-break variance check applies across these two
     sub-blocks only, not three.

CONFIRMATION (reserved, zero role in candidate selection, evaluated
  exactly once after D1-FREEZE, no tuning permitted on it):
  Jul-Aug block: 2026-07-15 .. 2026-08-13  (22 trading days)

D2 internal robustness (unchanged from D0-A1, still explicitly non-blind):
  2026-03-02 .. 2026-05-14

D3 true forward holdout (unchanged from D0-A1):
  2026-08-18 onward, quarantined per §3 below
```

**Why Jul-Aug is the reserved confirmation block, stated before any
result exists so the reasoning can be checked later**: it is the block
closest in calendar time to D3's forward window, and reserving the most
recent (rather than an arbitrarily-picked older) block avoids the
opposite failure mode flagged earlier this session — designing a rule
using the most recent regime and only checking it against older data.
Reserving the newest historical block as confirmation is the more
conservative choice, not the convenient one.

## 3. D3 quarantine circularity — resolved with a blind eligibility counter

**The bug, stated plainly**: D0-A1 required knowing that 15 qualifying
entries had occurred before permitting the entry rule to be applied to
post-2026-08-18 data — but counting qualifying entries requires applying
the entry rule. That's circular as written.

**Fix**: after D1-FREEZE, a narrow **D3 Blind Eligibility Counter** is
permitted to run continuously on post-2026-08-18 data. It may compute and
expose exactly two numbers, nothing else:

```
- number of elapsed eligible NSE trading days since 2026-08-18
- number of qualifying Pillar-I-Intraday-V1 entry signals fired
  (the frozen D1-FREEZE entry rule IS applied — this is unavoidable to
  count signals at all — but only the fact of qualification is exposed)
```

**Explicitly prohibited from being computed or exposed by the counter**,
until both thresholds in D0-A1 §6 are met:

```
trade P&L · exit result · target/stop outcome · MFE · MAE · win/loss ·
future price path beyond the entry bar · aggregate expectancy ·
equity curve · performance broken down by symbol
```

User-facing status during accrual is exactly this shape, nothing richer:

```
D3 ACCRUAL STATUS
Trading days:      47 / 60
Qualifying entries: 11 / 15
D3 remains SEALED
```

Only once `days >= 60 AND entries >= 15` (both, per D0-A1 §6's frozen
rule — the later of the two governs) does the full D3 result — P&L,
exit outcomes, expectancy, everything — open, exactly once. This
preserves the outcome-blindness the quarantine was meant to guarantee
while making the counter itself actually implementable.

## Freeze certificate — complete preregistration package

```
SHA256                                                            File
------------------------------------------------------------------------------
b0012cc879de04c68f5b99528cc61c80e25b84d230d0fd976920a68b639f6b0e  P01D_RRME_R0_ARCHITECTURE_FREEZE_20260818.md
e273f59af3c5f98a572011419f444ed037e5949c793ea46944099fa688b948a2  P01D_PILLAR_I_INTRADAY_V1_D0_PREREGISTRATION_20260818.md
b1ad8ffc2f1ff9f1e9e795669e20ba7221c03c100f3460386de7ae0d613f53e2  P01D_PILLAR_I_INTRADAY_V1_D0-A1_ADDENDUM_20260818.md
```
(D0 and D0-A1 hashes taken as revised — D0's §5/§9 correction and D0-A1
itself are both already reflected in the hashed content above; this
document, D0-A2, is not self-hashed since it cannot certify its own
content.)

**Together, D0 + D0-A1 + D0-A2 are the complete, frozen preregistration.**
No further architecture or specification discussion is required before
opening D1. Any future change to universe, features, causal timing, cost
model, calendar allocation, search bounds, or quarantine mechanics is a
new preregistration, not an edit to this package.

## Release boundary

No code written. No data loaded. No experiment run. `LIVE_TRADING_ENABLED`
untouched. D1 (discovery) is authorized to begin only as its own explicit
next step.
