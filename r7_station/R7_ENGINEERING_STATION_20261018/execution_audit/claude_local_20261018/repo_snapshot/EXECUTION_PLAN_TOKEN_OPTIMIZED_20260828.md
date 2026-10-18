# Execution Plan: Token-Optimized, Dual-Machine Workflow
## Date: 2026-08-28
## Objective: Maximize work output while conserving tokens for QA and project management

---

## PHILOSOPHY

**Token Conservation Strategy:**
- ✅ **Work Phase:** PC & Razer work independently, produce outputs, NO back-and-forth
- ✅ **Review Phase:** Claude reviews outputs in focused batches (5-10 min sessions)
- ✅ **Gate Phase:** Claude acts as QA manager, not code writer
- ✅ **No Chat Loops:** Each machine gets ONE clear instruction, runs to completion

**Result:** 80% of tokens spent on work output (PC/Razer), 20% spent on Claude review/QA

---

## PHASE STRUCTURE

Each phase follows this pattern:

```
WORK PHASE (Duration: 2-4 hours, Minimal Claude involvement)
├─ PC: Independent task (isolated analysis/computation)
├─ Razer: Independent task (isolated analysis/computation)
└─ Both produce output files (CSV, JSON, MD reports)

CHECKPOINT (Duration: 10-15 min, Focused Claude review)
├─ Claude reads outputs
├─ Validates against acceptance criteria
├─ Creates gate decision (PASS/HOLD/FAIL)
└─ Documents findings (1-2 page summary)

NEXT PHASE STARTS (if PASS)
```

---

## PHASE 1: PREREGISTRATION APPROVAL & FINALIZATION
**Duration:** 1 hour (minimal Claude, mostly decision-making)
**Allocation:** 50 tokens (reading, 5 min review)

### Step 1: Owner Signature (You)
- [ ] Read `ITEM_3B_PREREGISTRATION_FROZEN_20260828.md` (15 min)
- [ ] Approve or request changes
- [ ] Sign document with date/timestamp

### Step 2: Claude Finalization (5 min, ~30 tokens)
- Git commit with preregistration hash
- Update MEMORY.md with approval status
- Create phase gate summary

**Output:** Frozen preregistration hash in Git, go/no-go decision

---

## PHASE 2A: MODEL HARNESS BUILD (DESKTOP ONLY)
**Duration:** 3-4 hours (completely independent)
**Allocation:** 0 tokens during execution, 20 tokens review

### Desktop Task: Build Model 0/1 Harnesses
**Instructions (ONE-TIME, no back-and-forth):**

```
Build and test Model 0/1 training harnesses based on frozen preregistration.

INPUT FILES:
- daily_multi_timescale_fusion_panel_20260825.csv
- p1_p2_state_decomposition_20260825.csv
- ITEM_3B_PREREGISTRATION_FROZEN_20260828.md

OUTPUT REQUIREMENTS (CRITICAL):
1. model_0_ridge_regression.py (Model 0 harness)
   - Ridge regression, λ=0.01
   - Train/val/holdout split per frozen dates
   - Save metrics (Rank IC, return, turnover, drawdown)
   - Output: model_0_metrics.json

2. model_1_xgboost.py (Model 1 harness)
   - XGBoost with frozen hyperparameters
   - Same splits, same metrics
   - Output: model_1_metrics.json

3. test_harness.py (validation)
   - Run both models on 8-symbol sample
   - Verify no data leakage
   - Check for NaN/inf values
   - Output: harness_validation_report.md

ACCEPTANCE CRITERIA:
- No errors during harness execution
- Metrics files contain valid JSON (not NaN/inf)
- Cross-validation fold consistency check PASS
- Validation report confirms no leakage

TIMING: 3-4 hours (run independently, save all outputs to project root)
```

### Claude Review Checkpoint (20 tokens, 10 min)
After desktop completes:
1. Read harness_validation_report.md
2. Verify metrics.json files are valid
3. Check for any warnings/errors in execution
4. **Decision:** PASS → proceed to Phase 2B, or HOLD → debug

**Output:** Validated harness code in Git, metrics baseline

---

## PHASE 2B: MODEL TRAINING (DESKTOP ONLY)
**Duration:** 4-6 hours (completely independent)
**Allocation:** 0 tokens during execution, 30 tokens review

### Desktop Task: Run Development Training
**Instructions (ONE-TIME):**

```
Train Model 0/1 on frozen harnesses using 8-symbol development sample.

EXECUTION SEQUENCE:
1. Model 0 training on 480-day training set
   - Output: model_0_trained.pkl, model_0_train_metrics.json
   
2. Model 0 validation evaluation
   - Compute Rank IC, return, turnover, drawdown on 90-day validation set
   - Output: model_0_validation_metrics.json
   
3. Model 1 training (same process)
   - Output: model_1_trained.pkl, model_1_train_metrics.json
   - Output: model_1_validation_metrics.json

4. Tier 1-3 evaluation
   - Statistical (Rank IC, calibration)
   - Economic (gross/net return, turnover, max drawdown)
   - Stability (year/symbol/regime/fold consistency)
   - Output: validation_tier_evaluation.json

5. Summary report
   - Which model passed which tier?
   - Recommendation: proceed to holdout?
   - Output: training_summary_report.md

ACCEPTANCE CRITERIA:
- Training completes without crashes
- Validation metrics present and valid
- At least one model passes Tier 1
- Summary report provides clear GO/NO-GO

TIMING: 4-6 hours (run overnight if needed, save all outputs)
```

### Claude Review Checkpoint (30 tokens, 15 min)
After training completes:
1. Read validation_tier_evaluation.json
2. Check which models passed Tier 1, 2, 3
3. Verify summary_report.md logic
4. **Decision:** GO to holdout (if Tier 3 PASS), or STOP (document why)

**Output:** Trained models, validation metrics, go/no-go decision

---

## PHASE 2C: HOLDOUT TESTING (DESKTOP ONLY)
**Duration:** 2-3 hours (if Phase 2B PASS)
**Allocation:** 0 tokens during execution, 20 tokens review

### Desktop Task: Open & Test Holdout Set
**Instructions (ONE-TIME, if approved):**

```
Open sealed holdout dataset and test best model.

EXECUTION:
1. Load trained Model 0 or Model 1 (per Phase 2B decision)
2. Predict on 180-day holdout set (Sep 2025 - Aug 2026)
3. Compute Rank IC, return, stability metrics
4. Compare with validation results
5. Document any discrepancies
6. Output: holdout_test_results.json, item_3b_closure_report.md

ACCEPTANCE CRITERIA:
- Holdout metrics consistent with validation (within 10% tolerance)
- No statistical anomalies
- Final report recommends Item 3b PASS or FAIL
```

### Claude Review Checkpoint (20 tokens, 10 min)
1. Read holdout_test_results.json and closure_report.md
2. Verify consistency between validation and holdout
3. **Final Decision:** Item 3b CLOSED (PASS/FAIL documented)

**Output:** Item 3b closure with OOS evidence

---

## PHASE 3A: FEATURE PROVENANCE & STABILITY (RAZER, Parallel with Phase 2)
**Duration:** 2-3 hours (independent, while desktop trains)
**Allocation:** 0 tokens during execution, 20 tokens review

### Razer Task: Feature Analysis & Documentation
**Instructions (ONE-TIME):**

```
While desktop trains models, document feature provenance and stability.
(This can run in parallel; no dependencies on Model 0/1 results.)

DELIVERABLES:
1. feature_provenance_report.md
   - For each feature in A/B/C/D sets
   - Source data, computation method
   - Causality verification, no leakage check
   - Output: markdown report

2. feature_importance_baseline.json
   - Placeholder importances (before model selection)
   - Will be updated after holdout closure
   - Output: JSON skeleton

3. feature_stability_analysis.md
   - How stable are features over time?
   - Any drift or anomalies?
   - Symbol-by-symbol review
   - Output: markdown report

ACCEPTANCE CRITERIA:
- All features documented with clear provenance
- No causality issues identified
- Analysis complete and thorough
```

### Claude Review Checkpoint (20 tokens, 10 min)
1. Read feature_provenance_report.md
2. Verify causality claims
3. Check feature_stability_analysis.md for any red flags
4. **Decision:** Ready for Item 4 (universe expansion)

**Output:** Feature documentation, stable baseline

---

## PHASE 3B: UNIVERSE EXPANSION PLANNING (RAZER, Parallel)
**Duration:** 1-2 hours (while desktop tests holdout)
**Allocation:** 0 tokens during execution, 20 tokens review

### Razer Task: Prepare 108-Symbol Expansion
**Instructions (ONE-TIME):**

```
Design and document 108-symbol universe expansion (Item 4).

DELIVERABLES:
1. universe_expansion_spec.md
   - Which 108 symbols (all NIFTY48 + extended)?
   - Data availability check (do we have 3+ years for all?)
   - Panel construction plan (same as 8-symbol dev sample)
   - Output: markdown spec

2. universe_expansion_checklist.json
   - Symbol list with data date ranges
   - Missing dates (if any)
   - Data quality flags
   - Output: JSON

3. build_108_symbol_panel.py (skeleton)
   - Code template for universe expansion
   - Parameterized for 108 symbols
   - No execution yet (needs Item 3b PASS first)
   - Output: Python script skeleton

ACCEPTANCE CRITERIA:
- All 108 symbols documented
- Data availability verified
- Expansion plan is clear and ready to execute
- No surprises after Item 3b closure
```

### Claude Review Checkpoint (20 tokens, 10 min)
1. Read universe_expansion_spec.md
2. Verify symbol list and data availability
3. Review build_108_symbol_panel.py skeleton
4. **Decision:** Ready to expand after Item 3b PASS

**Output:** Universe expansion plan ready to execute

---

## PHASE 4: CONSOLIDATION & QA REVIEW
**Duration:** 1-2 hours (Claude intensive, but strategic)
**Allocation:** 50-70 tokens (focused review, no execution)

### Claude Task: Comprehensive QA Review
**When:** After Phase 2C (holdout testing) AND Phase 3A/3B (Razer analysis) complete

**Checklist:**
- [ ] Item 3b closure report reviewed and approved
- [ ] Feature provenance verified (no leakage)
- [ ] Feature stability confirmed (no drift)
- [ ] Universe expansion spec validated
- [ ] All JSON/CSV outputs verified (no corruption)
- [ ] Git commits clean and documented

**Output:** 
- Consolidated Item 3b closure document
- Item 4 (universe expansion) approved
- Go/no-go decision for next phase
- Updated project timeline

---

## EXECUTION CALENDAR

### Session 1: Preregistration (TODAY, Aug 28)
- 30 min: Owner reads & signs preregistration ✓ (DONE)
- 5 min: Claude commits hash (20 tokens)
- **Deliverable:** Frozen preregistration in Git

### Session 2: Harness Build (Aug 29, morning)
- 3-4 hours: Desktop builds Model 0/1 harnesses (0 tokens)
- 10 min: Claude reviews harness_validation_report.md (20 tokens)
- **Deliverable:** Validated training harnesses

### Session 3: Training & Razer Analysis (Aug 29-30, day/night)
- 4-6 hours: Desktop trains Model 0/1 (0 tokens, runs overnight)
- 2-3 hours: Razer builds feature documentation (parallel, 0 tokens)
- 2-3 hours: Razer designs universe expansion (parallel, 0 tokens)
- **Deliverable:** Trained models + feature analysis

### Session 4: Holdout & Consolidation (Aug 30-31)
- 2-3 hours: Desktop tests holdout set (0 tokens)
- 1-2 hours: Claude comprehensive QA review (50-70 tokens)
- **Deliverable:** Item 3b closed (PASS/FAIL), Item 4 ready

### Session 5: Universe Expansion (Sep 1-3)
- 3-4 hours: Desktop builds 108-symbol panel (0 tokens)
- 2-3 hours: Desktop trains Model 0/1 on 108-symbol (0 tokens)
- 10 min: Claude reviews expansion results (20 tokens)
- **Deliverable:** Item 4 methodology frozen

### Sessions 6+: Three-Head Assembly → Live Trading
- Oct 3-31: Items 5/MPC/Shadow/Live (same pattern)

---

## TOKEN BUDGET ALLOCATION

**Total Available Tokens:** 15,000,000

**Phase 1-4 Budget:** ~300 tokens (20% of work)
- Preregistration: 20 tokens
- Harness review: 20 tokens
- Training review: 30 tokens
- Holdout review: 20 tokens
- Feature review: 20 tokens
- Universe review: 20 tokens
- Consolidation: 50-70 tokens

**Phase 5+ Budget:** ~500 tokens
- Expansion review: 20 tokens
- Three-head assembly: 50 tokens
- MPC integration: 50 tokens
- Shadow testing: 30 tokens
- Live hardening: 30 tokens

**Reserve for Emergencies:** 14,200 tokens
- If model training breaks, need to debug
- If data issues arise, need investigation
- If gates fail, need pivot analysis

**Utilization:** <5% for delivery, 95% reserve for problem-solving & re-work

---

## GATE CRITERIA (NO RESCUE)

| Gate | Condition | Action if PASS | Action if FAIL |
|------|-----------|----------------|----------------|
| Preregistration | Owner signed | Proceed to harness build | Revise & re-sign |
| Harness validation | No errors, metrics valid | Proceed to training | Debug & rebuild |
| Training Tier 1 | Rank IC ≥ 0.025 | Continue | STOP (document why) |
| Training Tier 2 | Return > 50bp net | Continue | STOP (document why) |
| Training Tier 3 | All stability tests pass | Open holdout | STOP (document why) |
| Holdout consistency | Within 10% of validation | Item 3b PASS | Item 3b FAIL (document) |
| Feature provenance | No leakage, no drift | Ready for Item 4 | Re-analyze (hold) |
| Universe expansion | 108 symbols available | Execute expansion | Reduce to 48 symbols |

**NO EXCEPTIONS** to any gate. If gate fails, outcome is documented and escalated (not re-tuned).

---

## CLAUDE'S ROLE (Quality Gate + Project Manager)

**During Work Phases (80% of time):**
- Inactive (let machines work)
- Tokens conserved

**During Review Phases (20% of time):**
- Read outputs (5 min)
- Validate against acceptance criteria (5 min)
- Document gate decision (5 min)
- Create brief summary (<1 page)

**Zero:** Writing code, iterating, tweaking, re-running

**100%:** Validating, approving, documenting, escalating

---

## RISK MITIGATION

**If tokens run low mid-phase:**
- Desktop/Razer can continue independently (they have full instructions)
- Claude can resume review in next session
- No loss of work or progress

**If a gate fails:**
- Decision documented clearly
- Next step specified (continue or escalate)
- No "soft" holds or re-tuning

**If model training crashes:**
- Desktop reruns with same harness
- Claude reviews error and decides: retry or pivot
- Reserve tokens used only if needed

---

## SUCCESS CRITERIA

✅ **Phase 1-4 Complete (Aug 28-31):**
- Preregistration signed
- Harnesses built & tested
- Models trained & validated
- Holdout tested
- Item 3b CLOSED (PASS/FAIL documented)

✅ **Phase 5 Complete (Sep 1-10):**
- 108-symbol panel built
- Models retrained on full universe
- Item 4 CLOSED

✅ **Phase 6-10 Complete (Sep 11 - Oct 31):**
- Three-head assembly
- MPC integration
- Shadow deployment
- Live trading enabled

✅ **Token Utilization:**
- <5% spent on execution
- 95% reserve maintained
- No mid-work token exhaustion

---

## NEXT IMMEDIATE STEP

**Your approval needed:**

1. **Do you approve this token-optimized plan?** (Yes/No)
2. **Can we proceed with Phase 2A (harness build) on Aug 29 morning?** (Yes/No)
3. **Any adjustments to the phase structure?** (Specify)

Once approved, I will:
- Prepare ONE-TIME detailed instructions for desktop (Phase 2A)
- Prepare ONE-TIME detailed instructions for Razer (Phase 3A/3B)
- Submit both in a single session
- Let machines run independently for 4-6 hours
- Reconvene for review in 24 hours

**This approach ensures:**
- Maximum work output from both machines
- Minimal token burn during execution
- Clear quality gates (no fuzzy decisions)
- Full documentation trail for audit

**Ready to proceed?**

