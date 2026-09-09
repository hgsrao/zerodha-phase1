from dataclasses import asdict

import pytest

import inhouse_validation.ray_optuna_calibration as calibration
from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.boxes import SafetyGatesTargetBox
from revision2.contracts import EffectiveConfig, TradePlan


def test_profit_floor_override_changes_real_post_sizing_admission():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    plan = TradePlan("BUY", 100.0, 95.0, 120.0, 1, 20)
    box = SafetyGatesTargetBox()
    blocked, _, _ = box.evaluate_post_sizing([100000.0], plan, 1,
                                               EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256))
    values["minimum_absolute_profit_rupees"] = 10.0
    allowed, _, _ = box.evaluate_post_sizing([100000.0], plan, 1,
                                               EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256))
    assert not blocked
    assert allowed


def test_inhouse_surface_rejects_immutable_safety_parameter():
    with pytest.raises(ValueError, match="non-calibratable"):
        calibration._specs(("slippage_tolerance_percent",))


def test_inhouse_surface_is_limited_to_economic_parameters():
    space = calibration.inhouse_search_space(("minimum_absolute_profit_rupees", "max_hold_bars"))
    assert set(space) == {"minimum_absolute_profit_rupees", "max_hold_bars"}


def test_inhouse_eligibility_rejects_no_execution_or_remediation():
    base = {"status": "PASSED", "reconciliation_exact": True,
            "audit_chain_valid": True, "gate16_breaches": 0}
    assert calibration._eligible(base, 1)
    assert not calibration._eligible(base, 0)
    assert not calibration._eligible({**base, "gate16_breaches": 1}, 1)
