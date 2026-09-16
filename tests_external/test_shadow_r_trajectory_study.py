from scripts.run_external_shadow_r_trajectory_study import aggregate_reports, month_windows


def _report(start: str, live: float, shadow: float):
    return {
        "status": "SHADOW_COMPLETE",
        "scope": {"start": start, "end": start},
        "identity": {key: "same" for key in (
            "stock_manifest_hash", "context_manifest_hash", "config_hash", "safety_contract_hash",
        )},
        "controller_telemetry_summary": {"shadow_r_trajectory_updates": 2},
        "trades": [
            {"net_pnl": live, "reason": "stop", "exit_timestamp": "2023-09-01T10:00:00",
             "shadow_r_trajectory": {"shadow_net_pnl": shadow, "shadow_exit_bars_held": 2,
                                      "shadow_exit_timestamp": "2023-09-01T09:50:00"}},
        ],
    }


def test_month_windows_are_consecutive_calendar_months():
    assert month_windows("2023-09-01", 3) == [
        ("2023-09-01", "2023-09-30"),
        ("2023-10-01", "2023-10-31"),
        ("2023-11-01", "2023-11-30"),
    ]


def test_aggregate_reports_keeps_shadow_and_live_ledger_separate():
    study = aggregate_reports([_report("2023-09-01", -10.0, -8.0), _report("2023-10-01", 5.0, 4.0)])
    assert study["aggregate"]["live_net_pnl"] == -5.0
    assert study["aggregate"]["shadow_counterfactual_net_pnl"] == -4.0
    assert study["aggregate"]["shadow_delta_net_pnl"] == 1.0
    assert study["aggregate"]["nontrivial_shadow_outcomes"] == 2
    assert study["promotion_authorized"] is False


def test_aggregate_reports_rejects_identity_mismatch():
    changed = _report("2023-10-01", 1.0, 1.0)
    changed["identity"]["config_hash"] = "different"
    try:
        aggregate_reports([_report("2023-09-01", 1.0, 1.0), changed])
    except ValueError as error:
        assert "mismatched sealed identities" in str(error)
    else:
        raise AssertionError("identity mismatch must fail closed")
