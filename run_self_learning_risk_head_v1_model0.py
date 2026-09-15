from __future__ import annotations

from pathlib import Path
import hashlib
import json
import warnings

import numpy as np
import pandas as pd

from sklearn.linear_model import Ridge
from sklearn.metrics import roc_auc_score, average_precision_score
from sklearn.model_selection import TimeSeriesSplit

import run_self_learning_sensor_fusion_v1_model0 as alpha0


ROOT = Path(__file__).resolve().parent

DATA = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"

OUT_JSON = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL0_LINEAR_VALIDATION_20260825.json"
OUT_PRED = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL0_LINEAR_VALIDATION_PREDICTIONS_20260825.csv"

EXPECTED_SHA256 = "2f51f697982063233ee120f0fbd93989d523efe868f810564efa26ef8099c888"

ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]

INNER_SPLITS = 4
INNER_GAP_DATES = 20

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_BLOCK_DATES = 5
BOOTSTRAP_SEED = 20260825

EVENT_THRESHOLD = 0.020


FEATURE_SETS = alpha0.FEATURE_SETS
BOOLEAN_COLUMNS = alpha0.BOOLEAN_COLUMNS


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rank_corr(y_true, y_pred) -> float:

    # Convert both inputs to fresh positional arrays first.
    # This prevents pandas from aligning an original DataFrame index
    # on y_true against the 0..N-1 prediction index.
    a = pd.Series(
        np.asarray(y_true, dtype=float)
    ).rank(method="average")

    b = pd.Series(
        np.asarray(y_pred, dtype=float)
    ).rank(method="average")

    valid = a.notna() & b.notna()

    a = a.loc[valid].reset_index(drop=True)
    b = b.loc[valid].reset_index(drop=True)

    if len(a) < 3:
        return np.nan

    if a.nunique() <= 1 or b.nunique() <= 1:
        return np.nan

    return float(a.corr(b))


def prepare_numeric_matrix(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
):

    tr = train.copy()
    te = test.copy()

    numeric_cols = []
    categorical_cols = []

    for c in features + ["symbol"]:

        if c == "symbol" or c == "regime_bucket":
            categorical_cols.append(c)
        else:
            numeric_cols.append(c)

    train_parts = []
    test_parts = []

    if numeric_cols:

        tr_num = tr[numeric_cols].copy()
        te_num = te[numeric_cols].copy()

        medians = tr_num.median(numeric_only=True)

        missing_train = tr_num.isna().astype(float)
        missing_test = te_num.isna().astype(float)

        missing_cols = [
            c for c in numeric_cols
            if tr_num[c].isna().any()
        ]

        tr_num = tr_num.fillna(medians)
        te_num = te_num.fillna(medians)

        means = tr_num.mean()
        stds = tr_num.std(ddof=0).replace(0, 1.0)

        tr_num = (tr_num - means) / stds
        te_num = (te_num - means) / stds

        train_parts.append(
            tr_num.reset_index(drop=True)
        )

        test_parts.append(
            te_num.reset_index(drop=True)
        )

        if missing_cols:

            train_parts.append(
                missing_train[
                    missing_cols
                ]
                .add_suffix("_missing")
                .reset_index(drop=True)
            )

            test_parts.append(
                missing_test[
                    missing_cols
                ]
                .add_suffix("_missing")
                .reset_index(drop=True)
            )

    if categorical_cols:

        tr_cat = pd.get_dummies(
            tr[categorical_cols].astype(str),
            columns=categorical_cols,
            dtype=float,
        )

        te_cat = pd.get_dummies(
            te[categorical_cols].astype(str),
            columns=categorical_cols,
            dtype=float,
        )

        te_cat = te_cat.reindex(
            columns=tr_cat.columns,
            fill_value=0.0,
        )

        train_parts.append(
            tr_cat.reset_index(drop=True)
        )

        test_parts.append(
            te_cat.reset_index(drop=True)
        )

    X_train = pd.concat(
        train_parts,
        axis=1,
    )

    X_test = pd.concat(
        test_parts,
        axis=1,
    )

    return X_train, X_test


def score_inner_fold(
    train_fold,
    test_fold,
    features,
    alpha,
):

    X_train, X_test = prepare_numeric_matrix(
        train_fold,
        test_fold,
        features,
    )

    model = Ridge(
        alpha=alpha,
        solver="lsqr",
    )

    model.fit(
        X_train,
        train_fold["adverse_1d"],
    )

    pred = model.predict(X_test)

    return rank_corr(
        test_fold["adverse_1d"],
        pred,
    )


def select_alpha(
    train,
    features,
):

    dates = np.array(
        sorted(train["date"].unique())
    )

    splitter = TimeSeriesSplit(
        n_splits=INNER_SPLITS,
        gap=INNER_GAP_DATES,
    )

    results = []

    for alpha in ALPHAS:

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

            fold_scores.append(
                score_inner_fold(
                    tr,
                    te,
                    features,
                    alpha,
                )
            )

        results.append({
            "alpha": alpha,
            "fold_spearman": fold_scores,
            "mean_spearman": float(
                np.nanmean(fold_scores)
            ),
        })

    finite_results = [
        r for r in results
        if np.isfinite(r["mean_spearman"])
    ]

    if not finite_results:
        raise RuntimeError(
            "All TRAIN-CV Spearman scores are non-finite. "
            "Failing closed instead of selecting an arbitrary alpha."
        )

    ranked = sorted(
        finite_results,
        key=lambda r: (
            -r["mean_spearman"],
            -r["alpha"],
        ),
    )

    return ranked[0]["alpha"], results


def block_bootstrap_metric(
    frame: pd.DataFrame,
    metric_fn,
    iterations: int,
    block_dates: int,
    seed: int,
):

    dates = np.array(
        sorted(frame["date"].unique())
    )

    n_dates = len(dates)

    if n_dates < block_dates:
        raise RuntimeError(
            "Not enough dates for block bootstrap"
        )

    starts = np.arange(
        0,
        n_dates - block_dates + 1
    )

    rng = np.random.default_rng(seed)

    estimates = []

    for _ in range(iterations):

        sampled_dates = []

        while len(sampled_dates) < n_dates:

            s = int(rng.choice(starts))

            sampled_dates.extend(
                dates[s:s + block_dates]
            )

        sampled_dates = sampled_dates[:n_dates]

        pieces = []

        for d in sampled_dates:

            g = frame[
                frame["date"] == d
            ]

            pieces.append(g)

        sample = pd.concat(
            pieces,
            ignore_index=True,
        )

        estimates.append(
            metric_fn(sample)
        )

    estimates = np.asarray(
        estimates,
        dtype=float,
    )

    estimates = estimates[
        np.isfinite(estimates)
    ]

    return {
        "estimate": float(
            metric_fn(frame)
        ),
        "ci_low": float(
            np.quantile(estimates, 0.025)
        ),
        "ci_high": float(
            np.quantile(estimates, 0.975)
        ),
    }


def metric_spearman(frame):
    return rank_corr(
        frame["adverse_1d"],
        frame["prediction"],
    )


def metric_top_quartile_excess(frame):

    ranked = frame.sort_values(
        "prediction",
        ascending=False,
    )

    n = max(
        1,
        int(np.ceil(len(ranked) * 0.25))
    )

    top = ranked.iloc[:n][
        "adverse_1d"
    ].mean()

    universe = ranked[
        "adverse_1d"
    ].mean()

    return float(
        top - universe
    )


def metric_event_auc(frame):

    y = (
        frame["adverse_1d"]
        >= EVENT_THRESHOLD
    ).astype(int)

    if y.nunique() < 2:
        return np.nan

    return float(
        roc_auc_score(
            y,
            frame["prediction"],
        )
    )


def metric_average_precision(frame):

    y = (
        frame["adverse_1d"]
        >= EVENT_THRESHOLD
    ).astype(int)

    if y.nunique() < 2:
        return np.nan

    return float(
        average_precision_score(
            y,
            frame["prediction"],
        )
    )


def paired_spearman_difference(
    left: pd.DataFrame,
    right: pd.DataFrame,
    seed: int,
):

    merged = (
        left[
            [
                "date",
                "symbol",
                "adverse_1d",
                "prediction",
            ]
        ]
        .merge(
            right[
                [
                    "date",
                    "symbol",
                    "prediction",
                ]
            ],
            on=["date", "symbol"],
            suffixes=("_left", "_right"),
            validate="one_to_one",
        )
    )

    dates = np.array(
        sorted(merged["date"].unique())
    )

    n_dates = len(dates)

    starts = np.arange(
        0,
        n_dates - BOOTSTRAP_BLOCK_DATES + 1
    )

    rng = np.random.default_rng(seed)

    diffs = []

    for _ in range(
        BOOTSTRAP_ITERATIONS
    ):

        sampled_dates = []

        while len(sampled_dates) < n_dates:

            s = int(
                rng.choice(starts)
            )

            sampled_dates.extend(
                dates[
                    s:s + BOOTSTRAP_BLOCK_DATES
                ]
            )

        sampled_dates = sampled_dates[
            :n_dates
        ]

        pieces = []

        for d in sampled_dates:
            pieces.append(
                merged[
                    merged["date"] == d
                ]
            )

        sample = pd.concat(
            pieces,
            ignore_index=True,
        )

        left_ic = rank_corr(
            sample["adverse_1d"],
            sample["prediction_left"],
        )

        right_ic = rank_corr(
            sample["adverse_1d"],
            sample["prediction_right"],
        )

        diffs.append(
            left_ic - right_ic
        )

    diffs = np.asarray(
        diffs,
        dtype=float,
    )

    observed = (
        rank_corr(
            merged["adverse_1d"],
            merged["prediction_left"],
        )
        -
        rank_corr(
            merged["adverse_1d"],
            merged["prediction_right"],
        )
    )

    return {
        "estimate": float(observed),
        "ci_low": float(
            np.quantile(diffs, 0.025)
        ),
        "ci_high": float(
            np.quantile(diffs, 0.975)
        ),
    }


def main():

    warnings.filterwarnings(
        "ignore",
        category=RuntimeWarning,
    )

    print("=" * 100)
    print("SELF-LEARNING RISK HEAD V1 - MODEL 0")
    print("LINEAR RISK BASELINE - VALIDATION ONLY")
    print("=" * 100)

    actual_sha = sha256_file(DATA)

    print()
    print("Frozen dataset SHA256:")
    print(actual_sha)

    if actual_sha != EXPECTED_SHA256:
        raise RuntimeError(
            "Frozen dataset hash mismatch. STOP."
        )

    df = pd.read_csv(DATA)

    df["date"] = pd.to_datetime(
        df["date"]
    )

    for c in BOOLEAN_COLUMNS:

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

    # Correct risk target.
    df["adverse_1d"] = (
        -df["mae_1d"]
    ).clip(lower=0.0)

    # HOLDOUT deliberately excluded.
    train = df[
        df["split_role"] == "TRAIN"
    ].copy()

    valid = df[
        df["split_role"] == "VALIDATION"
    ].copy()

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
        "Primary target: adverse_1d = max(0, -mae_1d)"
    )

    print(
        "Primary metric: pooled Spearman correlation "
        "between predicted and realized adverse excursion"
    )

    print(
        "Secondary event: adverse_1d >= 2.0%"
    )

    results = {}
    prediction_frames = []
    pred_by_model = {}

    for model_name, features in FEATURE_SETS.items():

        print()
        print("=" * 100)
        print(model_name)
        print("=" * 100)

        missing = [
            c for c in features
            if c not in df.columns
        ]

        if missing:
            raise RuntimeError(
                f"{model_name} missing features: {missing}"
            )

        best_alpha, cv_results = select_alpha(
            train,
            features,
        )

        print(
            "Selected alpha:",
            best_alpha
        )

        for r in cv_results:

            print(
                f"alpha={r['alpha']:8g} "
                f"mean TRAIN-CV Spearman="
                f"{r['mean_spearman']:+.5f} "
                f"folds="
                + ", ".join(
                    f"{x:+.4f}"
                    for x in r["fold_spearman"]
                )
            )

        X_train, X_valid = prepare_numeric_matrix(
            train,
            valid,
            features,
        )

        model = Ridge(
            alpha=best_alpha,
            solver="lsqr",
        )

        model.fit(
            X_train,
            train["adverse_1d"],
        )

        pred = model.predict(
            X_valid
        )

        pf = valid[
            [
                "date",
                "symbol",
                "adverse_1d",
            ]
        ].copy()

        pf["prediction"] = pred
        pf["model"] = model_name

        pred_by_model[
            model_name
        ] = pf.copy()

        prediction_frames.append(pf)

        spearman = block_bootstrap_metric(
            pf,
            metric_spearman,
            BOOTSTRAP_ITERATIONS,
            BOOTSTRAP_BLOCK_DATES,
            BOOTSTRAP_SEED,
        )

        topq = block_bootstrap_metric(
            pf,
            metric_top_quartile_excess,
            BOOTSTRAP_ITERATIONS,
            BOOTSTRAP_BLOCK_DATES,
            BOOTSTRAP_SEED + 1,
        )

        auc = block_bootstrap_metric(
            pf,
            metric_event_auc,
            BOOTSTRAP_ITERATIONS,
            BOOTSTRAP_BLOCK_DATES,
            BOOTSTRAP_SEED + 2,
        )

        ap = block_bootstrap_metric(
            pf,
            metric_average_precision,
            BOOTSTRAP_ITERATIONS,
            BOOTSTRAP_BLOCK_DATES,
            BOOTSTRAP_SEED + 3,
        )

        event_rate = float(
            (
                pf["adverse_1d"]
                >= EVENT_THRESHOLD
            ).mean()
        )

        negative_predictions = float(
            (
                pf["prediction"] < 0
            ).mean()
        )

        print()
        print("2025 VALIDATION")

        print(
            f"Risk Spearman          : "
            f"{spearman['estimate']:+.5f}"
        )

        print(
            f"Spearman 95% block CI : "
            f"[{spearman['ci_low']:+.5f}, "
            f"{spearman['ci_high']:+.5f}]"
        )

        print(
            f"Top-risk quartile excess adverse: "
            f"{topq['estimate'] * 10000:+.2f} bp"
        )

        print(
            f"Top-quartile 95% CI   : "
            f"[{topq['ci_low'] * 10000:+.2f}, "
            f"{topq['ci_high'] * 10000:+.2f}] bp"
        )

        print(
            f"2% event ROC-AUC       : "
            f"{auc['estimate']:.5f}"
        )

        print(
            f"ROC-AUC 95% block CI  : "
            f"[{auc['ci_low']:.5f}, "
            f"{auc['ci_high']:.5f}]"
        )

        print(
            f"2% event Avg Precision : "
            f"{ap['estimate']:.5f}"
        )

        print(
            f"Validation event rate  : "
            f"{event_rate:.3%}"
        )

        print(
            f"Negative raw predictions: "
            f"{negative_predictions:.3%}"
        )

        results[model_name] = {
            "selected_alpha":
                best_alpha,

            "train_cv":
                cv_results,

            "validation": {
                "risk_spearman":
                    spearman,

                "top_risk_quartile_excess_adverse":
                    topq,

                "event_2pct_roc_auc":
                    auc,

                "event_2pct_average_precision":
                    ap,

                "event_rate":
                    event_rate,

                "negative_prediction_rate":
                    negative_predictions,
            },
        }

    print()
    print("=" * 100)
    print("PAIRED FUSION-INCREMENT TESTS")
    print("=" * 100)

    pairs = [
        (
            "C_ALL",
            "A_EXPERT_DAILY",
        ),
        (
            "C_ALL",
            "B_INTRADAY_DAILY_SUMMARY",
        ),
        (
            "D_PROVENANCE_AWARE",
            "A_EXPERT_DAILY",
        ),
        (
            "D_PROVENANCE_AWARE",
            "B_INTRADAY_DAILY_SUMMARY",
        ),
    ]

    paired = {}

    for i, (left, right) in enumerate(
        pairs,
        1
    ):

        diff = paired_spearman_difference(
            pred_by_model[left],
            pred_by_model[right],
            seed=BOOTSTRAP_SEED + 100 + i,
        )

        key = f"{left}_minus_{right}"

        paired[key] = diff

        print(
            f"{key:60s} "
            f"Spearman delta={diff['estimate']:+.5f} "
            f"95%CI=[{diff['ci_low']:+.5f}, "
            f"{diff['ci_high']:+.5f}]"
        )

    print()
    print("=" * 100)
    print("RISK HEAD MODEL 0 VALIDATION COMPARISON")
    print("=" * 100)

    table_rows = []

    for name, r in results.items():

        v = r["validation"]

        table_rows.append({
            "model":
                name,

            "risk_spearman":
                v["risk_spearman"]["estimate"],

            "ci_low":
                v["risk_spearman"]["ci_low"],

            "ci_high":
                v["risk_spearman"]["ci_high"],

            "topq_excess_bp":
                v[
                    "top_risk_quartile_excess_adverse"
                ]["estimate"] * 10000,

            "auc_2pct":
                v["event_2pct_roc_auc"]["estimate"],

            "avg_precision":
                v[
                    "event_2pct_average_precision"
                ]["estimate"],
        })

    table = (
        pd.DataFrame(table_rows)
        .sort_values(
            "risk_spearman",
            ascending=False,
        )
    )

    print(
        table.to_string(
            index=False,
            formatters={
                "risk_spearman":
                    lambda x: f"{x:+.5f}",
                "ci_low":
                    lambda x: f"{x:+.5f}",
                "ci_high":
                    lambda x: f"{x:+.5f}",
                "topq_excess_bp":
                    lambda x: f"{x:+.2f}",
                "auc_2pct":
                    lambda x: f"{x:.5f}",
                "avg_precision":
                    lambda x: f"{x:.5f}",
            },
        )
    )

    output = {
        "schema":
            "SELF_LEARNING_RISK_HEAD_V1_MODEL0_LINEAR_VALIDATION",

        "frozen_dataset":
            DATA.name,

        "frozen_dataset_sha256":
            actual_sha,

        "target":
            "adverse_1d=max(0,-mae_1d)",

        "primary_metric":
            "pooled Spearman correlation between "
            "predicted and realized adverse_1d",

        "secondary_event":
            "adverse_1d >= 0.020",

        "holdout_touched":
            False,

        "results":
            results,

        "paired_fusion_increment_tests":
            paired,
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
    print("RISK HEAD MODEL 0 COMPLETE - 2026 HOLDOUT UNTOUCHED")
    print("=" * 100)


if __name__ == "__main__":
    main()
