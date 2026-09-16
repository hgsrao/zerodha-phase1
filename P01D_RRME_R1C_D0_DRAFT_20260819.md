# P01D RRME R1-C — D0 (Gate G2)

**Status: FROZEN — 2026-08-19, owner confirmation: "Let us go ahead...
It's totally frozen."** Every proposed number in this document is
adopted exactly as proposed, no amendments. From this point forward,
changing any parameter below requires a new identity (`R1-C V2`) and a
fresh accrual period, per §5 of the concept document — not an edit here.
Companion to `P01D_RRME_R1C_PROPOSAL_20260819.md` (concept) and
`R1C_G1_BRIDGE_HASH_MANIFEST_20260819.json` (certified bridge, G1
complete).

Per R1-C's own rule (proposal §4): every number below must be frozen
**before** any combined outcome is calculated - including before the
live observation window opens. Nothing here may be adjusted after
watching how the composite behaves, live or historical, without that
producing a new identity (`R1-C V2`), per the same document's §5.

---

## 1. Component parameterization — reused verbatim from already-frozen R0 candidates, not newly chosen

Each role reuses one specific, already-frozen R0 candidate exactly as
tested - never retuned to fit this architecture (proposal §2's own
rule). Provenance stated for each pick, not asserted blind:

| Role | Reused from | Exact frozen parameters | Why this one |
|---|---|---|---|
| **Context filter** (V2-C) | V2-C's single frozen classifier | 9 features, threshold `P(REVERTING) >= 0.77278226` | Only one V2-C classifier exists - no choice to make |
| **Setup detector** (Pillar II) | D1 candidate **C3** | `z_window=20, z_thresh=-2.0, idio_thresh=-0.003, vol_mult=2.0, vwap_dist=0.003, combine=AND, target=VWAP_RECLAIM` | The only candidate using the VWAP-reclaim exit style — the literal phenomenon (84% reclaim rate, `V2C_A2`/fold-attribution findings) motivating this whole architecture. Picking any other candidate here would be choosing based on which one's *P&L* looks better on already-exposed data - exactly what §5's firewall forbids. C3 is chosen for what it *measures*, not what it *returned*. |
| **Timing confirmation** (Pillar I) | D1 candidate **C4** | `short_rs=6, long_rs=30, breakout=20, participation=1.5, combine=AND, stop_mult=1.5, rr=2.0` | The frozen "best of 5" already named in R0's own closure record (`P01D_RRME_INTERIM_STATUS_20260818.md`) - a designation made at closure time, before this proposal existed, so using it here is not picking-after-seeing-R1-C-results. |

## 2. State timing — genuinely new decisions, no existing frozen precedent, proposed not assumed

These have no R0 precedent to reuse and need the owner's explicit
confirmation, not a silent default:

- **`REVERSION_PERMITTED` validity window** (how many subsequent
  trading sessions V2-C's daily permission stays active): **proposed 5
  trading sessions** from the qualifying event's own `t0` - matches
  V2-C's own D0/D0-A1 resolver's outcome-observation horizon (the same
  window V2-C's own REVERTING/DETERIORATING labels were resolved over),
  reused rather than invented. **Needs explicit confirmation - a
  different number is a legitimate choice, this is a proposal.**
- **`REVERSION_SETUP` validity window** (how long Pillar II's setup
  stays eligible for Pillar I's confirmation once it fires): **proposed
  same trading session only** - both Pillar I and Pillar II are
  intraday-only, mandatory-square-off designs by their own original R0
  preregistration; extending a setup across a session boundary would
  contradict that. **Needs explicit confirmation.**
- **Entry**: next causally available 1-minute executable price after
  Pillar I's confirmation fires (matches proposal §4 item 4 exactly,
  already effectively frozen there).
- **Exit / stop**: Pillar I's C4 exit rule (`stop_mult=1.5, rr=2.0`,
  fixed R-multiple target, session-end square-off) applies to the
  composite trade unmodified - the composite does not invent its own
  exit; it inherits the confirmation leg's own frozen exit, since no
  R0 track ever specified a different combined-system exit and
  inventing one now would be an unreviewed new parameter.

## 3. Capital, concurrency, sizing

- **Notional per composite trade**: proposed **Rs 100,000**, reused
  directly from V2-C's own frozen `NOTIONAL_TARGET` (not re-derived -
  matching V2-C's own floor/`UNTRADEABLE_AT_NOTIONAL` sizing rule from
  A2). **Needs explicit confirmation** - this is a research notional,
  not a live-capital decision (LIVE_TRADING_ENABLED stays False
  regardless).
- **Concurrency**: proposed **one open composite position per symbol**
  (matches the frozen authorizer pattern already used elsewhere in this
  project - `SYMBOL_ALREADY_HELD`), **no explicit portfolio-level cap
  proposed yet** - open question, needs the owner's number.
- **Sector caps**: none proposed - open question if the owner wants one.

## 4. Named outcomes (per proposal §9, no PASS possible on exposed data)

```
TRAIN_FEASIBILITY_NO_GO           - closes R1-C V1
ELIGIBLE_FOR_PROSPECTIVE_ACCRUAL  - not evidence, only justifies waiting
[live/prospective VALIDATION]     - PASS / FAIL / INCONCLUSIVE, the
                                     only stage where PASS can appear
```

## 5. What freezing this D0 actually authorizes

Freezing this document (as-is or as amended by the owner) authorizes:
- Building the composite decision logic exactly as specified above,
  wired to the G1-certified bridge.
- Running it against the live observation feed already scoped in
  `P01D_R0_POST_CLOSURE_LIVE_OBSERVATION_20260819.md`, as R1-C's own
  genuinely fresh, prospective accrual (its only path to ever report a
  real PASS) - not merely as another exposed diagnostic.

Freezing this document does **not** authorize: any broker write,
`request_entry()`, promotion, or HOLDOUT access. Every rule in the
concept document (§1, §5) remains in force unchanged.

## 6. Freeze confirmation

All five items below were adopted exactly as proposed, no amendments,
per the owner's explicit go-ahead:

1. `REVERSION_PERMITTED` window = **5 trading sessions**.
2. `REVERSION_SETUP` window = **same session only**.
3. Notional = **Rs 100,000**.
4. Portfolio-level concurrency cap = **none set** — only the
   per-symbol cap (§3) is frozen; a portfolio-level number remains
   genuinely open and is not needed for a single-symbol-scoped live
   observation.
5. Component choices (V2-C's one classifier; Pillar II **C3**; Pillar I
   **C4**) confirmed as proposed in §1.

**D0 is now frozen. Next: build the composite decision engine against
these exact, unchangeable parameters, then wire it to the live
observation feed already scoped in
`P01D_R0_POST_CLOSURE_LIVE_OBSERVATION_20260819.md`.**
