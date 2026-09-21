import pytest

from revision5.relay_coordination import (
    IECInverseCurve,
    iec_operate_time_seconds,
    IDMTLowStage,
    HighSetStage,
    DualStageIDMTRelay,
    DifferentialSetting,
    GeneratorDifferentialRelay,
    DistanceZone,
    DistanceProtectionRelay,
    Breaker86Lockout,
)


def test_iec_inverse_time_shortens_as_current_rises():
    t2 = iec_operate_time_seconds(
        multiple=2.0,
        tms=0.10,
        curve=IECInverseCurve.VERY_INVERSE,
    )

    t5 = iec_operate_time_seconds(
        multiple=5.0,
        tms=0.10,
        curve=IECInverseCurve.VERY_INVERSE,
    )

    assert t5 < t2


def test_phase_oc_low_set_and_high_set():
    relay = DualStageIDMTRelay(
        element="ANSI_50_51",
        low=IDMTLowStage(
            pickup_multiple=1.20,
            tms=0.10,
            curve=(
                IECInverseCurve.VERY_INVERSE
            ),
        ),
        high=HighSetStage(
            pickup_multiple=5.0,
            delay_seconds=0.10,
        ),
    )

    normal = relay.evaluate(
        measured_multiple=1.0,
        dt_seconds=1.0,
    )

    assert normal.tripped is False

    high = relay.evaluate(
        measured_multiple=6.0,
        dt_seconds=0.10,
    )

    assert high.tripped is True
    assert high.stage == "HIGH_SET"


def test_differential_uses_bias_not_idmt():
    relay = GeneratorDifferentialRelay(
        DifferentialSetting(
            minimum_pickup_pu=0.20,
            slope=0.30,
            high_set_pu=2.0,
        )
    )

    healthy = relay.evaluate(
        differential_current_pu=0.10,
        restraint_current_pu=1.0,
    )

    assert healthy.tripped is False

    trip = relay.evaluate(
        differential_current_pu=0.40,
        restraint_current_pu=1.0,
    )

    assert trip.tripped is True
    assert (
        trip.stage
        == "PERCENTAGE_DIFFERENTIAL"
    )


def test_distance_has_independent_zone_timers():
    relay = DistanceProtectionRelay(
        (
            DistanceZone(
                "ZONE_1",
                0.80,
                0.0,
            ),
            DistanceZone(
                "ZONE_2",
                1.20,
                0.35,
            ),
            DistanceZone(
                "ZONE_3",
                2.00,
                0.80,
            ),
        )
    )

    z1 = relay.evaluate(
        apparent_impedance_ratio=0.50,
        dt_seconds=0.001,
    )

    assert z1.tripped is True
    assert z1.stage == "ZONE_1"

    relay.reset()

    first = relay.evaluate(
        apparent_impedance_ratio=1.0,
        dt_seconds=0.20,
    )

    assert first.tripped is False

    second = relay.evaluate(
        apparent_impedance_ratio=1.0,
        dt_seconds=0.20,
    )

    assert second.tripped is True
    assert second.stage == "ZONE_2"


def test_86_lockout_requires_explicit_reset():
    lockout = Breaker86Lockout()

    assert lockout.breaker_open is False

    lockout.trip(
        "ANSI_87G differential"
    )

    assert lockout.breaker_open is True

    # Nothing automatically clears it.
    assert lockout.breaker_open is True

    lockout.reset()

    assert lockout.breaker_open is False
