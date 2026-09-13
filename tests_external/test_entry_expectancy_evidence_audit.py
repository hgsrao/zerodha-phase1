from scripts.audit_entry_expectancy_evidence import build_audit


def test_audit_accepts_causal_resolved_evidence() -> None:
    row = {
        "candidate_id": "c1", "symbol": "INFY", "side": "BUY", "timestamp": "2023-09-01T10:00:00",
        "pa_confidence": 0.7, "id_confidence": 0.6, "studies_confidence": 0.5,
        "atr_fraction": 0.01, "target_r": 1.5, "exit_timestamp": "2023-09-01T10:02:00",
        "exit_reason": "stop", "bars_held": 2, "gross_pnl": -10.0, "costs": 1.0, "net_pnl": -11.0,
    }
    audit = build_audit({"period_start": "2023-09-01", "period_end_exclusive": "2023-10-01",
                         "metrics": {"completed_trades": 1},
                         "entry_expectancy_evidence": {"resolved_candidates": 1,
                                                       "pending_candidates": 0, "resolved": [row]}})
    assert audit["integrity"]["valid"]
    assert audit["overall"]["resolved"] == 1


def test_audit_rejects_same_bar_or_earlier_exit_timestamp() -> None:
    audit = build_audit({"entry_expectancy_evidence": {"resolved": [{
        "candidate_id": "c1", "symbol": "INFY", "side": "BUY", "timestamp": "2023-09-01T10:00:00",
        "pa_confidence": 0.7, "id_confidence": 0.6, "studies_confidence": 0.5,
        "atr_fraction": 0.01, "target_r": 1.5, "exit_timestamp": "2023-09-01T10:00:00",
        "exit_reason": "stop", "bars_held": 0, "gross_pnl": -10.0, "costs": 1.0, "net_pnl": -11.0,
    }]}})
    assert not audit["integrity"]["valid"]
