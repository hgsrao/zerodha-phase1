"""
PHASE 2C: HOLDOUT TESTING & ITEM 3B CLOSURE
Final Out-of-Sample Validation

Desktop Execution Only
Input: Trained models from Phase 2B (model_0_trained.pkl, model_1_trained.pkl)
Packages:
  2C-1: Holdout prediction on 180-day sealed set
  2C-2: Holdout metrics & comparison with validation
  2C-3: Item 3b closure report & recommendation
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

print("=" * 80)
print("PHASE 2C: HOLDOUT TESTING & ITEM 3B CLOSURE")
print("Start time: {}".format(datetime.now().isoformat()))
print("=" * 80)

# ============================================================================
# LOAD TRAINED MODELS & DATA
# ============================================================================

print("\n--- LOADING TRAINED MODELS ---")
try:
    with open("model_0_trained.pkl", "rb") as f:
        model0 = pickle.load(f)
    print("[OK] Model 0 loaded (Ridge)")

    with open("model_1_trained.pkl", "rb") as f:
        model1 = pickle.load(f)
    print("[OK] Model 1 loaded (XGBoost)")
except Exception as e:
    print("[FAIL] Could not load models: {}".format(e))
    sys.exit(1)

print("\n--- LOADING DATA ---")
try:
    panel_df = pd.read_csv("daily_multi_timescale_fusion_panel_20260825.csv")
    dates = pd.to_datetime(panel_df['date'])

    # Define splits
    train_end = pd.Timestamp('2025-04-30')
    val_end = pd.Timestamp('2025-08-29')
    holdout_start = pd.Timestamp('2025-09-01')

    # Get all splits
    train_mask = dates <= train_end
    val_mask = (dates > train_end) & (dates <= val_end)
    holdout_mask = dates >= holdout_start

    train_df = panel_df[train_mask].reset_index(drop=True)
    val_df = panel_df[val_mask].reset_index(drop=True)
    holdout_df = panel_df[holdout_mask].reset_index(drop=True)

    print("[OK] Training: {} rows".format(len(train_df)))
    print("[OK] Validation: {} rows".format(len(val_df)))
    print("[OK] Holdout (SEALED UNTIL NOW): {} rows".format(len(holdout_df)))

    # Select numeric features
    feature_cols = [c for c in panel_df.columns
                   if panel_df[c].dtype in ['float64', 'int64', 'float32', 'int32']]
    target_col = feature_cols[1]
    print("[OK] Features: {} columns".format(len(feature_cols)))

except Exception as e:
    print("[FAIL] Data loading: {}".format(e))
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# LOAD VALIDATION RESULTS (FOR COMPARISON)
# ============================================================================

print("\n--- LOADING VALIDATION RESULTS ---")
try:
    with open("phase_2b_results.json", "r") as f:
        validation_results = json.load(f)
    print("[OK] Validation results loaded")
except Exception as e:
    print("[FAIL] Could not load validation results: {}".format(e))
    validation_results = None

# ============================================================================
# PACKAGE 2C-1: HOLDOUT PREDICTION
# ============================================================================

print("\n" + "=" * 80)
print("PACKAGE 2C-1: HOLDOUT PREDICTION")
print("=" * 80)

results = {
    "model_0": {},
    "model_1": {},
    "comparison": {},
    "metadata": {}
}

try:
    print("\n[2C-1] Running predictions on {} holdout rows...".format(len(holdout_df)))

    # Extract features and target
    X_holdout_raw = holdout_df[feature_cols].fillna(0).astype(float)
    y_holdout = holdout_df[target_col].fillna(0).astype(float)

    print("  Holdout data: X shape {}, y shape {}".format(X_holdout_raw.shape, y_holdout.shape))

    # Model 0 needs preprocessing (scaler from training)
    from sklearn.preprocessing import StandardScaler
    scaler0 = StandardScaler()
    X_train_raw = train_df[feature_cols].fillna(0).astype(float)
    scaler0.fit(X_train_raw)
    X_holdout_scaled_0 = scaler0.transform(X_holdout_raw)

    y_pred_0 = model0.predict(X_holdout_scaled_0)
    print("[OK] Model 0 predictions: {} values".format(len(y_pred_0)))

    # Model 1 needs same preprocessing
    scaler1 = StandardScaler()
    scaler1.fit(X_train_raw)
    X_holdout_scaled_1 = scaler1.transform(X_holdout_raw)

    y_pred_1 = model1.predict(X_holdout_scaled_1)
    print("[OK] Model 1 predictions: {} values".format(len(y_pred_1)))

except Exception as e:
    print("[FAIL] Holdout prediction: {}".format(e))
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# PACKAGE 2C-2: HOLDOUT METRICS & COMPARISON
# ============================================================================

print("\n" + "=" * 80)
print("PACKAGE 2C-2: HOLDOUT METRICS & COMPARISON")
print("=" * 80)

try:
    print("\n[2C-2] Computing holdout metrics...")

    # Model 0 holdout metrics
    rank_ic_0_holdout, _ = spearmanr(y_pred_0, y_holdout)
    pred_mean_0_holdout = np.mean(y_pred_0)
    pred_std_0_holdout = np.std(y_pred_0)
    actual_mean_holdout = np.mean(y_holdout)
    actual_std_holdout = np.std(y_holdout)

    results["model_0"]["holdout"] = {
        "rank_ic": float(rank_ic_0_holdout) if not np.isnan(rank_ic_0_holdout) else None,
        "pred_mean": float(pred_mean_0_holdout),
        "pred_std": float(pred_std_0_holdout),
        "actual_mean": float(actual_mean_holdout),
        "actual_std": float(actual_std_holdout),
        "rows": len(holdout_df)
    }

    # Model 1 holdout metrics
    rank_ic_1_holdout, _ = spearmanr(y_pred_1, y_holdout)
    pred_mean_1_holdout = np.mean(y_pred_1)
    pred_std_1_holdout = np.std(y_pred_1)

    results["model_1"]["holdout"] = {
        "rank_ic": float(rank_ic_1_holdout) if not np.isnan(rank_ic_1_holdout) else None,
        "pred_mean": float(pred_mean_1_holdout),
        "pred_std": float(pred_std_1_holdout),
        "actual_mean": float(actual_mean_holdout),
        "actual_std": float(actual_std_holdout),
        "rows": len(holdout_df)
    }

    print("[OK] Model 0 holdout metrics:")
    print("  Rank IC: {:.6f}".format(rank_ic_0_holdout if not np.isnan(rank_ic_0_holdout) else 0))
    print("  Pred mean: {:.4f}, Actual mean: {:.4f}".format(pred_mean_0_holdout, actual_mean_holdout))

    print("[OK] Model 1 holdout metrics:")
    print("  Rank IC: {:.6f}".format(rank_ic_1_holdout if not np.isnan(rank_ic_1_holdout) else 0))
    print("  Pred mean: {:.4f}, Actual mean: {:.4f}".format(pred_mean_1_holdout, actual_mean_holdout))

    # Comparison with validation
    if validation_results:
        val_rank_ic_0 = validation_results["model_0"].get("rank_ic", 0)
        val_rank_ic_1 = validation_results["model_1"].get("rank_ic", 0)

        drift_0 = abs(rank_ic_0_holdout - val_rank_ic_0) / max(abs(val_rank_ic_0), 0.001)
        drift_1 = abs(rank_ic_1_holdout - val_rank_ic_1) / max(abs(val_rank_ic_1), 0.001)

        results["comparison"] = {
            "model_0_val_ic": float(val_rank_ic_0),
            "model_0_holdout_ic": float(rank_ic_0_holdout) if not np.isnan(rank_ic_0_holdout) else None,
            "model_0_drift_pct": float(drift_0 * 100),
            "model_1_val_ic": float(val_rank_ic_1),
            "model_1_holdout_ic": float(rank_ic_1_holdout) if not np.isnan(rank_ic_1_holdout) else None,
            "model_1_drift_pct": float(drift_1 * 100)
        }

        print("\n[Comparison with Validation]")
        print("  Model 0: Val IC={:.6f}, Holdout IC={:.6f}, Drift={:.1f}%".format(
            val_rank_ic_0, rank_ic_0_holdout if not np.isnan(rank_ic_0_holdout) else 0, drift_0 * 100))
        print("  Model 1: Val IC={:.6f}, Holdout IC={:.6f}, Drift={:.1f}%".format(
            val_rank_ic_1, rank_ic_1_holdout if not np.isnan(rank_ic_1_holdout) else 0, drift_1 * 100))

except Exception as e:
    print("[FAIL] Metrics computation: {}".format(e))
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# PACKAGE 2C-3: ITEM 3B CLOSURE REPORT
# ============================================================================

print("\n" + "=" * 80)
print("PACKAGE 2C-3: ITEM 3B CLOSURE REPORT")
print("=" * 80)

try:
    # Determine if holdout results are consistent with validation
    consistency_threshold = 0.10  # 10% drift tolerance

    model_0_consistent = False
    model_1_consistent = False

    if validation_results:
        drift_0 = results["comparison"].get("model_0_drift_pct", 100)
        drift_1 = results["comparison"].get("model_1_drift_pct", 100)

        model_0_consistent = drift_0 < (consistency_threshold * 100)
        model_1_consistent = drift_1 < (consistency_threshold * 100)

    # Final decision logic
    tier1_pass_0 = results["model_0"].get("holdout", {}).get("rank_ic", 0) >= 0.025 if results["model_0"].get("holdout", {}).get("rank_ic") else False
    tier1_pass_1 = results["model_1"].get("holdout", {}).get("rank_ic", 0) >= 0.025 if results["model_1"].get("holdout", {}).get("rank_ic") else False

    item_3b_pass = (tier1_pass_0 or tier1_pass_1) and (model_0_consistent or model_1_consistent)

    recommendation = "PASS - Item 3b validated" if item_3b_pass else "HOLD - Inconsistent with validation"

    results["metadata"] = {
        "timestamp": datetime.now().isoformat(),
        "phase": "2C",
        "holdout_rows": len(holdout_df),
        "model_0_tier1_pass": tier1_pass_0,
        "model_1_tier1_pass": tier1_pass_1,
        "model_0_consistent": model_0_consistent,
        "model_1_consistent": model_1_consistent,
        "item_3b_recommendation": recommendation,
        "next_phase": "Phase 4 (Universe Expansion)" if item_3b_pass else "Re-evaluation"
    }

    print("\n[Item 3b Closure Criteria]")
    print("  Model 0 Tier 1 (Rank IC >= 0.025): {}".format("PASS" if tier1_pass_0 else "FAIL"))
    print("  Model 1 Tier 1 (Rank IC >= 0.025): {}".format("PASS" if tier1_pass_1 else "FAIL"))
    print("  Model 0 Consistency (<10% drift): {}".format("PASS" if model_0_consistent else "FAIL"))
    print("  Model 1 Consistency (<10% drift): {}".format("PASS" if model_1_consistent else "FAIL"))
    print("\n[ITEM 3B DECISION]")
    print("  Recommendation: {}".format(recommendation))
    print("  Next Phase: {}".format(results["metadata"]["next_phase"]))

except Exception as e:
    print("[FAIL] Closure report: {}".format(e))
    traceback.print_exc()
    results["metadata"]["item_3b_recommendation"] = "ERROR"

# ============================================================================
# SAVE RESULTS
# ============================================================================

print("\n[Saving results...]")
with open("phase_2c_holdout_results.json", "w") as f:
    json.dump(results, f, indent=2)

print("[OK] Saved to phase_2c_holdout_results.json")

# ============================================================================
# COMPLETION SUMMARY
# ============================================================================

print("\n" + "=" * 80)
print("PHASE 2C COMPLETE - ITEM 3B DECISION MADE")
print("=" * 80)
print("\nRecommendation: {}".format(results["metadata"].get("item_3b_recommendation", "UNKNOWN")))
print("Next: {}".format(results["metadata"].get("next_phase", "UNKNOWN")))
print("=" * 80)
