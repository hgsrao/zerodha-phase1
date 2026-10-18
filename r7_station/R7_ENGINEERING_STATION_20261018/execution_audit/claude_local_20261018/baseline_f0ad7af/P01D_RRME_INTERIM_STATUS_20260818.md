# P01D RRME — Interim Status

**STATUS: RECORD OF WHERE THE PROGRAM STANDS.** Not a design document —
summarizes outcomes already sealed elsewhere. See each linked manifest
for the authoritative detail; this file is a pointer, not a restatement.

**SUPERSEDED as of 2026-08-19 — see "V2-C — final disposition" and
"STAGE 1 FINAL STATUS" further below for the current, authoritative
summary. The block immediately below is preserved unedited as the
contemporaneous 2026-08-18 record; do not read it as current status.**

```
PILLAR I  (trend continuation)      CLOSED — FAIL
PILLAR II (dislocation reversion)   CLOSED — FAIL
ROUTER / SHADOW TOURNAMENT          BLOCKED — no validated candidate to route
request_entry() INTEGRATION         BLOCKED — same reason
V2-C (exhaustion/deterioration)     OPEN — sole active lane, OBSERVE_ONLY,
                                      15-minute acquisition COMPLETE (69
                                      intervals + NIFTY 50), DATASET
                                      CERTIFICATION COMPLETE (0 FAIL, 0
                                      NOT_CERTIFIED, on the cleaned
                                      derivative - see
                                      V2C_DATASET_CERTIFICATION_RECORD_20260818.md).
                                      EPOCH FREEZE COMPLETE (TRAIN
                                      2015-02-02..2020-12-31, VALIDATION
                                      2021-01-01..2022-03-31, HOLDOUT
                                      2022-04-01..2023-07-31 - see
                                      V2C_EPOCH_FREEZE_20260818.md,
                                      hash-frozen). TRAIN-ONLY LABELS
                                      GENERATED, corrected after an
                                      independent audit caught a real
                                      boundary defect (5467 events: 3833
                                      REVERTING, 1127 DETERIORATING,
                                      507 UNRESOLVED; all integrity
                                      checks pass). TRAIN CLASSIFIER
                                      PREREGISTRATION FROZEN (threshold
                                      0.77278226, predictive gate
                                      AUC-ROC>=0.55). ECONOMIC-GATE
                                      ADDENDUM A2 — PASS/FROZEN (final,
                                      independently re-audited twice) -
                                      superseded a first version (6
                                      defects), then A1 itself
                                      (CONDITIONAL FAIL/REVISE: 3 spec
                                      gaps), then a provenance erratum in
                                      A2 itself (NSE tier rationale + DP
                                      figure sourcing) - see
                                      P01D_V2C_ECONOMIC_GATE_ADDENDUM_A2_20260818.md;
                                      all prior versions preserved, not
                                      deleted. VALIDATION-era fee gap now
                                      CLOSED (NSE/FA/46730 + Zerodha's own
                                      2023-04-03 notice, both independently
                                      verified: 0.00345% from 2021-01-01,
                                      see ENGINE/v2c_validation_era_costs.py).
                                      Economic prerequisite for VALIDATION
                                      now CLEARED. TRAIN FEATURE
                                      CONSTRUCTION PASS/FROZEN (independent
                                      re-audit confirmed, 1 non-blocking
                                      hardening item fixed - see
                                      P01D_V2C_TRAIN_FEATURE_INTEGRITY_SUMMARY_20260818.md).
                                      TRAIN CLASSIFIER FIT (single frozen
                                      L2 logistic regression, 4960 resolved
                                      events, threshold 0.77278226,
                                      TRAIN-only diagnostic AUC 0.7234 -
                                      NOT a gate). VALIDATION ONE-SHOT
                                      EXECUTION HARNESS v2 BUILT +
                                      HASH-FROZEN, submitted for re-audit
                                      - v1 FAILED re-audit (6 audit/output
                                      -layer defects: scores not
                                      persisted, non-atomic write,
                                      unrecorded failures, unstable
                                      sigmoid, misleading counts, silent
                                      exclusions), all 6 fixed in v2
                                      (40/40 tests passing, up from
                                      32/32; v1 preserved, not deleted).
                                      Code-level authorization gate
                                      refuses to run against real data
                                      without explicit sign-off - see
                                      v2c_validation_harness.py. HOLD:
                                      VALIDATION run NOT yet authorized,
                                      token never entered. VALIDATION/
                                      HOLDOUT untouched; predictive/economic
                                      gates unevaluated.
P02 execution shell / safety        UNCHANGED, CERTIFIED — unaffected by
                                      either Pillar's result
```

**CURRENT STATUS, 2026-08-19 (supersedes the block above):**

```
PILLAR I  (trend continuation)      CLOSED — FAIL (D1 discovery, 2026-08-18)
PILLAR II (dislocation reversion)   CLOSED — FAIL (D1 discovery, 2026-08-18)
V2-C (exhaustion/deterioration)     CLOSED — FAIL / OBSERVE_ONLY (VALIDATION
                                      economic gate, Attempt 2, 2026-08-19).
                                      Predictive gate PASS (AUC-ROC 0.6948),
                                      economic gate FAIL (E3: mean return
                                      below the 0.30% hurdle under both
                                      DP-excluded and DP-stressed cost
                                      assumptions, despite both win-rate
                                      checks passing). HOLDOUT SEALED, never
                                      opened, unreachable from this result.
                                      See P01D_V2C_CLOSURE_MANIFEST_20260819.txt
                                      for the full, hash-manifested closure
                                      record.
ROUTER / SHADOW TOURNAMENT          BLOCKED — no validated candidate exists
                                      from any of the three Stage-1 tracks
request_entry() INTEGRATION         BLOCKED — same reason
STAGE 1                             ALL THREE TRACKS CLOSED. Research cycle
                                      PAUSED pending a new, independently
                                      preregistered hypothesis.
P02 execution shell / safety        UNCHANGED, CERTIFIED — unaffected by
                                      any of the three closures
```

## Formal governance verdict — TRAIN feature integrity (2026-08-18)

Recorded verbatim as the owner's formal status:

```
V2-C TRAIN FEATURE INTEGRITY:        PASS / FROZEN
TRAIN classifier fitting:            AUTHORIZED (now complete - item 19)
VALIDATION:                          SEALED
HOLDOUT:                             SEALED
Economic-gate A1:                    still a separate blocker to opening VALIDATION
```

Independent re-audit confirmed all items in item 18's integrity report
plus additional checks I had not run myself: all 70 certified
stock/NIFTY files verified against the dataset hash manifest (NIFTY
included). **One non-blocking hardening item was raised**:
`v2c_train_feature_construction.py` verified the label manifest and
calendar but not the underlying certified dataset itself — fixed
immediately (`verify_dataset_manifest_hash()` added, fail-closed,
called from `verify_inputs()`), and the previously-unprotected
`V2C_15MIN_DATASET_HASH_MANIFEST_20260818.json` was hash-frozen with
its own sidecar (SHA256
`a58e8526c6dfe9d9b199941ece18ef981bc180bf45474ce9cecb2f6571cca290`).
Rerunning the hardened generator reproduced byte-identical feature CSV
and integrity-report output (same SHA256s as item 18) — confirming the
hardening changed nothing about the frozen feature values, only added
a check. The features hash manifest was refrozen to record the updated
generator hash (SHA256
`9d8543481e8a0c78e12a996f66ef0a2f83ab04d7aa900cf7039b267fa5ef87e2`).

## Formal governance verdict — TRAIN classifier fit (2026-08-18)

Recorded verbatim as the owner's formal status:

```
V2-C TRAIN CLASSIFIER FIT:           PASS / FROZEN
VALIDATION:                          SEALED
HOLDOUT:                             SEALED
Next step:                           submit economic-gate A1 for independent
                                      re-audit BEFORE VALIDATION-era fee sourcing
```

Independent re-audit reproduced the fit from the frozen artifacts
directly (not merely re-reading the report): scaler means/scales
reproduced to floating-point precision; serialized model scores
recomputed the reported TRAIN AUC to 15 significant figures
(`0.7233893954591785` recomputed vs `0.7233893954591784` reported —
a last-digit floating-point rounding difference, not a discrepancy);
class counts, excluded-UNRESOLVED count, frozen feature list,
threshold, and hyperparameters all independently confirmed against the
preregistration; model/report/script/manifest/sidecar hashes all
verified; the item-18 hardening's dataset-manifest sidecar also
reverified. TRAIN AUC reconfirmed descriptive-only, not a gate. No
evidence of VALIDATION or HOLDOUT access.

**Per explicit instruction, VALIDATION-era fee sourcing is now held**
in favor of submitting economic-gate A1 for independent re-audit first
— see the submission summary below. Only once A1 itself returns a PASS
verdict does the remaining 2021-2022 fee-sourcing work resume, against
that approved design (not before, so sourcing effort is never spent
against a design that re-audit might still change).

## Economic-gate A1 — submitted for independent re-audit (2026-08-18)

Nothing changed in A1 since it was frozen — re-verified intact before
submission: `P01D_V2C_ECONOMIC_GATE_ADDENDUM_A1_20260818.md` SHA256
`506840b20540c904d4fd633bcfb5e5f5cf5036933dc37ab7284ed9d31cb8e70f`
(matches sidecar), `ENGINE/v2c_train_era_costs.py` SHA256
`fd165b1051fbf2f06170097aa92439b82a9b9d1384cb4dfbad6efb7bd8607fdc`,
`TESTS/test_v2c_train_era_costs.py` SHA256
`658b0a15fb7d937143114683ab9497c59ccd1df9d97c7271a85f24191ccf7031`
(19/19 passing, reran clean). Pointer to where each of the six named
fixes lives, for re-audit convenience:

1. **Flagged-event population** (excluding UNRESOLVED used future
   information) — A1 §1: every classifier-flagged event with an
   observable `t0+1` entry bar is priced, UNRESOLVED included; only
   structural `NO_FORWARD_DATA` abstains excluded.
2. **UNRESOLVED liquidation rule** — A1 §2: exit at the close of the
   final eligible bar within
   `[observation_window_start, observation_window_effective_end]`,
   frozen here, does not alter the D0/D0-A1 label itself.
3. **Gap execution** (D0-exact exit pricing) — A1 §2: exit price reads
   the frozen audit CSV's `resolution_price` verbatim (gap-open for
   gap-through cases, resolution-bar close for ordinary recovery, the
   stop level for an ordinary stop touch) — never re-derived, never a
   blanket close/stop-level substitution.
4. **Intraday/delivery classification** — A1 §3: derived from
   entry/exit dates (same day = INTRADAY), feeding differentiated
   STT/stamp-duty treatment in §4's table.
5. **Brokerage history** — A1 §4 and the "Why A1 exists" defect #4:
   `ENGINE/v2c_train_era_costs.py`'s `delivery_brokerage()` — Rs 0 from
   2015-12-01, lower of 0.1%/Rs 20 before, sourced to two independently
   fetched Zerodha Z-Connect announcements quoted verbatim.
6. **DP charges** — A1 §4 and defect #5: excluded from every priced
   `total`, exposed as `dp_charge_upper_bound` (Rs 15.34), with Gate
   E3's profitability check required to survive that bound being
   subtracted (§5).

Also available for re-audit: the real-TRAIN-data diagnostic
(`v2c_economic_trade_pricing.py`, 5465/5467 events priced, 44.4%
INTRADAY finding, independently reproduced twice) referenced in A1 §1,
and §6's disclosed, still-open VALIDATION-era fee gap (unaffected by
this submission — deliberately held per the instruction above).

## Formal governance verdict — A1 re-audit (2026-08-18)

```
Economic-gate A1:                    CONDITIONAL FAIL / REVISE BEFORE VALIDATION
```

The six original defects were confirmed substantially corrected (5465
priced events and trade-type counts reproduced; all hashes matched).
Three specification gaps were found: (1) the Rs 100,000 sizing rule
was dropped when A1 rewrote the original addendum — no stated
`floor`/`round` rule, no stated return denominator; (2) Rs 15.34 was
merely the largest *current-era* figure retrieved, not a proven bound
for any period actually priced, and the "per leg" aggregation language
was imprecise (DP charges are levied per scrip per day, not generically
per leg); (3) Gate E3's "checked twice" heading did not explicitly
state that the DP-stressed win-rate check is its own required
condition, only the mean-return one. A non-blocking hardening request
was also raised: `v2c_economic_trade_pricing.py` loaded certified price
bars without verifying them against the frozen dataset manifest (the
re-auditor independently confirmed the existing inputs matched, so this
did not invalidate the diagnostic already reported).

Instruction: revise and refreeze as a corrected version, hold
VALIDATION-era fee sourcing until the revision is itself resubmitted,
then proceed with that sourcing against the approved design.

## Economic-gate A2 — corrections applied, VALIDATION-era gap closed,
## submitted for a short final re-audit (2026-08-18)

`P01D_V2C_ECONOMIC_GATE_ADDENDUM_A2_20260818.md`, SHA256
`786d63d96bc9c934b9b355cca7491295dff593f0687c414aabbe69afec54939e`
(reproducibility verified). A1 preserved unmodified at
`SUPERSEDED_ECONOMIC_GATE_ADDENDUM_A1_20260818/`. A2 is a targeted
delta document (same pattern as D0-A1 was to D0) — it does not restate
A1's six original-defect fixes, which remain authoritative.

**Gap 1 (sizing) closed**: `quantity = floor(100000 / entry_price)`,
chosen over `round()` because deployed capital must never exceed the
stated notional. Degenerate case named: `floor(...) < 1` excludes the
event from Gate E1's sample count with reason code
`UNTRADEABLE_AT_NOTIONAL`, never silently a zero-return trade. Return
denominator defined as actual deployed capital
(`entry_value = quantity * entry_price`), not the round Rs 100,000
target.

**Gap 2 (DP bound) closed**: re-sourced to Rs 15.93 (Rs 13.5 base +
18% GST), the rate specifically applicable in **2021-2022** — the
VALIDATION era Gate E3 actually evaluates — replacing the unproven
current-era Rs 15.34 figure. Aggregation rule stated explicitly: once
per scrip per day when sold, which in this model's single-fill
execution (one BUY, one SELL, no partial fills) is identical to "once
per delivery sell leg." `ENGINE/v2c_train_era_costs.py`
(`DP_CHARGE_UPPER_BOUND`) updated accordingly, SHA256
`044628c6ac70fb14c32decb8ba20e8dd375cfd9327d3cf82fb67c8913e9e0119`;
`TESTS/test_v2c_train_era_costs.py` updated to match, SHA256
`638027601f5d02d2fba2fe8852c550364fa1ad26bbe9296642813086dfdc1c25`
(19/19 passing).

**Gap 3 (E3 ambiguity) closed**: A2 §3 states all four required checks
individually — mean return and win rate, each evaluated once
DP-excluded and once DP-stressed — any single failure is a genuine
FAIL, not an average.

**Hardening applied**: `v2c_economic_trade_pricing.py` now calls
`verify_dataset_manifest_hash()` before loading any price bar, SHA256
`9bcf5422019debfcfe1a2db1f2530d002a599d525e21f8af42e81a5b613960cd`.
Rerunning reproduced byte-identical diagnostic output (same 5465/2228/
1605/195/932/2/503 breakdown) — the hardening changed nothing about
the already-reported result.

**VALIDATION-era fee sourcing — resumed and closed**, per instruction,
kept strictly to externally-sourced NSE fee facts; no VALIDATION event,
feature, prediction, or price was read. Sourced directly from NSE
circular NSE/FA/46730 (2020-12-18, PDF fetched and read directly): cash
market exchange transaction charge revised **Rs 3.25/lakh → Rs
3.45/lakh each side, effective 2021-01-01** (0.00345%, a 6.15% hike,
corroborating the previously-known "~6%" description). Read and
resolved a load-bearing caveat in the same circular: the revised rate
applies only to NIFTY 50/NIFTY Next 50 constituents (other stocks keep
an unchanged concessional rate) — since V2-C's universe is built
exclusively from point-in-time NIFTY 50 membership, every VALIDATION
event is non-concessional by construction, so the revised rate
genuinely applies, reasoned explicitly rather than assumed. Confirmed
the hike was not rolled back until 2023-04-01 (outside VALIDATION),
so no further within-window tiering is needed. New module
`ENGINE/v2c_validation_era_costs.py` (SHA256
`024061b534df5b59c565c5e5ef6414dfa96f4b49388fb44b34cfb55136509b9f`),
tested in `TESTS/test_v2c_validation_era_costs.py` (SHA256
`5af1cd12c3e320ea715736f394a78ebc3a65b7e9e70e8c623a803600a55f7426`,
9/9 passing) — fail-closed to 2021-01-01..2022-03-31, same discipline
as the TRAIN module. All other VALIDATION-era rates (brokerage, STT,
SEBI fee, stamp duty, GST, the DP bound) carry forward from TRAIN
unchanged, each flagged "no further change found" rather than silently
assumed solid; stamp duty is in fact fully certain for VALIDATION
(entirely post-2020-07-01 reform, no Karnataka approximation needed).
**This closes A1 §6 in full** — Gate E3 can now, in principle, price a
VALIDATION trade once VALIDATION is opened; it remains sealed
regardless.

Full submission hash-frozen as one set:
`V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json` (SHA256
`3bd8dd448cc20c8ae6de37d0e970ac93c3fb452e314d5cfe3ae0448f6313b1ca`),
covering A2, both cost modules, both test files, and the hardened
diagnostic script. All 28 relevant tests pass
(`TESTS/test_v2c_train_era_costs.py` + `TESTS/test_v2c_validation_era_costs.py`).
Submitted for a short final re-audit, per instruction.

## Formal governance verdict — A2 re-audit (2026-08-18)

```
Economic-gate A2:                    CONDITIONAL PASS / PROVENANCE ERRATUM REQUIRED
```

Sizing, deployed-capital denominator, `UNTRADEABLE_AT_NOTIONAL`
handling, DP aggregation, the four E3 checks, both modules, both test
files, the diagnostic hardening, and all submission hashes confirmed
internally correct. One rationale required correction before
VALIDATION opens: (1) NSE's monthly-volume slabs apply to the *trading
member's* aggregate turnover, not to V2-C's simulated Rs 100,000
position — the claim that V2-C necessarily occupies the lowest slab
had to be removed; (2) the chosen 0.00345% client rate is nevertheless
correct, supported instead by Zerodha's own 2023-04-03 client-facing
notice, which names 0.00345% as its prior equity intraday/delivery
charge before the 2023-04-01 reduction; (3) A2's Rs 15.34/Rs 15.93 DP
figures needed an exact, reproducible archived source, not a stated
result without a citable document.

## Economic-gate A2 — corrected, PASS / FROZEN (2026-08-18)

Provenance erratum corrected and A2 refrozen. **Verified independently
in this session, not merely trusted**: both newly-cited sources were
fetched and confirmed to say exactly what A2 now claims —

- Zerodha's 2023-04-03 bulletin
  (`marketintel/bulletin/347967/revision-in-transaction-charges-and-stt-from-1st-april-2023`)
  confirms 0.00345% → 0.00325% for both intraday and delivery, effective
  2023-04-01, exactly as A2 §4 states.
- Zerodha's BTST-settlement support article
  (`support.zerodha.com/.../change-in-btst-trade-settlement`) confirms
  the settlement-process change took effect 2021-06-03 and states the
  DP charge as "Rs 13 + 18% GST" (= Rs 15.34), exactly as A2 §2 states.

Changes applied: (1) the NSE-tier rationale in
`ENGINE/v2c_validation_era_costs.py` and A2 §4 now correctly attributes
the circular's volume slabs to trading-member aggregate turnover, and
grounds the 0.00345% client rate in Zerodha's own notice instead;
(2) `DP_CHARGE_UPPER_BOUND` raised from an unproven Rs 15.34/Rs 15.93 to
a **deliberately adverse Rs 20.00 stress amount**, explicitly not
presented as exact historical accounting, with the real Rs 15.34
BTST-era figure retained as the documented (lower) evidence it stress-
exceeds; (3) both cost modules and both test files updated to match
(`ENGINE/v2c_train_era_costs.py`, `ENGINE/v2c_validation_era_costs.py`,
`TESTS/test_v2c_train_era_costs.py`, `TESTS/test_v2c_validation_era_costs.py`).
No model or cost arithmetic changed beyond the DP figure itself — all
28 tests re-verified passing; the hash manifest, both file hashes and
sizes, and both sidecars re-verified with zero mismatches against the
corrected files.

```
Economic-gate A2:                    PASS / FROZEN
```

**The economic prerequisite for VALIDATION is now cleared.** VALIDATION
was not accessed during any part of this correction; HOLDOUT remains
sealed. This is the last item that was blocking VALIDATION from
opening — TRAIN labels, TRAIN feature construction, the TRAIN
classifier fit, and now the economic-gate addendum have all
independently passed re-audit. Opening VALIDATION itself remains a
separate, deliberate act this record does not take unilaterally.

## Formal governance verdict — economic gate (2026-08-18)

Recorded verbatim as the owner's formal status, on review of the six
defects listed in item 16 and their remediation in item 17:

```
Economic gate:                       FAIL / SUPERSEDE WITH A1
VALIDATION:                          SEALED
HOLDOUT:                             SEALED
```

**A1 and the corrected cost model are hash-frozen (item 17) but that is
necessary, not sufficient.** Per the same standing, "**The replacement
A1 and cost model must be frozen and independently re-audited before
any VALIDATION prices, labels, predictions, or economics are
examined**" — an independent re-audit of A1 has not yet happened (the
same two-step pattern already used for the TRAIN labels and TRAIN
features: freeze first, then a separate independent audit pass before
anything downstream may rely on it). Until that re-audit returns a
verdict, VALIDATION stays SEALED on this ground as well as on the
already-disclosed unsourced VALIDATION-era fee-rate gap (item 17 §6) —
either alone would block it; both currently do.

TRAIN feature construction (item 18) is now built and self-checked
across every requested integrity dimension, but per explicit
instruction this is a separate, sequential step from classifier
fitting: **fitting becomes authorized only once an independent
re-audit of the feature-integrity report returns a PASS verdict** —
work now pauses at exactly that point, mirroring the same freeze-then-
audit discipline applied to A1 and to the TRAIN labels before it.

## Formal governance verdict — VALIDATION opening (2026-08-18)

Recorded verbatim as the owner's formal status, choosing to hold
rather than open VALIDATION now that its research-rules prerequisites
(TRAIN labels, features, fit, and the economic-gate addendum) had all
cleared re-audit:

```
VALIDATION opening:                  HOLD — Option 2, do not open yet
Required first:                      one-shot execution harness, built +
                                      tested on synthetic fixtures + hash-
                                      frozen, BEFORE the real run
HOLDOUT:                             SEALED regardless
```

Rationale stated explicitly: avoids discovering an implementation
defect only after sealed data has already been exposed - the harness
must be proven correct against fixtures no one needs VALIDATION access
to construct, before it is ever pointed at the real thing. Item 21
satisfies this: the harness is built, 32/32 synthetic and fail-closed
tests pass, and it is hash-frozen. **VALIDATION has still never been
opened** — the harness's own code-level authorization gate (not merely
this record) independently enforces that, refusing to run against the
real project root without an explicit `authorized=True` /
`--confirm=AUTHORIZE_IRREVERSIBLE_VALIDATION_RUN_20260818`. The next
step is explicit authorization of the real, one-shot, irreversible run
- not taken here, not implied by anything in this record.

## Formal governance verdict — harness re-audit (2026-08-18)

```
VALIDATION HARNESS:                  FAIL / DO NOT AUTHORIZE YET (v1)
```

Six blocking defects found in the first frozen harness (v1, preserved
unmodified at `SUPERSEDED_VALIDATION_HARNESS_V1_20260818/`), all in the
audit/output layer, not the core calculations: (1) feature scores and
per-event probabilities were not persisted at all - a predictive-gate
FAIL would have left almost no scoring evidence behind; (2) the bundle
write was not atomic - a failure partway through could leave a partial
final directory blocking reruns; (3) a failure after VALIDATION reading
began was unrecorded - risking a silent rerun or a failure mistaken for
a research result; (4) the naive `1/(1+exp(-logit))` sigmoid could
overflow on extreme logits; (5) `n_priced_trades = len(trades)`
conflated priced, unflagged, and abstained rows into one misleading
count; (6) frozen-window/eligibility exclusions were silently dropped
with no record.

**All six corrected in v2**:
1. New `EventScore`/`score_all_events()` - every event, feature-complete
   or abstained, gets one persisted row (key, abstention status, all
   nine features, `p_reverting`, label, flagged) in
   `V2C_VALIDATION_FEATURE_SCORES.csv`, written unconditionally
   regardless of any gate's verdict. `evaluate_predictive_gate()` and
   `price_all_flagged_events()` both now consume these same scores -
   never recompute a probability twice.
2. `_stage_and_finalize_bundle()` - every bundle (success or failure)
   is built in a sibling staging directory, then atomically renamed
   into place. A partial write leaves the staging directory (not the
   final directory) as forensic evidence, and blocks any further
   attempt at the same output location until a human investigates.
3. `execute_one_shot_run()` - writes an immutable `RUN_STARTED` marker
   (JSON, timestamped) before any VALIDATION file is opened; any
   exception anywhere in the pipeline is caught and finalized as a
   hash-manifested `..._FAILED_INFRASTRUCTURE` bundle, explicitly
   labeled `FAILED_INFRASTRUCTURE` (never a PASS/FAIL/UNDECIDABLE
   research verdict), with the exception type/message/traceback
   recorded; both the marker and the failed-bundle directory block a
   second attempt at the same location.
4. `_stable_sigmoid()` - branches on the logit's sign so the exponential
   argument is always <= 0 (can only underflow to 0.0, never overflow) -
   confirmed the naive formula genuinely raises `OverflowError` at
   logit=-1000 in this environment, the fix is not a redundant
   precaution.
5. `bundle["counts"]` - `n_events`, `n_excluded_frozen_window`,
   `n_scored`, `n_abstained`, `n_flagged`, `n_priced`,
   `n_untradeable_at_notional`, `n_not_flagged`, each independently
   countable and none conflated.
6. New `ExcludedCandidate` - every frozen-window/eligibility exclusion
   is recorded with its reason in
   `V2C_VALIDATION_EXCLUDED_CANDIDATES.csv`, even though none are
   expected during VALIDATION.

**8 new required tests added** (partial-write failure, predictive-gate
failure with scoring evidence intact, zero-event output, extreme
logits x2, exact feature-score CSV round-trip, immutable failure
finalization, a success-path positive control for the new
`RUN_STARTED`/staging machinery) - **40/40 tests passing** (19
synthetic + 21 fail-closed, up from 32), plus the pre-existing 28
cost-model tests unaffected (68 total). Refrozen:
`V2C_VALIDATION_HARNESS_HASH_MANIFEST_20260818.json` (SHA256
`5f33c8e3b468a21feacb74569626d6286e0730000bcf9376a1af3641c92f2de6`,
covering `v2c_validation_harness.py` SHA256
`f5af922bc75c77654b6906faf2cc8839dcb9ee2bd969141f5207ab2cbc1b06aa`
and both test files). **VALIDATION was not accessed at any point
during this correction** - confirmed no `V2C_VALIDATION_RUN_20260818`,
`RUN_STARTED`, or `FAILED_INFRASTRUCTURE` artifact exists anywhere in
the real project tree. **The authorization token was not entered.**
Resubmitted for re-audit; VALIDATION and HOLDOUT remain sealed.

## Formal governance verdict — v2 re-audit (2026-08-18)

```
V2-C VALIDATION HARNESS V2:          FAIL / SUPERSEDE WITH V3
VALIDATION:                          SEALED
HOLDOUT:                             SEALED
```

The six original defects were confirmed substantially corrected; all
frozen hashes/sizes matched the manifest exactly. Two **P1 governance
defects** remained, both in the exposure-control chain itself, plus one
smaller accounting correction:

1. **One-shot controls could be bypassed** (`v2c_validation_harness.py:817-829`,
   v2 line numbers): `run_validation_harness()` carried its own
   independent `authorized: bool` check on the real project root —
   completely separate from `execute_one_shot_run()`'s marker/staging/
   failure-bundling machinery. Calling the lower-level function
   directly with `authorized=True` unlocked real VALIDATION data while
   skipping every protection the higher-level entry point exists to
   provide — two authorization surfaces that didn't compose.
2. **`RUN_STARTED` was not immutable** (`:968-981`): the marker was
   written via `if marker_path.exists(): raise ... else: write_text(...)`
   — a check-then-write with a real TOCTOU race. Two racing/repeated
   invocations could both pass the existence check before either had
   written, and the second `write_text()` would silently overwrite the
   first marker's timestamp, destroying the exact evidence the marker
   exists to preserve.
3. **`n_not_flagged` accounting**: computed from `trades`, which is
   deliberately empty whenever the predictive gate fails — so a failed
   gate always reported zero unflagged events even when scored,
   non-flagged events genuinely existed.

Verified independently: all hashes/sizes matched; feature-score
persistence, stable sigmoid, frozen-window exclusions, staged bundle
finalization, and failure bundles were all present and correct; no
real VALIDATION or HOLDOUT data was accessed. (The re-auditor could not
locally rerun the test suite — no `pytest` in that runtime — and
explicitly noted this doesn't affect the code findings.)

## Economic-gate-pattern harness — v3, all three defects corrected (2026-08-18)

**All three fixed, verified with new tests, 47/47 passing (up from 40).**

1. **Exposure-chain bypass closed**: `run_validation_harness()` no
   longer accepts an `authorized` parameter at all. Its only gate is
   `_require_one_shot_precondition()` — a RUN_STARTED marker must
   already exist at the expected path before real data (cwd ==
   `REAL_PROJECT_ROOT`) can be read. This ties the gate to the SAME
   filesystem artifact the audit trail depends on: whatever path a
   caller takes to reach real data, the marker (proof
   `execute_one_shot_run()`'s pre-flight step already ran) must already
   exist — the two authorization surfaces now collapse into one.
   `protected_root`/`marker_path` parameters exist solely so tests can
   exercise both branches of the guard without ever operating from the
   real project root.
2. **Marker atomicity fixed**: `_write_run_started_marker()` now opens
   the file with `mode="x"` (POSIX `O_CREAT|O_EXCL` / Windows
   `CREATE_NEW`), atomic at the OS level — two racing creators can never
   both succeed, and a loser can never silently overwrite a winner's
   timestamp. Applied the same fix to `_stage_and_finalize_bundle()`'s
   staging-directory creation for consistency (same class of bug, same
   discipline).
3. **`n_not_flagged` now computed directly from `scores`**
   (`not s.abstain and not s.flagged`), exactly like `n_flagged` —
   correct regardless of whether `trades` is empty.

**New regression tests, exactly as requested**: direct-call refusal
(`test_refuses_to_run_against_the_real_project_root_without_authorization`,
now marker-gated; `test_run_validation_harness_no_longer_accepts_an_authorized_bypass_flag`,
confirms the bypass parameter is gone via signature inspection;
`test_direct_call_with_real_root_spoofed_via_protected_root_still_requires_marker`,
exercises both branches of the new guard safely); marker tampering/
concurrent creation (`test_marker_second_creation_attempt_never_overwrites_the_first`,
`test_marker_creation_is_exclusive_open_not_check_then_write`,
`test_tampering_with_marker_after_creation_is_at_least_detectable_via_content_mismatch`);
predictive-gate-failure counts (`test_n_not_flagged_is_correct_when_predictive_gate_fails_and_trades_is_empty`,
a scenario engineered to have a nonzero expected count, proving the old
`trades`-based computation was genuinely wrong;
`test_predictive_gate_failure_reports_correct_n_not_flagged`, against
the real fixture).

**Disclosed transparently, not hidden**: unlike v1→v2 (where v1's exact
source files were copied to `SUPERSEDED_VALIDATION_HARNESS_V1_20260818/`
before editing), v2 was edited in place to produce v3 without first
preserving its exact bytes — a repeat of the same process gap already
disclosed for v1's hash-manifest wrapper. v2's hash remains on record
as a historical fact (`v2c_validation_harness.py` SHA256
`f5af922bc75c77654b6906faf2cc8839dcb9ee2bd969141f5207ab2cbc1b06aa`,
differing from v3's, proving a real change occurred), but a literal
byte-for-byte v2 artifact is not available for independent re-inspection.

Refrozen: `V2C_VALIDATION_HARNESS_HASH_MANIFEST_20260818.json` (SHA256
`6b9013ee93fd3fc866c0cc466f669c49c4a750ad30d6e1748ceb9bae3bbeb8cf`),
covering `v2c_validation_harness.py` (SHA256
`6193a386fe4a9cbbbab86c8581829368bf3aceb16e2cba8503d80245c0d44d7b`)
and both test files. **VALIDATION was not accessed at any point during
this correction** — reconfirmed no `V2C_VALIDATION_RUN_20260818`,
`RUN_STARTED`, or `FAILED_INFRASTRUCTURE` artifact exists anywhere in
the real project tree. **The authorization token was not entered.**
Resubmitted for a short final audit; VALIDATION and HOLDOUT remain
sealed.

## Formal governance verdict — v3 re-audit (2026-08-18)

```
V2-C VALIDATION HARNESS V3:          FAIL / SUPERSEDE WITH V4
VALIDATION:                          SEALED
HOLDOUT:                             SEALED
```

The accounting fix and exclusive marker creation were confirmed
correct; all three files matched their frozen manifest hashes and
sizes. **Two P1 governance defects remained, both deeper instances of
the same root problem the v2 fix only partially closed**:

1. **Test hooks bypass the real-data gate** (`v2c_validation_harness.py:817-854`,
   v3 line numbers): the `marker_path`/`protected_root` keyword
   parameters I had added to `run_validation_harness()` — intended as
   test-isolation hooks — were themselves unrestricted production
   parameters. The gate only tested `marker_path.exists()`, with no
   content or identity check, so ANY pre-existing file anywhere on disk
   (an unrelated file that merely happened to exist) satisfied it. A
   caller could unlock real data with zero relationship to a genuine
   one-shot run, using a parameter I had added specifically to make
   testing convenient.
2. **Marker integrity remains unsealed** (`:1006-1035`): exclusive
   creation prevented a second WRITE from overwriting the first, but
   the marker's *content* carried no integrity protection at all - no
   hash, no signature, no binding to which run/output location it was
   for. A marker could be deleted and silently recreated, hand-edited,
   or (mixed with defect 1) an arbitrary unrelated file could stand in
   for one entirely.

The re-auditor noted explicitly that the submitted test itself
acknowledged marker tampering lacked cryptographic detection - a
self-reported gap, not one that had to be independently found.

## Economic-gate-pattern harness — v4, both P1s corrected (2026-08-18)

**53/53 tests passing (up from 47), plus the pre-existing 28 cost-model
tests unaffected (81 total).**

1. **Caller-controlled production gate paths removed entirely**:
   `run_validation_harness()` now takes **zero parameters**;
   `execute_one_shot_run()` takes only `authorized: bool`. Every path
   either function uses (`RUN_OUTPUT_DIR`, `FAILED_BUNDLE_DIR`,
   `RUN_STARTED_MARKER`, `RUN_STARTED_MARKER_SHA`) is a hardcoded
   module-level constant — there is no keyword argument capable of
   redirecting the gate anywhere. Tests exercise every branch via
   `monkeypatch.setattr` on those module constants (a pytest-only
   mechanism with no production calling convention, auto-reverted per
   test), never via a function parameter.
2. **RUN_STARTED cryptographically bound and validated**: the marker
   now carries a `run_id` (128-bit `secrets.token_hex`), records the
   exact `out_dir`/`failed_dir` it authorizes, and includes an
   `integrity_sha256` computed over its own canonicalized fields — plus
   a separate file-level `.sha256` sidecar, the same content+sidecar
   tamper-evidence pattern this project already uses for every other
   frozen artifact. `_verify_run_started_marker()` fail-closes on five
   independent dimensions: existence, sidecar presence, sidecar/file
   hash match, internal integrity-hash self-consistency, and exact
   out_dir/failed_dir identity match. A real bug surfaced and was fixed
   while building this: writing the marker via a text-mode file handle
   let Windows silently translate `\n`→`\r\n`, making the bytes on disk
   disagree with whatever was hashed beforehand — fixed by writing
   binary and hashing what is actually on disk, never a pre-write
   string, exactly matching this project's `_sha256()` convention
   everywhere else.

**New regression tests, exactly as requested**: alternate
`protected_root` no longer exists as a concept to test bypassing (the
parameter is gone; verified via signature inspection); arbitrary marker
files refused (`test_arbitrary_preexisting_file_is_not_accepted_as_a_marker`);
modified markers refused, at both the sidecar layer
(`test_marker_with_sidecar_but_tampered_content_is_refused`) and the
internal-integrity layer with a sidecar regenerated to match
(`test_marker_with_matching_sidecar_but_internally_inconsistent_hash_is_refused`);
mismatched marker/output identities refused, for both out_dir and
failed_dir independently
(`test_marker_bound_to_a_different_out_dir_is_refused_as_mismatched_identity`,
`..._failed_dir_...`); all confirmed to refuse **before any data
read** (each test asserts the `HarnessRefusal` fires at the guard,
never reaching `verify_all_frozen_inputs()` or beyond). Plus a full
seal/round-trip positive control and updated versions of every
marker/failure-finalization test from v2/v3.

**Preserved this time**: v3's exact source files copied to
`SUPERSEDED_VALIDATION_HARNESS_V3_20260818/` *before* editing — closing
the same process gap disclosed (not hidden) for v1→v2→v3.

Refrozen: `V2C_VALIDATION_HARNESS_HASH_MANIFEST_20260818.json` (SHA256
`d6da2956d5982de1fe2a7300cbfdc0e98b527b2418d3f47f4bee57c65d68a04d`),
covering `v2c_validation_harness.py` (SHA256
`5a5031c973db24e50ba6735d29e1a5ae79a6d9a0099f2880a8a9f015b7f3dbb9`)
and both test files. **VALIDATION was not accessed at any point during
this correction** — reconfirmed no `V2C_VALIDATION_RUN_20260818`,
`RUN_STARTED`, or `FAILED_INFRASTRUCTURE` artifact exists anywhere in
the real project tree. **The authorization token was not entered.**
Resubmitted for a short final audit; VALIDATION and HOLDOUT remain
sealed.

## Formal governance verdict — v4 re-audit (2026-08-18)

```
V2-C VALIDATION HARNESS V4:          CONDITIONAL FAIL / ISSUE V4-A1
VALIDATION:                          SEALED
HOLDOUT:                             SEALED
```

Explicitly confirmed "materially stronger" than v3, and explicitly
scoped as a small fix, not a pipeline redesign: production entry points
no longer accept path overrides; exclusive creation confirmed for both
marker and sidecar; file/sidecar/internal-integrity/out_dir/failed_dir
checks all confirmed working; the Windows byte-level hashing fix
confirmed correct; `n_not_flagged` confirmed still correct; all V4
files matched their frozen hashes/sizes; v3 confirmed preserved; no
real run marker or bundle of either kind existed. **Two remaining P1s**:

1. **Marker schema is not validated** (`:883-927`, v4 line numbers): a
   marker's fields were read with bare `.get()` calls and only
   incidentally checked (out_dir/failed_dir equality, integrity-hash
   match) - nothing verified the payload was even a JSON object, had
   exactly the expected keys, or that each field had the expected type.
   A structurally malformed marker (missing a field, an extra field, a
   wrong-typed field, invalid JSON, or a JSON array instead of an
   object) risked an unhandled crash rather than a clean refusal.
2. **Final bundle is not linked to RUN_STARTED** (`:1074-1126`): neither
   the success bundle's `V2C_VALIDATION_GATE_VERDICT.json` nor the
   failure bundle's `V2C_VALIDATION_FAILURE_RECORD.json` recorded which
   marker (`run_id`) had actually authorized the run that produced it -
   the marker and its resulting bundle were two separate, unlinked
   artifacts.

## Economic-gate-pattern harness — V4-A1, both items corrected (2026-08-18)

**67/67 harness tests passing (up from 53), 95/95 total with the
cost-model suite.**

1. **Strict, closed marker schema** (`_MARKER_SCHEMA` + new
   `_validate_marker_schema()`, run inside `_verify_run_started_marker`
   before any hash/identity check): the payload must be a JSON object;
   its key set must be exactly the six schema fields (no missing, no
   extra); every field must have the exact expected type (`bool` values
   explicitly rejected as `str`, since `bool` is a Python `int`
   subclass); `output_dirname` must equal the frozen constant; `run_id`
   must be a well-formed 128-bit hex token; `run_started_utc` must be a
   parseable ISO-8601 timestamp; `integrity_sha256` must be a
   well-formed SHA256 hex digest. Malformed/non-JSON content and
   non-object JSON (e.g. an array) are now caught explicitly and
   refused cleanly via `HarnessRefusal`, never left to crash with a raw
   `AttributeError`/`JSONDecodeError`.
2. **Explicit marker-to-result linkage**: `_require_one_shot_precondition()`
   now returns the verified marker's `run_id` (or `None` when the
   real-root gate is a no-op, i.e. every synthetic test);
   `run_validation_harness()` threads it into `bundle["run_id"]`;
   `execute_one_shot_run()` captures the `run_id` it wrote, and - after
   the pipeline returns - **cross-checks** that the pipeline's own
   independently-verified `run_id` agrees before finalizing anything,
   refusing (as a `FAILED_INFRASTRUCTURE` bundle, itself now also
   carrying the same `run_id`) on any disagreement. Both bundle types
   are now traceable to the one marker that authorized them, not merely
   assumed to correspond.

**New regression tests, exactly as requested**: missing field, extra
field, wrong-typed field (including the `bool`-vs-`str` edge case),
wrong `output_dirname`, malformed `run_id`/timestamp/`integrity_sha256`
format, invalid JSON, and a JSON array instead of an object - nine
schema-validation tests, each confirmed to refuse **before any data
read**, plus a positive control confirming the unmutated baseline
itself still passes (so a passing negative test proves the specific
mutation was caught, not indiscriminate rejection). Bundle linkage:
one test confirming the success bundle's `run_id` matches an
independent re-verification of the marker; one confirming the same for
the failure bundle; one confirming `execute_one_shot_run` itself
refuses (finalizing a `FAILED_INFRASTRUCTURE` bundle, never a success
one) if the pipeline's own verified `run_id` is ever spoofed to
disagree.

**Preserved**: v4's exact source files copied to
`SUPERSEDED_VALIDATION_HARNESS_V4_20260818/` before editing.

Refrozen: `V2C_VALIDATION_HARNESS_HASH_MANIFEST_20260818.json` (SHA256
`02e089bc2d114293d7057498b38be9aef30ab022d78fcb1d61ee8bcbc4050a43`),
covering `v2c_validation_harness.py` (SHA256
`99ff9bf983e96850e20e98848bdd04bf7d491162403c6d6579c155f3183ade56`)
and both test files. **VALIDATION was not accessed at any point during
this correction** — reconfirmed no `V2C_VALIDATION_RUN_20260818`,
`RUN_STARTED`, or `FAILED_INFRASTRUCTURE` artifact exists anywhere in
the real project tree. **The authorization token was not entered.**
Resubmitted for a short final audit; VALIDATION and HOLDOUT remain
sealed.

## Formal governance verdict — final harness audit (2026-08-18)

```
V2-C VALIDATION HARNESS V4-A1:       PASS / FROZEN
```

Final audit found no remaining authorization-blocking defect: strict
closed marker schema enforced; missing/extra/malformed/wrongly-typed/
non-object marker payloads all fail closed; marker hash, internal
integrity, output identity, and failure-directory identity all checked
before any data access; production entry points confirmed free of
caller-controlled path overrides; success verdicts and infrastructure-
failure records both confirmed to carry the verified marker `run_id`;
the cross-check between the entry point's created `run_id` and the
pipeline's independently verified value confirmed present; all
submitted files and the manifest sidecar confirmed matching; v4
confirmed preserved before A1; no real VALIDATION marker or run bundle
exists; HOLDOUT confirmed untouched.

**Current governance status, recorded verbatim as the owner's own
summary:**

```
TRAIN labels:                        PASS / FROZEN
TRAIN features:                      PASS / FROZEN
TRAIN classifier:                    PASS / FROZEN
Economic gate A2:                    PASS / FROZEN
VALIDATION harness V4-A1:            PASS / FROZEN
VALIDATION:                          CLEARED FOR ONE-SHOT EXECUTION,
                                      NOT YET OPENED
HOLDOUT:                              SEALED
```

Every prerequisite this whole track has built toward is now genuinely
satisfied. **The one remaining step — explicit authorization of the
real, irreversible, one-shot VALIDATION run — is a decision for the
owner alone, not one this record or any prior verdict makes on their
behalf.** Per standing instruction: no incremental inspection of
VALIDATION, no rerun after seeing results, HOLDOUT stays sealed
regardless of the VALIDATION outcome.

## First real execution attempt — FAILED_INFRASTRUCTURE, no predictive/economic VALIDATION exposure (2026-08-18)

**Governance correction, 2026-08-19 (applied retroactively to this
section's own wording — see the dedicated correction section below for
the full account):** the phrase "zero VALIDATION exposure" originally
used as this section's heading, and "before any VALIDATION-period file
was ever opened" / "No VALIDATION-period certified data file was read"
in the body below, are imprecise. `verify_all_frozen_inputs()`
performs byte-level SHA-256 integrity reads of the certified dataset
files (Stage 1) before the A2 manifest check that actually failed —
Attempt 1 DID read VALIDATION-period file bytes, for hash verification
only. It never parsed, inspected, labeled, scored, summarized, or
economically evaluated any VALIDATION-period price observation, since
event discovery (Stage 2, the first stage that interprets a price
value) was never entered. The body text below is left as originally
written, for an honest record of exactly what was said at the time;
treat this note as the authoritative correction.

The owner explicitly authorized the real run. `python v2c_validation_harness.py --confirm=AUTHORIZE_IRREVERSIBLE_VALIDATION_RUN_20260818`
was executed from the real project root, for real, exactly once.

**Result: FAILED_INFRASTRUCTURE, not a research verdict.** RUN_STARTED
(`run_id b5033953f064296e38126f3e11297770`) was written first, as
designed. The run then crashed inside `verify_all_frozen_inputs()` -
**before any VALIDATION-period file was ever opened** - because
`V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json` (built in an earlier
phase of this session, with a different working directory) records its
file paths relative to `P01D_V2B_REGIME_TWO_PILLAR_20260816/`, while
every other manifest (TRAIN classifier, dataset) records paths relative
to the project root - the harness's `_verify_manifest_files()` assumed
the latter convention universally. The 67-test synthetic suite never
caught this because its own fixture built a self-consistent fake A2
manifest using the *correct* (project-root) convention, not a replica
of the real file's actual, inconsistent one - a genuine testing gap,
not a flaw in the safety mechanism itself.

**The safety mechanism worked exactly as designed despite the genuine,
unexpected failure**: RUN_STARTED marker written and sealed correctly;
a hash-manifested `V2C_VALIDATION_RUN_20260818_FAILED_INFRASTRUCTURE/`
bundle was produced, self-consistent (verified independently - zero
manifest mismatches), carrying the exact same `run_id` as the marker
(explicit linkage confirmed working under a real failure, not just in
tests); the failure record is unambiguously labeled
`"status": "FAILED_INFRASTRUCTURE"` with the note that it "must not be
treated as a negative result." **No `V2C_VALIDATION_RUN_20260818`
success directory exists. No VALIDATION-period certified data file was
read.** The real project root now permanently carries this RUN_STARTED
marker and FAILED_INFRASTRUCTURE bundle as evidence of this attempt;
per the harness's own design, neither `execute_one_shot_run` nor
`run_validation_harness` can be invoked again at this location without
a human first investigating and deliberately clearing them - exactly
as intended, and not done unilaterally here.

**Reported to the owner in full** rather than silently patched and
retried - fixing the manifest-path bug and requesting a fresh,
separate authorization for a new attempt is the owner's decision, not
one taken automatically on their behalf.

## Attempt 1 recovery — harness corrected to V4-A2, submitted for short audit, Attempt 2 NOT YET authorized (2026-08-18)

The owner's exact 8-step recovery instruction was followed in full.
**Attempt 1's marker and `FAILED_INFRASTRUCTURE` bundle remain exactly
as they were written — permanently preserved, never cleared,
overwritten, or deleted** (confirmed unchanged: same file sizes,
same 21:52 timestamps, before and after every fix and test run below).

1. **Preserved.** The V4-A1 harness and both its test files were
   copied byte-for-byte to
   `SUPERSEDED_VALIDATION_HARNESS_V4A1_20260818/` before any edit.
2. **`_verify_manifest_files()` fixed.** Gained an explicit `base_dir:
   Path = Path(".")` parameter, documented in-line as to why: the real
   A2 manifest (`V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json`)
   records paths relative to `P01D_V2B_REGIME_TWO_PILLAR_20260816/`,
   while the TRAIN classifier manifest records paths relative to the
   project root — a genuine, pre-existing convention mismatch between
   two independently frozen artifacts, not a bug to "normalize away."
3. **Correct base wired per manifest.** `verify_all_frozen_inputs()`
   now calls `_verify_manifest_files(CLASSIFIER_MANIFEST_JSON, ...,
   base_dir=Path("."))` and `_verify_manifest_files(A2_MANIFEST_JSON,
   ..., base_dir=REAL_BASE)` — each a fixed, hardcoded value, not a
   caller-facing parameter. **Verified directly against the real,
   untouched project files**: `verify_all_frozen_inputs()` now
   succeeds, returning all 6 hash results (previously crashed on the
   A2 manifest).
4. **Regression test added using the real convention.** The synthetic
   fixtures in `test_v2c_validation_harness_synthetic.py` had been
   (wrongly) building their fake A2 manifest with the *corrected*
   convention, which would never have caught this bug; both fixtures
   were fixed to replicate reality's actual (mixed) convention, and
   `test_a2_manifest_entries_resolve_relative_to_p01d_subdirectory_not_project_root`
   plus
   `test_a2_manifest_verification_fails_closed_if_resolved_against_the_wrong_base`
   were added to `test_v2c_validation_harness_failclosed.py`, the
   latter proving the `base_dir` fix does real work (forcing
   project-root resolution for the A2 manifest and confirming it then
   fails closed).
5. **Preflight-only regression test added.**
   `test_real_environment_preflight_passes_without_entering_event_discovery`
   runs `verify_all_frozen_inputs()` against the real, unmodified
   project files with `discover_validation_events` monkeypatched to
   raise `AssertionError` if called at all — the single most direct
   regression test for Attempt 1's exact failure mode.
6. **Attempt 2 given distinct canonical names.** `RUN_OUTPUT_DIRNAME`
   and `FAILED_BUNDLE_DIRNAME` were renamed from
   `V2C_VALIDATION_RUN_20260818` / `..._FAILED_INFRASTRUCTURE` to
   `V2C_VALIDATION_RUN_20260818_ATTEMPT2` /
   `..._ATTEMPT2_FAILED_INFRASTRUCTURE`, so Attempt 2's machinery can
   never collide with, overwrite, or be mistaken for Attempt 1's
   permanently-preserved evidence.
7. **Refrozen and submitted for short audit — this section.**
8. **Fresh authorization not yet requested** — pending the owner's
   short audit of everything below, per their own step 7 before their
   own step 8.

**An additional, self-found gap, beyond the owner's literal 8 steps.**
While diagnosing why one pre-existing test
(`test_refuses_to_run_against_the_real_project_root_without_authorization`)
started failing — caused simply by Attempt 1's own real marker now
legitimately existing — inspection showed that
`_require_one_shot_precondition()`, as it stood, would have treated
that still-cryptographically-valid marker as sufficient to authorize a
**new** call to `run_validation_harness()`, even though Attempt 1 had
already concluded (as a failure). A marker's validity is deliberately
permanent (it is audit-trail evidence), so validity alone is not the
same as "this specific attempt has not yet concluded." This was
confirmed empirically, read-only, against the real environment, before
being treated as a real finding. **Fixed** by having
`_require_one_shot_precondition()` also refuse outright if
`RUN_OUTPUT_DIR` or `FAILED_BUNDLE_DIR` already exists at the canonical
location — closing the gap structurally for every future attempt, not
only this one. Two new tests
(`test_a_stale_but_valid_marker_cannot_reauthorize_reading_data_once_a_success_bundle_exists`,
`..._once_a_failure_bundle_exists`) prove a valid, well-formed,
correctly bound marker is refused once either bundle directory already
exists. **Re-verified against the real environment**: with the new
ATTEMPT2-suffixed names in place and no marker yet written at that
location, `_require_one_shot_precondition()` correctly refuses with
"no RUN_STARTED marker exists" — the gate is back to its expected
not-yet-started state for Attempt 2.

**Verification performed this round, all independently confirmed, not
just asserted:**
- `verify_all_frozen_inputs()` called directly against the real
  environment — succeeds, 6/6 hash checks pass.
- `_require_one_shot_precondition()` called directly against the real
  environment — correctly refuses (no ATTEMPT2 marker exists yet).
- Full harness test suite: **73/73 passing**
  (`test_v2c_validation_harness_failclosed.py` +
  `test_v2c_validation_harness_synthetic.py`, up from 71/71 at V4-A1;
  +2 for the manifest-base_dir fix's own regressions, +2 for the
  marker-reuse-gap fix — net +4, with one incidental path-bug in a new
  test caught and fixed before counting).
- Cost-model regression suites re-run for full confidence:
  `test_v2c_train_era_costs.py` + `test_v2c_validation_era_costs.py` —
  **28/28 passing**, unaffected.
- Real project directory listing re-checked before and after every
  fix and test run: only Attempt 1's original three artifacts
  (`V2C_VALIDATION_RUN_20260818.RUN_STARTED.json` + `.sha256`,
  `V2C_VALIDATION_RUN_20260818_FAILED_INFRASTRUCTURE/`) exist, all at
  their original 21:52 timestamps — no ATTEMPT2 marker or bundle has
  been created by anything in this correction round, since none of it
  ran the real one-shot pipeline.
- V4-A1's exact bytes preserved at
  `SUPERSEDED_VALIDATION_HARNESS_V4A1_20260818/` before any edit.
- Corrected harness (`v2c_validation_harness.py`) plus both test files
  hash-frozen as V4-A2:
  `V2C_VALIDATION_HARNESS_V4A2_HASH_MANIFEST_20260818.json` (+
  `.sha256` sidecar), recording all three files' SHA-256 and byte
  sizes.

**Status: VALIDATION HARNESS V4-A2 — corrected, tested, refrozen,
submitted for the owner's short audit. Attempt 2 is NOT authorized and
will not run until the owner explicitly authorizes it separately, per
their own step 8. VALIDATION remains SEALED (Attempt 1 never opened
it; nothing in this correction round opened it either). HOLDOUT
remains SEALED.**

## V4-A2 audit verdict: PASS — with one governance wording correction (2026-08-19)

**V2-C VALIDATION HARNESS V4-A2 — PASS / FROZEN. ATTEMPT 2 — NOT
AUTHORIZED. HOLDOUT — SEALED.** The owner independently verified all
eight items above (A2/classifier manifest base resolution, distinct
Attempt-2 canonical paths, Attempt-1 evidence intact, the marker-reuse
gap closed for both success- and failure-bundle cases, the two new
regression tests reproducing the real mixed-path convention, harness
and test files matching the V4-A2 manifest, no Attempt-2 artifact
existing) and found the corrected code sound.

**One correction was required, in the record's own wording, not the
harness logic**: the module docstring and one test's comments had
stated "no VALIDATION-period file was ever opened" / "before any
VALIDATION-period file was ever opened." This is literally incorrect.
`verify_all_frozen_inputs()`'s Stage 1 performs byte-level SHA-256
integrity reads of the certified dataset files, and that stage runs
**before** the A2 manifest check that actually failed — so Attempt 1
did read VALIDATION-period file bytes, for hash verification only.
**The accurate description, now used throughout**: Attempt 1 performed
preflight byte-level integrity reads of the certified dataset files
for hash verification. It did not parse, inspect, label, score,
summarize, or economically evaluate any VALIDATION-period price
observation. Event discovery was never entered, and no research result
was exposed. This is preflight integrity checking, not a predictive or
economic look at VALIDATION — it does not invalidate the TRAIN/
VALIDATION/HOLDOUT split and does not block Attempt 2.

**Correction applied precisely where the wording lived, nothing else
touched:**
- `v2c_validation_harness.py`'s module docstring (V4-A2 paragraph, and
  the `_verify_manifest_files()` docstring's account of what Attempt 1
  discovered) corrected to this precise wording.
- `test_v2c_validation_harness_failclosed.py`'s comments around
  `test_real_environment_preflight_passes_without_entering_event_discovery`
  corrected the same way (this test itself was and remains correct —
  it proves event discovery is never entered; only its own comment's
  claim about dataset-file access was imprecise).
- This section (immediately above) annotated in place, and the
  original "zero VALIDATION exposure" heading corrected, rather than
  silently rewritten — the original wording is kept, marked, so the
  record shows exactly what was said and exactly what was corrected.
- No harness logic changed. `verify_all_frozen_inputs()`,
  `_require_one_shot_precondition()`, `run_validation_harness()`, and
  every gate/pipeline function are byte-identical in behavior to the
  V4-A2 the owner just audited.

**Because these corrected comments live inside files already covered
by the V4-A2 hash manifest, this required an actual refreeze, not just
an edit** — the previously-audited V4-A2 bytes were first reconstructed
exactly (by reversing this round's edits) and hash-verified against the
prior manifest's own recorded SHA-256 values (all three files matched
bit-for-bit) before being archived at
`SUPERSEDED_VALIDATION_HARNESS_V4A2_PRECORRECTION_20260819/`, preserving
the exact version the owner audited. The corrected files were then
re-hashed into the same
`V2C_VALIDATION_HARNESS_V4A2_HASH_MANIFEST_20260818.json` (+ `.sha256`
sidecar) — still labeled V4-A2 since no logic changed, only wording,
with the manifest's own description recording that this is a
documentation-only correction pass.

**Re-verification after the refreeze:**
- Full harness suite: **73/73 passing**, unchanged count (wording-only
  edits, no test added or removed).
- Cost-model suites: **28/28 passing**, unaffected.
- Attempt 1's marker and `FAILED_INFRASTRUCTURE` bundle re-confirmed
  untouched (same file sizes, same original timestamps).
- No Attempt-2 artifact exists — this correction round never called
  `run_validation_harness()` or `execute_one_shot_run()`.

**Status: VALIDATION HARNESS V4-A2 (governance-wording-corrected) —
PASS / FROZEN. Attempt 2 remains NOT AUTHORIZED pending a fresh,
separate, explicit go-ahead from the owner. VALIDATION remains SEALED.
HOLDOUT remains SEALED.**

## Attempt 2 — executed, real result, VALIDATION now genuinely opened (2026-08-19)

The owner audited V4-A2 (PASS/FROZEN, governance wording accepted) and
gave fresh, explicit, one-shot authorization: *"I explicitly authorize
one—and only one—execution of VALIDATION Attempt 2 using the frozen
V4-A2 harness and its canonical Attempt-2 paths."*
`python v2c_validation_harness.py --confirm=AUTHORIZE_IRREVERSIBLE_VALIDATION_RUN_20260818`
was run from the real project root, for real, exactly once, with no
retry and no intermediate inspection.

**Result: a genuine SUCCESS bundle** at
`P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_VALIDATION_RUN_20260818_ATTEMPT2/`
(`run_id 91ae0b89eba73a7b5653e26da6d32db8`, matching the RUN_STARTED
marker's own `run_id` exactly). Independently re-verified after the
run: all 5 bundle files' SHA-256 match the bundle's own
`V2C_VALIDATION_RUN_HASH_MANIFEST.json`; that manifest's own sidecar
hash matches; the verdict JSON's `run_id` matches the marker.

**Verdict, exactly once, as printed by the harness itself:**
- **Predictive gate: PASS.** AUC-ROC = 0.6948 (>= 0.55 threshold), on
  995 resolved VALIDATION events.
- **Economic gate E1 (n>=30): PASS.** 595 flagged and priced trades.
- **Economic gate E2 (>=3 quarters): PASS.** 5 quarters present
  (2021Q1 - 2022Q1).
- **Economic gate E3 (four required checks per A2 §3): FAIL.**
  `mean_return_dp_excluded` = 0.00114 (threshold 0.003, FAIL);
  `win_rate_dp_excluded` = 0.526 (threshold 0.50, PASS);
  `mean_return_dp_stressed` = 0.00107 (threshold 0.003, FAIL);
  `win_rate_dp_stressed` = 0.523 (threshold 0.50, PASS).

**Overall: the preregistered predictive gate passes** (AUC-ROC 0.6948
clears the frozen >=0.55 threshold on held-out VALIDATION events) **-
this is a preregistered pass/fail comparison, not a claim of
statistical significance; no confidence interval or significance test
was preregistered or computed, and none is claimed here** - **but the
full economic gate does NOT pass** - both win-rate checks clear their
50% bar, but mean per-trade return falls short of the required 0.3%
threshold under both the DP-excluded and DP-stressed cost assumptions.
Under A2's own rule (all four checks required), **E3 fails, so the
economic gate as a whole fails**, even though E1 and E2 individually
pass.

**Also true and worth stating plainly**: this is a real result, not a
rescue candidate. No retuning, threshold-lowering, or re-slicing is
being proposed or performed here - that would be exactly the kind of
same-night hypothesis rescue this project's entire discipline (V2-C's
own no-rescue design, the RRME architecture's own closure rule for
Pillar I/II) exists to prevent. HOLDOUT was never opened, referenced,
or read by anything in this run or this report. No further inspection
of the bundle's underlying event/trade-level CSVs was performed beyond
the harness's own printed top-level summary and the file-level
integrity re-check above.

**Status: V2-C VALIDATION — OPENED, ATTEMPT 2 COMPLETE. Predictive
gate PASS, economic gate FAIL (E3). HOLDOUT remains SEALED - not
reachable from any passing result, since the economic gate did not
pass.** Superseded by the formal closure verdict immediately below -
this section is kept as the contemporaneous record of the run itself.

## V2-C — final disposition: CLOSED / FAIL / OBSERVE_ONLY (2026-08-19)

The owner independently re-audited the Attempt-2 bundle (all five
artifact hashes and sizes match; manifest sidecar and marker sidecar
match; marker and verdict share `run_id
91ae0b89eba73a7b5653e26da6d32db8`; counts reconcile: 1,086 scored + 1
abstained = 1,087 events, 595 flagged + 491 not flagged = 1,086
scored) and issued the formal closing verdict, recorded verbatim:

```
V2-C VALIDATION ATTEMPT 2 — PASS INTEGRITY / FROZEN
PREDICTIVE GATE — PASS
ECONOMIC GATE — FAIL
V2-C QUALIFICATION — CLOSED / FAIL / OBSERVE_ONLY
HOLDOUT — SEALED; DO NOT OPEN
```

Because Pillar I, Pillar II, and V2-C have all now failed
qualification, **no validated standalone candidate exists from any
Stage-1 track**. Consequently: Stage 2 (shadow tournament) — BLOCKED;
Stage 3 (router/combination research) — BLOCKED; Stage 4
(production-path integration shadow) — BLOCKED. V2-C receives ZERO
trading or veto authority. No threshold changes, alternative exits,
reslicing, or rescue analysis of any kind.

**Full closure record, hash-manifested, referencing the immutable
Attempt-2 bundle without restating or copying it**:
`P01D_V2C_CLOSURE_MANIFEST_20260819.txt` (this directory) + its
`.sha256` sidecar.

**The research cycle is now paused.** Any later hypothesis (a new
intraday feature family, a different exhaustion/deterioration
formulation, a revisited cost/threshold structure) must begin as an
entirely new preregistered program, independently frozen through its
own D0, never as a repair, retune, or rerun of anything closed here.

## Pillar I — CLOSED, FAIL

Trend-continuation hypothesis. All 5 preregistered candidates net
negative, consistently across both discovery sub-blocks. Best of 5
(C4): −₹17.60/trade. Zero candidates cleared the discovery hurdle.
Confirmation never opened.
Record: `frozen_releases`-equivalent in the sandbox —
`P01D_PILLAR_I_INTRADAY_V1_CLOSURE_MANIFEST_20260818.txt`
(`C:\Users\Dishan\Python - ARR\Zerodha_live_bot_3.4\P01D_intraday_intelligence_R0_20260815\`).

## Pillar II — CLOSED, FAIL

Idiosyncratic-dislocation-reversion hypothesis. All 5 preregistered
candidates failed the viability filter (positive at 0bps *and* 2bps, in
*both* discovery sub-blocks independently) — zero survivors, so no
ranking occurred. Confirmation never opened.
Genuine finding, carried forward as a note, not a rescue: C3's
VWAP-reclaim exit reclaimed 84% of the time (97/115) — the reversion
phenomenon itself is common in this data — yet net economics stayed
negative. Reversion occurring and reversion being profitably capturable
at this cost/sizing structure are different claims.
Record: `P01D_PILLAR_II_INTRADAY_V1_CLOSURE_MANIFEST_20260818.txt`, same
sandbox location.

## Router, shadow tournament, request_entry() integration — BLOCKED

Per R0's own rule: these require at least one validated alpha candidate.
None exists. Not opened, not attempted, not scheduled — this is the
architecture behaving as designed, not a gap to fill.

## V2-C — the sole open lane [SUPERSEDED 2026-08-19 — CLOSED / FAIL / OBSERVE_ONLY, see "V2-C — final disposition" above; the narrative below is the contemporaneous 2026-08-18 record of how V2-C got to that point, kept unedited]

Resolver specification sealed
(`P01D_V2B_REGIME_TWO_PILLAR_20260816\P01D_V2C_OUTCOME_RESOLVER_D0_20260818.md`).
67/67 engineering tests still pass. Zero real-data validation exists.
**Acquisition complete, same night**: `v2c_acquire_15min_dataset.py`
(project root) pulled 68 of 76 requested security-intervals + NIFTY 50
against the point-in-time universe, 2014-12-03..2023-07-31; 8 symbols
correctly logged as unresolved rather than fabricated (real corporate
actions — merger/rename/delisting — not acquisition bugs), 2 identity
cases (SSLT/VEDL) already flagged in the source requirements file and
correctly skipped. **Certification now in progress**
(`v2c_certify_15min_dataset.py`, project root) — first pass found the
dataset itself is largely sound; nearly all "FAIL" verdicts trace back to
~16 shared calendar dates (Muhurat sessions, Budget days, COVID
circuit-breaker halts, one NSE system outage), not per-symbol defects.
**Update, same session**: all 13 special-session dates independently
sourced (calendar/news/regulatory sources, not inferred from the price
file — see `V2C_CALENDAR_DATABASE_CORRECTION_20260818.md`), and LTIM is
resolved (not delisted — renamed to `LTM` effective 2026-02-27, see
`V2C_IDENTITY_RESOLUTION_LTIM_LTM_20260818.md`). **Six dates remain
genuinely unresolved** (2016-01-01, 2017-07-10, 2015-03-16, 2015-12-22,
2020-04-27, 2023-07-20) — not yet classified, no source found either
direction.

**Further update, same session**: certification was explicitly NOT
rerun until the following were done first, per the required order —
1. Interim-status stale lines fixed (this document). DONE.
2. All 13 audited special-session dates encoded into
   `v2c_certify_15min_dataset.py` itself (`SPECIAL_SESSION_DATES` +
   per-type grid handling — MUHURAT excluded from the 25-bar grid,
   BUDGET_SPECIAL added to it, CIRCUIT_INTERRUPTED/
   EXTENDED_OUTAGE_RECOVERY gaps routed to a documented bucket, not
   FAIL). DONE, syntax-verified.
3. Corrected calendar file + hash built:
   `NSE_TRADING_CALENDAR_CORRECTED_20260818.csv` (2165 rows, +
   `session_type`/`correction_note` columns; `regular_session` changed
   on exactly the 10 dates the audit table calls for — 8 MUHURAT →
   FALSE, 2 BUDGET_SPECIAL → TRUE; the 6 unresolved dates get a
   `session_type=UNRESOLVED_CALENDAR_EXCEPTION` flag only, value left
   unchanged, no guessing), SHA256 sidecar
   `NSE_TRADING_CALENDAR_CORRECTED_20260818.sha256`. DONE (built via
   `v2c_build_corrected_calendar.py`, project root).
4. **Round 2 review, same session** (external review, addressed
   before any rerun): three blocking issues found and fixed —
   (a) LTIM reacquisition script accepted any non-empty Kite response
   and cleared the unresolved-token flag on token-resolution alone;
   now requires a fail-closed identity-bridge check (timestamps parsed,
   zero duplicates, >=95% of distinct returned dates actually inside
   the required 2023-06-14..2023-07-31 window, batch min/max touching
   that window) before writing anything or touching the summary/
   unresolved-token files — a resolving `LTM` token alone does not
   prove Kite exposes the historical `LTIM` period under it.
   (b) Certification was still pointed at the uncorrected working
   calendar; now points at `NSE_TRADING_CALENDAR_CORRECTED_20260818.csv`
   and refuses to run (`verify_calendar_hash()`, hard exit) if that
   file's SHA256 doesn't match the frozen sidecar.
   (c) The 13-date exceptions were whole-date exemptions (any missing
   bar or any out-of-session timestamp on a documented date passed,
   which could have silently excused an unrelated gap or fabricated
   timestamp). Tightened to event-scoped windows, independently
   sourced: `MUHURAT_SESSION_WINDOW` (17:00–19:45, a conservative
   corridor covering the 6 of 8 Muhurat years with a directly-sourced
   NSE circular window, applied uniformly to all 8 since 2016/2019
   lacked a locatable circular); `CIRCUIT_WINDOWS` (per-date halt
   windows for 2020-03-13/2020-03-23, BusinessToday/Business Standard);
   `OUTAGE_WINDOW` (2021-02-24 missing/extra sub-windows, SEBI/RBI/
   Zerodha Z-Connect). BUDGET_SPECIAL now gets no exemption at all
   (ordinary-day treatment beyond being added to the grid);
   CIRCUIT_INTERRUPTED gets missing-slot documentation only, never
   extra-bar documentation. All three fixes syntax-verified and
   exercised with synthetic single-file self-tests (Muhurat noon bar
   now unexplained; circuit-date gap outside the halt window now
   correctly FAILs while the in-window slot is documented; Budget
   Saturday stray bar now unexplained; tampered-calendar hash mismatch
   correctly refused) — not the authoritative multi-file rerun itself.
5. LTIM re-acquisition under `LTM`: **DONE, live, passed its own
   fail-closed identity-bridge check** — 825 candles, 33 distinct
   dates, 100% inside the required 2023-06-14..2023-07-31 window, 0
   duplicate timestamps. Written, summary appended, LTIM cleared from
   the unresolved-token file (7 genuinely-delisted/merged symbols
   remain, untouched).
6. **Authoritative certification rerun — DONE**, then extended with a
   second, sourced pass after its own results surfaced new residual
   patterns (same discipline as everything above: independent source
   first, price file for confirmation only):
   - First rerun (69 intervals): 64 FAIL, 4 PASS_WITH_DOCUMENTED_EXCEPTION, 1 PASS.
   - **2017-07-10 resolved** (NSE technical glitch, 5 independent news
     sources - BusinessToday, Business Standard x3, India Infoline,
     Forbes India, Zee Business; delayed open 09:15-12:15, resumed
     12:30) — added as a 14th audited date.
   - **2016-01-01 reclassified**, not exempted: the 2016 official
     holiday list confirms Jan 1 was a normal trading day (not a
     calendar error) - the uniform 25/25-bar absence across 46/69
     files is a genuine acquisition/archive data gap, deferred to the
     corporate-action-audit/final-disposition step, not silently
     passed.
   - **2021-02-24 boundary-corrected**: the sourced outage's documented
     end (15:17) falls after the 15:15 bar-open, so that slot belongs
     inside the documented-missing window (was 10:00-15:00, corrected
     to 10:00-15:15) - a grid-mapping fix to an already-sourced window,
     not a new claim.
   - **2015-01-16 and 2017-02-21 newly flagged** (NIFTY-50-index-only,
     surfaced by the rerun itself, never part of the original 78-row
     per-symbol residual review) — recorded as
     `NEWLY_FLAGGED_UNINVESTIGATED`, not resolved, not exempted.
   - Final rerun after all of the above: **55 FAIL, 13
     PASS_WITH_DOCUMENTED_EXCEPTION, 1 PASS**. Calendar-level view now
     names exactly 5 dates: 2015-03-16, 2016-01-01 (confirmed data gap,
     not a calendar issue), 2020-04-27 (new observation - a uniform
     extra bar at 15:30 across 28/69 files - explicitly NOT treated as
     resolution evidence, stays unresolved), 2015-12-22, 2023-07-20.
   - Full detail: `V2C_CALENDAR_DATABASE_CORRECTION_20260818.md`
     "Round 2" section; corrected calendar rebuilt, new SHA256
     `45aae0e52ca2a9cde96b9324d55067d52d7ba2142aefe0c6faae03940b63d4ed`.
7. **Corporate-action audit — DONE** for all 69 acquired intervals,
   kept deliberately separate from the residual-calendar-date
   disposition (per instruction: never used to explain away a session
   gap unless it genuinely does — confirmed none of the 5 residual
   calendar dates or 2 NIFTY-only anomalies coincide with any action
   found here). Two-stage methodology: price-driven screening (170
   candidate dislocations ≥12% log-move, `v2c_corporate_action_screen.py`,
   consistency-check only per instruction, never the basis for a
   finding) followed by sourced verdict assignment
   (`v2c_corporate_action_audit.py`). Per-row schema now matches the
   exact spec given: security_key, historical_symbol, window,
   corporate_action_present (Y/N/UNKNOWN, asked independently of the
   disposition), action_type, effective_or_ex_date, source,
   ohlc_series_raw_or_adjusted, disposition (5-category taxonomy),
   largest-discontinuity-near-action consistency check, note. Result:
   **67 PASS_NO_ACTION, 1 PASS_ACTION_ADJUSTED (INFY), 1
   REQUIRES_ADJUSTMENT (YESBANK), 0 EXCLUDE, 0 UNRESOLVED**. Six
   symbols got dedicated, individually-sourced investigation (INFY,
   YESBANK, HDFCBANK, GRASIM ×2, ADANIENT/ADANIPORTS); the rest rest on
   a disclosed methodology limit (no clean split/bonus ratio match +
   consistency with the well-documented COVID-2020 crash, not
   individually news-verified per date). Real, sourced findings:
   YESBANK's 2020-03-04..2020-03-18 window (RBI moratorium +
   reconstruction scheme) recommended for exclusion from V2-C labeling;
   HDFC-HDFCBANK merger (2023-07-01) and the Grasim/ABNL merger-
   demerger (~2017-07-05) both confirmed to require no adjustment (fall
   outside/between the acquired windows respectively). **New finding,
   separate from corporate actions**: two data artifacts (INFY
   2015-04-24, YESBANK 2015-08-12) — full sessions of zero-volume,
   identical-OHLC bars at ~4-5× the real surrounding price, reverting
   the next real day. These pass bar-count certification (25/25
   present) while containing no real trading information — a defect
   class the calendar/session certification pass cannot catch by
   construction. Both must be excluded from any future labeling. Full
   detail: `V2C_CORPORATE_ACTION_AUDIT_20260818.md`.
8. **Residual-date work-through — IN PROGRESS**, worked in the
   instructed order:
   - **2016-01-01 — RESOLVED.** Targeted diagnostic reacquisition
     (`v2c_reacquire_diagnostic_20160101.py`): independent fresh pull
     of HDFCBANK/RELIANCE/INFY/SBIN + NIFTY 50 control confirmed 0/25
     bars for all 4 sample equities, matching the original acquisition
     exactly, while NIFTY 50 had full data both times — a persistent
     upstream gap for individual equities, not an acquisition bug.
     Added to certification as `CONFIRMED_UPSTREAM_DATA_GAP` (full-day
     documented window). FAIL count dropped 55→53; calendar-level view
     now names only 4 dates.
   - **2020-04-27 — investigated, stays unresolved.** No independent
     evidence found (equity cash-market hours unchanged during COVID
     lockdown; deferred NIFTY reconstitution still pending as of a
     2020-05-13 press release, not effective 2020-04-27). Per
     instruction, not blessed on pattern-uniformity alone.
   - **2015-03-16 — RESOLVED.** Targeted diagnostic reacquisition
     (`v2c_reacquire_diagnostic_20150316.py`, chosen over further web
     search per instruction, since search had already run dry and the
     precise pattern made a fresh pull more informative): HDFCBANK,
     RELIANCE, INFY, SBIN, and NIFTY 50 — all 5, unanimously — returned
     exactly 124/125 slots, missing precisely the 09:15 bar-open,
     matching the original acquisition exactly, 0 duplicates.
     **Type A**: persistent upstream Kite slot-level gap, same
     evidentiary structure as 2016-01-01/2015-01-16, single-slot rather
     than full-day. Added to certification as
     `CONFIRMED_UPSTREAM_DATA_GAP` with a deliberately narrow
     single-slot `GAP_WINDOWS` entry (09:15 only) so any other missing
     slot on this date stays a real, undocumented gap. **Effect: FAIL
     dropped 53→36** (this was the single most widely shared cause,
     47/69 files). YESBANK's own remaining FAIL cause is now purely
     2015-12-22; NIFTY's is purely 2017-02-21. Calendar hash
     `23713384e9c9610d51eed7b7cda3c3ff496826ffac3a6b49d45b48a97110380f`.
     Also fixed in passing: an earlier note wrongly called this
     pattern "non-uniform" — corrected inline, not silently.
   - **2015-12-22 — RESOLVED.** Same method as 2015-03-16
     (`v2c_reacquire_diagnostic_20151222.py`). Result initially read as
     "mixed" by the script's own summary — HDFCBANK/INFY/NIFTY showed
     10:30 present, RELIANCE/SBIN showed it missing, both fresh and
     original — but that framing was checked before accepting it, not
     taken at face value: cross-referenced against the original
     certification report, which confirms HDFCBANK/INFY/NIFTY were
     never among the 20 originally-affected files at all. Clean
     **Type A** confirmation for the symbols actually affected
     (RELIANCE, SBIN). Added to certification as
     `CONFIRMED_UPSTREAM_DATA_GAP`, single-slot (10:30 only), same
     discipline as 2015-03-16. **Effect: FAIL dropped 36→28.**
     Calendar-level view now names exactly **2 dates: 2020-04-27,
     2023-07-20**. **YESBANK's bar-level verdict finally reached
     PASS_WITH_DOCUMENTED_EXCEPTION** (its last unrelated gap resolved)
     — `release_disposition` correctly reached
     `CERTIFIED_WITH_FROZEN_EXCLUSION` for the first time. Calendar
     hash `7e9f9a5733e2fcfdb976ef03dab1723be7de9db2d7f02456dfca34ca91d2eeb7`.
   - **2020-04-27 — diagnostic run, Case A confirmed, classification
     deferred.** Two-group design (AFFECTED: HDFCBANK/INFY/RELIANCE;
     UNAFFECTED: SBIN/ADANIPORTS/ASIANPAINT; CONTROL: NIFTY 50), all
     drawn from the actual certification report first.
     `v2c_reacquire_diagnostic_20200427.py`: all 3 affected symbols
     reproduce the exact 15:30 bar in a fresh pull; all 3 unaffected +
     NIFTY stay clean. Bar shape (diagnostic evidence only): flat
     (O=H=L=C) but genuinely different small nonzero volume per symbol
     (202/12/946), not zero-volume-synthetic, not a duplicate of that
     symbol's own 15:15 bar - consistent with a real isolated tiny
     late print, not a fabricated placeholder. Satisfies the stated
     criteria for `CONFIRMED_UPSTREAM_EXTRA_BAR_ARTIFACT`, but
     classification and any narrow cleaning rule explicitly **deferred
     to final certification review**, per instruction - reproduction
     establishes persistent upstream behavior, not legitimacy, and
     those stay separate claims. **Still the sole remaining calendar-
     level FAIL cause (28/69 files).**
   - **2023-07-20 — RESOLVED.** Two-group design (AFFECTED: RELIANCE;
     UNAFFECTED: HDFCBANK/INFY/TCS; CONTROL: NIFTY 50).
     `v2c_reacquire_diagnostic_20230720.py`: RELIANCE reproduces
     exactly the same 3-slot gap (09:15/09:30/09:45) in a fresh pull;
     all controls fully clean, fresh and original. Clean Type A. Added
     as `CONFIRMED_UPSTREAM_DATA_GAP`, 3-slot `GAP_WINDOWS` entry.
     RELIANCE's `interior_missing_dates_full` now empty for this date.
     Total FAIL/PASS counts unchanged from the 2020-04-27 rerun only
     because RELIANCE is *also* one of the three symbols still failing
     on 2020-04-27 - not because this resolution had no effect.
     **Calendar-level view now names exactly 1 date: 2020-04-27**.
     Calendar hash
     `3b9033e265f53691d7d8a0ffbe2a39b73e1bfc12ffacfbb064a8bba3ed21d91b`.
   - **2015-01-16 — RESOLVED, via two rounds of correction.** First
     characterized as "NIFTY-only" (wrong); corrected to "market-wide"
     (also incomplete - true that it's absent everywhere, but the
     mechanism differs by instrument class); **now fully resolved**
     after the diagnostic ran live. Result: HDFCBANK/RELIANCE/INFY/SBIN
     returned an empty batch for the whole diagnostic window - checked
     directly, their real Kite history starts 2015-02-02, entirely
     after the window, the same pre-existing edge-truncation mechanism
     already handled elsewhere (0/69 stock intervals ever have this
     date as a standalone gap: 45 edge-masked, 24 window-excluded).
     NIFTY's real history starts 2015-01-09 (before the target date),
     so its gap is genuine and isolated, confirmed via two independent
     live pulls. Added to certification as `CONFIRMED_UPSTREAM_DATA_GAP`
     (same mechanism as 2016-01-01), effectively scoped to NIFTY only.
     **A real bug was caught before accepting the rerun's output**:
     fully documenting away 2015-01-16 for stock files broke the
     edge-truncation contiguity scan (which depended on
     `missing_by_date`, now missing that date), spuriously
     reclassifying the rest of several stocks' edge blocks
     (2015-01-23/27/28/29/30) as scattered interior FAILs. Fixed: the
     contiguity scan now uses the full missing-slot set regardless of
     documentation status, while FAIL-eligibility still only counts
     undocumented slots. Verified clean: synthetic self-tests pass, the
     spurious dates are gone, FAIL count unchanged at 53 (zero effect
     on stocks, as predicted), NIFTY's own FAIL now traces purely to
     `2015-03-16` and `2017-02-21`. Full correction trail (both
     supersessions, the diagnostic result, and the bug) preserved in
     `V2C_CALENDAR_DATABASE_CORRECTION_20260818.md` Rounds 4-5; final
     calendar hash `0437641e41c353d387920e59314f4200847a68dc4f724a8fa23c04e493551237`.
   - **2017-02-21 — RESOLVED. Last of the six originally-identified
     residual dates.** AFFECTED: NIFTY 50 only (confirmed genuinely
     NIFTY-specific in Round 4 - not superseded, that finding was
     correct on the first pass). UNAFFECTED controls: HDFCBANK, INFY.
     `v2c_reacquire_diagnostic_20170221.py`: NIFTY 50 reproduces the
     exact missing 11:15 slot in a fresh independent pull; both
     controls stay fully clean, fresh matching original exactly. Clean
     Type A. Added as `CONFIRMED_UPSTREAM_DATA_GAP`, single-slot
     `GAP_WINDOWS` (11:15 only). **Effect: NIFTY 50's own verdict
     reached `PASS_WITH_DOCUMENTED_EXCEPTION` for the first time**
     (`CERTIFIED_CLEAN`). Stock-level FAIL count unchanged (28/69) -
     this date never touched any stock interval. Calendar hash
     `14a06c0d7622e59b9f9f2c28a26b8413a272e5e96f058a1c6e48d4b00229ec96`.
   - **All six originally-identified residual dates now resolved.**
     2020-04-27 is the sole remaining open item - Case A confirmed
     (item above), classification explicitly deferred to final
     certification review. All 28 remaining stock-level FAILs trace to
     this one date.
   - Full detail: `V2C_CALENDAR_DATABASE_CORRECTION_20260818.md`
     "Round 4" through "Round 10" sections.
9. **YESBANK frozen exclusion rule — DONE.** Implemented exactly as
   specified: symbol-specific, date-bounded (2020-03-04..2020-03-18
   inclusive, YESBANK only — not "exclude YESBANK"), referencing the
   corporate-action audit's `REQUIRES_ADJUSTMENT` disposition, applied
   as a pre-labeling eligibility mask rather than touching the
   bar-level certification verdict.
   `v2c_frozen_data_validity_exclusions.py` (project root) is the
   enforceable registry + `check_event_eligibility()` (crossing-window
   interval-overlap test — an event is ineligible if its feature-
   lookback OR outcome window overlaps the excluded range at all, not
   just its anchor date — verified against 11 boundary cases, all
   pass) + `count_excluded_bars()` (reporting, never silent-drop — real
   check against YESBANK's acquired file finds 10 distinct excluded
   trading dates). Full rule record:
   `V2C_FROZEN_DATA_VALIDITY_EXCLUSIONS_20260818.md`.
   `v2c_certify_15min_dataset.py` now computes a `release_disposition`
   per interval (CERTIFIED_CLEAN / CERTIFIED_WITH_FROZEN_EXCLUSION /
   NOT_CERTIFIED), orthogonal to the bar-level `verdict`. At the time
   this rule was built, YESBANK showed NOT_CERTIFIED (bar-level FAIL
   from 2015-03-16 and 2015-12-22, both then unresolved). **Both have
   since resolved** (items 8-9 above) — YESBANK's bar-level verdict
   reached PASS_WITH_DOCUMENTED_EXCEPTION, and its
   `release_disposition` now correctly reads
   `CERTIFIED_WITH_FROZEN_EXCLUSION`, exactly as this record predicted
   it would once those gaps cleared.
10. **`CONFIRMED_UPSTREAM_EXTRA_BAR_ARTIFACT` locked in, narrow cleaning
    rule built and executed, dataset certified — DONE.**
    `v2c_clean_20200427_extra_bar.py`: for NSE equity data only, on
    2020-04-27, removes the bar-open-15:30 row only in the 28 files
    where certification independently flagged it — line-level removal
    (byte-identity on every kept line, not parse-and-reserialize),
    never touching any other timestamp/date/symbol/field. Both
    realities preserved: raw untouched in `V2C_15MIN_DATA_ACQUIRED/`;
    cleaned derivative in `V2C_15MIN_DATA_CLEANED/` (28 files with
    exactly 1 row removed each, verified; 42 copied byte-identical,
    raw/cleaned hashes asserted equal). Transformation manifest +
    full hash inventory written alongside. Consistency-checked before
    trusting it: INFY's separate 2015-04-24 artifact confirmed
    untouched; YESBANK confirmed entirely untouched (never in the
    affected set). `v2c_certify_15min_dataset.py` given a `--data-dir`
    override so the same certification logic runs unchanged against
    either directory. **Full rerun on the cleaned derivative — proved,
    not assumed: 69/69 intervals + NIFTY 50, 0 FAIL, 0 NOT_CERTIFIED**
    (68 CERTIFIED_CLEAN + 1 CERTIFIED_WITH_FROZEN_EXCLUSION). The
    SHA256-manifest gate itself was fixed to be a real, checked
    condition rather than a hardcoded message — it now genuinely
    verifies 0 FAIL/0 NOT_CERTIFIED before writing anything. Final
    dataset hash manifest generated:
    `V2C_15MIN_DATA_CLEANED/V2C_15MIN_DATASET_HASH_MANIFEST_20260818.json`
    (70 entries). Capstone record written:
    `V2C_DATASET_CERTIFICATION_RECORD_20260818.md` — states explicitly
    that the certified dataset is a deterministically-cleaned
    derivative of preserved raw Kite data, not the raw acquisition
    unchanged.

11. **V2-C epoch freeze — DONE.** Boundaries derived only from time and
    achieved certified coverage — never from performance, event
    counts, class balance, or label outcomes (no label exists yet;
    stated as a standing commitment for after they do too). Achieved
    equity coverage span computed from the cleaned derivative's own
    certification report (real first/last bars, not nominal request
    dates, NIFTY excluded as context-only): **2015-02-02 (ACC) ..
    2023-07-31**, 2096 real trading sessions. Chronological ~70/15/15
    split by trading-session count, both cut points snapped to the
    nearest calendar quarter-end (one consistent convention, not
    mixed month/quarter picked after seeing which lands closer).
    **Frozen**: TRAIN 2015-02-02..2020-12-31 (1459 sessions, 69.61%),
    VALIDATION 2021-01-01..2022-03-31 (308 sessions, 14.69%), HOLDOUT
    2022-04-01..2023-07-31 (329 sessions, 15.70%) — verified exact
    partition, no gap, no overlap. Checked (not assumed): YESBANK's
    frozen exclusion window falls entirely inside TRAIN; the INFY/
    YESBANK data artifacts both fall inside TRAIN; documented-
    exception dates are distributed across all three epochs and stay
    governed by the existing certification machinery regardless.
    Document hash-frozen: `V2C_EPOCH_FREEZE_20260818.md`, SHA256
    `ac89cbe9513b78d223a1a0f1891e97e71bcf8bf0e6c5aa9ea920125f13602c42`
    (sidecar `V2C_EPOCH_FREEZE_20260818.sha256`), reproducibility
    verified.

12. **TRAIN-only outcome-resolver labels — DONE, corrected after an
    independent audit caught a real defect.** Before writing any code:
    found that the existing sandbox resolver
    (`V2C_REAL_DATA_SANDBOX_20260817/ENGINE/v2c_outcome_resolver.py`,
    written 2026-08-17 17:15) was stale — built *after* the correction
    note said its recovery-exit condition was still unfrozen, and
    *before* `P01D_V2C_OUTCOME_RESOLVER_D0_20260818.md` (2026-08-18
    06:08) properly froze a different definition the next morning
    (`close[t0-1]` recovery, `close[t0] - 2*ATR[t0]` stop — not the
    sandbox's SMA20 recovery / entry-price stop). Confirmed with the
    user before writing anything; built `v2c_train_label_generation.py`
    fresh against the D0 spec only, not the sandbox code.

    **First run** (5467 events: 3834 REVERTING, 1127 DETERIORATING,
    506 UNRESOLVED) was sent for independent audit rather than treated
    as final. **The audit caught a real, exact bug**: a fallback
    (`horizon_dates[0] if horizon_dates else t0`) loaded bars starting
    at t0 itself when a symbol's own certified file had no session
    after t0 (ZEEL, last date 2020-09-24; JINDALSTEL, last date
    2015-03-26) — letting ZEEL's own pre-event 13:00 bar resolve
    REVERTING, a genuine causality violation. Fixed: the fallback is
    removed entirely; an empty forward horizon now resolves immediately
    as UNRESOLVED (`NO_FORWARD_DATA`), never loads or walks a bar. Two
    new invariants added, both fail-closed during generation AND
    re-verified after as explicit reported checks:
    `observation_window_start` strictly after `event_t0`;
    `resolution_timestamp` on or after `observation_window_start`. A
    one-point D0 clarification was frozen in the same pass: observation
    begins on the first eligible bar of t0+1, never a bar from t0
    itself.

    **Corrected result: 5467 events — 3833 REVERTING, 1127
    DETERIORATING, 507 UNRESOLVED** — exactly the audit's own predicted
    correction. Both originally-flagged events verified fixed
    (`NO_FORWARD_DATA`, empty window, no resolution). All integrity
    checks pass across the full regenerated set, including the two new
    invariants (0 violations each) and the frozen-window crossing rule
    (5 correctly excluded, including anchors outside YESBANK's window
    whose outcome span reaches into it). The defective first run's
    outputs are preserved, not deleted, at
    `V2C_LABELS_TRAIN_20260818/SUPERSEDED_DEFECTIVE_RUN_20260818/`.
    Corrected audit file:
    `V2C_LABELS_TRAIN_20260818/V2C_TRAIN_LABEL_AUDIT_20260818.csv`.
    Integrity report:
    `V2C_LABELS_TRAIN_20260818/V2C_TRAIN_LABEL_INTEGRITY_REPORT_20260818.json`.
    **No classifier fit. No feature vector built (only `event_z20`,
    the qualifying feature — the full 9-feature vector is a separate,
    later task). VALIDATION and HOLDOUT untouched.**

    **Independently re-audited: PASS/CORRECTED verdict received** —
    5467 events, corrected counts confirmed, all integrity checks
    (including the two new invariants) independently reproduced,
    both originally-flagged events confirmed fixed.

13. **D0-A1 observation-window clarification — FROZEN, hashed as its
    own document.** `P01D_V2C_OUTCOME_RESOLVER_D0-A1_OBSERVATION_WINDOW_CLARIFICATION_20260818.md`,
    SHA256 `6f86756c61fff92001e1ed7b5d7b16ea333d68e17358f06e0a40d44a4cc43fdb`
    (reproducibility verified) — the `t0+1..t0+10` rule (D0 left the
    exact starting bar unspecified) now lives outside a single script's
    docstring, with the full discovery trail (the ZEEL/JINDALSTEL bug)
    recorded as the reason it needed freezing now, not later.
14. **TRAIN label artifacts hash-frozen**:
    `V2C_LABELS_TRAIN_20260818/V2C_TRAIN_LABELS_HASH_MANIFEST_20260818.json`
    — SHA256 for the corrected audit CSV, integrity report, excluded-
    events CSV, and the generator script itself.
15. **TRAIN classifier preregistration — FROZEN**, before any fitting.
    `P01D_V2C_TRAIN_CLASSIFIER_PREREGISTRATION_20260818.md`, SHA256
    `aa36c59b1eca870b0c1d341daaf0fa795778ff81507f0c31e2c1f72126da720c`
    (reproducibility verified). References, does not redefine, the
    original preregistration's already-frozen 9-feature spec and
    classifier spec (hash-verified intact first — case-insensitive
    comparison bug caught and fixed in my own verification script
    before trusting the result). Records the REVERSION/DETERIORATION/
    CENSORED ↔ REVERTING/DETERIORATING/UNRESOLVED terminology bridge
    explicitly, once. **Newly frozen** (could not exist before real
    TRAIN labels): classification threshold = **0.77278226** (the
    resolved REVERSION base rate, `3833/4960`, computed once from the
    corrected TRAIN audit file); a predictive acceptance gate for
    VALIDATION (AUC-ROC >= 0.55, reasoning stated since this is a newly
    chosen number, not inherited from an earlier freeze). The economic
    gate is explicitly NOT specified yet — declined to invent a
    plausible-sounding profitability bar without the real fee model
    integrated; a dedicated addendum is named as a precondition before
    HOLDOUT can ever open. Also flags, not hides, that the remaining 8
    of 9 features (everything but `event_z20`) have not been built for
    the labeled events yet — a separate, not-yet-started engineering
    step, required before this classifier can actually be fit.

16. **Economic-gate addendum (first version) — SUPERSEDED, not deleted.**
    `P01D_V2C_ECONOMIC_GATE_ADDENDUM_20260818.md` (SHA256
    `715f53365ed0ba5819ced24dd32dca4eb276b9a27b127074a51488a52d40b647`)
    was reviewed and found **not ready to govern VALIDATION** — an
    independent audit found six real defects (selection bias from
    excluding UNRESOLVED events using future information; wrong,
    over-optimistic exit pricing that ignored D0's gap logic; no
    intraday/delivery distinction; wrong brokerage history — flat
    min(₹20,0.03%) instead of Zerodha's real zero-delivery-brokerage-
    from-2015-12-01 regime change; missing DP charges; unsourced
    VALIDATION-era rates). Preserved unmodified at
    `SUPERSEDED_ECONOMIC_GATE_ADDENDUM_20260818/` (original doc, sidecar,
    and the defective `v2c_train_era_costs.py`/tests) — see item 17 for
    the corrected replacement.
17. **Economic-gate addendum A1 — FROZEN, supersedes item 16 in full.**
    `P01D_V2C_ECONOMIC_GATE_ADDENDUM_A1_20260818.md`, SHA256
    `506840b20540c904d4fd633bcfb5e5f5cf5036933dc37ab7284ed9d31cb8e70f`
    (reproducibility verified). All six defects corrected: (1) prices
    every classifier-flagged event with an observable `t0+1` entry bar
    — REVERTING + DETERIORATING + UNRESOLVED alike — under a new frozen
    liquidation rule for UNRESOLVED (exit at the final eligible bar's
    close); only structural `NO_FORWARD_DATA` abstains are excluded;
    (2) exit price now reads the frozen audit CSV's `resolution_price`
    verbatim (D0's own gap-open/ordinary-close/stop-level logic, never
    re-derived); (3) trade type (INTRADAY vs DELIVERY) is derived from
    entry/exit dates, with differentiated STT/stamp duty; (4) corrected
    Zerodha brokerage history (₹0 delivery from 2015-12-01, confirmed
    via two independently fetched Zerodha Z-Connect announcements; the
    lower of 0.1%/₹20 before); (5) DP charges disclosed and bounded
    (₹15.34 upper bound, excluded from `total`, Gate E3 now requires the
    profitability margin to survive that bound); (6) VALIDATION-era fee
    gap restated, unresolved, still blocking. Corrected cost model at
    `ENGINE/v2c_train_era_costs.py` (SHA256
    `fd165b1051fbf2f06170097aa92439b82a9b9d1384cb4dfbad6efb7bd8607fdc`,
    19/19 tests passing) now exposes separate
    `train_era_delivery_round_trip()`/`train_era_intraday_round_trip()`.
    **Verified against real TRAIN data as a diagnostic** (no gate
    evaluated, VALIDATION untouched):
    `v2c_economic_trade_pricing.py` priced 5465/5467 real TRAIN events
    (2 structural abstains) and found **44.4% would be INTRADAY trades**
    (2425 of 5465) — proving the first version's blanket delivery
    assumption was wrong for nearly half the population, independently
    reproduced twice with identical counts. Same three ordered gates
    (E1 sample size, E2 quarterly diversity, E3 profitability) retained,
    with E3's threshold reasoning recomputed from the corrected,
    real-data-verified cost figures.
18. **TRAIN feature construction — BUILT, PENDING INDEPENDENT RE-AUDIT.
    Classifier fitting NOT yet authorized.**
    `P01D_V2C_TRAIN_FEATURE_INTEGRITY_SUMMARY_20260818.md`, SHA256
    `ba7f90c4a4ce60e0e311c646153d6b93d13fc4ec04ce69e5b8ac50ee189ae966`
    (reproducibility verified). Built the remaining 8 of 9 frozen
    features for exactly the 5467 frozen TRAIN events, via
    `v2c_train_feature_construction.py` (imports
    `v2c_train_label_generation.py` directly — reuses its exact
    `build_daily_series`/`compute_atr14`/`compute_event_z20` functions
    rather than reimplementing them, so label/feature alignment cannot
    silently drift). Output hash-frozen:
    `V2C_FEATURES_TRAIN_20260818/V2C_TRAIN_FEATURES_HASH_MANIFEST_20260818.json`
    (SHA256 `4168f41d792cee80d462e92d090adb31d0810a10167ec60bb6d564f2c03b31c5`).
    **Every requested integrity dimension checked**: row counts (5467
    in, 5467 out, 1:1), exact event-key joins (output/audit key sets
    identical), no duplicates, causal timing (`event_z20` recomputed
    from the identical daily series and matched against the frozen
    value with 0 mismatches across all 5467; `atr14_pct`'s
    prior-completed-session ATR14 independently confirmed distinct
    from the resolver's t0-inclusive `atr14_t0` — differs in 5433/5467,
    proving it is a genuinely separate, causally-correct computation,
    not an accidental look-ahead duplicate), NIFTY alignment (0
    date-not-found / insufficient-history / missing-close events across
    all 5467), missing/non-finite abstentions (0 — the pipeline
    supports 9 distinct abstain reason codes, none fired), frozen-window
    enforcement (`check_event_eligibility` re-run per event on the
    actual narrower feature-only lookback window, 0 violations), and
    feature-order/schema compliance (CSV columns 5-13 asserted
    programmatically equal to the frozen 9-feature order). Hand-verified
    against the CSV's first row as an additional spot check. **No
    classifier fit in this step, per explicit instruction — feature
    construction and fitting are separate steps.** Independently
    re-audited 2026-08-18: **PASS / FROZEN**, with one non-blocking
    hardening item (dataset-manifest verification was missing from the
    generator; fixed same-session, see the governance-verdict section
    above).
19. **TRAIN classifier — FITTED, authorized by the feature-integrity
    PASS verdict.** `v2c_train_classifier_fit.py` (project root,
    self-contained — does not import from `V2C_REAL_DATA_SANDBOX_20260817/`,
    reimplements the frozen classifier spec directly to avoid any
    provenance question). Fail-closed verified the classifier
    preregistration, both hash manifests, and every file they list
    before touching any data. Fit population: **4960 resolved TRAIN
    events** (3833 REVERTING + 1127 DETERIORATING, counts asserted
    against the frozen label set, not merely observed), 507 UNRESOLVED
    correctly excluded from fitting. Used **only the frozen 9-column
    feature order**, diagnostic columns never touched. Independently
    recomputed the resolved-TRAIN REVERSION base rate as a cross-check
    (`0.77278226`) against the already-frozen threshold — matched to
    within 5e-9 — and used the **frozen threshold value itself**, never
    a recomputed substitute. Single L2-penalized logistic regression,
    exact frozen hyperparameters
    (`penalty=l2, C=1.0, solver=lbfgs, fit_intercept=True,
    class_weight=None, max_iter=1000`), `StandardScaler` fit on the
    resolved TRAIN population only. Output:
    `V2C_CLASSIFIER_TRAIN_20260818/V2C_TRAIN_CLASSIFIER_FIT_20260818.json`
    (human-readable scaler mean/scale + logistic coefficients/intercept
    — no pickle, fully inspectable) and
    `V2C_TRAIN_CLASSIFIER_FIT_REPORT_20260818.json`. Hash-frozen via
    `V2C_TRAIN_CLASSIFIER_HASH_MANIFEST_20260818.json` (SHA256
    `02f819c1035146e61565b0221361a86f19ac61bc31290f9d132ae49cb6645dae`).
    A **TRAIN-only diagnostic AUC of 0.7234** is reported for
    description only — explicitly labeled `NOT_A_GATE` in the fit
    report; it is fit-population performance, not the predictive gate
    (VALIDATION-only, AUC-ROC>=0.55, still unevaluated). VALIDATION and
    HOLDOUT were never read.
20. **Economic-gate addendum A2 — PASS / FROZEN (final), supersedes
    A1's three specification gaps, closes A1 §6's VALIDATION-era fee
    gap, and corrects a provenance erratum found in A2's own first
    re-audit.**
    `P01D_V2C_ECONOMIC_GATE_ADDENDUM_A2_20260818.md`, SHA256
    `d8fff2f5d2a9c8f7da80c4136513489f42f595bebbe7f766ba9eb78cc441d97e`
    (reproducibility verified); A1 preserved unmodified at
    `SUPERSEDED_ECONOMIC_GATE_ADDENDUM_A1_20260818/`. Fixes: (1) sizing
    rule refrozen (`floor(100000/entry_price)`, `UNTRADEABLE_AT_NOTIONAL`
    named for the degenerate case, return denominator defined as actual
    deployed capital); (2) DP-charge bound set to a **deliberately
    adverse Rs 20.00 stress amount** (revised again after the first
    re-audit found the earlier Rs 15.93 "exact" figure unsupported;
    Rs 20 exceeds the actually-documented Rs 15.34 BTST-era charge,
    sourced to Zerodha's own BTST-settlement support article —
    independently fetched and confirmed this session, not merely
    trusted) with the per-scrip-per-day aggregation rule stated
    explicitly; (3) Gate E3 restated as four individually-required
    checks (mean return + win rate, each DP-excluded and DP-stressed).
    Hardened `v2c_economic_trade_pricing.py` with dataset-manifest
    verification (reproduced the identical 5465-event breakdown).
    **Closed A1 §6**: sourced the VALIDATION-era NSE exchange
    transaction charge from circular NSE/FA/46730 (2020-12-18, PDF read
    directly) — 0.00345% effective 2021-01-01, a real 6.15% hike. The
    first re-audit found the rationale wrongly implied V2-C's own
    Rs 100,000 simulated turnover determined the applicable NSE volume
    tier (the circular's slabs are keyed to the *trading member's*
    aggregate turnover, not one client's position); corrected to instead
    ground the 0.00345% client rate in Zerodha's own 2023-04-03
    client-facing notice (independently fetched and confirmed this
    session: it names 0.00345% as the exact pre-2023-04-01 intraday/
    delivery rate). V2-C's applicability still holds via point-in-time
    NIFTY 50 membership excluding it from the circular's separate
    concessional-rate carve-out. New module
    `ENGINE/v2c_validation_era_costs.py`, fail-closed to
    2021-01-01..2022-03-31, 9/9 tests passing. No VALIDATION event,
    feature, prediction, or price was read at any point in this work.
    Full submission hash-frozen:
    `V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json` (SHA256
    `bef8b43695b8206ff78651e20436455b2982694d870e3c7f55beb8926535cc01`,
    all 6 listed files independently re-verified against it with zero
    mismatches).
21. **VALIDATION one-shot execution harness (v1) — BUILT, TESTED ON
    SYNTHETIC FIXTURES, HASH-FROZEN. NEVER RUN AGAINST REAL VALIDATION
    DATA. Superseded by v2 — see item 22 and the harness re-audit
    governance verdict above; this v1 record is kept for the trail,
    not as the current state.**
    Per explicit instruction ("Hold, do not open yet... After that
    harness passes synthetic and fail-closed tests and is hash-frozen,
    explicitly authorize the irreversible VALIDATION run"),
    `v2c_validation_harness.py` implements all six required stages as
    one atomic pipeline (never staged/incremental like TRAIN's, since
    VALIDATION is sealed - staged inspection would itself be a
    look-ahead exposure): (1) fail-closed verification of every frozen
    input (epoch freeze, calendar, certified dataset manifest, TRAIN
    classifier fit + manifest, economic-gate A2 + manifest); (2)
    VALIDATION event discovery + D0/D0-A1 labeling, reusing
    `v2c_train_label_generation`'s `build_daily_series`/`compute_atr14`/
    `compute_event_z20`/`resolve_bar` verbatim rather than
    reimplementing them; (3) causal VALIDATION feature construction,
    reusing `v2c_train_feature_construction`'s primitives the same way;
    (4) scoring via the frozen TRAIN scaler/model/threshold - a pure
    NumPy reimplementation independently verified **bit-for-bit against
    sklearn on real (already-open TRAIN, never VALIDATION) data**: AUC
    reproduced to `0.7233893954591784`/`...85` (machine-precision
    agreement) against the classifier fit report's own recorded value;
    (5) trade pricing under A2's sizing rule (`floor`,
    `UNTRADEABLE_AT_NOTIONAL` named, deployed-capital denominator) and
    D0-exact exit pricing, costed via `ENGINE.v2c_validation_era_costs`;
    (6) predictive gate (AUC-ROC>=0.55) then, only if it passes,
    economic gates E1/E2/E3 (E3's all-four-checks rule from A2 §3),
    evaluated exactly once; (7) an immutable, hash-manifested audit
    bundle, written once - `write_audit_bundle()` refuses to overwrite
    an existing run directory.
    **Code-level authorization gate, not just a convention**:
    `run_validation_harness()` refuses to execute if
    `Path.cwd().resolve() == REAL_PROJECT_ROOT` (a hardcoded absolute
    path) unless called with `authorized=True`; the CLI additionally
    requires an exact `--confirm=AUTHORIZE_IRREVERSIBLE_VALIDATION_RUN_20260818`
    token. **32 tests, all passing** — 15 synthetic-fixture tests
    (`TESTS/test_v2c_validation_harness_synthetic.py`, a fully
    synthetic 3-security/90-day market with engineered REVERTING/
    DETERIORATING/UNRESOLVED outcomes, entirely `chdir`-isolated from
    real data) covering event discovery, causal features, scoring,
    pricing, and bundle-writing end-to-end, plus isolated unit tests
    for every E1/E2/E3/predictive-gate boundary condition; 17
    fail-closed tests (`TESTS/test_v2c_validation_harness_failclosed.py`)
    each corrupting exactly one frozen input (or withholding
    authorization) and asserting refusal - epoch freeze, calendar,
    dataset manifest (both the manifest's own hash and a certified
    data file altered after freeze), classifier fit + manifest, A2 doc
    + manifest, the CLI confirm token, and immutable-bundle
    overwrite protection. A genuine, documented finding from building
    the synthetic market: the frozen `event_z20` rolling-20-day
    z-score formula produces secondary qualifying events in the ~20
    sessions following any sharp price discontinuity (a real property
    of the formula, not a fixture defect) - noted for future reference
    when real VALIDATION results are eventually examined. Hash-frozen at
    the time (SHA256 `bbcbdb60cbaf26da0eb75b199b2ef1df8d894aba208f0758f29bd7cf0d4fc66e`
    for the v1 manifest) - **note**: the manifest JSON itself was
    overwritten in place when refreezing v2 rather than preserved
    separately (a minor process gap, disclosed rather than hidden); the
    three actual v1 SOURCE FILES it covered ARE preserved byte-identical
    in `SUPERSEDED_VALIDATION_HARNESS_V1_20260818/` and independently
    re-verified against their originally-recorded individual hashes
    (`v2c_validation_harness.py` SHA256 `22bdf06b84c43c395f3955e7d17f40e9375f7afbe8724367e4b202237ca1e1aa`,
    both test files likewise) - the actual evidentiary content is
    intact, only the manifest wrapper's exact bytes were not.

22. **VALIDATION one-shot execution harness (v2) — all six re-audit
    defects corrected, 40/40 tests passing, HASH-FROZEN. STILL NEVER RUN
    AGAINST REAL VALIDATION DATA.** See the "Formal governance verdict —
    harness re-audit" section above for the full defect list and fixes.
    Current, authoritative hash:
    `V2C_VALIDATION_HARNESS_HASH_MANIFEST_20260818.json` (SHA256
    `5f33c8e3b468a21feacb74569626d6286e0730000bcf9376a1af3641c92f2de6`),
    covering `v2c_validation_harness.py` (SHA256
    `f5af922bc75c77654b6906faf2cc8839dcb9ee2bd969141f5207ab2cbc1b06aa`),
    `TESTS/test_v2c_validation_harness_synthetic.py`, and
    `TESTS/test_v2c_validation_harness_failclosed.py`. Submitted for
    re-audit. Authorization token still not entered anywhere.

**Certification, epoch freeze, TRAIN-only label generation (independently
re-audited, PASS/CORRECTED), the TRAIN classifier preregistration, TRAIN
feature construction (independently re-audited, PASS/FROZEN), the TRAIN
classifier fit (independently re-audited, PASS/FROZEN), economic-gate
addendum A2 (independently re-audited twice — a specification revision
then a provenance erratum — now PASS/FROZEN), and the VALIDATION one-shot
execution harness (built, synthetic + fail-closed tested, hash-frozen,
never run against real data) are all complete. The economic prerequisite
for VALIDATION is now cleared and the execution mechanism itself is built
and tested. VALIDATION and HOLDOUT remain NOT
authorized** — this status does not open them, and opening VALIDATION
remains a separate, deliberate act requiring explicit authorization of
the harness's own code-level gate; the architecture's next step is:
explicit authorization → the one-shot VALIDATION run → immutable audit
bundle → open HOLDOUT once, if warranted.
Pillar I and Pillar II remain closed, untouched. Gate document:
`P01D_V2C_DATASET_CERTIFICATION_GATE_20260818.md`.

## What is explicitly not being opened right now, and why

New universe, higher capital, modified Pillar II costs, an alternative
VWAP-exit variant, a Pillar III/IV, router work, shadow tournament, or
`request_entry()` integration — any of these immediately after two
negative results is exactly the hypothesis-proliferation the RRME
architecture exists to prevent. Each remains a legitimate *future*
question, opened only as its own fresh preregistration, not as a
same-night pivot away from an inconvenient result.

## Release boundary

Pillar I/II: no code written beyond what's already sealed in the two
closure manifests. V2-C: historical data acquisition, calendar
correction, dataset certification (0 FAIL / 0 NOT_CERTIFIED on the
cleaned derivative), corporate-action audit, YESBANK's frozen
exclusion rule, the 2020-04-27 extra-bar cleaning rule, epoch freeze,
TRAIN-only resolver label generation (independently re-audited,
PASS/CORRECTED), the D0-A1 observation-window clarification, the TRAIN
classifier preregistration (features/threshold/model family/predictive
gate), TRAIN feature construction (the remaining 8 of 9 frozen
features, all 5467 TRAIN events, full integrity report, independently
re-audited PASS/FROZEN), the TRAIN classifier fit (4960 resolved
events, frozen 9-feature order, frozen threshold, no VALIDATION/HOLDOUT
access, independently re-audited PASS/FROZEN), and the economic-gate
addendum A2, now **PASS/FROZEN (final)** (execution assumptions
including the refrozen sizing rule, corrected order-type-dependent
TRAIN-era and VALIDATION-era fee models, the DP-charge stress amount
sourced and its aggregation rule stated, all three VALIDATION
acceptance-gate thresholds with E3 restated as four individual checks,
and the VALIDATION-era NSE exchange-charge gap closed via a
directly-sourced circular plus Zerodha's own client-facing rate notice
— superseding a first version six real defects were found in, then
A1's three specification gaps, then a provenance erratum in A2 itself,
all three prior versions preserved not deleted), and the VALIDATION
one-shot execution harness — v1 failed its own re-audit (six
audit/output-layer defects), all corrected in v2 (hash-frozen, 40/40
synthetic + fail-closed tests passing, up from 32/32, v1 preserved not
deleted, never run against real data) — are all
**complete** — see items 1-22 above and their linked documents for
authoritative detail.
The classifier is fit; its TRAIN-only diagnostic AUC (0.7234) is
explicitly not a gate. **The economic prerequisite for VALIDATION is
now cleared, and the execution mechanism to run it exactly once is
built, corrected after re-audit, and tested** — every item that was
blocking VALIDATION (TRAIN labels, TRAIN features, the TRAIN classifier
fit, the economic-gate
addendum) has independently passed re-audit, and the harness itself
passed its own synthetic + fail-closed test suite. **VALIDATION
opening is on HOLD by explicit choice (Option 2)** pending a separate,
deliberate authorization of the harness's real run - the predictive
gate and economic gate remain unevaluated, since VALIDATION has not
been opened. VALIDATION and HOLDOUT remain untouched throughout.
`LIVE_TRADING_ENABLED` unaffected throughout. P02 and V11 bridge
threads are untouched by anything in this record.

**[SUPERSEDED - see "R1 candidate proposal" below and "V2-C — final
disposition" earlier in this document for the current, authoritative
state. This "Release boundary" section is preserved unedited as its
own point-in-time (pre-VALIDATION) record.]**

## R1 candidate proposal received — DRAFT CONCEPT ONLY, not a program (2026-08-19)

Following R0's closure (all three Stage 1 tracks CLOSED/FAIL, see
"V2-C — final disposition" above), a **draft R1 candidate concept
proposal** was produced and reviewed. It is **not** a frozen research
program, **not** D0, and carries **no authorization of any kind** -
this section exists purely to record that it has been seen and to
prevent a future session from mistaking it for something already open.

**Location** (external to this project's frozen tree, referenced not
copied):
`C:\Users\Dishan\Documents\Codex\2026-08-18\referenced-chatgpt-conversation-this-is-an\P01D_RRME_R1_PROPOSAL_20260819.md`

**Proposed identity**: `P01D_R1_PREOPEN_PRICE_DISCOVERY_V1`.
**Proposed hypothesis**: an unusually large overnight gap, supported by
NSE's pre-open call-auction price discovery, participation, and
directional imbalance, may continue briefly after the regular market
opens - provided the position is actually executable at the intended
size and clears all observed and stressed costs. A genuinely different
mechanism from all three closed R0 tracks (not continuous-session
trend, not intraday reversion, not the exhaustion classifier), single
independent track (not three), deterministic rule in V1 (no ML),
executable-price/impact economics gated before any predictive testing,
prospective-only TRAIN/VALIDATION/HOLDOUT epochs preferred (R0's V2-C
HOLDOUT explicitly excluded from reuse "by governance choice even
though it was never opened").

**Its own stated status is the authoritative one, verified by reading
the full document, not just its summary**:
```
R0:                 FROZEN / CLOSED
R1:                 DRAFT PROPOSAL — AWAITING OWNER DECISION
Current authority:  RESEARCH_ONLY / ZERO DECISION AUTHORITY
Next safe action
  if approved:       write the outcome-blind data-feasibility and
                      license memo (§12 item 2) - nothing more
Not authorized:      data acquisition, backtest, model fitting,
                      VALIDATION, HOLDOUT, shadow routing,
                      request_entry(), or broker mutation
```

**One refinement identified and worth carrying into R1-G2 (the
eventual D0), when and if that gate is reached**: the proposal's own
§2 already discloses what R0's aggregate outcomes taught R1's design
and already restricts R1 from mining R0's event-level artifacts or
opening R0's V2-C HOLDOUT - but a later external review correctly
pointed out that R1's D0 cannot honestly claim "no performance number
was seen before D0," since R0's aggregate results are now known. The
precise, honest framing to freeze in R1-G2 is: *no R1 performance data
examined before D0; prior R0 aggregate results disclosed as known
exposure; no R0 event-level mining; R0 HOLDOUT excluded.* Not yet
acted on - simply recorded here so it isn't lost before R1-G2 exists.

**The owner's six required decisions (proposal §12) remain fully
open** - mechanism approval, data-feasibility budget/license approval,
prospective-vs-historical data boundary, symmetric-vs-single direction,
research notional/risk cap, and patience for a possibly months-long
prospective accrual that may end INCONCLUSIVE. **Approving the proposal
"conceptually" would authorize only item 2 - the data-feasibility and
license memo - nothing past that.** No such approval has been given as
of this record. R0 remains FROZEN/CLOSED and untouched by any of this.

## R1-B — second, competing candidate concept drafted (2026-08-19)

The owner asked whether R0's three closed tracks could instead be
**combined** (a router/ensemble over Pillar I, Pillar II and V2-C) via
Stage 2's shadow observer. **This was declined, in full, citing R0's own
pre-committed rule** (`P01D_RRME_R0_ARCHITECTURE_FREEZE_20260818.md`:
*"A failed Pillar is not rescued because architecture time was already
spent on it. If none of the three pass, the answer is to stop - not to
invent a fourth signal or optimize a combination of three failures into
a success."*) and the R1 proposal's own §10 item 5, which independently
lists this exact combination as explicitly rejected.

The legitimate kernel of the idea - V2-C's classifier *methodology*
proved it can find real signal (AUC 0.6948) even where the specific rule
tested around it didn't clear its economic hurdle - was carried forward
honestly as a **second, competing, freshly-preregistered candidate
concept**, not a transplant: `P01D_RRME_R1B_PROPOSAL_20260819.md`
(project root), `P01D_R1B_STATE_INFORMED_EXHAUSTION_V1`. Applies the
same classifier-around-an-extreme-move methodology to a deliberately
different event population - extreme *upside* momentum-selection scores
in V11's own universe, not V2-C's extreme-downside-dislocation domain -
motivated by real, already-disclosed evidence (the 2026-08-16 fold-
attribution study: fold 2's -22% loss traced to the most extreme
momentum picks, all reversing sharply the month after selection; the
Fibonacci score-cap experiment showed a hard threshold doesn't fix it,
leaving room for a richer classifier). Carries the same known-exposure
disclosure discipline as R1, the same G0-G6 gate sequence, the same
"at least as strict as 0.30%" economic hurdle, and is explicit that even
a full PASS would still require its own new P02/V11 candidate-version
process (P02 is frozen, never edited directly) before touching anything
currently running.

**Status: R1 and R1-B are two independent draft concepts, both
`AWAITING OWNER DECISION`, neither authorized past a possible
feasibility check. R0 remains FROZEN/CLOSED. No combination of the three
closed R0 tracks was performed, will be performed, or is being proposed
anywhere in this record.**

## R1-C — third candidate concept: Three-Pillar Composite State Machine (2026-08-19)

The owner brought a more sophisticated combination proposal (sourced
from an external review) - a sequential state machine (V2-C as slow
context filter → Pillar II as setup detector → Pillar I as timing
confirmation), explicitly not a vote, reusing R0's three frozen
components verbatim. **Independently re-evaluated rather than
rubber-stamped.**

**Verdict: the sequential-role SHAPE is genuinely sound** - it matches
what R0's own architects originally envisioned as the eventually-correct
combination design ("V2-C may veto a Pillar-II event only inside its own
validated domain"). **But it cannot be R0 Stage 3 Router/Combination
Research** - R0's frozen precondition ("if and only if all three
eventually validate independently") is not satisfied; none of the three
did. Reclassified instead as a **third, independent R1-track hypothesis**
- on the same footing as R1 and R1-B, not a Stage 3 continuation -
drafted as `P01D_RRME_R1C_PROPOSAL_20260819.md` (project root),
`P01D_R1_THREE_PILLAR_COMPOSITE_STATE_MACHINE_V1`.

**One hardening applied before drafting, non-negotiable**: the reviewed
version's TRAIN-sandbox economic gates (net hurdle, stress survival,
stability, concentration) read as validation-grade when run on data
already used to construct each component's assigned role - a real risk,
since the composite's roles were hand-fitted with full knowledge of each
component's own specific failure mode (V2-C's mean-return miss, Pillar
II's 84% VWAP-reclaim rate, Pillar I's post-setup confirmation logic). R1-C's
§5 makes this structural, not just disclosed: everything computed on
the already-exposed 2015-2023/2026 data carries **zero** evidentiary
weight toward PASS, permanently - a FAIL there is informative and closes
the idea; a PASS there is expected and uninformative. Only a genuinely
fresh, prospective, post-freeze accrual period may ever report PASS.
Real risks named explicitly and not glossed over: sample starvation
(three ordered conditions is multiplicative, not additive, against an
already-small V2-C VALIDATION base rate), confirmation-delay eroding the
edge, and a real cross-pipeline harmonization gate (1-minute Pillar I/II
data vs. V2-C's 15-minute/15:00-clock pipeline) that must reproduce
V2-C's frozen calculations exactly before any composite logic is trusted.

**Recommendation against "supersede but not delete"**: all three
candidate concepts (R1, R1-B, R1-C) are kept as independent, undecided
drafts - none costs anything to keep on file, none has started real
work, all are pre-G0/zero-authority. The owner chooses among them, or
sequences several, or rejects all.

**Status: R1, R1-B, and R1-C are three independent draft concepts, all
`AWAITING OWNER DECISION`. R0 remains FROZEN/CLOSED. Stage 3 combination
research remains correctly BLOCKED - nothing here opens it or argues
around its precondition.**

## R1-C refined, same day (2026-08-19) — two accepted corrections, one self-correction

A further external review of the R1-C draft above was independently
re-evaluated rather than accepted wholesale. Two refinements were
genuine improvements and were adopted into
`P01D_RRME_R1C_PROPOSAL_20260819.md`:

1. **The sandbox's vocabulary no longer contains the word `PASS` at
   all.** Replaced with two named, mutually exclusive outputs:
   `TRAIN_FEASIBILITY_NO_GO` (a real, informative negative - closes R1-C
   V1) and `ELIGIBLE_FOR_PROSPECTIVE_ACCRUAL` (mechanically sound,
   non-trivial event count - explicitly **not** evidence of edge).
   Every sandbox artifact must now carry a literal marking string:
   `EXPOSED R1-C TRAIN DIAGNOSTIC — ZERO CONFIRMATORY WEIGHT — NOT A
   RESEARCH VERDICT`. Any change to the frozen rule after seeing a
   sandbox result creates a new identity (`R1-C V2`) requiring its own
   fresh, untouched accrual period - the same "new identity, not a
   retune" discipline already applied everywhere else in this project.
2. **G0 split into G0a (concept registration) and G0b (known-exposure
   declaration)** - making explicit, as its own named gate, that every
   one of R1-C's three role assignments was chosen with full knowledge
   of each component's specific already-observed failure.

One self-correction, disclosed rather than silently fixed: an earlier
verbal characterization (in chat, not in the frozen document) described
R1-C's shape as matching what R0's architects originally envisioned for
combination research. That overstated it - R0's own sketch was
contingent on independent validation and never envisioned Pillar I
confirming a setup from an already-failed Pillar II. R1-C's own §1 now
states this precisely: a genuinely new, result-informed architecture,
not a resumption of R0's original combination idea.

A verified flowchart of the full R1-C sequence (R0's frozen tracks →
reusable engineering primitives only → the composite state machine →
the TRAIN-sandbox firewall → prospective VALIDATION → sealed R1-C
HOLDOUT → downstream) was published for reference; the TRAIN-sandbox
side of that diagram structurally cannot reach a node containing the
word PASS.

**Status unchanged in substance: R1, R1-B, R1-C all `AWAITING OWNER
DECISION`, zero authority, R0 remains FROZEN/CLOSED, its HOLDOUT remains
sealed.**

## Foundation Calibration F0 — R1/R1-B/R1-C paused, platform diagnosis first (2026-08-19)

The owner asked the right meta-question before authorizing any R1
candidate: three well-documented strategy classes (trend, reversion,
exhaustion) all failing here is suspicious - is this the market, or is
it the platform? A first diagnostic pass found no evidence of a
systemic bug and grounded the pattern in published literature. **A
second, independent, adversarial review of that diagnosis then found
two real, confirmed errors in it**, verified directly against primary
sources in this session (not adjudicated by preference):
- V2-C's universe was claimed unrestricted by market cap; it is in fact
  built exclusively from historical point-in-time NIFTY 50 membership
  (`P01D_V2C_ECONOMIC_GATE_ADDENDUM_A2_20260818.md` line 179 - reread and
  confirmed directly).
- A momentum cost-horizon finding cited as Indian evidence is a UK study
  (1988-2003) - confirmed by fetching the paper itself.
- A "momentum decayed 10%->2%" figure traced to a blog-tier source,
  presented with unwarranted precision.

**The honest verdict, adopted without softening: not "the platform is
probably sound" but `NOT PROVEN BROKEN / NOT YET CALIBRATED`.**

Full point-wise strategy drafted as its own document:
`P01D_FOUNDATION_CALIBRATION_F0_20260819.md` (project root). Two
firewalls made explicit: F0 cannot reopen R0's frozen verdicts (a fixed
engine bug applies to future work, never retroactively rescues a closed
track), and F0 authorizes no new candidate - R1/R1-B/R1-C stay paused,
not deleted, until F0 concludes. Five exercises sequenced cheapest-first
(economic-geometry precheck, using real numbers independently
recomputed from `d1_discovery_trades.json` - fees consumed 53-77% of
Pillar I's entire designed risk budget on some candidates; deterministic
engine tests at intraday-minute resolution; positive/negative controls;
NSE data-truth audit; literal Nifty200 Momentum 30 benchmark replication
in a second engine for genuine two-engine agreement).

**Status: F0 is PROPOSED, awaiting owner authorization. R1, R1-B, R1-C
PAUSED. R0 remains FROZEN/CLOSED, HOLDOUT sealed, unreachable by any of
this.**

## R0 Post-Closure Live Observation — explicit owner-authorized exception (2026-08-19)

Same day, the owner asked to connect Pillar I/Pillar II/V2-C's already-
frozen, already-CLOSED/FAIL logic to a real live Zerodha feed and
observe behavior for a period. **Clarified first** (two readings offered
- a pure data-truth check that fits inside F0, vs. watching the closed
signals fire live, which bypasses R0's own Stage 2 precondition
"qualified standalone candidates only" and runs outside the F0 pause) -
**owner explicitly chose the latter, informed of exactly what it
bypasses.**

Scoped and recorded as a named, one-off exception, not a redefinition of
Stage 2 for anything else:
`P01D_R0_POST_CLOSURE_LIVE_OBSERVATION_20260819.md` (project root).
Read-only throughout - zero broker writes, zero orders, zero
`request_entry()`, `LIVE_TRADING_ENABLED` remains False structurally.
Runs all 5 Pillar I candidates, all 5 Pillar II candidates, and V2-C's
one frozen classifier, unmodified, against real live data, logging every
signal/no-signal decision plus hypothetical (never real) paper P&L to an
Evidence Ledger. **Cannot change R0's verdicts, cannot qualify anything,
cannot promote to Stage 3/4 - no PASS state exists for this exercise.**
Credentials remain the owner's own (existing project token-exchange
pattern reused); connectivity reuses the existing read-only live-scan
pattern already proven for P02.

**Status: scoping document drafted, awaiting owner confirmation on
duration/scope before the observer script itself is written. F0 remains
separately PROPOSED and unaffected. R0 remains FROZEN/CLOSED.**

## R1-C Gate G1 certified; G2 (D0) drafted for review (2026-08-19)

Owner confirmed the live observation should be done properly - R1-C's
G1 bridge certification built and frozen first, so the live window can
genuinely count as R1-C's own prospective validation data rather than
another non-confirmatory diagnostic.

`r1c_bridge_1min_to_15min.py` built and certified:
`R1C_G1_BRIDGE_HASH_MANIFEST_20260819.json`. **Real finding, not
anticipated by R1-C's original G1 wording**: no historical overlap
exists between V2-C's certified dataset (through 2023-07-31) and
Pillar I/II's data (2026 onward) - certified via deterministic synthetic
fixtures plus round-trip tests feeding real output directly into V2-C's
real, frozen, unmodified functions instead. 11/11 new tests pass.
Along the way, two tests in the already-frozen V2-C harness suite were
found stale for a good reason (Attempt 2's own real bundle now
permanently exists, contradicting a "no marker exists yet" assertion
written before Attempt 2 ran) - fixed the same way the equivalent
Attempt-1 staleness was fixed earlier, refrozen as V4-A2 revision 3.
Full suite: **84/84 passing.**

G0a/G0b/G1 marked COMPLETE in `P01D_RRME_R1C_PROPOSAL_20260819.md`. G2
(D0) drafted with concrete proposed numbers, not placeholders:
`P01D_RRME_R1C_D0_DRAFT_20260819.md` - component roles reused verbatim
from already-frozen R0 candidates (V2-C's one classifier; Pillar II's
C3, chosen for using the VWAP-reclaim exit style that motivated this
whole architecture, not for its P&L; Pillar I's C4, R0's own
already-named "best of 5"), plus genuinely new proposed numbers
(REVERSION_PERMITTED window, REVERSION_SETUP window, notional,
concurrency) explicitly flagged as needing the owner's confirmation, not
assumed.

**Status: G1 COMPLETE and certified. G2 (D0) DRAFT, awaiting owner
review/freeze. Nothing runs live yet.**

## D0 frozen; live Pillar I (C4) evaluator built and verified (2026-08-19)

Owner: "Let us go ahead... It's totally frozen." D0 frozen exactly as
proposed, all five items, no amendments -
`P01D_RRME_R1C_D0_DRAFT_20260819.md`, hash-sidecarred.

Composite engine build started. First component:
`r1c_live_pillar1_evaluator.py` +
`R1C_PILLAR1_LIVE_EVALUATOR_HASH_MANIFEST_20260819.json`. Reuses Pillar
I's real C4 logic (`p01d_pillar1_intraday_v1_d1.py`, in the separate
sandbox) via hash-verified import - refuses to load if that file has
changed since R0's closure. **Two real findings, not guessed, found by
tracing/instrumenting before and during test-writing**:
1. C4's participation check needs 10 PRIOR trading days at the same
   time-of-day slot - a per-session-reset buffer could never satisfy
   this, so the evaluator carries genuine rolling multi-day history
   instead (design corrected before writing a single test).
2. An initial synthetic test fixture (price ramp slope=0.02/min)
   satisfied 3 of C4's 4 AND-conditions but not breakout - diagnosed by
   directly instrumenting each of the four checks, not by guessing;
   root cause was the ramp being shallower than each bar's own
   high-over-close offset. Fixed by raising the slope.

9/9 new tests pass, including one full engineered end-to-end scenario
that genuinely fires a real C4 signal (verified fields: entry/stop/
target/exit/fees/net_pnl) and confirms a flat control symbol does not
fire. **Full suite: 93/93 passing.**

**Status: D0 FROZEN. G1 (bridge) CERTIFIED. Pillar I live evaluator
BUILT + VERIFIED. Remaining: Pillar II (C3) live evaluator (same
pattern), the R1-C state machine wiring on top of all three, then the
real Kite connection (needs the owner's own credential step, per
`P01D_R0_POST_CLOSURE_LIVE_OBSERVATION_20260819.md`). Nothing runs
against real live data yet.**

## Live Pillar II (C3) evaluator built and verified (2026-08-19, same session)

`r1c_live_pillar2_evaluator.py` +
`R1C_PILLAR2_LIVE_EVALUATOR_HASH_MANIFEST_20260819.json`. Same reuse-
via-hash-verified-import discipline as Pillar I's evaluator, reusing
Pillar II's real C3 logic (`p01d_pillar2_intraday_v1_d1.py`) unmodified.
Same rolling multi-day buffer design, for the same underlying reason
(C3's climactic-volume check needs 10 prior same-time-slot trading
days, identical pattern to Pillar I's participation check).

6/6 new tests passed on the first run, including one full engineered
end-to-end scenario (quiet 30-bucket oscillation, then a sharp single-
bucket ~1% crash with a volume spike, NIFTY held flat throughout) that
genuinely fires a correctly-shaped C3 signal - verified fields
including `z <= -2.0`, and confirmed the absence of a `target` field
(Pillar I's dict has one; Pillar II's, using the VWAP_RECLAIM exit
style, does not) - plus confirms a quiet control symbol does not fire.
**Full suite: 99/99 passing.**

**Status: D0 FROZEN. G1 CERTIFIED. Both Pillar I and Pillar II live
evaluators BUILT + VERIFIED. Remaining: the R1-C state machine itself
(wiring V2-C's context filter, Pillar II's setup detector, and Pillar
I's confirmation together per the frozen D0 sequencing rules), then the
real Kite connection (owner's own credentials, not yet done). Nothing
runs against real live data yet.**

## Live V2-C evaluator and the composite state machine built (2026-08-19, same session)

**Live V2-C evaluator**: `r1c_live_v2c_evaluator.py` +
`R1C_V2C_LIVE_EVALUATOR_HASH_MANIFEST_20260819.json`. Reuses V2-C's real
event-detection, feature-construction, and scoring functions directly
against the G1-certified bridge's output. The one copied formula block
(no smaller already-frozen single-event function exists) is verified
byte-for-byte against the real, already-frozen TRAIN features CSV on a
real TRAIN event (ACC, 2015-04-17) - all 9 features match to 1e-9, the
strongest form of drift protection available. 5/5 tests pass, including
one full engineered multi-day dislocation that genuinely detects a
qualifying event and scores it with the real frozen classifier.

**The composite state machine itself**: `r1c_state_machine.py` +
`R1C_STATE_MACHINE_HASH_MANIFEST_20260819.json`. Sequences all three
live evaluators per D0's frozen rule (REVERSION_PERMITTED, 5 sessions ->
REVERSION_SETUP, same session -> confirmation strictly after setup,
same session -> COMPOSITE_ENTRY), recording every decision - grants,
abstentions, expirations, entries - to an Evidence Ledger.

**One genuine interpretive gap in D0, disclosed rather than silently
resolved**: D0 does not say whether an unconfirmed Pillar II setup
consumes the underlying V2-C permission or leaves it available for a
later attempt within the same 5-session window. Implemented with one
explicit, defensible default (a permission survives an unconfirmed
setup; only a real COMPOSITE_ENTRY or window expiry consumes it) -
flagged for the owner's review, not presented as already decided.

9/9 new tests pass, covering the full happy path, out-of-order
rejection (confirmation timestamped before setup must not count),
5-session expiry, the disclosed permission-survival behavior, V2-C
abstain/below-threshold handling, no-overwrite on an already-active
permission, multi-symbol isolation, and correct Evidence Ledger
behavior (one test's own expectation was found wrong, not the state
machine - a genuinely idle day correctly produces no ledger entry,
since the ledger records decisions, not the passage of time).

**Full accumulated R1-C + V2-C suite: 113/113 passing.**

**Status: THE FULL R1-C COMPOSITE ENGINE IS BUILT AND VERIFIED** - G1
bridge, all three live evaluators, and the sequencing state machine, all
hash-frozen with real, independently-verified tests at every layer.
**The only remaining step is the real Kite connection**, which requires
the owner's own credential exchange (never handled by Claude) -
everything on the code side is ready for it. Nothing has run against
real live data yet; `LIVE_TRADING_ENABLED` remains `False` structurally
throughout every module built today.

## Live observer entry point built - Kite connection wired, not yet run for real (2026-08-19, same session)

Owner completed the token-exchange steps themselves (own terminal, own
credentials, never seen by Claude). Two final components built to
actually use that connection:

- **`r1c_live_kite_client.py`** - new, minimal, read-only Kite
  historical-minute-bar client, deliberately separate from the V11
  bridge's own already-frozen read-only client (different phase, no
  need to touch a sealed artifact). Only `instruments()` and
  `historical_data()` - both read-only market-data endpoints incapable
  of submitting anything.
- **`r1c_live_observer.py`** - the actual runnable entry point. Polls
  for new 1-minute bars incrementally (never re-fetches an already-seen
  window), ingests them into all three already-verified live
  evaluators, and runs the composite state machine exactly once per
  trading day (not once per poll - verified directly by counting
  invocations, not inferred from ledger side effects), appending every
  decision to an append-only JSONL Evidence Ledger.

**Read-only, checked twice over**: no write-capable Kite method
(`place_order`/`modify_order`/`cancel_order`/`exit_order`) is called
anywhere in either module - verified both by inspection and by a
dedicated static grep-pattern test in each test file.
`LIVE_TRADING_ENABLED` is never referenced as code anywhere in the
observer, only in prose explaining its own absence - also verified by a
dedicated test, not just claimed.

17/17 new tests pass (8 for the Kite client, 9 for the observer), all
against a fake, duck-typed Kite client - no real network access, no
real credentials, anywhere in the test suite. **Full accumulated R1-C +
V2-C suite: 130/130 passing.**

**Status: THE ENTIRE R1-C PIPELINE IS CODE-COMPLETE, TESTED, AND
HASH-FROZEN, INCLUDING THE LIVE KITE CONNECTION ITSELF.** Nothing has
actually run against real live data yet - that is the owner's own next
action (`python r1c_live_observer.py --once`, in their own terminal,
with `KITE_API_KEY`/`KITE_ACCESS_TOKEN` set), and only produces
meaningful data during real NSE market hours (09:15-15:30 IST) on a
real trading day.

## First real connection made; a real bug found and fixed live; trade-chart tool built (2026-08-19, same session)

Owner ran `r1c_live_observer.py --once` for real - genuine success, real
1-minute bars fetched from Kite, zero warnings. Then ran continuous
mode (no `--once`) and hit a real bug on the SECOND poll:
`TypeError: can't compare offset-naive and offset-aware datetimes`.

**Root cause, diagnosed precisely, not guessed**: real Kite
`historical_data()` responses carry timezone-AWARE (IST) datetimes;
`datetime.now()` is naive; the observer's own `last_fetched_until`
bookkeeping stored an aware value from the first poll, then compared it
against a naive `now` on the second. This is exactly the class of bug
synthetic tests built with naive fixtures throughout could never catch
- disclosed plainly, not glossed over.

**Fixed at the single boundary where broker data enters this project**:
`r1c_live_kite_client.get_minute_bars` now normalizes every timestamp
to a naive IST wall-clock string (converting explicitly through IST,
not merely stripping whatever offset is present - covers a
defensively-tested non-IST-offset case too), and the observer gained
`_ist_now_naive()` - real IST wall-clock time, anchored independent of
the machine's own local timezone setting. 3 new regression tests added,
including a full two-poll integration test through the real
`R1CLiveKiteClient` reproducing the exact original failure. Refrozen as
v2, then v3 adds durable per-symbol 1-minute bar persistence
(`r1c_live_bars/*.csv`) - needed for anything to chart later, since the
evaluators only ever held bars in memory before this.

**Trade chart tool built**: `r1c_trade_chart.py` - reads the durable
Evidence Ledger and bar CSVs, renders one price chart per
`COMPOSITE_ENTRY` with V2-C permission, Pillar II setup, Pillar I entry,
stop, target, and exit all marked. The one thing not already on record
(the exact exit timestamp) is recovered by reusing Pillar I's own real,
frozen `fixed_exit_generic` function again - deterministic, never a
re-derivation of the trade's already-frozen economics. 11/11 tests
pass. A synthetic demo (clearly labeled, not real data) was published
so the owner can see exactly what a real trade will look like once one
fires: [artifact link in chat].

**Full accumulated R1-C + V2-C suite: 146/146 passing.**

**Status: LIVE CONNECTION CONFIRMED WORKING FOR REAL. One real
production bug found and fixed the same session it appeared, with
regression coverage. Bar persistence and trade-chart tooling both built
and tested. Still no COMPOSITE_ENTRY has fired in the real ledger -
awaiting the first real trading session past market close.**

**Fix independently reconfirmed live, not just in tests, same day**:
after stopping the stale process that was still running the pre-fix
code (a real, understandable confusion - the crash traceback printed a
source line that no longer matched the current file, since Python's
traceback formatter reads the file from disk by line number, not what
actually executed) and starting a genuinely fresh process, two
consecutive continuous-mode polls completed cleanly with zero errors.
**The timezone fix is confirmed correct against the real broker, not
only against the regression test suite.**
