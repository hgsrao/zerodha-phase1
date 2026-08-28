# P01D Historical Brain Reconstruction — 2026-08-14

Read-only reconstruction per the correction to the P01D revalidation status.
`LIVE_TRADING_ENABLED` untouched (`False`, unverified-unchanged since last
check). No runner started. No broker API called. No code modified. No
frozen P02 file touched. Every claim below is sourced to a specific file,
and every number is either quoted from a file or computed live from a
results file in this pass — marked which.

## Corrected diagnosis (confirmed)

**"P01D does not currently have a strategy bridge feeding `request_entry()`"
is the accurate statement — not "no intraday strategy exists."** A
repo-wide search for `.request_entry(` (call sites, not the definition)
finds it invoked only by P02's own test suite and
`test_v34_market_hours_gate.py` — never by any shadow, observatory, or
research file. Every shadow/research module below states this about
itself explicitly in its own docstring, independent of my search; the two
lines of evidence agree.

## 1. V1–V9 strategy lineage — CONFIRMED, with real numbers

Source: `BRAIN_RESEARCH_SPEC*.md` (root, un-versioned = V1) and
`brain_results_v*/` (raw JSON output per version). All are **offline
research**, explicitly labeled "not a production trading strategy," no
broker/order code anywhere in the chain.

| Ver | File(s) | What it tested | Status |
|---|---|---|---|
| V1 | `BRAIN_RESEARCH_SPEC.md`, `brain_research_lab.py`, `brain_results_v1/*.json` | 15-min breakout: trend regime + breakout + volume + ATR stop, single position, fixed qty, 5 symbols | Weak gross signal, cost-eaten (per spec's own framing for why V2 exists) |
| V2 | `BRAIN_RESEARCH_SPEC_V2.md`, `brain_results_v2/*.json` | Same rules, 60-min candles instead of 15-min (lower turnover) | Near break-even, inconsistent across instruments |
| V3 | `BRAIN_RESEARCH_SPEC_V3.md`, `brain_results_v3/*.json` | + slow-trend-rising filter | (per V4 framing) not the superior branch |
| V4 | `BRAIN_RESEARCH_SPEC_V4.md`, `brain_results_v4/*.json` | + NIFTY regime filter (index above own 50-bar mean) | Returned to as the better V2-lineage baseline |
| V5 | `BRAIN_RESEARCH_SPEC_V5_WALK_FORWARD.md`, `walk_forward_v5.py`, `brain_results_v5/*` | Walk-forward validation of V4's signal across a **predeclared 20-symbol universe** (5 original + 15 added) | Validation method, not a new signal |
| V6 | `BRAIN_RESEARCH_SPEC_V6_CROSS_SECTIONAL.md`, `cross_sectional_brain_v6.py`, `brain_results_v6/*` | New architecture: daily cross-sectional ranking — 30h/120h relative return vs NIFTY (30%/30%), distance-from-120h-high (25%), abnormal final-hour volume — top eligible names vs universe over next 3 sessions | Signal research only, explicitly "not portfolio PnL/permission to trade" |
| V7 | `BRAIN_RESEARCH_SPEC_V7_EARLIER_DECISION.md` | V6 diagnosed overnight-move leakage (~0.20%); moves the ranking decision one hour earlier (13:15 vs 14:15) | One-item change on V6 |
| V8 | `BRAIN_RESEARCH_SPEC_V8_PATIENT_ENTRY.md` | Keeps V7 ranking, changes entry to a patient limit (0.25% below signal close, 6-bar validity, `UNFILLED` if never touched) | One-item change on V7 |
| V9 | `BRAIN_RESEARCH_SPEC_V9_PORTFOLIO.md`, `portfolio_brain_v9.py`, `brain_results_v9/portfolio_v9.json` | Portfolio construction on top of V8: `TOP1` / `TOP2_SECTOR` / `TOP4_SECTOR`, 0.25% risk/position, 1.5×ATR stop, sector-no-duplicate rule in the `_SECTOR` variants, max 80% deployed | See verified results below |

This matches your recalled lineage point-for-point: 15-min breakout (V1),
60-min (V2), NIFTY-regime filtering (V4), cross-sectional ranking (V6),
earlier-decision/patient-limit entries (V7/V8), diversified/sector-controlled
ranking (V9). "Relative strength / medium-term strength / breakout
proximity / abnormal volume" maps exactly onto V6's four weighted features
(30h return / 120h return / distance-from-120h-high / volume vs median).

## 2. V9 evidence — your weak-edge claim independently re-verified, not just repeated

I did not take the profit-factor/window/contributor numbers on trust —
recomputed all three directly from `brain_results_v9/portfolio_v9.json`
in this pass:

```
TOP4_SECTOR (the variant you cited):
  profit_factor      = 1.0329505877120289        (matches "1.03")
  net_return         = +1.82%  (₹101,823 final on ₹100,000 start)
  window_returns (5 six-month windows):
    2024-02-14..08-14: -6.12%
    2024-08-14..2025-02-14: +1.54%
    2025-02-14..08-14: +5.71%
    2025-08-14..2026-02-14: +1.14%
    2026-02-14..08-14: -0.06%
  -> 3 of 5 positive         (matches "3/5 profitable windows")

Best-contributor test (computed live, per-symbol P&L aggregation):
  Total net P&L, all 453 trades: +₹1,823.19
  Best single symbol: LAURUSLABS, +₹4,169.35
  Total with LAURUSLABS removed: -₹2,346.16   -> NEGATIVE
  (removing only the single best *trade*, ₹1,428.46, leaves +₹394.73 -
   still barely positive; it's removing the best *symbol* that flips it,
   which is the correct reading of "best contributor")
```

**All three of your cited figures check out exactly.** TOP4_SECTOR's edge
is thin and concentrated in one name — consistent with "weak historical
evidence," not "no evidence." `TOP1` and `TOP2_SECTOR` are worse
(profit factor 0.927 and 0.984 respectively — both net losers).

## 3. Shadow/Observatory system — CONFIRMED, but not under that literal name

**Correction to the correction:** a repo-wide search for "Multi-Brain" or
"Shadow Observatory" as literal strings finds **zero matches**. That exact
name does not appear anywhere in this codebase. What does exist, and does
functionally match what you described, is a real, working multi-program
correlation system — I'm naming this precisely so neither of us treats a
paraphrase as a filename later:

| File | Purpose (from its own docstring) | Reads broker? | Can place orders? | Calls `request_entry`? |
|---|---|---|---|---|
| `read_only_shadow_collector.py` | Market snapshot collector for shadow telemetry; broker op used: `quote` only | Yes (quote only) | No | No |
| `shadow_strategy_evaluator.py` | Pure calc: hypothetical decisions from supplied snapshots (momentum_z/stability_z/OBI formula) | No | No | No — "deliberately has no broker, Kite, runner, credential, state-store, request_entry, or order-placement imports" |
| `external_momentum_shadow.py` | The **validated V11** 12-1 momentum, paper-only, real Zerodha delivery costs, 19-symbol universe | No | No | No — "never imports a broker SDK, the runner, or request_entry" |
| `orb_shadow_observer.py` / `orb_shadow_collector.py` | Opening Range Breakout, explicitly labeled **EXPLORATORY / not backtested** (only 60-min bars exist; ORB needs finer resolution) | Yes (`quote` only, collector) | No | No |
| `live_input_shadow_lifecycle.py` | Local lifecycle state machine driven by collector telemetry (gates: quote_integrity, universe_coverage, momentum, stability, affordability, obi) | No (reads local JSON only) | No | No |
| `shadow_entry_exit_lifecycle.py` | Separate, non-production shadow BUY/SELL lifecycle state | No | No | No |
| `entry_gate_dry_run.py` | Runs live shadow candidates through the **real** `RunnerEntryAuthorizer` pipeline (v2: full policy layer, not just the shallow gate) — answers "would this have passed?" without being able to dispatch | No order path; reads broker snapshot for realism | No — "physically incapable of dispatching an order" (per today's session record) | No |
| `v34_observatory_v2/v3/v4.py` | Read-only correlation dashboard; v4 correlates all four programs (production bot, momentum shadow, ORB shadow, entry-gate dry run) on one screen | No | No — "no broker SDK import, no order API, no writes of any kind" | No |
| `observation_suite_launcher.py` | Orchestrates runner + collector + Observatory v3; **refuses to run unless the production source retains `LIVE_TRADING_ENABLED = False`** | N/A (launcher) | No | No |

**`external_momentum_shadow.py` is the "external 12-1 momentum live-paper
variant"** you referenced — confirmed, it is V11 (the one strategy that
actually cleared edge validation) running paper-only against live data
with real costs applied.

**This is functionally a multi-brain shadow observatory** — four
independently-running "brains" (production engine state, validated V11
momentum, exploratory ORB, and the real-gate dry run) correlated on one
read-only dashboard — it's just never been given that name as an artifact.
Worth deciding whether to formally name/consolidate it if this line of
work continues, but that's a naming/packaging decision, not a discovery
of missing functionality.

## 4. Complete-live-day question — directly answered, and it sharpens your NOT YET CONFIRMED

I checked this from raw log evidence rather than from the narrative
session summary, because the two turned out to disagree in one place:

**2026-08-13** (`shadow_collector.log`, root, the momentum-shadow
collector's own append log):
```
First observation: 2026-08-13T06:28:45 UTC  (11:58 IST)
Last observation:   2026-08-13T10:33:27 UTC  (16:03 IST)
Total observations: 694, all dated 2026-08-13, none on any other day
Cadence: ~15s between cycles (694 obs / ~4h05m span)
```
NSE trading hours are 09:15–15:30 IST (03:45–10:00 UTC). This log **starts
at 11:58 IST — 2h43m after market open — and ends at 16:03 IST, 33min
after close.** The opening 2h43m of the session has zero shadow
observations on this date. **Not a complete trading day.**

**2026-08-14** (today): I could not find a continuous per-observation log
for the shadow collector at all — `shadow_collector.log` has no 2026-08-14
entries (it stops at Aug 13 10:33 UTC), and no per-session shadow-collector
log turned up under `session_logs/20260814_*/`. The only Aug-14 artifact
for this component is `shadow_strategy_telemetry.json`, a **single latest-
state snapshot**, timestamped `2026-08-14T12:06:06 UTC` (17:36 IST — 2h06m
**after** market close). One snapshot cannot establish continuous coverage,
duration, or gaps.

By contrast, the **P01D production runner's own log**
(`session_logs/20260814_082925/bot_production.log`) does show a clean,
continuous 08:29:25–16:13:24 IST session today (~7h44m) — but that's the
idle execution engine, not the shadow strategy telemetry.

**Conclusion: "one complete live day: NOT YET CONFIRMED" is correct, and
the reason is now precise rather than assumed** — the one dated log that
exists (Aug 13) demonstrably misses the market open, and today's claimed
full-day shadow run has no corroborating continuous log I could find,
only a single post-close snapshot. This is worth resolving before trusting
any "ran all day" claim about the shadow system going forward — either the
log is elsewhere and should be pointed to, or the coverage claim needs
correcting the same way the P01D-closure claim just was.

## 5. Corrected P01D architecture map

```
STRATEGY                    SHADOW / BRIDGE                  AUTHORIZER                    ENGINE                  BROKER
--------                    ---------------                  ----------                     ------                  ------
V1-V9 brain lineage    ->   (none - research only,       ->  RunnerEntryAuthorizer      ->  TradingEngineV34   ->  Zerodha
(offline, historical)       never touches request_entry)      (real, tested via                (P01D candidate,       (Kite)
                                                                entry_gate_dry_run.py,           execution-only,
V11 12-1 momentum      ->   external_momentum_shadow.py  ->    but that dry run is a           request_entry()
(validated, the one         (paper-only, real costs,           terminal read-only probe,        exists but is
 that passed edge            never calls request_entry)         not a live gate a bridge          never called by
 validation)                                                    could route through yet)          the live loop)
                                                                       |
ORB (exploratory,      ->   orb_shadow_observer/                      |  <- the missing link:
never backtested)           collector.py (read-only)                     no code anywhere
                                                                           calls
shadow_strategy_evaluator.py's                                           engine.request_entry(...)
own momentum_z/stability_z/OBI                                            from a live decision
formula                ->   live_input_shadow_lifecycle.py               loop. This is the sole
                             + shadow_entry_exit_lifecycle.py             gap, confirmed by (a)
                             (local-only, never broker writes)            reading the runner's
                                                                            step() loop, and (b)
                                                                            repo-wide search for
                                                                            .request_entry( calls.
```

**The gap is exactly one box wide.** Everything left of "no code calls
request_entry()" exists, in varying states of validation (V11 strong,
V9/TOP4_SECTOR weak-but-real, V1-V8/ORB not production-grade). Everything
right of it (`RunnerEntryAuthorizer`, `TradingEngineV34`, the broker
adapter) is built, tested (577/577), and safety-certified per tonight's
earlier P01D report. **What was never built is the translator that takes
a shadow module's hypothetical decision and calls `request_entry()` with
it** — by design, since every shadow file explicitly refuses to import
`request_entry` at all, on purpose, as a safety property while they were
observation-only tools.

## Revised P01D status (superseding tonight's earlier close)

```
P01D CURRENT EXECUTION ENGINE
    Safety/regression:        strong (577/577, per I0-I4 tonight)
    Current signal bridge:    MISSING (confirmed twice, independently)

HISTORICAL INTRADAY BRAIN
    Existence:                CONFIRMED (V1-V9, real files, real results)
    V1-V9 lineage:             CONFIRMED, matches your recollection exactly
    Shadow observatory:        CONFIRMED as working system; "Multi-Brain
                               Shadow Observatory" is not a literal artifact
                               name anywhere in the repo - functional
                               equivalent (v34_observatory_v4 + 4 correlated
                               programs) does exist
    One complete live day:     NOT CONFIRMED (Aug 13 log misses market
                               open by 2h43m; Aug 14 has no continuous log
                               found, only one post-close snapshot)
    Robust profitable edge:    NOT PROVEN (TOP4_SECTOR PF 1.03, 3/5
                               windows, single-symbol-dependent - all
                               reconfirmed by direct computation tonight)

P01D LIVE STATUS
    NO-GO
```

Reason, precisely: not "no brain" and not "the shell is unsafe" — an
earlier, weak-but-real brain exists, and the current shell has no bridge
connecting anything to it. Reconnecting it would require: (a) deciding
whether TOP4_SECTOR's thin, single-symbol-dependent edge is worth
building a bridge for at all (my read: no — this is closer to the P01D-
vs-V11 shape mismatch than a resurrection candidate, since V9 is itself a
multi-position/sector-controlled portfolio strategy, not a single-symbol
MIS-shaped one, so it has the *same* structural mismatch with the current
P01D engine that V11 does), and (b) confirming a genuinely complete,
gap-free live shadow day before trusting any of this against real capital
regardless of (a).

## What was and wasn't done

- No code modified, no runner started, no broker API called, no frozen
  P02 file touched.
- Every number in §2 was computed fresh from `brain_results_v9/portfolio_v9.json`
  in this pass, not copied from a prior write-up.
- Every "does it call request_entry" claim in §3 is cross-checked two ways:
  the module's own docstring, and an independent repo-wide grep.
- §4's log-gap finding surfaces a real discrepancy between the narrative
  claim ("stayed healthy for the full day") and the raw log evidence for
  the shadow collector specifically — flagged rather than smoothed over.
