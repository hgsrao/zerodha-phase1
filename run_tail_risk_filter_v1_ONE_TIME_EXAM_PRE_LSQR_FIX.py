from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os

import numpy as np
import pandas as pd

from sklearn.linear_model import Ridge
from sklearn.metrics import roc_auc_score, average_precision_score

import run_self_learning_risk_head_v1_model0 as r


ROOT = Path.cwd()

# ---------------------------------------------------------------------
# LOCKED SOURCES
# ---------------------------------------------------------------------

FROZEN_DATA = (
    ROOT
    / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"
)

POST_DATA = (
    ROOT
    / "daily_multitimescale_sensor_fusion_v1_20260825.csv"
)

FREEZE_FILE = (
    ROOT
    / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json"
)

CLOSURE_FILE = (
    ROOT
    / "RISK_HEAD_V1_FINAL_CLOSURE_20260825.json"
)

PREREG_FILE = (
    ROOT
    / "TAIL_RISK_FILTER_V1_PREREGISTERED_20260825.json"
)

# ---------------------------------------------------------------------
# ONE-TIME OUTPUTS / GUARD
# ---------------------------------------------------------------------

GUARD_FILE = (
    ROOT
    / "TAIL_RISK_FILTER_V1_ONE_TIME_EXAM_OPENED_20260825.guard.json"
)

OUT_JSON = (
    ROOT
    / "TAIL_RISK_FILTER_V1_ONE_TIME_RESULT_20260825.json"
)

OUT_MD = (
    ROOT
    / "TAIL_RISK_FILTER_V1_ONE_TIME_RESULT_20260825.md"
)

OUT_CSV = (
    ROOT
    / "TAIL_RISK_FILTER_V1_ONE_TIME_PREDICTIONS_20260825.csv"
)

OUT_SHA = (
    ROOT
    / "TAIL_RISK_FILTER_V1_ONE_TIME_RESULT_20260825.sha256"
)

EXPECTED_DATASET_SHA = (
    "2f51f697982063233ee120f0fbd93989"
    "d523efe868f810564efa26ef8099c888"
)

EXPECTED_FREEZE_SHA = (
    "b018eaaccc8096c0ceb749e8ae087f8"
    "cd8348e2ebbe8827383c978a48fe61adc"
)

EXPECTED_CLOSURE_SHA = (
    "5aa6c821f7eaee040fa38cedf634eb2a"
    "9e6f6b7289ddb33d28dcc3bcd0cd1ea0"
)

EXPECTED_PREREG_SHA = (
    "052b5b07ebcb1b4434342bccee698bde"
    "00f043102605dca0fcb824ce89beaf28"
)

ALPHA = 1000.0
EVENT_THRESHOLD = 0.020

TRAIN_START = pd.Timestamp("2023-09-22")
TRAIN_END = pd.Timestamp("2024-12-02")

VALID_START = pd.Timestamp("2025-01-01")
VALID_END = pd.Timestamp("2025-12-02")

POST_START = pd.Timestamp("2026-07-28")
POST_END = pd.Timestamp("2026-08-21")

CAS_START = pd.Timestamp("2026-08-03")

BOOTSTRAP_ITERATIONS = 10_000
BOOTSTRAP_SEED = 20260825

EXPECTED_TRAIN_ROWS = 2336
EXPECTED_VALID_ROWS = 1824
EXPECTED_POST_ROWS = 152
EXPECTED_POST_DATES = 19

EXPECTED_VALID_SPEARMAN = 0.09524578882414786
EXPECTED_VALID_AUC = 0.6424580126237585

REPRO_TOL = 1e-10


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def load_json(path: Path) -> dict:

    if not path.exists():
        raise RuntimeError(
            f"Missing required file: {path.name}"
        )

    if path.stat().st_size == 0:
        raise RuntimeError(
            f"Required file is empty: {path.name}"
        )

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def adverse_from_mae(series: pd.Series) -> np.ndarray:

    x = pd.to_numeric(
        series,
        errors="raise",
    ).to_numpy(dtype=float)

    if not np.isfinite(x).all():
        raise RuntimeError(
            "Non-finite mae_1d encountered."
        )

    return np.maximum(
        0.0,
        -x,
    )


def prepare_xy(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
):

    prepared = r.prepare_numeric_matrix(
        train,
        test,
        features,
    )

    if not isinstance(prepared, tuple):
        raise RuntimeError(
            "prepare_numeric_matrix did not return tuple."
        )

    if len(prepared) < 2:
        raise RuntimeError(
            "prepare_numeric_matrix returned fewer than two objects."
        )

    x_train = np.asarray(
        prepared[0],
        dtype=float,
    )

    x_test = np.asarray(
        prepared[1],
        dtype=float,
    )

    if x_train.ndim != 2 or x_test.ndim != 2:
        raise RuntimeError(
            "Prepared matrices are not 2-dimensional."
        )

    if x_train.shape[1] != x_test.shape[1]:
        raise RuntimeError(
            "TRAIN/test prepared feature width mismatch."
        )

    if not np.isfinite(x_train).all():
        raise RuntimeError(
            "Non-finite values in prepared TRAIN matrix."
        )

    if not np.isfinite(x_test).all():
        raise RuntimeError(
            "Non-finite values in prepared test matrix."
        )

    return x_train, x_test


def fit_predict_exact(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
):

    x_train, x_test = prepare_xy(
        train,
        test,
        features,
    )

    y_train = adverse_from_mae(
        train["mae_1d"]
    )

    model = Ridge(
        alpha=ALPHA,
    )

    model.fit(
        x_train,
        y_train,
    )

    pred = np.asarray(
        model.predict(x_test),
        dtype=float,
    )

    if not np.isfinite(pred).all():
        raise RuntimeError(
            "Non-finite predictions produced."
        )

    return pred


def percentile_ci(values):

    a = np.asarray(
        values,
        dtype=float,
    )

    a = a[
        np.isfinite(a)
    ]

    if len(a) == 0:
        return {
            "ci_low": None,
            "ci_high": None,
            "valid_iterations": 0,
        }

    return {
        "ci_low": float(
            np.percentile(a, 2.5)
        ),
        "ci_high": float(
            np.percentile(a, 97.5)
        ),
        "valid_iterations": int(len(a)),
    }


def add_daily_top2(frame: pd.DataFrame) -> pd.DataFrame:

    pieces = []

    for d, g in frame.groupby(
        "date",
        sort=True,
    ):

        g = g.sort_values(
            ["predicted_risk", "symbol"],
            ascending=[False, True],
            kind="mergesort",
        ).copy()

        g["daily_risk_rank"] = np.arange(
            1,
            len(g) + 1,
        )

        g["top_risk_quartile"] = (
            g["daily_risk_rank"] <= 2
        )

        if len(g) != 8:
            raise RuntimeError(
                f"{d}: expected 8 symbols, found {len(g)}"
            )

        pieces.append(g)

    return pd.concat(
        pieces,
        ignore_index=True,
    )


def primary_effect(frame: pd.DataFrame) -> float:

    top = frame.loc[
        frame["top_risk_quartile"],
        "adverse_1d",
    ]

    rest = frame.loc[
        ~frame["top_risk_quartile"],
        "adverse_1d",
    ]

    if len(top) == 0 or len(rest) == 0:
        return float("nan")

    return float(
        top.mean() - rest.mean()
    )


def event_auc(frame: pd.DataFrame) -> float:

    y = frame[
        "event_2pct"
    ].astype(int).to_numpy()

    p = frame[
        "predicted_risk"
    ].to_numpy(dtype=float)

    if len(np.unique(y)) < 2:
        return float("nan")

    return float(
        roc_auc_score(
            y,
            p,
        )
    )


def event_ap(frame: pd.DataFrame) -> float:

    y = frame[
        "event_2pct"
    ].astype(int).to_numpy()

    p = frame[
        "predicted_risk"
    ].to_numpy(dtype=float)

    if int(y.sum()) == 0:
        return float("nan")

    return float(
        average_precision_score(
            y,
            p,
        )
    )


def block_bootstrap(
    frame: pd.DataFrame,
    iterations: int,
    seed: int,
):

    dates = np.array(
        sorted(
            frame["date"].unique()
        ),
        dtype="datetime64[ns]",
    )

    if len(dates) < 2:
        raise RuntimeError(
            "Need at least two dates for bootstrap."
        )

    groups = {
        pd.Timestamp(d):
        frame[
            frame["date"] == pd.Timestamp(d)
        ].copy()
        for d in dates
    }

    rng = np.random.default_rng(seed)

    primary_values = []
    auc_values = []
    ap_values = []

    n_dates = len(dates)

    for _ in range(iterations):

        sampled = rng.choice(
            dates,
            size=n_dates,
            replace=True,
        )

        blocks = []

        for occurrence, d in enumerate(sampled):

            block = groups[
                pd.Timestamp(d)
            ].copy()

            # Distinguish duplicate bootstrap draws
            # without changing any metric.
            block["_bootstrap_occurrence"] = occurrence

            blocks.append(block)

        sample = pd.concat(
            blocks,
            ignore_index=True,
        )

        primary_values.append(
            primary_effect(sample)
        )

        auc_values.append(
            event_auc(sample)
        )

        ap_values.append(
            event_ap(sample)
        )

    return {
        "primary": percentile_ci(
            primary_values
        ),
        "auc": percentile_ci(
            auc_values
        ),
        "average_precision": percentile_ci(
            ap_values
        ),
    }


# ---------------------------------------------------------------------
# START
# ---------------------------------------------------------------------

print("=" * 110)
print("TAIL-RISK FILTER V1 - ONE-TIME PREREGISTERED EXAM")
print("=" * 110)

# ---------------------------------------------------------------------
# 1. ONE-TIME STATE CHECK
# ---------------------------------------------------------------------

for p in [
    GUARD_FILE,
    OUT_JSON,
    OUT_MD,
    OUT_CSV,
    OUT_SHA,
]:
    if p.exists():
        raise RuntimeError(
            f"ONE-TIME GUARD: {p.name} already exists. "
            "Refusing a second execution."
        )

print()
print("1. One-time guard state: ARMED")
print("   No prior result/guard files exist.")

# ---------------------------------------------------------------------
# 2. PROVENANCE
# ---------------------------------------------------------------------

for p in [
    FROZEN_DATA,
    POST_DATA,
    FREEZE_FILE,
    CLOSURE_FILE,
    PREREG_FILE,
]:
    if not p.exists():
        raise RuntimeError(
            f"Missing required source: {p.name}"
        )

dataset_sha = sha256_file(
    FROZEN_DATA
)

freeze_sha = sha256_file(
    FREEZE_FILE
)

closure_sha = sha256_file(
    CLOSURE_FILE
)

prereg_sha = sha256_file(
    PREREG_FILE
)

if dataset_sha != EXPECTED_DATASET_SHA:
    raise RuntimeError(
        "Frozen dataset SHA mismatch."
    )

if freeze_sha != EXPECTED_FREEZE_SHA:
    raise RuntimeError(
        "Frozen candidate SHA mismatch."
    )

if closure_sha != EXPECTED_CLOSURE_SHA:
    raise RuntimeError(
        "Risk Head closure SHA mismatch."
    )

if prereg_sha != EXPECTED_PREREG_SHA:
    raise RuntimeError(
        "Tail-Risk preregistration SHA mismatch."
    )

freeze = load_json(
    FREEZE_FILE
)

prereg = load_json(
    PREREG_FILE
)

if prereg.get("status") != (
    "PREREGISTERED_BEFORE_OUTCOME_INSPECTION"
):
    raise RuntimeError(
        "Unexpected preregistration status."
    )

features = list(
    freeze["candidate"]["feature_list"]
)

if len(features) != 25:
    raise RuntimeError(
        "Frozen B feature count is not 25."
    )

if list(
    r.FEATURE_SETS[
        "B_INTRADAY_DAILY_SUMMARY"
    ]
) != features:
    raise RuntimeError(
        "Runtime B feature list differs from freeze."
    )

print()
print("2. Provenance: PASS")
print("   Frozen dataset SHA :", dataset_sha)
print("   Frozen candidate SHA:", freeze_sha)
print("   Risk closure SHA    :", closure_sha)
print("   Preregistration SHA :", prereg_sha)

# ---------------------------------------------------------------------
# 3. LOAD ORIGINAL FROZEN MODELING DATA
#
# This is safe: these are already-used TRAIN/VALIDATION observations.
# ---------------------------------------------------------------------

frozen = pd.read_csv(
    FROZEN_DATA
)

if "date" not in frozen.columns:
    raise RuntimeError(
        "Frozen modeling data lacks date."
    )

frozen["date"] = pd.to_datetime(
    frozen["date"],
    errors="raise",
)

train = frozen[
    (frozen["date"] >= TRAIN_START)
    & (frozen["date"] <= TRAIN_END)
].copy()

valid = frozen[
    (frozen["date"] >= VALID_START)
    & (frozen["date"] <= VALID_END)
].copy()

if len(train) != EXPECTED_TRAIN_ROWS:
    raise RuntimeError(
        f"TRAIN row mismatch: {len(train)} "
        f"!= {EXPECTED_TRAIN_ROWS}"
    )

if len(valid) != EXPECTED_VALID_ROWS:
    raise RuntimeError(
        f"VALIDATION row mismatch: {len(valid)} "
        f"!= {EXPECTED_VALID_ROWS}"
    )

# ---------------------------------------------------------------------
# 4. REPRODUCE FROZEN 2025 RESULT
#
# CRITICAL:
# The new 152 outcomes have NOT been loaded at this point.
# ---------------------------------------------------------------------

valid_pred = fit_predict_exact(
    train,
    valid,
    features,
)

valid_y = adverse_from_mae(
    valid["mae_1d"]
)

valid_event = (
    valid_y >= EVENT_THRESHOLD
).astype(int)

valid_spearman = float(
    r.rank_corr(
        valid_y,
        valid_pred,
    )
)

valid_auc = float(
    roc_auc_score(
        valid_event,
        valid_pred,
    )
)

print()
print("3. Frozen 2025 reproduction check")
print(
    "   Risk Spearman:",
    f"{valid_spearman:+.12f}",
)

print(
    "   Expected     :",
    f"{EXPECTED_VALID_SPEARMAN:+.12f}",
)

print(
    "   ROC-AUC      :",
    f"{valid_auc:.12f}",
)

print(
    "   Expected     :",
    f"{EXPECTED_VALID_AUC:.12f}",
)

if abs(
    valid_spearman
    - EXPECTED_VALID_SPEARMAN
) > REPRO_TOL:
    raise RuntimeError(
        "Frozen validation Spearman reproduction FAILED. "
        "POST-HOLDOUT OUTCOMES REMAIN UNREAD."
    )

if abs(
    valid_auc
    - EXPECTED_VALID_AUC
) > REPRO_TOL:
    raise RuntimeError(
        "Frozen validation ROC-AUC reproduction FAILED. "
        "POST-HOLDOUT OUTCOMES REMAIN UNREAD."
    )

print("   REPRODUCTION: PASS")

# ---------------------------------------------------------------------
# 5. LOAD ONLY POST-HOLDOUT FEATURES
#
# No mae_1d / future outcome is read yet.
# ---------------------------------------------------------------------

feature_columns = (
    ["symbol", "date"]
    + features
)

if "state_5m_bar_count" in pd.read_csv(
    POST_DATA,
    nrows=0,
).columns:
    feature_columns.append(
        "state_5m_bar_count"
    )

post_features = pd.read_csv(
    POST_DATA,
    usecols=feature_columns,
)

post_features["date"] = pd.to_datetime(
    post_features["date"],
    errors="raise",
)

post_features = post_features[
    (post_features["date"] >= POST_START)
    & (post_features["date"] <= POST_END)
].copy()

post_features = post_features.sort_values(
    ["date", "symbol"],
).reset_index(
    drop=True
)

if len(post_features) != EXPECTED_POST_ROWS:
    raise RuntimeError(
        f"Post feature rows mismatch: "
        f"{len(post_features)} != {EXPECTED_POST_ROWS}"
    )

if post_features["date"].nunique() != EXPECTED_POST_DATES:
    raise RuntimeError(
        "Post feature date-count mismatch."
    )

per_date_n = (
    post_features.groupby(
        "date"
    )["symbol"]
    .nunique()
)

if not (
    per_date_n == 8
).all():
    raise RuntimeError(
        "Not every evaluation date contains exactly 8 symbols."
    )

if post_features[
    ["date", "symbol"]
].duplicated().any():
    raise RuntimeError(
        "Duplicate symbol/date rows in evaluation features."
    )

# Predict BEFORE touching outcomes.

post_pred = fit_predict_exact(
    train,
    post_features,
    features,
)

post_features[
    "predicted_risk"
] = post_pred

print()
print("4. New unseen feature population: PASS")
print("   Dates      :", post_features["date"].nunique())
print("   Symbol-days:", len(post_features))
print("   Outcomes   : STILL UNREAD")

# ---------------------------------------------------------------------
# 6. ATOMIC ONE-TIME GUARD
#
# From this point onward, the 152 new outcomes may be opened exactly once.
# ---------------------------------------------------------------------

guard_payload = {
    "schema":
        "TAIL_RISK_FILTER_V1_ONE_TIME_EXAM_GUARD",

    "opened_at_utc":
        datetime.now(
            timezone.utc
        ).isoformat(),

    "pid":
        os.getpid(),

    "preregistration_sha256":
        prereg_sha,

    "frozen_candidate_sha256":
        freeze_sha,

    "status":
        "POST_HOLDOUT_OUTCOMES_OPENED_ONCE",

    "second_execution_prohibited":
        True,
}

with GUARD_FILE.open(
    "x",
    encoding="utf-8",
) as f:
    json.dump(
        guard_payload,
        f,
        indent=2,
        sort_keys=True,
    )
    f.write("\n")

print()
print("=" * 110)
print("ONE-TIME GUARD COMMITTED")
print("POST-HOLDOUT OUTCOMES WILL NOW BE READ")
print("SECOND EXECUTION IS PROHIBITED")
print("=" * 110)

# ---------------------------------------------------------------------
# 7. READ EXACTLY THE NEW OUTCOME COLUMN
# ---------------------------------------------------------------------

outcomes = pd.read_csv(
    POST_DATA,
    usecols=[
        "symbol",
        "date",
        "mae_1d",
    ],
)

outcomes["date"] = pd.to_datetime(
    outcomes["date"],
    errors="raise",
)

outcomes = outcomes[
    (outcomes["date"] >= POST_START)
    & (outcomes["date"] <= POST_END)
].copy()

outcomes = outcomes.sort_values(
    ["date", "symbol"],
).reset_index(
    drop=True
)

if len(outcomes) != EXPECTED_POST_ROWS:
    raise RuntimeError(
        "Post outcome row count mismatch."
    )

if outcomes["mae_1d"].isna().any():
    raise RuntimeError(
        "Missing mae_1d inside locked evaluation window."
    )

exam = post_features.merge(
    outcomes,
    on=[
        "date",
        "symbol",
    ],
    how="inner",
    validate="one_to_one",
)

if len(exam) != EXPECTED_POST_ROWS:
    raise RuntimeError(
        "Feature/outcome merge lost rows."
    )

exam["adverse_1d"] = adverse_from_mae(
    exam["mae_1d"]
)

exam["event_2pct"] = (
    exam["adverse_1d"]
    >= EVENT_THRESHOLD
)

exam = add_daily_top2(
    exam
)

exam["session_regime"] = np.where(
    exam["date"] >= CAS_START,
    "CAS_72BAR",
    "STANDARD_75BAR",
)

# ---------------------------------------------------------------------
# 8. PREREGISTERED PRIMARY + SECONDARY
# ---------------------------------------------------------------------

primary_estimate = primary_effect(
    exam
)

auc_estimate = event_auc(
    exam
)

ap_estimate = event_ap(
    exam
)

event_rate = float(
    exam["event_2pct"].mean()
)

boot = block_bootstrap(
    exam,
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
)

primary_ci = boot["primary"]
auc_ci = boot["auc"]
ap_ci = boot["average_precision"]

if primary_ci["valid_iterations"] != BOOTSTRAP_ITERATIONS:
    raise RuntimeError(
        "Primary bootstrap did not produce 10,000 valid iterations."
    )

if auc_ci["valid_iterations"] < 9500:
    raise RuntimeError(
        "Too few valid ROC-AUC bootstrap iterations."
    )

primary_pass = bool(
    primary_estimate > 0
    and primary_ci["ci_low"] is not None
    and primary_ci["ci_low"] > 0
)

secondary_pass = bool(
    auc_estimate > 0.50
    and auc_ci["ci_low"] is not None
    and auc_ci["ci_low"] > 0.50
)

if primary_pass and secondary_pass:
    classification = "FULL_PASS"

elif primary_pass or secondary_pass:
    classification = "PARTIAL_PASS"

else:
    classification = "NO_GO"

# ---------------------------------------------------------------------
# 9. SESSION-REGIME SENSITIVITY
# ---------------------------------------------------------------------

cas = exam[
    exam["session_regime"]
    == "CAS_72BAR"
].copy()

standard = exam[
    exam["session_regime"]
    == "STANDARD_75BAR"
].copy()

cas_primary = primary_effect(
    cas
)

cas_auc = event_auc(
    cas
)

cas_ap = event_ap(
    cas
)

cas_boot = block_bootstrap(
    cas,
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED + 1,
)

# Standard = 4 dates only.
# Point estimates only, as preregistered.

standard_primary = primary_effect(
    standard
)

standard_auc = event_auc(
    standard
)

standard_ap = event_ap(
    standard
)

# ---------------------------------------------------------------------
# 10. SYMBOL DIAGNOSTICS - DESCRIPTIVE ONLY
# ---------------------------------------------------------------------

symbol_diag = {}

for symbol, g in exam.groupby(
    "symbol",
    sort=True,
):

    symbol_diag[
        symbol
    ] = {
        "rows":
            int(len(g)),

        "event_count":
            int(
                g["event_2pct"].sum()
            ),

        "event_rate":
            float(
                g["event_2pct"].mean()
            ),

        "mean_adverse_1d":
            float(
                g["adverse_1d"].mean()
            ),

        "mean_predicted_risk":
            float(
                g["predicted_risk"].mean()
            ),

        "top_quartile_count":
            int(
                g[
                    "top_risk_quartile"
                ].sum()
            ),
    }

# ---------------------------------------------------------------------
# 11. SAVE PREDICTIONS
# ---------------------------------------------------------------------

prediction_columns = [
    "date",
    "symbol",
    "session_regime",
    "predicted_risk",
    "daily_risk_rank",
    "top_risk_quartile",
    "adverse_1d",
    "event_2pct",
]

exam[
    prediction_columns
].to_csv(
    OUT_CSV,
    index=False,
)

# ---------------------------------------------------------------------
# 12. RESULT JSON
# ---------------------------------------------------------------------

result = {
    "schema":
        "TAIL_RISK_FILTER_V1_ONE_TIME_PREREGISTERED_RESULT",

    "completed_at_utc":
        datetime.now(
            timezone.utc
        ).isoformat(),

    "status":
        "ONE_TIME_EXAM_COMPLETE",

    "second_execution_prohibited":
        True,

    "production_authorized":
        False,

    "classification":
        classification,

    "provenance": {
        "frozen_dataset_sha256":
            dataset_sha,

        "risk_head_freeze_sha256":
            freeze_sha,

        "risk_head_closure_sha256":
            closure_sha,

        "tail_risk_preregistration_sha256":
            prereg_sha,

        "one_time_guard_file":
            GUARD_FILE.name,

        "bootstrap_iterations":
            BOOTSTRAP_ITERATIONS,

        "bootstrap_seed":
            BOOTSTRAP_SEED,
    },

    "frozen_model": {
        "feature_set":
            "B_INTRADAY_DAILY_SUMMARY",

        "feature_count":
            len(features),

        "features":
            features,

        "model_family":
            "Ridge",

        "alpha":
            ALPHA,

        "fit_policy":
            "ORIGINAL TRAIN ONLY",

        "target":
            "adverse_1d=max(0,-mae_1d)",

        "event_threshold":
            EVENT_THRESHOLD,
    },

    "validation_reproduction": {
        "rows":
            int(len(valid)),

        "risk_spearman":
            valid_spearman,

        "expected_risk_spearman":
            EXPECTED_VALID_SPEARMAN,

        "roc_auc":
            valid_auc,

        "expected_roc_auc":
            EXPECTED_VALID_AUC,

        "status":
            "PASS",
    },

    "evaluation_population": {
        "start":
            POST_START.date().isoformat(),

        "end":
            POST_END.date().isoformat(),

        "dates":
            int(
                exam["date"].nunique()
            ),

        "rows":
            int(len(exam)),

        "symbols":
            int(
                exam["symbol"].nunique()
            ),

        "standard_75bar_rows":
            int(len(standard)),

        "cas_72bar_rows":
            int(len(cas)),
    },

    "primary_top_quartile_excess_adverse": {
        "estimate":
            primary_estimate,

        "ci_low":
            primary_ci["ci_low"],

        "ci_high":
            primary_ci["ci_high"],

        "bootstrap_valid_iterations":
            primary_ci[
                "valid_iterations"
            ],

        "pass":
            primary_pass,
    },

    "secondary_event_2pct_roc_auc": {
        "estimate":
            auc_estimate,

        "ci_low":
            auc_ci["ci_low"],

        "ci_high":
            auc_ci["ci_high"],

        "bootstrap_valid_iterations":
            auc_ci[
                "valid_iterations"
            ],

        "pass":
            secondary_pass,
    },

    "average_precision": {
        "estimate":
            ap_estimate,

        "ci_low":
            ap_ci["ci_low"],

        "ci_high":
            ap_ci["ci_high"],

        "event_rate":
            event_rate,

        "lift_vs_event_rate":
            (
                ap_estimate / event_rate
                if event_rate > 0
                else None
            ),
    },

    "cas_72bar_sensitivity": {
        "dates":
            int(
                cas["date"].nunique()
            ),

        "rows":
            int(len(cas)),

        "primary_estimate":
            cas_primary,

        "primary_ci_low":
            cas_boot[
                "primary"
            ]["ci_low"],

        "primary_ci_high":
            cas_boot[
                "primary"
            ]["ci_high"],

        "roc_auc":
            cas_auc,

        "roc_auc_ci_low":
            cas_boot[
                "auc"
            ]["ci_low"],

        "roc_auc_ci_high":
            cas_boot[
                "auc"
            ]["ci_high"],

        "average_precision":
            cas_ap,

        "event_rate":
            float(
                cas[
                    "event_2pct"
                ].mean()
            ),
    },

    "standard_75bar_descriptive_only": {
        "dates":
            int(
                standard[
                    "date"
                ].nunique()
            ),

        "rows":
            int(len(standard)),

        "primary_estimate":
            standard_primary,

        "roc_auc":
            standard_auc,

        "average_precision":
            standard_ap,

        "event_rate":
            float(
                standard[
                    "event_2pct"
                ].mean()
            ),
    },

    "symbol_diagnostics_descriptive_only":
        symbol_diag,

    "interpretation_guardrails": [
        "Primary endpoint was fixed before outcome inspection.",
        "Secondary 2% ROC-AUC was fixed before outcome inspection.",
        "Top-risk bucket is exactly top 2 of 8 per date.",
        "No alternative threshold or bucket may be selected after this result.",
        "No symbols or dates may be excluded based on this result.",
        "This 19-date evaluation is a pilot validation, not production authorization.",
        "No live exposure control is authorized.",
        "P01D remains sovereign and unchanged.",
    ],
}

OUT_JSON.write_text(
    json.dumps(
        result,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)

# ---------------------------------------------------------------------
# 13. HUMAN-READABLE RECORD
# ---------------------------------------------------------------------

def fmt_ci(low, high, scale=1.0, decimals=6):

    if low is None or high is None:
        return "NA"

    return (
        f"[{low * scale:+.{decimals}f}, "
        f"{high * scale:+.{decimals}f}]"
    )


md = f"""# Tail-Risk Filter V1 — One-Time Preregistered Result
## 25 August 2026

**CLASSIFICATION: {classification}**

**PRODUCTION AUTHORIZED: NO**

### Evaluation population

- Dates: {exam["date"].nunique()}
- Symbol-days: {len(exam)}
- Symbols: {exam["symbol"].nunique()}
- Standard 75-bar rows: {len(standard)}
- CAS 72-bar rows: {len(cas)}

### Frozen-model reproduction

2025 validation Risk Spearman:

`{valid_spearman:+.12f}`

Frozen expected:

`{EXPECTED_VALID_SPEARMAN:+.12f}`

2025 validation ROC-AUC:

`{valid_auc:.12f}`

Frozen expected:

`{EXPECTED_VALID_AUC:.12f}`

**Reproduction: PASS**

### Primary — top-risk quartile excess adverse excursion

Estimate:

`{primary_estimate * 10000:+.2f} bp`

95% whole-date bootstrap CI:

`{fmt_ci(primary_ci["ci_low"], primary_ci["ci_high"], 10000, 2)} bp`

PASS:

`{primary_pass}`

### Secondary — 2% adverse-event ROC-AUC

ROC-AUC:

`{auc_estimate:.6f}`

95% whole-date bootstrap CI:

`{fmt_ci(auc_ci["ci_low"], auc_ci["ci_high"], 1.0, 6)}`

PASS:

`{secondary_pass}`

### Average precision

Average precision:

`{ap_estimate:.6f}`

Observed event rate:

`{event_rate:.6f}`

Lift versus event-rate baseline:

`{(ap_estimate / event_rate if event_rate > 0 else float("nan")):.3f}x`

### CAS 72-bar sensitivity

Dates:

`{cas["date"].nunique()}`

Primary excess adverse:

`{cas_primary * 10000:+.2f} bp`

95% CI:

`{fmt_ci(cas_boot["primary"]["ci_low"], cas_boot["primary"]["ci_high"], 10000, 2)} bp`

ROC-AUC:

`{cas_auc:.6f}`

95% CI:

`{fmt_ci(cas_boot["auc"]["ci_low"], cas_boot["auc"]["ci_high"], 1.0, 6)}`

### Standard 75-bar subset

Only four dates are available.

These values are descriptive only.

Primary excess adverse:

`{standard_primary * 10000:+.2f} bp`

ROC-AUC:

`{standard_auc:.6f}`

### Decision rule

- FULL PASS = primary and secondary both pass.
- PARTIAL PASS = exactly one passes.
- NO-GO = neither passes.

Whatever the classification, this 19-date pilot does **not**
authorize live exposure control.

No post-result feature changes, threshold search, bucket search,
sign inversion, symbol exclusions, or date exclusions are permitted.

P01D remains unchanged and sovereign.
"""

OUT_MD.write_text(
    md,
    encoding="utf-8",
)

# ---------------------------------------------------------------------
# 14. HASH EVERYTHING
# ---------------------------------------------------------------------

json_sha = sha256_file(
    OUT_JSON
)

md_sha = sha256_file(
    OUT_MD
)

csv_sha = sha256_file(
    OUT_CSV
)

guard_sha = sha256_file(
    GUARD_FILE
)

OUT_SHA.write_text(
    f"{guard_sha}  {GUARD_FILE.name}\n"
    f"{json_sha}  {OUT_JSON.name}\n"
    f"{md_sha}  {OUT_MD.name}\n"
    f"{csv_sha}  {OUT_CSV.name}\n",
    encoding="utf-8",
)

# ---------------------------------------------------------------------
# 15. FINAL REPORT
# ---------------------------------------------------------------------

print()
print("=" * 110)
print("TAIL-RISK FILTER V1 - FINAL RESULT")
print("=" * 110)

print()
print("PRIMARY: TOP-RISK QUARTILE EXCESS ADVERSE")
print(
    f"  Estimate : {primary_estimate * 10000:+.2f} bp"
)
print(
    "  95% CI  : "
    f"[{primary_ci['ci_low'] * 10000:+.2f}, "
    f"{primary_ci['ci_high'] * 10000:+.2f}] bp"
)
print(
    "  PASS     :",
    primary_pass,
)

print()
print("SECONDARY: 2% EVENT ROC-AUC")
print(
    f"  Estimate : {auc_estimate:.6f}"
)
print(
    "  95% CI  : "
    f"[{auc_ci['ci_low']:.6f}, "
    f"{auc_ci['ci_high']:.6f}]"
)
print(
    "  PASS     :",
    secondary_pass,
)

print()
print("AVERAGE PRECISION")
print(
    f"  AP       : {ap_estimate:.6f}"
)
print(
    f"  Event rate: {event_rate:.6f}"
)
print(
    f"  Lift     : "
    f"{ap_estimate / event_rate:.3f}x"
)

print()
print("CAS 72-BAR SENSITIVITY")
print(
    f"  Primary  : {cas_primary * 10000:+.2f} bp"
)
print(
    "  Primary CI: "
    f"[{cas_boot['primary']['ci_low'] * 10000:+.2f}, "
    f"{cas_boot['primary']['ci_high'] * 10000:+.2f}] bp"
)
print(
    f"  ROC-AUC  : {cas_auc:.6f}"
)
print(
    "  AUC CI   : "
    f"[{cas_boot['auc']['ci_low']:.6f}, "
    f"{cas_boot['auc']['ci_high']:.6f}]"
)

print()
print("=" * 110)
print("CLASSIFICATION:", classification)
print("PRODUCTION AUTHORIZED: False")
print("SECOND EXECUTION PROHIBITED: True")
print("=" * 110)

print()
print("RESULT JSON :", OUT_JSON.name)
print("RESULT MD   :", OUT_MD.name)
print("PREDICTIONS :", OUT_CSV.name)
print("HASH RECORD :", OUT_SHA.name)
print("GUARD       :", GUARD_FILE.name)

print()
print("Result JSON SHA256:", json_sha)
print("Predictions SHA256:", csv_sha)

print()
print("=" * 110)
print("ONE-TIME TAIL-RISK FILTER V1 EXAM COMPLETE")
print("=" * 110)
