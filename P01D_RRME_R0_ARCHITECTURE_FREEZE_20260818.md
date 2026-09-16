# P01D RRME — R0 Architecture Freeze

**STATUS: FROZEN.** Design record only — no code written, no data touched,
no runner started. `LIVE_TRADING_ENABLED` unaffected throughout. This
document answers the four open questions from
`P01D_INTRADAY_BRAIN_V1_S0_INTERFACE_SPEC.md`'s black-box proposal and
supersedes the earlier "Dynamic Contextual Gating" pitch, which is
rejected — see §5.

## Context

Three candidate signal sources exist, at three very different evidence
levels:

- **Pillar I (Trend) / Pillar II (Mean-Reversion)** — real, extensive
  evidence from `P02_QUANT_LAB_20260816`: four independent structural fix
  attempts (trailing-stop width, index-regime filter, cross-sectional
  ranking, mid/small-cap universe) never closed the gap to passive, on
  **daily bars**. This is real negative prior evidence, not erased by
  moving to a new timeframe.
- **V2-C (Exhaustion/Deterioration)** — 67/67 engineering tests pass
  (synthetic fixtures only). TRAIN has never been run. Zero real-data
  evidence either way — see [[p01d-v2c-master-record]].
- **rs15 + adaptive exit** (the P01D momentum-selector conviction filter)
  — a genuinely different, already-closed research line: passed one blind
  holdout, failed a second. NO-GO, not reopened by anything here.

## The rejected alternative

An earlier "Dynamic Contextual Gating" proposal — a regime-weighting
network dynamically combining all three sources — is explicitly rejected.
It answers "how do we reduce risk" with "add a fourth unvalidated model on
top of three already-unvalidated/failed ones," which is the same
complexity-without-validation trap R10-A was flagged for avoiding. It also
contained three factual errors (`TradingEngineV14` — doesn't exist, the
real engine is `TradingEngineV34`; L2 tick-data infrastructure that
doesn't exist in this project; a fixed-2%-stop presented as settled when
it's the S0 spec's open question) — evidence it wasn't checked against the
real system.

## 1. Combination rule — FROZEN

**Independent shadow tracks first. No voting, ranking, weighted average,
or mutual gating at this stage.**

```
Pillar I  ─┐
           ├─ (future, only if all validate) → simple frozen domain router
Pillar II ─┘

V2-C ─ conditional information/veto, ONLY inside V2-C's own validated
        event domain, never a global regime oracle
```

Rejected explicitly: 2-of-3 voting (the three outputs — a trend score, a
Z-score, a classifier probability — are not comparable quantities without
calibration; averaging or ranking them is meaningless), dynamic
regime-weighting (a new unvalidated model), and any router that gates one
Pillar off another before each has independently passed its own gate.

If — and only if — all three eventually validate independently, the
first combination rule to research is **domain routing**: Pillar I owns
trend-domain setups, Pillar II owns reversion-domain setups, V2-C may
veto a Pillar-II event only inside its own validated domain, conflict or
ambiguity → ABSTAIN. Confidence: high.

## 2. Pillar I/II timeframe re-validation — FROZEN

**Yes — new hypotheses, not a resurrection of the daily rule, and not a
clean slate either.** New identities: `PILLAR_I_INTRADAY_V1`,
`PILLAR_II_INTRADAY_V1`. Daily failures remain relevant prior evidence,
carried forward, not erased.

**Correction to how "intraday" is implemented**: not a literal
daily-parameters-divided-by-375 conversion. **1-minute execution/
observation resolution, with causally completed 5-minute/15-minute
features where economically appropriate** — the same causal separation
already proven in the P01D research harness (`p01d_r6_same_symbol_execution_timing.py`:
a 5-minute study state is only consumed once its `available_time` has
arrived at a subsequent 1-minute decision point). 1-minute execution does
not mean every indicator is computed on 1-minute bars.

Each Pillar gets the same D0→D1→D2→D3 discipline already proven on rs15:
one predeclared economic hypothesis, frozen causal features/universe/
entry/exit, real-fee hurdle rate, slippage stress, a discovery period, one
independent confirmation, one untouched blind holdout, **no threshold
rescue after opening holdout.** Confidence: high.

## 3. V2-C's gap — FROZEN

**Zero decision authority today.** 67/67 engineering tests establish the
implementation matches its own spec — nothing about real predictive
value. Required chain before any authority is granted:

```
Event definition (already exists: event_z20 < -2.0)
        ↓
Frozen outcome-resolution engine — REVERTING / DETERIORATING / UNRESOLVED
   (deterministic; if bar ordering can't be established, UNRESOLVED or the
    conservative label — never credit the favorable path, same posture
    p01d_r10a already used for ambiguous 1-minute OHLC ordering)
        ↓
Frozen TRAIN / VALIDATION / HOLDOUT epochs (by date, before any label
   is generated — per P01D_V2C_MASTER_RESEARCH_RECORD_CORRECTION_20260817)
        ↓
TRAIN → VALIDATION → model+threshold FREEZE → untouched HOLDOUT
        ↓
Two separate gates, both required:
  PREDICTIVE gate — classifies unseen events better than baseline?
  ECONOMIC gate  — does using it improve net expectancy after real costs?
```

Until both gates pass: `V2-C authority = OBSERVE_ONLY`. It may log event,
features, predicted class, confidence, eventual outcome — it cannot block
or approve a trade, for Pillar I or Pillar II. Confidence: very high.

## 4. Dry-run scope — FROZEN

**Two sequential, separately-named gates answering different questions —
never combined into one experiment.**

**Dry Run A — Scientific Shadow** (does the strategy behave sensibly on
unseen live data?):
```
Market data → Pillar I / Pillar II / V2-C → independent decisions
            → Evidence Ledger → hypothetical ResearchIntent → STOP
broker_network=False  order_api=False  request_entry=False
production_mutation=False  LIVE_TRADING_ENABLED=False
```
Same posture already proven by the existing ORB shadow observer.

**Dry Run B — Production-Path Shadow** (can a *qualified* strategy safely
travel through the real, certified execution path?), only entered after a
strategy survives A:
```
StrategyIntent → bridge → request_entry() → real RunnerEntryAuthorizer
              → real engine state machinery → SHADOW DECLINE
LIVE_TRADING_ENABLED=False, no real order
```
This exercises real risk sizing, cooldowns, same-symbol lock, daily
counters, halt behavior — proves integration, not alpha. Doing only A
leaves integration unproven; doing only B contaminates alpha research
with execution-state behavior before any signal has earned the right to
be there.

## Evidence Ledger — carried forward from the earlier proposal, kept

Every model opinion is recorded, including rejected/abstained decisions,
not just trades: timestamp, symbol, each candidate's eligibility/signal/
score/reason codes/feature snapshot/model version, router decision and
reason, intent emitted or not, data freshness/causality-cutoff, and —
once outcomes resolve — the counterfactual result each un-selected
candidate would have had. This is what makes it possible to later ask
"did V2-C actually add value" without re-running history with new
assumptions.

## ResearchIntent vs StrategyIntent

Research generates a richer `ResearchIntent` (native signal assumptions,
own entry/stop/target, explicit "do not send to broker" marker — the
existing `p01d_intraday_lab_R1.py` pattern). Only a strategy that has
already qualified gets translated into the frozen `StrategyIntent`
contract. The production contract does not contaminate research, and the
stop_price advisory-vs-authoritative question stays open exactly as the
S0 spec left it — not resolved here, not resolved by default.

## Priority order, frozen

```
1. Pillar I / Pillar II independent intraday qualification (D0-D3 each)
2. V2-C outcome-resolver + TRAIN/VALIDATION/HOLDOUT, in parallel
3. Read-only shadow tournament (Dry Run A) for whatever passed #1/#2
4. Only then: domain-router research (§1's "future" combination rule)
5. request_entry() shadow integration (Dry Run B) for whatever passes #4
```

A hard stop applies after every stage. A failed Pillar is not rescued
because architecture time was already spent on it. If none of the three
pass, the answer is to stop — not to invent a fourth signal or optimize a
combination of three failures into a success.

## What this document does not do

Does not start Pillar I's D0 specification (universe, causal feature
clock, entry/exit definition, cost model, discovery/confirmation windows)
— that's real, separate work, tracked as the next actual step whenever
picked up. Does not touch P02, V11 bridge, or any frozen file. Does not
change `LIVE_TRADING_ENABLED`.
