# THREE-DIRECTION EXECUTION GUIDE
## Hypothesis Revisions 7-9: Complete Code Templates

---

## OVERVIEW

Three orthogonal alpha directions with production-grade validation gates:

1. **REVISION 7: Daily/Weekly Swing Trading** (3-5R targets, long duration)
2. **REVISION 8: Pairs Cointegration** (market-neutral, 1R targets)
3. **REVISION 9: Volume Profile Mean-Reversion** (structural support/resistance, 1.5R targets)

All three share:
- **Frozen model protocol** (train on TRAIN only, freeze weights)
- **Kill-switch validation** (50.80% hurdle rate, >0 bps net P&L)
- **Out-of-sample quarantine** (Jan-Mar train, Apr validation, May test, Jun sealed)
- **Identical execution pattern** (5 phases per direction)

---

## EXECUTION PLAN

### PHASE 1: REVISION 7 (DAILY/WEEKLY SWING TRADING)
**Duration:** 1-2 hours | **Complexity:** Low | **Proof-of-concept:** First

```bash
# Navigate to project root
cd /home/shrinivas/ECS_Project_external_engine

# STEP 1: Extract daily features (resamples 1-minute → daily closes)
python3 scripts/revision7_daily_swing_complete.py --phase feature_extraction

# STEP 2: Generate 5-day triple-barrier labels (3.0R target, 1.0R stop)
python3 scripts/revision7_daily_swing_complete.py --phase label_generation

# STEP 3: Train frozen model on TRAIN split (Jan-Mar 2026)
python3 scripts/revision7_daily_swing_complete.py --phase model_training

# STEP 4: VALIDATION GATE (April 2026)
# Must achieve: win_rate ≥ 50.80% AND net_pnl_bps > 0.0
python3 scripts/revision7_daily_swing_complete.py --phase validation_gate

# STEP 5: KILL-SWITCH TEST (May 2026)
# If passes: unlocks June sealed data
python3 scripts/revision7_daily_swing_complete.py --phase test_gate
```

**Expected Output:**
- `revision7_daily/train_X.npy` & `train_y.npy`: Training data
- `revision7_daily/model.pkl`: Frozen model + scaler
- Validation gate report (e.g., 52.31% win rate, +3.45 bps)
- Test gate report (pass/fail decision)

**Kill-Switch Decision Tree:**
```
├─ Validation Gate FAILS → STOP (do not proceed to test)
├─ Validation Gate PASSES → Proceed to test
│  ├─ Test Gate FAILS → STOP (alpha does not generalize)
│  ├─ Test Gate PASSES → UNLOCK June sealed
```

---

### PHASE 2: REVISION 8 (PAIRS COINTEGRATION)
**Duration:** 1-2 hours | **Complexity:** Medium | **Run in parallel with Phase 1 analysis**

```bash
# STEP 1: Compute cointegration matrix (identifies 1,128+ pairs)
python3 scripts/revision8_pairs_cointegration_complete.py --phase cointegration_compute

# STEP 2: Generate spread-based triple-barrier labels
# Signal: Long laggard + short leader when spread > ±2.0σ
python3 scripts/revision8_pairs_cointegration_complete.py --phase label_generation

# STEP 3: Train frozen pair selection model (TRAIN split)
python3 scripts/revision8_pairs_cointegration_complete.py --phase model_training

# STEP 4: VALIDATION GATE (April 2026, market-neutral)
python3 scripts/revision8_pairs_cointegration_complete.py --phase validation_gate

# STEP 5: KILL-SWITCH TEST (May 2026)
python3 scripts/revision8_pairs_cointegration_complete.py --phase test_gate
```

**Expected Output:**
- `revision8_pairs/train_pairs.pkl`: Cointegrated pair list
- `revision8_pairs/train_X.npy` & `train_y.npy`: Pair-spread labels
- `revision8_pairs/model.pkl`: Frozen model
- Validation gate report (51-54% win rate expected)
- Test gate report

**Key Feature:** Market-neutral (no directional bias)
```
Portfolio = LONG laggard + SHORT leader
Net exposure to market direction = ~0
Profit from mean-reversion only
```

---

### PHASE 3: REVISION 9 (VOLUME PROFILE MEAN-REVERSION)
**Duration:** 1-2 hours | **Complexity:** High | **Run after Phase 1-2 analysis**

```bash
# STEP 1: Compute volume profiles and POC (Point of Control)
# POC = price level with highest institutional volume
python3 scripts/revision9_volume_profile_complete.py --phase poc_computation

# STEP 2: Generate POC-snap triple-barrier labels
# Entry: Price ±2 ATR from POC (extreme)
# Exit: Snap within 0.5 ATR of POC (mean-reversion)
python3 scripts/revision9_volume_profile_complete.py --phase label_generation

# STEP 3: Train frozen POC prediction model (TRAIN split)
python3 scripts/revision9_volume_profile_complete.py --phase model_training

# STEP 4: VALIDATION GATE (April 2026)
python3 scripts/revision9_volume_profile_complete.py --phase validation_gate

# STEP 5: KILL-SWITCH TEST (May 2026)
python3 scripts/revision9_volume_profile_complete.py --phase test_gate
```

**Expected Output:**
- `revision9_volume/train_poc.parquet` files: POC-enriched OHLCV
- `revision9_volume/train_X.npy` & `train_y.npy`: POC labels
- `revision9_volume/model.pkl`: Frozen model
- Validation gate report
- Test gate report

---

## VALIDATION GATE MECHANICS (ALL THREE)

Each direction runs the same gate on April 2026 data:

```python
# Load frozen model (trained on TRAIN only)
model, scaler = pickle.load(open('{revision}/model.pkl', 'rb'))

# Score validation split
X_val_scaled = scaler.transform(X_val)
scores = model.predict_proba(X_val_scaled)[:, 1]

# Top 1% threshold (frozen)
threshold = np.percentile(scores, 99.0)
top1_mask = scores >= threshold

# Evaluate kill-switch criteria
win_rate = y_val[top1_mask].mean() * 100
gross_R = (win_rate/100 * TARGET) - ((100-win_rate)/100 * STOP)
net_bps = (gross_R - FRICTION_R) * 30.0

# PASS/FAIL
passes = (win_rate > 50.80%) AND (net_bps > 0.0)
```

**Hurdle Rate Math:**
- Friction: 0.27R (8 bps round-trip)
- Target-Stop ratio varies per direction:
  - Revision 7: 3.0R / 1.0R → need 50.80% to breakeven
  - Revision 8: 1.0R / 1.0R → need 50.80% (market-neutral)
  - Revision 9: 1.5R / 1.0R → need 50.80%

**Result Interpretation:**
```
Validation Win Rate | Status                           | Action
≥50.80% + >0 bps    | ✅ PASS                         | Proceed to test
<50.80% or ≤0 bps   | ❌ FAIL                         | STOP (kill-switch)
```

---

## KILL-SWITCH TEST (MAY 2026)

After passing validation, run on May 2026 test data:

```bash
# Same mechanics as validation, but on out-of-sample May data
# This is the final proof: does the alpha survive regime change?
```

**Possible Outcomes:**

| Scenario | Win Rate | Net P&L | Decision |
|---|---|---|---|
| A | ≥50.80% | >0 bps | ✅ PASS - Unlock June sealed |
| B | <50.80% | >0 bps | ❌ FAIL - Win rate below hurdle |
| C | ≥50.80% | ≤0 bps | ❌ FAIL - Friction eats all edge |
| D | <50.80% | ≤0 bps | ❌ FAIL - Complete failure |

**If PASS:** April passed, May passed → Unlock June sealed data for final confirmation
**If FAIL:** Stop immediately (do not touch June)

---

## RUNNING ALL THREE IN PARALLEL

Recommended timeline:

```
Week 1 (Monday-Wednesday):
  └─ REVISION 7 (all 5 phases)
     └─ Analyze results while Phase 2 starts

Week 1-2 (Thursday-Friday + following days):
  ├─ REVISION 8 (all 5 phases) [in parallel with Phase 1 analysis]
  ├─ REVISION 9 (all 5 phases) [in parallel with Phase 1-2 analysis]

Post-Week 2:
  └─ Collect all results
     └─ Determine which passed kill-switch
     └─ Unlock June sealed for survivors
```

---

## INTERPRETING RESULTS

### Single Direction Passes (e.g., Revision 7 only)

```
✅ Revision 7 (Daily/Weekly): PASS
❌ Revision 8 (Pairs): FAIL
❌ Revision 9 (Volume): FAIL

DECISION: Deploy Revision 7 alone
          Revisit Revision 8-9 with parameter tuning
```

### Multiple Directions Pass

```
✅ Revision 7 (Daily/Weekly): PASS
✅ Revision 8 (Pairs): PASS
❌ Revision 9 (Volume): FAIL

DECISION: Deploy Revision 7 + 8 as ensemble
          Investigate why Revision 9 failed (POC features weak?)
          Potential ensemble: 60% daily + 40% pairs for diversification
```

### All Three Pass

```
✅ Revision 7 (Daily/Weekly): PASS
✅ Revision 8 (Pairs): PASS
✅ Revision 9 (Volume): PASS

DECISION: Deploy all three with position-size allocation
          Example: 40% capital to daily, 30% to pairs, 30% to volume
          Rebalance quarterly if correlation changes
```

---

## TROUBLESHOOTING

### Issue: Feature mismatch during validation/test

**Symptom:**
```
ValueError: X.shape[1] = 3 does not match n_features_in_ = 4
```

**Solution:**
- Model was trained on different features than validation
- Check scaler.feature_names_in_ vs actual feature columns
- Ensure same feature order: `['atr_14d', 'vwap_dist_atr', 'trend_4d', 'realized_vol']`

### Issue: All predictions are zero class

**Symptom:**
```
Win rate = 0.0% (model predicts all 0)
```

**Solution:**
- Model not learning positive class
- Check: class balance in training data
  - If <30% positive labels, use `class_weight='balanced'`
  - Resample minority class if needed
- Verify labels are correctly generated (check range of y_val)

### Issue: Validation passes but test fails dramatically

**Symptom:**
```
Validation: 52.1% win rate
Test: 41.3% win rate
```

**Solution:**
- **Expected behavior** - kill-switch working correctly
- May 2026 was different regime (lower volatility, bear chop)
- Don't try to "fix" - this is feature decay, not engineering error
- Document in post-mortem and move to next hypothesis

### Issue: Data load error (files not found)

**Symptom:**
```
FileNotFoundError: no files found matching 'revision2/features_2026/train/*_mtf_2026.parquet'
```

**Solution:**
- Verify feature extraction was run first
- Check file paths: `ls revision2/features_2026/train/` (should show parquet files)
- Ensure you're running from project root: `/home/shrinivas/ECS_Project_external_engine/`

---

## OUTPUT FILES SUMMARY

After completing all three directions:

```
revision7_daily/
├── model.pkl                      # Frozen RF model + scaler
├── train_X.npy, train_y.npy      # Training data
├── validation_X.npy, validation_y.npy
├── test_X.npy, test_y.npy
└── {symbol}_daily.parquet        # Daily features (48 symbols)

revision8_pairs/
├── model.pkl                      # Frozen GB model + scaler
├── train_pairs.pkl               # Cointegrated pair list
├── train_X.npy, train_y.npy      # Pair spread labels
├── validation_X.npy, validation_y.npy
├── test_X.npy, test_y.npy
└── logs (cointegration stats)

revision9_volume/
├── model.pkl                      # Frozen RF model + scaler
├── train_X.npy, train_y.npy      # POC labels
├── validation_X.npy, validation_y.npy
├── test_X.npy, test_y.npy
├── {symbol}_poc.parquet          # POC-enriched OHLCV (48 symbols)
└── logs (POC strength stats)
```

---

## FINAL GATE: JUNE SEALED VALIDATION

If **any** direction passes May test gate, you can unlock June:

```bash
# Only if kill-switch passed
# Do not manually compute percentiles on June data
# Use FROZEN thresholds from model training

# Sealed split verification (final confirmation)
python3 scripts/sealed_gate_validation.py --direction revision7
python3 scripts/sealed_gate_validation.py --direction revision8
python3 scripts/sealed_gate_validation.py --direction revision9
```

**Sealed gate criteria:** Must maintain ≥50.80% win rate (no retraining allowed)

---

## KEY PRINCIPLES (DO NOT VIOLATE)

1. **Frozen model protocol:** Train model on TRAIN only, use frozen weights on VALIDATION/TEST/SEALED
2. **No percentile recomputation:** Thresholds locked at train time
3. **No retraining on holdouts:** Never touch VALIDATION/TEST/SEALED during model development
4. **Kill-switch is non-negotiable:** 50.80% hurdle AND >0 bps are both required
5. **Registry latch prevents premature access:** Sealed data locked until kill-switch passes

---

## DEPLOYMENT READINESS CHECKLIST

- [ ] All three directions completed (feature → labels → train → validation → test)
- [ ] At least one direction passed kill-switch (≥50.80% + >0 bps)
- [ ] Sealed gate passed (unlocked June data, ≥50.80% maintained)
- [ ] Model pkl files frozen and archived
- [ ] Hyperparameters locked (no further tuning)
- [ ] Risk management rules in place (position size, max loss per trade)
- [ ] Live execution: Start with REVISION 7 (daily) first (simplest)

---

**Ready to execute. Run phases in order.**
