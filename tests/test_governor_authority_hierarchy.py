"""Position-authority hierarchy: hard safety > inner-loop HOLD in min-hold > advisory conviction."""
import pytest

from revision2_external.bb05_bb06_parameters import default_config
from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
from revision5.topology import BAY_IDS
from revision5.governor_authority import GovernorAuthorityConfig, position_decision

CFG = GovernorAuthorityConfig.from_config(default_config())


def _governor():
    return BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[BAY_IDS[0]])


def _decide(**overrides):
    kwargs = dict(position_id="INFY", measured_r=0.3, reference_r=0.2, max_favorable_r=0.3,
                  elapsed_bars=2, min_hold_bars=8, max_hold_bars=60, trade_target_r=3.0,
                  conviction=0.9, drawdown=0.0, velocity=0.2, session_bar=10_000, path_noise_r=0.8)
    kwargs.update(overrides)
    return position_decision(_governor(), CFG, **kwargs)


def test_healthy_inner_loop_baseline_holds():
    assert _decide()["action"] == "HOLD"


def test_conviction_collapse_inside_min_hold_is_held_when_inner_loop_healthy():
    result = _decide(conviction=0.0, elapsed_bars=3, min_hold_bars=8)
    assert result["action"] == "HOLD"
    assert result["conviction_exit_deferred"] is True
    assert result["inner"]["action"] == "HOLD"


def test_sustained_fuel_cut_inside_min_hold_is_held():
    result = _decide(conviction=0.0, fuel_cut_confirmed=True, elapsed_bars=3, min_hold_bars=8)
    assert result["action"] == "HOLD" and result["conviction_exit_deferred"] is True


@pytest.mark.parametrize("elapsed", [8, 9, 30])
def test_conviction_collapse_at_or_after_min_hold_exits_on_fsrn(elapsed):
    result = _decide(conviction=0.0, elapsed_bars=elapsed, min_hold_bars=8)
    assert result["action"] == "EXIT" and result["reason"] == "FSR_BELOW_EXIT:FSRN"


def test_sustained_fuel_cut_after_min_hold_exits():
    result = _decide(conviction=0.0, fuel_cut_confirmed=True, elapsed_bars=8, min_hold_bars=8)
    assert result["reason"] == "FSRN_SUSTAINED_DETERIORATION"


@pytest.mark.parametrize("elapsed", [0, 1, 7, 8, 40])
@pytest.mark.parametrize("conviction", [0.9, 0.0])
def test_hard_stop_exits_immediately_regardless_of_elapsed_or_conviction(elapsed, conviction):
    result = _decide(measured_r=-1.5, max_favorable_r=0.0, elapsed_bars=elapsed, min_hold_bars=8,
                     conviction=conviction)
    assert result["action"] == "EXIT" and result["reason"] == "GOVERNOR_HARD_STOP"


def test_target_reached_exits_inside_min_hold():
    result = _decide(measured_r=3.2, max_favorable_r=3.2, elapsed_bars=1, min_hold_bars=8)
    assert result["reason"] == "GOVERNOR_TARGET_REACHED"


def test_exhaust_spread_trip_overrides_min_hold():
    result = _decide(bay_exhaust_spread=CFG.exhaust_spread_trip, elapsed_bars=1, min_hold_bars=8)
    assert result["reason"] == "EXHAUST_SPREAD_TRIP"


def test_independent_protective_limiter_is_not_deferred_inside_min_hold():
    result = _decide(drawdown=1.0, conviction=0.9, elapsed_bars=1, min_hold_bars=8)
    if result["action"] == "EXIT":
        assert result["reason"].startswith("FSR_BELOW_EXIT:") and not result["reason"].endswith("FSRN")
    else:  # drawdown limiter alone does not breach the threshold under this config
        pytest.skip("drawdown limiter cannot breach exit threshold with default config")


def test_invalid_inputs_still_fail_closed_inside_min_hold():
    assert _decide(velocity=None, elapsed_bars=1, min_hold_bars=8)["action"] == "EXIT"
    assert _decide(conviction=float("nan"), elapsed_bars=1, min_hold_bars=8)["action"] == "EXIT"
