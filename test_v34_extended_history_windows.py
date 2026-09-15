from datetime import date

from extended_history_windows import EXCLUDED_SYMBOLS, EXTENDED_WINDOWS
from walk_forward_v5 import WINDOWS


class TestExtendedWindows:
    def test_windows_are_six_months_each(self):
        for start, end in EXTENDED_WINDOWS:
            months = (end.year - start.year) * 12 + (end.month - start.month)
            assert months == 6
            assert start.day == end.day == 14

    def test_windows_are_contiguous_and_chronological(self):
        for (_, end), (next_start, _) in zip(EXTENDED_WINDOWS, EXTENDED_WINDOWS[1:]):
            assert end == next_start

    def test_starts_at_february_2018(self):
        assert EXTENDED_WINDOWS[0][0] == date(2018, 2, 14)

    def test_ends_at_the_same_boundary_as_the_frozen_windows(self):
        assert EXTENDED_WINDOWS[-1][1] == WINDOWS[-1][1]

    def test_last_five_extended_folds_match_the_frozen_windows_exactly(self):
        assert EXTENDED_WINDOWS[-5:] == WINDOWS

    def test_seventeen_folds(self):
        assert len(EXTENDED_WINDOWS) == 17


class TestExcludedSymbols:
    def test_polycab_is_excluded(self):
        assert EXCLUDED_SYMBOLS == {"POLYCAB"}
