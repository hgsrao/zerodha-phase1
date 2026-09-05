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
    #
    # Confidence must VARY bar to bar here, not stay constant: the entry
    # PID's setpoint is now a rolling mean of the symbol's own recent
    # confidence (see pid_controller.py's _entry_setpoint), so a constant
    # confidence lets that baseline converge to it and error go to ~0
    # regardless of Kp -- which would prove nothing about the gain at all.
    box_low = SimplePIDModelPredictiveControlBox()
    box_high = SimplePIDModelPredictiveControlBox()
    confidences = [0.55, 0.72, 0.51, 0.68, 0.60]

    plans_low, plans_high = [], []
    for confidence in confidences:
        signal = PASignal(symbol="TEST", timestamp="2024-01-01 09:20", direction=1, confidence=confidence,
                           momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)
        decision = IDDecision(approved=True, reason="approved", confidence=confidence, risk_reward_ratio=2.0, timing_quality=0.6)
        plan_low, _, _ = box_low.build_plan(signal, decision, 1000.0, 8.0, _config({"pid_kp_entry": 0.01}))
        plan_high, _, _ = box_high.build_plan(signal, decision, 1000.0, 8.0, _config({"pid_kp_entry": 2.0}))
        plans_low.append(plan_low.entry_price)
        plans_high.append(plan_high.entry_price)

    assert plans_low != plans_high, "pid_kp_entry has no effect on the real trade plan"


def test_bounded_output_never_exceeds_the_configured_clamp():
    box = SimplePIDModelPredictiveControlBox()
    config = _config({"pid_ki_entry": 5.0, "pid_integral_max_clamp": 0.4})
    # Alternating high/low confidence keeps real, nonzero error flowing on
    # every other bar even after the rolling baseline adapts -- constant
    # confidence would let the baseline converge and satisfy the clamp
    # check trivially, without ever stressing it.
    confidences = ([0.95, 0.35] * 25)[:50]
    for confidence in confidences:
        signal = PASignal(symbol="TEST", timestamp="2024-01-01 09:20", direction=1, confidence=confidence,
                           momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)
        decision = IDDecision(approved=True, reason="approved", confidence=confidence, risk_reward_ratio=2.0, timing_quality=0.6)
        plan, pid_info, _ = box.build_plan(signal, decision, 1000.0, 8.0, config)
        assert abs(pid_info["entry_adjustment"]) <= 0.4 + 1e-9


def test_entry_pid_no_longer_permanently_saturates_on_the_real_infy_pattern():
    # Reproduces the real, mathematically-guaranteed bug this fix addresses.
    # Under the OLD fixed target (0.5, identical to entry_confidence_
    # threshold's own default), every confidence IntelligentDiscrimination
    # ever admits is >= 0.5 by construction, so error = target - confidence
    # was <= 0 on every single call -- not usually small, but *guaranteed*
    # one-signed regardless of Kp/Ki/Kd. That pinned the integral at its
    # clamp permanently. Verified on real INFY data before this fix:
    # entry_adjustment = -0.09971 and -0.100 for confidences 0.5130 and
    # 0.5709 (2023-07-13 trade, both approved "amber" signals).
    box = SimplePIDModelPredictiveControlBox()
    config = _config()
    clamp = config.require("pid_integral_max_clamp")
    confidences = [0.513, 0.571, 0.52, 0.56, 0.55, 0.54, 0.57, 0.53, 0.55, 0.56] * 3

    adjustments = []
    for confidence in confidences:
        signal = PASignal(symbol="INFY_LIKE", timestamp="2024-01-01 09:20", direction=1, confidence=confidence,
                           momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)
        decision = IDDecision(approved=True, reason="approved", confidence=confidence, risk_reward_ratio=2.0, timing_quality=0.6)
        plan, pid_info, _ = box.build_plan(signal, decision, 1000.0, 8.0, config)
        adjustments.append(pid_info["entry_adjustment"])

    later_calls = adjustments[-10:]
    assert any(abs(a) < clamp - 1e-6 for a in later_calls), (
        f"entry PID is still permanently pinned at its clamp: {later_calls}"
    )
    assert len(set(round(a, 6) for a in later_calls)) > 1, (
        f"entry PID output is flat, not genuine proportional control: {later_calls}"
    )


def test_exit_pid_also_stops_saturating_once_it_shares_the_adaptive_baseline():
    # The exit PID's target used to be decision.timing_quality (effectively
    # fixed at exit_confidence_threshold) -- not mathematically guaranteed
    # one-signed the way entry's was, but empirically worse in practice: a
    # real 6-month INFY run (after fixing entry alone) showed the exit PID
    # pinned at its clamp on 14.6% of calls (68/467) vs only 5.4% (25/467)
    # for the already-fixed entry PID. Moving exit onto the same rolling
    # confidence baseline as entry should show the same kind of relief.
    box = SimplePIDModelPredictiveControlBox()
    config = _config()
    clamp = config.require("pid_integral_max_clamp")
    confidences = [0.513, 0.571, 0.52, 0.56, 0.55, 0.54, 0.57, 0.53, 0.55, 0.56] * 3

    adjustments = []
    for confidence in confidences:
        signal = PASignal(symbol="INFY_LIKE_EXIT", timestamp="2024-01-01 09:20", direction=1, confidence=confidence,
                           momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)
        decision = IDDecision(approved=True, reason="approved", confidence=confidence, risk_reward_ratio=2.0, timing_quality=0.6)
        plan, pid_info, _ = box.build_plan(signal, decision, 1000.0, 8.0, config)
        adjustments.append(pid_info["exit_adjustment"])

    later_calls = adjustments[-10:]
    assert any(abs(a) < clamp - 1e-6 for a in later_calls), (
        f"exit PID is still permanently pinned at its clamp: {later_calls}"
    )
    assert len(set(round(a, 6) for a in later_calls)) > 1, (
        f"exit PID output is flat, not genuine proportional control: {later_calls}"
    )


def test_entry_and_exit_pids_share_one_confidence_baseline_not_two():
    # _confidence_baseline must be called exactly once per build_plan() call
    # and its result reused for both PIDs -- calling it twice would double
    # count the same bar in the rolling window and desync the two PIDs'
    # setpoints even though they observe the identical confidence series.
    box = SimplePIDModelPredictiveControlBox()
    signal = PASignal(symbol="SHARED_BASELINE", timestamp="2024-01-01 09:20", direction=1, confidence=0.6,
                       momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)
    decision = IDDecision(approved=True, reason="approved", confidence=0.6, risk_reward_ratio=2.0, timing_quality=0.6)
    box.build_plan(signal, decision, 1000.0, 8.0, _config())
    assert len(box._confidence_history["SHARED_BASELINE"]) == 1, (
        "one build_plan() call must append exactly one confidence reading, "
        f"got {len(box._confidence_history['SHARED_BASELINE'])}"
    )
    assert box._entry_pids["SHARED_BASELINE"].setpoint == box._exit_pids["SHARED_BASELINE"].setpoint


def test_fresh_symbol_starts_neutral_instead_of_pre_biased_toward_a_rail():
    # With no confidence history yet, the rolling baseline defaults to the
    # very first reading itself, so error (and therefore the adjustment) on
    # that very first call is exactly zero -- the controller starts neutral
    # rather than already leaning on a fixed, possibly-mismatched target.
    box = SimplePIDModelPredictiveControlBox()
    signal = PASignal(symbol="FRESH", timestamp="2024-01-01 09:20", direction=1, confidence=0.52,
                       momentum=0.5, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2)
    decision = IDDecision(approved=True, reason="approved", confidence=0.52, risk_reward_ratio=2.0, timing_quality=0.6)
    plan, pid_info, _ = box.build_plan(signal, decision, 1000.0, 8.0, _config())
    assert abs(pid_info["entry_adjustment"]) < 1e-9, (
        f"expected ~0 on the first-ever call for a fresh symbol, got {pid_info['entry_adjustment']}"
    )
