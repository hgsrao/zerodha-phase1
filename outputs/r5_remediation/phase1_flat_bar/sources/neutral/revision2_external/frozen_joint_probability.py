"""Pre-registered joint-feature probability research model.

This is a discovery instrument, not an entry model.  It fits once on a
chronological train period, freezes its transformations and coefficients, and
reports target-first rates in score deciles on later periods.  It does not
choose a live threshold, place orders, or modify any controller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .frozen_decile_analysis import FEATURES

SIDE_FEATURE = "side"
TIME_FEATURE = "session_bucket"
SESSION_BUCKETS = (
    "open_0915_1015", "morning_1015_1130", "midday_1130_1300",
    "afternoon_1300_1415", "close_1415_1530",
)
MODEL_FEATURES = (*FEATURES, SIDE_FEATURE, TIME_FEATURE)
ELIGIBLE_LABELS = ("TARGET_FIRST", "STOP_FIRST")
DECILE_QUANTILES = np.linspace(0.0, 1.0, 11)


def session_bucket(value: object) -> str:
    time = str(value)
    if time < "10:15":
        return "open_0915_1015"
    if time < "11:30":
        return "morning_1015_1130"
    if time < "13:00":
        return "midday_1130_1300"
    if time < "14:15":
        return "afternoon_1300_1415"
    return "close_1415_1530"


def _design_rows(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"label", "side", "time_of_day", *FEATURES}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"joint probability input missing columns: {sorted(missing)}")
    out = frame.loc[frame["label"].isin(ELIGIBLE_LABELS), [*FEATURES, "side", "time_of_day", "label"]].copy()
    out[SIDE_FEATURE] = out.pop("side").map({"BUY": 1.0, "SELL": -1.0})
    if out[SIDE_FEATURE].isna().any():
        raise ValueError("side must be BUY or SELL")
    out[TIME_FEATURE] = out.pop("time_of_day").map(session_bucket)
    return out


def build_pipeline() -> Pipeline:
    """Fixed, deliberately modest model family; no hyperparameter search."""
    numeric = [*FEATURES, SIDE_FEATURE]
    prep = ColumnTransformer([
        ("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]), numeric),
        ("session", OneHotEncoder(categories=[list(SESSION_BUCKETS)], handle_unknown="ignore"), [TIME_FEATURE]),
    ])
    return Pipeline([
        ("prepare", prep),
        # L2 regularisation is fixed before July is read.  The model is
        # intentionally small and interpretable; it is not an optimizer.
        ("logit", LogisticRegression(C=0.1, solver="lbfgs", max_iter=200, random_state=20250912)),
    ])


def _cohorts(frame: pd.DataFrame, score: np.ndarray, edges: np.ndarray, split: str) -> list[Dict[str, Any]]:
    bucket = np.searchsorted(edges[1:-1], score, side="right") + 1
    working = frame[["label"]].copy()
    working["bucket"] = bucket
    rows: list[Dict[str, Any]] = []
    for number, part in working.groupby("bucket", sort=True):
        targets = int((part.label == "TARGET_FIRST").sum())
        stops = int((part.label == "STOP_FIRST").sum())
        rows.append({
            "split": split, "bucket": int(number), "rows": int(len(part)),
            "target_first": targets, "stop_first": stops,
            "target_rate": targets / (targets + stops) if targets + stops else None,
            "mean_predicted_probability": float(score[part.index.to_numpy() - working.index.min()].mean())
            if False else None,
        })
    # Score means need positional, rather than source-index, alignment.
    for row in rows:
        scores = score[bucket == row["bucket"]]
        row["mean_predicted_probability"] = float(scores.mean()) if len(scores) else None
    return rows


@dataclass(frozen=True)
class FrozenJointModel:
    model: Pipeline
    train_score_decile_edges: np.ndarray
    train_design: pd.DataFrame
    train_score: np.ndarray


def predict_frozen(frozen: FrozenJointModel, frame: pd.DataFrame) -> pd.Series:
    """Return frozen target-first probabilities indexed to eligible source rows."""
    design = _design_rows(frame)
    score = frozen.model.predict_proba(design.loc[:, MODEL_FEATURES])[:, 1]
    return pd.Series(score, index=design.index, name="frozen_probability")


def fit_train_only(train: pd.DataFrame) -> FrozenJointModel:
    """Fit once on the chronological train period and freeze the result."""
    fitted_train = _design_rows(train)
    if len(fitted_train) == 0:
        raise ValueError("no eligible train rows")
    x_train = fitted_train.loc[:, MODEL_FEATURES]
    y_train = (fitted_train.label == "TARGET_FIRST").astype(int)
    model = build_pipeline()
    model.fit(x_train, y_train)
    train_score = model.predict_proba(x_train)[:, 1]
    edges = np.quantile(train_score, DECILE_QUANTILES)
    return FrozenJointModel(model, edges, fitted_train, train_score)


def score_frozen(frozen: FrozenJointModel, later: Iterable[tuple[str, pd.DataFrame]]) -> Dict[str, Any]:
    """Score later periods without fitting or transforming model state."""
    edges = frozen.train_score_decile_edges
    report: Dict[str, Any] = {
        "method": "Frozen L2 logistic regression; fit January-March only; fixed session buckets; train score deciles applied unchanged later.",
        "contract": {
            "numeric_features": list(FEATURES), "side_encoding": {"BUY": 1.0, "SELL": -1.0},
            "session_buckets": list(SESSION_BUCKETS), "regularization_c": 0.1,
            "eligible_labels": list(ELIGIBLE_LABELS), "model_is_entry_rule": False,
        },
        "train_score_decile_edges": [float(edge) for edge in edges],
        "cohorts": _cohorts(frozen.train_design, frozen.train_score, edges, "train"),
        "model": frozen.model,
    }
    for split, frame in later:
        design = _design_rows(frame)
        score = frozen.model.predict_proba(design.loc[:, MODEL_FEATURES])[:, 1]
        report["cohorts"].extend(_cohorts(design, score, edges, split))
    return report


def fit_and_score(train: pd.DataFrame, later: Iterable[tuple[str, pd.DataFrame]]) -> Dict[str, Any]:
    """Compatibility helper: fit only ``train``, then score later frames."""
    return score_frozen(fit_train_only(train), later)
