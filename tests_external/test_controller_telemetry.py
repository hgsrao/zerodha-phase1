from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, IDDecision, PASignal
from revision2_external.continuous_exit_controller import ContinuousExitController
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox


def _config():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    return EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256)


def _signal():
    return PASignal(symbol="TEST", timestamp="2024-01-01 09:20", direction=1, confidence=0.8,
                    momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)


def _decision():
    return IDDecision(approved=True, reason="approved", confidence=0.8, risk_reward_ratio=2.0, timing_quality=0.6)


def test_mpc_telemetry_exposes_pid_terms_without_changing_plan_contract():
    box = SimplePIDModelPredictiveControlBox()
    plan, info, _ = box.build_plan(_signal(), _decision(), 1000.0, 8.0, _config())

    assert plan is not None
    assert info["entry_setpoint"] == info["exit_setpoint"]
    assert info["entry_measurement"] == 0.8
    assert info["entry_error"] == info["entry_setpoint"] - info["entry_measurement"]
    assert {"entry_p", "entry_i", "entry_d", "exit_p", "exit_i", "exit_d", "exit_tightness"} <= set(info)


def test_exit_controller_telemetry_captures_monotonic_stop_action():
    controller = ContinuousExitController(
        kp=0.12, ki=0.04, kd=0.06, clamp=0.1, atr_droop_mult=1.0, baseline_window=5,
    )
    state = controller.open_position("BUY", 100.0, 95.0, 110.0, 20)
    state = controller.update("TEST", state, 0.6, 0.6, 104.0, 2.0)

    telemetry = state.last_telemetry
    assert telemetry["stop_after"] >= telemetry["stop_before"]
    assert telemetry["bars_held"] == 1
    assert {"pa_setpoint", "pa_measurement", "studies_setpoint", "studies_measurement", "combined_tightness"} <= set(telemetry)
