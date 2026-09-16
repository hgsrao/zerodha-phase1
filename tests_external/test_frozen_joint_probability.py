import pandas as pd

from revision2_external.frozen_decile_analysis import FEATURES
from revision2_external.frozen_joint_probability import fit_and_score


def _frame(labels):
    rows = []
    for index, label in enumerate(labels):
        rows.append({
            "label": label, "side": "BUY" if index % 2 else "SELL",
            "time_of_day": "10:30" if index % 3 else "09:30",
            **{feature: float(index) for feature in FEATURES},
        })
    return pd.DataFrame(rows)


def test_joint_model_fits_train_only_and_reports_frozen_later_deciles():
    train = _frame(["TARGET_FIRST", "STOP_FIRST"] * 30)
    validation = _frame(["TARGET_FIRST", "STOP_FIRST"] * 10 + ["TIMEOUT"])
    report = fit_and_score(train, [("validation", validation)])

    assert report["contract"]["model_is_entry_rule"] is False
    assert len(report["train_score_decile_edges"]) == 11
    assert {row["split"] for row in report["cohorts"]} == {"train", "validation"}
    assert all(0.0 <= row["target_rate"] <= 1.0 for row in report["cohorts"] if row["target_rate"] is not None)
