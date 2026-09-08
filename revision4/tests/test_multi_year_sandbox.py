from revision4.run_multi_year import PERIODS


def test_periods_are_chronological_non_overlapping_and_named():
    assert [name for name, _, _ in PERIODS] == ["train", "validation", "untouched_test"]
    assert PERIODS[0][2] < PERIODS[1][1] < PERIODS[1][2] < PERIODS[2][1]
