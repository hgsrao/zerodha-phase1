# V34 — V11 → P02 Rebalance Bridge — Invariants Specification (R0)

**STATUS: FROZEN.** §13's loop-ownership question is resolved (below);
every other section was already grounded and self-consistent at first
draft. Written before any bridge code exists, per the same discipline
that produced `V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` before P02-B. This
document is the gate: implementation will be judged against it, not the
reverse. Nothing here authorizes live trading. `LIVE_TRADING_ENABLED`
remains `False`, unconditionally. No P02 frozen file is modified. No
broker-order bypass of P02 exists anywhere in this design. No V11
ranking/momentum logic is copied into P02.

Grounded directly in the two systems as they actually exist today, not as
assumed: `V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` (frozen 2026-08-14),
`institutional_engine_v34_p02_multipos_candidate.py` (the frozen P02-I
candidate), and `anchored_walk_forward_momentum_v11.py` /
`external_model_comparison.py` (V11's validated research code). Where a
decision below depends on a fact read from that code rather than inferred,
it's cited.

## 0. Two load-bearing facts this design is built around

These aren't in the original 14-point list but change the shape of
everything below, so they're stated first.

**Fact A — P02 has no live entry point today.** The freeze manifest
(`frozen_releases/P02_I_freeze_20260814/MANIFEST.txt`) states explicitly:
*"this candidate has no live entry point at all."* There is no
`run_production_p02*.py`. `TradingEngineV34P02.step()` exists and is fully
tested, but nothing currently calls it in a loop against a real broker
session. **Consequence: the bridge project is not "add a diff calculator
next to an existing P02 runner" — it also has to answer who drives the
operational loop** (calls `.step()` repeatedly until each pending
entry/exit resolves). §13 below treats this as a bridge responsibility,
not a hidden P02 gap, but it must be named or R1 will silently invent an
answer.

**Fact B — P02 supports full entry or full exit only, never a resize.**
`request_exit()` (`institutional_engine_v34_p02_multipos_candidate.py:454`)
always exits `ctx.filled_qty` — the entire held quantity for that symbol.
There is no partial-exit or resize-in-place primitive. **Consequence: if
V11's target quantity for a KEEP symbol differs from the currently held
quantity, that is not representable as an adjustment.** §3 below makes
this an explicit diff outcome rather than something R1 discovers by
accident.

## 1. TargetPortfolio — definition and provenance

```
Ranked V11 universe (dated signal)
  -> eligibility filters
  -> sector rule
  -> top N
  -> position sizing
  -> TargetPortfolio
```

**Frozen rule: `TargetPortfolio` is an immutable, versioned value object,
never a live query.** It is produced once, from a single dated V11
snapshot, and from that point on is treated as a fact about "what V11
wanted on date X," not a thing that can be silently recomputed by calling
V11 again mid-rebalance. Required fields:

```python
@dataclass(frozen=True)
class TargetPortfolio:
    target_id: str            # see §10 - deterministic hash-based ID
    signal_date: date         # the dated V11 snapshot this derives from
    generated_at: datetime    # wall-clock time TargetPortfolio was built
    positions: FrozenDict[str, TargetPosition]   # symbol -> {quantity, sector, weight}
    universe_version: str     # hash/version of the eligibility+sector ruleset used
    source_model_version: str # V11 code/spec version, e.g. "V11_walk_forward_v1"
```

**V11 supplies quantities, not just weights or ranks.** Position sizing
(the last step in the pipeline above) is V11's responsibility, using
`trial_capital`-equivalent figures V11 already validates against — the
bridge does not re-derive share counts from weights, because that would
mean the bridge silently makes a sizing decision no walk-forward run ever
tested. If V11's sizing methodology changes, that is a V11 research change
requiring its own validation, not a bridge parameter.

**Reproducibility requirement.** Given `signal_date` and
`universe_version`, regenerating `TargetPortfolio` must be deterministic —
same symbols, same quantities, same `target_id`. Non-determinism anywhere
in that pipeline (unseeded randomness, wall-clock-dependent filtering)
is a defect, not a tolerated property, because §10's idempotency guarantee
depends on it.

## 2. Authoritative truth hierarchy

Directly inherited from P02 spec §1, extended one layer:

1. **The broker is the sole source of truth for `CurrentPortfolio`.** The
   bridge never maintains an independent ledger of what is owned; it reads
   broker state fresh at rebalance start and at every completion check.
2. **The V11 signal snapshot is the sole source of truth for
   `TargetPortfolio`.** Once generated, it is immutable (§1) — the bridge
   never edits it to make a rebalance "work."
3. **The bridge computes the diff and owns `RebalancePlan` state (§4);
   it is not authoritative for either side.** A `RebalancePlan` that
   disagrees with a fresh broker snapshot is stale plan state, not a
   reason to distrust the broker.
4. **P02 is the sole authority for whether/how any individual EXIT or
   ENTER may be executed safely.** The bridge proposes; P02 disposes.
5. On any disagreement the bridge cannot resolve deterministically against
   these rules: **fail closed**, same posture as P02 spec §1.4.

## 3. Current-vs-target diff semantics

Three-way classification, computed once per rebalance from a single fresh
broker snapshot and the frozen `TargetPortfolio`:

```
KEEP:  symbol in CURRENT and symbol in TARGET, same quantity
RESIZE: symbol in CURRENT and symbol in TARGET, different quantity
EXIT:  symbol in CURRENT, not in TARGET (or TARGET quantity is 0)
ENTER: symbol in TARGET, not in CURRENT
```

**Frozen rule, following directly from Fact B (§0):** `RESIZE` is not a
distinct executable action — P02 has no primitive for it. A `RESIZE`
outcome is expanded into `EXIT` (full current quantity) followed by
`ENTER` (full target quantity) for that symbol, sequenced exactly like any
other exit/entry pair per §4. This is stated explicitly here so R1 doesn't
either (a) invent a P02 resize call that doesn't exist, or (b) silently
treat a quantity mismatch as a `KEEP` and leave the position at the wrong
size.

The diff is a durable, immutable object once computed:

```python
@dataclass(frozen=True)
class RebalanceDiff:
    target_id: str
    computed_from_current: FrozenDict[str, int]   # broker snapshot at diff time
    keep: FrozenSet[str]
    exits: FrozenDict[str, int]     # symbol -> quantity to exit (includes RESIZE-exit legs)
    enters: FrozenDict[str, int]    # symbol -> quantity to enter (includes RESIZE-enter legs)
```

`computed_from_current` is retained specifically so a later staleness check
(§8, §12) can tell whether the broker portfolio the diff was computed
against still matches reality, rather than re-deriving that from memory.

## 4. Durable RebalancePlan state

One `RebalancePlan` per `target_id`, persisted before any P02 call is
made — same "durable intent before side effect" discipline P02 itself
uses for `TradeContext`/`request_entry()`.

```python
@dataclass
class RebalancePlan:
    target_id: str
    diff: RebalanceDiff
    status: RebalancePlanStatus       # see state machine below
    exit_status: Dict[str, LegStatus]   # per-symbol: PENDING/SUBMITTED/CONFIRMED/FAILED
    enter_status: Dict[str, LegStatus]  # per-symbol: PENDING/SUBMITTED/CONFIRMED/DECLINED/FAILED
    created_at: datetime
    last_broker_check_at: datetime
```

```
RebalancePlanStatus:
  CREATED -> EXITING -> EXITS_COMPLETE -> ENTERING -> COMPLETE
                                                     -> PARTIAL (frozen, see §6)
  any state -> HALTED (integrity ambiguity, fail-closed per §2.5)
```

This state, not the process's memory, is what a restart reconstructs from
(§8).

## 5. Exit-before-entry sequencing

**Frozen: strict exit-before-entry, no exceptions, no interleaving.**

```
1. Submit all EXIT legs (request_exit for each exiting symbol)
2. Drive P02's step() loop until every EXIT leg reaches a terminal state
   (CONFIRMED via broker-reality position=0, or FAILED)
3. Re-fetch broker snapshot; recompute available capital from broker cash
   reality, not from the pre-rebalance estimate
4. Submit ENTER legs, capital-ordered (highest-conviction/highest-weight
   V11 target first) against that recomputed capacity
5. Drive step() until every ENTER leg reaches a terminal state
   (CONFIRMED, DECLINED by P02, or FAILED)
```

Rationale, exactly as you framed it: with limited capital, entering before
exits are confirmed risks a capital-ceiling rejection for an ENTER that
would have fit once the EXIT proceeds landed. No entry is submitted while
any exit leg is still non-terminal.

**Within the EXIT phase, all exits may be submitted concurrently** (as
distinct `request_exit()` calls for distinct symbols) — they don't compete
with each other for capital or sector slots the way entries do, and P02's
exit path is explicitly unconditional on `ENTRY_LOCK` state (P02 spec §2).
**Within the ENTER phase, entries are submitted strictly one at a time,
each only after the previous one reaches a terminal state** — not because
P02 requires it (single-process reservation atomicity already serializes
this per P02 spec §9) but because the bridge needs each entry's outcome
(CONFIRMED vs DECLINED) to correctly account remaining capacity for the
next one, and submitting them all at once would just recreate the exact
race P02 spec §9 flags as the highest-priority adversarial case.

## 6. Partial-completion policy — the hard case, attacked directly

Your worked example:

```
Plan: EXIT RELIANCE, EXIT SBIN, ENTER INFY, ENTER HDFCBANK
Reality: EXIT RELIANCE ✅  EXIT SBIN ✅  ENTER INFY ✅  ENTER HDFCBANK ❌
```

**This is not one policy question — it's two, and conflating them is
exactly how an implementation-default behavior sneaks in:**

**Question 1 — was the failure a P02 decline or a P02/bridge integrity
failure?** These get different treatment (§11):
- If HDFCBANK was **declined** by P02 (`ENTRY_LOCK`-class: capital
  ceiling, sector, `ENTRY_REQUEST_REJECTED: SIMULTANEOUS_POSITION_LIMIT`,
  kill switch) — this is an **expected, in-scope outcome**, not an error.
  P02 gave a definite, provable answer: this entry cannot happen right
  now, for a named reason.
- If HDFCBANK's outcome is **unresolved** (broker acknowledgement lost,
  crash mid-submission, ambiguous reconciliation) — this is `HALTED`, not
  `PARTIAL`. Per §2.5, fail closed. `PARTIAL` is a resolved-but-incomplete
  state, never a stand-in for "we don't know."

**Question 2, assuming a clean P02 decline — is the resulting portfolio
(RELIANCE and SBIN exited, INFY entered, HDFCBANK not entered, cash
sitting uninvested) acceptable, retryable, or grounds to recompute?**

**Frozen rule: `ACCEPT_PARTIAL`, with a bounded, once-per-rebalance retry
first — never silent `RECOMPUTE`, never indefinite retry, never `HALT`
for a clean decline.**

```
On a clean P02 decline of an ENTER leg:
  1. Mark that leg DECLINED with P02's exact reason, in RebalancePlan.
  2. Re-fetch broker snapshot. If the decline reason is capacity-shaped
     (capital ceiling, position-count ceiling) and capacity may have
     freed up because a LATER exit in this same plan hasn't landed yet -
     this cannot happen, because §5 already drains all exits before any
     entry starts. So a capacity-shaped decline after full exit
     completion means capacity is genuinely exhausted, not stale.
  3. Retry that single leg exactly once, immediately, against the fresh
     snapshot. (Guards against a transient, already-cleared ENTRY_LOCK -
     e.g. a rolling-window loss metric that aged out between the first
     attempt and now - without pretending capacity materializes from
     nowhere.)
  4. If the retry also declines: leg is terminally DECLINED. Move on to
     the next ENTER leg in the plan (do not abort the whole plan for one
     declined symbol).
  5. When every leg reaches a terminal state (CONFIRMED or DECLINED, no
     leg left PENDING/SUBMITTED), the plan's overall status is COMPLETE
     if every leg is CONFIRMED, or PARTIAL if at least one leg is
     terminally DECLINED. PARTIAL is a normal, expected, closed status -
     not a fault.
```

**Why not `RECOMPUTE`:** recomputing the target mid-rebalance means asking
V11 for a new opinion using a `signal_date` that no longer matches what's
actually happened to the portfolio so far (some exits/entries already
executed against the old target). That's a new `TargetPortfolio` and
belongs to a *new* rebalance, generated deliberately (§7), never
auto-triggered by a partial failure.

**Why not indefinite retry:** an ENTER that fails capacity twice in a row,
seconds apart, is not going to succeed on a third attempt for any reason
the bridge can reason about — that's the strategy or capital allocation
being genuinely infeasible today, not a transient blip. Retrying forever
just delays converging on a known, honest `PARTIAL` outcome.

**Why not `HALT` for a clean decline:** `HALT` is reserved for integrity
ambiguity (§2.5, §11), where the bridge or P02 cannot prove what happened.
A named, reasoned P02 decline is the opposite of ambiguous — treating a
working safety gate as an emergency would be exactly the kind of alarm
fatigue that erodes trust in the fail-closed posture elsewhere.

**Result for the worked example**: RELIANCE exited, SBIN exited, INFY
entered, HDFCBANK terminally DECLINED (reason recorded). Plan status:
`PARTIAL`. This is logged, not hidden, and is a legitimate rebalance
outcome pending the next scheduled V11 signal — not something the bridge
tries again on its own initiative before then (§7 governs when the next
attempt is even valid).

## 7. Stale-signal validity/expiry

**Frozen: a `TargetPortfolio` is valid only through the end of its
`signal_date`'s trading session (NSE market close), and only if the
rebalance hasn't already started against it.** No grace window into the
next day, no fixed-minutes expiry — V11 is a monthly-rebalance model
(session summary: "holds for weeks... rebalanced monthly"), so
same-session validity is generous relative to how V11 was actually
validated, not tight enough to cause false expiries, while still refusing
to execute a target against a trading day it was never computed for.

```
is_stale(target) = current_trading_day != target.signal_date
```

**A stale target must never be executed silently — this means two
concrete gates, not one:**
- The bridge refuses to *create* a `RebalancePlan` from a stale
  `TargetPortfolio` — hard error, not a warning, not an auto-refresh.
- If a plan is already `EXITING`/`ENTERING` when the trading day rolls
  over mid-rebalance (e.g. a crash-recovery restart the next morning,
  §8), the bridge does **not** resume submitting new legs against the now
  -stale target. It finishes reconciling what's already in flight
  (confirms terminal state for legs already submitted to the broker) but
  submits no new EXIT/ENTER for legs still `PENDING`, and closes the plan
  as `PARTIAL` (or `STALE_ABANDONED` if literally nothing was submitted
  yet) rather than treating an overnight-old target as still current.

## 8. Restart mid-rebalance

Persisted before any P02 call and re-read on every restart, per §4's
`RebalancePlan`:

```
target_id                  - which TargetPortfolio this plan executes
diff.computed_from_current - the CurrentPortfolio snapshot the diff assumed
exit_status / enter_status - per-symbol: PENDING / SUBMITTED / CONFIRMED / DECLINED / FAILED
plan status                - CREATED / EXITING / EXITS_COMPLETE / ENTERING / COMPLETE / PARTIAL / HALTED
```

**Restart procedure, frozen:**
```
1. Load RebalancePlan from durable state. If none exists: nothing to
   recover, normal cold start.
2. Fetch a fresh broker snapshot (CurrentPortfolio, per §2's authority
   rule - never trust the persisted computed_from_current as current fact).
3. For every leg marked SUBMITTED (not yet CONFIRMED/FAILED at crash
   time): reconcile against broker reality using the same fingerprint-
   matching discipline P02 itself uses for ENTRY_UNKNOWN
   (P02 spec / institutional_engine_v34_p01d_candidate.py precedent) -
   determine CONFIRMED, FAILED, or still-ambiguous (-> HALT, never guess).
4. For every leg still PENDING (never submitted): re-check target
   staleness (§7) before resuming. If stale: do not submit, close as
   PARTIAL/STALE_ABANDONED. If still valid: resume sequencing from
   exactly where §5's phase order left off - never re-submit a leg
   already CONFIRMED or terminally DECLINED, and never re-derive the
   diff from a fresh CurrentPortfolio (that would be a silent
   RECOMPUTE, prohibited by §6).
5. Never blindly replay the whole plan. "Replay" here specifically means:
   never re-call request_exit()/request_entry() for a leg whose
   persisted status is already CONFIRMED, DECLINED, or FAILED.
```

This mirrors P02 spec §1's fail-closed reconciliation discipline one layer
up: the bridge reconciles its own plan against broker reality exactly as
P02 reconciles `active_trades` against broker reality.

## 9. Rebalance-complete definition

**Not "all API calls returned success."** Defined against broker reality,
directly extending your framing:

```
rebalance_complete(plan) :=
    broker-owned symbols with nonzero CNC position, restricted to
    {plan.diff.keep ∪ plan.diff.enters}
    == the achievable target
    (target minus any symbol whose ENTER leg is terminally DECLINED)
  AND
    no leg remains PENDING or SUBMITTED (every leg is CONFIRMED, DECLINED,
    or FAILED)
  AND
    no bridge-owned reservation/intent remains open in RebalancePlan state
```

"Achievable target" — not the raw `TargetPortfolio` — is the correct
comparison specifically because of §6: a terminally `DECLINED` leg is a
resolved, expected outcome, not an open question, and comparing broker
reality against the *unadjusted* original target would make every
`PARTIAL` rebalance look forever "incomplete" even after every leg has
reached a terminal state. Complete and fully-achieved are different
claims; only the latter requires zero declines.

## 10. Rebalance/signal idempotency

**`target_id` is a deterministic hash, not a UUID or timestamp**, per your
`V11_2026-09-01_TARGET_<hash>` sketch:

```
target_id = f"V11_{signal_date.isoformat()}_{sha256(canonical_json(positions, universe_version))[:16]}"
```

Hashing the actual position set (not just the date) means an accidental
double-generation from the same inputs produces the same `target_id`
rather than two IDs for one logical target — and a genuine V11 methodology
change (different `universe_version` or different resulting positions)
produces a different ID even on the same date, so it's never silently
conflated with an earlier same-day attempt.

**Frozen idempotency rule**: before creating a new `RebalancePlan`, the
bridge checks for an existing plan with the same `target_id`.
- No existing plan: proceed normally (§4).
- Existing plan, status `COMPLETE` or `PARTIAL`: **no-op, return the
  existing plan's outcome.** The same target presented twice must never
  create duplicate exits or entries — this is the direct analog of P02's
  own entry-fingerprint duplicate prevention, one layer up.
- Existing plan, status `HALTED`: refuse to proceed automatically; this
  requires the same kind of deliberate operator reconciliation P02
  requires for `ENGINE_HALT` (P02 spec §7).
- Existing plan, status `CREATED`/`EXITING`/`ENTERING` (mid-flight): this
  is the restart case (§8), not a new-plan case — resume, don't recreate.

## 11. Interaction with P02 declines (sector, capital, position-count, ENTRY_LOCK)

Grounded in the real decline surface, not a guess. Reading
`institutional_engine_v34_p02_multipos_candidate.py:398-452` and P02 spec
§2 together, P02 declines an ENTER through **two structurally different
channels the bridge must handle differently**:

- **Synchronous rejection at `request_entry()` call time** — raises
  `RuntimeError` immediately: `SYMBOL_ALREADY_HELD`,
  `SIMULTANEOUS_POSITION_LIMIT`, market-closed window, malformed
  params. The bridge catches this, records the leg as terminally
  `DECLINED` with the exception's reason text, right away — no
  observation step needed, P02 never created a pending intent.
- **Asynchronous `ENTRY_LOCK`-class abandonment during `step()`** —
  `request_entry()` returns successfully (`"STATE_CHANGED"`), an intent
  is created, but P02's authorizer later evaluates it during
  `ENTRY_SUBMIT` handling and can abandon it (capital ceiling, sector
  concentration, a tripped daily/drawdown lock, kill switch) — audit-
  logged as `ENTRY_ABANDONED_POLICY_HALT`, with **no exception raised
  back to the original caller**, because the caller had already moved on
  by the time `step()` processes it. **The bridge cannot know this
  synchronously — it must poll `RebalancePlan`'s driven observation of
  P02 state (active_trades / audit log / broker reality) after each
  `step()` cycle to detect it**, exactly the same "don't trust the return
  value, trust reconciled reality" posture as §9.

**In both cases, the bridge's response is identical and already specified
by §6: record the reason, apply the one-retry rule, then treat as
terminally `DECLINED` if the retry also fails.** The bridge never
distinguishes these two channels in its own outcome semantics — only in
*how* it detects them (immediate exception vs. polled reconciliation).
**The bridge never bypasses P02 to force an entry through** — there is no
code path in this design that calls `self.kite.place_order` or any raw
broker order function directly; every position change goes through
`request_entry()`/`request_exit()`.

**Sector and position-count interaction, specifically:** because P02
spec §8 defines sector occupancy as `Held ∪ Pending ∪ Reserved` (not just
currently-held), and §5 already drains all exits before any entry begins,
by the time the bridge submits ENTER legs the "Held" side only reflects
symbols in `plan.diff.keep` — exited symbols' sectors are already
vacated. The bridge does not need to reason about sector capacity
speculatively; it submits and lets P02's authorizer, which already has
the correct up-to-date view, make the call.

## 12. Broker reality changes independently mid-rebalance

Covers manual trades, unexpected positions, changed quantities, or
foreign (non-bridge, non-P02-tagged) orders appearing during a rebalance.

**Frozen rule: any broker-side change the bridge cannot attribute to its
own `RebalancePlan` legs or to P02's own tagged orders (`V3.4_ENTRY`/
`V3.4_EXIT`-tag family) is an integrity condition, not a rebalancing
input.** Concretely:
- At every re-fetch of `CurrentPortfolio` (§5 step 3, §8 step 2), the
  bridge compares the fetched positions against
  `plan.diff.computed_from_current` plus the confirmed effect of every
  `CONFIRMED` leg so far. Any symbol/quantity present that this
  arithmetic can't explain is `UNEXPLAINED_BROKER_STATE` — the bridge
  halts the plan (`HALTED`, §4) rather than incorporating the surprise
  into its diff. This is the direct analog of P02 spec §5's "unexplained
  equity jump is an integrity event, never investment performance" rule,
  applied to positions instead of equity.
- The bridge never auto-recomputes a new target to account for the
  surprise (that's a human decision — a manual trade during an automated
  rebalance means a human is intentionally overriding, and the bridge
  should stop and ask, not guess intent).
- This is symmetric with P02's own `UNEXPECTED_BROKER_EXPOSURE`-class
  handling in `reconcile_startup()` (orphan detection) — the bridge is
  not inventing a new philosophy here, just applying the existing one to
  its own layer.

## 13. Responsibility boundaries — and who drives the loop

```
V11    : decides WHAT the portfolio should be.        Never sees order IDs.
Bridge : decides WHAT must change (diff) and WHEN      Never bypasses P02.
         legs are submitted (sequencing, §5).          Never knows momentum
                                                         ranking logic.
P02    : decides WHETHER/HOW each individual EXIT/     Never diffs portfolios.
         ENTER may happen safely.                       Never talks to V11.
Broker : decides WHAT actually exists.                  Authoritative, always.
```

**Addressing Fact A (§0): resolved.** P02 has no runner of its own;
something has to call `.step()` repeatedly until every leg resolves. Two
shapes were possible:
- (a) the bridge process itself calls `.step()` in a loop (bridge embeds
  the operational loop), or
- (b) a separate, minimal P02 runner process exists, and the bridge only
  submits intents (`request_entry`/`request_exit`) and polls state,
  never calling `.step()` itself.

**Decision: (b), a separate minimal P02 runner.** The bridge never calls
`.step()` directly — it only calls `request_entry()`/`request_exit()` and
observes `active_trades`/broker reality/audit log to detect outcomes
(§11's polled-reconciliation posture already assumed this shape). The
runner is a dedicated, always-on process that drives P02's poll loop
continuously, mirroring the existing single-position engine's operational
pattern (`run_production_p01d_candidate.py`) rather than the bridge owning
a loop that only runs during an active rebalance. Building that runner is
its own piece of work, separate from R1 (the `TargetPortfolio` object) —
tracked as a distinct next step, not bundled into R1.

## 14. Accepted market risks — explicitly out of scope for software correctness

The following are real risks a live V11+bridge+P02 system will face and
this specification does **not** attempt to eliminate or compensate for
them in software; they're strategy/market realities, not defects:

- **Overnight gaps** between EXIT confirmation and ENTER submission if a
  rebalance spans a session boundary (should not happen given §7's
  same-session validity rule under normal operation, but a `PARTIAL`
  plan resumed the next day, per §7, could still see gapped entry prices
  versus the original target's assumed pricing).
- **Inability to fill at V11's assumed/target price** — `TargetPortfolio`
  quantities are sized against a signal-time price; by execution time the
  market has moved. This is ordinary execution slippage, already a known
  V11 cost-model concern (`BRAIN_RESEARCH_SPEC_V14_REALISTIC_COSTS.md`),
  not something the bridge "solves."
- **Adverse price movement during sequencing** — because §5 enforces
  exit-before-entry, there is unavoidable time between an EXIT confirming
  and its corresponding capital being redeployed via ENTER; the market
  can move against the plan during that window. This is the direct cost
  of the capital-safety property §5 buys, and is accepted, not treated as
  a bug.
- **A `PARTIAL` outcome having different portfolio-level risk
  characteristics than the intended `TargetPortfolio`** (e.g., ending up
  overweight cash, or with an unintended sector skew because one entry
  declined) — this is a consequence of P02's safety gates doing their job
  correctly, explicitly accepted in §6, not a failure to be engineered
  away.

Explicitly **not** accepted as a risk to route around: any of the above
becoming a reason to bypass P02's gates, guess at broker state, or retry
indefinitely. §2's authority hierarchy and §6's bounded-retry rule apply
regardless of how much these market risks tempt a "just force it through"
shortcut.

## 15. Release boundary

R0 ends here, with this written specification only.

- No live trading. `LIVE_TRADING_ENABLED` remains `False` everywhere it
  exists.
- No P02 file modified. Nothing in `frozen_releases/P02_I_freeze_20260814/`
  or the working-copy P02 candidate was written to during this design
  pass — only read, to ground §11/§0's facts.
- No broker-order bypass — every position-changing action in this design
  routes through `request_entry()`/`request_exit()`; no raw
  `place_order`/`modify_order`/`cancel_order` call is proposed anywhere
  in the bridge.
- No V11 ranking/momentum logic is proposed to move into P02, and no P02
  risk-gate logic is proposed to move into V11 or the bridge — §13's
  boundary table is binding on R1.

---

## Report

**Resolved during freeze review:**
1. §13 — which process drives P02's `.step()` loop during a rebalance.
   **Decided: a separate, minimal P02 runner** — the bridge only calls
   `request_entry()`/`request_exit()` and polls state; it never drives
   `.step()` itself. Building that runner is tracked as its own piece of
   work, separate from R1.

**Still unresolved decisions** (deliberately left open, not oversights):
1. Exact mechanism for "human is notified of a `HALTED` or `PARTIAL`
   plan" (log-only vs. an explicit alert channel) — operational tooling,
   not a rebalance invariant, deferred to R1/R3.
2. Whether a `PARTIAL` plan with a terminally `DECLINED` leg should ever
   be eligible for a bridge-initiated retry *on the next V11 signal* using
   only the undeclined portion as its new baseline, or whether every new
   `TargetPortfolio` always diffs from scratch against current broker
   reality regardless of prior declines. Current answer implicit in §2/§3
   is "diffs from scratch, always" (the bridge holds no memory of past
   declines once a plan reaches a terminal status) — worth an explicit
   sign-off since it means a repeatedly-declined symbol gets no special
   treatment on the next attempt, by design.

**Contradictions found and resolved during this pass:**
- Your original Thread-2 framing (item 4, exit-before-entry) and item 5
  (capital availability after exits) implicitly assumed entries could be
  submitted as a batch once exits complete. §5 tightens this to
  strictly-sequential entries specifically because P02 spec §9's
  reservation-atomicity discussion flags concurrent-entry racing as its
  *highest-priority* adversarial concern — batching entries here would
  have silently reintroduced the exact race P02 was designed to prevent
  at a layer where P02 can no longer see it coming (the bridge, not P02,
  would be creating the race by firing multiple `request_entry()` calls
  before observing each one's outcome).
- No hard contradiction found between the two source specs otherwise —
  P02 spec §11 explicitly lists "rebalance-as-portfolio-diff framing for
  the future bridge" as out of scope for P02, which is consistent with
  everything here.

**Assumptions inherited from V11:**
- V11 remains a monthly-rebalance, multi-position, cross-sectional
  momentum model (per `SESSION_SUMMARY_20260814.md` and
  `BRAIN_RESEARCH_SPEC_V11_MOMENTUM_WALK_FORWARD.md`) — §7's same-session
  staleness window assumes rebalances are infrequent relative to a
  trading day; if V11's cadence ever changes to weekly or faster, §7
  needs re-review, not silent reuse.
- V11 supplies fully-sized target quantities, not just ranks/weights
  (§1) — if V11's research output format changes, the bridge's sizing
  assumption breaks and must be re-frozen, not patched around.

**Assumptions inherited from P02:**
- Six-position structural ceiling and `max_simultaneous_positions` config
  (P02 spec, `Config`) are treated as given, not renegotiated by the
  bridge — the bridge's target sizing (V11's job, §1) must already respect
  whatever ceiling P02 enforces, or every rebalance will chronically hit
  `SIMULTANEOUS_POSITION_LIMIT`.
- P02's sector mapping (`portfolio_brain_v9.SECTORS`) is the sector
  vocabulary both V11's target construction and the bridge's diff must
  use — no separate bridge-side sector taxonomy is introduced.
- P02's "no live entry point" state (Fact A, §0) is current as of this
  writing (2026-08-14); if a P02 runner is built independently before the
  bridge is implemented, §13's open question narrows to one answer
  automatically.

**Is R0 ready to freeze before R1 begins?**

**Yes — frozen as of this revision.** §13 is resolved (separate minimal
P02 runner); every other section (§1-§12, §14-§15) was already groundable
in real code and self-consistent. R1 (the immutable `TargetPortfolio`
object) begins against this frozen document, judged against it exactly as
P02-B was judged against `V34_P02_PORTFOLIO_INVARIANTS_SPEC.md`.
