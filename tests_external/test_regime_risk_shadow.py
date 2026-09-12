from revision2_external.closed_loop_control import regime_risk_derate


def test_hmm_posterior_recommendation_only_derates_risk():
    result = regime_risk_derate({"available": True, "stress_probability": 0.72})
    assert result["suggested_regime_derate"] == 0.28
    assert 0.0 <= result["suggested_regime_derate"] <= 1.0


def test_missing_regime_never_invents_a_risk_signal():
    result = regime_risk_derate({"available": False, "reason": "NO_DISTINCT_STRESSED_STATE"})
    assert result["suggested_regime_derate"] == 1.0
    assert result["available"] is False
