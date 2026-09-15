from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import roc_auc_score

from run_self_learning_risk_head_v1_model0 import (
    prepare_numeric_matrix,
    rank_corr,
)


ROOT = Path(__file__).resolve().parent

PREREG = ROOT / "EXOGENOUS_CONTEXT_V1_PREREGISTERED_20260825.json"
FUSION = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"
BASELINE_JSON = ROOT / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json"

EXO_DIR = ROOT / "EXOGENOUS_CONTEXT_V1_RAW_20160101_20260825"

VIX_FILE = EXO_DIR / "INDIA_VIX_15MIN_20160101_20260825.csv"
BANK_FILE = EXO_DIR / "NIFTY_BANK_15MIN_20160101_20260825.csv"
FUT_FILE = EXO_DIR / "NIFTY_FUTURES_CONTINUOUS_DAILY_OI_20160101_20260825.csv"

GUARD = ROOT / "EXOGENOUS_CONTEXT_V1_2025_VALIDATION_OPENED_20260825.guard.json"

RESULT_JSON = ROOT / "EXOGENOUS_CONTEXT_V1_ONE_TIME_RESULT_20260825.json"
RESULT_MD = ROOT / "EXOGENOUS_CONTEXT_V1_ONE_TIME_RESULT_20260825.md"
RESULT_SHA = ROOT / "EXOGENOUS_CONTEXT_V1_ONE_TIME_RESULT_20260825.sha256"

PREDICTIONS = ROOT / "EXOGENOUS_CONTEXT_V1_ONE_TIME_PREDICTIONS_20260825.csv"

EXPECTED_PREREG_SHA = (
    "c3a3af9c5d6e7d34c3f0a759243cb801"
    "f588b3e350ce21730e9222b11fa7ff34"
)

ALPHA = 1000.0
SOLVER = "lsqr"

EVENT_THRESHOLD = 0.020

BOOTSTRAP_ITERATIONS = 10000
BOOTSTRAP_SEED = 20260825


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def require_files():
    for p in [
        PREREG,
        FUSION,
        BASELINE_JSON,
        VIX_FILE,
        BANK_FILE,
        FUT_FILE,
    ]:
        if not p.exists():
            raise RuntimeError(
                f"Missing required input: {p.name}"
            )

    if GUARD.exists():
        raise RuntimeError(
            "SECOND EXECUTION PROHIBITED: "
            f"{GUARD.name} already exists."
        )

    for p in [
        RESULT_JSON,
        RESULT_MD,
        RESULT_SHA,
        PREDICTIONS,
    ]:
        if p.exists():
            raise RuntimeError(
                "Result artifact already exists: "
                f"{p.name}"
            )


def verify_preregistration():
    actual = sha256_file(PREREG)

    if actual != EXPECTED_PREREG_SHA:
        raise RuntimeError(
            "Preregistration SHA mismatch."
        )

    obj = json.loads(
        PREREG.read_text(
            encoding="utf-8"
        )
    )

    if obj.get("status") != (
        "FROZEN_BEFORE_EXOGENOUS_OUTCOME_EVALUATION"
    ):
        raise RuntimeError(
            "Unexpected preregistration status."
        )

    hashes = obj["artifact_hashes"]

    for p in [
        FUSION,
        BASELINE_JSON,
        VIX_FILE,
        BANK_FILE,
        FUT_FILE,
    ]:
        expected = hashes.get(p.name)

        if expected is None:
            raise RuntimeError(
                f"Preregistration lacks hash for {p.name}"
            )

        actual = sha256_file(p)

        if actual != expected:
            raise RuntimeError(
                f"Input hash mismatch: {p.name}"
            )

    return obj


def build_daily_exogenous() -> pd.DataFrame:
    vix = pd.read_csv(VIX_FILE)
    bank = pd.read_csv(BANK_FILE)
    fut = pd.read_csv(FUT_FILE)

    for df in [vix, bank, fut]:
        df["date"] = pd.to_datetime(
            df["date"],
            errors="raise",
        )

    # --------------------------------------------------------
    # INDIA VIX:
    # final available 15-minute close for each source date.
    # --------------------------------------------------------

    vix = vix.sort_values(
        "date"
    ).copy()

    vix["research_date"] = (
        vix["date"].dt.normalize()
    )

    vix_daily = (
        vix.groupby(
            "research_date",
            as_index=False,
        )
        .tail(1)
        [
            [
                "research_date",
                "close",
            ]
        ]
        .sort_values(
            "research_date"
        )
        .reset_index(
            drop=True
        )
    )

    vix_daily["close"] = pd.to_numeric(
        vix_daily["close"],
        errors="raise",
    )

    if (
        vix_daily["close"] <= 0
    ).any():
        raise RuntimeError(
            "Non-positive VIX close."
        )

    vix_daily[
        "exo_vix_log_close"
    ] = np.log(
        vix_daily["close"]
    )

    vix_daily[
        "exo_vix_log_change_1d"
    ] = (
        vix_daily[
            "exo_vix_log_close"
        ]
        .diff()
    )

    vix_daily = vix_daily[
        [
            "research_date",
            "exo_vix_log_close",
            "exo_vix_log_change_1d",
        ]
    ]

    # --------------------------------------------------------
    # NIFTY BANK:
    # final available 15-minute close for each source date.
    # --------------------------------------------------------

    bank = bank.sort_values(
        "date"
    ).copy()

    bank["research_date"] = (
        bank["date"].dt.normalize()
    )

    bank_daily = (
        bank.groupby(
            "research_date",
            as_index=False,
        )
        .tail(1)
        [
            [
                "research_date",
                "close",
            ]
        ]
        .sort_values(
            "research_date"
        )
        .reset_index(
            drop=True
        )
    )

    bank_daily["close"] = pd.to_numeric(
        bank_daily["close"],
        errors="raise",
    )

    if (
        bank_daily["close"] <= 0
    ).any():
        raise RuntimeError(
            "Non-positive NIFTY BANK close."
        )

    bank_daily[
        "bank_log_return_1d"
    ] = (
        np.log(
            bank_daily["close"]
        )
        .diff()
    )

    bank_daily = bank_daily[
        [
            "research_date",
            "bank_log_return_1d",
        ]
    ]

    # --------------------------------------------------------
    # Continuous NIFTY future:
    # daily close + OI.
    # --------------------------------------------------------

    fut = fut.sort_values(
        "date"
    ).copy()

    fut["research_date"] = (
        fut["date"].dt.normalize()
    )

    if (
        fut[
            "research_date"
        ].duplicated()
    ).any():
        raise RuntimeError(
            "Duplicate NIFTY futures dates."
        )

    fut["close"] = pd.to_numeric(
        fut["close"],
        errors="raise",
    )

    fut["oi"] = pd.to_numeric(
        fut["oi"],
        errors="raise",
    )

    modern = fut[
        fut[
            "research_date"
        ]
        >= pd.Timestamp(
            "2023-01-01"
        )
    ]

    if (
        modern["close"] <= 0
    ).any():
        raise RuntimeError(
            "Non-positive modern futures close."
        )

    if (
        modern["oi"] <= 0
    ).any():
        raise RuntimeError(
            "Missing/non-positive modern futures OI."
        )

    fut[
        "exo_nifty_fut_log_return_1d"
    ] = (
        np.log(
            fut["close"]
        )
        .diff()
    )

    # OI logs are valid only where both current and previous
    # values are strictly positive.
    log_oi = pd.Series(
        np.nan,
        index=fut.index,
        dtype=float,
    )

    positive_oi = fut["oi"] > 0

    log_oi.loc[
        positive_oi
    ] = np.log(
        fut.loc[
            positive_oi,
            "oi",
        ]
    )

    fut[
        "exo_nifty_fut_log_oi_change_1d"
    ] = log_oi.diff()

    fut_daily = fut[
        [
            "research_date",
            "exo_nifty_fut_log_return_1d",
            "exo_nifty_fut_log_oi_change_1d",
        ]
    ].copy()

    # --------------------------------------------------------
    # Join certified market-level sources by exact source date.
    # --------------------------------------------------------

    exo = (
        vix_daily
        .merge(
            bank_daily,
            on="research_date",
            how="inner",
            validate="one_to_one",
        )
        .merge(
            fut_daily,
            on="research_date",
            how="inner",
            validate="one_to_one",
        )
    )

    exo[
        "exo_bank_minus_nifty_fut_log_return_1d"
    ] = (
        exo[
            "bank_log_return_1d"
        ]
        -
        exo[
            "exo_nifty_fut_log_return_1d"
        ]
    )

    exo[
        "exo_nifty_fut_price_oi_interaction"
    ] = (
        exo[
            "exo_nifty_fut_log_return_1d"
        ]
        *
        exo[
            "exo_nifty_fut_log_oi_change_1d"
        ]
    )

    exo = exo[
        [
            "research_date",
            "exo_vix_log_close",
            "exo_vix_log_change_1d",
            "exo_nifty_fut_log_return_1d",
            "exo_bank_minus_nifty_fut_log_return_1d",
            "exo_nifty_fut_log_oi_change_1d",
            "exo_nifty_fut_price_oi_interaction",
        ]
    ].copy()

    return exo


def event_auc(
    adverse: np.ndarray,
    prediction: np.ndarray,
) -> float:
    y = (
        np.asarray(
            adverse,
            dtype=float,
        )
        >= EVENT_THRESHOLD
    ).astype(int)

    if np.unique(y).size < 2:
        return np.nan

    return float(
        roc_auc_score(
            y,
            prediction,
        )
    )


def paired_date_bootstrap(
    frame: pd.DataFrame,
):
    """
    Preregistered paired DATE-cluster bootstrap.

    Each sampled date carries all eight symbols together.
    Dates are sampled with replacement.
    """

    dates = np.array(
        sorted(
            frame["date"].unique()
        )
    )

    if len(dates) != 228:
        raise RuntimeError(
            f"Expected 228 validation dates, "
            f"found {len(dates)}"
        )

    groups = []

    for d in dates:
        idx = np.flatnonzero(
            frame["date"].to_numpy()
            == d
        )

        if len(idx) != 8:
            raise RuntimeError(
                f"Date {d} has {len(idx)} rows, expected 8."
            )

        groups.append(idx)

    adverse = frame[
        "adverse_1d"
    ].to_numpy(
        dtype=float
    )

    baseline = frame[
        "baseline_prediction"
    ].to_numpy(
        dtype=float
    )

    challenger = frame[
        "challenger_prediction"
    ].to_numpy(
        dtype=float
    )

    rng = np.random.default_rng(
        BOOTSTRAP_SEED
    )

    delta_spearman = np.empty(
        BOOTSTRAP_ITERATIONS,
        dtype=float,
    )

    delta_auc = np.empty(
        BOOTSTRAP_ITERATIONS,
        dtype=float,
    )

    n_dates = len(dates)

    for i in range(
        BOOTSTRAP_ITERATIONS
    ):
        sampled_date_positions = (
            rng.integers(
                0,
                n_dates,
                size=n_dates,
            )
        )

        idx = np.concatenate(
            [
                groups[j]
                for j
                in sampled_date_positions
            ]
        )

        y = adverse[idx]
        b = baseline[idx]
        c = challenger[idx]

        b_s = rank_corr(
            y,
            b,
        )

        c_s = rank_corr(
            y,
            c,
        )

        delta_spearman[i] = (
            c_s - b_s
        )

        b_auc = event_auc(
            y,
            b,
        )

        c_auc = event_auc(
            y,
            c,
        )

        delta_auc[i] = (
            c_auc - b_auc
        )

    ds = delta_spearman[
        np.isfinite(
            delta_spearman
        )
    ]

    da = delta_auc[
        np.isfinite(
            delta_auc
        )
    ]

    if len(ds) < (
        BOOTSTRAP_ITERATIONS
        * 0.99
    ):
        raise RuntimeError(
            "Too many invalid Spearman bootstrap replicates."
        )

    if len(da) < (
        BOOTSTRAP_ITERATIONS
        * 0.99
    ):
        raise RuntimeError(
            "Too many invalid AUC bootstrap replicates."
        )

    return {
        "delta_spearman": {
            "valid_replicates":
                int(len(ds)),

            "ci_low":
                float(
                    np.quantile(
                        ds,
                        0.025,
                    )
                ),

            "ci_high":
                float(
                    np.quantile(
                        ds,
                        0.975,
                    )
                ),
        },

        "delta_auc": {
            "valid_replicates":
                int(len(da)),

            "ci_low":
                float(
                    np.quantile(
                        da,
                        0.025,
                    )
                ),

            "ci_high":
                float(
                    np.quantile(
                        da,
                        0.975,
                    )
                ),
        },
    }


def bootstrap_self_test():
    """
    Offline structural test before examination guard opens.
    """

    rows = []

    for d in pd.date_range(
        "2020-01-01",
        periods=10,
        freq="D",
    ):
        for symbol_i in range(8):
            x = float(
                symbol_i
                + d.day / 100.0
            )

            rows.append({
                "date": d,
                "adverse_1d":
                    x / 100.0,

                "baseline_prediction":
                    x,

                "challenger_prediction":
                    x + 0.001,
            })

    test = pd.DataFrame(rows)

    # Do not run the expensive 10k function here.
    # Structural assertions only.
    assert (
        test.groupby("date")
        .size()
        .eq(8)
        .all()
    )

    assert np.isfinite(
        rank_corr(
            test["adverse_1d"],
            test["baseline_prediction"],
        )
    )


def main():
    require_files()

    prereg = verify_preregistration()

    bootstrap_self_test()

    baseline_obj = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8"
        )
    )

    baseline_features = (
        baseline_obj[
            "candidate"
        ][
            "feature_list"
        ]
    )

    exogenous_features = (
        prereg[
            "augmented_model"
        ][
            "exogenous_features"
        ]
    )

    if len(
        baseline_features
    ) != 25:
        raise RuntimeError(
            "Frozen baseline feature count changed."
        )

    if len(
        exogenous_features
    ) != 6:
        raise RuntimeError(
            "Frozen exogenous feature count changed."
        )

    # --------------------------------------------------------
    # Load frozen modeling population.
    # --------------------------------------------------------

    df = pd.read_csv(
        FUSION
    )

    df["date"] = pd.to_datetime(
        df["date"],
        errors="raise",
    ).dt.normalize()

    df["mae_1d"] = pd.to_numeric(
        df["mae_1d"],
        errors="raise",
    )

    df[
        "adverse_1d"
    ] = (
        -df["mae_1d"]
    ).clip(
        lower=0.0
    )

    for feature in baseline_features:
        if feature not in df.columns:
            raise RuntimeError(
                f"Missing frozen baseline feature: {feature}"
            )

        if (
            feature != "regime_bucket"
        ):
            df[
                feature
            ] = pd.to_numeric(
                df[feature],
                errors="coerce",
            )

    train_original = df[
        df[
            "split_role"
        ]
        == "TRAIN"
    ].copy()

    valid_original = df[
        df[
            "split_role"
        ]
        == "VALIDATION"
    ].copy()

    if len(
        train_original
    ) != 2336:
        raise RuntimeError(
            f"Unexpected TRAIN rows: {len(train_original)}"
        )

    if len(
        valid_original
    ) != 1824:
        raise RuntimeError(
            f"Unexpected VALIDATION rows: {len(valid_original)}"
        )

    if (
        train_original[
            "date"
        ].nunique()
        != 292
    ):
        raise RuntimeError(
            "Unexpected TRAIN date count."
        )

    if (
        valid_original[
            "date"
        ].nunique()
        != 228
    ):
        raise RuntimeError(
            "Unexpected VALIDATION date count."
        )

    # --------------------------------------------------------
    # Reproduce frozen Candidate B BEFORE joining challenger.
    # These baseline validation outcomes were already opened in
    # the original Risk Head experiment.
    # --------------------------------------------------------

    X_train_b, X_valid_b = (
        prepare_numeric_matrix(
            train_original,
            valid_original,
            baseline_features,
        )
    )

    baseline_model = Ridge(
        alpha=ALPHA,
        solver=SOLVER,
    )

    baseline_model.fit(
        X_train_b,
        train_original[
            "adverse_1d"
        ],
    )

    baseline_pred_original = (
        baseline_model.predict(
            X_valid_b
        )
    )

    baseline_spearman_original = (
        rank_corr(
            valid_original[
                "adverse_1d"
            ],
            baseline_pred_original,
        )
    )

    baseline_auc_original = (
        event_auc(
            valid_original[
                "adverse_1d"
            ].to_numpy(),
            baseline_pred_original,
        )
    )

    expected_spearman = (
        baseline_obj[
            "validation_results_linear_B"
        ][
            "risk_spearman"
        ][
            "estimate"
        ]
    )

    expected_auc = (
        baseline_obj[
            "validation_results_linear_B"
        ][
            "event_2pct_roc_auc"
        ][
            "estimate"
        ]
    )

    if not np.isclose(
        baseline_spearman_original,
        expected_spearman,
        rtol=0.0,
        atol=1e-12,
    ):
        raise RuntimeError(
            "Frozen baseline Spearman reproduction FAILED: "
            f"{baseline_spearman_original} "
            f"vs {expected_spearman}"
        )

    if not np.isclose(
        baseline_auc_original,
        expected_auc,
        rtol=0.0,
        atol=1e-12,
    ):
        raise RuntimeError(
            "Frozen baseline AUC reproduction FAILED: "
            f"{baseline_auc_original} "
            f"vs {expected_auc}"
        )

    print("=" * 110)
    print(
        "EXOGENOUS CONTEXT V1 - ONE-TIME 2025 VALIDATION"
    )
    print("=" * 110)

    print()
    print(
        "Frozen baseline reproduction Spearman:",
        baseline_spearman_original,
    )

    print(
        "Expected                            :",
        expected_spearman,
    )

    print(
        "Frozen baseline reproduction AUC    :",
        baseline_auc_original,
    )

    print(
        "Expected                            :",
        expected_auc,
    )

    print()
    print(
        "BASELINE REPRODUCTION: PASS"
    )

    # --------------------------------------------------------
    # Build causal exogenous features.
    # No target data used in construction.
    # --------------------------------------------------------

    exo = build_daily_exogenous()

    train = (
        train_original
        .merge(
            exo,
            left_on="date",
            right_on="research_date",
            how="left",
            validate="many_to_one",
        )
        .drop(
            columns=[
                "research_date"
            ]
        )
    )

    valid = (
        valid_original
        .merge(
            exo,
            left_on="date",
            right_on="research_date",
            how="left",
            validate="many_to_one",
        )
        .drop(
            columns=[
                "research_date"
            ]
        )
    )

    train_missing = (
        train[
            exogenous_features
        ]
        .isna()
        .any(axis=1)
    )

    valid_missing = (
        valid[
            exogenous_features
        ]
        .isna()
        .any(axis=1)
    )

    print()
    print(
        "TRAIN exogenous-missing rows     :",
        int(
            train_missing.sum()
        )
    )

    print(
        "VALIDATION exogenous-missing rows:",
        int(
            valid_missing.sum()
        )
    )

    # Certified modern source coverage implies zero exclusions.
    # Abort before opening exam if that is not true.
    if train_missing.any():
        raise RuntimeError(
            "Unexpected TRAIN exogenous missingness. "
            "Exam remains unopened."
        )

    if valid_missing.any():
        raise RuntimeError(
            "Unexpected VALIDATION exogenous missingness. "
            "Exam remains unopened."
        )

    if not (
        train.groupby("date")
        .size()
        .eq(8)
        .all()
    ):
        raise RuntimeError(
            "TRAIN date cluster integrity failed."
        )

    if not (
        valid.groupby("date")
        .size()
        .eq(8)
        .all()
    ):
        raise RuntimeError(
            "VALIDATION date cluster integrity failed."
        )

    # --------------------------------------------------------
    # Fit both models on identical TRAIN population.
    # --------------------------------------------------------

    X_train_base, X_valid_base = (
        prepare_numeric_matrix(
            train,
            valid,
            baseline_features,
        )
    )

    model_base = Ridge(
        alpha=ALPHA,
        solver=SOLVER,
    )

    model_base.fit(
        X_train_base,
        train[
            "adverse_1d"
        ],
    )

    baseline_pred = (
        model_base.predict(
            X_valid_base
        )
    )

    augmented_features = (
        baseline_features
        + exogenous_features
    )

    X_train_ch, X_valid_ch = (
        prepare_numeric_matrix(
            train,
            valid,
            augmented_features,
        )
    )

    model_ch = Ridge(
        alpha=ALPHA,
        solver=SOLVER,
    )

    model_ch.fit(
        X_train_ch,
        train[
            "adverse_1d"
        ],
    )

    challenger_pred = (
        model_ch.predict(
            X_valid_ch
        )
    )

    # Predictions themselves do not use VALIDATION outcomes.
    prediction_frame = valid[
        [
            "date",
            "symbol",
            "adverse_1d",
        ]
    ].copy()

    prediction_frame[
        "baseline_prediction"
    ] = baseline_pred

    prediction_frame[
        "challenger_prediction"
    ] = challenger_pred

    # Baseline on identical joined population must still match.
    baseline_spearman = (
        rank_corr(
            prediction_frame[
                "adverse_1d"
            ],
            prediction_frame[
                "baseline_prediction"
            ],
        )
    )

    baseline_auc = (
        event_auc(
            prediction_frame[
                "adverse_1d"
            ].to_numpy(),
            prediction_frame[
                "baseline_prediction"
            ].to_numpy(),
        )
    )

    if not np.isclose(
        baseline_spearman,
        expected_spearman,
        rtol=0.0,
        atol=1e-12,
    ):
        raise RuntimeError(
            "Joined-population baseline reproduction failed. "
            "Exam remains unopened."
        )

    if not np.isclose(
        baseline_auc,
        expected_auc,
        rtol=0.0,
        atol=1e-12,
    ):
        raise RuntimeError(
            "Joined-population baseline AUC reproduction failed. "
            "Exam remains unopened."
        )

    # --------------------------------------------------------
    # LAST PRE-EXAM CHECK.
    # --------------------------------------------------------

    print()
    print(
        "Joined TRAIN rows      :",
        len(train)
    )

    print(
        "Joined VALIDATION rows :",
        len(valid)
    )

    print(
        "Joined TRAIN dates     :",
        train["date"].nunique()
    )

    print(
        "Joined VALIDATION dates:",
        valid["date"].nunique()
    )

    print(
        "Exogenous features     :",
        len(exogenous_features)
    )

    print(
        "Bootstrap              :",
        BOOTSTRAP_ITERATIONS,
        "paired date-level replicates"
    )

    # --------------------------------------------------------
    # OPEN ONE-TIME EXAM.
    #
    # From this point onward, rerunning is prohibited even if
    # later output is unfavorable.
    # --------------------------------------------------------

    script_sha = sha256_file(
        Path(__file__).resolve()
    )

    guard_obj = {
        "schema":
            "EXOGENOUS_CONTEXT_V1_2025_VALIDATION_GUARD",

        "opened_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "status":
            "OPENED_SECOND_EXECUTION_PROHIBITED",

        "preregistration_sha256":
            EXPECTED_PREREG_SHA,

        "validator_script":
            Path(__file__).name,

        "validator_script_sha256":
            script_sha,

        "validation_rows":
            len(valid),

        "validation_dates":
            valid[
                "date"
            ].nunique(),

        "bootstrap_iterations":
            BOOTSTRAP_ITERATIONS,

        "bootstrap_unit":
            "DATE_CLUSTER_ALL_8_SYMBOLS",

        "bootstrap_seed":
            BOOTSTRAP_SEED,

        "second_execution_prohibited":
            True,
    }

    with GUARD.open(
        "x",
        encoding="utf-8",
    ) as f:
        json.dump(
            guard_obj,
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")

    print()
    print(
        "ONE-TIME VALIDATION GUARD: COMMITTED"
    )

    print(
        "2025 EXOGENOUS OUTCOME EXAM: OPEN"
    )

    # --------------------------------------------------------
    # First and only challenger outcome evaluation.
    # --------------------------------------------------------

    challenger_spearman = (
        rank_corr(
            prediction_frame[
                "adverse_1d"
            ],
            prediction_frame[
                "challenger_prediction"
            ],
        )
    )

    challenger_auc = (
        event_auc(
            prediction_frame[
                "adverse_1d"
            ].to_numpy(),
            prediction_frame[
                "challenger_prediction"
            ].to_numpy(),
        )
    )

    delta_spearman = (
        challenger_spearman
        - baseline_spearman
    )

    delta_auc = (
        challenger_auc
        - baseline_auc
    )

    bootstrap = (
        paired_date_bootstrap(
            prediction_frame
        )
    )

    primary_ci = (
        bootstrap[
            "delta_spearman"
        ]
    )

    secondary_ci = (
        bootstrap[
            "delta_auc"
        ]
    )

    primary_pass = bool(
        challenger_spearman
        > baseline_spearman

        and

        delta_spearman > 0.0

        and

        primary_ci[
            "ci_low"
        ] > 0.0
    )

    secondary_pass = bool(
        challenger_auc
        > baseline_auc

        and

        delta_auc > 0.0

        and

        secondary_ci[
            "ci_low"
        ] > 0.0
    )

    classification = (
        "GO_RESEARCH_SIGNAL"
        if primary_pass
        else "NO_GO"
    )

    event_rate = float(
        (
            prediction_frame[
                "adverse_1d"
            ]
            >= EVENT_THRESHOLD
        ).mean()
    )

    # --------------------------------------------------------
    # Write immutable one-time outputs.
    # --------------------------------------------------------

    prediction_frame.to_csv(
        PREDICTIONS,
        index=False,
        lineterminator="\n",
    )

    result = {
        "schema":
            "EXOGENOUS_CONTEXT_V1_ONE_TIME_RESULT",

        "evaluated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "classification":
            classification,

        "primary_pass":
            primary_pass,

        "secondary_pass":
            secondary_pass,

        "production_authorized":
            False,

        "prospective_confirmation_required":
            (
                classification
                == "GO_RESEARCH_SIGNAL"
            ),

        "second_2025_attempt_prohibited":
            True,

        "2026_rescue_prohibited":
            True,

        "population": {
            "train_rows":
                len(train),

            "train_dates":
                train[
                    "date"
                ].nunique(),

            "validation_rows":
                len(valid),

            "validation_dates":
                valid[
                    "date"
                ].nunique(),

            "symbols":
                valid[
                    "symbol"
                ].nunique(),

            "validation_event_rate_2pct":
                event_rate,
        },

        "baseline": {
            "spearman":
                float(
                    baseline_spearman
                ),

            "roc_auc":
                float(
                    baseline_auc
                ),
        },

        "challenger": {
            "spearman":
                float(
                    challenger_spearman
                ),

            "roc_auc":
                float(
                    challenger_auc
                ),
        },

        "primary": {
            "metric":
                "challenger_minus_baseline_spearman",

            "estimate":
                float(
                    delta_spearman
                ),

            "ci_low":
                primary_ci[
                    "ci_low"
                ],

            "ci_high":
                primary_ci[
                    "ci_high"
                ],

            "bootstrap_valid_replicates":
                primary_ci[
                    "valid_replicates"
                ],

            "pass":
                primary_pass,
        },

        "secondary": {
            "metric":
                "challenger_minus_baseline_roc_auc",

            "estimate":
                float(
                    delta_auc
                ),

            "ci_low":
                secondary_ci[
                    "ci_low"
                ],

            "ci_high":
                secondary_ci[
                    "ci_high"
                ],

            "bootstrap_valid_replicates":
                secondary_ci[
                    "valid_replicates"
                ],

            "pass":
                secondary_pass,
        },

        "bootstrap": {
            "iterations":
                BOOTSTRAP_ITERATIONS,

            "unit":
                "DATE_CLUSTER_ALL_8_SYMBOLS",

            "sampling":
                "dates sampled with replacement",

            "seed":
                BOOTSTRAP_SEED,
        },

        "features": {
            "baseline_count":
                len(
                    baseline_features
                ),

            "exogenous_count":
                len(
                    exogenous_features
                ),

            "exogenous":
                exogenous_features,
        },

        "artifact_hashes": {
            "preregistration":
                sha256_file(
                    PREREG
                ),

            "guard":
                sha256_file(
                    GUARD
                ),

            "validator":
                script_sha,

            "predictions":
                sha256_file(
                    PREDICTIONS
                ),
        },
    }

    RESULT_JSON.write_text(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result_sha = sha256_file(
        RESULT_JSON
    )

    md = f"""# Exogenous Context V1 — One-Time 2025 Validation

**Classification: {classification}**

## Primary

Baseline Spearman: {baseline_spearman:+.9f}

Challenger Spearman: {challenger_spearman:+.9f}

Delta Spearman: {delta_spearman:+.9f}

95% paired date-bootstrap CI:
[{primary_ci['ci_low']:+.9f}, {primary_ci['ci_high']:+.9f}]

Primary pass: {primary_pass}

## Secondary

Baseline ROC-AUC: {baseline_auc:.9f}

Challenger ROC-AUC: {challenger_auc:.9f}

Delta ROC-AUC: {delta_auc:+.9f}

95% paired date-bootstrap CI:
[{secondary_ci['ci_low']:+.9f}, {secondary_ci['ci_high']:+.9f}]

Secondary pass: {secondary_pass}

## Research boundary

2025 has now been examined once for the frozen six-feature
Exogenous Context V1 challenger.

A second 2025 attempt is prohibited.

2026 may not be used to rescue a failure.

Production authorization remains false.

P01D remains unchanged and sovereign.

Result JSON SHA256: `{result_sha}`
"""

    RESULT_MD.write_text(
        md,
        encoding="utf-8",
    )

    md_sha = sha256_file(
        RESULT_MD
    )

    RESULT_SHA.write_text(
        f"{result_sha}  {RESULT_JSON.name}\n"
        f"{md_sha}  {RESULT_MD.name}\n"
        f"{sha256_file(PREDICTIONS)}  {PREDICTIONS.name}\n"
        f"{sha256_file(GUARD)}  {GUARD.name}\n",
        encoding="utf-8",
    )

    print()
    print("=" * 110)
    print("ONE-TIME RESULT")
    print("=" * 110)

    print()
    print(
        "Baseline Spearman   :",
        baseline_spearman
    )

    print(
        "Challenger Spearman :",
        challenger_spearman
    )

    print(
        "Delta Spearman      :",
        delta_spearman
    )

    print(
        "Delta Spearman 95% CI:",
        (
            primary_ci[
                "ci_low"
            ],
            primary_ci[
                "ci_high"
            ],
        )
    )

    print(
        "PRIMARY PASS        :",
        primary_pass
    )

    print()
    print(
        "Baseline ROC-AUC    :",
        baseline_auc
    )

    print(
        "Challenger ROC-AUC  :",
        challenger_auc
    )

    print(
        "Delta ROC-AUC       :",
        delta_auc
    )

    print(
        "Delta ROC-AUC 95% CI:",
        (
            secondary_ci[
                "ci_low"
            ],
            secondary_ci[
                "ci_high"
            ],
        )
    )

    print(
        "SECONDARY PASS      :",
        secondary_pass
    )

    print()
    print(
        "CLASSIFICATION      :",
        classification
    )

    print(
        "Production          : FALSE"
    )

    print(
        "Second 2025 attempt : PROHIBITED"
    )

    print(
        "2026 rescue         : PROHIBITED"
    )

    print()
    print(
        "Result JSON SHA256  :",
        result_sha
    )

    print(
        "Predictions SHA256  :",
        sha256_file(
            PREDICTIONS
        )
    )

    print(
        "Guard SHA256        :",
        sha256_file(
            GUARD
        )
    )

    print()
    print("=" * 110)
    print(
        "2025 EXOGENOUS CONTEXT V1 EXAM CLOSED"
    )
    print("=" * 110)


if __name__ == "__main__":
    main()
