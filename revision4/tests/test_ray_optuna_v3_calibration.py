import pytest

import revision4.ray_optuna_v3_calibration as ray_calibration
from revision4.ray_optuna_v3_calibration import (
    INELIGIBLE_SCORE,
    RayOptunaV3Calibrator,
    normalize_trial_parameters,
    search_space_for,
    validated_parameter_specs,
)
from revision4.v3_intraday_calibration import REQUIRED_TEN_BOX_PATHS, V3IntradayCalibrator


def _safe_report(net_pnl=12.0):
    return {
        "status": "PASSED",
        "financials": {"realized_pnl": net_pnl},
        "intraday_audit": {"completed_trade_count": 3, "all_trades_same_session": True},
        "reconciliation": {"exact": True},
        "ten_box_audit": {"calls": {name: 1 for name in REQUIRED_TEN_BOX_PATHS}},
    }


def test_ray_surface_contains_only_requested_canonical_economic_parameters():
    names = ("profit_target_atr_mult", "max_hold_bars")
    specs = validated_parameter_specs(names)
    space = search_space_for(names)
    assert set(specs) == set(space) == set(names)
    assert specs["max_hold_bars"].param_type == "int"


def test_ray_surface_refuses_gate16_safety_parameter():
    with pytest.raises(ValueError, match="non-calibratable"):
        validated_parameter_specs(("slippage_tolerance_percent",))


def test_sampled_values_are_normalized_to_canonical_types():
    params = normalize_trial_parameters(
        {"profit_target_atr_mult": 1.7, "max_hold_bars": 61.9},
        ("profit_target_atr_mult", "max_hold_bars"),
    )
    assert params == {"profit_target_atr_mult": 1.7, "max_hold_bars": 61}


def test_invalid_report_maps_to_finite_ray_sentinel_not_an_economic_score():
    report = _safe_report()
    report["status"] = "NO_EXECUTION"
    assert V3IntradayCalibrator._score(report) == float("-inf")
    assert INELIGIBLE_SCORE < -1e17


def test_parallelism_is_hard_bounded_to_two_workers():
    calibrator = RayOptunaV3Calibrator("2023-09-01", "2023-09-29", "2023-10-02", "2023-10-06")
    with pytest.raises(ValueError, match="between 1 and 2"):
        calibrator.run(samples=1, max_concurrent_trials=3)


def test_worker_manifest_path_is_absolute_for_ray_trial_directories():
    calibrator = RayOptunaV3Calibrator("2023-09-01", "2023-09-29", "2023-10-02", "2023-10-06")
    assert calibrator.manifest_path.startswith("/")


def test_trial_reports_one_metrics_mapping_to_ray(monkeypatch):
    captured = {}
    monkeypatch.setattr(ray_calibration.tune, "report", lambda metrics: captured.update(metrics))

    def evaluator(**_kwargs):
        return _safe_report(27.0)

    ray_calibration.evaluate_sealed_trial(
        {"profit_target_atr_mult": 1.5},
        train_start="2023-09-01",
        train_end="2023-09-29",
        parameter_names=("profit_target_atr_mult",),
        evaluator=evaluator,
    )
    assert captured["score"] == 27.0
    assert captured["eligible"] is True
    assert captured["completed_trades"] == 3
