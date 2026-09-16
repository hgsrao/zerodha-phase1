import pytest

from revision4.v3_intraday_calibration import REQUIRED_TEN_BOX_PATHS, V3IntradayCalibrator


def _safe_report(net_pnl):
    return {
        "status": "PASSED",
        "financials": {"realized_pnl": net_pnl},
        "intraday_audit": {"completed_trade_count": 4, "all_trades_same_session": True},
        "reconciliation": {"exact": True},
        "ten_box_audit": {"calls": {name: 1 for name in REQUIRED_TEN_BOX_PATHS}},
    }


def test_calibrator_trains_then_validates_without_validation_feedback():
    calls = []

    def evaluator(**kwargs):
        calls.append(kwargs)
        score = float(kwargs["calibration_overrides"]["profit_target_atr_mult"])
        return _safe_report(score)

    calibrator = V3IntradayCalibrator(
        "2023-09-01", "2023-09-15", "2023-09-18", "2023-09-22",
        parameter_names=("profit_target_atr_mult",), evaluator=evaluator, seed=5,
    )
    result = calibrator.run(phase1_trials=2, phase2_generations=0, phase3_iterations=0, validation_finalists=1)

    assert len(result.training_trials) == 2
    assert len(result.validation_trials) == 1
    assert result.selected_params is not None
    assert all(call["month_start"] == "2023-09-01" for call in calls[:2])
    assert calls[-1]["month_start"] == "2023-09-18"


def test_calibrator_refuses_immutable_gate16_parameter():
    with pytest.raises(ValueError, match="non-calibratable"):
        V3IntradayCalibrator(
            "2023-09-01", "2023-09-15", "2023-09-18", "2023-09-22",
            parameter_names=("slippage_tolerance_percent",),
        )


def test_calibrator_rejects_non_intraday_candidate_even_with_positive_pnl():
    report = _safe_report(100.0)
    report["intraday_audit"]["all_trades_same_session"] = False
    assert V3IntradayCalibrator._score(report) == float("-inf")


def test_calibrator_rejects_report_missing_a_required_box_path():
    report = _safe_report(100.0)
    del report["ten_box_audit"]["calls"]["exit_controller"]
    assert V3IntradayCalibrator._score(report) == float("-inf")
