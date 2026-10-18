# Pillar I Intraday V1 — D0 Preregistration

**STATUS: FROZEN BEFORE ANY TEST IS RUN.** Per `P01D_RRME_R0_ARCHITECTURE_FREEZE_20260818.md`'s
rule: this document contains hypothesis, data boundaries, causal timing,
costs, entry/exit definition, hurdle methodology, and pass/fail/
inconclusive criteria — **no performance results, no code run, no data
touched.** `PILLAR_I_INTRADAY_V1` is a new identity; it does not overwrite
or reference the daily Pillar I's parameters, only its economic intent
and its negative prior evidence.

## 1. Economic hypothesis

**Stocks exhibiting genuine intraday trend persistence — established via
completed 5-minute price structure and volume participation — continue in
the direction of that trend over a bounded intraday holding horizon,
sufficiently to overcome realistic NSE execution costs.**

This is a descendant of daily Pillar I's trend-following intent, not a
literal daily-parameters-onto-1-minute conversion (rejected explicitly in
R0 §2). Prior evidence carried forward, not erased: daily Pillar I failed
to beat passive across four independent structural fix attempts
(trailing-stop width, index-regime filter, cross-sectional ranking,
mid/small-cap universe) — see `p02-quant-lab-20260816-audit`. This D0
treats intraday persistence as a materially different phenomenon (costs,
noise, and horizon all differ at 1-minute vs. daily resolution) worth one
controlled test, not a assumption that it must fail the same way.

## 2. Universe — frozen

The same 19-symbol universe already used throughout the P01D R1-R12
research (for direct comparability, not because it's the "right"
universe — that question is out of scope for this D0):

```
AXISBANK, BAJFINANCE, BHARTIARTL, HDFCBANK, HINDUNILVR, ICICIBANK, INFY,
ITC, KOTAKBANK, LAURUSLABS, LT, MARUTI, NTPC, RELIANCE, SBIN, SUNPHARMA,
TATASTEEL, TCS, ZYDUSLIFE
```

Plus NIFTY 50 index data for relative-strength computation (already on
disk for the discovery/confirmation range, per §5).

## 3. Causal timing — frozen

- **Decision clock**: completed 5-minute bars. A bar's features are only
  usable once that bar has closed — no same-bar look-ahead.
- **Execution clock**: 1-minute. Entry/exit occur at the first available
  1-minute bar open at-or-after a qualifying completed 5-minute decision
  point. Same causal-availability discipline already proven in
  `p01d_r6_same_symbol_execution_timing.py` (a 5-minute state is only
  consumed once its `available_time` has arrived at a subsequent
  1-minute point) — reused, not reinvented.
- **One event per symbol per day**: first qualifying signal only, matching
  the R1-R12 "first selected operation" convention, to avoid intraday
  double-counting/occupancy confounds.
- **Hard stop always active** from entry, independent of any other exit
  logic.

## 4. Candidate feature family — frozen set, no weights/thresholds chosen

D0 freezes *which* features are eligible to define the entry rule; it
does not choose values. Values are a D1 (discovery) decision, made once,
before opening confirmation/holdout data:

- Relative strength vs. NIFTY 50 over a completed trailing window (short
  and one longer window, e.g. 30-bar and 120-bar 5-minute, mirroring the
  V6 cross-sectional lineage's short/medium split — exact bar counts fixed
  at D1, not here).
- Volatility-normalized momentum: trailing return divided by trailing
  ATR, on completed 5-minute bars.
- Participation: completed-bar volume vs. trailing median volume for the
  same intraday time-of-day slot (avoids conflating "high volume" with
  "later in the session, when volume is naturally higher").
- Breakout/persistence structure: price position relative to a trailing
  high/range on completed bars.

**Frozen rule**: D1 may combine these into exactly one entry rule. It may
not sweep multiple combinations and report the best — that is the "27
combinations of ATR × lookback × Z-score" failure mode this whole
architecture exists to prevent. One frozen rule, written down before
discovery-period results are computed, exactly like rs15's single frozen
threshold.

## 5. Discovery / confirmation / forward holdout — corrected 2026-08-18

**Correction to this section's original wording**: the original draft
called 2026-03-02–2026-05-14 "Holdout 1/2." That was too generous. NIFTY
50 minute data through 2026-08-14 has already been inspected at minute
level in prior P01D work — including hindsight/oracle outputs over the
same symbols and dates during rs15 and related research. Calling that a
blind holdout for Pillar I would overstate the evidence a PASS on it
could support, even though no Pillar-I-specific number has ever been
computed on it. Corrected classification:

```
Previously exposed historical research data (2026-03-02 through 2026-08-14):
  suitable for D1 discovery/model selection and D2 internal robustness
  checking. NOT a blind holdout for Pillar I, regardless of outcome.

  Discovery + confirmation: 2026-05-15 through 2026-08-13
    (same three blocks used for rs15 discovery — reused for direct
     hurdle-economics comparability)
  D2 internal robustness: 2026-03-02 through 2026-05-14
    (rs15's former two "holdout" blocks — explicitly non-blind here,
     useful only as an internal consistency check, not as evidence
     of a validated edge on their own)

TRUE forward holdout (D3): 2026-08-18 onward, opened only after the
  D1-FREEZE rule and the predeclared minimum-evidence requirement (set in
  D0-A1) are both satisfied. Quarantined during D1 — raw price data may
  be collected, but no Pillar-I outcome/P&L may be inspected on it while
  D1 is still being developed.
```

This is a genuinely stronger design than the original wording, not a
downgrade: it stops relying on data that was never really blind, and
reserves real, no-longer-substitutable calendar time as the actual test.
Full search-space bounding for D1, the quarantine rule, and the D3
evidence requirement are specified in the separate
`P01D_PILLAR_I_INTRADAY_V1_D0-A1_ADDENDUM_20260818.md` — this correction
does not itself bound D1's search freedom; that addendum does.

## 6. Entry/exit definition — frozen, single hypothesis

- **Entry**: at the first 1-minute bar open following the first completed
  5-minute bar each day where the combined D1 entry rule (§4) qualifies.
  No chase logic, no study-timed delay — R6/R7 already established that
  delaying a valid entry destroys value relative to taking it (confirmed
  three independent historical blocks). Entry is immediate once the
  causal rule fires.
- **Exit — single frozen family, fixed R-multiple**: hard stop at entry
  minus a volatility-scaled distance (exact multiple fixed at D1, using
  the same causal ATR the entry rule already computes — not a new
  parameter search); target at a fixed reward:risk multiple. This is the
  simplest, most robust variant from R7/R11's own findings — pooled
  fixed-R actually beat every dynamic-exit variant once entry-timing
  contamination was removed. Dynamic/adaptive exits are explicitly
  **out of scope for this D0** — a separate, equally pre-registered
  experiment if Pillar I's entry clears its hurdle and a second question
  (does exit management add value on top of it) becomes worth asking.
- **Maximum holding period**: to end-of-session on entry day. No
  overnight carry (matches the intraday/MIS shape this is ultimately
  meant to feed).

## 7. Risk convention and cost model — frozen, reused unchanged

- **Capital**: ₹20,000. **Risk fraction**: 0.5% per trade
  (`risk_budget = capital * 0.005`), quantity = `min(risk_qty, capital_qty)`
  — identical to every R6-R11 convention, for direct hurdle-rate
  comparability.
- **Cost model**: the real Zerodha/NSE intraday fee schedule already built
  and validated in R9/R11 (`zerodha_nse_intraday_costs()` — brokerage
  min(0.03%, ₹20)/order, STT 0.025% sell-side, NSE txn charge 0.00307%,
  GST 18% on brokerage+SEBI+txn+IPFT, SEBI ₹10/crore, stamp 0.003%
  buy-side, NSE IPFT ₹0.01/crore) — reused verbatim, not reinvented.
- **Slippage stress**: 0/1/2/5 bps one-way, applied independently to
  entry and exit notional, same convention as R9/R11.

## 8. Hurdle-rate methodology — frozen

Computed the same way as R11: mean real fee per trade plus mean slippage
cost per trade at each stress level, from the discovery+confirmation
sample's own realized turnover — not an assumed flat number. The
candidate's mean gross P&L/trade must clear this hurdle **with margin**,
not just nominally — R11's own history (fixed-1.5R needed roughly 1.8-2x
its actual edge to clear even zero-slippage fees) is the standing caution
against treating "barely positive" as sufficient.

## 9. Pass / Fail / Inconclusive criteria — corrected 2026-08-18

**A PASS on discovery, confirmation, or D2 internal robustness is
informative but never final** — none of that data is blind (§5). Only
D3 (the true forward holdout) can produce a final PASS.

- **Discovery/confirmation/D2 (non-blind) PASS**: net expectancy (after
  real fees, at 0bps slippage minimum) clears the hurdle-rate threshold
  on discovery, replicates on confirmation, and replicates on D2 —
  evaluated per-block, not pooled (pooling discovery-selected data
  overstates evidence, the same correction already applied to rs15).
  Result: candidate is **eligible for D1-FREEZE**, not yet validated.
- **Discovery/confirmation/D2 FAIL**: net expectancy negative or fails
  the hurdle on confirmation or D2. Candidate is closed — same
  no-rescue posture as rs15's actual failure.
- **D3 (forward holdout) PASS/FAIL/INCONCLUSIVE**: the only result that
  can call Pillar I Intraday V1 validated. INCONCLUSIVE — too few
  qualifying trades to distinguish signal from noise — is an accepted,
  named outcome, not a failure to design around. No lowering the
  predeclared minimum-evidence requirement (set in D0-A1) because early
  D3 sessions look good or bad.

**No threshold rescue at any stage after its data is opened.** A FAIL is
accepted as a FAIL, exactly as rs15 was.

## Release boundary

No code written. No data loaded. No experiment run. `LIVE_TRADING_ENABLED`
untouched. This document alone does not authorize starting D1 — D1
(discovery) begins only when explicitly opened as its own next step.
