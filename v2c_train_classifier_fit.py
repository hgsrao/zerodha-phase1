"""V2-C TRAIN classifier fitting. LOCAL FILES ONLY - no Kite calls, no
credentials, no network.

Fits the single frozen classifier
(`P01D_V2C_TRAIN_CLASSIFIER_PREREGISTRATION_20260818.md`, classifier
spec SHA256 `2C0E9BEE4AA89B7DB39DF3C37FB9CB616B81DA27D4D8434EF30A4CEE920A0327`)
on the resolved TRAIN population only, using **only the explicit frozen
nine-column feature list** from `V2C_TRAIN_FEATURES_20260818.csv` -
never the diagnostic columns appended after it. Authorized per the
independent feature-integrity re-audit's PASS/FROZEN verdict
(2026-08-18).

Self-contained: does NOT import anything from
`V2C_REAL_DATA_SANDBOX_20260817/` (that tree was flagged earlier this
project as containing a premature, out-of-sequence resolver artifact;
this script re-implements the classifier mechanics directly against
the frozen spec rather than reopening any question about sandbox
provenance).

  - StandardScaler mean/SD fit on TRAIN's resolved population only.
  - LogisticRegression: penalty=l2, C=1.0, solver=lbfgs,
    fit_intercept=True, class_weight=None, max_iter=1000.
  - UNRESOLVED events excluded from fitting, counted in the report.
  - Classification threshold = the ALREADY-FROZEN value 0.77278226
    (`P01D_V2C_TRAIN_CLASSIFIER_PREREGISTRATION_20260818.md`) - used
    verbatim, never recomputed-and-substituted, though this script does
    independently recompute the resolved TRAIN REVERSION base rate as
    an integrity cross-check and refuses to proceed if it disagrees.
  - No model tournament, no hyperparameter search.

**Does not open VALIDATION or HOLDOUT.** A TRAIN-only diagnostic AUC is
reported for descriptive purposes alone - it is NOT the predictive
gate (that gate is VALIDATION-only, per the preregistration, and
remains unevaluated).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

BASE = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816")
FEATURES_DIR = BASE / "V2C_FEATURES_TRAIN_20260818"
FEATURES_CSV = FEATURES_DIR / "V2C_TRAIN_FEATURES_20260818.csv"
FEATURES_MANIFEST = FEATURES_DIR / "V2C_TRAIN_FEATURES_HASH_MANIFEST_20260818.json"
FEATURES_MANIFEST_SHA = FEATURES_DIR / "V2C_TRAIN_FEATURES_HASH_MANIFEST_20260818.json.sha256"

LABELS_DIR = BASE / "V2C_LABELS_TRAIN_20260818"
AUDIT_CSV = LABELS_DIR / "V2C_TRAIN_LABEL_AUDIT_20260818.csv"
LABELS_MANIFEST = LABELS_DIR / "V2C_TRAIN_LABELS_HASH_MANIFEST_20260818.json"
LABELS_MANIFEST_SHA = LABELS_DIR / "V2C_TRAIN_LABELS_HASH_MANIFEST_20260818.sha256"

CLASSIFIER_PREREG_MD = BASE / "P01D_V2C_TRAIN_CLASSIFIER_PREREGISTRATION_20260818.md"
CLASSIFIER_PREREG_SHA = BASE / "P01D_V2C_TRAIN_CLASSIFIER_PREREGISTRATION_20260818.sha256"

OUT_DIR = BASE / "V2C_CLASSIFIER_TRAIN_20260818"
OUT_DIR.mkdir(exist_ok=True)
MODEL_JSON = OUT_DIR / "V2C_TRAIN_CLASSIFIER_FIT_20260818.json"
FIT_REPORT_JSON = OUT_DIR / "V2C_TRAIN_CLASSIFIER_FIT_REPORT_20260818.json"

FROZEN_FEATURE_ORDER = [
    "event_z20", "downside_return", "drawdown_from_day_high", "recovery_from_day_low",
    "close_location", "atr14_pct", "intraday_range_pct", "nifty_return", "relative_return",
]
CLASSIFIER_SPEC_SHA256 = "2C0E9BEE4AA89B7DB39DF3C37FB9CB616B81DA27D4D8434EF30A4CEE920A0327"
FROZEN_THRESHOLD = 0.77278226
_EPS_THRESHOLD = 5e-9  # tolerance for the independent base-rate cross-check


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_sidecar(target: Path, sidecar: Path, label: str) -> str:
    if not sidecar.exists():
        raise SystemExit(f"REFUSING TO RUN: no sidecar hash for {target.name} ({label}).")
    recorded = sidecar.read_text(encoding="utf-8").split()[0].strip().lower()
    actual = _sha256(target).lower()
    if recorded != actual:
        raise SystemExit(f"REFUSING TO RUN: {target.name} hash mismatch against {sidecar.name} "
                          f"({label}) - recorded {recorded}, actual {actual}.")
    print(f"{label} verified: {target.name} matches frozen SHA256 {actual}.")
    return actual


def verify_inputs() -> None:
    _verify_sidecar(CLASSIFIER_PREREG_MD, CLASSIFIER_PREREG_SHA, "Classifier preregistration")
    _verify_sidecar(LABELS_MANIFEST, LABELS_MANIFEST_SHA, "Label hash manifest")
    _verify_sidecar(FEATURES_MANIFEST, FEATURES_MANIFEST_SHA, "Feature hash manifest")
    # Every file the two manifests list must itself still match.
    for manifest_path, base_dir in ((LABELS_MANIFEST, Path(".")), (FEATURES_MANIFEST, Path("."))):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            candidate = base_dir / entry["file"].replace("\\", "/")
            if not candidate.exists():
                raise SystemExit(f"REFUSING TO RUN: manifest-listed file {candidate} does not exist.")
            h = _sha256(candidate).lower()
            if h != entry["sha256"].lower():
                raise SystemExit(f"REFUSING TO RUN: {candidate} hash mismatch against "
                                  f"{manifest_path.name}.")
    print("All files listed in the label and feature hash manifests verified intact.")


def load_joined_dataset() -> tuple[np.ndarray, np.ndarray, int, int]:
    with AUDIT_CSV.open(newline="", encoding="utf-8") as h:
        labels_by_key = {(r["security_key"], r["event_t0"]): r["label"] for r in csv.DictReader(h)}

    X_rows: list[list[float]] = []
    y_rows: list[int] = []
    n_unresolved = 0
    n_resolved = 0

    with FEATURES_CSV.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            key = (row["security_key"], row["event_t0"])
            if key not in labels_by_key:
                raise SystemExit(f"INTEGRITY VIOLATION: feature row {key} has no matching label - "
                                  f"join defect against the frozen label set.")
            if row["abstain"] == "True":
                raise SystemExit(f"INTEGRITY VIOLATION: feature row {key} is marked abstain - "
                                  f"the frozen feature-integrity re-audit reported zero abstentions; "
                                  f"this should never happen.")
            label = labels_by_key[key]
            if label == "UNRESOLVED":
                n_unresolved += 1
                continue
            if label not in ("REVERTING", "DETERIORATING"):
                raise SystemExit(f"INTEGRITY VIOLATION: unrecognized label '{label}' for {key}.")

            n_resolved += 1
            X_rows.append([float(row[f]) for f in FROZEN_FEATURE_ORDER])
            y_rows.append(1 if label == "REVERTING" else 0)

    if len(labels_by_key) != n_resolved + n_unresolved:
        raise SystemExit("INTEGRITY VIOLATION: feature/label row count does not partition cleanly "
                          "into resolved + unresolved.")

    return np.asarray(X_rows, dtype=float), np.asarray(y_rows, dtype=int), n_resolved, n_unresolved


def main() -> int:
    verify_inputs()

    X, y, n_resolved, n_unresolved = load_joined_dataset()
    print(f"\nJoined dataset: {n_resolved} resolved TRAIN events (fit population), "
          f"{n_unresolved} UNRESOLVED (excluded from fitting, retained in accounting).")

    if n_resolved != 4960:
        raise SystemExit(f"INTEGRITY VIOLATION: expected 4960 resolved TRAIN events "
                          f"(3833 REVERTING + 1127 DETERIORATING per the frozen label set), "
                          f"got {n_resolved}.")

    n_reverting = int(y.sum())
    n_deteriorating = int(len(y) - n_reverting)
    if (n_reverting, n_deteriorating) != (3833, 1127):
        raise SystemExit(f"INTEGRITY VIOLATION: expected 3833 REVERTING / 1127 DETERIORATING, "
                          f"got {n_reverting} / {n_deteriorating}.")

    # Independent cross-check of the frozen threshold - never substituted for it.
    recomputed_base_rate = n_reverting / n_resolved
    if abs(recomputed_base_rate - FROZEN_THRESHOLD) > _EPS_THRESHOLD:
        raise SystemExit(f"INTEGRITY VIOLATION: recomputed resolved-TRAIN REVERSION base rate "
                          f"{recomputed_base_rate!r} disagrees with the frozen threshold "
                          f"{FROZEN_THRESHOLD!r}.")
    print(f"Frozen threshold cross-check: recomputed base rate {recomputed_base_rate:.8f} "
          f"matches frozen {FROZEN_THRESHOLD:.8f}.")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        penalty="l2", C=1.0, solver="lbfgs", fit_intercept=True,
        class_weight=None, max_iter=1000,
    )
    model.fit(X_scaled, y)

    # TRAIN-only diagnostic - descriptive, NOT the predictive gate (VALIDATION-only, unevaluated).
    train_proba = model.predict_proba(X_scaled)[:, 1]
    train_auc_diagnostic = float(roc_auc_score(y, train_proba))

    model_record = {
        "classifier_spec_sha256": CLASSIFIER_SPEC_SHA256,
        "feature_order": FROZEN_FEATURE_ORDER,
        "hyperparameters": {
            "penalty": "l2", "C": 1.0, "solver": "lbfgs", "fit_intercept": True,
            "class_weight": None, "max_iter": 1000,
        },
        "scaler": {
            "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(),
        },
        "model": {
            "coef": model.coef_[0].tolist(),
            "intercept": float(model.intercept_[0]),
            "classes": model.classes_.tolist(),
        },
        "threshold": FROZEN_THRESHOLD,
    }
    with MODEL_JSON.open("w", encoding="utf-8") as h:
        json.dump(model_record, h, indent=2)

    fit_report = {
        "n_resolved_fit_population": n_resolved,
        "n_reverting": n_reverting,
        "n_deteriorating": n_deteriorating,
        "n_unresolved_excluded_from_fitting": n_unresolved,
        "frozen_threshold": FROZEN_THRESHOLD,
        "recomputed_base_rate_cross_check": recomputed_base_rate,
        "threshold_cross_check_passed": True,
        "train_only_diagnostic_auc_NOT_A_GATE": train_auc_diagnostic,
        "note": "train_only_diagnostic_auc is descriptive only - it is fit-set performance, "
                "not the predictive gate. The predictive gate (AUC-ROC >= 0.55) is defined on "
                "VALIDATION only and has not been evaluated. VALIDATION and HOLDOUT were not "
                "read or touched by this script.",
        "feature_order": FROZEN_FEATURE_ORDER,
        "diagnostic_columns_excluded": True,
        "validation_touched": False,
        "holdout_touched": False,
    }
    with FIT_REPORT_JSON.open("w", encoding="utf-8") as h:
        json.dump(fit_report, h, indent=2)

    print(f"\nFitted model -> {MODEL_JSON}")
    print(f"Fit report -> {FIT_REPORT_JSON}")
    print(f"\nTRAIN-only diagnostic AUC (NOT a gate): {train_auc_diagnostic:.4f}")
    print("\nVALIDATION and HOLDOUT not read. Predictive gate (VALIDATION AUC-ROC>=0.55) "
          "not evaluated. Economic gate not evaluated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
