from revision2_external.sealed_research_evaluator import evaluate_fixed_alpha


def _row(n=40, gross=1.0, net=.5):
    return {"candidates": n, "gross_pnl_per_share": gross, "net_pnl_per_share": net, "target_first_rate": .6}


def test_training_profit_alone_cannot_promote_an_alpha():
    report = evaluate_fixed_alpha({"train": _row(), "validation": _row(net=-.1), "test": _row()})
    assert report["overall_verdict"] == "NOT_ELIGIBLE_FOR_CLOSED_LOOP_SHADOW"
    assert report["window_verdicts"][1]["verdict"] == "REJECTED"


def test_all_time_separated_positive_windows_are_eligible_for_shadow_only():
    report = evaluate_fixed_alpha({"train": _row(), "validation": _row(), "test": _row()})
    assert report["overall_verdict"] == "ELIGIBLE_FOR_CLOSED_LOOP_SHADOW"


def test_low_sample_window_is_not_silently_promoted():
    report = evaluate_fixed_alpha({"train": _row(), "validation": _row(n=2), "test": _row()})
    assert report["window_verdicts"][1]["verdict"] == "INSUFFICIENT_EVIDENCE"
