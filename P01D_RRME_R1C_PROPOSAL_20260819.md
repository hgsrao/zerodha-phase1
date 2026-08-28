# P01D RRME R1-C — Three-Pillar Composite State Machine (Candidate Concept)

**Status: DRAFT PROPOSAL ONLY — NOT FROZEN, NOT AUTHORIZED.**
**Date:** 2026-08-19
**Proposed identity:** `P01D_R1_THREE_PILLAR_COMPOSITE_STATE_MACHINE_V1`
**Relationship to the other R1 candidates:** presented as a **third**,
fully independent concept alongside `P01D_RRME_R1_PROPOSAL_20260819.md`
(pre-open price-discovery) and `P01D_RRME_R1B_PROPOSAL_20260819.md`
(state-informed exhaustion, V11-side). **None of the three is superseded
or deleted by this document.** The owner may choose one, several as
separate sequenced tracks, or none. Nothing here decides that.

This document authorizes no data acquisition, experiment, broker call,
HOLDOUT access, or production integration. `LIVE_TRADING_ENABLED` remains
`False`.

---

## 1. Classification — read this section first

**This is not R0 Stage 3 Router/Combination Research, and cannot become
that.** R0's own frozen architecture requires, as an explicit precondition
for combination research: *"if — and only if — all three eventually
validate independently."* None of the three did — Pillar I and Pillar II
closed FAIL at D1 discovery, V2-C closed FAIL at the economic gate. That
precondition is not satisfied and this document does not attempt to argue
around it.

**What this is instead: a brand-new R1-track hypothesis** — on exactly the
same footing as R1 and R1-B — that happens to reuse R0's three *frozen
components* (V2-C's exact classifier/threshold, Pillar II's exact
VWAP-reclaim setup, Pillar I's exact confirmation rule) as engineering
building blocks inside a new, sequential architecture, rather than
re-deriving three new signals from scratch. Because it is a new hypothesis
and not a continuation of Stage 3, it must earn its own complete gate
sequence, from G0, with zero inherited credit from any of R0's own
results — including, critically, zero credit from anything computed on
data any of the three components has already been tested against.

**To be precise about how new this is**: this is *not* a continuation of
R0's own originally-sketched combination idea (which was, in R0's own
words, "V2-C may veto a Pillar-II event only inside its own **validated**
domain" — i.e., contingent on independent validation, and never
envisioned Pillar I confirming a setup from an already-**failed** Pillar
II). Every one of R1-C's three role assignments — V2-C as filter, Pillar
II as setup, Pillar I as confirmation — was chosen *after* seeing each
component's specific failure mode. This must be disclosed as exactly
that: a genuinely new, result-informed architecture, not a resumption of
anything R0 already had in mind. §5 exists because of this, not despite
it.

---

## 2. What this explicitly is NOT

- **Not** a vote, ranking, or weighted average — already correctly
  rejected by the original design for good reasons (incomparable
  outputs, different clocks, tunable weights inviting overfitting).
- **Not** R0 Stage 3 combination research (see §1).
- **Not** a retuning of any component's threshold, setup rule, or
  confirmation logic to fit this architecture — each component's exact,
  already-frozen form is reused unmodified, or this stops being the same
  proposal.
- **Not** a claim of evidence from anything computed on the already-
  exposed 2015–2023 or 2026 data (see §5 — this is the section that
  matters most).
- **Not** a selection among ablations after seeing results — the
  three-component composite is the sole primary candidate; ablations are
  attribution diagnostics only (§7).

## 3. Proposed architecture

A sequential state machine, not a vote:

```
V2-C:       Is this multi-session downside event likely to revert?
                                │
                                ▼
Pillar II:  Has an actionable intraday downside dislocation appeared?
                                │
                                ▼
Pillar I:   Has price actually turned and established positive
            short-term momentum?
                                │
                                ▼
                    COMPOSITE LONG ENTRY
```

| Component | R0 role | R1-C role |
|---|---|---|
| V2-C | Standalone trade classifier | Slow contextual permission/veto |
| Pillar II | Immediate reversion entry | Intraday setup detector |
| Pillar I | Standalone trend entry | Post-setup reversal confirmation |
| R1-C composite | Did not exist | Sole trade-producing decision system |

## 4. Proposed decision logic

1. V2-C emits `P(REVERTING) ≥ 0.77278226` at its causally valid 15:00
   decision point, creating a fixed, expiring `REVERSION_PERMITTED`
   state for later sessions.
2. While that state is active, the frozen Pillar II setup must fire.
3. After — never before — the Pillar II timestamp, the frozen Pillar I
   confirmation must fire inside one preregistered confirmation window.
4. Entry occurs at the next causally available one-minute executable
   price.
5. Any condition absent, late, ambiguous, or out of order → ABSTAIN.
   `DETERIORATING`, missing V2-C features, or insufficient data → no
   trade.

The exact confirmation window, exit, and stop must be frozen (D0) before
any combined outcome is calculated — not chosen by trying alternatives.

## 5. The evidentiary firewall — the one non-negotiable rule in this document

This is the correction to the version of this proposal already reviewed
elsewhere. Read literally, not as a suggestion:

> **Everything computed on the already-exposed 2015–2023 (V2-C) or 2026
> (Pillar I/II) data is TRAIN-tier, permanently, and carries ZERO
> evidentiary weight toward any PASS decision — not reduced weight, zero.**
> A FAIL on this data is informative and closes R1-C V1: if a rule
> custom-built with full knowledge of each component's specific failure
> mode still cannot clear the bar on the exact data that hindsight came
> from, that is a real, meaningful negative. **A PASS on this data is not
> informative and must never be reported, described, or treated as if it
> were a validation result** — the composite's roles (V2-C as filter,
> Pillar II as setup, Pillar I as confirmation) were assigned with full
> knowledge of each component's own already-observed weakness, so a
> favorable result on the same data is the expected, near-guaranteed
> outcome of that construction, not evidence of a repeatable edge.
>
> **The only result that may ever be reported as PASS is a genuinely
> fresh, prospective accrual period, collected entirely after the
> composite's every parameter (window, threshold, exit, stop) is frozen
> under D0, using data no component and no version of this proposal has
> ever touched.** This mirrors, and is stricter than, the "known exposure"
> disclosure required of R1 and R1-B — here the exposure is not just
> disclosed, the contaminated data is structurally barred from ever being
> called evidence.

**Enforced structurally, not just by instruction**: the word `PASS` is
removed from the sandbox's vocabulary entirely. The TRAIN-only sandbox
(§6) has exactly two legitimate output statuses, and no others exist:

- **`TRAIN_FEASIBILITY_NO_GO`** — the frozen composite (or every one of
  its ablations, per §7) fails even on the data its own roles were
  fitted to explain. R1-C V1 closes. This is a real, informative,
  binding negative.
- **`ELIGIBLE_FOR_PROSPECTIVE_ACCRUAL`** — the composite is mechanically
  sound, produces a non-trivial event count, and did not immediately and
  obviously fail. **This is not evidence of edge and must never be
  described as one** — it only justifies spending the time to collect a
  genuinely fresh period; it does not shorten or soften what that fresh
  period must show.

Every artifact the sandbox produces — every table, chart, CSV, and
summary JSON — must carry this exact label, verbatim, wherever a result
is presented:

```
EXPOSED R1-C TRAIN DIAGNOSTIC — ZERO CONFIRMATORY WEIGHT — NOT A RESEARCH VERDICT
```

**No sandbox result may change the state machine's rule, confirmation
window, exits, costs, or thresholds.** Any change of any kind — however
small, however well-motivated by something the sandbox showed — creates
a new identity, `R1-C V2`, and requires its own later, still-untouched
accrual period. This is the same "new identity, not a retune" discipline
already applied to every closure in this project (Pillar I/II, V2-C's
own no-rescue design); it is not a special rule invented for R1-C.

## 6. The sandbox — TRAIN-only, feasibility-grade

Name: **R1-C TRAIN-ONLY SCIENTIFIC COMBINATION SANDBOX.** Not R0 Dry Run
A. Produces no `StrategyIntent`, no broker request. `LIVE_TRADING_ENABLED
= False`, no broker API, no `request_entry()` path anywhere in it.

Records, for diagnostic use only (per §5, never as evidence):
- every raw Pillar I signal, every raw Pillar II setup, every V2-C score
  and state, with exact causal timestamps, ordering, and expiry;
- every composite ENTER/ABSTAIN decision and its reason code;
- executable entry/exit prices and full costs;
- capital occupancy and overlapping-position handling;
- resulting event/trade counts (the actual purpose of this sandbox: is
  the plumbing correct, and is the surviving sample size even large
  enough to be worth a prospective test at all — see §8).

## 7. Attribution controls, not candidate selection

Fixed ablations, computed only for diagnosis, never for picking a winner:

1. Pillar II alone.
2. V2-C + Pillar II.
3. Pillar II + Pillar I confirmation.
4. V2-C + Pillar II + Pillar I — the sole primary candidate.

If a pairwise version looks better than the full composite in the
TRAIN-only sandbox, **that changes nothing** — per §5 none of this is
evidence either way. If the frozen three-component primary fails even
this non-evidentiary check, R1-C V1 closes. A pairwise version is never
promoted on a TRAIN-sandbox result.

## 8. Named, real risks (not hypothetical)

- **Sample starvation.** V2-C's own VALIDATION period (15 months)
  produced 1,087 events and only 595 flagged trades from that alone.
  Requiring three independent, ordered conditions to all fire is
  multiplicative, not additive — the surviving trade count could
  plausibly be too small for any meaningful statistical or economic
  claim, on either the TRAIN sandbox or a future prospective period. If
  the sandbox's own event count is not large enough to make a genuinely
  fresh prospective test worthwhile, that is itself grounds to close R1-C
  before ever collecting new data.
- **Confirmation delay destroying the remaining edge.** Even if V2-C's
  and Pillar II's signals carry information, requiring Pillar I's
  confirmation *after* both may consume the price move the composite is
  trying to capture.
- **Cross-pipeline harmonization risk.** Pillar I/II used 1-minute 2026
  data; V2-C's certified pipeline used 15-minute data (2015–2023) on a
  15:00 semantic decision clock. Building a common causal timeline —
  aggregating 1-minute data to reproduce V2-C's exact frozen inputs — is
  real engineering work with real correctness risk (this project has
  already found genuine bugs in comparable cross-artifact plumbing: a
  manifest path-convention mismatch, a CRLF-vs-hash mismatch). The
  aggregation must be verified to reproduce V2-C's original calculations
  **exactly**, byte-for-byte on overlapping inputs, before any composite
  logic is trusted — this is its own gate (G1), not a formality.

## 9. Gate sequence

- **G0a — Concept registration**: owner reviews and accepts this document
  as a new R1-track hypothesis, distinct from Stage 3 (see §1's "how new
  this is").
- **G0b — Known-exposure declaration**: a standalone, explicit record that
  every role assignment in §3 was chosen with full knowledge of each
  component's specific observed failure — named as fact, not buried in
  prose. Required before G1 opens.
- **G1 — Feasibility & harmonization**: build and verify the 1-minute →
  V2-C-input aggregation reproduces V2-C's frozen calculations exactly on
  known overlapping data. No composite logic before this passes.
- **G2 — D0 preregistration**: confirmation window, exit, stop, capital/
  concurrency limits, all named outcomes, and §5's firewall restated as a
  binding clause — frozen before any combined outcome is calculated.
- **G3 — TRAIN-only sandbox** (§6/§7): outputs only
  `TRAIN_FEASIBILITY_NO_GO` or `ELIGIBLE_FOR_PROSPECTIVE_ACCRUAL` — never
  `PASS`, per §5.
- **G4 — Genuinely fresh, prospective VALIDATION**: the only stage that
  can produce a PASS. Opens only if G3 returns
  `ELIGIBLE_FOR_PROSPECTIVE_ACCRUAL`, and only after independent audit of
  G1–G3.
- **G5 — Sealed HOLDOUT**: separately accrued, opened once, only if
  VALIDATION fully passes.
- **G6 — Downstream**: identical to R1/R1-B — a HOLDOUT PASS produces a
  qualified `ResearchIntent`; any eventual production path still requires
  its own new P02/V11 candidate-version process, entirely separate from
  this research document, since P02 remains frozen throughout. Only then
  does Scientific Dry Run A (read-only shadow observer) open.

## 10. Decisions required from the owner

1. **Classification**: confirm this is understood and pursued as a new
   R1-track hypothesis, not Stage 3 combination research.
2. **Firewall**: confirm §5's rule — zero evidentiary weight from
   already-exposed data, ever — as binding, not aspirational.
3. **Choice among R1 / R1-B / R1-C**: pursue one, several as separate
   sequenced tracks, or none.
4. **If R1-C**: approve G1 (harmonization feasibility) only — not D0, not
   sandbox construction, not testing.
5. **Patience**: accept that even a promising TRAIN-sandbox event count
   still requires a genuinely fresh, possibly months-long prospective
   period before anything can be called PASS.

## 11. Why engineering priority, if any, is not a profitability claim

If the owner chooses to sequence R1-C ahead of R1 or R1-B, the honest
reason is that it reuses substantial already-built, already-frozen work
and most directly addresses the system architecture originally intended
— **not** because anything in this document, or any existing result,
shows it is more likely to be profitable than the other two candidates.
Nothing here has been tested prospectively; nothing here has earned any
claim of expected edge.

## 12. Gate progress (updated 2026-08-19)

- **G0a — Concept registration: COMPLETE.** This document, reviewed and
  refined across multiple rounds (including an independent external
  cross-check that corrected two real errors before this gate closed).
- **G0b — Known-exposure declaration: COMPLETE.** §5 above, plus this
  project's interim status record.
- **G1 — Feasibility & harmonization: COMPLETE, certified.**
  `r1c_bridge_1min_to_15min.py` + `test_r1c_bridge_1min_to_15min.py`,
  hash-manifested at
  `R1C_G1_BRIDGE_HASH_MANIFEST_20260819.json`. **A real finding, not
  anticipated by this document's original G1 wording**: no overlapping
  historical data exists between V2-C's certified dataset
  (through 2023-07-31) and Pillar I/II's data (2026 onward) - the
  planned "known overlapping data" comparison was impossible. Certified
  instead via deterministic synthetic fixtures (exact, hand-specified
  1-minute bars aggregating to an exactly-known 15-minute result) plus
  round-trip tests feeding this module's real output directly into
  V2-C's real, frozen, unmodified functions
  (`get_bars_by_date`, `through_1500_hilo`, `build_daily_series`).
  `SIGNAL_SLOT`/`SIGNAL_TIME` cross-checked by direct import from the
  frozen modules, not duplicated as a bare string. 11/11 new tests pass;
  84/84 across the full V2-C/R1-C suite after this addition (which also
  caught and fixed two genuinely stale - not defective - tests in the
  already-frozen V2-C harness suite, refrozen as V4-A2 revision 3).
- **G2 — D0 preregistration: DRAFT PROPOSED, not yet frozen.** See the
  companion document `P01D_RRME_R1C_D0_DRAFT_20260819.md` for the
  concrete numbers proposed for the owner's review.

## 13. Proposed governance verdict

```
R0:                  FROZEN / CLOSED — unaffected
R1 (pre-open):        DRAFT PROPOSAL — AWAITING OWNER DECISION — unaffected
R1-B (V11-exhaustion): DRAFT PROPOSAL — AWAITING OWNER DECISION — unaffected
R1-C (this doc):       G0a/G0b/G1 COMPLETE — G2 (D0) drafted, awaiting freeze
Current authority:    RESEARCH_ONLY / ZERO DECISION AUTHORITY
Next safe action:      review and freeze D0 (see companion draft)
Not authorized:        any result on already-exposed data treated as
                        evidence (structurally barred, §5), sandbox
                        construction, event generation beyond
                        feasibility, VALIDATION, HOLDOUT, any P02/V11
                        code change, or broker mutation
```
