import sys

sys.path.insert(0, ".")

from revision2_external.final_execution_controller import FinalExecutionController


def _entry(**overrides):
    values = dict(side="BUY", pa_confidence=0.7, id_approved=True, studies_direction=1,
                  studies_confidence=0.8, entry_price=100.0, stop_price=90.0,
                  target_price=115.0, maximum_hold_bars=30,
                  entry_quality={"symbol_regime_samples": 0, "suggested_entry_derate": 1.0},
                  dynamics={"response_time_bars": 12.0})
    values.update(overrides)
    return FinalExecutionController().entry_decision(**values)


def test_entry_requires_id_and_directional_study_alignment():
    assert _entry()["action"] == "ADMIT"
    assert _entry(studies_direction=0)["action"] == "DEFER"
    assert _entry(studies_direction=-1)["action"] == "CANCEL"
    assert _entry(id_approved=False)["reason"] == "ID_NOT_APPROVED"


def test_bootstrap_target_and_horizon_are_explicit_not_pretend_adaptive():
    result = _entry()
    assert result["target_setpoint_r"] == 1.5
    assert result["target_setpoint_source"] == "BOOTSTRAP_MPC"
    assert result["maximum_hold_setpoint_bars"] == 30


def test_exit_prefers_a_bounded_next_bar_path_exit_to_target_chasing():
    result = FinalExecutionController().exit_decision(
        path={"behind_path": True}, exit_pid={"studies_clamped": False, "stop_before": 90.0, "stop_after": 91.0},
        held_bars=3, minimum_hold_bars=2, maximum_hold_bars=30,
    )
    assert result["action"] == "EXIT_NEXT_BAR"
    assert result["reason"] == "BEHIND_REFERENCE_PATH"
