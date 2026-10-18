import pytest
from revision5.ccpp_unified_plant import MarkVExecutiveController, MarkVTelemetryInputs

def test_startup_warmup_clamps_mvg_admission():
    ctrl = MarkVExecutiveController()
    inp = MarkVTelemetryInputs(
        bay_id="GTG1",
        raw_signal_present=True,
        signal_side="BUY",
        fsrn_speed_droop=0.95,
        fsrt_exhaust_temp=0.95,
        fsra_acceleration=0.90,
        fsrs_startup_ramp=0.35,  # Opening warmup active
        fsrm_manual_stop=1.0,
        sync_bus_aligned=True,
        master_protective_trip=False,
        current_position_lots=0
    )
    res = ctrl.evaluate(inp)
    assert not res["admitted"]
    assert res["action"] == "HOLD_STANDBY"
    assert res["controlling_limiter"] == "FSRS_STARTUP_RAMP"

def test_prime_entry_admits_at_mvg_stroke():
    ctrl = MarkVExecutiveController()
    inp = MarkVTelemetryInputs(
        bay_id="GTG2",
        raw_signal_present=True,
        signal_side="BUY",
        fsrn_speed_droop=0.90,
        fsrt_exhaust_temp=0.82,  # Binding limiter
        fsra_acceleration=0.95,
        fsrs_startup_ramp=1.0,
        fsrm_manual_stop=1.0,
        sync_bus_aligned=True,
        master_protective_trip=False,
        current_position_lots=0
    )
    res = ctrl.evaluate(inp)
    assert res["admitted"]
    assert res["action"] == "ENTRY_BUY"
    assert res["fsr_selected"] == 0.82
    assert res["controlling_limiter"] == "FSRT_EXHAUST_TEMP"

def test_severe_droop_triggers_unwind_exit():
    ctrl = MarkVExecutiveController()
    inp = MarkVTelemetryInputs(
        bay_id="CSTG1",
        raw_signal_present=False,
        signal_side="BUY",
        fsrn_speed_droop=0.20,  # Below 0.25 trip cutoff
        fsrt_exhaust_temp=0.80,
        fsra_acceleration=0.90,
        fsrs_startup_ramp=1.0,
        fsrm_manual_stop=1.0,
        sync_bus_aligned=True,
        master_protective_trip=False,
        current_position_lots=50
    )
    res = ctrl.evaluate(inp)
    assert not res["admitted"]
    assert res["action"] == "EXIT"
    assert res["controlling_limiter"] == "FSRN_SPEED_DROOP"

def test_ansi86_trip_enforces_fsrmin_floor():
    ctrl = MarkVExecutiveController()
    inp = MarkVTelemetryInputs(
        bay_id="BPSTG",
        raw_signal_present=False,
        signal_side="BUY",
        fsrn_speed_droop=0.80,
        fsrt_exhaust_temp=0.80,
        fsra_acceleration=0.80,
        fsrs_startup_ramp=1.0,
        fsrm_manual_stop=1.0,
        sync_bus_aligned=True,
        master_protective_trip=True,
        current_position_lots=25,
        fsrmin_floor=0.15
    )
    res = ctrl.evaluate(inp)
    assert not res["admitted"]
    assert res["action"] == "EXIT"
    assert res["fsr_selected"] == 0.0
    assert res["effective_fsr"] == 0.15
