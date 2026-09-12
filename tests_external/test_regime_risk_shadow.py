from revision2_external.closed_loop_control import HMMRiskHysteresis, regime_risk_derate


def test_hmm_posterior_recommendation_only_derates_risk():
    result = regime_risk_derate({"available": True, "stress_probability": 0.72})
    assert result["suggested_regime_derate"] == 0.28
    assert 0.0 <= result["suggested_regime_derate"] <= 1.0


def test_missing_regime_never_invents_a_risk_signal():
    result = regime_risk_derate({"available": False, "reason": "NO_DISTINCT_STRESSED_STATE"})
    assert result["suggested_regime_derate"] == 1.0
    assert result["available"] is False


def test_hysteresis_requires_sustained_stress_then_sustained_recovery():
    loop = HMMRiskHysteresis(enter_stress_probability=0.75, exit_stress_probability=0.55,
                             confirmation_bars=2, smoothing_alpha=1.0)
    assert loop.update({"available": True, "stress_probability": 0.90})["stressed_latched"] is False
    assert loop.update({"available": True, "stress_probability": 0.90})["stressed_latched"] is True
    # One benign observation does not cause a rapid on/off switch.
    assert loop.update({"available": True, "stress_probability": 0.20})["stressed_latched"] is True
    released = loop.update({"available": True, "stress_probability": 0.20})
    assert released["stressed_latched"] is False
    assert 0.0 <= released["suggested_hysteresis_derate"] <= 1.0


def test_sizing_step_buffer_ignores_minor_posterior_changes():
    loop = HMMRiskHysteresis(confirmation_bars=3, smoothing_alpha=1.0, minimum_derate_step=0.15)
    first = loop.update({"available": True, "stress_probability": 0.40})
    # 0.40 stress -> 0.60 derate, a material first adjustment.
    assert first["suggested_hysteresis_derate"] == 0.60
    second = loop.update({"available": True, "stress_probability": 0.46})
    # 0.54 is only a 0.06 proposed change, below the step buffer.
    assert second["suggested_hysteresis_derate"] == 0.60
