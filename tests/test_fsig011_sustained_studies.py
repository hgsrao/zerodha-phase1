"""Active advisory surface must describe actual positive saturation persistence."""
from revision2_external.continuous_exit_controller import ContinuousExitController
from revision2_external.final_execution_controller import FinalExecutionController
import pytest


def recommendation(telemetry):
    # Ignore stop ratchet solely when isolating the confidence recommendation.
    telemetry=dict(telemetry,stop_before=900,stop_after=900)
    return FinalExecutionController().exit_decision(path=None,exit_pid=telemetry,
        held_bars=10,minimum_hold_bars=3,maximum_hold_bars=100)


@pytest.mark.parametrize('baseline,measurement,expected',[(.9,.1,'EXIT'),(.1,.9,'HOLD')])
def test_real_studies_pid_sign_and_persistence(baseline,measurement,expected):
    ctrl=ContinuousExitController(kp=1,ki=0,kd=0,clamp=.1,atr_droop_mult=1,baseline_window=60,saturation_exit_bars=3)
    state=ctrl.open_position('BUY',entry_price=1000,stop_price=900,target_price=2000,max_hold_bars=100)
    for _ in range(10):
        ctrl.update('TITAN',state,.5,baseline,1000,5)
    for index in range(3):
        ctrl.update('TITAN',state,.5,measurement,1000,5)
        assert state.last_telemetry['studies_clamped']
        result=recommendation(state.last_telemetry)
        assert result['action']==(expected if index==2 else 'HOLD')
    assert state.last_telemetry['studies_positive_saturation_count']==(3 if expected=='EXIT' else 0)
    assert ctrl.saturation_exit_reason(state)==('saturation_exit_studies' if expected=='EXIT' else None)


def test_legacy_absolute_clamp_without_persistence_is_not_sustained_exit():
    assert recommendation(dict(studies_clamped=True,studies_output=.1))['action']=='HOLD'
