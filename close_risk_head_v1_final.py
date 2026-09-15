from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json


ROOT = Path(__file__).resolve().parent

FREEZE = ROOT / "RISK_HEAD_V1_CANDIDATE_B_FROZEN_20260825.json"
MODEL0 = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL0_LINEAR_VALIDATION_20260825.json"
MODEL1 = ROOT / "SELF_LEARNING_RISK_HEAD_V1_MODEL1_NONLINEAR_VALIDATION_20260825.json"
HOLDOUT = ROOT / "RISK_HEAD_V1_CANDIDATE_B_HOLDOUT_EXAM_RESULT_20260825.json"

OUT_JSON = ROOT / "RISK_HEAD_V1_FINAL_CLOSURE_20260825.json"
OUT_MD = ROOT / "RISK_HEAD_V1_FINAL_CLOSURE_20260825.md"
OUT_SHA = ROOT / "RISK_HEAD_V1_FINAL_CLOSURE_20260825.sha256"

EXPECTED_DATASET_SHA = (
    "2f51f697982063233ee120f0fbd93989"
    "d523efe868f810564efa26ef8099c888"
)

EXPECTED_FREEZE_SHA = (
    "b018eaaccc8096c0ceb749e8ae087f8"
    "cd8348e2ebbe8827383c978a48fe61adc"
)

CANDIDATE_KEY = "B_INTRADAY_DAILY_SUMMARY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"Required source missing: {path.name}")

    if path.stat().st_size == 0:
        raise RuntimeError(f"Required source is empty: {path.name}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(
            f"JSON parse failed for {path.name}: {e}"
        )


def metric(d: dict, key: str) -> dict:
    if key not in d:
        raise RuntimeError(
            f"Required metric '{key}' not found. "
            f"Available keys: {sorted(d.keys())}"
        )

    value = d[key]

    if not isinstance(value, dict):
        raise RuntimeError(
            f"Metric '{key}' is not a metric dictionary"
        )

    return value


print("=" * 100)
print("RISK HEAD V1 - FINAL HOLDOUT CLOSURE")
print("=" * 100)

freeze = load_json(FREEZE)
m0 = load_json(MODEL0)
m1 = load_json(MODEL1)
hold = load_json(HOLDOUT)

# ------------------------------------------------------------------
# 1. PROVENANCE / HASH CHECKS
# ------------------------------------------------------------------

freeze_sha = sha256_file(FREEZE)

if freeze_sha != EXPECTED_FREEZE_SHA:
    raise RuntimeError(
        "Frozen candidate record hash mismatch.\n"
        f"Expected: {EXPECTED_FREEZE_SHA}\n"
        f"Actual  : {freeze_sha}"
    )

for name, obj in [
    ("MODEL0", m0),
    ("MODEL1", m1),
    ("HOLDOUT", hold),
]:
    actual = obj.get("frozen_dataset_sha256")

    if actual != EXPECTED_DATASET_SHA:
        raise RuntimeError(
            f"{name} dataset hash mismatch: {actual}"
        )

freeze_dataset_sha = (
    freeze.get("dataset", {}).get("sha256")
    or freeze.get("dataset", {}).get("dataset_sha256")
    or freeze.get("dataset", {}).get("frozen_dataset_sha256")
)

if freeze_dataset_sha != EXPECTED_DATASET_SHA:
    raise RuntimeError(
        "Freeze record dataset hash mismatch: "
        f"{freeze_dataset_sha}"
    )

if freeze.get("holdout_touched") is not False:
    raise RuntimeError(
        "Freeze record does not certify holdout_touched=false"
    )

if m0.get("holdout_touched") is not False:
    raise RuntimeError(
        "Model 0 validation record touched holdout"
    )

if m1.get("holdout_touched") is not False:
    raise RuntimeError(
        "Model 1 validation record touched holdout"
    )

if hold.get("holdout_touched") is not True:
    raise RuntimeError(
        "Holdout result does not certify holdout_touched=true"
    )

if hold.get("alpha") != 1000.0:
    raise RuntimeError(
        f"Unexpected holdout alpha: {hold.get('alpha')}"
    )

if hold.get("n_features") != 25:
    raise RuntimeError(
        f"Unexpected holdout feature count: {hold.get('n_features')}"
    )

if hold.get("target") != "adverse_1d=max(0,-mae_1d)":
    raise RuntimeError(
        f"Unexpected holdout target: {hold.get('target')}"
    )

if hold.get("secondary_event") != "adverse_1d >= 0.020":
    raise RuntimeError(
        f"Unexpected secondary event: {hold.get('secondary_event')}"
    )

hold_freeze_sha = hold.get("frozen_candidate_record_sha256")

if hold_freeze_sha != EXPECTED_FREEZE_SHA:
    raise RuntimeError(
        "Holdout exam did not use the expected frozen candidate record"
    )

# ------------------------------------------------------------------
# 2. SOURCE RESULTS
# ------------------------------------------------------------------

if CANDIDATE_KEY not in m0.get("results", {}):
    raise RuntimeError("Linear B result missing from Model 0")

if CANDIDATE_KEY not in m1.get("results", {}):
    raise RuntimeError("Nonlinear B result missing from Model 1")

linear_b = m0["results"][CANDIDATE_KEY]
nonlinear_b = m1["results"][CANDIDATE_KEY]

linear_validation = linear_b.get("validation")

if not isinstance(linear_validation, dict):
    raise RuntimeError(
        "Linear B validation result missing"
    )

hold_results = hold.get("results")

if not isinstance(hold_results, dict):
    raise RuntimeError(
        "Holdout results dictionary missing"
    )

# Required holdout metrics from the one-time exam.
risk_spearman = metric(
    hold_results,
    "risk_spearman"
)

roc_auc = metric(
    hold_results,
    "event_2pct_roc_auc"
)

avg_precision = metric(
    hold_results,
    "event_2pct_average_precision"
)

top_quartile = metric(
    hold_results,
    "top_risk_quartile_excess_adverse"
)

event_rate = hold_results.get("event_rate")

if event_rate is None:
    raise RuntimeError(
        "Holdout event_rate missing"
    )

for name, x in [
    ("risk_spearman", risk_spearman),
    ("event_2pct_roc_auc", roc_auc),
    ("event_2pct_average_precision", avg_precision),
    ("top_risk_quartile_excess_adverse", top_quartile),
]:
    for required in ["estimate", "ci_low", "ci_high"]:
        if required not in x:
            raise RuntimeError(
                f"{name}.{required} missing"
            )

# ------------------------------------------------------------------
# 3. FINAL CLASSIFICATION
#
# Primary criterion was frozen as risk Spearman.
# Its holdout CI crossing zero means PRIMARY NO-GO.
#
# Secondary findings are preserved, not promoted to primary after
# seeing holdout.
# ------------------------------------------------------------------

primary_ci_excludes_zero_positive = (
    risk_spearman["ci_low"] > 0
)

auc_ci_above_random = (
    roc_auc["ci_low"] > 0.5
)

topq_ci_positive = (
    top_quartile["ci_low"] > 0
)

if primary_ci_excludes_zero_positive:
    raise RuntimeError(
        "Unexpected: primary holdout CI is entirely positive. "
        "Closure classification must be reviewed manually."
    )

secondary_tail_signal_survived = (
    auc_ci_above_random
    and topq_ci_positive
)

final_classification = {
    "primary_holdout_result":
        "NO_GO_PRIMARY_METRIC_NOT_CONFIRMED",

    "primary_reason":
        "Frozen primary risk-Spearman point estimate remained positive "
        "but its 95% date-block bootstrap confidence interval crossed zero.",

    "secondary_tail_risk_result":
        (
            "PARTIAL_PASS_SECONDARY_SIGNAL_SURVIVED"
            if secondary_tail_signal_survived
            else "NO_GO_SECONDARY_SIGNAL_NOT_CONFIRMED"
        ),

    "secondary_reason":
        "The predeclared 2% adverse-event ROC-AUC remained above random "
        "with its confidence interval above 0.50, and the highest predicted-"
        "risk quartile retained positive excess adverse excursion."
        if secondary_tail_signal_survived
        else
        "The secondary tail-risk diagnostics did not jointly retain "
        "their required positive holdout evidence.",

    "overall_research_status":
        "RISK_HEAD_V1_CLOSED_MIXED_RESULT",

    "production_authorization":
        "NOT_AUTHORIZED",

    "live_exposure_control_authorized":
        False,

    "retuning_on_2026_holdout_authorized":
        False,

    "second_holdout_attempt_authorized":
        False,

    "allowed_future_research":
        "A new tail-risk-filter hypothesis may be studied only on genuinely "
        "new unseen data or prospective shadow observations. The 2026 "
        "holdout may not be reused for model selection or rescue.",
}

# ------------------------------------------------------------------
# 4. BUILD PERMANENT JSON RECORD
# ------------------------------------------------------------------

closure = {
    "schema":
        "RISK_HEAD_V1_FINAL_HOLDOUT_CLOSURE",

    "closed_at_utc":
        datetime.now(timezone.utc).isoformat(),

    "status":
        "CLOSED",

    "frozen_candidate": {
        "feature_set":
            CANDIDATE_KEY,

        "model_family":
            hold.get("model_family"),

        "alpha":
            hold.get("alpha"),

        "n_features":
            hold.get("n_features"),

        "target":
            hold.get("target"),

        "secondary_event":
            hold.get("secondary_event"),

        "fit_policy":
            hold.get("fit_policy"),
    },

    "provenance": {
        "frozen_dataset_sha256":
            EXPECTED_DATASET_SHA,

        "frozen_candidate_record":
            FREEZE.name,

        "frozen_candidate_record_sha256":
            freeze_sha,

        "model0_validation_file":
            MODEL0.name,

        "model0_validation_sha256":
            sha256_file(MODEL0),

        "model1_validation_file":
            MODEL1.name,

        "model1_validation_sha256":
            sha256_file(MODEL1),

        "holdout_exam_file":
            HOLDOUT.name,

        "holdout_exam_sha256":
            sha256_file(HOLDOUT),
    },

    "validation_linear_B":
        linear_validation,

    "nonlinear_B_result":
        nonlinear_b,

    "holdout_exam": {
        "window":
            hold.get("holdout_window"),

        "one_time_exam":
            hold.get("one_time_exam"),

        "train_window":
            hold.get("train_window"),

        "results":
            hold_results,

        "monthly_spearman":
            hold.get("monthly_spearman"),

        "symbol_spearman":
            hold.get("symbol_spearman"),
    },

    "final_classification":
        final_classification,

    "interpretation_guardrails": [
        "Primary holdout failure must not be rewritten as an overall pass.",
        "Secondary 2% tail-risk discrimination is preserved as a real but limited finding.",
        "Secondary metrics must not be promoted post hoc to replace the frozen primary metric.",
        "No sign flip, threshold change, feature change, model change, or refit may be justified using the 2026 holdout.",
        "Risk Head V1 is not authorized to control production exposure.",
        "Any future tail-risk-filter research requires genuinely new unseen or prospective data.",
        "P01D remains sovereign and unchanged.",
    ],
}

OUT_JSON.write_text(
    json.dumps(
        closure,
        indent=2,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

json_sha = sha256_file(OUT_JSON)

# ------------------------------------------------------------------
# 5. HUMAN-READABLE MARKDOWN
# ------------------------------------------------------------------

md = f"""# Risk Head V1 — Final Holdout Closure
## 25 August 2026

**STATUS: CLOSED — PRIMARY NO-GO / SECONDARY TAIL-RISK PARTIAL PASS**

### Frozen candidate

- Feature family: `{CANDIDATE_KEY}`
- Model: `{hold.get("model_family")}`
- Ridge alpha: `{hold.get("alpha")}`
- Frozen features: `{hold.get("n_features")}`
- Target: `{hold.get("target")}`
- Secondary event: `{hold.get("secondary_event")}`
- Dataset SHA256: `{EXPECTED_DATASET_SHA}`
- Frozen candidate SHA256: `{freeze_sha}`

### 2025 validation

The candidate earned the one-time holdout exam on the frozen primary
risk-ranking metric. Exact validation values are preserved in the JSON
closure record directly from the Model 0 source artifact.

### One-time 2026 holdout exam

Holdout window: `{hold.get("holdout_window")}`

Primary Risk Spearman:

- Estimate: `{risk_spearman["estimate"]:+.6f}`
- 95% CI: `[{risk_spearman["ci_low"]:+.6f}, {risk_spearman["ci_high"]:+.6f}]`

2% adverse-event ROC-AUC:

- Estimate: `{roc_auc["estimate"]:.6f}`
- 95% CI: `[{roc_auc["ci_low"]:.6f}, {roc_auc["ci_high"]:.6f}]`

2% event average precision:

- Estimate: `{avg_precision["estimate"]:.6f}`
- 95% CI: `[{avg_precision["ci_low"]:.6f}, {avg_precision["ci_high"]:.6f}]`
- Actual holdout event rate: `{float(event_rate):.6%}`

Highest predicted-risk quartile excess adverse excursion:

- Estimate: `{top_quartile["estimate"] * 10000:+.2f} bp`
- 95% CI: `[{top_quartile["ci_low"] * 10000:+.2f}, {top_quartile["ci_high"] * 10000:+.2f}] bp`

### Final classification

**Primary result: NO-GO.**

The predeclared primary metric remained positive in point estimate but
its 95% confidence interval crossed zero on the untouched holdout.
Therefore general continuous risk-ranking ability was not independently
confirmed.

**Secondary tail-risk result: PARTIAL PASS.**

The 2% adverse-event classifier retained discrimination above random,
and the highest predicted-risk quartile retained greater realized
adverse excursion. These are genuine secondary findings, but they do
not replace the failed primary criterion.

### Production decision

**NOT AUTHORIZED FOR LIVE EXPOSURE CONTROL.**

No second attempt on the 2026 holdout is permitted. No sign inversion,
feature retuning, threshold change, target change, or model rescue may
be justified using the holdout results.

A future tail-risk-filter hypothesis may be researched only using
genuinely new unseen data or prospective shadow observations.

P01D remains sovereign.

### Closure provenance

- Model 0 SHA256: `{sha256_file(MODEL0)}`
- Model 1 SHA256: `{sha256_file(MODEL1)}`
- Holdout result SHA256: `{sha256_file(HOLDOUT)}`
- Closure JSON SHA256: `{json_sha}`
"""

OUT_MD.write_text(
    md,
    encoding="utf-8",
)

md_sha = sha256_file(OUT_MD)

OUT_SHA.write_text(
    f"{json_sha}  {OUT_JSON.name}\n"
    f"{md_sha}  {OUT_MD.name}\n",
    encoding="utf-8",
)

# ------------------------------------------------------------------
# 6. REPORT
# ------------------------------------------------------------------

print()
print("=" * 100)
print("FINAL CLOSURE WRITTEN")
print("=" * 100)

print("Primary Risk Spearman:")
print(
    f"  {risk_spearman['estimate']:+.6f} "
    f"95% CI "
    f"[{risk_spearman['ci_low']:+.6f}, "
    f"{risk_spearman['ci_high']:+.6f}]"
)

print()
print("2% ROC-AUC:")
print(
    f"  {roc_auc['estimate']:.6f} "
    f"95% CI "
    f"[{roc_auc['ci_low']:.6f}, "
    f"{roc_auc['ci_high']:.6f}]"
)

print()
print("Top-risk quartile excess adverse:")
print(
    f"  {top_quartile['estimate'] * 10000:+.2f} bp "
    f"95% CI "
    f"[{top_quartile['ci_low'] * 10000:+.2f}, "
    f"{top_quartile['ci_high'] * 10000:+.2f}] bp"
)

print()
print(
    "PRIMARY CLASSIFICATION :",
    final_classification["primary_holdout_result"]
)

print(
    "SECONDARY CLASSIFICATION:",
    final_classification["secondary_tail_risk_result"]
)

print(
    "PRODUCTION AUTHORIZED  :",
    final_classification["live_exposure_control_authorized"]
)

print()
print("JSON:", OUT_JSON)
print("MD  :", OUT_MD)
print("SHA :", OUT_SHA)

print()
print("Closure JSON SHA256:", json_sha)
print("Closure MD SHA256  :", md_sha)

print()
print("=" * 100)
print("RISK HEAD V1 PERMANENTLY CLOSED")
print("=" * 100)


if __name__ == "__main__":
    pass
