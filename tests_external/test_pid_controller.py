import sys
sys.path.insert(0, ".")

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, IDDecision, PASignal
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox


def _config(overrides=None):
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    values.update(overrides or {})
    return EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256)


def _signal():
    return PASignal(symbol="TEST", timestamp="2024-01-01 09:20", direction=1, confidence=0.8,
                     momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)


def _decision():
    return IDDecision(approved=True, reason="approved", confidence=0.8, risk_reward_ratio=2.0, timing_quality=0.6)


def test_plan_is_produced_for_a_real_approved_decision():
    box = SimplePIDModelPredictiveControlBox()
    plan, pid_info, trace = box.build_plan(_signal(), _decision(), entry_price=1000.0, atr=8.0, config=_config())
    assert plan is not None
    assert plan.side == "BUY"
    assert plan.stop_price < plan.entry_price < plan.target_price
    assert len(trace) > 0


def test_pid_gains_causally_move_the_plan_not_just_a_diagnostic():
    # The exact bug class this project already found and fixed once for
    # BoundedPID -- proving the swap didn't quietly reintroduce it.
    box_low = SimplePIDModelPredictiveControlBox()
    box_high = SimplePIDModelPredictiveControlBox()
    signal, decision = _signal(), _decision()

    plans_low, plans_high = [], []
    for _ in range(5):
        plan_low, _, _ = box_low.build_plan(signal, decision, 1000.0, 8.0, _config({"pid_kp_entry": 0.01}))
        plan_high, _, _ = box_high.build_plan(signal, decision, 1000.0, 8.0, _config({"pid_kp_entry": 2.0}))
        plans_low.append(plan_low.entry_price)
        plans_high.append(plan_high.entry_price)

    assert plans_low != plans_high, "pid_kp_entry has no effect on the real trade plan"


def test_bounded_output_never_exceeds_the_configured_clamp():
    box = SimplePIDModelPredictiveControlBox()
    signal, decision = _signal(), _decision()
    config = _config({"pid_ki_entry": 5.0, "pid_integral_max_clamp": 0.4})
    for _ in range(50):
        plan, pid_info, _ = box.build_plan(signal, decision, 1000.0, 8.0, config)
        assert abs(pid_info["entry_adjustment"]) <= 0.4 + 1e-9
