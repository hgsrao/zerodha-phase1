from revision5.bay_feasibility import (
    BayFeasibilityChecker,
    BayFeasibilityInput,
)
from revision5.machine_archetypes import (
    MACHINE_ARCHETYPES,
)


def checker():
    return BayFeasibilityChecker()


def envelope(bay):
    return MACHINE_ARCHETYPES[
        bay
    ].operating


def state(
    previous,
    requested,
    *,
    online=10,
    offline=10,
    breaker=True,
    available=True,
):
    return BayFeasibilityInput(
        previous_load_fraction=previous,
        requested_load_fraction=requested,
        elapsed_online_bars=online,
        elapsed_offline_bars=offline,
        generator_breaker_closed=breaker,
        machine_available=available,
    )


def test_normal_gtg_operating_point_is_feasible():
    result = checker().evaluate(
        envelope=envelope(
            "GTG1_HEAVY_INDUSTRY"
        ),
        state=state(
            0.30,
            0.40,
        ),
    )

    assert result.feasible
    assert result.violations == ()


def test_reserve_headroom_is_preserved():
    result = checker().evaluate(
        envelope=envelope(
            "GTG1_HEAVY_INDUSTRY"
        ),
        state=state(
            0.80,
            0.95,
        ),
    )

    assert not result.feasible
    assert (
        "CAPACITY_OR_RESERVE_LIMIT"
        in result.violations
    )


def test_ramp_up_is_machine_class_specific():
    gtg = checker().evaluate(
        envelope=envelope(
            "GTG1_HEAVY_INDUSTRY"
        ),
        state=state(
            0.20,
            0.40,
        ),
    )

    bp = checker().evaluate(
        envelope=envelope(
            "BPSTG_HEALTHCARE"
        ),
        state=state(
            0.20,
            0.40,
        ),
    )

    assert gtg.feasible
    assert not bp.feasible
    assert "RAMP_UP_LIMIT" in bp.violations


def test_minimum_offline_time_blocks_restart():
    result = checker().evaluate(
        envelope=envelope(
            "BPSTG_HEALTHCARE"
        ),
        state=state(
            0.0,
            0.05,
            offline=1,
        ),
    )

    assert not result.feasible

    assert (
        "MINIMUM_OFFLINE_TIME"
        in result.violations
    )


def test_breaker_open_blocks_nonzero_load():
    result = checker().evaluate(
        envelope=envelope(
            "GTG1_HEAVY_INDUSTRY"
        ),
        state=state(
            0.20,
            0.30,
            breaker=False,
        ),
    )

    assert not result.feasible

    assert (
        "GENERATOR_BREAKER_OPEN"
        in result.violations
    )


def test_machine_unavailable_blocks_nonzero_load():
    result = checker().evaluate(
        envelope=envelope(
            "GTG1_HEAVY_INDUSTRY"
        ),
        state=state(
            0.20,
            0.30,
            available=False,
        ),
    )

    assert not result.feasible

    assert (
        "MACHINE_UNAVAILABLE"
        in result.violations
    )
