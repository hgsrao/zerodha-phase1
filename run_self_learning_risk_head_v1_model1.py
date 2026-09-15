from __future__ import annotations

from pathlib import Path
import hashlib
import itertools
import json
import warnings

import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.model_selection import TimeSeriesSplit

# Reuses, does not reimplement:
#   - alpha0.FEATURE_SETS / resolve_feature_types / BOOLEAN_COLUMNS -
#     the SAME frozen A/B/C/D feature lists Alpha Model 0/1 and Risk
#     Head Model 0 already use. Not re-specified here.
#   - risk0.rank_corr / block_bootstrap_metric / metric_spearman /
#     metric_top_quartile_excess / metric_event_auc /
#     metric_average_precision / paired_spearman_difference /
#     EVENT_THRESHOLD - Risk Head Model 0's own already-validated risk
#     target and metric definitions, UNCHANGED. This file only supplies
#     a different MODEL FAMILY (nonlinear) to plug into that exact same
#     evaluation machinery - not a new risk definition.
# This is a deliberate combination of two already-frozen, already-run
# scripts (Alpha Model 1's nonlinear pipeline + Risk Head Model 0's risk
# target/metrics), not a new design - per the owner's explicit
# instruction: "Run exactly one restrained nonlinear family, preferably
# the same HistGradientBoostingRegressor approach we used for Alpha
# Model 1, on A/B/C/D... Do not invent new features."
import run_self_learning_sensor_fusion_v1_model0 as alpha0
import run_self_learning_risk_head_v1_model0 as risk0


ROOT = Path(__file__).resolve().parent

DATA = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"

RISK0_JSON = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL0_LINEAR_VALIDATION_20260825.json"
RISK0_PRED = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL0_LINEAR_VALIDATION_PREDICTIONS_20260825.csv"

OUT_JSON = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL1_NONLINEAR_VALIDATION_20260825.json"
OUT_PRED = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL1_NONLINEAR_VALIDATION_PREDICTIONS_20260825.csv"

EXPECTED_SHA256 = risk0.EXPECTED_SHA256

FEATURE_SETS = alpha0.FEATURE_SETS
BOOLEAN_COLUMNS = alpha0.BOOLEAN_COLUMNS

INNER_SPLITS = risk0.INNER_SPLITS
INNER_GAP_DATES = risk0.INNER_GAP_DATES

BOOTSTRAP_ITERATIONS = risk0.BOOTSTRAP_ITERATIONS
BOOTSTRAP_BLOCK_DATES = risk0.BOOTSTRAP_BLOCK_DATES
BOOTSTRAP_SEED = risk0.BOOTSTRAP_SEED

EVENT_THRESHOLD = risk0.EVENT_THRESHOLD  # 0.020 - unchanged, per instruction

# SAME frozen hyperparameter grid as Alpha Model 1 - not re-tuned for
# this target, per the owner's explicit "do not optimize specifically
# for B" / restrained-nonlinear-family instruction.
GRID = [
    {
        "max_leaf_nodes": leaf,
        "min_samples_leaf": min_leaf,
        "l2_regularization": l2,
        "learning_rate": 0.05,
        "max_iter": 250,
    }
    for leaf, min_leaf, l2 in itertools.product(
        [7, 15],
        [40, 80],
        [1.0, 10.0],
    )
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dense_onehot():
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def build_pipeline(features, params):
    """Identical structure to Alpha Model 1's build_pipeline - same
    imputer/onehot/ColumnTransformer/HistGradientBoostingRegressor
    setup, unchanged. Only the fitting TARGET differs (adverse_1d, set
    by the caller), not the pipeline itself."""
    numeric, categorical = alpha0.resolve_feature_types(features)

    numeric_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
    ])

    transformers = [("numeric", numeric_pipe, numeric)]

    if categorical:
        categorical_pipe = Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", dense_onehot()),
        ])
        transformers.append(("categorical", categorical_pipe, categorical))

    preprocessor = ColumnTransformer(
        transformers=transformers, remainder="drop", sparse_threshold=0.0,
    )

    model = HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=params["learning_rate"],
        max_iter=params["max_iter"],
        max_leaf_nodes=params["max_leaf_nodes"],
        min_samples_leaf=params["min_samples_leaf"],
        l2_regularization=params["l2_regularization"],
        early_stopping=False,
        random_state=20260825,
    )

    return Pipeline([("preprocess", preprocessor), ("model", model)])


def inner_fold_score(train_fold, test_fold, features, params):
    """Scores a TRAIN-CV fold with Risk Head's OWN metric (risk0.rank_corr -
    a pooled Spearman over the fold's rows), not Alpha's per-date-averaged
    cross-sectional IC. The risk target's own established convention is
    kept exactly as Risk Head Model 0 defined it - only the model family
    (nonlinear pipeline vs. Ridge) changes here."""
    model_features = list(dict.fromkeys(features + ["symbol"]))

    model = build_pipeline(features, params)
    model.fit(train_fold[model_features], train_fold["adverse_1d"])
    pred = model.predict(test_fold[model_features])

    return risk0.rank_corr(test_fold["adverse_1d"], pred)


def select_params(train, features):
    dates = np.array(sorted(train["date"].unique()))
    splitter = TimeSeriesSplit(n_splits=INNER_SPLITS, gap=INNER_GAP_DATES)

    results = []
    for params in GRID:
        fold_scores = []
        for train_idx, test_idx in splitter.split(dates):
            train_dates = set(dates[train_idx])
            test_dates = set(dates[test_idx])
            tr = train[train["date"].isin(train_dates)]
            te = train[train["date"].isin(test_dates)]
            fold_scores.append(inner_fold_score(tr, te, features, params))

        results.append({
            "params": params,
            "fold_spearman": fold_scores,
            "mean_spearman": float(np.nanmean(fold_scores)),
        })

    finite_results = [r for r in results if np.isfinite(r["mean_spearman"])]
    if not finite_results:
        # Same fail-closed guard as Risk Head Model 0 - do not silently
        # pick an arbitrary hyperparameter combo if every score is NaN/inf.
        raise RuntimeError(
            "All TRAIN-CV Spearman scores are non-finite. "
            "Failing closed instead of selecting an arbitrary hyperparameter set."
        )

    ranked = sorted(
        finite_results,
        key=lambda r: (
            -r["mean_spearman"],
            r["params"]["max_leaf_nodes"],
            -r["params"]["min_samples_leaf"],
            -r["params"]["l2_regularization"],
        ),
    )
    return ranked[0]["params"], results


def main():
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    print("=" * 100)
    print("SELF-LEARNING RISK HEAD V1 - MODEL 1")
    print("RESTRAINED NONLINEAR RISK MODEL - VALIDATION ONLY")
    print("=" * 100)

    actual_sha = sha256_file(DATA)
    print()
    print("Frozen dataset SHA256:", actual_sha)
    if actual_sha != EXPECTED_SHA256:
        raise RuntimeError("Frozen dataset changed. STOP.")

    if not RISK0_JSON.exists():
        raise RuntimeError("Risk Head Model 0 result JSON missing.")
    if not RISK0_PRED.exists():
        raise RuntimeError("Risk Head Model 0 predictions CSV missing.")

    risk0_record = json.loads(RISK0_JSON.read_text(encoding="utf-8"))
    if risk0_record.get("holdout_touched") is not False:
        raise RuntimeError("Risk Head Model 0 does not certify untouched holdout.")

    df = pd.read_csv(DATA)
    df["date"] = pd.to_datetime(df["date"])

    for c in BOOLEAN_COLUMNS:
        if c not in df.columns:
            continue
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.lower().map({"true": 1.0, "false": 0.0})
        else:
            df[c] = df[c].astype(float)

    # SAME target definition as Risk Head Model 0 - unchanged.
    df["adverse_1d"] = (-df["mae_1d"]).clip(lower=0.0)

    # HOLDOUT deliberately excluded.
    train = df[df["split_role"] == "TRAIN"].copy()
    valid = df[df["split_role"] == "VALIDATION"].copy()

    print()
    print("TRAIN:", len(train), "rows /", train["date"].nunique(), "dates")
    print("VALIDATION:", len(valid), "rows /", valid["date"].nunique(), "dates")
    print("HOLDOUT: NOT EVALUATED")

    print()
    print("Primary target: adverse_1d = max(0, -mae_1d)  [unchanged from Model 0]")
    print("Primary metric: pooled Spearman correlation between predicted and realized adverse_1d")
    print("Secondary event: adverse_1d >= 2.0%  [unchanged from Model 0]")
    print()
    print("Frozen nonlinear grid (identical to Alpha Model 1's grid, not re-tuned):")
    for i, params in enumerate(GRID, 1):
        print(i, params)

    results = {}
    prediction_frames = []
    pred_by_model = {}

    for model_no, (model_name, features) in enumerate(FEATURE_SETS.items(), 1):
        print()
        print("=" * 100)
        print(f"[{model_no}/4] {model_name}")
        print("=" * 100)
        print("Feature count:", len(features), "+ symbol fixed effect")

        best_params, cv_results = select_params(train, features)

        print()
        print("TRAIN-ONLY CV SEARCH")
        for r in cv_results:
            p = r["params"]
            print(
                f"leaves={p['max_leaf_nodes']:2d} minleaf={p['min_samples_leaf']:3d} "
                f"l2={p['l2_regularization']:4.1f} meanSpearman={r['mean_spearman']:+.5f} "
                f"folds=" + ", ".join(f"{x:+.4f}" for x in r["fold_spearman"])
            )
        print()
        print("SELECTED:", best_params)

        model_features = list(dict.fromkeys(features + ["symbol"]))
        model = build_pipeline(features, best_params)
        model.fit(train[model_features], train["adverse_1d"])
        pred = model.predict(valid[model_features])

        pf = valid[["date", "symbol", "adverse_1d"]].copy()
        pf["prediction"] = pred
        pf["model"] = model_name
        pred_by_model[model_name] = pf.copy()
        prediction_frames.append(pf)

        spearman = risk0.block_bootstrap_metric(
            pf, risk0.metric_spearman, BOOTSTRAP_ITERATIONS, BOOTSTRAP_BLOCK_DATES, BOOTSTRAP_SEED,
        )
        topq = risk0.block_bootstrap_metric(
            pf, risk0.metric_top_quartile_excess, BOOTSTRAP_ITERATIONS, BOOTSTRAP_BLOCK_DATES, BOOTSTRAP_SEED + 1,
        )
        auc = risk0.block_bootstrap_metric(
            pf, risk0.metric_event_auc, BOOTSTRAP_ITERATIONS, BOOTSTRAP_BLOCK_DATES, BOOTSTRAP_SEED + 2,
        )
        ap = risk0.block_bootstrap_metric(
            pf, risk0.metric_average_precision, BOOTSTRAP_ITERATIONS, BOOTSTRAP_BLOCK_DATES, BOOTSTRAP_SEED + 3,
        )
        event_rate = float((pf["adverse_1d"] >= EVENT_THRESHOLD).mean())
        negative_predictions = float((pf["prediction"] < 0).mean())

        print()
        print("2025 VALIDATION")
        print(f"Risk Spearman           : {spearman['estimate']:+.5f}")
        print(f"Spearman 95% block CI   : [{spearman['ci_low']:+.5f}, {spearman['ci_high']:+.5f}]")
        print(f"Top-risk quartile excess: {topq['estimate'] * 10000:+.2f} bp   "
              f"CI=[{topq['ci_low'] * 10000:+.2f}, {topq['ci_high'] * 10000:+.2f}] bp")
        print(f"2% event ROC-AUC        : {auc['estimate']:.5f}   CI=[{auc['ci_low']:.5f}, {auc['ci_high']:.5f}]")
        print(f"2% event Avg Precision  : {ap['estimate']:.5f}")
        print(f"Validation event rate   : {event_rate:.3%}")
        print(f"Negative raw predictions: {negative_predictions:.3%}")

        results[model_name] = {
            "features": features,
            "selected_params": best_params,
            "inner_train_cv": cv_results,
            "validation": {
                "risk_spearman": spearman,
                "top_risk_quartile_excess_adverse": topq,
                "event_2pct_roc_auc": auc,
                "event_2pct_average_precision": ap,
                "event_rate": event_rate,
                "negative_prediction_rate": negative_predictions,
            },
        }

    print()
    print("=" * 100)
    print("PAIRED FUSION-INCREMENT TESTS (nonlinear)")
    print("=" * 100)

    pairs = [
        ("C_ALL", "A_EXPERT_DAILY"),
        ("C_ALL", "B_INTRADAY_DAILY_SUMMARY"),
        ("D_PROVENANCE_AWARE", "A_EXPERT_DAILY"),
        ("D_PROVENANCE_AWARE", "B_INTRADAY_DAILY_SUMMARY"),
    ]
    paired = {}
    for i, (left, right) in enumerate(pairs, 1):
        diff = risk0.paired_spearman_difference(
            pred_by_model[left], pred_by_model[right], seed=BOOTSTRAP_SEED + 100 + i,
        )
        key = f"{left}_minus_{right}"
        paired[key] = diff
        print(f"{key:60s} Spearman delta={diff['estimate']:+.5f}  "
              f"95%CI=[{diff['ci_low']:+.5f}, {diff['ci_high']:+.5f}]")

    print()
    print("=" * 100)
    print("NONLINEAR MODEL 1 vs LINEAR MODEL 0 (risk head)")
    print("=" * 100)

    linear_pred = pd.read_csv(RISK0_PRED)
    linear_pred["date"] = pd.to_datetime(linear_pred["date"])

    nonlinear_vs_linear = {}
    for i, model_name in enumerate(FEATURE_SETS.keys(), 1):
        lp = linear_pred[linear_pred["model"] == model_name].copy()
        diff = risk0.paired_spearman_difference(
            pred_by_model[model_name], lp, seed=BOOTSTRAP_SEED + 200 + i,
        )
        nonlinear_vs_linear[model_name] = diff
        print(f"{model_name:30s} Model1-Model0 Spearman={diff['estimate']:+.5f}  "
              f"95%CI=[{diff['ci_low']:+.5f}, {diff['ci_high']:+.5f}]")

    print()
    print("=" * 100)
    print("MODEL 1 (RISK) VALIDATION COMPARISON")
    print("=" * 100)

    table_rows = []
    for name, r in results.items():
        v = r["validation"]
        table_rows.append({
            "model": name,
            "risk_spearman": v["risk_spearman"]["estimate"],
            "ci_low": v["risk_spearman"]["ci_low"],
            "ci_high": v["risk_spearman"]["ci_high"],
            "topq_excess_bp": v["top_risk_quartile_excess_adverse"]["estimate"] * 10000,
            "auc_2pct": v["event_2pct_roc_auc"]["estimate"],
            "avg_precision": v["event_2pct_average_precision"]["estimate"],
        })
    table = pd.DataFrame(table_rows).sort_values("risk_spearman", ascending=False)
    print(table.to_string(index=False, formatters={
        "risk_spearman": lambda x: f"{x:+.5f}", "ci_low": lambda x: f"{x:+.5f}",
        "ci_high": lambda x: f"{x:+.5f}", "topq_excess_bp": lambda x: f"{x:+.2f}",
        "auc_2pct": lambda x: f"{x:.5f}", "avg_precision": lambda x: f"{x:.5f}",
    }))

    output = {
        "schema": "SELF_LEARNING_RISK_HEAD_V1_MODEL1_NONLINEAR_VALIDATION",
        "frozen_dataset": DATA.name,
        "frozen_dataset_sha256": actual_sha,
        "model_family": "HistGradientBoostingRegressor",
        "target": "adverse_1d=max(0,-mae_1d)",
        "primary_metric": "pooled Spearman correlation between predicted and realized adverse_1d",
        "secondary_event": "adverse_1d >= 0.020",
        "hyperparameter_grid": GRID,
        "early_stopping": False,
        "holdout_touched": False,
        "results": results,
        "paired_fusion_increment_tests": paired,
        "nonlinear_vs_linear": nonlinear_vs_linear,
    }

    OUT_JSON.write_text(json.dumps(output, indent=2), encoding="utf-8")
    pd.concat(prediction_frames, ignore_index=True).to_csv(OUT_PRED, index=False)

    print()
    print("RESULT JSON:", OUT_JSON)
    print("PREDICTIONS:", OUT_PRED)
    print()
    print("=" * 100)
    print("RISK HEAD MODEL 1 COMPLETE - 2026 HOLDOUT UNTOUCHED")
    print("=" * 100)


if __name__ == "__main__":
    main()
