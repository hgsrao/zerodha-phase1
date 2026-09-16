from revision2_external.closed_loop_dry_run import build_closed_loop_cascade_dry_run, build_stress_test_dry_run


def _event(report, event_type):
    return next(
        event for event in report["events"]
        if event.get("event_type") == event_type or event.get("scenario") == event_type
    )


def test_dry_run_proves_completed_outcomes_only_affect_future_entry_feedback():
    report = build_closed_loop_cascade_dry_run()
    before = _event(report, "ENTRY_QUALITY_BEFORE_OUTCOMES")["profile"]
    after = _event(report, "ENTRY_QUALITY_AFTER_COMPLETED_OUTCOMES")["profile"]

    assert before["completed_outcomes_total"] == 0
    assert after["completed_outcomes_total"] == 20
    assert after["suggested_entry_derate"] <= before["suggested_entry_derate"]
    assert after["suggested_confidence_offset"] >= before["suggested_confidence_offset"]


def test_dry_run_hmm_and_cascade_are_bounded_shadow_recommendations():
    report = build_closed_loop_cascade_dry_run()
    hmm = [event for event in report["events"] if event["event_type"] == "HMM_REGIME_RISK_SHADOW"]
    cascade = _event(report, "CASCADE_PROPOSAL_SHADOW")

    assert hmm[-1]["observation"]["stressed_latched"] is True
    assert all(event["execution_affected"] is False for event in hmm)
    assert 0.0 <= cascade["combined_proposed_risk_multiplier"] <= 1.0
    assert cascade["execution_affected"] is False
    assert report["hmm_execution_status"] == "SHADOW_ONLY"


def test_dry_run_stop_is_armed_for_next_bar_not_retroactively_filled():
    report = build_closed_loop_cascade_dry_run()
    armed = _event(report, "TRADE_PATH_STOP_ARMED_NEXT_BAR")
    later = _event(report, "TRADE_PATH_NEXT_BAR_CHECK")

    assert armed["same_bar_exit_checked"] is False
    assert armed["controller_telemetry"]["stop_after"] >= armed["controller_telemetry"]["stop_before"]
    assert later["would_trigger_prior_stop"] is True
    assert later["prior_armed_stop"] == armed["stop_armed_from_close"]


def test_stress_run_fails_closed_at_hard_limit_and_under_minimum_size():
    report = build_stress_test_dry_run()
    hard_limit = _event(report, "PORTFOLIO_HARD_LIMIT")["observation"]
    minimum = _event(report, "MINIMUM_SIZE_REJECTION")
    contradiction = _event(report, "CONTRADICTORY_SIGNALS")

    assert hard_limit["suggested_new_risk_derate"] == 0.0
    assert minimum["final_quantity"] == 0
    assert minimum["would_submit_order"] is False
    assert contradiction["combined_proposed_risk_multiplier"] == 0.0


def test_stress_run_keeps_hmm_halted_in_deadband_and_gaps_conservatively():
    report = build_stress_test_dry_run()
    hmm = _event(report, "HMM_HYSTERESIS_DEADBAND_SHADOW")
    pooling = _event(report, "ENTRY_QUALITY_PARTIAL_POOLING")
    gap = _event(report, "NEXT_BAR_STOP_GAP")["result"]

    assert hmm["latch_trace"][-1]["stressed_latched"] is True
    assert hmm["deadband_observation"]["stressed_latched"] is True
    assert pooling["after_two_losses"]["partial_pool_weight"] < 0.10
    assert gap["shadow_exit_reason"] == "shadow_r_stop_gap"
    assert gap["shadow_exit_price"] == 98.0
