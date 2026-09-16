# V34-P02 — Portfolio Invariants Specification (P02-0)

**STATUS: FROZEN (Revision 2 + valuation-cadence pin below).** No further
architectural changes to this document except in response to an actual
contradiction found during P02-B onward — implementation is now judged
against this spec, not the other way around.

Gating document, written before any engine-lifecycle code. Revision 2:
incorporates a second review pass that found one genuine architectural
contradiction (halt-class conflation) and several precise gaps in the
daily-P&L, drawdown, sector-concentration, and reservation-atomicity
definitions. Nothing here authorizes live trading; `LIVE_TRADING_ENABLED`
stays `False`, unconditionally, throughout — unchanged release boundary.

Terminology: **positions**, not "slots." Six is a structural ceiling, not
a target.

## 1. Authoritative truth hierarchy (unchanged from rev 1, restated)

1. **The broker is the sole source of truth** for what positions exist.
2. **`BotState.active_trades` is a reconciled view of the broker** —
   rebuilt/verified at every `reconcile_startup()` and re-checked at every
   poll, never an independent ledger.
3. **The runner's `open_position_symbols` is durable reservation/recovery
   metadata only** — never authoritative.
4. **On disagreement the reconciliation logic cannot resolve
   deterministically: fail closed** — with the stale-reservation nuance
   added in §7 below (disagreement that can be *proven* benign is not the
   same as disagreement that can only be *inferred* benign).

## 2. Three control classes — not one boolean

**This is the one correction that had to happen before anything else.**
Rev 1 left `request_entry()` and the (also new) `request_exit()` sharing
the same `terminator.halted` / `RECONCILIATION_HALT` precondition. That's
fine for genuine integrity ambiguity, but §5 (rev 1) separately promised
that financial/policy limits *never* block exits — and financial limits
were never wired into anything else, so nothing actually enforced that
promise. If a financial policy halt were ever surfaced as an exception
that the engine's generic `except Exception` handler in `step()` could
catch, it would call `trigger_hard_halt()` and silently break the exact
guarantee §5 states. Fixed by defining three distinct, non-overlapping
control classes up front:

| Class | Meaning | Blocks | Cleared by |
|---|---|---|---|
| **`ENTRY_LOCK`** | A computed financial/policy threshold is currently tripped, or a kill-switch file is present. Not an integrity problem — the system's understanding of reality is fine, it just isn't allowed to add new exposure right now. | New `request_entry()`/`authorize_buy()` approvals only. | Self-clears when the underlying metric recovers (day rolls over, rolling window ages out, drawdown recovers) — no operator action needed, except the kill-switch file itself, which requires deliberate removal. |
| **`ENGINE_HALT`** | Broker/local state ambiguity, malformed data, or an integrity problem that cannot be proven benign. | **Everything** — `request_entry()`, `request_exit()`, all reactive `step()` advancement, for every position. | `clear_halt_and_reconcile()` only, after broker-clean verification — unchanged from today. |
| **`KILL_SWITCH`** | Operator/file-driven global stop (`P03_KILL_SWITCH_FILE`), existing mechanism. | New entries only — **existing code already gives emergency exit "no restriction" by design** (`P03RiskController.evaluate_emergency_exit()`), and this design keeps that property. | Operator removes the kill-switch file. |

**Concretely, this changes how the new authorizer communicates a financial
halt to the engine.** The authorizer's `authorize_buy()`-equivalent
**never raises an exception for an `ENTRY_LOCK`-class reason** — it always
returns a decision object (`allowed=False, reason=...`). Exceptions from
the authorizer are reserved for genuine broker-contract violations
(malformed snapshot, unparseable payload), which the engine correctly
treats as `ENGINE_HALT`. When `step()`'s `ENTRY_SUBMIT` handling receives
an `allowed=False` decision for an `ENTRY_LOCK` reason, it does **not**
hard-halt: it abandons that one pending entry (`ctx` is removed from
`active_trades`, audit-logged with the specific reason, no broker order
was ever placed), and the engine continues operating normally for every
other position. This is new behavior `request_entry()`'s original
single-position design never needed, because there was never a "some
other position's loss tripped a halt between intent and submission" case
worth distinguishing from "the broker doesn't understand what's happening."
`request_exit()` checks `ENGINE_HALT` and `KILL_SWITCH`-class conditions
only — it has no dependency on `ENTRY_LOCK` state at all, by construction,
not by convention.

## 3. What constitutes an owned position (unchanged from rev 1)

A symbol is "owned" iff the broker reports a nonzero CNC position for it
**and** `active_trades[symbol]` exists with `filled_qty > 0`.

## 4. Capital accounting — cost-basis ceiling, plus observational gross exposure

Unchanged core formula and rationale (appreciation cannot trip the
ceiling, because the ceiling is a *cost-basis commitment* limit, not an
*economic exposure* limit):
```
CommittedCapital = DeployedCapital (cost basis) + PendingBuyExposure + ReservedEntryCapital
Gate: CommittedCapital + ProposedEntryCapital <= CapitalCeiling
```

**New**: these are two genuinely different quantities and both are worth
tracking, so both are computed — one gates, one only observes:

```
GrossMarketExposure = Σ |quantity_i × current_LTP_i| over all owned positions
```

`GrossMarketExposure` is added to the risk snapshot and persisted/logged
every cycle, but **is not gated on in P02** — no strategy work here has
validated a market-value exposure ceiling, so inventing one now would be
exactly the ad hoc-rule mistake V12 already demonstrated is dangerous.
Logging it now means a future decision to gate on it is a data-informed
one, and means "capital ceiling" and "economic risk ceiling" can never be
quietly confused for the same thing later — ₹20,000 committed can
legitimately carry ₹35,000 of live market exposure after appreciation,
and that fact is visible, not hidden.

## 5. Portfolio equity and daily P&L — corrected to avoid exit-day double-counting

Rev 1 tried to define daily P&L by stitching together "realized today" and
"change in carried-position MTM." That's exactly the kind of decomposition
that double-counts a position sold today (its gain since entry gets
counted as "realized," while some formulations of the carried-MTM term
could also capture part of the same move). **Corrected: daily P&L is
defined as portfolio equity change, not a stitched decomposition.**

The bot has no literal segregated cash balance (real sub-allocation of a
real account, not a segregated one) — but the existing `trial_capital` +
cumulative-P&L pattern is *mathematically equivalent* to a real
Cash + MarketValue equity figure, once cash is treated as capital not
currently tied up in cost-basis-valued positions:

```
Equity_t = trial_capital + trial_cumulative_realized_pnl_t
                          + trial_cumulative_unrealized_pnl_t
                          - trial_cumulative_charges_t
```

(This is the same figure rev 1 called `NAV_t` — the correction is entirely
in how the *daily change* is computed from it, not in this formula.)

```
DailyPnL_t = Equity_t - Equity_{t-1, close}   (assuming no external cash flows — see below)
```

This one differencing rule automatically handles carried positions,
same-day entries, same-day exits, and partial fills correctly, because
`Equity_t` already reflects the true current total regardless of whether
a given rupee is sitting in an open position or was just realized as cash
— no separate bookkeeping path to keep in sync. Realized/unrealized P&L
remain useful **diagnostic** decompositions (shown in logs/telemetry,
and still used per-position via `ctx.prior_close_mtm` for
explainability), but **the circuit breaker is defined on equity change,
full stop** — nothing else feeds `DAILY_HARD_HALT`/`ROLLING_WEEK_HALT`.

**External cash flows**: prohibited, by policy, for the duration of this
trial. `trial_capital` does not change except through the bot's own
recorded trades and charges. If a fresh authoritative snapshot implies an
equity change the bot's own trade/charge records cannot fully explain,
that is not "a deposit" — it's an integrity event: `ENGINE_HALT`, exactly
as any other unexplained state disagreement in §1. This is written down
explicitly so nobody later reads an unexplained equity jump as investment
performance (or a loss) instead of what it actually is: proof something
outside the bot's own accounting happened.

## 6. Portfolio-level daily accounting checkpoint — not solely per-position

Rev 1's `TradeContext.prior_close_mtm` had a real restart problem: if a
position closes and its `TradeContext` is later removed from
`active_trades`, part of the evidence needed to explain that day's P&L
goes with it. **Fixed: the authoritative daily/HWM state is a
portfolio-level structure, persisted in the runner's durable state file
(alongside the existing `entries_today`/`week_pnl_by_day`-class fields),
not derived solely from ephemeral per-position contexts:**

```python
@dataclass
class DailyAccountingCheckpoint:
    trading_day: str
    prior_close_equity: Decimal      # Equity_{t-1, close} - see exact
                                      # definition below, not "whatever was
                                      # last stored"
    day_start_equity: Decimal        # normally == prior_close_equity;
                                      # kept distinct so an integrity reset
                                      # is visible as a deliberate break,
                                      # not silently absorbed
    trial_high_water_mark: Decimal   # monotonic, daily cadence - see below
```

`ctx.prior_close_mtm` (per position) is kept, but demoted to a
**diagnostic-only** field — useful for explaining "which positions moved
the number," never itself the source of a halt decision.

**`prior_close_equity` — exact definition**: the `Equity_t` computed from
the first authoritative broker snapshot obtained during
`reconcile_startup()` on a newly-detected trading day (i.e., the same
`TRADING_DAY_ROLLED_FORWARD` moment the existing single-position engine
already detects, generalized). At that moment nothing has traded yet
today, so this figure *is* operationally "yesterday's close" — captured
once, deterministically, at first reconciliation after rollover, not
"whatever value happened to be sitting in the file." This makes the
definition robust to the bot being stopped overnight (the normal
operating pattern) rather than requiring it to be running at exactly
15:30 IST to capture a literal close print.

**Valuation cadence for `trial_high_water_mark`/drawdown — pinned to the
same cadence V11 was actually validated at, not to poll frequency.**
V11's walk-forward (`anchored_walk_forward_momentum_v11.py`, via
`external_model_comparison.daily_closes()`) measures returns, the
bootstrap, and every reported drawdown-adjacent statistic on **one
observation per trading day** — `daily_closes()` collapses each day's bars
to a single end-of-day close before anything downstream ever sees it.
(The 5% `trial_drawdown_halt_pct` figure itself came from general
risk-management sourcing, not from V11's own measured drawdown — see
`BRAIN_RESEARCH_SPEC_V15`. But the *cadence* choice has one clear,
validated precedent to anchor to, and using anything finer than that
would be a real, silent behavior change: measuring a peak more often than
a strategy was ever validated at mechanically produces higher observed
peaks and larger apparent subsequent drawdowns, purely as a sampling
artifact, independent of what actually happened to the portfolio.)
**Therefore: `trial_high_water_mark` updates exactly once per trading
day**, at the same `reconcile_startup()` rollover moment that captures
`prior_close_equity` — using the *prior* day's now-final close-equivalent
equity, not intraday snapshots:

```
trial_high_water_mark_d = max(trial_high_water_mark_{d-1}, prior_close_equity_d)
TrialDrawdown_d = (trial_high_water_mark_d - prior_close_equity_d) / trial_capital
```
held constant through the trading day, compared against
`trial_drawdown_halt_pct`, class `ENTRY_LOCK`.

**This pin applies only to the HWM/drawdown pair, not to `DailyPnL_t`
(§5).** `DAILY_HARD_HALT`/`ROLLING_WEEK_HALT` are same-day loss circuit
breakers by design and are *supposed* to react intraday — they compare
live `Equity_t` against the fixed `prior_close_equity` anchor throughout
the day, exactly as sourced from the daily-loss-limit risk-management
convention they were built on. The distinction: same-day loss reacting
live during the day is intended behavior; a *peak* being redefined every
few seconds of polling is an accidental behavior change with no
validation precedent. Only the latter is pinned to daily cadence here.

## 7. `REGISTRY_DISAGREEMENT` — distinguish provable staleness from real ambiguity

Rev 1 treated any registry/broker disagreement as an operator-required
halt. That's too blunt: a reservation written just before a crash, where
the BUY was never actually transmitted, is *exactly* the case the
reservation mechanism exists to survive, and the broker can prove it.
**Two outcomes, not one, decided by what can be proven, not inferred:**

- **`PROVABLY_STALE_RESERVATION`** — broker order history conclusively
  shows no matching order (by fingerprint) and no matching position for
  the reserved symbol. Deterministic, automatic recovery: the stale
  reservation is dropped, audit-logged as `STALE_RESERVATION_CLEARED`, no
  operator action, no halt of any class.
- **`UNEXPLAINED_REGISTRY_DISAGREEMENT`** — anything that cannot be
  conclusively proven benign (a matching order exists but its outcome is
  ambiguous, partial evidence, a malformed record). This is an
  `ENGINE_HALT`-class condition (per §2's table) — not an `ENTRY_LOCK`,
  because it reflects unresolved integrity ambiguity, not a financial
  threshold, and unlike financial locks it does not self-clear; it
  requires deliberate operator reconciliation.

## 8. Sector concentration gate — fully specified

`SECTOR_ALREADY_HELD` (rev 1) needs three things stated explicitly, all
now part of the frozen design, not left implicit:

1. **The sector mapping (`portfolio_brain_v9.SECTORS`) is treated as
   immutable/versioned for the duration of the trial.** It is not a
   live-editable input the running system re-reads with different
   contents; a real change to the mapping is a deliberate, logged,
   versioned update between runs, never a silent mid-session change.
2. **A symbol with no entry in the sector mapping fails closed** — reject
   the candidate entry rather than assume it's unconcentrated.
3. **Sector occupancy is `Held ∪ Pending ∪ Reserved`, not just currently-
   held broker positions.** Otherwise two candidates in the same sector,
   evaluated in the same authorization pass before either reaches the
   broker, could both be approved and defeat the gate entirely. This
   follows the exact same reservation-visibility rule as capital in §4 —
   a reserved-but-not-yet-broker-visible entry occupies its sector
   immediately, at reservation time, not at broker-confirmation time.

## 9. Reservation atomicity — the invariant, stated as one indivisible operation

The authorization hierarchy (§6 of rev 1, unchanged) and the capital/sector
reservation logic (§4, §8) must never be split into an independently
observable "check" step and a separate "reserve" step. The required shape:

```
validate fresh snapshot
  -> compute capacity (capital ceiling, sector occupancy, position count)
  -> reserve (symbol + sector + capital) durably
  -> fsync/save
  -> only then return "allowed" to the caller, which then submits
```
as **one indivisible unit of work** within a single authorizer evaluation
call — no candidate's capacity check and its reservation write are ever
two separately-callable operations that another candidate's evaluation
could interleave between. In this design (a single process evaluating
candidates sequentially, never multi-threaded against the same state),
this invariant is satisfied by construction as long as the code is never
refactored to split check-then-reserve across two call sites. **If
multi-process or multi-threaded concurrent evaluation is ever introduced
later, this invariant additionally requires OS-level file locking on the
state file** — the existing durable-write pattern (temp file + fsync +
`os.replace`) makes individual writes atomic but does not by itself
prevent two concurrent readers from both observing pre-reservation
capacity and both reserving against it. Flagged for that future case, not
solved here, since it isn't a real risk in a single-process design.

**This is the highest-priority new adversarial test** (§10, #16) — two
candidates racing for the last unit of capacity, exactly one must win, and
the loser must see a real gate reason, never a corrupted or double-
reserved state.

## 10. Expanded adversarial test matrix (rev 1's 16+9 list, plus)

All of rev 1's scenarios stand. Added for this revision:

17. `PROVABLY_STALE_RESERVATION` auto-clears without operator action;
    `UNEXPLAINED_REGISTRY_DISAGREEMENT` correctly halts and does not.
18. Two same-sector candidates evaluated in one authorization pass — only
    one may reserve; the second sees `SECTOR_ALREADY_HELD` computed
    against `Held ∪ Pending ∪ Reserved`, not stale broker-only state.
19. A financial `ENTRY_LOCK` trips *between* `request_entry()` creating a
    pending intent and `step()` reaching `ENTRY_SUBMIT` — the pending
    entry is gracefully abandoned (removed from `active_trades`,
    audit-logged), the engine is **not** hard-halted, and `request_exit()`
    on a *different*, already-owned position succeeds in the same cycle
    (direct proof of §2's class separation).
20. A carried position is sold today; `DailyPnL_t` (equity-differencing
    method) matches hand-computed economics exactly — the specific
    exit-day double-counting case that motivated §5's correction.
21. `trial_high_water_mark` updates correctly across multiple valid
    snapshots within a single day, not just once at close.
22. An unexplained equity change (simulated as a broker snapshot the
    bot's own trade/charge records can't reconcile) triggers `ENGINE_HALT`
    rather than being absorbed as a gain or loss.

## 11. Explicitly NOT in scope (unchanged from rev 1)

Rebalance transaction atomicity; overnight gap protection; true
correlation-based concentration limits (sector-match is a proxy, not a
replacement); rebalance-as-portfolio-diff framing for the future bridge.
`GrossMarketExposure` (§4) is observational only — not gated on.

## 12. Revised build order

Unchanged shape from rev 1 (P02-A this document, through P02-I the final
spec doc). **P02-B scope, pinned exactly** to: `Config`, `BotState`,
`TradeContext`, `DailyAccountingCheckpoint`, `EntryReservation`/reservation
metadata, `active_trades: Dict[symbol, TradeContext]`, engine-level status
(`STARTUP`/`RECONCILING`/`RUNNING`/`RECONCILIATION_HALT`), per-position
lifecycle status, the `ENTRY_LOCK`/`ENGINE_HALT` representations from §2,
serialization/deserialization, schema validation, durable-state
compatibility checks. **No broker execution logic. No signal bridge. No
clever reconciliation implementation yet. No live trading. No new risk
policy invented during coding** — every number and formula P02-B encodes
must already exist in this document.

**Deserialization rule for P02-B, binding**: invalid or incomplete
persisted state must fail closed. Loading a state file must never
silently substitute a default for a missing or malformed
safety-critical field (e.g. a corrupted `DailyAccountingCheckpoint`
alongside valid `active_trades` must never resolve to
`prior_close_equity = trial_capital` and continue) — it must raise and
surface as an explicit reconciliation/integrity failure
(`ENGINE_HALT`-class, per §2), exactly the same fail-closed posture
already applied to broker-side malformed data throughout this spec,
applied symmetrically to the local state file.

## Release boundary

`LIVE_TRADING_ENABLED` remains `False`, unconditionally, in every file
this project touches. No broker calls capable of a real order were made
producing this document. This spec does not authorize live trading or the
future rebalance bridge. **This document is frozen as of the
valuation-cadence pin above.** From P02-B onward, code is judged against
this spec; further changes to this document happen only in response to an
actual contradiction discovered during implementation or testing, not as
routine iteration.
