# P01D Foundation Calibration F0

**Status: PROPOSED — the sole authorized-to-discuss next step. R1, R1-B,
and R1-C are all PAUSED, not deleted. R0 remains FROZEN/CLOSED, untouched
by anything in this document.**
**Date:** 2026-08-19

This document is the point-wise strategy that fell out of a full,
adversarial review of everything done in this project so far — not a new
idea introduced from outside it. It authorizes no data acquisition, no
new engine installation, no code change, and no promotion of any
candidate. `LIVE_TRADING_ENABLED` remains `False`.

---

## 1. How the project actually got here (condensed, not re-litigated)

- **R0** ran three fully independent tracks under a frozen architecture.
  All three closed: Pillar I `CLOSED/FAIL` (5/5 candidates net negative
  at D1 discovery), Pillar II `CLOSED/FAIL` (0/5 cleared the viability
  filter), V2-C `CLOSED/FAIL/OBSERVE_ONLY` (predictive gate PASS, AUC
  0.6948; economic gate FAIL on E3's mean-return checks).
- Three candidate concepts were drafted in response — **R1** (pre-open
  auction continuation), **R1-B** (V2-C's methodology refit on V11's own
  momentum-crash population), **R1-C** (a three-pillar sequential state
  machine, reclassified as a new hypothesis rather than R0 Stage 3,
  hardened with an evidentiary firewall against its own hindsight
  contamination).
- Before authorizing any of the three, the owner asked the right
  question: **three well-documented strategy classes all failing here is
  suspicious — is this the market, or is it us?**
- A first diagnostic pass found no evidence of a systemic bug and
  grounded the pattern in real published literature (Indian momentum
  cost sensitivity, short-term reversal cost erosion, global factor
  decay). **A second, independent, adversarial review of that diagnosis
  then found two real errors in it**, confirmed directly against primary
  sources in this session, not adjudicated by preference:
  - V2-C's universe was claimed to be unrestricted by market cap; it is
    in fact built exclusively from historical point-in-time NIFTY 50
    membership (`P01D_V2C_ECONOMIC_GATE_ADDENDUM_A2_20260818.md`, line
    179) — confirmed by direct rereading of the frozen document.
  - A momentum cost-horizon finding cited as Indian evidence is in fact
    a UK study, 1988–2003 (Post-Cost Profitability of Momentum Trading
    Strategies, UK) — confirmed by fetching the paper itself.
  - A "momentum decayed from 10% to 2%" figure was traced to a blog-tier
    source and presented with more precision than that sourcing
    supports.
- **The honest current verdict, adopted from that review and not
  softened**: not "the platform is probably sound" but **`NOT PROVEN
  BROKEN / NOT YET CALIBRATED`.** That distinction is the entire reason
  this document exists.

## 2. What F0 is, and the two firewalls that make it safe to run

**F0 diagnoses the platform - data, engine, cost model, execution
assumptions. It does not re-litigate any strategy's economics.**

- **Firewall 1 - R0 cannot be reopened by F0.** If F0 finds a real
  engine, fill, or data bug, the fix applies to future work. It does
  **not** retroactively reinterpret Pillar I, Pillar II, or V2-C's
  verdicts. Their governance packages are already consumed. A fixed
  engine gets applied to a **fresh** candidate, never used to re-argue an
  old one.
- **Firewall 2 - F0 authorizes no new candidate.** Not R1, not R1-B, not
  R1-C, not a fourth idea, not "large-cap V2-C" (already tested, see §1),
  not literal long-horizon momentum as a live hypothesis. F0 only
  answers whether the platform tells the truth. What to research next is
  a separate, later decision (§7).

## 3. The five exercises, sequenced for cost, not just for logic

Ordered cheapest-and-most-likely-to-catch-something first, so the
expensive step isn't spent rediscovering what arithmetic would have
caught in five minutes:

### F0.1 — Economic-geometry precheck (cheapest, do first)
No new tooling. For every closed R0 track and every future candidate,
before any backtest campaign: does the intended risk budget (stop
distance x position size) clear total round-trip cost by a stated
margin? This is not hypothetical - it already explains Pillar I:
`gross_pnl` mean across candidates ranged −₹1.42 to +₹2.56 against fees
of ~₹20 and stop-risk of only ₹26-38 (independently recomputed from
`d1_discovery_trades.json`, not estimated). Fees consumed 53-77% of the
entire designed risk budget on some candidates. **Made a permanent,
standing gate for every future candidate from this point forward, not a
one-time retrospective exercise.**

### F0.2 — Deterministic engine tests, at the resolution that matters
Synthetic price paths with known-in-advance entry, stop, gap, exit, and
P&L. **Must include intraday-minute-bar-level fixtures specifically**,
not only daily/index-level ones - Pillar I/II depend on 1-minute fill
mechanics that a daily-level test would never exercise.

### F0.3 — Positive and negative controls
- A random-signal placebo should show ~zero gross edge, negative net.
- A deliberately future-aware oracle (prohibited as a strategy, valid as
  an engine sanity check) should make money before costs - if it
  doesn't, the engine itself is broken.
- Buy-and-hold and simple scheduled rebalancing must reproduce
  independently calculated returns.

### F0.4 — Data-truth audit
Stratified symbol-days reconciled against official NSE OHLCV, sessions,
timestamps, missing bars, and corporate actions - closing-price
differences assessed using NSE's actual closing-price construction, not
naive equality. **ftInvstr and BacktestIndia may be used only as
secondary comparison sources after their own data-provenance is
checked** (they are vendors self-certifying their own survivorship-bias
claims, not independent certification) - NSE remains the primary truth
source.

### F0.5 — Literal benchmark replication
Reproduce NSE's own published **Nifty200 Momentum 30** methodology (6-
and 12-month volatility-adjusted returns, 30-stock portfolio, semiannual
rebalance) as literally as available point-in-time data permits. Any
missing licensed input is declared, never silently approximated. This is
a **calibration exercise, not a live candidate** - see §7.

### F0.6 — Two-engine agreement
Run the same benchmark in this project's own engine and an independent
one (NautilusTrader - confirmed to share portfolio/execution/strategy
components between backtest and live, with configurable fill/matching
models). Compare event times, orders, fills, positions, fees, and
portfolio values - not merely final return. Diagnosis is then objective:

```
Both engines disagree                    -> implementation bug
Both agree with each other, not NSE      -> data or methodology-input gap
Both reproduce NSE, our strategies fail  -> strategy/economics is the real problem
NSE-replica fails only under our costs   -> check index-vs-retail-implementation mismatch
```

## 4. Governance review, alongside the technical work

- **Test-window representativeness**: audit every window already used
  (V2-C 2015-2022, V11's 2023-2026 Yahoo data) against market-structural
  criteria only - bull/bear, volatility regime, liquidity, crisis
  periods - **while blind to strategy returns**. Any replacement or
  additional period must be chosen the same way, never by searching for
  a period where a strategy happens to work.
- **Economic hurdle framework**: going forward, separate three distinct
  levels explicitly rather than one blended number - (1) positive net
  expectancy, (2) statistical confidence above zero, (3) deployable
  safety margin (V2-C's 0.30%, confirmed as roughly double realistic
  TRAIN-era cost, is level 3, not level 1).
- **Experiment registry**: maintain a record of every variant tried,
  including informal/exploratory ones, not only formally gated
  candidates - separates exploratory TRAIN-tier work from confirmatory
  tests and keeps future multiple-testing risk visible instead of
  implicit.

## 5. Corrections formally retracted, on the record

Stated in this session's own analysis and now corrected rather than
quietly revised:
- "V2-C's universe wasn't restricted to large-caps" - **false**; it is
  exclusively point-in-time NIFTY 50 by construction.
- The 6-month momentum cost-horizon finding presented as Indian evidence
  - **it is a UK study (1988-2003)**, usable only as a general,
  cross-market hypothesis about turnover/cost sensitivity, not
  India-specific evidence.
- "Momentum decayed from 10% to 2%" - **under-sourced**, traced to a
  blog-tier citation; replaced with the qualitative claim only (momentum
  is cyclical and implementation-sensitive, weakens post-discovery).
- "The platform is probably sound" - **too confident**; replaced with
  `NOT PROVEN BROKEN / NOT YET CALIBRATED` throughout this document and
  going forward.

## 6. Decisions required from the owner

1. **Authorize F0** as the sole next action - or decline, in which case
   R1/R1-B/R1-C remain paused with no calibration work either, pending a
   different instruction.
2. **Resourcing**: accept that F0.6 (two-engine agreement) is real,
   non-trivial engineering - comparable in size to a full G1-G2 stage of
   any R1 candidate - not a quick side-check.
3. **Sequencing**: confirm F0.1-F0.3 (cheap, no external dependency) run
   before F0.4-F0.6 (data sourcing, second-engine integration).

## 7. What happens only after F0 - not decided here

F0's outcome determines, not presupposes, the next real decision:
literal long-horizon Indian momentum as its own fresh candidate (only
after its NSE-replica calibration in F0.5 succeeds), a V2-C
reformulation, one of R1/R1-B/R1-C, some other new hypothesis, or a
decision that no further systematic strategy work is warranted right
now. **None of these is chosen by this document.**

## 8. Status

```
R0:                   FROZEN / CLOSED - unaffected, unreachable by F0
R1 / R1-B / R1-C:      PAUSED, not deleted - no further work until F0 concludes
F0 (this document):    PROPOSED - awaiting owner authorization
Current authority:    RESEARCH_ONLY / ZERO DECISION AUTHORITY
Platform verdict:     NOT PROVEN BROKEN / NOT YET CALIBRATED
Not authorized:        any new candidate, any data acquisition, any
                        engine installation, any code change, HOLDOUT
                        access, live or shadow promotion
```
