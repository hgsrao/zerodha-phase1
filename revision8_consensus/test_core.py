import pytest

from .core import Evidence, R8Controller, Vote


def test_buy_requires_all_four_evidence_sources():
    c = R8Controller()
    d = c.decide(Evidence(.8, .7, .6, .9))
    assert d.direction is Vote.BUY
    assert d.reason == "FOUR_WAY_BUY_CONSENSUS"


def test_one_conflicting_source_produces_hold():
    d = R8Controller().decide(Evidence(.8, .7, -.2, .9))
    assert d.direction is Vote.HOLD
    assert d.reason == "NO_DIRECTIONAL_CONSENSUS"


def test_long_exit_requires_governor_and_studies_support():
    d = R8Controller().decide(Evidence(-.8, .1, .1, -.8, actual_exposure=1.0))
    assert d.direction is Vote.SELL
    assert d.action_intent == "REDUCE_OR_EXIT_LONG"
    assert d.execution_target == 0.0


def test_hold_oversized_exposure_is_bounded_for_execution_but_visible():
    e = Evidence(.01, .01, .01, .01, actual_exposure=3.0)
    d = R8Controller().decide(e)
    assert d.direction is Vote.HOLD
    assert d.evidence.actual_exposure == 3.0
    assert d.execution_target == 1.0


def test_execution_ramp_is_separate_from_direction():
    c = R8Controller(slew_per_second=.01)
    d = c.decide(Evidence(.8, .8, .8, .8))
    assert d.direction is Vote.BUY
    assert c.ramp(d.execution_target, d.evidence.actual_exposure, 60) == pytest.approx(.6)


def test_ineligible_input_holds():
    d = R8Controller().decide(Evidence(.9, .9, .9, .9, eligible=False))
    assert d.direction is Vote.HOLD
    assert d.reason == "INELIGIBLE_INPUT"
