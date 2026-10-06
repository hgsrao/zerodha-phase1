"""Fleet loading controller: units, anti-windup, protective trip, disabled behaviour, persistence."""
import dataclasses
import inspect
import json
import math

import pytest

from revision5.fleet_loading_controller import (
    FleetLoadingController, FleetLoadingPolicy, FleetLoadingTelemetry, new_risk_headroom_pu)

POLICY = FleetLoadingPolicy(enabled=True, kp=0.5, ki=0.1, kd=0.0, integral_limit_pu_seconds=5.0, max_capacity_pu=1.0)
KD_POLICY = FleetLoadingPolicy(enabled=True, kp=0.5, ki=0.1, kd=1.0, integral_limit_pu_seconds=5.0, max_capacity_pu=1.0)


def ctl(policy=POLICY):
    return FleetLoadingController(policy)


# --------------------------------------------------------------------------- policy
def test_default_policy_is_disabled_and_valid():
    p = FleetLoadingPolicy()
    assert p.enabled is False
    assert p.kp >= 0 and p.ki >= 0 and p.kd >= 0 and p.integral_limit_pu_seconds > 0 and p.max_capacity_pu > 0


def test_policy_is_immutable_and_telemetry_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        POLICY.kp = 9.0
    t = ctl().update(0.4, 0.1, 1.0, 1.0)
    assert isinstance(t, FleetLoadingTelemetry)
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.allowed_capacity_pu = 9.0


@pytest.mark.parametrize("kwargs", [
    {"kp": -0.1}, {"ki": -1.0}, {"kd": -1.0}, {"kp": math.nan}, {"ki": math.inf}, {"kd": -math.inf},
    {"integral_limit_pu_seconds": 0.0}, {"integral_limit_pu_seconds": math.nan},
    {"max_capacity_pu": 0.0}, {"max_capacity_pu": -1.0}, {"max_capacity_pu": math.inf},
    {"enabled": 1}, {"enabled": "yes"}, {"kp": True}, {"kp": "0.5"},
])
def test_policy_validation_rejects(kwargs):
    with pytest.raises(ValueError):
        FleetLoadingPolicy(**kwargs)


def test_policy_identity_changes_with_any_field():
    base = POLICY.identity
    assert base == dataclasses.replace(POLICY).identity
    for change in ({"enabled": False}, {"kp": 0.6}, {"ki": 0.2}, {"kd": 0.1},
                   {"integral_limit_pu_seconds": 6.0}, {"max_capacity_pu": 2.0}):
        assert dataclasses.replace(POLICY, **change).identity != base


def test_no_buy_sell_preference_in_interface():
    params = set(inspect.signature(FleetLoadingController.update).parameters)
    assert params == {"self", "reference_pu", "actual_exposure_pu", "dt_seconds", "capacity_pu", "protection_tripped"}
    assert not {f.name for f in dataclasses.fields(FleetLoadingTelemetry)} & {"side", "direction", "buy", "sell"}


# --------------------------------------------------------------------------- under / over loading
def test_underloaded_fleet_raises_allowed_capacity_above_reference():
    t = ctl().update(0.4, 0.1, 1.0, 1.0)
    assert t.error_pu == pytest.approx(0.3)
    assert t.p == pytest.approx(0.15)
    assert t.integral_pu_seconds == pytest.approx(0.3)
    assert t.i == pytest.approx(0.03)
    assert t.d == 0.0 and t.derivative_pu_per_second == 0.0
    assert t.raw_output_pu == pytest.approx(0.58)
    assert t.allowed_capacity_pu == pytest.approx(0.58)
    assert t.allowed_capacity_pu > 0.4
    assert not t.saturated and not t.protection_tripped


def test_overloaded_fleet_lowers_allowed_capacity_below_reference():
    t = ctl().update(0.4, 0.7, 1.0, 1.0)
    assert t.error_pu == pytest.approx(-0.3)
    assert t.integral_pu_seconds == pytest.approx(-0.3)
    assert t.raw_output_pu == pytest.approx(0.4 - 0.15 - 0.03)
    assert t.allowed_capacity_pu == pytest.approx(0.22)
    assert t.allowed_capacity_pu < 0.4
    assert not t.saturated


def test_zero_error_outputs_reference_exactly():
    t = ctl().update(0.5, 0.5, 1.0, 1.0)
    assert t.raw_output_pu == pytest.approx(0.5)
    assert t.allowed_capacity_pu == pytest.approx(0.5)


def test_output_is_total_capacity_not_headroom():
    t = ctl().update(0.5, 0.5, 1.0, 1.0)
    assert t.allowed_capacity_pu == pytest.approx(0.5)          # controller does not subtract actual
    assert new_risk_headroom_pu(t.allowed_capacity_pu, 0.5) == 0.0
    t2 = ctl().update(0.4, 0.1, 1.0, 1.0)
    assert new_risk_headroom_pu(t2.allowed_capacity_pu, 0.1) == pytest.approx(0.48)
    assert t2.allowed_capacity_pu != pytest.approx(new_risk_headroom_pu(t2.allowed_capacity_pu, 0.1))
    assert new_risk_headroom_pu(0.3, 0.9) == 0.0                # never negative
    with pytest.raises(ValueError):
        new_risk_headroom_pu(math.nan, 0.1)
    with pytest.raises(ValueError):
        new_risk_headroom_pu(0.3, -0.1)


# --------------------------------------------------------------------------- capacity clamp
def test_capacity_is_min_of_configured_max_and_plant_capacity():
    assert ctl().update(0.9, 0.0, 1.0, 5.0).allowed_capacity_pu == pytest.approx(1.0)   # config max binds
    assert ctl().update(0.9, 0.0, 1.0, 0.3).allowed_capacity_pu == pytest.approx(0.3)   # plant binds
    t = ctl().update(0.9, 0.0, 1.0, 0.0)
    assert t.allowed_capacity_pu == 0.0 and t.saturated


# --------------------------------------------------------------------------- dt scaling
def test_integral_scales_with_dt_seconds():
    a = ctl().update(0.4, 0.1, 1.0, 1.0)
    b = ctl().update(0.4, 0.1, 2.0, 1.0)
    assert b.integral_pu_seconds == pytest.approx(2.0 * a.integral_pu_seconds)
    assert b.i == pytest.approx(2.0 * a.i)


def test_constant_error_integral_independent_of_step_split():
    one = ctl()
    t1 = one.update(0.4, 0.1, 2.0, 1.0)
    many = ctl()
    for _ in range(4):
        t4 = many.update(0.4, 0.1, 0.5, 1.0)
    assert t4.integral_pu_seconds == pytest.approx(t1.integral_pu_seconds)


def test_derivative_is_per_second():
    c = ctl(KD_POLICY)
    first = c.update(0.5, 0.3, 1.0, 1.0)                        # e = 0.2, no previous sample
    assert first.derivative_pu_per_second == 0.0 and first.d == 0.0
    second = c.update(0.5, 0.4, 0.5, 1.0)                       # e = 0.1 over 0.5 s
    assert second.derivative_pu_per_second == pytest.approx(-0.2)
    assert second.d == pytest.approx(-0.2)
    c2 = ctl(KD_POLICY)
    c2.update(0.5, 0.3, 1.0, 1.0)
    assert c2.update(0.5, 0.4, 2.0, 1.0).derivative_pu_per_second == pytest.approx(-0.05)


# --------------------------------------------------------------------------- saturation / recovery
def test_upper_saturation_does_not_wind_and_recovers_immediately():
    c = ctl()
    for _ in range(3):
        c.update(0.5, 0.4, 1.0, 1.0)
    held = c.update(0.5, 0.4, 1.0, 1.0).integral_pu_seconds    # accumulated before saturation
    for _ in range(200):
        t = c.update(0.9, 0.0, 1.0, 1.0)                        # pre-clamp output well above cap
        assert t.saturated and t.allowed_capacity_pu == pytest.approx(1.0)
        assert t.raw_output_pu > 1.0
    assert t.integral_pu_seconds == pytest.approx(held)         # frozen, not wound
    rec = c.update(0.9, 0.9, 1.0, 1.0)                          # error vanishes
    assert not rec.saturated
    assert rec.allowed_capacity_pu == pytest.approx(0.9 + 0.1 * held)
    rev = c.update(0.9, 1.2, 1.0, 1.0)                          # reversal: overloaded
    assert rev.allowed_capacity_pu < 0.9 and rev.integral_pu_seconds < held


def test_lower_saturation_does_not_wind_and_recovers_immediately():
    c = ctl()
    for _ in range(3):
        c.update(0.5, 0.4, 1.0, 1.0)
    held = c.update(0.5, 0.4, 1.0, 1.0).integral_pu_seconds
    for _ in range(200):
        t = c.update(0.1, 2.0, 1.0, 1.0)                        # raw output < 0
        assert t.saturated and t.allowed_capacity_pu == 0.0 and t.raw_output_pu < 0.0
    assert t.integral_pu_seconds == pytest.approx(held)
    rec = c.update(0.1, 0.1, 1.0, 1.0)
    assert not rec.saturated
    assert rec.allowed_capacity_pu == pytest.approx(0.1 + 0.1 * held)
    rev = c.update(0.5, 0.0, 1.0, 1.0)                          # reversal: underloaded
    assert rev.allowed_capacity_pu > 0.5 and rev.integral_pu_seconds > held


def test_plant_capacity_saturation_holds_integrator():
    c = ctl()
    for _ in range(50):
        t = c.update(0.5, 0.1, 1.0, 0.3)                        # plant limits to 0.3, error stays positive
    assert t.saturated and t.allowed_capacity_pu == pytest.approx(0.3)
    assert t.integral_pu_seconds == 0.0
    assert not c.update(0.5, 0.5, 1.0, 1.0).saturated


def test_integral_limit_bounds_unsaturated_integration():
    p = dataclasses.replace(POLICY, ki=0.001, kp=0.0, integral_limit_pu_seconds=2.0)
    c = ctl(p)
    for _ in range(100):
        t = c.update(0.5, 0.1, 1.0, 1.0)
    assert t.integral_pu_seconds == pytest.approx(2.0)
    for _ in range(100):
        t = c.update(0.1, 0.9, 1.0, 1.0)
    assert t.integral_pu_seconds == pytest.approx(-2.0)


# --------------------------------------------------------------------------- protective trip
def test_trip_zeroes_capacity_and_freezes_integrator():
    c = ctl()
    before = c.update(0.4, 0.1, 1.0, 1.0)
    for _ in range(100):
        t = c.update(0.9, 0.0, 1.0, 1.0, protection_tripped=True)
        assert t.protection_tripped and t.allowed_capacity_pu == 0.0
        assert t.integral_pu_seconds == pytest.approx(before.integral_pu_seconds)
    after = c.update(0.4, 0.1, 1.0, 1.0)
    assert not after.protection_tripped
    assert after.integral_pu_seconds == pytest.approx(2 * before.integral_pu_seconds)  # no wind during trip


def test_trip_is_immediate_even_with_full_capacity_and_zero_error():
    t = ctl().update(0.5, 0.5, 1.0, 1.0, protection_tripped=True)
    assert t.allowed_capacity_pu == 0.0 and t.protection_tripped


def test_fault_recovery_resumes_without_derivative_kick():
    c = ctl(KD_POLICY)
    c.update(0.5, 0.45, 1.0, 1.0)
    c.update(0.5, 0.0, 1.0, 1.0, protection_tripped=True)
    t = c.update(0.5, 0.0, 1.0, 1.0)
    assert t.derivative_pu_per_second == 0.0 and t.d == 0.0
    assert t.allowed_capacity_pu > 0.0


def test_tripped_state_survives_serialization_without_integral_change():
    c = ctl()
    c.update(0.4, 0.1, 1.0, 1.0)
    c.update(0.4, 0.1, 1.0, 1.0, protection_tripped=True)
    d = ctl()
    d.restore_state(c.export_state())
    assert json.loads(d.export_state())["integral_pu_seconds"] == pytest.approx(0.3)


# --------------------------------------------------------------------------- disabled
def test_disabled_returns_supplied_capacity_unchanged():
    c = ctl(FleetLoadingPolicy())          # enabled=False
    for cap in (0.0, 0.25, 1.0, 3.7):      # 3.7 > default max_capacity_pu: no hidden clamp
        t = c.update(0.9, 0.0, 1.0, cap)
        assert t.allowed_capacity_pu == cap and t.raw_output_pu == cap
        assert t.p == t.i == t.d == 0.0 and t.integral_pu_seconds == 0.0
        assert not t.saturated and not t.protection_tripped
    assert json.loads(c.export_state())["integral_pu_seconds"] == 0.0


def test_disabled_still_validates_inputs():
    c = ctl(FleetLoadingPolicy())
    for bad in (math.nan, math.inf, -0.1):
        with pytest.raises(ValueError):
            c.update(0.5, 0.2, 1.0, bad)


def test_disabled_protection_trip_still_zeroes_capacity():
    t = ctl(FleetLoadingPolicy()).update(0.5, 0.2, 1.0, 1.0, protection_tripped=True)
    assert t.allowed_capacity_pu == 0.0 and t.protection_tripped


# --------------------------------------------------------------------------- invalid input
@pytest.mark.parametrize("args", [
    (math.nan, 0.1, 1.0, 1.0), (math.inf, 0.1, 1.0, 1.0), (-0.1, 0.1, 1.0, 1.0),
    (0.4, math.nan, 1.0, 1.0), (0.4, math.inf, 1.0, 1.0), (0.4, -0.1, 1.0, 1.0),
    (0.4, 0.1, 0.0, 1.0), (0.4, 0.1, -1.0, 1.0), (0.4, 0.1, math.nan, 1.0), (0.4, 0.1, math.inf, 1.0),
    (0.4, 0.1, 1.0, math.nan), (0.4, 0.1, 1.0, math.inf), (0.4, 0.1, 1.0, -0.5),
    (None, 0.1, 1.0, 1.0), ("0.4", 0.1, 1.0, 1.0), (True, 0.1, 1.0, 1.0),
])
def test_invalid_inputs_rejected_without_state_change(args):
    c = ctl()
    c.update(0.4, 0.1, 1.0, 1.0)
    snapshot = c.export_state()
    with pytest.raises(ValueError):
        c.update(*args)
    assert c.export_state() == snapshot


def test_protection_flag_must_be_bool():
    with pytest.raises(ValueError):
        ctl().update(0.4, 0.1, 1.0, 1.0, protection_tripped=1)


def test_invalid_input_rejected_even_when_tripped():
    with pytest.raises(ValueError):
        ctl().update(0.4, 0.1, math.nan, 1.0, protection_tripped=True)


def test_controller_requires_policy():
    with pytest.raises(TypeError):
        FleetLoadingController({"enabled": True})


# --------------------------------------------------------------------------- serialization
def test_export_restore_continuity():
    inputs = [(0.5, 0.2, 1.0, 1.0), (0.5, 0.3, 0.5, 1.0), (0.9, 0.0, 2.0, 1.0), (0.4, 0.8, 1.0, 1.0)]
    a = ctl(KD_POLICY)
    for x in inputs:
        a.update(*x)
    payload = a.export_state()
    json.loads(payload)                                         # valid JSON
    b = ctl(KD_POLICY)
    b.restore_state(payload)
    assert b.export_state() == payload
    for x in [(0.5, 0.25, 1.0, 1.0), (0.6, 0.2, 0.7, 1.0), (0.6, 0.2, 0.7, 1.0)]:
        assert a.update(*x) == b.update(*x)


def test_export_contains_policy_identity_and_all_state():
    c = ctl(KD_POLICY)
    c.update(0.5, 0.2, 1.0, 1.0)
    data = json.loads(c.export_state())
    assert data["policy"] == KD_POLICY.to_dict()
    assert data["policy_identity"] == KD_POLICY.identity
    assert data["integral_pu_seconds"] == pytest.approx(0.3)
    assert data["prev_error_pu"] == pytest.approx(0.3)
    assert data["updates"] == 1


def _state(**overrides):
    c = ctl()
    c.update(0.4, 0.1, 1.0, 1.0)
    data = json.loads(c.export_state())
    data.update(overrides)
    return data


def test_restore_rejects_policy_mismatch():
    other = dataclasses.replace(POLICY, kp=0.6)
    payload = ctl(other).export_state()
    with pytest.raises(ValueError):
        ctl().restore_state(payload)
    with pytest.raises(ValueError):
        ctl(dataclasses.replace(POLICY, enabled=False)).restore_state(ctl().export_state())


@pytest.mark.parametrize("overrides", [
    {"schema": "other/1"}, {"policy_identity": "0" * 64}, {"integral_pu_seconds": 5.5},
    {"integral_pu_seconds": "0.3"}, {"integral_pu_seconds": None}, {"prev_error_pu": "x"},
    {"updates": -1}, {"updates": 1.5}, {"updates": True},
    {"policy": {"enabled": True}}, {"policy": {**POLICY.to_dict(), "extra": 1}},
])
def test_restore_rejects_tampered_state(overrides):
    c = ctl()
    snapshot = c.export_state()
    with pytest.raises(ValueError):
        c.restore_state(json.dumps(_state(**overrides)))
    assert c.export_state() == snapshot                         # failed restore leaves state untouched


def test_restore_rejects_nonfinite_malformed_and_wrong_shape():
    c = ctl()
    snapshot = c.export_state()
    nan_payload = json.dumps(_state(integral_pu_seconds=math.nan))          # emits bare NaN token
    inf_payload = json.dumps(_state(prev_error_pu=math.inf))
    extra = _state()
    extra["unexpected"] = 1
    missing = _state()
    del missing["updates"]
    for payload in (nan_payload, inf_payload, "{not json", "[]", "null", json.dumps(extra), json.dumps(missing), None, b"{}"):
        with pytest.raises(ValueError):
            c.restore_state(payload)
    assert c.export_state() == snapshot


def test_restore_accepts_null_prev_error():
    c = ctl()
    c.restore_state(json.dumps(_state(prev_error_pu=None)))
    assert json.loads(c.export_state())["prev_error_pu"] is None
