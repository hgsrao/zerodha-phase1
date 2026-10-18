# PHASE 2A: Harness Build — Detailed Instructions
## Date: Aug 29, 2026 (Morning)
## Machine: Desktop PC
## Duration: 1.5-2 hours
## Status: READY TO EXECUTE

---

## APPROVAL TIMESTAMP
- **Owner Approval:** YES (2026-08-28, 22:00 IST)
- **Preregistration:** FROZEN
- **Token Budget:** Allocated (120 tokens for reviews)
- **Go/No-Go:** **GO**

---

## PACKAGE 2A-1: Model 0 Ridge Regression Harness
**Duration:** 30 minutes  
**Start Time:** 09:00 IST

### Task
Build Python harness for Ridge regression (Model 0) based on frozen preregistration.

### Input Files
- `ITEM_3B_PREREGISTRATION_FROZEN_20260828.md` (frozen config)
- `daily_multi_timescale_fusion_panel_20260825.csv` (55 columns, 5,928 rows)
- `p1_p2_state_decomposition_20260825.csv` (31 columns, 5,944 rows)

### Output Files (Save to project root)
- **`model_0_ridge_regression.py`** (main harness code)
- **`model_0_ridge_config.json`** (hyperparameters frozen)

### Code Requirements
1. **Imports:** sklearn.linear_model.Ridge, pandas, numpy, json
2. **Configuration:**
   - Regularization: L2 Ridge
   - Lambda (α): 0.01
   - Fit intercept: True
   - Normalize: False (done in preprocessing)
3. **Functions:**
   - `load_data(train_start, train_end)` → DataFrame
   - `preprocess(df)` → standardized features
   - `train_model(X_train, y_train)` → fitted model object
   - `predict(model, X_val)` → predictions
   - `compute_rank_ic(predictions, actuals)` → float (Rank IC)
4. **No execution yet** (just code structure)

### Acceptance Criteria
- ✅ Python file has no syntax errors
- ✅ All imports resolve
- ✅ Functions defined but not called
- ✅ Config JSON is valid (parseable)

### Success Output
```
✓ model_0_ridge_regression.py created
✓ model_0_ridge_config.json created
✓ Code compiles (python -m py_compile model_0_ridge_regression.py)
```

---

## PACKAGE 2A-2: Model 1 XGBoost Harness
**Duration:** 30 minutes  
**Start Time:** 09:30 IST

### Task
Build Python harness for XGBoost (Model 1) based on frozen preregistration.

### Input Files
- Preregistration (Model 1 specs)
- Same data CSVs as 2A-1

### Output Files (Save to project root)
- **`model_1_xgboost.py`** (main harness code)
- **`model_1_xgboost_config.json`** (hyperparameters frozen)

### Code Requirements
1. **Imports:** xgboost, pandas, numpy, json
2. **Configuration (from preregistration):**
   - Algorithm: XGBoost
   - n_estimators: 500
   - learning_rate: 0.05
   - max_depth: 5
   - subsample: 0.8
   - colsample_bytree: 0.8
   - lambda (L2): 1.0
   - gamma: 0.1
   - early_stopping_rounds: 50
   - objective: reg:squarederror
3. **Functions:**
   - `load_data(train_start, train_end)` → DataFrame
   - `preprocess(df)` → standardized features
   - `train_model(X_train, y_train, X_val, y_val)` → fitted model object
   - `predict(model, X_val)` → predictions
   - `compute_rank_ic(predictions, actuals)` → float
4. **No execution yet** (just code structure)

### Acceptance Criteria
- ✅ Python file has no syntax errors
- ✅ All imports resolve (xgboost installed?)
- ✅ Functions defined but not called
- ✅ Config JSON is valid

### Success Output
```
✓ model_1_xgboost.py created
✓ model_1_xgboost_config.json created
✓ Code compiles
```

---

## PACKAGE 2A-3: Harness Validation Test
**Duration:** 30 minutes  
**Start Time:** 10:00 IST

### Task
Run both harnesses on a small sample (first 100 rows) to verify they execute without error.

### Input Files
- model_0_ridge_regression.py
- model_1_xgboost.py
- Data CSVs (use first 100 rows only for this test)

### Execution Steps
1. Load data (first 100 rows)
2. Preprocess data
3. Train Model 0 on first 50 rows
4. Predict on rows 51-100
5. Compute Rank IC
6. Train Model 1 on first 50 rows
7. Predict on rows 51-100
8. Compute Rank IC
9. Save metrics to JSON

### Output Files (Save to project root)
- **`harness_validation_report.md`** (text summary)
- **`harness_test_metrics.json`** (metrics from test run)

### Report Requirements (harness_validation_report.md)
```markdown
# Harness Validation Report
**Date:** 2026-08-29  
**Data:** First 100 rows (test sample)

## Model 0 (Ridge)
- Training: PASS/FAIL
- Prediction: PASS/FAIL
- Rank IC: [value or error]
- Notes: [any issues?]

## Model 1 (XGBoost)
- Training: PASS/FAIL
- Prediction: PASS/FAIL
- Rank IC: [value or error]
- Notes: [any issues?]

## Metrics JSON
- All metrics present: YES/NO
- No NaN values: YES/NO
- Valid JSON: YES/NO

## Gate Decision
PASS / HOLD / FAIL
```

### Acceptance Criteria
- ✅ Both harnesses execute without crashes
- ✅ Rank IC computed for both models
- ✅ Metrics JSON is valid (no NaN at top level)
- ✅ No data leakage warnings
- ✅ Report clearly states PASS or FAIL

### Success Output
```
✓ harness_validation_report.md created
✓ harness_test_metrics.json created
✓ Gate: PASS (ready for Phase 2B)
```

---

## PACKAGE 2A-4: Data Integrity Check
**Duration:** 20 minutes  
**Start Time:** 10:30 IST (can run in parallel with 2A-3)

### Task
Verify that train/validation/holdout splits are correct per frozen preregistration.

### Input Files
- Data CSVs
- Preregistration (split dates: Aug 2023-Apr 2025 / May-Aug 2025 / Sep 2025-Aug 2026)

### Checks
1. Count rows in each split
   - Training: Should be ~480 trading days
   - Validation: Should be ~90 trading days
   - Holdout: Should be ~180 trading days
2. Verify no overlap (no date appears in multiple splits)
3. Verify chronological order (no look-ahead)
4. Verify no duplicate rows within splits
5. Verify target dates are D+1 onward (no same-day leakage)

### Output Files (Save to project root)
- **`data_split_verification.json`**

### JSON Structure
```json
{
  "training_set": {
    "start_date": "2023-08-25",
    "end_date": "2025-04-30",
    "row_count": 480,
    "status": "PASS"
  },
  "validation_set": {
    "start_date": "2025-05-01",
    "end_date": "2025-08-29",
    "row_count": 90,
    "status": "PASS"
  },
  "holdout_set": {
    "start_date": "2025-09-01",
    "end_date": "2026-08-29",
    "row_count": 180,
    "status": "PASS"
  },
  "overlap_check": "PASS",
  "leakage_check": "PASS",
  "duplicate_check": "PASS",
  "overall_status": "PASS"
}
```

### Acceptance Criteria
- ✅ All splits have correct row counts (±5% tolerance)
- ✅ No overlap between splits
- ✅ No leakage detected
- ✅ No duplicates within splits
- ✅ Overall status: PASS

---

## PHASE 2A SUMMARY

| Package | Duration | Output | Status |
|---------|----------|--------|--------|
| 2A-1 | 30 min | model_0_ridge_regression.py | ✅ Code ready |
| 2A-2 | 30 min | model_1_xgboost.py | ✅ Code ready |
| 2A-3 | 30 min | harness_validation_report.md | ✅ Tests pass |
| 2A-4 | 20 min | data_split_verification.json | ✅ Data verified |
| **Total** | **1.5-2h** | **4 files** | **✅ READY** |

---

## GATE CHECKPOINT (10:55 IST)

**Claude Review (5 minutes):**
1. Read `harness_validation_report.md`
2. Validate `harness_test_metrics.json` (no NaN, valid JSON)
3. Validate `data_split_verification.json` (all splits correct)
4. **Decision:** PASS → Proceed to Phase 2B | HOLD → Debug

**Result:**
- ✅ PASS → Phase 2B begins at 14:00 IST (Model 0/1 training)
- ❌ FAIL → Fix issues and rerun 2A-3

---

## CRITICAL REMINDERS

1. **No execution yet** — Only code structure, no model training
2. **Use frozen config** — Every parameter from preregistration, no changes
3. **Save to project root** — All files in `/C:/Users/Dishan/Documents/Codex/Zerodha_live_bot_3.4_ENTRY_UNKNOWN/`
4. **Test on small sample** — First 100 rows only, not full dataset
5. **Document everything** — Every output file is reviewed by Claude

---

## IF SOMETHING BREAKS

**Error:** Python import fails (xgboost not found)
→ Install: `pip install xgboost`

**Error:** Data file not found
→ Verify file paths in preregistration

**Error:** Metrics contain NaN
→ Check data preprocessing (missing values?)

**Error:** Harness validation fails
→ Debug model training logic, do NOT proceed to Phase 2B

---

## NEXT PHASE (if PASS)

**Phase 2B starts at 14:00 IST (Aug 29, afternoon):**
- Package 2B-1: Model 0 full training (2 hours)
- Package 2B-2: Model 0 validation eval (1 hour)
- Package 2B-3: Model 1 full training (2 hours)
- Package 2B-4: Model 1 validation eval (1 hour)
- Package 2B-5: Tier 1-3 summary (30 min)

**Parallel (Razer):**
- Phase 3A: Feature provenance analysis
- Phase 3B: Universe expansion planning

---

## READY TO BEGIN?

**Start time:** 09:00 IST (Aug 29, 2026)  
**Duration:** 1.5-2 hours  
**Machine:** Desktop PC  
**Status:** ✅ **GO**

All files prepared. All inputs available. All config frozen.

**Proceed with Package 2A-1.**

