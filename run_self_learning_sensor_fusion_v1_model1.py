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

import run_self_learning_sensor_fusion_v1_model0 as m0


ROOT = Path(__file__).resolve().parent

DATA = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"

MODEL0_JSON = ROOT / "SELF_LEARNING_SENSOR_FUSION_V1_MODEL0_LINEAR_VALIDATION_20260825.json"
MODEL0_PRED = ROOT / "SELF_LEARNING_SENSOR_FUSION_V1_MODEL0_LINEAR_VALIDATION_PREDICTIONS_20260825.csv"

OUT_JSON = ROOT / "SELF_LEARNING_SENSOR_FUSION_V1_MODEL1_NONLINEAR_VALIDATION_20260825.json"
OUT_PRED = ROOT / "SELF_LEARNING_SENSOR_FUSION_V1_MODEL1_NONLINEAR_VALIDATION_PREDICTIONS_20260825.csv"

EXPECTED_SHA256 = "2f51f697982063233ee120f0fbd93989d523efe868f810564efa26ef8099c888"

PRIMARY_RAW_TARGET = "fwd_return_5d"
PRIMARY_TARGET = "target_5d_xs"

INNER_SPLITS = 4
INNER_GAP_DATES = 20

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_BLOCK = 5
BOOTSTRAP_SEED = 20260825


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
        return OneHotEncoder(
            handle_unknown="ignore",
            sparse_output=False,
        )
    except TypeError:
        return OneHotEncoder(
            handle_unknown="ignore",
            sparse=False,
        )


def build_pipeline(features, params):

    numeric, categorical = m0.resolve_feature_types(features)

    numeric_pipe = Pipeline([
        (
            "impute",
            SimpleImputer(
                strategy="median",
                add_indicator=True,
            ),
        ),
    ])

    transformers = [
        (
            "numeric",
            numeric_pipe,
            numeric,
        ),
    ]

    if categorical:
        categorical_pipe = Pipeline([
            (
                "impute",
                SimpleImputer(
                    strategy="most_frequent",
                ),
            ),
            (
                "onehot",
                dense_onehot(),
            ),
        ])

        transformers.append(
            (
                "categorical",
                categorical_pipe,
                categorical,
            )
        )

    preprocessor = ColumnTransformer(
        transformers=transformers,
        remainder="drop",
        sparse_threshold=0.0,
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

    return Pipeline([
        ("preprocess", preprocessor),
        ("model", model),
    ])


def inner_fold_score(
    train_fold,
    test_fold,
    features,
    params,
):

    model_features = list(
        dict.fromkeys(
            features + ["symbol"]
        )
    )

    model = build_pipeline(
        features,
        params,
    )

    model.fit(
        train_fold[model_features],
        train_fold[PRIMARY_TARGET],
    )

    pred = model.predict(
        test_fold[model_features]
    )

    daily = m0.daily_spearman_table(
        test_fold["date"],
        test_fold[PRIMARY_TARGET].to_numpy(),
        pred,
        test_fold[PRIMARY_RAW_TARGET].to_numpy(),
    )

    return float(
        daily["rank_ic"].mean()
    )


def select_params(train, features):

    dates = np.array(
        sorted(train["date"].unique())
    )

    splitter = TimeSeriesSplit(
        n_splits=INNER_SPLITS,
        gap=INNER_GAP_DATES,
    )

    results = []

    for params in GRID:

        fold_scores = []

        for train_idx, test_idx in splitter.split(dates):

            train_dates = set(dates[train_idx])
            test_dates = set(dates[test_idx])

            tr = train[
                train["date"].isin(train_dates)
            ]

            te = train[
                train["date"].isin(test_dates)
            ]

            score = inner_fold_score(
                tr,
                te,
                features,
                params,
            )

            fold_scores.append(score)

        results.append({
            "params": params,
            "fold_rank_ic": fold_scores,
            "mean_rank_ic": float(
                np.nanmean(fold_scores)
            ),
        })

    ranked = sorted(
        results,
        key=lambda r: (
            -r["mean_rank_ic"],
            r["params"]["max_leaf_nodes"],
            -r["params"]["min_samples_leaf"],
            -r["params"]["l2_regularization"],
        ),
    )

    return ranked[0]["params"], results


def paired_bootstrap(
    left_daily,
    right_daily,
    column="rank_ic",
    seed=20260825,
):

    merged = (
        left_daily[
            ["date", column]
        ]
        .merge(
            right_daily[
                ["date", column]
            ],
            on="date",
            suffixes=("_left", "_right"),
            validate="one_to_one",
        )
        .sort_values("date")
    )

    diff = (
        merged[f"{column}_left"]
        - merged[f"{column}_right"]
    )

    return m0.moving_block_bootstrap_mean(
        diff,
        block_length=BOOTSTRAP_BLOCK,
        iterations=BOOTSTRAP_ITERATIONS,
        seed=seed,
    )


def daily_from_prediction_frame(frame):

    return m0.daily_spearman_table(
        frame["date"],
        frame[PRIMARY_TARGET].to_numpy(),
        frame["prediction"].to_numpy(),
        frame[PRIMARY_RAW_TARGET].to_numpy(),
    )


def main():

    warnings.filterwarnings(
        "ignore",
        category=RuntimeWarning,
    )

    print("=" * 100)
    print("SELF-LEARNING SENSOR FUSION V1 - MODEL 1")
    print("RESTRAINED NONLINEAR INTERACTION MODEL - VALIDATION ONLY")
    print("=" * 100)

    actual_sha = sha256_file(DATA)

    print()
    print("Frozen dataset SHA256:")
    print(actual_sha)

    if actual_sha != EXPECTED_SHA256:
        raise RuntimeError(
            "Frozen dataset changed. STOP."
        )

    if not MODEL0_JSON.exists():
        raise RuntimeError(
            "Model 0 result JSON missing."
        )

    if not MODEL0_PRED.exists():
        raise RuntimeError(
            "Model 0 predictions CSV missing."
        )

    model0_record = json.loads(
        MODEL0_JSON.read_text(
            encoding="utf-8"
        )
    )

    if model0_record.get("holdout_touched") is not False:
        raise RuntimeError(
            "Model 0 does not certify untouched holdout."
        )

    df = pd.read_csv(DATA)

    df["date"] = pd.to_datetime(
        df["date"]
    )

    for c in m0.BOOLEAN_COLUMNS:

        if c not in df.columns:
            continue

        if df[c].dtype == object:
            df[c] = (
                df[c]
                .astype(str)
                .str.lower()
                .map({
                    "true": 1.0,
                    "false": 0.0,
                })
            )
        else:
            df[c] = df[c].astype(float)

    work = df[
        df["split_role"].isin(
            ["TRAIN", "VALIDATION"]
        )
    ].copy()

    train = work[
        work["split_role"] == "TRAIN"
    ].copy()

    valid = work[
        work["split_role"] == "VALIDATION"
    ].copy()

    train[PRIMARY_TARGET] = m0.make_xs_target(train)
    valid[PRIMARY_TARGET] = m0.make_xs_target(valid)

    print()
    print(
        "TRAIN:",
        len(train),
        "rows /",
        train["date"].nunique(),
        "dates"
    )

    print(
        "VALIDATION:",
        len(valid),
        "rows /",
        valid["date"].nunique(),
        "dates"
    )

    print("HOLDOUT: NOT EVALUATED")

    print()
    print(
        "Primary metric: mean daily cross-sectional "
        "Spearman rank IC"
    )

    print()
    print("Frozen nonlinear grid:")

    for i, params in enumerate(GRID, 1):
        print(i, params)

    results = {}
    prediction_frames = []
    nonlinear_daily = {}

    for model_no, (
        model_name,
        features,
    ) in enumerate(
        m0.FEATURE_SETS.items(),
        1
    ):

        print()
        print("=" * 100)
        print(f"[{model_no}/4] {model_name}")
        print("=" * 100)

        print(
            "Feature count:",
            len(features),
            "+ symbol fixed effect"
        )

        best_params, inner_results = select_params(
            train,
            features,
        )

        print()
        print("TRAIN-ONLY CV SEARCH")

        for r in inner_results:

            p = r["params"]

            print(
                f"leaves={p['max_leaf_nodes']:2d} "
                f"minleaf={p['min_samples_leaf']:3d} "
                f"l2={p['l2_regularization']:4.1f} "
                f"meanIC={r['mean_rank_ic']:+.5f} "
                f"folds="
                + ", ".join(
                    f"{x:+.4f}"
                    for x in r["fold_rank_ic"]
                )
            )

        print()
        print("SELECTED:", best_params)

        model_features = list(
            dict.fromkeys(
                features + ["symbol"]
            )
        )

        model = build_pipeline(
            features,
            best_params,
        )

        model.fit(
            train[model_features],
            train[PRIMARY_TARGET],
        )

        pred = model.predict(
            valid[model_features]
        )

        daily = m0.daily_spearman_table(
            valid["date"],
            valid[PRIMARY_TARGET].to_numpy(),
            pred,
            valid[PRIMARY_RAW_TARGET].to_numpy(),
        )

        nonlinear_daily[model_name] = daily

        ic_boot = m0.moving_block_bootstrap_mean(
            daily["rank_ic"],
            block_length=BOOTSTRAP_BLOCK,
            iterations=BOOTSTRAP_ITERATIONS,
            seed=BOOTSTRAP_SEED,
        )

        top2_boot = m0.moving_block_bootstrap_mean(
            daily["top2_minus_universe"],
            block_length=BOOTSTRAP_BLOCK,
            iterations=BOOTSTRAP_ITERATIONS,
            seed=BOOTSTRAP_SEED + 1,
        )

        ls_boot = m0.moving_block_bootstrap_mean(
            daily["top2_minus_bottom2"],
            block_length=BOOTSTRAP_BLOCK,
            iterations=BOOTSTRAP_ITERATIONS,
            seed=BOOTSTRAP_SEED + 2,
        )

        print()
        print("2025 VALIDATION")

        print(
            f"Mean daily rank IC : "
            f"{ic_boot['mean']:+.5f}"
        )

        print(
            f"IC 95% block CI    : "
            f"[{ic_boot['ci_low']:+.5f}, "
            f"{ic_boot['ci_high']:+.5f}]"
        )

        print(
            f"Top2 - universe    : "
            f"{top2_boot['mean'] * 10000:+.2f} bp"
        )

        print(
            f"Top2 95% block CI  : "
            f"[{top2_boot['ci_low'] * 10000:+.2f}, "
            f"{top2_boot['ci_high'] * 10000:+.2f}] bp"
        )

        print(
            f"Top2 - bottom2     : "
            f"{ls_boot['mean'] * 10000:+.2f} bp"
        )

        results[model_name] = {
            "features": features,
            "selected_params": best_params,
            "inner_train_cv": inner_results,
            "validation": {
                "rank_ic": ic_boot,
                "top2_minus_universe": top2_boot,
                "top2_minus_bottom2": ls_boot,
            },
        }

        pf = valid[
            [
                "date",
                "symbol",
                PRIMARY_RAW_TARGET,
            ]
        ].copy()

        pf[PRIMARY_TARGET] = (
            valid[PRIMARY_TARGET].to_numpy()
        )

        pf["prediction"] = pred
        pf["model"] = model_name

        prediction_frames.append(pf)

    print()
    print("=" * 100)
    print("PAIRED INCREMENTAL FUSION TESTS")
    print("=" * 100)

    fusion_pairs = [
        ("C_ALL", "A_EXPERT_DAILY"),
        ("C_ALL", "B_INTRADAY_DAILY_SUMMARY"),
        ("D_PROVENANCE_AWARE", "A_EXPERT_DAILY"),
        ("D_PROVENANCE_AWARE", "B_INTRADAY_DAILY_SUMMARY"),
    ]

    fusion_diffs = {}

    for i, (left, right) in enumerate(
        fusion_pairs,
        1
    ):

        diff = paired_bootstrap(
            nonlinear_daily[left],
            nonlinear_daily[right],
            column="rank_ic",
            seed=BOOTSTRAP_SEED + 100 + i,
        )

        key = f"{left}_minus_{right}"
        fusion_diffs[key] = diff

        print(
            f"{key:60s} "
            f"mean={diff['mean']:+.5f} "
            f"95%CI=[{diff['ci_low']:+.5f}, "
            f"{diff['ci_high']:+.5f}]"
        )

    print()
    print("=" * 100)
    print("NONLINEAR MODEL 1 vs LINEAR MODEL 0")
    print("=" * 100)

    linear_pred = pd.read_csv(
        MODEL0_PRED
    )

    linear_pred["date"] = pd.to_datetime(
        linear_pred["date"]
    )

    nonlinear_vs_linear = {}

    for i, model_name in enumerate(
        m0.FEATURE_SETS.keys(),
        1
    ):

        lp = linear_pred[
            linear_pred["model"] == model_name
        ].copy()

        linear_daily = daily_from_prediction_frame(lp)

        diff = paired_bootstrap(
            nonlinear_daily[model_name],
            linear_daily,
            column="rank_ic",
            seed=BOOTSTRAP_SEED + 200 + i,
        )

        nonlinear_vs_linear[model_name] = diff

        print(
            f"{model_name:30s} "
            f"Model1-Model0 IC={diff['mean']:+.5f} "
            f"95%CI=[{diff['ci_low']:+.5f}, "
            f"{diff['ci_high']:+.5f}]"
        )

    print()
    print("=" * 100)
    print("MODEL 1 VALIDATION COMPARISON")
    print("=" * 100)

    rows = []

    for name, r in results.items():

        v = r["validation"]

        rows.append({
            "model": name,
            "rank_ic": v["rank_ic"]["mean"],
            "ic_low": v["rank_ic"]["ci_low"],
            "ic_high": v["rank_ic"]["ci_high"],
            "top2_universe_bp":
                v["top2_minus_universe"]["mean"] * 10000,
        })

    table = (
        pd.DataFrame(rows)
        .sort_values(
            "rank_ic",
            ascending=False,
        )
    )

    print(
        table.to_string(
            index=False,
            formatters={
                "rank_ic":
                    lambda x: f"{x:+.5f}",
                "ic_low":
                    lambda x: f"{x:+.5f}",
                "ic_high":
                    lambda x: f"{x:+.5f}",
                "top2_universe_bp":
                    lambda x: f"{x:+.2f}",
            },
        )
    )

    output = {
        "schema":
            "SELF_LEARNING_SENSOR_FUSION_V1_MODEL1_NONLINEAR_VALIDATION",

        "frozen_dataset":
            DATA.name,

        "frozen_dataset_sha256":
            actual_sha,

        "model_family":
            "HistGradientBoostingRegressor",

        "primary_target":
            PRIMARY_TARGET,

        "primary_metric":
            "mean daily cross-sectional Spearman rank IC",

        "hyperparameter_grid":
            GRID,

        "early_stopping":
            False,

        "holdout_touched":
            False,

        "results":
            results,

        "paired_fusion_increment_tests":
            fusion_diffs,

        "nonlinear_vs_linear":
            nonlinear_vs_linear,
    }

    OUT_JSON.write_text(
        json.dumps(
            output,
            indent=2,
        ),
        encoding="utf-8",
    )

    pd.concat(
        prediction_frames,
        ignore_index=True,
    ).to_csv(
        OUT_PRED,
        index=False,
    )

    print()
    print("RESULT JSON:", OUT_JSON)
    print("PREDICTIONS:", OUT_PRED)

    print()
    print("=" * 100)
    print("MODEL 1 COMPLETE - 2026 HOLDOUT STILL UNTOUCHED")
    print("=" * 100)


if __name__ == "__main__":
    main()
