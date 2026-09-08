"""Regression tests for the read-only slippage diagnostic."""

import csv
import json

from revision4.diagnostic_48symbol import DiagnosticCollector


def test_gap_type_uses_calendar_dates_for_space_and_iso_timestamps(tmp_path):
    collector = DiagnosticCollector(str(tmp_path))
    collector.record_candidate(
        "INFY", 0,
        "2024-08-01 09:15:00+00:00", "2024-08-01 09:16:00+00:00",
        100.0, 100.1,
    )
    collector.record_candidate(
        "INFY", 1,
        "2024-08-02T15:29:00+00:00", "2024-08-05T09:15:00+00:00",
        100.0, 100.1,
    )

    assert [row["gap_type"] for row in collector.raw_observations] == ["intraday", "session"]
    assert collector.raw_observations[0]["decision_date"] == "2024-08-01"
    assert collector.raw_observations[1]["fill_date"] == "2024-08-05"


def test_raw_csv_and_summary_share_the_same_run_id(tmp_path):
    collector = DiagnosticCollector(str(tmp_path))
    collector.record_candidate(
        "INFY", 0,
        "2024-08-01T09:15:00+00:00", "2024-08-01T09:16:00+00:00",
        100.0, 100.0,
    )
    collector.save_raw_data()
    summary = collector.generate_summary()

    with (tmp_path / "raw_observations.csv").open(newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    with (tmp_path / "summary_statistics.json").open() as handle:
        persisted_summary = json.load(handle)

    assert raw_rows[0]["run_id"] == collector.run_id
    assert summary["run_id"] == collector.run_id
    assert persisted_summary["run_id"] == collector.run_id
