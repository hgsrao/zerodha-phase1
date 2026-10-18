from revision2_external.entry_candidate_observations import EntryCandidateObservationLedger


def test_candidate_observation_records_one_terminal_disposition() -> None:
    ledger = EntryCandidateObservationLedger()
    ledger.observe({"candidate_id": "c1", "symbol": "INFY", "side": "BUY", "timestamp": "t",
                    "pa_confidence": 0.6, "id_confidence": 0.7})
    ledger.dispose("c1", "REJECTED", "execution_gate")
    report = ledger.report()
    assert report["dispositions"] == {"REJECTED": 1}


def test_candidate_observation_finalizes_undisposed_rows_explicitly() -> None:
    ledger = EntryCandidateObservationLedger()
    ledger.observe({"candidate_id": "c1", "symbol": "INFY", "side": "BUY", "timestamp": "t",
                    "pa_confidence": 0.6, "id_confidence": 0.7})
    ledger.finalize_pending()
    assert ledger.report()["rows"][0]["execution_disposition"] == "NOT_FILLED_UNCLASSIFIED"
