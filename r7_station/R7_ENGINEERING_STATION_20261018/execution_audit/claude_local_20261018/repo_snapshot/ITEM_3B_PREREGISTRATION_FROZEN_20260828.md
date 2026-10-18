# Item 3b Model Development — Preregistration (FROZEN)
## Date: 2026-08-28
## Status: AWAITING OWNER SIGNATURE

---

## EXECUTIVE SUMMARY

This document freezes ALL decisions required before any Model 0/1 training on the 8-symbol development sample. No parameter changes, no threshold adjustments, and no rescue modifications are permitted after this signature. Violations of this contract constitute a new, separate experiment requiring independent preregistration.

**Preregistration Hash:** `[TO BE CALCULATED AFTER SIGNATURE]`  
**Data Panel Hash:** `52f39a49722c9c32ed1ea763395cd831b7b3c11b56f654df393ea5b14636d1b0` (fusion_panel_20260825.py)  
**Authority:** Study Layer V2 Research Roadmap (2026-08-25)

---

## SECTION 1: FROZEN DATA & FEATURES

### Input Data
- **Panel:** `daily_multi_timescale_fusion_panel_20260825.csv`
  - Rows: 5,928 (8 symbols × 3 years)
  - Columns: 55 (state + targets)
  - Date range: 2023-08-25 to 2026-08-24
  - SHA256: `52f39a49722c9c32ed1ea763395cd831b7b3c11b56f654df393ea5b14636d1b0`

- **P1/P2 State:** `p1_p2_state_decomposition_20260825.csv`
  - Rows: 5,944
  - Columns: 31 (P1 trend + P2 reversion state)
  - SHA256: `2ec5bd377fdbb3e3ded882a7f4c25dc3ec6f5d3ed16c59cc33f68c05a264080f`

### Symbols (8)
BAJFINANCE, SBIN, SUNPHARMA, RELIANCE, INFY, TCS, HDFCBANK, ICICIBANK

### Feature Sets (4 variants, all frozen)

**Feature Set A — P1/P2 Only (10 features)**
- trend_slope
- trend_strength_90d
- breakout_distance_atr
- invalidation_distance_atr
- trend_persistence_days
- reversion_zscore
- displacement_atr
- overshoot
- reversion_setup_age_days
- p1_signal_active, p2_signal_active (descriptive flags)

**Feature Set B — 7D+ORB+Map (17 features)**
- 7D session summaries: S_last, S_mean, L_vwap_last, L_vwap_mean, M_last, M_max, M_min, V_last, V_max
- ORB: orb_active, orb_breakout_strength, orb_range_atr, orb_time_of_first_break_minutes
- Map+Context: map_event_count, map_reclaim_seen, map_last_state, map_distance_to_level

**Feature Set C — A + B (27 features)**
- Combination of all P1/P2 + 7D+ORB+Map

**Feature Set D — Provenance-Aware Regularized (TBD)**
- Subset of A/B/C
- Selection criteria: feature importance in Models 0/1, causality verified
- Final list determined after validation, if either model passes Tier 2 threshold

### Targets (Frozen at 5 horizons)
- `fwd_return_{h}d`: absolute forward return
- `excess_return_{h}d`: return vs. NIFTY 50
- `cross_sectional_rank_{h}d`: rank within 8-symbol universe
- `mfe_{h}d`, `mae_{h}d`: maximum favorable/adverse excursion

**Horizons:** {1, 3, 5, 10, 20} trading days forward

### Causality Protections (Verified & Frozen)
- ✓ All features computed from data available at or before close of day D
- ✓ Targets use D+1 onward, no same-day leakage
- ✓ ORB/Map computations use only D's bars, not future data
- ✓ P1/P2 features from precomputed, frozen CSV
- ✓ Cross-sectional rank computed within-date across 8-symbol universe
- ✓ No directional votes mixed into feature set (risk-filter test results not used as features)

---

## SECTION 2: CHRONOLOGICAL TRAIN/VALIDATION/HOLDOUT SPLIT

### Split Strategy (75/10/15 by trading days)

**Training Set**
- Date range: 2023-08-25 to 2025-04-30
- Trading days: 480
- Purpose: Fit Model 0/1 hyperparameters and architecture
- Data available to model: Full features + targets

**Validation Set**
- Date range: 2025-05-01 to 2025-08-29
- Trading days: 90
- Purpose: Hyperparameter tuning, Tier 1-3 evaluation, model selection
- Data available: Same as training (no look-ahead)
- **Critical constraint:** Holdout must remain sealed until all decisions frozen

**Holdout Set**
- Date range: 2025-09-01 to 2026-08-29
- Trading days: 180 (exactly 1 year forward in time)
- Purpose: Final OOS evidence (opened exactly once, after preregistration)
- Data available: None until decision made
- **Critical constraint:** No tuning, no threshold adjustment, no re-parametrization

### Rationale
- Preserves strict temporal ordering (no future leakage)
- 480 training days sufficient for stable regression (Model 0) and tree-based (Model 1)
- 90 validation days adequate for fold-based evaluation
- 180 holdout days = 1 full year, covers multiple market regimes

### No Exceptions
- No cross-validation (fold-based evaluation only within training set)
- No random shuffle or stratification (temporal order sacred)
- No re-splitting based on model performance

---

## SECTION 3: DATA PREPROCESSING PIPELINE (FROZEN)

### Missing Value Handling
- **Rule:** Impute missing values if <5% of column; drop column if ≥5% missing
- **Method:** Forward-fill for time-series features (e.g., ORB state); median imputation for cross-sectional features
- **Applied to:** Both training and validation sets (using training set statistics)
- **Applied at:** Model 0/1 harness level, before any model run

### Feature Scaling
- **Rule:** Standardize to mean=0, std=1
- **Method:** `(X - X_train_mean) / X_train_std`
- **Applied to:** Training, validation, holdout sets (using training set statistics)
- **Exception:** Bounded features with natural scale (e.g., 0-1 ranks) may use min-max instead (decision during harness build if needed)

### Feature Engineering
- **Prohibition:** No new features beyond frozen A/B/C/D sets
- **Prohibition:** No interactions, polynomials, or derived features
- **Prohibition:** No domain-specific transformations not already in the input panel

### Target Preprocessing
- **No scaling** on forward returns or rank targets (raw values used)
- **Handling:** Drop rows where target is NaN (represents insufficient forward data)

---

## SECTION 4: MODEL 0 CONFIGURATION (Regularized Linear Regression)

### Algorithm Choice (FROZEN)
- **Model:** Ordinary Least Squares with L2 regularization (Ridge regression)
- **Reason:** Interpretable, stable, no hyperparameter tuning risk on small validation set
- **Not allowed:** Lasso (L1), ElasticNet, or other alternatives

### Hyperparameter Specification (FROZEN)

**Regularization Strength: λ = 0.01 (default)**
- Primary value: 0.01
- Sensitivity analysis values: 0.001, 0.1 (explored on validation fold only, decision remains at 0.01 for holdout)
- Tuning method: Grid search on validation set only (no leakage to holdout)

**Solver:** Standard matrix inversion (analytical solution)

**Fit Intercept:** True (model includes intercept term)

**Normalize:** False (handled separately in preprocessing step)

### What Model 0 Predicts
- Target: Forward return at horizons {1, 3, 5, 10, 20} days
- Output: Continuous score (use rank IC for evaluation, not raw prediction accuracy)
- Feature set options: A, B, C evaluated separately; D selected only if either A/B/C passes Tier 2

### Training Procedure (FROZEN)
1. Fit on 480-day training set
2. Evaluate on 90-day validation set
3. If validation Tier 2 passes: test on 180-day holdout set
4. If validation Tier 2 fails: stop (no rescue threshold adjustments)

---

## SECTION 5: MODEL 1 CONFIGURATION (Tree-Based / Boosting)

### Algorithm Choice
- **Model:** XGBoost (gradient boosting decision trees)
- **Reason:** Handles nonlinear relationships, captures interactions, proven on quantitative finance tasks
- **Alternative:** LightGBM (if XGBoost unavailable; same evaluation gates apply)

### Hyperparameter Specification (FROZEN)

**Tree Complexity:**
- max_depth = 5 (maximum tree depth)
- min_child_weight = 1.0 (minimum leaf samples)
- subsample = 0.8 (row subsampling)
- colsample_bytree = 0.8 (feature subsampling per tree)

**Boosting:**
- n_estimators = 500 (maximum boosting rounds)
- learning_rate = 0.05 (shrinkage per round)
- early_stopping_rounds = 50 (stop if validation loss doesn't improve)

**Regularization:**
- lambda (L2) = 1.0 (tree regularization)
- gamma = 0.1 (split gain threshold)

**Other:**
- objective = "reg:squarederror" (squared error loss for regression)
- random_state = 42 (reproducibility)

### What Model 1 Predicts
- Same targets and feature sets as Model 0
- Output: Continuous score (rank IC used for evaluation)
- Feature set options: A, B, C evaluated; D selected if A/B/C pass

### Training Procedure (FROZEN)
1. Fit on 480-day training set
2. Validate on 90-day validation set (use early stopping)
3. If validation Tier 2 passes: test on 180-day holdout
4. If validation Tier 2 fails: stop (no hyperparameter rescue)

---

## SECTION 6: EVALUATION FRAMEWORK (THREE TIERS)

### Tier 1 — Statistical Significance

**Rank Information Coefficient (Rank IC)**
- Definition: Spearman correlation between model predictions and actual forward returns
- Computed on: Validation set, separately for each horizon {1,3,5,10,20}
- **PASS threshold:** Mean Rank IC ≥ 0.025 (p < 0.05)
- **FAIL threshold:** Mean Rank IC < 0.025
- Method: Jackknife/bootstrap confidence intervals for each symbol
- Multiple testing: Benjamini-Hochberg FDR correction across 8 symbols × 5 horizons = 40 tests

**Calibration Test**
- Definition: Does predicted rank match actual rank distribution?
- Computed on: Validation set
- **PASS threshold:** Slope of calibration curve 0.8–1.2 (no severe over/under-prediction)
- **FAIL threshold:** Slope < 0.5 or > 1.5

**Uncertainty Quantification**
- Definition: 90% prediction interval width
- **PASS threshold:** Interval width < 2× cross-sectional return std
- **FAIL threshold:** Interval width ≥ 2× std

**Tier 1 Gate:** ALL three sub-tests must PASS to proceed to Tier 2

---

### Tier 2 — Economic Return & Feasibility

**Gross Annual Return**
- Definition: Sum of daily `fwd_return_1d × model_prediction_1d` across validation period, annualized
- **PASS threshold:** ≥ 200 basis points (2%) on 8-symbol universe
- **FAIL threshold:** < 200 bp
- Computed separately for each symbol and feature set (A/B/C/D)

**Net Annual Return (After Costs)**
- Definition: Gross return minus 2 basis points per day trading cost
- **PASS threshold:** ≥ 50 basis points (0.5%) after costs
- **FAIL threshold:** < 50 bp
- Cost model: 2 bp execution cost assumed daily (real Zerodha NSE + brokerage)

**Average Daily Turnover**
- Definition: Proportion of portfolio rebalanced per day
- **PASS threshold:** ≤ 15% per day on average
- **FAIL threshold:** > 15% (impractical execution given market impact)

**Maximum Drawdown (Validation Period)**
- Definition: Worst cumulative loss from peak
- **PASS threshold:** ≤ 10% on validation period
- **FAIL threshold:** > 10%

**Tier 2 Gate:** ALL four sub-tests must PASS to proceed to Tier 3

---

### Tier 3 — Stability & Generalization

**Year-over-Year Consistency (Validation Holdout)**
- Definition: Annualized return computed separately for each calendar year in holdout period (Sep 2025 – Aug 2026)
- **PASS threshold:** All 12 months ≥ +50 bp net return
- **FAIL threshold:** Any month ≤ 0 (or < 50 bp)

**Symbol Stability**
- Definition: Annualized net return for each symbol independently
- **PASS threshold:** All 8 symbols ≥ +50 bp on average
- **FAIL threshold:** Any symbol ≤ 0

**Regime Stability**
- Definition: Split holdout into bull/sideways/bear markets (using ORB/Map regime); evaluate each regime
- **PASS threshold:** Each regime individually ≥ +50 bp net return
- **FAIL threshold:** Any regime ≤ 0

**Walk-Forward Fold Consistency**
- Definition: Divide holdout into 4 overlapping walk-forward folds; each fold must meet Tier 2 thresholds independently
- **PASS threshold:** All 4 folds pass Tier 2 independently
- **FAIL threshold:** Any fold fails Tier 2

**Tier 3 Gate:** ALL four sub-tests must PASS for model to be "validated"

---

## SECTION 7: MULTIPLE-TESTING CORRECTION

### Family Definition
- **Family 1 — Rank IC testing:** 8 symbols × 5 horizons = 40 tests (per model, per feature set)
- **Family 2 — Economic return testing:** 4 tests (gross, net, turnover, drawdown) × 4 feature sets = 16 tests
- **Family 3 — Stability testing:** 4 sub-tests (year/symbol/regime/fold) per feature set

### Correction Method
- **Benjamini-Hochberg FDR** at family-wise level α = 0.05
- **Applied to:** p-values from Rank IC, calibration, and return significance tests
- **Not applied to:** Threshold-based pass/fail (e.g., "return > 200 bp") — these are decision rules, not hypothesis tests

### Interpretation
- Reject hypothesis of "zero signal" if FDR-adjusted p-value < 0.05
- If rejected: Model has statistically significant predictive power
- If not rejected: Signal insufficient for trading (model fails Tier 1, stop)

---

## SECTION 8: MODEL SELECTION RULE (Multi-Criteria, Not R² Alone)

### Evaluation Order (Sequential Gates)

**Gate 1 — Tier 1 Pass (Statistical Significance)**
- Does Model 0/1 have predictive power (Rank IC > threshold)?
- **Decision:** PASS Tier 1 → proceed to Tier 2. FAIL Tier 1 → STOP (model rejected, no rescue)

**Gate 2 — Tier 2 Pass (Economic Feasibility)**
- Does Model 0/1 produce positive risk-adjusted returns after costs?
- **Decision:** PASS Tier 2 → proceed to Tier 3. FAIL Tier 2 → STOP (not economically viable, no rescue)

**Gate 3 — Tier 3 Pass (Stability)**
- Does Model 0/1 remain profitable across time, symbols, regimes, and folds?
- **Decision:** PASS Tier 3 → PROMOTED to Item 5 (three-head assembly). FAIL Tier 3 → STOP

### Selection Among Feature Sets (A vs. B vs. C vs. D)

If multiple feature sets pass all three tiers:
1. **Preference:** Simpler features win (A > B > C > D)
2. **Rationale:** Avoid overfitting to interaction effects
3. **Decision:** Select feature set with highest Rank IC among passing sets

### Selection Among Models (Model 0 vs. Model 1)

If both Model 0 and Model 1 pass all three tiers on same feature set:
1. **Preference:** Model 0 wins (simpler is better)
2. **Rationale:** Interpretability, fewer hyperparameters, lower maintenance
3. **Decision:** Use Model 0 for Item 5; Model 1 archived as backup

### Tie-Breaking (Unlikely, but specified)

If Rank IC identical between models/feature sets at high precision:
1. **Criterion 1:** Lowest turnover (most efficient)
2. **Criterion 2:** Lowest maximum drawdown (most stable)
3. **Criterion 3:** Earliest entry date in model registry (first to pass)

---

## SECTION 9: ABORT CONDITIONS (Pre-Defined Failure Triggers)

Any of the following conditions trigger automatic STOP (no rescue):

**Abort Condition 1 — No Signal Passes Tier 1**
- If neither Model 0 nor Model 1 achieves Rank IC ≥ 0.025 on validation
- **Action:** STOP. Outcome: "Signal insufficient for trading."
- **No rescue:** No threshold adjustment, no re-feature-engineering within Item 3b

**Abort Condition 2 — Economic Failure on Tier 2**
- If all passing models fail net return > 50 bp after costs
- **Action:** STOP. Outcome: "Strategy not economically viable."
- **No rescue:** No cost assumption changes, no position-sizing tricks

**Abort Condition 3 — Stability Collapse (Any Tier 3 Sub-Test Fails)**
- If any symbol, year, regime, or fold drops below +50 bp net return
- **Action:** STOP. Outcome: "Model unstable; overfitting suspected."
- **No rescue:** No threshold relaxation

**Abort Condition 4 — Turnover Too High**
- If average daily turnover > 15%
- **Action:** STOP. Outcome: "Trading too frequently; execution cost prohibitive."
- **No rescue:** No turnover cap relaxation

**Abort Condition 5 — Drawdown Excessive**
- If max drawdown > 25% on validation OR any fold > 15% on holdout
- **Action:** STOP. Outcome: "Risk-return profile unacceptable."
- **No rescue:** No drawdown threshold adjustment

**Abort Condition 6 — Data Quality Issue Detected During Training**
- If >10% of validation rows identified as corrupt or missing unexpectedly
- **Action:** STOP. Outcome: "Data integrity compromised."
- **No rescue:** Investigate upstream data source

---

## SECTION 10: VALIDATION PROCEDURE (Nested Walk-Forward)

### Validation Phase (On 90-day validation set)

**Step 1: Model 0 Training & Validation**
1. Fit Model 0 (Ridge λ=0.01) on 480-day training set
2. Predict on 90-day validation set
3. Compute Tier 1 metrics (Rank IC, calibration, uncertainty)
4. Compute Tier 2 metrics (gross/net return, turnover, drawdown)
5. **Decision:** Pass all Tier 1 & Tier 2? → Go to Step 3. Else → STOP.

**Step 2: Model 1 Training & Validation**
1. Fit Model 1 (XGBoost) on 480-day training set
2. Predict on 90-day validation set
3. Compute Tier 1 & Tier 2 metrics
4. **Decision:** Pass all Tier 1 & Tier 2? → Continue. Else → Mark as FAIL.

**Step 3: Tier 3 Evaluation (Stability)**
- For model(s) passing Tier 1 & Tier 2:
  - Compute year/symbol/regime/fold consistency
  - **Decision:** Pass Tier 3? → Promoted to holdout. Fail? → STOP.

**Step 4: Holdout Test (If any model passes Tier 3)**
1. Open holdout dataset (180-day set, Sep 2025 – Aug 2026)
2. Predict using trained Model 0/1 (no re-training)
3. Compute Rank IC, return, and stability on holdout
4. **Final decision:** Holdout Tier 1-3 consistent with validation? → PROMOTED to Item 5. Else → Document evidence and STOP.

### Nested Walk-Forward Details (If Enabled)

Within the training set (480 days), use 4 overlapping folds:
- Fold 1: Days 1–240, validate on 241–360
- Fold 2: Days 121–360, validate on 361–480
- Fold 3: Days 1–360, validate on holdout (if available early)
- Fold 4: Entire training set, validate on actual validation set (90 days)

Each fold must pass Tier 2 independently. If any fold fails, model rejected.

### No Data Leakage Constraints

- ✓ Training set features computed from days ≤ training end date
- ✓ Validation targets never used in training
- ✓ Holdout data sealed until final decision
- ✓ No target peeking during hyperparameter tuning (use validation only)
- ✓ All statistics (mean, std, thresholds) computed from training set, applied universally

---

## SECTION 11: FROZEN CONTRACTS & BINDING COMMITMENTS

### Contract 1: Model Architecture Freeze
**Binding Statement:** We commit to train only Model 0 (Ridge λ=0.01) and Model 1 (XGBoost) on features from `p1_p2_state_decomposition_20260825.csv` and `daily_multi_timescale_fusion_panel_20260825.csv`. No new features, no hand-crafted engineering, no model substitutions.

**Enforcement:** If a different model (e.g., neural network) is proposed after this signature, it requires independent preregistration.

### Contract 2: Hyperparameter Lock
**Binding Statement:** Model 0 uses λ=0.01; Model 1 uses the specified XGBoost config. If sensitivity analysis on validation reveals better performance at λ=0.001 or λ=0.1, we document the finding but remain locked to λ=0.01 for holdout testing.

**Enforcement:** No post-hoc parameter sweeps after preregistration signature.

### Contract 3: Evaluation Threshold Lock
**Binding Statement:** Rank IC must exceed 0.025, net return must exceed +50 bp, and all Tier 3 sub-tests must pass. These thresholds are immutable. If a model achieves Rank IC = 0.024, it fails, and the result stands as a negative finding.

**Enforcement:** No threshold relaxation (e.g., "0.020 is close enough").

### Contract 4: No-Rescue Discipline
**Binding Statement:** If a model fails Tier 2 or Tier 3, we do not:
- Re-tune regularization
- Add new features
- Adjust cost assumptions
- Re-split train/validation boundaries
- Exclude "bad" periods from evaluation

**Enforcement:** One legitimate outcome is "strategy does not work on this data." We accept that outcome.

### Contract 5: Data Integrity & Reproducibility
**Binding Statement:** All feature generation is deterministic. The input panel CSVs are frozen. All preprocessing steps are documented. Any researcher should be able to reproduce this analysis from frozen artifacts.

**Enforcement:** Final report includes exact code commit hash, file hashes, and step-by-step reproduction guide.

---

## SECTION 12: DECISION AUTHORITY & SIGN-OFF

### Required Approvals (Before First Model Run)

**Owner Approval:**
- [ ] Signature required on this document
- [ ] Date of signature: _______________
- [ ] Printed name: _________________________

**Claude Code (Researcher):**
- [ ] Preregistration captured in Git with commit hash: _________________
- [ ] Training harness code reviewed against this spec
- [ ] Data integrity verified (panel SHA256 matches)

### Rejection Criteria (Reasons This Preregistration Can Be Rejected)

1. **Owner disagrees with split dates** — Propose alternative with justification
2. **Owner prefers different model(s)** — Specify alternatives; update preregistration
3. **Owner requires different evaluation thresholds** — Provide data/rationale
4. **Owner wants to reserve right to rescue** — Document this explicitly as variance from frozen discipline

### Amendments (After Signature = New Preregistration)

Any material change (split dates, thresholds, models, features) requires:
1. New preregistration document
2. New owner signature
3. New Git commit with "VARIANT" tag
4. Results clearly labeled as from amended spec

---

## SECTION 13: SUCCESS & FAILURE DEFINITIONS

### SUCCESS (Model Promoted to Item 5)

A model succeeds Item 3b when:
1. ✓ Passes Tier 1 (Rank IC ≥ 0.025, p < 0.05)
2. ✓ Passes Tier 2 (net return > 50 bp, drawdown < 10%)
3. ✓ Passes Tier 3 (all symbols, years, regimes, folds stable)
4. ✓ Holdout evidence consistent with validation
5. ✓ Documented in final OOS report with reproducibility guide

**Outcome:** Model 0 or Model 1 + Feature Set (A/B/C/D) promoted to Item 5 (three-head assembly)

### FAILURE (Stop, No Rescue)

A model fails if any of these occur:
- ✗ Rank IC < 0.025 (Tier 1 fail)
- ✗ Net return < 50 bp (Tier 2 fail)
- ✗ Any Tier 3 sub-test fails
- ✗ Abort condition triggered
- ✗ Data integrity issue detected

**Outcome:** "Item 3b inconclusive. Strategy does not demonstrate predictive edge on 8-symbol universe." Escalate to owner for next steps (return to feature engineering, or pause trading research).

---

## APPENDIX A: Git Artifacts (Frozen Hashes)

These files are committed to Git and immutable:

| Artifact | Path | SHA256 Hash | Lock Status |
|----------|------|-------------|------------|
| Fusion panel | `daily_multi_timescale_fusion_panel_20260825.csv` | `52f39a49722c9c32ed1ea763395cd831b7b3c11b56f654df393ea5b14636d1b0` | ✅ FROZEN |
| P1/P2 state | `p1_p2_state_decomposition_20260825.csv` | `2ec5bd377fdbb3e3ded882a7f4c25dc3ec6f5d3ed16c59cc33f68c05a264080f` | ✅ FROZEN |
| Research roadmap | `STUDY_LAYER_V2_RESEARCH_ROADMAP_20260825.md` | `[to be computed]` | ✅ FROZEN |
| Model harness (Model 0) | `model_0_ridge_regression.py` | `[to be computed after build]` | ⏳ Pending |
| Model harness (Model 1) | `model_1_xgboost.py` | `[to be computed after build]` | ⏳ Pending |
| This preregistration | `ITEM_3B_PREREGISTRATION_FROZEN_20260828.md` | `[to be computed after signature]` | ⏳ Pending signature |

---

## APPENDIX B: Timeline

- **Aug 28, 2026** — Preregistration drafted (this document)
- **Aug 29, 2026** — Owner review & signature required
- **Aug 30, 2026** — Git commit with frozen hash
- **Aug 30–Sep 4, 2026** — Model 0/1 harness built & tested
- **Sep 5–18, 2026** — Training, validation, holdout evaluation
- **Sep 19, 2026** — Item 3b closure (PASS or FAIL documented)
- **Sep 19–Oct 2, 2026** — Item 4 universe expansion (if Item 3b PASS)
- **Oct 31, 2026** — LIVE_TRADING_ENABLED = True (if Item 4 PASS)

---

## SIGNATURE BLOCK

**Owner (Zerodha Trading Authority):**

Signature: ________________________  
Printed name: _______________________  
Date: _____________________________  
Timestamp (IST): _____________________

**Researcher (Claude Code):**

Signature: ________________________  
Printed name: Claude Haiku 4.5  
Date: 2026-08-28  
Timestamp (IST): 19:25 IST

---

## DOCUMENT HISTORY

| Version | Date | Change | Authority |
|---------|------|--------|-----------|
| 1.0 | 2026-08-28 | Initial draft from PACKAGES 9/10 | Claude Code |
| [FROZEN] | [After signature] | Locked in Git with hash | Owner + Claude |

---

**This preregistration is valid only when signed and committed to Git.**  
**Any unsigned version is a working draft, not binding.**

