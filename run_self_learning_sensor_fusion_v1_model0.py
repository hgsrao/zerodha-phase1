from __future__ import annotations

from pathlib import Path
import hashlib
import json
import warnings

import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.model_selection import TimeSeriesSplit


ROOT = Path(__file__).resolve().parent

DATA = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"
MANIFEST = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.json"

OUT_JSON = ROOT / "SELF_LEARNING_SENSOR_FUSION_V1_MODEL0_LINEAR_VALIDATION_20260825.json"
OUT_PRED = ROOT / "SELF_LEARNING_SENSOR_FUSION_V1_MODEL0_LINEAR_VALIDATION_PREDICTIONS_20260825.csv"

EXPECTED_SHA256 = "2f51f697982063233ee120f0fbd93989d523efe868f810564efa26ef8099c888"

PRIMARY_RAW_TARGET = "fwd_return_5d"
PRIMARY_TARGET = "target_5d_xs"

ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]

# Conservative gap, matching the frozen experiment's
# maximum 20-trading-day target horizon.
INNER_GAP_DATES = 20
INNER_SPLITS = 4

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_BLOCK = 5
BOOTSTRAP_SEED = 20260825


# ================================================================
# FEATURE CONTRACT — FROZEN BEFORE MODEL RESULTS
# ================================================================

A_EXPERT = [
    "p1_trend_slope",
    "p1_trend_strength_90d",
    "p1_breakout_distance_atr",
    "p1_invalidation_distance_atr",
    "p1_trend_persistence_days",
    "p1_signal_active",

    "p2_reversion_zscore",
    "p2_displacement_atr",
    "p2_overshoot",
    "p2_reversion_setup_age_days",
    "p2_signal_active",

    "atr_pct",
    "regime_bucket",
]

B_INTRADAY = [
    "structure_last",
    "structure_mean",

    "location_vwap_last",
    "location_vwap_mean",

    "location_bb_last",
    "location_bb_mean",

    "momentum_last",
    "momentum_mean",
    "momentum_max",
    "momentum_min",

    "momentum_delta_last",
    "momentum_delta_mean",

    "volatility_pct_last",
    "volatility_pct_mean",
    "volatility_pct_max",

    "orb_active",
    "orb_range_pct",
    "orb_breakout_minute",
    "orb_first_breakout_strength_bps",

    "map_active",
    "map_entry_count",
    "map_first_entry_minute",
    "map_first_stop_distance_pct",
    "map_first_target_distance_pct",
    "map_mean_stop_distance_pct",
]

C_ALL = A_EXPERT + B_INTRADAY

# Provenance-aware / de-duplicated version.
#
# Remove threshold/bucket outputs whose underlying continuous
# state is already supplied:
#   p1_signal_active
#   p2_signal_active
#   regime_bucket
#
# Retain ORB/MAP event flags because they identify whether the
# associated event geometry is semantically applicable.
D_PROVENANCE = [
    x for x in C_ALL
    if x not in {
        "p1_signal_active",
        "p2_signal_active",
        "regime_bucket",
    }
]

FEATURE_SETS = {
    "A_EXPERT_DAILY": A_EXPERT,
    "B_INTRADAY_DAILY_SUMMARY": B_INTRADAY,
    "C_ALL": C_ALL,
    "D_PROVENANCE_AWARE": D_PROVENANCE,
}

BOOLEAN_COLUMNS = {
    "p1_signal_active",
    "p2_signal_active",
    "orb_active",
    "map_active",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_xs_target(frame: pd.DataFrame) -> pd.Series:
    """
    Cross-sectional 5-day relative return:
        stock 5D return - equal-weight 8-stock mean 5D return
    calculated separately for each decision date.
    """
    mean_by_date = frame.groupby("date")[PRIMARY_RAW_TARGET].transform("mean")
    return frame[PRIMARY_RAW_TARGET] - mean_by_date


def daily_spearman_table(
    dates: pd.Series,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    raw_returns: np.ndarray,
) -> pd.DataFrame:

    temp = pd.DataFrame({
        "date": pd.to_datetime(dates).to_numpy(),
        "y": y_true,
        "pred": y_pred,
        "raw_return": raw_returns,
    })

    rows = []

    for day, g in temp.groupby("date", sort=True):

        if len(g) != 8:
            raise RuntimeError(
                f"Expected exactly 8 symbols on {day}, got {len(g)}"
            )

        # Spearman = Pearson correlation of ranks.
        yr = g["y"].rank(method="average")
        pr = g["pred"].rank(method="average")

        if yr.nunique() <= 1 or pr.nunique() <= 1:
            ic = np.nan
        else:
            ic = float(yr.corr(pr))

        ranked = g.sort_values(
            "pred",
            ascending=False
        ).reset_index(drop=True)

        top2 = float(
            ranked.iloc[:2]["raw_return"].mean()
        )

        bottom2 = float(
            ranked.iloc[-2:]["raw_return"].mean()
        )

        universe = float(
            ranked["raw_return"].mean()
        )

        rows.append({
            "date": day,
            "rank_ic": ic,

            # Diagnostic forward spreads only.
            # NOT a trading P&L because 5D targets overlap.
            "top2_minus_universe": top2 - universe,
            "top2_minus_bottom2": top2 - bottom2,
        })

    return pd.DataFrame(rows)


def moving_block_bootstrap_mean(
    values,
    block_length=5,
    iterations=2000,
    seed=20260825,
):

    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]

    n = len(x)

    if n < block_length:
        raise ValueError("Not enough observations for block bootstrap")

    rng = np.random.default_rng(seed)

    starts = np.arange(
        0,
        n - block_length + 1
    )

    estimates = np.empty(
        iterations,
        dtype=float
    )

    for i in range(iterations):

        sample = []

        while len(sample) < n:
            start = int(rng.choice(starts))
            sample.extend(
                x[start:start + block_length]
            )

        sample = np.asarray(
            sample[:n]
        )

        estimates[i] = np.mean(sample)

    return {
        "mean": float(np.mean(x)),
        "ci_low": float(np.quantile(estimates, 0.025)),
        "ci_high": float(np.quantile(estimates, 0.975)),
    }


def build_pipeline(
    numeric_features,
    categorical_features,
    alpha,
):

    numeric_pipe = Pipeline([
        (
            "impute",
            SimpleImputer(
                strategy="median",
                add_indicator=True,
            ),
        ),
        (
            "scale",
            StandardScaler(),
        ),
    ])

    transformers = [
        (
            "numeric",
            numeric_pipe,
            numeric_features,
        )
    ]

    if categorical_features:
        categorical_pipe = Pipeline([
            (
                "impute",
                SimpleImputer(
                    strategy="most_frequent"
                ),
            ),
            (
                "onehot",
                OneHotEncoder(
                    handle_unknown="ignore"
                ),
            ),
        ])

        transformers.append(
            (
                "categorical",
                categorical_pipe,
                categorical_features,
            )
        )

    preprocessor = ColumnTransformer(
        transformers=transformers,
        remainder="drop",
    )

    return Pipeline([
        (
            "preprocess",
            preprocessor,
        ),
        (
            "model",
            Ridge(
                alpha=alpha,
                solver="lsqr",
            ),
        ),
    ])


def resolve_feature_types(features):

    # Symbol is included as a common nuisance/fixed-effect control
    # in every model, so A/B/C/D comparisons measure incremental
    # sensor information above persistent symbol identity.
    categorical = ["symbol"]

    if "regime_bucket" in features:
        categorical.append(
            "regime_bucket"
        )

    numeric = [
        c for c in features
        if c not in categorical
    ]

    return numeric, categorical


def score_inner_fold(
    train_fold,
    test_fold,
    features,
    alpha,
):

    numeric, categorical = resolve_feature_types(
        features
    )

    model = build_pipeline(
        numeric,
        categorical,
        alpha,
    )

    model_features = list(
        dict.fromkeys(
            features + ["symbol"]
        )
    )

    model.fit(
        train_fold[model_features],
        train_fold[PRIMARY_TARGET],
    )

    pred = model.predict(
        test_fold[model_features]
    )

    daily = daily_spearman_table(
        test_fold["date"],
        test_fold[PRIMARY_TARGET].to_numpy(),
        pred,
        test_fold[PRIMARY_RAW_TARGET].to_numpy(),
    )

    return float(
        daily["rank_ic"].mean()
    )


def select_alpha(
    train,
    features,
):

    dates = np.array(
        sorted(
            train["date"].unique()
        )
    )

    tscv = TimeSeriesSplit(
        n_splits=INNER_SPLITS,
        gap=INNER_GAP_DATES,
    )

    alpha_results = []

    for alpha in ALPHAS:

        fold_scores = []

        for fold_no, (
            train_idx,
            test_idx
        ) in enumerate(
            tscv.split(dates),
            1
        ):

            train_dates = set(
                dates[train_idx]
            )
            test_dates = set(
                dates[test_idx]
            )

            tr = train[
                train["date"].isin(
                    train_dates
                )
            ]

            te = train[
                train["date"].isin(
                    test_dates
                )
            ]

            score = score_inner_fold(
                tr,
                te,
                features,
                alpha,
            )

            fold_scores.append(
                score
            )

        alpha_results.append({
            "alpha": alpha,
            "fold_rank_ic": fold_scores,
            "mean_rank_ic": float(
                np.nanmean(fold_scores)
            ),
        })

    ranked = sorted(
        alpha_results,
        key=lambda x: (
            -x["mean_rank_ic"],
            x["alpha"],
        )
    )

    return ranked[0]["alpha"], alpha_results


def main():

    warnings.filterwarnings(
        "ignore",
        category=RuntimeWarning,
    )

    print("=" * 100)
    print("SELF-LEARNING SENSOR FUSION V1 - MODEL 0")
    print("REGULARIZED LINEAR / VALIDATION ONLY")
    print("=" * 100)

    actual_sha = sha256_file(DATA)

    print()
    print("Frozen dataset SHA256:")
    print(actual_sha)

    if actual_sha != EXPECTED_SHA256:
        raise RuntimeError(
            "Frozen dataset hash mismatch. "
            "STOP: modeling population changed."
        )

    df = pd.read_csv(DATA)

    df["date"] = pd.to_datetime(
        df["date"]
    )

    # Convert known boolean fields explicitly.
    for c in BOOLEAN_COLUMNS:
        if c in df.columns:
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

    # ------------------------------------------------------------
    # CRITICAL:
    # HOLDOUT is deliberately removed immediately.
    # No HOLDOUT metrics, predictions, model selection or inspection.
    # ------------------------------------------------------------

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

    # Construct target independently within each period so no
    # target transformation uses HOLDOUT.
    train[PRIMARY_TARGET] = make_xs_target(
        train
    )

    valid[PRIMARY_TARGET] = make_xs_target(
        valid
    )

    print()
    print("PRIMARY TARGET:")
    print(
        "5-trading-day cross-sectional relative return "
        "(stock 5D return minus same-date 8-stock mean)"
    )

    print()
    print("TRAIN:")
    print(
        len(train),
        "rows /",
        train["date"].nunique(),
        "dates /",
        train["date"].min().date(),
        "->",
        train["date"].max().date(),
    )

    print("VALIDATION:")
    print(
        len(valid),
        "rows /",
        valid["date"].nunique(),
        "dates /",
        valid["date"].min().date(),
        "->",
        valid["date"].max().date(),
    )

    # Make sure every feature exists before any fit.
    missing_contract = {}

    for model_name, features in FEATURE_SETS.items():

        missing = [
            c for c in features
            if c not in df.columns
        ]

        if missing:
            missing_contract[
                model_name
            ] = missing

    if missing_contract:
        raise RuntimeError(
            "Feature contract mismatch: "
            + json.dumps(
                missing_contract,
                indent=2,
            )
        )

    results = {}
    prediction_frames = []

    for model_name, features in FEATURE_SETS.items():

        print()
        print("=" * 100)
        print(model_name)
        print("=" * 100)

        numeric, categorical = resolve_feature_types(
            features
        )

        model_features = list(
            dict.fromkeys(
                features + ["symbol"]
            )
        )

        print(
            "Feature count:",
            len(features),
            "+ symbol fixed effect"
        )

        print(
            "Selecting Ridge alpha using TRAIN-only "
            "date-grouped walk-forward..."
        )

        best_alpha, inner_results = select_alpha(
            train,
            features,
        )

        print("Selected alpha:", best_alpha)

        for r in inner_results:
            print(
                f"  alpha={r['alpha']:8g} "
                f"mean TRAIN-CV IC={r['mean_rank_ic']:+.5f} "
                f"folds="
                + ", ".join(
                    f"{x:+.4f}"
                    for x in r["fold_rank_ic"]
                )
            )

        model = build_pipeline(
            numeric,
            categorical,
            best_alpha,
        )

        model.fit(
            train[model_features],
            train[PRIMARY_TARGET],
        )

        pred = model.predict(
            valid[model_features]
        )

        y = valid[
            PRIMARY_TARGET
        ].to_numpy()

        raw = valid[
            PRIMARY_RAW_TARGET
        ].to_numpy()

        daily = daily_spearman_table(
            valid["date"],
            y,
            pred,
            raw,
        )

        pooled_corr = float(
            np.corrcoef(
                y,
                pred,
            )[0, 1]
        )

        rmse = float(
            mean_squared_error(
                y,
                pred
            ) ** 0.5
        )

        r2 = float(
            r2_score(
                y,
                pred,
            )
        )

        ic_boot = moving_block_bootstrap_mean(
            daily["rank_ic"],
            block_length=BOOTSTRAP_BLOCK,
            iterations=BOOTSTRAP_ITERATIONS,
            seed=BOOTSTRAP_SEED,
        )

        top2_boot = moving_block_bootstrap_mean(
            daily["top2_minus_universe"],
            block_length=BOOTSTRAP_BLOCK,
            iterations=BOOTSTRAP_ITERATIONS,
            seed=BOOTSTRAP_SEED + 1,
        )

        ls_boot = moving_block_bootstrap_mean(
            daily["top2_minus_bottom2"],
            block_length=BOOTSTRAP_BLOCK,
            iterations=BOOTSTRAP_ITERATIONS,
            seed=BOOTSTRAP_SEED + 2,
        )

        print()
        print("VALIDATION RESULTS")
        print(
            f"Daily rank IC mean     : "
            f"{ic_boot['mean']:+.5f}"
        )
        print(
            f"IC 95% block CI        : "
            f"[{ic_boot['ci_low']:+.5f}, "
            f"{ic_boot['ci_high']:+.5f}]"
        )
        print(
            f"Pooled correlation     : "
            f"{pooled_corr:+.5f}"
        )
        print(
            f"R2 cross-sectional     : "
            f"{r2:+.5f}"
        )
        print(
            f"RMSE                   : "
            f"{rmse:.6f}"
        )

        print()
        print(
            "Top2 - universe 5D diagnostic spread:"
        )
        print(
            f"  mean                 : "
            f"{top2_boot['mean'] * 10000:+.2f} bp"
        )
        print(
            f"  95% block CI         : "
            f"[{top2_boot['ci_low'] * 10000:+.2f}, "
            f"{top2_boot['ci_high'] * 10000:+.2f}] bp"
        )

        print(
            "Top2 - bottom2 5D diagnostic spread:"
        )
        print(
            f"  mean                 : "
            f"{ls_boot['mean'] * 10000:+.2f} bp"
        )
        print(
            f"  95% block CI         : "
            f"[{ls_boot['ci_low'] * 10000:+.2f}, "
            f"{ls_boot['ci_high'] * 10000:+.2f}] bp"
        )

        print(
            "NOTE: these 5D spreads are predictive diagnostics, "
            "NOT strategy P&L; adjacent 5D targets overlap."
        )

        results[model_name] = {
            "features": features,
            "selected_alpha": best_alpha,
            "inner_train_cv": inner_results,

            "validation": {
                "dates": int(
                    valid["date"].nunique()
                ),
                "rows": int(len(valid)),

                "daily_rank_ic": ic_boot,
                "pooled_correlation": pooled_corr,
                "r2": r2,
                "rmse": rmse,

                "top2_minus_universe": top2_boot,
                "top2_minus_bottom2": ls_boot,
            },
        }

        pred_frame = valid[
            [
                "date",
                "symbol",
                PRIMARY_RAW_TARGET,
            ]
        ].copy()

        pred_frame[
            PRIMARY_TARGET
        ] = y

        pred_frame[
            "prediction"
        ] = pred

        pred_frame[
            "model"
        ] = model_name

        prediction_frames.append(
            pred_frame
        )

    # ------------------------------------------------------------
    # Comparison table
    # ------------------------------------------------------------

    print()
    print("=" * 100)
    print("MODEL 0 VALIDATION COMPARISON")
    print("=" * 100)

    comparison = []

    for name, r in results.items():

        v = r["validation"]

        comparison.append({
            "model": name,
            "alpha": r["selected_alpha"],
            "rank_ic": v["daily_rank_ic"]["mean"],
            "ic_low": v["daily_rank_ic"]["ci_low"],
            "ic_high": v["daily_rank_ic"]["ci_high"],
            "top2_universe_bp":
                v["top2_minus_universe"]["mean"] * 10000,
            "top2_bottom2_bp":
                v["top2_minus_bottom2"]["mean"] * 10000,
            "r2": v["r2"],
        })

    comp = pd.DataFrame(
        comparison
    ).sort_values(
        "rank_ic",
        ascending=False,
    )

    print(
        comp.to_string(
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
                "top2_bottom2_bp":
                    lambda x: f"{x:+.2f}",
                "r2":
                    lambda x: f"{x:+.5f}",
            },
        )
    )

    output = {
        "schema":
            "SELF_LEARNING_SENSOR_FUSION_V1_MODEL0_LINEAR_VALIDATION",

        "frozen_dataset":
            DATA.name,

        "frozen_dataset_sha256":
            actual_sha,

        "primary_target":
            PRIMARY_TARGET,

        "primary_target_definition":
            "5D stock forward return minus same-date "
            "equal-weight 8-stock forward-return mean",

        "model_family":
            "Ridge linear regression",

        "alpha_candidates":
            ALPHAS,

        "inner_cv":
            {
                "method":
                    "TRAIN-only expanding TimeSeriesSplit by date",
                "splits":
                    INNER_SPLITS,
                "gap_dates":
                    INNER_GAP_DATES,
                "selection_metric":
                    "mean daily cross-sectional Spearman rank IC",
            },

        "validation_policy":
            "A/B/C/D evaluated once on VALIDATION. "
            "No HOLDOUT predictions or metrics generated.",

        "holdout_touched":
            False,

        "results":
            results,
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
    print("RESULT JSON :", OUT_JSON)
    print("PREDICTIONS :", OUT_PRED)

    print()
    print("=" * 100)
    print("MODEL 0 COMPLETE - HOLDOUT NOT EVALUATED")
    print("=" * 100)


if __name__ == "__main__":
    main()
