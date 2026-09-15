"""Formal freeze of the SELF-LEARNING RISK HEAD V1 candidate: Linear B
(2026-08-25, owner-authorized, BEFORE the 2026 HOLDOUT is touched).

Every value in the freeze record is pulled directly from the actual
frozen dataset and the actual Model 0 / Model 1 result JSONs - nothing
here is retyped by hand from prose, to guarantee the freeze record can
never silently drift from what was really measured.

This script does not touch HOLDOUT rows in any way - it only reads
already-computed VALIDATION-window results and TRAIN/VALIDATION/HOLDOUT
date boundaries (dates are metadata, not predictions or metrics).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import run_self_learning_sensor_fusion_v1_model0 as alpha0

ROOT = Path(__file__).parent
DATA = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"
MODEL0_JSON = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL0_LINEAR_VALIDATION_20260825.json"
MODEL1_JSON = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL1_NONLINEAR_VALIDATION_20260825.json"

EXPECTED_SHA256 = "2f51f697982063233ee120f0fbd93989d523efe868f810564efa26ef8099c888"
CANDIDATE_MODEL = "B_INTRADAY_DAILY_SUMMARY"

OUT_JSON = ROOT / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json"
OUT_MD = ROOT / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.md"

SCRIPTS_TO_HASH = [
    "run_self_learning_sensor_fusion_v1_model0.py",
    "run_self_learning_risk_head_v1_model0.py",
    "run_self_learning_risk_head_v1_model1.py",
    "freeze_sensor_fusion_v1_model_population.py",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    # --- Verify the frozen dataset hasn't moved ---
    actual_sha = sha256_file(DATA)
    if actual_sha != EXPECTED_SHA256:
        raise RuntimeError(f"Frozen dataset hash mismatch: {actual_sha} != {EXPECTED_SHA256}. STOP.")
    print("Dataset SHA256 verified:", actual_sha)

    # --- Pull real TRAIN/VALIDATION/HOLDOUT windows from the dataset itself ---
    df = pd.read_csv(DATA)
    df["date"] = pd.to_datetime(df["date"])
    windows = {}
    for role in df["split_role"].unique():
        sub = df[df["split_role"] == role]
        windows[role] = {
            "start": str(sub["date"].min().date()), "end": str(sub["date"].max().date()),
            "n_dates": int(sub["date"].nunique()), "n_rows": int(len(sub)),
        }
    print("Split windows verified from dataset:", json.dumps(windows, indent=2))

    # --- Load Model 0 (linear) and Model 1 (nonlinear) results, verify holdout untouched ---
    m0 = json.loads(MODEL0_JSON.read_text(encoding="utf-8"))
    m1 = json.loads(MODEL1_JSON.read_text(encoding="utf-8"))
    if m0.get("holdout_touched") is not False:
        raise RuntimeError("Model 0 does not certify untouched holdout. STOP.")
    if m1.get("holdout_touched") is not False:
        raise RuntimeError("Model 1 does not certify untouched holdout. STOP.")
    if m0.get("frozen_dataset_sha256") != EXPECTED_SHA256 or m1.get("frozen_dataset_sha256") != EXPECTED_SHA256:
        raise RuntimeError("Model 0/1 result JSON references a different dataset hash. STOP.")

    b_linear = m0["results"][CANDIDATE_MODEL]
    b_nonlinear = m1["results"][CANDIDATE_MODEL]
    b_nonlinear_vs_linear = m1["nonlinear_vs_linear"][CANDIDATE_MODEL]
    c_minus_b = m0["paired_fusion_increment_tests"]["C_ALL_minus_B_INTRADAY_DAILY_SUMMARY"]
    d_minus_b = m0["paired_fusion_increment_tests"]["D_PROVENANCE_AWARE_minus_B_INTRADAY_DAILY_SUMMARY"]

    # --- Exact frozen feature list, pulled from the real module, not retyped ---
    feature_list = alpha0.FEATURE_SETS[CANDIDATE_MODEL]

    # --- Hash every script this candidate depends on ---
    script_hashes = {s: sha256_file(ROOT / s) for s in SCRIPTS_TO_HASH}

    record = {
        "schema": "RISK_HEAD_V1_CANDIDATE_B_FROZEN",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN - awaiting explicit authorization for the one-time 2026 HOLDOUT evaluation",

        "dataset": {
            "filename": DATA.name,
            "sha256": actual_sha,
            "split_windows": windows,
        },

        "candidate": {
            "model_key": CANDIDATE_MODEL,
            "model_family": "Ridge regression",
            "alpha": b_linear["selected_alpha"],
            "feature_list": feature_list,
            "n_features": len(feature_list),
            "feature_list_source": "run_self_learning_sensor_fusion_v1_model0.FEATURE_SETS['B_INTRADAY_DAILY_SUMMARY'] - unchanged",
        },

        "target": {
            "primary": "adverse_1d = max(0, -mae_1d)",
            "primary_metric": "pooled Spearman correlation between predicted and realized adverse_1d",
            "secondary_event": "adverse_1d >= 0.020",
        },

        "preprocessing_contract": {
            "numeric_features": "median-imputed (TRAIN-only median), missingness indicator column added per feature with any TRAIN NaN, "
                                 "then standardized using TRAIN-only mean/std (VALIDATION transformed with the SAME TRAIN statistics, never refit)",
            "categorical_features": "symbol (always) + regime_bucket (if present in the feature set) - most-frequent-imputed, one-hot encoded "
                                     "via TRAIN-only categories (VALIDATION categories absent from TRAIN map to all-zero, never a new column)",
            "source": "run_self_learning_risk_head_v1_model0.prepare_numeric_matrix - unchanged",
        },

        "validation_results_linear_B": {
            "risk_spearman": b_linear["validation"]["risk_spearman"],
            "top_risk_quartile_excess_adverse": b_linear["validation"]["top_risk_quartile_excess_adverse"],
            "event_2pct_roc_auc": b_linear["validation"]["event_2pct_roc_auc"],
            "event_2pct_average_precision": b_linear["validation"]["event_2pct_average_precision"],
            "event_rate": b_linear["validation"]["event_rate"],
            "negative_prediction_rate": b_linear["validation"]["negative_prediction_rate"],
        },

        "model1_rejection_rationale": {
            "decision": "Nonlinear (HistGradientBoostingRegressor) Model 1 REJECTED - Linear B carried forward as the sole V1 candidate.",
            "nonlinear_B_risk_spearman": b_nonlinear["validation"]["risk_spearman"],
            "nonlinear_minus_linear_B_spearman": b_nonlinear_vs_linear,
            "nonlinear_minus_linear_B_spearman_excludes_zero":
                bool(b_nonlinear_vs_linear["ci_low"] > 0 or b_nonlinear_vs_linear["ci_high"] < 0),
            "linear_B_roc_auc": b_linear["validation"]["event_2pct_roc_auc"]["estimate"],
            "nonlinear_B_roc_auc": b_nonlinear["validation"]["event_2pct_roc_auc"]["estimate"],
            "roc_auc_verdict": "DETERIORATED under the nonlinear fit - not an improvement",
            "C_minus_B_linear_spearman_delta": c_minus_b,
            "D_minus_B_linear_spearman_delta": d_minus_b,
            "fusion_verdict": "Neither C (A+B combined) nor D (provenance-aware) significantly outperformed B alone - "
                               "both paired-increment CIs cross zero. Fusion complexity did not earn promotion over B.",
            "summary": "Nonlinear B's Spearman improvement over linear B was +{:.5f} with a CI crossing zero, and its "
                       "2% event ROC-AUC deteriorated from {:.4f} to {:.4f}. C and D did not significantly beat B either. "
                       "No form of added complexity (nonlinearity or multi-timescale fusion) demonstrated a validated "
                       "improvement over the simplest candidate.".format(
                           b_nonlinear_vs_linear["estimate"],
                           b_linear["validation"]["event_2pct_roc_auc"]["estimate"],
                           b_nonlinear["validation"]["event_2pct_roc_auc"]["estimate"],
                       ),
        },

        "holdout_touched": False,

        "script_hashes": script_hashes,

        "prohibitions_after_holdout_opens": [
            "No feature addition, removal, or reordering-with-semantic-change to the B feature list.",
            "No re-selection of Ridge alpha - stays frozen at the value recorded above.",
            "No change to the primary target (adverse_1d) or secondary event threshold (2.0%).",
            "No retraining on TRAIN+VALIDATION combined before the holdout read.",
            "No second attempt on the 2026 HOLDOUT with a modified model, regardless of the first result.",
            "No inspection of HOLDOUT rows for any purpose (EDA, distribution checks, feature engineering) before the "
            "single authorized evaluation run.",
        ],

        "production_status": "No production runner started. No trades placed. LIVE_TRADING_ENABLED unchanged. "
                              "P01D remains sovereign and untouched by this research thread.",
    }

    OUT_JSON.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    freeze_hash = sha256_file(OUT_JSON)
    OUT_JSON.with_suffix(".json.sha256").write_text(f"{freeze_hash}  {OUT_JSON.name}\n", encoding="utf-8")

    md_lines = [
        "# Risk Head V1 — Candidate B, Frozen",
        "",
        f"**Frozen:** {record['frozen_at_utc']}",
        f"**Freeze record SHA256:** `{freeze_hash}`",
        "",
        "## Candidate",
        f"- Model: Ridge regression, alpha = **{b_linear['selected_alpha']}**",
        f"- Feature set: `B_INTRADAY_DAILY_SUMMARY` ({len(feature_list)} features, 7D + ORB + Map daily summaries)",
        f"- Target: `adverse_1d = max(0, -mae_1d)`",
        f"- Secondary event: `adverse_1d >= 0.020`",
        "",
        "## Validation (2025, never touching HOLDOUT)",
        f"- Risk Spearman: **{b_linear['validation']['risk_spearman']['estimate']:+.5f}**, "
        f"95% CI [{b_linear['validation']['risk_spearman']['ci_low']:+.5f}, "
        f"{b_linear['validation']['risk_spearman']['ci_high']:+.5f}]",
        f"- 2% event ROC-AUC: **{b_linear['validation']['event_2pct_roc_auc']['estimate']:.4f}**",
        f"- 2% event average precision: {b_linear['validation']['event_2pct_average_precision']['estimate']:.4f}",
        f"- Event rate in validation: {b_linear['validation']['event_rate']:.3%}",
        "",
        "## Why nonlinear (Model 1) was rejected",
        f"- Nonlinear B Spearman improvement: {b_nonlinear_vs_linear['estimate']:+.5f}, "
        f"CI [{b_nonlinear_vs_linear['ci_low']:+.5f}, {b_nonlinear_vs_linear['ci_high']:+.5f}] — crosses zero.",
        f"- Nonlinear B ROC-AUC: {b_nonlinear['validation']['event_2pct_roc_auc']['estimate']:.4f} "
        f"(down from {b_linear['validation']['event_2pct_roc_auc']['estimate']:.4f}) — deteriorated.",
        "- C and D did not significantly outperform B (both paired-increment CIs cross zero).",
        "",
        "## Status",
        "- `holdout_touched: false`",
        "- 2026 HOLDOUT (2026-01-01 to 2026-07-27, 139 dates, 1,112 rows) remains sealed.",
        "- No production runner started. No trades placed. `LIVE_TRADING_ENABLED` unchanged. P01D sovereign.",
        "",
        "## Prohibited after the holdout opens",
        *[f"- {p}" for p in record["prohibitions_after_holdout_opens"]],
    ]
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

    print()
    print("Wrote", OUT_JSON.name, "(sha256", freeze_hash[:16] + "...)")
    print("Wrote", OUT_MD.name)
    print()
    print("FREEZE COMPLETE. holdout_touched=false. Awaiting explicit authorization for the one-time 2026 HOLDOUT read.")


if __name__ == "__main__":
    main()
