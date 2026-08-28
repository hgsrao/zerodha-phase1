# P01D Intraday Brain V1 — S0: Execution-Body Interface Freeze

**New, separate thread**, opened per owner decision 2026-08-14: keep P01D,
build a strategy on top of it rather than discard it. Scope for tonight is
**S0 only** — define exactly what a strategy is allowed to send the
existing P01D execution body. **S1–S9 (reconstructing V1–V8 as contestants,
new hypotheses, the common backtest lab, walk-forward, adversarial
robustness, picking one, the bridge, shadow run, release decision) are not
started.** That's real strategy-research work, correctly scoped as its own
multi-session project, not something to rush through the same night as
three other design docs.

Target product for V1 of this brain, per owner preference: **Option A, the
P01D Intraday Tactical Bot** — one or two high-quality trades/day, slower
setup detection with a regime filter, one position at a time, stop defined
before entry, no overnight positions. **Not** Option B (true scalping) —
explicitly deferred to a hypothetical future `P01D_SCALP_V1` candidate if
evidence ever supports it. This choice shapes several answers below; it is
frozen for V1 and revisited only with its own evidence, not by drift.

`LIVE_TRADING_ENABLED` untouched. No code modified. No runner started. No
broker call made.

## 0. What the execution body actually accepts today — read, not assumed

Grounded directly in `institutional_engine_v34_p01d_candidate.py` and
`run_production_p01d_candidate.py`, re-verified in this pass, not carried
over from memory:

```python
def request_entry(
    self, *, symbol: str, quantity: int, price: Decimal,
    entry_tag: str = "V3.4_ENTRY",
) -> str
```
(`institutional_engine_v34_p01d_candidate.py:340`)

That's the entire caller-supplied surface. Four facts about what's *not*
there, each confirmed by reading the code rather than inferred:

1. **No stop parameter exists.** `stop_loss_pct: Decimal = Decimal("0.02")`
   is a fixed **config-level** percentage (`Config` dataclass and the
   authorizer's `stop_loss_pct` constructor argument,
   `run_production_p01d_candidate.py:840`) used to size risk
   (`per_share_risk = price * self.stop_loss_pct`) and, downstream, to
   place the actual protective SL-M order. The engine's `PROTECTION` state
   (`institutional_engine_v34_p01d_candidate.py:1911`) only *adopts* a
   pre-existing matching SL order (tag `V3.3_SL`/`V3.4_SL`) — it doesn't
   compute one from a caller-supplied stop price, because there's nowhere
   for a caller to supply one.
2. **BUY-only.** Entry submission is hardcoded to
   `transaction_type="BUY"` (`institutional_engine_v34_p01d_candidate.py:1696`).
   There is no code path for a short-side entry anywhere in `step()`'s
   `ENTRY_SUBMIT` handling. This is a cash-equity-shaped intraday body,
   long-only, by construction.
3. **No expiry/validity parameter.** `request_entry()` either creates the
   durable intent now or is rejected now (market-closed, symbol-already-
   held, etc.) — there's no "this signal is only good until HH:MM" concept
   built in.
4. **No signal-identity or reason field.** `entry_tag` exists but is
   pinned to the exact literal `"V3.4_ENTRY"` — `request_entry` raises if
   it's anything else (`institutional_engine_v34_p01d_candidate.py`, the
   `ENTRY_SUBMIT` handler's tag check). It's a safety constant, not a
   free-text or per-signal field.

**Everything the request-frequency controls need is already fixed at the
authorizer/config layer, not supplied per-call** — confirmed:
`max_daily_entries: int = 2`, `max_simultaneous_positions: int = 1`,
`entry_cooldown_seconds: int = 900` (15 min)
(`run_production_p01d_candidate.py:119,125-126`). This is exactly the
"maximum two entries, one simultaneous position, cooldowns" the owner
described from memory — now cited to line numbers instead of recalled.

## 1. The StrategyIntent contract — frozen shape, mapped honestly against §0

The owner's proposed clean-intent fields, evaluated one at a time against
what actually exists:

```python
@dataclass(frozen=True)
class StrategyIntent:
    symbol: str
    side: Literal["BUY"]          # see below - not a real choice today
    quantity: int                  # or risk_pct, see below
    entry_reference: Decimal       # the price the signal was computed at
    stop_price: Decimal            # advisory today - see below
    signal_id: str                 # brain's own identifier, not entry_tag
    valid_until: datetime          # enforced by the BRIDGE, not the engine
    reason: str                    # free text / feature snapshot, logging only
    score: Decimal                 # ranking signal, logging only
```

**Field-by-field disposition — frozen, not left implicit:**

- **`side`**: fixed to `"BUY"` for V1. The type system should make this
  a `Literal["BUY"]`, not an enum inviting a future `"SELL"` value, so a
  strategy that produces a short signal fails at construction time, not
  silently at the broker boundary. Revisit only if a dedicated short-side
  engine candidate is ever built — not a V1 concern.
- **`quantity`**: the bridge computes this from the strategy's declared
  risk allowance and `stop_price` (risk-based sizing:
  `quantity = floor(risk_amount / abs(entry_reference - stop_price))`),
  **then** passes only the resulting integer `quantity` to
  `request_entry()`. The strategy may express intent as risk_pct/₹risk;
  the engine only ever sees a plain integer, exactly as it does today.
- **`entry_reference`**: passed through as `request_entry`'s `price`
  argument. **Frozen rule, following directly from the engine's own
  `ENTRY_SUBMIT` reconciliation logic** (which matches existing broker
  orders on `order_price == ctx.entry_price` — an exact match, no
  tolerance band): the bridge must submit `entry_reference` verbatim, not
  a recomputed or "improved" price, or duplicate-order fingerprinting
  breaks silently.
- **`stop_price`**: **this is the one genuine architectural gap, named
  explicitly rather than hidden inside a bridge workaround.** Today's
  engine has no parameter for it — protective stops are placed at a fixed
  `stop_loss_pct` (2%) regardless of what a strategy wants. Two honest
  options, and this must be picked before S7, not discovered during it:
  - **(a) Advisory-only for V1**: the strategy's `stop_price` is used
    solely for the bridge's own risk-based position sizing (above); the
    engine still places its own fixed-2% protective stop after fill,
    independently. Simplest, zero P01D modification, but means a
    strategy whose edge depends on a *tighter or wider* stop than 2% is
    misrepresented by the executed trade.
  - **(b) Extend `request_entry()`/the protection flow to accept a
    caller-supplied stop distance**, gated by the same discipline P02's
    changes get — a proposed, reviewed, tested modification to the P01D
    candidate, not a silent patch. This is real engineering work, not a
    bridge-only change.
  **Recommendation, not yet frozen — needs an explicit owner call**: start
  with (a) for the S1–S5 research phase (research doesn't need the real
  engine at all, only the common backtest lab), and decide between (a)
  and (b) at S6 once a specific strategy's stop-sensitivity is known from
  actual walk-forward results — deciding now, with no candidate strategy
  yet, would be exactly the "redesign the body around whichever strategy
  looks attractive" mistake the owner explicitly ruled out.
- **`signal_id`**: bridge-internal bookkeeping only (idempotency,
  duplicate-signal detection, logging) — never sent to the engine.
  `entry_tag` stays pinned to `"V3.4_ENTRY"` exactly as it is today; the
  engine remains ignorant of which brain or signal produced a request,
  by design (§13 of the V11 bridge spec's responsibility-boundary
  principle applies here too: the execution body must not know strategy
  identity).
- **`valid_until`**: **enforced entirely by the bridge, before it ever
  calls `request_entry()`.** The engine has no expiry concept and none is
  proposed — a stale signal must simply never reach `request_entry()` in
  the first place, mirroring the V11 bridge's own stale-target rule
  (never execute a stale instruction silently).
- **`reason`, `score`**: logging/audit fields only, attached to the
  bridge's own durable intent record, never passed to the engine.

## 2. Authorization independence — binding, mirrors the V11 bridge's own rule

**The brain proposes; P01D independently decides.** Concretely: the
bridge calls `request_entry()` exactly as any other caller would, and
whatever `RunnerEntryAuthorizer`/`P03RiskController` decide (capital
ceiling, `DAILY_ENTRY_LIMIT`, cooldown, kill switch, market-hours gate)
applies unchanged and unmodified. **No S0-or-later change may add a bypass
path, a strategy-specific exception, or a "trust the brain, skip the gate"
shortcut.** A `StrategyIntent` that the authorizer declines is a normal,
expected, logged outcome — not a bridge failure to route around.

This is the same "propose vs. dispose" boundary already frozen for the
V11→P02 bridge (`V34_V11_P02_REBALANCE_INVARIANTS_SPEC.md` §13) — restated
here rather than assumed, because P01D and P02 are different engines with
independently-evaluated gates; the *principle* transfers, the specific
gate mechanics do not.

## 3. Multi-brain tournament — how it coexists with this interface, explicitly

The owner's "let V1/V2/V4/V7/V8/new-candidates/NIFTY-baseline all run
simultaneously in the shadow observatory" idea requires **zero changes to
this spec** to support, and that's worth stating precisely: every
contestant brain runs exactly like today's existing shadow modules
(`external_momentum_shadow.py`, `orb_shadow_observer.py`, etc.) —
**producing telemetry only, never constructing a `StrategyIntent` and
never calling the bridge.** Only the single strategy that survives S6
(picked, not merged, not run "a little bit live" while still contesting)
ever gets a bridge built for it in S7. Running the tournament and running
the eventually-chosen brain through this interface are sequential, not
simultaneous, for any given brain — a contestant graduates to §0-§2's
interface only once, at S6, not before.

## 4. What S0 explicitly does not decide

- Which strategy (that's S1-S6's job, not S0's).
- Whether risk is expressed as fixed quantity, ₹ risk, or %-of-equity risk
  in the strategy's own output — deferred to whichever brain wins S6,
  since different setup types (breakout vs. mean-reversion vs.
  regime-filtered momentum) naturally size risk differently. §1's
  `StrategyIntent.quantity` derivation handles any of these upstream of
  the bridge.
- The (a)/(b) `stop_price` question above — flagged, not resolved.
- Whether `max_daily_entries=2`/`entry_cooldown_seconds=900` are the right
  *numbers* for an intraday tactical bot specifically (they were tuned for
  the original single-symbol design, not necessarily validated against a
  ranked-candidate intraday brain) — a config-tuning question for S6/S7,
  not an interface question; §0 documents what exists today, it doesn't
  bless the numbers as final.

## 5. Release boundary

No live trading. `LIVE_TRADING_ENABLED` remains `False`. No modification
to `institutional_engine_v34_p01d_candidate.py` or
`run_production_p01d_candidate.py` was made or proposed as code in this
pass — §1(b) names a possible future modification explicitly as a
decision point, not as work performed. No P02 file touched. No broker
call made.

---

## Report

**Unresolved decisions:**
1. `stop_price` advisory-only vs. engine extension (§1) — recommended to
   defer to S6, not decide now.
2. Whether `max_daily_entries`/`entry_cooldown_seconds` need retuning for
   an intraday-tactical (vs. original single-symbol) shape — deferred to
   S6/S7.
3. Risk-expression convention in `StrategyIntent` (fixed qty vs. ₹risk vs.
   %-equity) — deferred until a specific S6 winner's own methodology is
   known.

**Contradictions found:** none between this spec and the V11 bridge spec
— both independently arrived at the same "propose vs. dispose" boundary
and the same "never execute a stale/unauthorized instruction silently"
posture, which is a consistency check in this design's favor, not a
coincidence to paper over.

**Assumptions inherited from P01D (frozen 2026-08-14 revalidation):**
Execution safety and regression are strong (577/577); BUY-only,
single-position, 2-entries/day, 15-min-cooldown shape is a real,
structural property of the current candidate, not a config default that
can be casually reinterpreted without re-certification.

**Assumptions inherited from the historical brain reconstruction:**
V1-V8 are contestants, not proven strategies (§3's tournament framing
already treats them that way); V9's shape (multi-position, sector-
controlled) doesn't fit this single-position body regardless of tournament
outcome, so V9 itself is not eligible to win S6 as-is — noted so nobody
rediscovers that mismatch mid-tournament.

**Is S0 ready to freeze before S1 begins?**

Yes, with the three unresolved decisions above explicitly carried forward
as open (not silently resolved by an implementation default) rather than
blocking. Unlike the V11 bridge's R0 (which had one blocking open question
about loop ownership), none of S0's three open items block starting S1 —
S1-S5 is pure research against a common backtest lab and doesn't touch
`request_entry()` at all. The `stop_price` question specifically should be
revisited with real data in hand at S6, not guessed at now.
