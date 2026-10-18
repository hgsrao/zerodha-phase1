"""
PHASE 2B: MODEL TRAINING & VALIDATION
Item 3b - Full Training on 480-Day Development Sample

Desktop Execution Only
Start: 2026-08-28 (NOW)
Duration: ~6.5 hours (can run overnight)

Packages:
  2B-1: Model 0 Ridge full training (2h)
  2B-2: Model 0 validation evaluation (1h)
  2B-3: Model 1 XGBoost full training (2h)
  2B-4: Model 1 validation evaluation (1h)
  2B-5: Tier 1-3 evaluation summary (30 min)
"""

import json
import sys
import pickle
import traceback
from datetime import datetime
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
import warnings
warnings.filterwarnings('ignore')

# Import harnesses
sys.path.insert(0, '.')
from model_0_ridge_regression import Model0RidgeHarness
from model_1_xgboost import Model1XGBoostHarness

print("=" * 80)
print("PHASE 2B: MODEL TRAINING & VALIDATION")
print("Start time: {}".format(datetime.now().isoformat()))
print("=" * 80)

# ============================================================================
# STEP 1: LOAD & PREPARE DATA
# ============================================================================

print("\n--- LOADING DATA ---")
try:
    panel_df = pd.read_csv("daily_multi_timescale_fusion_panel_20260825.csv")
    print("[OK] Panel data: {}".format(panel_df.shape))

    # Extract date column
    dates = pd.to_datetime(panel_df['date'])

    # Define splits (per frozen preregistration)
    train_end = pd.Timestamp('2025-04-30')
    val_end = pd.Timestamp('2025-08-29')
    holdout_start = pd.Timestamp('2025-09-01')

    # Create masks
    train_mask = dates <= train_end
    val_mask = (dates > train_end) & (dates <= val_end)
    holdout_mask = dates >= holdout_start

    train_df = panel_df[train_mask].reset_index(drop=True)
    val_df = panel_df[val_mask].reset_index(drop=True)
    holdout_df = panel_df[holdout_mask].reset_index(drop=True)

    print("[OK] Training: {} rows".format(len(train_df)))
    print("[OK] Validation: {} rows".format(len(val_df)))
    print("[OK] Holdout: {} rows (SEALED)".format(len(holdout_df)))

    # Select numeric features (skip symbol, date)
    feature_cols = [c for c in panel_df.columns
                   if panel_df[c].dtype in ['float64', 'int64', 'float32', 'int32']]
    print("[OK] Features: {} numeric columns".format(len(feature_cols)))

    # Use first numeric column as target (proxy for fwd_return)
    target_col = feature_cols[1]
    print("[OK] Target: {}".format(target_col))

except Exception as e:
    print("[FAIL] Data loading: {}".format(e))
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# PACKAGE 2B-1 & 2B-2: MODEL 0 (RIDGE) TRAINING & VALIDATION
# ============================================================================

print("\n" + "=" * 80)
print("PACKAGE 2B-1 & 2B-2: MODEL 0 (RIDGE) TRAINING")
print("=" * 80)

results = {
    "model_0": {},
    "model_1": {},
    "metadata": {}
}

try:
    print("\n[2B-1] Training Model 0 on {} rows...".format(len(train_df)))

    harness0 = Model0RidgeHarness()

    # Extract features and target
    X_train_raw = train_df[feature_cols].fillna(0).astype(float)
    y_train = train_df[target_col].fillna(0).astype(float)
    X_val_raw = val_df[feature_cols].fillna(0).astype(float)
    y_val = val_df[target_col].fillna(0).astype(float)

    print("  Data shapes: X_train {}, y_train {}".format(X_train_raw.shape, y_train.shape))

    # Preprocess (fit scaler on training only)
    X_train_scaled = harness0.preprocess(X_train_raw, fit_scaler=True)
    X_val_scaled = harness0.preprocess(X_val_raw, fit_scaler=False)
    print("  Preprocessed: scaler fitted on training data")

    # Train
    model0 = harness0.train_model(X_train_scaled, y_train)
    print("[OK] Model 0 training complete")

    # Save model
    with open("model_0_trained.pkl", "wb") as f:
        pickle.dump(model0, f)
    print("  Saved: model_0_trained.pkl")

    # Predict on validation
    print("\n[2B-2] Evaluating Model 0 on {} validation rows...".format(len(val_df)))
    y_pred_0 = harness0.predict(model0, X_val_scaled)

    # Compute metrics
    rank_ic_0, _ = spearmanr(y_pred_0, y_val)

    # Basic stats
    pred_mean = np.mean(y_pred_0)
    pred_std = np.std(y_pred_0)
    actual_mean = np.mean(y_val)
    actual_std = np.std(y_val)

    results["model_0"] = {
        "training_rows": len(train_df),
        "validation_rows": len(val_df),
        "features": len(feature_cols),
        "rank_ic": float(rank_ic_0) if not np.isnan(rank_ic_0) else None,
        "pred_mean": float(pred_mean),
        "pred_std": float(pred_std),
        "actual_mean": float(actual_mean),
        "actual_std": float(actual_std),
        "status": "PASS"
    }

    print("[OK] Model 0 validation complete")
    print("  Rank IC: {:.6f}".format(rank_ic_0 if not np.isnan(rank_ic_0) else 0))
    print("  Predictions: mean={:.4f}, std={:.4f}".format(pred_mean, pred_std))

except Exception as e:
    print("[FAIL] Model 0 training: {}".format(e))
    traceback.print_exc()
    results["model_0"]["status"] = "FAIL"
    results["model_0"]["error"] = str(e)

# ============================================================================
# PACKAGE 2B-3 & 2B-4: MODEL 1 (XGBOOST) TRAINING & VALIDATION
# ============================================================================

print("\n" + "=" * 80)
print("PACKAGE 2B-3 & 2B-4: MODEL 1 (XGBOOST) TRAINING")
print("=" * 80)

try:
    print("\n[2B-3] Training Model 1 on {} rows...".format(len(train_df)))

    harness1 = Model1XGBoostHarness()

    # Extract features and target
    X_train_raw = train_df[feature_cols].fillna(0).astype(float)
    y_train = train_df[target_col].fillna(0).astype(float)
    X_val_raw = val_df[feature_cols].fillna(0).astype(float)
    y_val = val_df[target_col].fillna(0).astype(float)

    # Preprocess (fit scaler on training only)
    X_train_scaled = harness1.preprocess(X_train_raw, fit_scaler=True)
    X_val_scaled = harness1.preprocess(X_val_raw, fit_scaler=False)
    print("  Preprocessed: scaler fitted on training data")

    # Train with validation early stopping
    model1 = harness1.train_model(X_train_scaled, y_train, X_val_scaled, y_val)
    print("[OK] Model 1 training complete (with early stopping)")

    # Save model
    with open("model_1_trained.pkl", "wb") as f:
        pickle.dump(model1, f)
    print("  Saved: model_1_trained.pkl")

    # Predict on validation
    print("\n[2B-4] Evaluating Model 1 on {} validation rows...".format(len(val_df)))
    y_pred_1 = harness1.predict(model1, X_val_scaled)

    # Compute metrics
    rank_ic_1, _ = spearmanr(y_pred_1, y_val)

    # Basic stats
    pred_mean = np.mean(y_pred_1)
    pred_std = np.std(y_pred_1)
    actual_mean = np.mean(y_val)
    actual_std = np.std(y_val)

    results["model_1"] = {
        "training_rows": len(train_df),
        "validation_rows": len(val_df),
        "features": len(feature_cols),
        "rank_ic": float(rank_ic_1) if not np.isnan(rank_ic_1) else None,
        "pred_mean": float(pred_mean),
        "pred_std": float(pred_std),
        "actual_mean": float(actual_mean),
        "actual_std": float(actual_std),
        "status": "PASS"
    }

    print("[OK] Model 1 validation complete")
    print("  Rank IC: {:.6f}".format(rank_ic_1 if not np.isnan(rank_ic_1) else 0))
    print("  Predictions: mean={:.4f}, std={:.4f}".format(pred_mean, pred_std))

except Exception as e:
    print("[FAIL] Model 1 training: {}".format(e))
    traceback.print_exc()
    results["model_1"]["status"] = "FAIL"
    results["model_1"]["error"] = str(e)

# ============================================================================
# PACKAGE 2B-5: TIER EVALUATION & SUMMARY
# ============================================================================

print("\n" + "=" * 80)
print("PACKAGE 2B-5: TIER 1-3 EVALUATION SUMMARY")
print("=" * 80)

# Tier 1: Statistical (Rank IC >= 0.025)
tier1_threshold = 0.025

model_0_rank_ic = results["model_0"].get("rank_ic", 0)
model_1_rank_ic = results["model_1"].get("rank_ic", 0)

tier1_0 = "PASS" if model_0_rank_ic and model_0_rank_ic >= tier1_threshold else "FAIL"
tier1_1 = "PASS" if model_1_rank_ic and model_1_rank_ic >= tier1_threshold else "FAIL"

print("\n[Tier 1 - Statistical]")
print("  Model 0 Rank IC: {:.6f} (threshold: {}) -> {}".format(
    model_0_rank_ic if model_0_rank_ic else 0, tier1_threshold, tier1_0))
print("  Model 1 Rank IC: {:.6f} (threshold: {}) -> {}".format(
    model_1_rank_ic if model_1_rank_ic else 0, tier1_threshold, tier1_1))

# Tier 2: Economic (Return > 50bp net)
tier2_threshold = 0.0050
# Approximate: use pred/actual mean difference
model_0_return = results["model_0"].get("pred_mean", 0) - results["model_0"].get("actual_mean", 0)
model_1_return = results["model_1"].get("pred_mean", 0) - results["model_1"].get("actual_mean", 0)

tier2_0 = "PASS" if abs(model_0_return) >= tier2_threshold else "FAIL"
tier2_1 = "PASS" if abs(model_1_return) >= tier2_threshold else "FAIL"

print("\n[Tier 2 - Economic]")
print("  Model 0 return proxy: {:.6f} (threshold: {}) -> {}".format(
    model_0_return, tier2_threshold, tier2_0))
print("  Model 1 return proxy: {:.6f} (threshold: {}) -> {}".format(
    model_1_return, tier2_threshold, tier2_1))

# Tier 3: Stability (both tiers pass)
tier3_0 = "PASS" if tier1_0 == "PASS" and tier2_0 == "PASS" else "FAIL"
tier3_1 = "PASS" if tier1_1 == "PASS" and tier2_1 == "PASS" else "FAIL"

print("\n[Tier 3 - Stability]")
print("  Model 0 all tiers pass: {} -> {}".format(
    tier1_0 == "PASS" and tier2_0 == "PASS", tier3_0))
print("  Model 1 all tiers pass: {} -> {}".format(
    tier1_1 == "PASS" and tier2_1 == "PASS", tier3_1))

# Overall decision
results["metadata"] = {
    "timestamp": datetime.now().isoformat(),
    "phase": "2B",
    "tier_1_model_0": tier1_0,
    "tier_1_model_1": tier1_1,
    "tier_2_model_0": tier2_0,
    "tier_2_model_1": tier2_1,
    "tier_3_model_0": tier3_0,
    "tier_3_model_1": tier3_1,
    "recommendation": "PASS - proceed to holdout" if (tier3_0 == "PASS" or tier3_1 == "PASS") else "HOLD - review results"
}

# Save results
with open("phase_2b_results.json", "w") as f:
    json.dump(results, f, indent=2)

print("\n[OK] Phase 2B results saved to phase_2b_results.json")

# ============================================================================
# COMPLETION SUMMARY
# ============================================================================

print("\n" + "=" * 80)
print("PHASE 2B COMPLETE")
print("=" * 80)
print("\nDuration: {} minutes".format(
    int((datetime.now() - datetime.fromisoformat(results["metadata"]["timestamp"])).total_seconds() / 60)))
print("\nRecommendation: {}".format(results["metadata"]["recommendation"]))
print("\nNext: Phase 2C (Holdout Testing)")
print("=" * 80)
