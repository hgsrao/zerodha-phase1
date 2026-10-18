from revision2_external.final_exit_authority_audit import build_final_exit_authority_audit


def _trade(trade_id: str, reason: str) -> dict:
    return {"trade_id": trade_id, "candidate_id": f"c-{trade_id}", "reason": reason}


def _decision(trade_id: str, action: str, reason: str) -> dict:
    return {"event_type": "FINAL_EXECUTION_EXIT_DECISION", "trade_id": trade_id, "action": action, "reason": reason}


def test_hard_barrier_is_preempted_not_misclassified_as_controller_failure():
    result = build_final_exit_authority_audit({
        "trades": [_trade("t1", "stop")],
        "controller_telemetry": [_decision("t1", "HOLD", "ON_PATH_OR_MINIMUM_HOLD")],
    })
    assert result["trades"][0]["verdict"] == "HARD_PRICE_BARRIER_PREEMPTED"
    assert result["unresolved_authority_blockers"] == 0
    assert not result["authority_promotion_allowed"]


def test_path_exit_requires_prior_final_exit_next_bar_decision():
    result = build_final_exit_authority_audit({
        "trades": [_trade("t1", "controller_path_exit")],
        "controller_telemetry": [_decision("t1", "EXIT_NEXT_BAR", "BEHIND_REFERENCE_PATH")],
    })
    assert result["trades"][0]["verdict"] == "MATCHED_CONTROLLER_PATH"
    assert result["authority_promotion_allowed"]


def test_pa_and_regime_exits_block_promotion_until_final_controller_represents_them():
    result = build_final_exit_authority_audit({
        "trades": [_trade("t1", "saturation_exit_pa"), _trade("t2", "regime_stressed_exit")],
        "controller_telemetry": [_decision("t1", "HOLD", "ON_PATH_OR_MINIMUM_HOLD")],
    })
    assert result["unresolved_authority_blockers"] == 2
    assert not result["authority_promotion_allowed"]
