# P01D Intraday Brain V1 — S1.0: Shared Backtest Lab Dataset Contract

**STATUS: FROZEN.** This is S1.0 only — the dataset contract. S1.1
(`StrategyIntent` contract), S1.2 (execution semantics), and S1.3
(scorecard/audit contract) are separate, not-yet-written documents. No
V1 (or any V1–V8 contestant) was implemented, no simulator was written,
no strategy was tuned or scored while producing this document — the only
code built in this pass is `p01d_dataset_auditor.py`, a read-only audit
tool, executed against real data with its findings recorded below.

**Governing principle for all of S1 (locked here, binding on S1.1–S1.3
too):** the harness must be capable of making every contestant lose. If
the lab contains conveniences designed around any one contestant, or a
contestant can influence fills, costs, position accounting, or scoring,
this is not a tournament — it is eight differently implemented backtests
each grading its own homework.

---

## 0. What was actually done to produce this document

`p01d_dataset_auditor.py` was built and run against every candidate
dataset directory. It is read-only — it never mutates a data file — and
computes, per symbol: total rows, malformed-timestamp rows, duplicate
rows, weekend rows, off-session-time rows, per-date bar counts against
a derived consensus trading calendar, and a SHA-256 hash of the file. Its
full output is reproduced in relevant part throughout this document and
in full in `p01d_dataset_audit_report.json` (committed alongside this
spec). Every number below is taken from that run, not estimated.

---

## 1. Canonical Market Data Contract

### 1.1 Primary tournament dataset — locked

**15-minute bars, 2023-08-14 → 2026-08-13, IST.** This is the dataset
V1–V8 are scored on. Not the 60-minute or the 2016-extended set — see
§1.2 for why those are demoted to a secondary role.

| Directory | Role | Symbols | Files |
|---|---|---|---|
| `historical_data_research_ready/` | primary (base 5) | INFY, LAURUSLABS, POLYCAB, TATASTEEL, ZYDUSLIFE | 5 |
| `historical_data_v5_additional_research_ready/` | primary (v5-additional 15) | AXISBANK, BAJFINANCE, BHARTIARTL, HDFCBANK, HINDUNILVR, ICICIBANK, ITC, KOTAKBANK, LT, MARUTI, NTPC, RELIANCE, SBIN, SUNPHARMA, TCS | 15 |
| `historical_data_market_research_ready/` | benchmark/regime | NIFTY 50 | 1 |

20 symbols total, all with **identical coverage**: 18,492 rows, 744
observed calendar dates, **0 malformed timestamps, 0 duplicate
timestamps**, across every single file — verified, not assumed. Schema:
`timestamp,open,high,low,close,volume`, timestamp ISO-8601 with an
explicit `+05:30` offset on every row (nothing to normalize at the
contract level — state it, don't compute it).

The corresponding **raw** (pre-correction) directories are
`historical_data/` and `historical_data_v5_additional/` — see §5 for the
RAW → RESEARCH_READY chain.

### 1.2 Secondary robustness/regime dataset — locked, demoted role

**60-minute bars, 2016-01-01 → 2026-08-13** (`historical_data_60minute_
extended_ready/`, 19–20 symbols depending on availability — see §7) plus
`historical_data_market_60minute_extended_ready/` for NIFTY. This exists
for regime/robustness testing where a contestant's own logic legitimately
operates at 60-minute resolution. **It must never be pooled into the
primary tournament score as though the observations were equivalent** —
different resolution means a materially different (worse) execution
fidelity, not just "more data."

The short-window (2023–2026) 60-minute directories
(`historical_data_60minute/`, `historical_data_v5_additional_60minute/`,
`historical_data_market_60minute/`) were found during this audit to have
**no corresponding `_research_ready` counterpart** — they never went
through `prepare_research_ohlcv.py`'s correction step. Since the
secondary role is explicitly assigned to the *extended* 2016–2026 set,
these short-window 60-minute directories are superseded and out of scope
for S1 — flagged here as archival candidates, not deleted or otherwise
acted on in this pass.

### 1.3 Session hours — asserted, not derived

**09:15–15:30 IST**, the standard NSE equity continuous session. This is
stable market structure, not something that changes year to year the way
holiday dates do, so it is stated directly rather than inferred from the
data. At 15-minute resolution this yields 25 bar-starts per session
(09:15 … 15:15); at 60-minute, 7 (09:15 … 15:15, the last bar covering
the shortened 15:15–15:30 close).

### 1.4 Trading-day calendar — derived by cross-symbol consensus, and why

This repository has exactly **one** independently verified NSE holiday
year on file
(`institutional_engine_v34_p01d_candidate.py::NSE_HOLIDAYS_2026`).
2023–2025 do not exist anywhere in this codebase. A live search and a
direct fetch of Zerodha's own holiday-calendar page were both tried
before writing the auditor; both returned 2026-only data. Rather than
hand-transcribe a multi-year holiday list from secondary sources — and
risk a silent transcription error becoming the ground truth every
missing-bar judgment depends on — the trading-day calendar for this
contract is **derived from cross-symbol consensus within the dataset
itself**: a weekday on which at least half the universe has data is
treated as an expected session. The audit confirms this resolves
cleanly for both datasets — **every weekday date is either
full-universe-present or full-universe-absent**, with zero dates landing
in an ambiguous middle. There is no partial-consensus noise to argue
about.

Primary dataset: **738 consensus session dates**, 2023-08-14 → 2026-08-13.
Secondary dataset: **2,619 consensus session dates**, 2016-01-04 →
2026-08-13.

### 1.5 Special-session findings — real, explained, not bugs

The audit flagged non-weekday-session rows in both datasets. Investigated
individually, not left as an unexplained anomaly count:

| Date | Day | Bars | Explanation |
|---|---|---|---|
| 2023-11-12 | Sunday | 4 (18:15–19:00) | Diwali Muhurat trading (symbolic evening session) |
| 2024-01-20 | Saturday | 25 (full day) | NSE special live-trading session |
| 2024-03-02 | Saturday | 7 (half day, 09:15–12:15) | NSE special live-trading session |
| 2024-05-18 | Saturday | 7 (half day, 09:15–12:15) | NSE special live-trading session |
| 2025-02-01 | Saturday | 25 (full day) | Union Budget day (Feb 1 fell on a Saturday) |
| 2026-02-01 | Sunday | 25 (full day) | Union Budget day (Feb 1 fell on a Sunday) |
| 2024-11-01 | Friday (weekday) | 4 (18:00–18:45) | Diwali Muhurat trading, same-day evening session |

**Consequence for S1.1/S1.2**: Muhurat trading is why 2024-11-01 and
2025-10-21 appeared in the raw missing-bar report as "expected 25,
observed 4" — the regular session had zero bars (a genuine holiday) and
the 4 observed bars were an unrelated evening session, not a partial
regular session. **The consensus-date algorithm as currently implemented
incorrectly counts a Muhurat-only date as a consensus session date**
(because *some* symbols have *some* data on it) — this is a real
refinement the auditor needs before S1.1: Muhurat/special-session dates
must be excluded from the regular-session consensus calendar, tracked as
their own labeled category, not compared against the 25-bar template.
Recorded here as a known, named limitation of the current auditor, not
silently patched into a "clean" number.

### 1.6 Data-currency finding — the dataset's own recent tail is truncated

The last several trading days before the pull cutoff (2026-08-03,
08-04, 08-05, 08-06, 08-07, 08-10, 08-11, 08-12, 08-13 — 9 dates,
verified identically across all 20 primary symbols) are each missing
**exactly their final bar** (15:15, covering 15:15–15:30) — every one of
these days shows first bar 09:15, last bar 15:00, 24 bars instead of 25.
This is consistent with the download having run before that day's final
candle was finalized, not a market data gap. **Any contestant or
scoring run using dates in this window must account for a systematically
truncated final bar**, not treat it as a random missing observation.

### 1.7 OHLC correction provenance

`prepare_research_ohlcv.py`'s only correction is enforcing internal OHLC
consistency (`high = max(o,h,l,c)`, `low = min(o,h,l,c)`), logged to a
`correction_audit.csv` per directory. Actual counts, this run:

| Directory | Corrected rows |
|---|---|
| `historical_data_research_ready/` | 1 (LAURUSLABS) |
| `historical_data_v5_additional_research_ready/` | 2 (BHARTIARTL, HDFCBANK) |
| `historical_data_60minute_extended_ready/` | 3 (BHARTIARTL, HDFCBANK, LAURUSLABS) |
| `historical_data_market_research_ready/` | 0 |
| `historical_data_market_60minute_extended_ready/` | 0 |

Small numbers, but counted, not assumed clean.

---

## 2. Contestant Interface Contract (binding constraints only — full spec is S1.1)

Recorded here as a hard boundary condition on the dataset, not the full
interface spec: every V1–V8 contestant receives only information
available at decision time from this frozen dataset (no future bars),
returns only a standardized `StrategyIntent`, and has no access to
simulator internals, account P&L, or special execution logic. One
position maximum, enforced structurally by S1.1's interface, not merely
documented — full detail deferred to S1.1.

---

## 3. Causal/Event Ordering Contract (binding constraint, full spec is S1.2)

A signal produced from bar `t` must have its entry timing (bar `t`
close, bar `t+1` open, or a resting order) stated explicitly and
identically for every contestant — no contestant-specific timing rule.
Same-bar look-ahead is prohibited. Full mechanics deferred to S1.2; the
one rule locked here because it is a dataset-shape consequence, not an
execution-engine detail:

### 3.1 Intrabar stop/target collision rule — LOCKED

> If both stop and target are reachable within the same OHLC candle, and
> no finer-resolution evidence establishes their actual sequence, the
> simulator shall resolve the ambiguity adversely to the position: **stop
> before target.** No random ordering. No optimistic ordering. No
> contestant-specific override.

This is stricter than it would need to be if 1-minute data existed to
disambiguate — it doesn't (§1, no finer-than-15-minute data exists
anywhere in this repository), so the adverse-ordering default is the
*only* defensible rule, not one of several reasonable choices. Gap-through-
stop behavior gets its own execution-price rule in S1.2, not this one —
a gapped stop is a different problem from an intrabar collision and must
not be resolved by the same assumption.

---

## 4. Execution Contract (full spec is S1.2 — not written here)

Fill model, tick-size rounding, slippage, brokerage/STT/exchange
charges/GST/stamp duty, partial-fill policy, rejected/unfilled orders,
end-of-day liquidation: all deferred to S1.2. Binding constraint on the
dataset side only: the simulator must be shared code, never overridden
by an individual contestant.

---

## 5. Provenance Chain — RAW → preparation → RESEARCH_READY → hash

Proven, not asserted: every `_research_ready` directory has a
corresponding raw source, a documented single-purpose correction step
(`prepare_research_ohlcv.py`, §1.7), and every resulting file has a
SHA-256 hash computed by `p01d_dataset_auditor.py` and recorded in
`p01d_dataset_audit_report.json`. Sample (first 12 hex chars shown here;
full 64-char hashes are in the JSON manifest):

| Symbol | File | SHA-256 (12) |
|---|---|---|
| INFY | `historical_data_research_ready/NSE_INFY_15minute_2023-08-14_2026-08-13.csv` | `d25bc131b7bb` |
| RELIANCE | `historical_data_v5_additional_research_ready/NSE_RELIANCE_15minute_2023-08-14_2026-08-13.csv` | `5a82f679e2a3` |
| NIFTY 50 | `historical_data_market_research_ready/NSE_NIFTY 50_15minute_2023-08-14_2026-08-13.csv` | `c2e78705f61b` |

Any future re-download or re-preparation must produce the same hash for
the same source data, or the dataset_id (§10) changes and every
downstream result is understood to be against a different dataset, not
silently re-scored against "the same" one.

---

## 6. Research-Separation Contract — evidence labels, not train/val/test

Based on this inventory, **no portion of the existing historical dataset
can be confirmed genuinely untouched by prior research.** V1 itself
already consumed some of the 15-minute history (`BRAIN_RESEARCH_SPEC.md`'s
original pass, results in `brain_results_v1/*.json`, framed as "weak
gross signal, cost-eaten"); the price history underlying both datasets
has separately participated in the V10–V17 momentum research program via
`extended_history_windows.py`'s six-month walk-forward windows. Declaring
any convenient slice "pristine holdout" would be fiction. Three labels
instead:

- **A — Prior-exposed research data.** Anything demonstrably consumed in
  previous strategy research. At minimum: whatever period V1's original
  `BRAIN_RESEARCH_SPEC.md` run covered, and any `EXTENDED_WINDOWS` slice
  already used to select or validate a momentum-research winner.
- **B — Tournament evaluation data.** Existing data used to compare
  V1–V8 under this newly frozen harness. Useful, legitimate — **not**
  called pristine or blind merely because this specific tournament
  hasn't run on it yet.
- **C — Future sealed data.** Market observations arriving after the
  contestant specifications (V1–V8, S1.1's interface) and the full S1
  harness (S1.0–S1.3) are frozen. This is the only label that supports a
  genuinely prospective claim.

### PROHIBITION — binding on every subsequent S1/S6/S7 report

> **No V1–V8 result obtained from historical data available before the
> S1/V1–V8 freeze shall be described as a blind prospective validation
> merely because that exact contestant has not previously seen those
> particular rows.**

That is the loophole most likely to produce self-deception later — a
technically-true, substantively-false claim of independence. Any report
built on this contract that uses the words "blind," "prospective," or
"out-of-sample" for label-B data is non-compliant with this contract.

---

## 7. POLYCAB and LAURUSLABS — ex-ante availability constraints, in the manifest

**POLYCAB**: included in the primary 15-minute universe (full
2023-08-14 → 2026-08-13 coverage confirmed, 18,492 rows, identical to
every other primary symbol). Excluded from the secondary 2016-extended
universe per the existing, pre-dating-this-session precedent in
`extended_history_windows.py` — its first observed bar in the extended
60-minute dataset is **2019-04-16**, matching the documented April 2019
listing rationale exactly. This is an ex-ante data-availability
constraint, not a performance-based exclusion, and the reason is
recorded here precisely so nobody re-derives or second-guesses it later
without the original justification in hand.

**LAURUSLABS — a new finding this audit surfaced, not previously
documented anywhere in this codebase**: in the secondary 2016-extended
60-minute dataset, LAURUSLABS' first observed bar is **2016-12-19**, not
2016-01-01 like the rest of the universe — a ~236-trading-day
(~11.5-month) gap at the start of its window that `extended_history_
windows.py`'s `EXCLUDED_SYMBOLS` (which names only POLYCAB) never
flagged. This didn't surface in prior momentum research because that
research's 12-1 formation calculation only needs the most recent ~13
months of data per symbol, not the full 10-year span — so a short start
never visibly mattered there. It would matter here if a P01D
robustness/regime test tries to use LAURUSLABS across the full 2016–2026
secondary window. **Recommendation, not yet a decision**: apply the same
treatment precedent as POLYCAB — record LAURUSLABS' effective secondary-
dataset start as 2016-12-19 in the manifest, and either exclude it from
full-history secondary robustness claims or explicitly bound any such
claim to 2016-12-19 onward. This is flagged for S1.1/S6 to decide
formally, not decided unilaterally here.

---

## 8. Tournament Output Contract (full spec is S1.3 — not written here)

Deferred in full. Binding constraint recorded now: ranking must not be
primarily win-rate or raw P&L; the complete trade ledger and intent
ledger must be retained so every headline statistic is reconstructable
from raw evidence, not just asserted.

---

## 9. Audit & Reproducibility — manifest schema

Every tournament run must carry:

```
dataset_id + dataset_hash + contestant_version + lab_version +
parameter_manifest + random_seed (if any) + run_id
```

`dataset_id` for this contract's two datasets:
- Primary: `P01D_PRIMARY_15MIN_20230814_20260813`
- Secondary: `P01D_SECONDARY_60MIN_EXTENDED_20160101_20260813`

`dataset_hash` is the hash of the full manifest in
`p01d_dataset_audit_report.json` (a hash-of-hashes over every constituent
file's SHA-256) — not defined further here; S1.1 should specify the
exact hash-of-hashes construction before the first contestant run, so it
exists before it's needed rather than being improvised under pressure
once a result needs to be traced back six weeks later.

---

## 10. Dataset Acceptance Gate

`p01d_dataset_auditor.py` is the acceptance-gate tool, not a one-off
script — it should be re-run (a) before S1.0 is amended, (b) before any
new symbol or date range is added to either dataset, and (c) before any
tournament run that claims to use "the" primary or secondary dataset, to
confirm the hashes still match this contract's recorded values. It
currently checks: row counts, malformed timestamps, duplicate
timestamps, weekend/off-session rows, per-date bar counts against the
consensus calendar, and per-file SHA-256. It does **not** yet exclude
Muhurat dates from the consensus calendar (§1.5) — that refinement should
land before S1.1 treats the auditor's missing-bar output as final. It
never forward-fills, interpolates, or otherwise manufactures a missing
observation — a gap is reported, never silently repaired.

---

## 11. Release boundary

`LIVE_TRADING_ENABLED` is not referenced anywhere in this pass — no
engine file was touched. No V1–V8 contestant was implemented. No
simulator was written. No strategy was tuned or scored. No broker or
network call was made; every check in this document runs against local
CSV files already on disk. `p01d_dataset_auditor.py` opens files
read-only and writes only its own report (`p01d_dataset_audit_report.json`)
— it never modifies a source data file.

---

## Report

**Unresolved decisions, explicitly carried forward:**
1. LAURUSLABS' secondary-dataset availability treatment (§7) —
   recommendation given, not decided.
2. The consensus-calendar algorithm's Muhurat-date misclassification
   (§1.5) — a known, named auditor limitation, not yet fixed.
3. `dataset_hash`'s exact hash-of-hashes construction (§9) — scoped for
   S1.1, not defined here.
4. S1.1 (StrategyIntent contract), S1.2 (execution semantics), S1.3
   (scorecard/audit contract) are not written — this document is S1.0
   only, as scoped.

**Is S1.0 ready to freeze?**

Yes, on the same standard R0 and S0 were held to: every open item above
is recorded as open, not silently resolved by an implementation default,
and none of them block starting S1.1 — S1.1's own contract doesn't
depend on resolving LAURUSLABS' treatment or the hash-of-hashes
construction, both of which can be decided when they're actually needed.
What S1.0 does lock, and lock hard, is the one thing every later
decision would otherwise silently inherit without noticing: which data
exists, what shape it's actually in (not assumed to be in), and which
parts of it are no longer available as independent evidence.
