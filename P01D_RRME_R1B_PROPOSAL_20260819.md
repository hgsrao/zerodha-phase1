# P01D RRME R1-B — State-Informed Exhaustion Continuation (Candidate Concept)

**Status: DRAFT PROPOSAL ONLY — NOT FROZEN, NOT AUTHORIZED.**
**Date:** 2026-08-19
**Proposed identity:** `P01D_R1B_STATE_INFORMED_EXHAUSTION_V1`
**Relationship to the other R1 candidate:** presented **alongside**, not in
place of, `P01D_RRME_R1_PROPOSAL_20260819.md` (pre-open price-discovery
continuation). The owner has two independent candidate concepts to choose
between, combine as two separate future tracks, or reject either or both.
Only one — or neither — needs to be picked; nothing here decides that.

This document authorizes no data acquisition, experiment, broker call,
HOLDOUT access, or production integration. `LIVE_TRADING_ENABLED` remains
`False`.

---

## 1. What this explicitly is NOT

Named up front, because this concept was born directly out of an impulse
that R0's own architecture freeze pre-emptively rejected and that the R1
proposal's §10 separately lists as prohibited:

- **Not** a router, ensemble, vote, or weighted combination over Pillar I,
  Pillar II, and V2-C.
- **Not** a retuning of V2-C's classifier, its threshold, or its 0.30%
  economic hurdle.
- **Not** a reuse of V2-C's trained model weights, its consumed VALIDATION
  data, or any R0 HOLDOUT.
- **Not** a resurrection of Pillar I's trend rule or Pillar II's reversion
  rule, unchanged or lightly modified.
- **Not** an inspection of any closed R0 track's event-level, feature-score,
  or trade-level artifacts beyond what is already summarized in this
  document (§4).

---

## 2. What this actually is

R0 established one durable, real, methodology-level finding — independent
of which specific rule was tested: **a classifier built to distinguish
continuation from reversal/deterioration around a statistically extreme
move can carry genuine predictive signal** (V2-C: AUC-ROC 0.6948 on
held-out VALIDATION events it never touched during TRAIN) **even when the
specific rule and cost structure tested around it does not clear a real
economic hurdle.**

That is a finding about a *method*, not a signal to transplant. R1-B
proposes testing whether the same method — a purpose-built causal
classifier around a statistically extreme move — carries a genuine,
freshly-trained, economically viable signal in a **deliberately different
event population**: not V2-C's extreme-*downside-dislocation* events
(`event_z20 < -2.0`), but extreme-*upside-momentum-selection* events —
stocks entering a cross-sectional momentum portfolio (V11-style 12-1
ranking) with an unusually large trailing score.

## 3. Proposed hypothesis

> When a stock is selected into a cross-sectional momentum portfolio with
> an unusually extreme trailing momentum score, its subsequent short-
> horizon return is not uniform — some extreme picks continue, others
> reverse sharply immediately after selection. A causally-available,
> purpose-built classifier, modeled after V2-C's REVERTING/DETERIORATING
> resolution methodology but independently trained on this new event
> population with its own frozen epochs, may distinguish the two well
> enough — and economically enough, after realistic costs — to improve net
> outcomes versus buying every top-N pick unconditionally.

This is a genuinely different mechanism from all three closed R0 tracks:
not continuous-session trend persistence, not intraday dislocation
reversion, not V2-C's own downside-exhaustion domain. It targets the
opposite tail (extreme *up*-moves at *selection* time, monthly frequency)
using the same *kind* of tool that already proved it can find real signal
in one domain.

## 4. Why this is motivated by real, already-disclosed evidence

Not invented from nothing — grounded in this project's own prior, already-
completed work:

- **Fold attribution, 2026-08-16** (`v11_yahoo_fold_attribution.py`, real
  data): the walk-forward study's fold 2 (−22.0% net) traced its loss
  specifically to the most extreme momentum picks — TRENT, ETERNAL,
  BAJAJ-AUTO, each selected on trailing 12-1 scores of 130%–260%+, each
  reversing sharply (−5% to −21%) the very month after being bought. Fold
  5 (near-flat, −0.1%), with far more modest scores (25%–88%), showed no
  such pattern.
- **Fibonacci score-cap experiment, 2026-08-16**: hard-excluding the most
  extreme scorers barely helped fold 2 (−22.00% → −21.04%) — the
  next-highest scorers lost money too. This rules out a simple threshold
  cutoff as the answer; it does **not** rule out that a richer,
  purpose-built classifier (multiple causal features, not one score
  threshold) could distinguish the continuing extreme picks from the
  reversing ones — exactly the kind of question V2-C's methodology was
  built to answer, just never asked of this event population.

## 5. Known-exposure disclosure — mandatory, same standard as R1

R1-B's design is directly informed by knowledge that must be named, not
hidden, exactly as flagged for R1's own eventual D0:

> No R1-B performance data is examined beyond what is disclosed in §4
> above. V11's fold-level walk-forward results (fold 2, fold 5, the
> Fibonacci cap experiment) and V2-C's classifier methodology are prior,
> disclosed exposure — not fresh ignorance. No further inspection or
> mining of R0's or V11's event-level or trade-level artifacts is
> permitted beyond what is already summarized here. V11's existing 5
> walk-forward folds are not to be re-sliced, re-weighted, or further
> mined to tune this rule; any TRAIN/VALIDATION/HOLDOUT split R1-B uses
> must be constructed fresh, under R1-B's own D0, not reused from the
> walk-forward study.

## 6. Structural comparison

| Dimension | R0 (V2-C) | R1 (pre-open auction) | R1-B (this proposal) |
|---|---|---|---|
| Event trigger | Extreme downside dislocation (`event_z20 < -2.0`) | Overnight gap at market open | Extreme upside momentum score at monthly selection |
| Frequency | Intraday, event-driven | Intraday, event-driven | Monthly, selection-driven |
| Universe | Historical NSE 15-min dataset (~70 symbols) | Point-in-time NIFTY 50 | V11's existing cross-sectional universe |
| Data boundary | Consumed (TRAIN/VALIDATION spent) | New, prospective-preferred | New, prospective within V11's own future rebalances |
| Model | L2 logistic regression, 9 frozen features | Deterministic rule, V1 (no ML) | Classifier (methodology proven useful in V2-C), fresh fit |
| Known exposure | None (first use of this method) | Disclosed R0 aggregate outcomes | Disclosed V11 fold 2/5 + Fibonacci-cap results |
| Outcome | Predictive PASS, economic FAIL | Not yet tested | Not yet tested |

## 7. Gate sequence (identical discipline to R1, no shortcuts)

- **G0 — Program authorization**: owner approves this as a genuinely
  distinct mechanism from R0 and from R1; confirms it is understood as
  *methodology reuse*, not signal transplant; sets a data/compute budget.
- **G1 — Feasibility**: confirm which causal features are actually
  available at the real monthly decision timestamp (not just at
  hindsight), and that V11's data pipeline can support a classifier
  fit/train/validate cycle without reusing the walk-forward study's own
  splits.
- **G2 — D0 preregistration**: universe, exact event/trigger definition,
  causal feature list, frozen TRAIN/VALIDATION/HOLDOUT epochs (fresh,
  never overlapping the existing 5-fold walk-forward windows unless
  explicitly and separately justified), predictive gate, economic gate
  (at least as strict as V2-C's 0.30% hurdle — never looser), named
  FAIL/PASS/INCONCLUSIVE outcomes. No performance result before this
  freezes.
- **G3 — TRAIN-only mechanism test**: fit the classifier once; no
  iteration on the rule after seeing results.
- **G4 — One-shot VALIDATION**: opened only after independent audit of G3,
  exactly like V2-C's own harness discipline.
- **G5 — Sealed HOLDOUT**: opened once, only if VALIDATION fully passes.
- **G6 — Downstream**: only a HOLDOUT PASS can produce a qualified
  `ResearchIntent`; this would then need its **own** new P02/V11 candidate
  version (P02 is frozen — "never edit, new defect/change = new candidate
  version" — this proposal does not touch that engine and cannot bypass
  its own versioning discipline), entirely separate from this research
  document.

## 8. Explicitly rejected variants of this proposal

1. Applying a V2-C-trained model (weights, threshold, or features)
   directly to V11's momentum picks without a fresh fit.
2. Choosing the classifier's features or threshold after seeing which
   ones would have avoided fold 2's specific losses.
3. Re-slicing or re-weighting the existing 5-fold walk-forward study to
   manufacture a better-looking result.
4. Treating a TRAIN or VALIDATION pass as permission to modify V11's live
   selection logic or the frozen P02 engine.
5. Any hard score-threshold cutoff presented as if it were the classifier
   result (already shown, in §4, not to work).

## 9. Decisions required from the owner

1. **Choice**: pursue R1 (pre-open auction), R1-B (this proposal), both as
   separate future tracks, or neither.
2. **If R1-B**: approve the feasibility check (G1) only — not D0, not
   testing.
3. **Epoch boundary**: confirm fresh TRAIN/VALIDATION/HOLDOUT splits must
   not overlap the existing 5-fold walk-forward windows.
4. **Economic hurdle**: confirm it must be at least as strict as V2-C's
   0.30%, never a "R1-B gets a pass because it's for V11" exception.
5. **Relationship to P02/V11 production**: confirm explicitly that even a
   full R1-B PASS would still require a separate, new P02/V11 candidate-
   version process before touching anything currently running — this
   document does not shortcut that.

## 10. Proposed governance verdict

```
R0:                 FROZEN / CLOSED — unaffected
R1 (pre-open):       DRAFT PROPOSAL — AWAITING OWNER DECISION — unaffected
R1-B (this doc):     DRAFT PROPOSAL — AWAITING OWNER DECISION
Current authority:   RESEARCH_ONLY / ZERO DECISION AUTHORITY
Next safe action
  if approved:        feasibility check (G1) only
Not authorized:       data reuse beyond §4's disclosed exposure, D0 freeze,
                       event generation, model fitting, VALIDATION,
                       HOLDOUT, any P02/V11 code change, or broker mutation
```
