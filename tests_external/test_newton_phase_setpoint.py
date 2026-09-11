from revision2_external.newton_phase_setpoint import NewtonPhaseSetpoints

def test_newton_phase_target_moves_toward_confirmed_turns_with_bounded_step():
    targets=NewtonPhaseSetpoints(minimum_samples=3,max_step_degrees=10)
    targets.update('SUN','BUY',30);targets.update('SUN','BUY',30)
    result=targets.update('SUN','BUY',30)
    assert result['updated'] is True
    assert 0 < result['target_after'] <= 10
    assert targets.target('SUN','SELL') is None
