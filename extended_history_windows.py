"""Anchored fold boundaries for the extended-history walk-forward.

walk_forward_v5.py's WINDOWS is frozen and untouched - this is a separate,
new set of boundaries for the 2016-2026 downloaded history, on the same
Feb-14/Aug-14 six-month cadence so the last five folds line up exactly with
the original WINDOWS (2024-02-14 onward).

The start (2018-02-14) is not the start of the downloaded data - it is
chosen so that even the latest-listed symbol still in the universe
(LAURUSLABS, first traded 2016-12-19) has cleared the 13-month formation
warmup the momentum strategies need before the first fold's training window
can select anything.
"""

from __future__ import annotations

from datetime import date


def _add_months(day: date, months: int) -> date:
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return date(year, month, day.day)


def _six_month_windows(start: date, end: date) -> tuple[tuple[date, date], ...]:
    windows = []
    cursor = start
    while cursor < end:
        nxt = _add_months(cursor, 6)
        windows.append((cursor, min(nxt, end)))
        cursor = nxt
    return tuple(windows)


EXTENDED_WINDOWS = _six_month_windows(date(2018, 2, 14), date(2026, 8, 14))

# The 19-symbol universe used for the extended run: POLYCAB excluded (its
# April 2019 listing would otherwise cap the whole universe's usable
# history to ~7.3 years instead of ~9.7).
EXCLUDED_SYMBOLS = frozenset({"POLYCAB"})
