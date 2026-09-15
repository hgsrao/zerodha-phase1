"""SELF-LEARNING RISK HEAD V1 - Candidate B - ONE-TIME 2026 HOLDOUT EXAM.

2026-08-25, owner-authorized ("I authorize the one-time frozen 2026
holdout evaluation now"), run exactly once against the exact frozen
candidate in RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json.

FROZEN, UNCHANGED FROM SELECTION:
  - Model: Ridge regression, alpha = 1000 (the exact value already
    selected and frozen - not re-selected here)
  - Features: the exact 25 B_INTRADAY_DAILY_SUMMARY features (via
    alpha0.FEATURE_SETS - unchanged)
  - Preprocessing: risk0.prepare_numeric_matrix, UNCHANGED - fit
    statistics (medians, means, stds, one-hot categories) come from
    TRAIN ONLY, exactly as they did when this same fit was scored
    against VALIDATION.
  - Target: adverse_1d = max(0, -mae_1d) - unchanged.
  - Secondary event: adverse_1d >= 0.020 - unchanged.

CRITICAL, per the freeze record's own explicit prohibition ("No
retraining on TRAIN+VALIDATION combined before the holdout read"): this
script fits on TRAIN ONLY - the identical fit that already produced the
2025 VALIDATION numbers - and evaluates it against HOLDOUT. It does NOT
retrain on TRAIN+VALIDATION combined, and does NOT touch VALIDATION rows
for anything other than the pre-existing frozen result already on disk.

ONE-SHOT GUARD: refuses to run if the HOLDOUT result files already
exist - "no second attempt on 2026 with a modified model, regardless of
the first result" is enforced structurally, not just by convention.

Answers exactly the 6 questions the owner posed, nothing else:
  1. Is Risk Spearman still positive?
  2. 95% block-bootstrap CI?
  3. Is 2% ROC-AUC still above 0.50?
  4. Average precision vs. the actual 2026 event rate?
  5. Does the top predicted-risk quartile show greater realized adverse
     excursion?
  6. Is the result stable across months and symbols?
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.linear_model import Ridge

import run_self_learning_sensor_fusion_v1_model0 as alpha0
import run_self_learning_risk_head_v1_model0 as risk0

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"
FREEZE_JSON = ROOT / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json"
FREEZE_HASH_FILE = ROOT / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json.sha256"

OUT_JSON = ROOT / "RISK_HEAD_V1_CANDIDATE_B_HOLDOUT_EXAM_RESULT_20260825.json"
OUT_PRED = ROOT / "RISK_HEAD_V1_CANDIDATE_B_HOLDOUT_EXAM_PREDICTIONS_20260825.csv"

CANDIDATE_MODEL = "B_INTRADAY_DAILY_SUMMARY"
EXPECTED_ALPHA = 1000.0
EXPECTED_SHA256 = risk0.EXPECTED_SHA256


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 100)
    print("RISK HEAD V1 - CANDIDATE B - ONE-TIME 2026 HOLDOUT EXAM")
    print("=" * 100)

    # --- ONE-SHOT GUARD: refuse if already run ---
    if OUT_JSON.exists() or OUT_PRED.exists():
        raise RuntimeError(
            "HOLDOUT exam output already exists. This is a ONE-TIME exam - "
            "no second attempt, regardless of the first result. STOP. "
            f"Existing files: {OUT_JSON.name if OUT_JSON.exists() else ''} "
            f"{OUT_PRED.name if OUT_PRED.exists() else ''}"
        )

    # --- Verify the frozen candidate record itself, then the dataset ---
    if not FREEZE_JSON.exists():
        raise RuntimeError("Freeze record missing. Cannot run an unauthorized exam. STOP.")
    freeze_actual_hash = sha256_file(FREEZE_JSON)
    freeze_expected_hash = FREEZE_HASH_FILE.read_text(encoding="utf-8").split()[0]
    if freeze_actual_hash != freeze_expected_hash:
        raise RuntimeError("Freeze record hash mismatch - the frozen candidate record itself has changed. STOP.")
    freeze = json.loads(FREEZE_JSON.read_text(encoding="utf-8"))
    if freeze["holdout_touched"] is not False:
        raise RuntimeError("Freeze record does not certify an untouched holdout as of freezing. STOP.")
    print("Freeze record verified, hash:", freeze_actual_hash)

    actual_sha = sha256_file(DATA)
    if actual_sha != EXPECTED_SHA256 or actual_sha != freeze["dataset"]["sha256"]:
        raise RuntimeError("Frozen dataset hash mismatch against freeze record. STOP.")
    print("Dataset SHA256 verified:", actual_sha)

    frozen_alpha = freeze["candidate"]["alpha"]
    if float(frozen_alpha) != EXPECTED_ALPHA:
        raise RuntimeError(f"Freeze record alpha ({frozen_alpha}) != expected ({EXPECTED_ALPHA}). STOP.")
    frozen_features = freeze["candidate"]["feature_list"]
    live_features = alpha0.FEATURE_SETS[CANDIDATE_MODEL]
    if frozen_features != live_features:
        raise RuntimeError("Live FEATURE_SETS['B_INTRADAY_DAILY_SUMMARY'] has drifted from the frozen feature list. STOP.")
    print(f"Candidate verified: Ridge alpha={frozen_alpha}, {len(live_features)} features, unchanged.")

    # --- Load data, apply the SAME target definition, SAME boolean coercion ---
    df = pd.read_csv(DATA)
    df["date"] = pd.to_datetime(df["date"])
    for c in risk0.BOOLEAN_COLUMNS:
        if c not in df.columns:
            continue
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.lower().map({"true": 1.0, "false": 0.0})
        else:
            df[c] = df[c].astype(float)
    df["adverse_1d"] = (-df["mae_1d"]).clip(lower=0.0)

    train = df[df["split_role"] == "TRAIN"].copy()
    holdout = df[df["split_role"] == "HOLDOUT"].copy()

    print()
    print("TRAIN:", len(train), "rows /", train["date"].nunique(), "dates  (fit population - unchanged from selection)")
    print("HOLDOUT:", len(holdout), "rows /", holdout["date"].nunique(), "dates  "
          f"({holdout['date'].min().date()} to {holdout['date'].max().date()})")
    print()
    print("Refitting the EXACT frozen candidate on TRAIN ONLY (identical to the fit already scored on VALIDATION) -")
    print("NOT retraining on TRAIN+VALIDATION combined, per the freeze record's own explicit prohibition.")

    # --- Fit on TRAIN only, exactly as done for the original VALIDATION scoring ---
    X_train, X_holdout = risk0.prepare_numeric_matrix(train, holdout, live_features)
    model = Ridge(alpha=frozen_alpha, solver="lsqr")
    model.fit(X_train, train["adverse_1d"])
    pred = model.predict(X_holdout)

    pf = holdout[["date", "symbol", "adverse_1d"]].copy()
    pf["prediction"] = pred
    pf["model"] = CANDIDATE_MODEL

    # --- The 4 frozen risk metrics, block-bootstrapped exactly as before ---
    spearman = risk0.block_bootstrap_metric(
        pf, risk0.metric_spearman, risk0.BOOTSTRAP_ITERATIONS, risk0.BOOTSTRAP_BLOCK_DATES, risk0.BOOTSTRAP_SEED,
    )
    topq = risk0.block_bootstrap_metric(
        pf, risk0.metric_top_quartile_excess, risk0.BOOTSTRAP_ITERATIONS, risk0.BOOTSTRAP_BLOCK_DATES, risk0.BOOTSTRAP_SEED + 1,
    )
    auc = risk0.block_bootstrap_metric(
        pf, risk0.metric_event_auc, risk0.BOOTSTRAP_ITERATIONS, risk0.BOOTSTRAP_BLOCK_DATES, risk0.BOOTSTRAP_SEED + 2,
    )
    ap = risk0.block_bootstrap_metric(
        pf, risk0.metric_average_precision, risk0.BOOTSTRAP_ITERATIONS, risk0.BOOTSTRAP_BLOCK_DATES, risk0.BOOTSTRAP_SEED + 3,
    )
    event_rate = float((pf["adverse_1d"] >= risk0.EVENT_THRESHOLD).mean())
    negative_predictions = float((pf["prediction"] < 0).mean())

    print()
    print("=" * 100)
    print("2026 HOLDOUT RESULT")
    print("=" * 100)
    print(f"Q1. Risk Spearman           : {spearman['estimate']:+.5f}  {'POSITIVE' if spearman['estimate'] > 0 else 'NOT POSITIVE'}")
    print(f"Q2. 95% block-bootstrap CI  : [{spearman['ci_low']:+.5f}, {spearman['ci_high']:+.5f}]  "
          f"excludes_zero={spearman['ci_low'] > 0 or spearman['ci_high'] < 0}")
    print(f"Q3. 2% event ROC-AUC        : {auc['estimate']:.5f}  "
          f"{'ABOVE 0.50' if auc['estimate'] > 0.5 else 'AT OR BELOW 0.50'}   CI=[{auc['ci_low']:.5f}, {auc['ci_high']:.5f}]")
    print(f"Q4. 2% event Avg Precision  : {ap['estimate']:.5f}   vs event rate {event_rate:.3%} "
          f"(lift = {ap['estimate'] / event_rate if event_rate > 0 else float('nan'):.2f}x)")
    print(f"Q5. Top-risk quartile excess adverse: {topq['estimate'] * 10000:+.2f} bp   "
          f"CI=[{topq['ci_low'] * 10000:+.2f}, {topq['ci_high'] * 10000:+.2f}] bp")
    print(f"Negative raw predictions    : {negative_predictions:.3%}")

    # --- Q6: stability across months and symbols ---
    pf["month"] = pf["date"].dt.to_period("M").astype(str)
    monthly = pf.groupby("month").apply(
        lambda g: risk0.rank_corr(g["adverse_1d"], g["prediction"]), include_groups=False
    )
    print()
    print("Q6a. Monthly Spearman (2026, stability check):")
    for month, val in monthly.items():
        print(f"  {month}: {val:+.4f}" if pd.notna(val) else f"  {month}: n/a (insufficient variation)")

    symbol_wise = pf.groupby("symbol").apply(
        lambda g: risk0.rank_corr(g["adverse_1d"], g["prediction"]), include_groups=False
    )
    print()
    print("Q6b. Symbol-wise Spearman (2026, stability check):")
    for sym, val in symbol_wise.items():
        print(f"  {sym}: {val:+.4f}" if pd.notna(val) else f"  {sym}: n/a")

    output = {
        "schema": "RISK_HEAD_V1_CANDIDATE_B_HOLDOUT_EXAM_RESULT",
        "frozen_candidate_record": FREEZE_JSON.name,
        "frozen_candidate_record_sha256": freeze_actual_hash,
        "frozen_dataset_sha256": actual_sha,
        "model_family": "Ridge regression",
        "alpha": frozen_alpha,
        "n_features": len(live_features),
        "target": "adverse_1d=max(0,-mae_1d)",
        "secondary_event": "adverse_1d >= 0.020",
        "train_window": {"start": str(train["date"].min().date()), "end": str(train["date"].max().date()),
                          "n_dates": int(train["date"].nunique()), "n_rows": int(len(train))},
        "holdout_window": {"start": str(holdout["date"].min().date()), "end": str(holdout["date"].max().date()),
                            "n_dates": int(holdout["date"].nunique()), "n_rows": int(len(holdout))},
        "fit_policy": "TRAIN ONLY - identical fit to the one already scored on VALIDATION. "
                       "TRAIN+VALIDATION combined refit explicitly NOT performed, per the freeze record's prohibition.",
        "results": {
            "risk_spearman": spearman,
            "top_risk_quartile_excess_adverse": topq,
            "event_2pct_roc_auc": auc,
            "event_2pct_average_precision": ap,
            "event_rate": event_rate,
            "negative_prediction_rate": negative_predictions,
        },
        "monthly_spearman": {k: (None if pd.isna(v) else float(v)) for k, v in monthly.items()},
        "symbol_spearman": {k: (None if pd.isna(v) else float(v)) for k, v in symbol_wise.items()},
        "one_time_exam": True,
        "second_attempt_prohibited": True,
        "holdout_touched": True,
    }

    OUT_JSON.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    pf.to_csv(OUT_PRED, index=False)
    result_hash = sha256_file(OUT_JSON)
    OUT_JSON.with_suffix(".json.sha256").write_text(f"{result_hash}  {OUT_JSON.name}\n", encoding="utf-8")

    print()
    print("RESULT JSON:", OUT_JSON, "(sha256", result_hash[:16] + "...)")
    print("PREDICTIONS:", OUT_PRED)
    print()
    print("=" * 100)
    print("ONE-TIME 2026 HOLDOUT EXAM COMPLETE. No second attempt permitted, regardless of outcome.")
    print("=" * 100)


if __name__ == "__main__":
    main()
