import pytest

from revision2_external.entry_expectancy_evidence import CausalEntryExpectancyLedger


def _candidate() -> dict:
    return {
        "candidate_id": "c1", "symbol": "MARUTI", "side": "BUY", "timestamp": "t",
        "pa_confidence": 0.7, "id_confidence": 0.8, "studies_confidence": 0.6,
        "atr_fraction": 0.01, "target_r": 1.5,
    }


def test_entry_evidence_only_resolves_after_completed_cost_aware_outcome():
    ledger = CausalEntryExpectancyLedger()
    ledger.observe_fill(_candidate())
    assert ledger.summary() == {"pending_candidates": 1, "resolved_candidates": 0}
    row = ledger.record_outcome({
        "candidate_id": "c1", "trade_id": "t1", "exit_timestamp": "u", "exit_reason": "stop",
        "bars_held": 3, "pnl": -10.0, "costs": 2.0, "net_pnl": -12.0,
    })
    assert row["net_pnl"] == -12.0
    assert ledger.summary() == {"pending_candidates": 0, "resolved_candidates": 1}


def test_unobserved_candidate_cannot_be_given_a_fabricated_outcome():
    ledger = CausalEntryExpectancyLedger()
    with pytest.raises(ValueError, match="candidate_id"):
        ledger.record_outcome({})
    assert ledger.record_outcome({
        "candidate_id": "unknown", "trade_id": "t1", "exit_timestamp": "u", "exit_reason": "stop",
        "bars_held": 3, "pnl": -10.0, "costs": 2.0, "net_pnl": -12.0,
    }) is None
