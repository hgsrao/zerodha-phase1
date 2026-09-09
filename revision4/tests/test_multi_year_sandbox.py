from revision4.run_multi_year import PERIODS, run_multi_year_replay


def test_periods_are_chronological_non_overlapping_and_named():
    assert [name for name, _, _ in PERIODS] == ["train", "validation", "untouched_test"]
    assert PERIODS[0][2] < PERIODS[1][1] < PERIODS[1][2] < PERIODS[2][1]


def test_one_month_prefix_uses_only_the_first_calendar_month(monkeypatch):
    calls = []

    def fake_validation(**kwargs):
        calls.append(kwargs)
        return {"status": "NO_EXECUTION", "metrics": {"fills": 0},
                "reconciliation": {"exact": True}}

    monkeypatch.setattr("revision4.run_multi_year.run_48symbol_validation", fake_validation)
    result = run_multi_year_replay(period_name="train", duration_months=1)

    assert calls[0]["month_start"] == "2023-09-01"
    assert calls[0]["month_end"] == "2023-09-30"
    assert result["duration_months"] == 1
