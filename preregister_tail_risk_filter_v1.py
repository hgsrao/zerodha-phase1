from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

ROOT = Path.cwd()

FREEZE = ROOT / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json"
CLOSURE = ROOT / "RISK_HEAD_V1_FINAL_CLOSURE_20260825.json"
FUSION = ROOT / "daily_multitimescale_sensor_fusion_v1_20260825.csv"

OUT_JSON = ROOT / "TAIL_RISK_FILTER_V1_PREREGISTERED_20260825.json"
OUT_MD = ROOT / "TAIL_RISK_FILTER_V1_PREREGISTERED_20260825.md"
OUT_SHA = ROOT / "TAIL_RISK_FILTER_V1_PREREGISTERED_20260825.sha256"

EXPECTED_DATASET_SHA = (
    "2f51f697982063233ee120f0fbd93989"
    "d523efe868f810564efa26ef8099c888"
)

EXPECTED_RISK_FREEZE_SHA = (
    "b018eaaccc8096c0ceb749e8ae087f8"
    "cd8348e2ebbe8827383c978a48fe61adc"
)

EXPECTED_RISK_CLOSURE_SHA = (
    "5aa6c821f7eaee040fa38cedf634eb2a"
    "9e6f6b7289ddb33d28dcc3bcd0cd1ea0"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"Missing/empty source: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


print("=" * 100)
print("TAIL-RISK FILTER V1 - FORMAL PREREGISTRATION")
print("=" * 100)

if OUT_JSON.exists() or OUT_MD.exists() or OUT_SHA.exists():
    raise RuntimeError(
        "Preregistration output already exists. "
        "Refusing to overwrite an existing preregistration."
    )

freeze = load_json(FREEZE)
closure = load_json(CLOSURE)

freeze_sha = sha256_file(FREEZE)
closure_sha = sha256_file(CLOSURE)

if freeze_sha != EXPECTED_RISK_FREEZE_SHA:
    raise RuntimeError(
        f"Risk Head freeze SHA mismatch: {freeze_sha}"
    )

if closure_sha != EXPECTED_RISK_CLOSURE_SHA:
    raise RuntimeError(
        f"Risk Head closure SHA mismatch: {closure_sha}"
    )

dataset_sha = (
    freeze.get("dataset", {}).get("sha256")
    or freeze.get("dataset", {}).get("dataset_sha256")
    or freeze.get("dataset", {}).get("frozen_dataset_sha256")
)

if dataset_sha != EXPECTED_DATASET_SHA:
    raise RuntimeError(
        f"Frozen dataset SHA mismatch: {dataset_sha}"
    )

features = freeze["candidate"]["feature_list"]

if len(features) != 25:
    raise RuntimeError(
        f"Expected 25 frozen B features, found {len(features)}"
    )

candidate = freeze["candidate"]

prereg = {
    "schema": "TAIL_RISK_FILTER_V1_PREREGISTRATION",
    "registered_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "PREREGISTERED_BEFORE_OUTCOME_INSPECTION",

    "research_question": (
        "Can the already-frozen Linear Risk Head B score identify a "
        "small subset of unusually dangerous next-day stock observations, "
        "even though continuous risk ranking failed its 2026 primary holdout?"
    ),

    "source_model": {
        "feature_set": "B_INTRADAY_DAILY_SUMMARY",
        "feature_count": 25,
        "feature_list": features,
        "model_family": candidate.get("model_family", "Ridge"),
        "alpha": 1000.0,
        "fit_policy": (
            "Fit on the original TRAIN population only, using the exact "
            "frozen preprocessing contract. Do not refit on validation, "
            "holdout, or post-holdout observations."
        ),
        "preprocessing_contract": freeze["preprocessing_contract"],
        "risk_head_freeze_file": FREEZE.name,
        "risk_head_freeze_sha256": freeze_sha,
        "risk_head_final_closure_file": CLOSURE.name,
        "risk_head_final_closure_sha256": closure_sha,
    },

    "evaluation_population": {
        "start_date": "2026-07-28",
        "end_date": "2026-08-21",
        "dates": 19,
        "symbols": 8,
        "rows": 152,
        "standard_75bar_dates": 4,
        "standard_75bar_rows": 32,
        "cas_72bar_dates": 15,
        "cas_72bar_rows": 120,
        "excluded_date": "2026-08-24",
        "excluded_reason": (
            "Next-day outcome unavailable in the current archive."
        ),
        "data_status": (
            "All 19 evaluation dates are genuinely post-holdout and were "
            "not used for training, validation, model selection, or the "
            "Risk Head V1 final holdout."
        ),
    },

    "target": {
        "continuous": "adverse_1d = max(0, -mae_1d)",
        "binary_tail_event": "adverse_1d >= 0.020",
    },

    "primary_test": {
        "name": "TOP_QUARTILE_EXCESS_ADVERSE",
        "definition": (
            "Within each evaluation date, rank the 8 symbols by the frozen "
            "predicted-risk score. The top 2 symbols constitute the fixed "
            "top-risk quartile. Compare their realized adverse_1d against "
            "the remaining 6 symbols."
        ),
        "estimate": (
            "Mean adverse_1d(top 2 per date) minus "
            "mean adverse_1d(other 6 per date)."
        ),
        "inference": (
            "Date-block bootstrap resampling whole dates with replacement, "
            "preserving all 8 same-day symbols together."
        ),
        "bootstrap_iterations": 10000,
        "success_criterion": (
            "Point estimate > 0 AND two-sided 95% date-block bootstrap "
            "confidence interval lower bound > 0."
        ),
    },

    "secondary_test": {
        "name": "TWO_PERCENT_EVENT_ROC_AUC",
        "definition": (
            "ROC-AUC of the frozen continuous predicted-risk score for "
            "adverse_1d >= 0.020 across all 152 observations."
        ),
        "inference": (
            "Date-block bootstrap resampling whole dates with replacement."
        ),
        "bootstrap_iterations": 10000,
        "success_criterion": (
            "ROC-AUC point estimate > 0.50 AND two-sided 95% "
            "date-block bootstrap confidence interval lower bound > 0.50."
        ),
    },

    "supporting_diagnostics": {
        "average_precision": (
            "Report average precision and compare it with the observed "
            "2% event rate. Descriptive/supporting only."
        ),
        "cas_sensitivity": (
            "Report primary effect and ROC-AUC separately for CAS 72-bar "
            "observations where estimable. Do not use the 4 standard dates "
            "for significance claims because that sample is too small."
        ),
        "symbol_diagnostics": (
            "Report symbol-level event counts/effects descriptively only. "
            "No symbol exclusion or selection is permitted."
        ),
    },

    "classification_rule": {
        "FULL_PASS": (
            "Both preregistered primary and secondary success criteria pass."
        ),
        "PARTIAL_PASS": (
            "Exactly one of the two preregistered success criteria passes."
        ),
        "NO_GO": (
            "Neither preregistered success criterion passes."
        ),
        "production_note": (
            "Even FULL_PASS is research evidence only because the evaluation "
            "contains only 19 dates. It does not authorize live exposure control."
        ),
    },

    "prohibitions": [
        "Do not change the 25 frozen features.",
        "Do not change Ridge alpha=1000.",
        "Do not refit using validation, 2026 holdout, or post-holdout data.",
        "Do not flip score sign.",
        "Do not search alternative event thresholds.",
        "Do not search top-10%, top-20%, top-third or other buckets.",
        "Do not exclude symbols or dates based on observed outcomes.",
        "Do not substitute a different primary metric after results are seen.",
        "Do not reuse the closed 2026 Risk Head holdout for model rescue.",
        "Do not authorize production exposure control from this pilot alone.",
        "P01D remains unchanged and sovereign."
    ],
}

OUT_JSON.write_text(
    json.dumps(prereg, indent=2, sort_keys=True) + "\n",
    encoding="utf-8"
)

json_sha = sha256_file(OUT_JSON)

md = f"""# Tail-Risk Filter V1 — Preregistration
## 25 August 2026

**STATUS: PREREGISTERED BEFORE POST-HOLDOUT OUTCOME INSPECTION**

### Frozen source model

- Feature set: B_INTRADAY_DAILY_SUMMARY
- Model: Ridge
- Alpha: 1000
- Frozen features: 25
- Fit policy: original TRAIN only
- Risk Head freeze SHA256: `{freeze_sha}`
- Risk Head closure SHA256: `{closure_sha}`

### New unseen evaluation population

- 2026-07-28 through 2026-08-21
- 19 dates
- 8 symbols
- 152 symbol-days
- 4 standard 75-bar dates / 32 rows
- 15 CAS 72-bar dates / 120 rows
- 2026-08-24 excluded because next-day outcome is unavailable

### Primary hypothesis

Within each date, rank all eight symbols by the frozen risk score.

The **top two symbols** are the fixed top-risk quartile.

Primary estimate:

`mean adverse_1d(top 2) - mean adverse_1d(other 6)`

Primary PASS requires:

- estimate > 0; and
- two-sided 95% date-block bootstrap CI lower bound > 0.

### Secondary hypothesis

Binary event:

`adverse_1d >= 2%`

Secondary PASS requires:

- ROC-AUC > 0.50; and
- two-sided 95% date-block bootstrap CI lower bound > 0.50.

### Inference

10,000 bootstrap iterations.

The bootstrap resamples **whole trading dates**, retaining all eight symbols
together so same-day cross-symbol dependence is preserved.

### Classification

- FULL PASS: primary and secondary both pass.
- PARTIAL PASS: exactly one passes.
- NO-GO: neither passes.

Even FULL PASS remains research evidence only because there are only 19 dates.
It does not authorize live exposure control.

### Locked prohibitions

No feature changes, alpha changes, score inversion, threshold search, bucket
search, symbol/date exclusions, refitting on later data, or post-result change
of primary metric.

P01D remains unchanged and sovereign.

### Preregistration JSON SHA256

`{json_sha}`
"""

OUT_MD.write_text(md, encoding="utf-8")

md_sha = sha256_file(OUT_MD)

OUT_SHA.write_text(
    f"{json_sha}  {OUT_JSON.name}\n"
    f"{md_sha}  {OUT_MD.name}\n",
    encoding="utf-8"
)

print()
print("PREREGISTRATION WRITTEN")
print()
print("JSON :", OUT_JSON.name)
print("MD   :", OUT_MD.name)
print("SHA  :", OUT_SHA.name)
print()
print("JSON SHA256:", json_sha)
print("MD SHA256  :", md_sha)
print()
print("Evaluation sample locked:")
print("  19 dates")
print("  152 symbol-days")
print("  Top-risk bucket = exactly 2 of 8 symbols per date")
print("  Tail event = adverse_1d >= 2%")
print("  Bootstrap = whole-date blocks, 10,000 iterations")
print()
print("NO OUTCOMES READ OR SCORED BY THIS SCRIPT")
print("=" * 100)
