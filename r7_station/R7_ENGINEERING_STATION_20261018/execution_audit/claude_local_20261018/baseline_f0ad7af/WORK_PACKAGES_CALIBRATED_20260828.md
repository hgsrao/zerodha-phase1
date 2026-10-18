# Work Packages: Machine-Calibrated & Size-Optimized
## Date: 2026-08-28
## Principle: Small packages, independent execution, zero blocking

---

## MACHINE PROFILES

### Desktop PC
- **CPU:** High-end workstation
- **RAM:** 16GB+ (system RAM)
- **GPU:** Adequate for Python execution
- **Network:** Reliable local
- **Capability:** Handle complex computation, large data transforms, model training
- **Package size:** 2-3 hours per package (can run longer overnight)
- **Best use:** Model training, numerical computation, large file I/O
- **Tolerance:** Can handle 1GB+ data files, 100k+ rows

### Razer Laptop
- **CPU:** Mobile processor (constrained)
- **RAM:** 16GB (upgradeable to 32GB Sep 2026)
- **GPU:** GTX 1060 Max-Q (6GB VRAM, limited)
- **Network:** Wi-Fi (can be unstable)
- **Capability:** Read-only analysis, lightweight computation, text/CSV processing
- **Package size:** 30 min - 1.5 hours per package (short, focused tasks)
- **Best use:** Code review, documentation, data inspection, small analysis
- **Tolerance:** Handle up to 500MB files, 10k rows max per package

---

## PACKAGE BREAKDOWN BY PHASE

### PHASE 2A: HARNESS BUILD (Desktop Only)

#### Package 2A-1: Model 0 Harness Code (30 min)
**What:** Build Ridge regression harness
**Input:** Preregistration frozen, data CSVs
**Output:** `model_0_ridge_regression.py` (code file, ~200 lines)
**Acceptance:** Code has no syntax errors, imports resolve
**Who:** Desktop
**Dependencies:** None

#### Package 2A-2: Model 1 Harness Code (30 min)
**What:** Build XGBoost harness
**Output:** `model_1_xgboost.py` (code file, ~250 lines)
**Acceptance:** Code has no syntax errors, imports resolve
**Who:** Desktop
**Dependencies:** None (parallel with 2A-1)

#### Package 2A-3: Harness Validation Test (30 min)
**What:** Run both harnesses on small sample (first 100 rows)
**Inputs:** model_0/1_ridge/xgboost.py, data CSVs
**Output:** `harness_validation_report.md`, `harness_test_metrics.json`
**Acceptance:** 
- Both harnesses execute without error
- Metrics JSON is valid (parseable, no NaN at top level)
- No data leakage detected
**Who:** Desktop
**Dependencies:** 2A-1 + 2A-2 complete

#### Package 2A-4: Data Integrity Check (20 min)
**What:** Verify train/val/holdout splits are correct
**Inputs:** Data CSVs, preregistration dates
**Output:** `data_split_verification.json`
**Acceptance:**
- All rows assigned to correct fold
- No duplicate rows across folds
- Target dates match preregistration
**Who:** Desktop
**Dependencies:** Anytime (can run parallel)

**Total Phase 2A:** 1.5-2 hours (4 small packages)

---

### PHASE 2B: MODEL TRAINING (Desktop Only)

#### Package 2B-1: Model 0 Training (2 hours)
**What:** Train Ridge regression on full training set
**Input:** model_0_ridge_regression.py, training data (480 days)
**Output:** `model_0_trained.pkl`, `model_0_train_log.json`
**Acceptance:**
- model_0_trained.pkl is valid pickle file
- train_log.json has no NaN values
- Training completed without crashes
**Who:** Desktop
**Dependencies:** 2A complete

#### Package 2B-2: Model 0 Validation Eval (1 hour)
**What:** Evaluate Model 0 on validation set
**Input:** model_0_trained.pkl, validation data (90 days)
**Output:** `model_0_validation_results.json` (Rank IC, return, metrics)
**Acceptance:**
- All metrics present and numeric
- Rank IC computed for each horizon {1,3,5,10,20}
- Return computed for each symbol
**Who:** Desktop
**Dependencies:** 2B-1 complete

#### Package 2B-3: Model 1 Training (2 hours)
**What:** Train XGBoost on full training set
**Input:** model_1_xgboost.py, training data
**Output:** `model_1_trained.pkl`, `model_1_train_log.json`
**Acceptance:** Same as 2B-1
**Who:** Desktop
**Dependencies:** 2A complete (can run parallel with 2B-1/2)

#### Package 2B-4: Model 1 Validation Eval (1 hour)
**What:** Evaluate Model 1 on validation set
**Output:** `model_1_validation_results.json`
**Acceptance:** Same as 2B-2
**Who:** Desktop
**Dependencies:** 2B-3 complete

#### Package 2B-5: Tier 1-3 Evaluation Summary (30 min)
**What:** Compare both models on Tier 1, 2, 3 criteria
**Inputs:** model_0/1_validation_results.json, preregistration thresholds
**Output:** `tier_evaluation_summary.json`, `training_summary_report.md`
**Acceptance:**
- Clear PASS/HOLD/FAIL decision for each tier
- Summary report recommends which model (if any) to holdout test
- All thresholds from preregistration applied correctly
**Who:** Desktop
**Dependencies:** 2B-2 + 2B-4 complete

**Total Phase 2B:** 6.5 hours (5 small packages, can run in sequence or parallel)

---

### PHASE 2C: HOLDOUT TESTING (Desktop Only, if 2B PASS)

#### Package 2C-1: Holdout Prediction (1 hour)
**What:** Load trained model, predict on 180-day holdout set
**Input:** Best model from 2B (pickle), holdout data (Sep 2025 - Aug 2026)
**Output:** `holdout_predictions.csv` (predictions for all 8 symbols × 180 days)
**Acceptance:**
- CSV has correct number of rows (8 symbols × 180 days ≈ 1,440 rows)
- Predictions are numeric (no NaN, no inf)
- Column names match feature set
**Who:** Desktop
**Dependencies:** 2B PASS decision

#### Package 2C-2: Holdout Metrics (1 hour)
**What:** Compute Rank IC, return, stability on holdout predictions
**Input:** holdout_predictions.csv, actual holdout targets
**Output:** `holdout_metrics.json`, `holdout_comparison.json`
**Acceptance:**
- Metrics computed for all {1,3,5,10,20} horizons
- Comparison shows validation vs. holdout side-by-side
- No obvious discrepancies (>10% drift)
**Who:** Desktop
**Dependencies:** 2C-1 complete

#### Package 2C-3: Item 3b Closure Report (30 min)
**What:** Compile final Item 3b evidence
**Input:** holdout_metrics.json, all prior reports
**Output:** `item_3b_closure_report.md`, `item_3b_final_evidence.json`
**Acceptance:**
- Report clearly states: Model X passes Item 3b (PASS/FAIL)
- Evidence hashes included
- Recommendation for Item 4 is explicit
**Who:** Desktop
**Dependencies:** 2C-2 complete

**Total Phase 2C:** 2.5 hours (3 small packages)

---

### PHASE 3A: FEATURE ANALYSIS (Razer Only, Parallel with Phase 2)

#### Package 3A-1: Feature Inventory & Provenance (45 min)
**What:** Document each feature in A/B/C/D sets
**Input:** `daily_multi_timescale_fusion_panel.py`, preregistration
**Output:** `feature_provenance_catalog.md` (one feature per section)
**Acceptance:**
- All features listed (P1, P2, 7D, ORB, Map)
- Source data and computation method documented for each
- No circular references (no look-ahead)
**Who:** Razer (lightweight text analysis)
**Dependencies:** None

#### Package 3A-2: Causality Verification (45 min)
**What:** Verify no data leakage in feature computation
**Input:** feature_provenance_catalog.md, data panel
**Output:** `causality_verification_report.md`
**Acceptance:**
- All feature timestamps verified (at or before D close)
- All target timestamps verified (D+1 onward)
- Cross-checks for known leakage patterns done
**Who:** Razer (read-only code inspection)
**Dependencies:** 3A-1 complete

#### Package 3A-3: Feature Stability Over Time (45 min)
**What:** Analyze feature distributions across years/symbols
**Input:** `daily_multi_timescale_fusion_panel_20260825.csv`
**Output:** `feature_stability_analysis.json`, `feature_stability_charts.md`
**Acceptance:**
- Mean/std of each feature by year (2023, 2024, 2025, 2026)
- Mean/std by symbol (8 symbols)
- Flags any >50% drift in mean values year-over-year
**Who:** Razer (lightweight pandas analysis, <500MB data)
**Dependencies:** None (can run anytime)

#### Package 3A-4: Feature Importance Baseline (30 min)
**What:** Create placeholder feature importance JSON (to be filled after holdout)
**Output:** `feature_importance_skeleton.json`
**Acceptance:** 
- JSON structure matches all features in A/B/C/D
- Ready to populate with Model 0/1 importance after 2C-3
**Who:** Razer (simple JSON scaffolding)
**Dependencies:** None

**Total Phase 3A:** 2.5-3 hours (4 small packages, can run in parallel with Phase 2)

---

### PHASE 3B: UNIVERSE EXPANSION PLANNING (Razer Only, Parallel with Phase 2)

#### Package 3B-1: Symbol & Data Availability Check (45 min)
**What:** Verify 108-symbol dataset completeness
**Input:** Kite data directories, historical file lists
**Output:** `universe_symbols.json`, `data_availability_report.md`
**Acceptance:**
- All 108 symbols listed (48 NIFTY + 60 extended)
- Data date ranges verified
- Missing dates flagged (if any)
- Quality assessment per symbol (✓ complete, ⚠ gaps, ✗ insufficient)
**Who:** Razer (lightweight file system scan)
**Dependencies:** None

#### Package 3B-2: Universe Expansion Specification (45 min)
**What:** Design the 108-symbol panel construction
**Input:** universe_symbols.json, existing panel spec
**Output:** `universe_expansion_spec.md`
**Acceptance:**
- Panel construction logic clear (same as 8-symbol?)
- Feature set application specified
- Target definition same as Item 3b?
- Computational cost estimate (time to build panel)
**Who:** Razer (text/spec writing)
**Dependencies:** 3B-1 complete

#### Package 3B-3: Build Panel Code Template (30 min)
**What:** Write skeleton code for 108-symbol panel
**Input:** `daily_multi_timescale_fusion_panel.py`, universe spec
**Output:** `build_108_symbol_panel_template.py` (parameterized, no execution)
**Acceptance:**
- Code has correct structure (imports, functions, no errors)
- Parameterized for 108 symbols (not hardcoded)
- Ready to execute after Item 3b PASS
**Who:** Razer (lightweight code writing)
**Dependencies:** 3B-2 complete

#### Package 3B-4: Expansion Risk Assessment (30 min)
**What:** Identify risks in 108-symbol expansion
**Input:** universe_symbols.json, data_availability_report.md
**Output:** `expansion_risk_assessment.json`
**Acceptance:**
- Risks categorized (data gaps, quality issues, missing symbols)
- Mitigation strategy for each risk
- Decision tree: proceed or reduce universe size?
**Who:** Razer (analysis + documentation)
**Dependencies:** 3B-1 + 3B-2 complete

**Total Phase 3B:** 2.5-3 hours (4 small packages, can run parallel with Phase 2)

---

## SMALL PACKAGE SCHEDULING

### Day 1 (Aug 29, Morning): Phase 2A Harness Build
```
Desktop:
  09:00 - 09:30  → Package 2A-1 (Model 0 harness code)
  09:30 - 10:00  → Package 2A-2 (Model 1 harness code)
  10:00 - 10:30  → Package 2A-3 (Harness validation test)
  10:30 - 10:50  → Package 2A-4 (Data integrity check)
  
Claude (5 min review):
  10:50 - 10:55  → Validate outputs (harness_validation_report.md + metrics)
  
Result: PASS/FAIL gate decision
```

### Day 2 (Aug 29, Afternoon/Night): Phase 2B Training + Phase 3 Analysis
```
Desktop (parallel track):
  14:00 - 16:00  → Package 2B-1 (Model 0 training, 2h)
  16:00 - 17:00  → Package 2B-2 (Model 0 validation eval, 1h)
  [Overnight]
  
  09:00 - 11:00  → Package 2B-3 (Model 1 training, 2h) [can run overnight]
  11:00 - 12:00  → Package 2B-4 (Model 1 validation eval, 1h)
  12:00 - 12:30  → Package 2B-5 (Tier 1-3 summary, 30 min)

Razer (parallel track, independent):
  14:00 - 14:45  → Package 3A-1 (Feature provenance, 45 min)
  14:45 - 15:30  → Package 3A-2 (Causality verification, 45 min)
  15:30 - 16:15  → Package 3A-3 (Feature stability, 45 min)
  16:15 - 16:45  → Package 3A-4 (Feature importance skeleton, 30 min)
  
  [Next day]
  14:00 - 14:45  → Package 3B-1 (Data availability, 45 min)
  14:45 - 15:30  → Package 3B-2 (Expansion spec, 45 min)
  15:30 - 16:00  → Package 3B-3 (Panel code template, 30 min)
  16:00 - 16:30  → Package 3B-4 (Risk assessment, 30 min)

Claude (10 min review after both complete):
  12:30 - 12:40  → Validate training results (Tier 1-3 summary)
  
  [After Razer finishes]
  17:00 - 17:10  → Validate feature analysis outputs
  
Result: Training PASS/FAIL + Analysis validated
```

### Day 3 (Aug 31): Phase 2C Holdout Testing
```
Desktop (if Phase 2B PASS):
  09:00 - 10:00  → Package 2C-1 (Holdout prediction, 1h)
  10:00 - 11:00  → Package 2C-2 (Holdout metrics, 1h)
  11:00 - 11:30  → Package 2C-3 (Item 3b closure report, 30 min)

Claude (10 min review):
  11:30 - 11:40  → Validate holdout results + closure report
  
Result: Item 3b CLOSED (PASS/FAIL documented)
```

---

## TOTAL TIME & TOKEN ALLOCATION

| Phase | Duration | Desktop | Razer | Claude Tokens |
|-------|----------|---------|-------|---------------|
| 2A | 1.5-2h | ✓ All | — | 20 (review) |
| 2B | 6.5h | ✓ All | — (parallel) | 30 (review) |
| 3A | 2.5-3h | — | ✓ All | 20 (review) |
| 3B | 2.5-3h | — | ✓ All | 20 (review) |
| 2C | 2.5h | ✓ All | — | 20 (review) |
| **Total** | **15-17h** | **~10-11h** | **~5-6h** | **~120 tokens** |

**Efficiency:**
- Desktop: ~10-11 hours of productive work
- Razer: ~5-6 hours of lightweight analysis (parallel)
- Claude: 120 tokens (~0.8% of budget)
- 99% of work done by machines, 1% by Claude (review only)

---

## ACCEPTANCE CRITERIA PER PACKAGE

Every package has **exact** acceptance criteria (no fuzzy "looks good"):

✅ **Code packages:** No syntax errors, imports resolve, no type mismatches
✅ **Data packages:** Expected row/column counts, no NaN at top level, valid JSON
✅ **Analysis packages:** Metrics complete, no anomalies >50% drift, report clear
✅ **Report packages:** Final decision explicit (PASS/FAIL/HOLD), evidence included

**If ANY package fails acceptance criteria:** 
- Hold gate
- Document failure reason
- Do NOT proceed to next package until issue resolved

---

## READY TO PROCEED?

Once approved, I will:

1. **Today (Aug 28, 20:00):** Prepare Phase 2A instructions (small, clear, one-time)
2. **Tomorrow (Aug 29, 09:00):** Desktop starts Package 2A-1
3. **Tomorrow (Aug 29, 10:55):** Claude reviews outputs (5 min)
4. **Tomorrow (Aug 29, 14:00):** Desktop starts Phase 2B + Razer starts Phase 3A (parallel)
5. **Aug 30:** Machines continue independently
6. **Aug 31:** Holdout testing + consolidation

**Approval needed on:**
- [ ] This package structure (8 packages per phase, size-calibrated)?
- [ ] Desktop gets larger packages (2-3h), Razer gets smaller packages (30 min - 1.5h)?
- [ ] All acceptance criteria are **objective** (not subjective)?

**Proceed?**

