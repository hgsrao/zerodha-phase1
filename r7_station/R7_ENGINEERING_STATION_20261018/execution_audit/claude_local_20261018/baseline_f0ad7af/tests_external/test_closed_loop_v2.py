from revision2_external.closed_loop_v2 import ClosedLoopV2Tester, CompletedOutcome, EntryQualityLoop


def test_entry_quality_is_timestamp_sealed_and_cannot_increase_risk():
    tester = ClosedLoopV2Tester(EntryQualityLoop(minimum_samples=2, prior_strength=2))
    # A later outcome must be invisible to this earlier decision.
    tester.outcome(CompletedOutcome("2023-09-01T10:05:00", "SUN", "BUY", 1.5, .1, True, 1.4))
    early = tester.candidate(symbol="SUN", side="BUY", decision_timestamp="2023-09-01T10:00:00", target_r=1.5, round_trip_cost_r=.1)
    late = tester.candidate(symbol="SUN", side="BUY", decision_timestamp="2023-09-01T10:10:00", target_r=1.5, round_trip_cost_r=.1)
    assert early.completed_samples == 0
    assert late.completed_samples == 1
    assert 0 <= late.entry_derate <= 1
    tester.validate()


def test_cost_aware_setpoint_increases_when_costs_increase():
    assert EntryQualityLoop.required_probability(1.5, .30) > EntryQualityLoop.required_probability(1.5, .05)
