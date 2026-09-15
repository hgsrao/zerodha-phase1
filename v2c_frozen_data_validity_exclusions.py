"""V2-C frozen data-validity exclusion registry + event-eligibility mask.
LOCAL FILES ONLY - no Kite calls, no credentials, no network.

Frozen rule (see V2C_FROZEN_DATA_VALIDITY_EXCLUSIONS_20260818.md for the
full record): for YESBANK ONLY, calendar dates 2020-03-04..2020-03-18
inclusive are EXCLUDED from V2-C event generation / labeling. Reason:
corporate-action audit disposition REQUIRES_ADJUSTMENT (RBI moratorium +
YES Bank Limited Reconstruction Scheme, 2020 - see
V2C_CORPORATE_ACTION_AUDIT_20260818.md). Outside that window, YESBANK
remains eligible subject to the normal certification rules exactly like
every other interval - this is not "exclude YESBANK."

This is a frozen DATA-VALIDITY MASK, not a bar-level certification
concern. It must be applied BEFORE any V2-C label/event is generated -
it is orthogonal to the certification verdict (missing/duplicate/
out-of-session bars), which stays untouched by corporate-action
findings, per the standing rule that a corporate action never explains
away a session gap unless it genuinely connects to it.

CROSSING-WINDOW RULE (the part that actually matters, not a detail):
an event is INELIGIBLE if its causal input window (feature lookback) OR
its outcome window (forward label horizon) overlaps the excluded range
AT ALL - not merely if the event's anchor/entry timestamp falls inside
it. A trailing lookback that starts before 2020-03-04 and extends into
it, or an outcome horizon that starts before 2020-03-18 and extends
past it, both make the event ineligible. Only an event whose ENTIRE
[lookback_start, outcome_end] span lies wholly outside
[2020-03-04, 2020-03-18] is eligible. Implemented as an interval-overlap
test, not a membership test on the anchor date alone - a narrower check
would silently let contaminated inputs/outcomes leak into an otherwise
"clean" event.

Excluded rows are COUNTED and REPORTED (see count_excluded_bars), never
silently dropped - any future event-generation step that calls this
module is expected to log/report exclusions, not swallow them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# security_key -> (excluded_start, excluded_end), both inclusive.
# Every entry here must trace to a specific, sourced corporate-action
# audit disposition - never a bare guess, never "exclude the whole
# symbol." See V2C_CORPORATE_ACTION_AUDIT_20260818.md for the source.
FROZEN_EXCLUSIONS: dict[str, tuple[date, date]] = {
    "YESBANK": (date(2020, 3, 4), date(2020, 3, 18)),
}

FROZEN_EXCLUSION_REASONS: dict[str, str] = {
    "YESBANK": (
        "corporate-action audit disposition REQUIRES_ADJUSTMENT: RBI-imposed moratorium "
        "(2020-03-05) + YES Bank Limited Reconstruction Scheme, 2020 (in force 2020-03-13, "
        "moratorium lifted 2020-03-18) - SBI-led capital infusion, AT1 bonds written to zero. "
        "See V2C_CORPORATE_ACTION_AUDIT_20260818.md."
    ),
}


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reason: str


def check_event_eligibility(security_key: str, lookback_start: date, outcome_end: date) -> EligibilityResult:
    """Returns whether an event is eligible for V2-C label/event
    generation, given the earliest date its feature lookback touches
    (inclusive) and the latest date its outcome/label horizon touches
    (inclusive). Call this BEFORE generating any label for an event -
    it is a pre-labeling gate, not a post-hoc filter.

    Raises ValueError on a malformed span (lookback_start > outcome_end)
    rather than silently returning a possibly-wrong answer.
    """
    if lookback_start > outcome_end:
        raise ValueError(f"lookback_start ({lookback_start}) must be <= outcome_end ({outcome_end})")

    excl = FROZEN_EXCLUSIONS.get(security_key)
    if excl is None:
        return EligibilityResult(True, "no frozen exclusion registered for this symbol")

    excl_start, excl_end = excl
    # Standard interval-overlap test: two closed intervals [a,b] and
    # [c,d] overlap iff a <= d and b >= c. This correctly catches BOTH
    # crossing directions (lookback bleeding in from before the window,
    # outcome bleeding out past the window), not just anchor-in-range.
    overlaps = lookback_start <= excl_end and outcome_end >= excl_start
    if overlaps:
        reason = FROZEN_EXCLUSION_REASONS.get(security_key, "frozen exclusion")
        return EligibilityResult(
            False,
            f"INELIGIBLE_FROZEN_EXCLUSION: event span [{lookback_start}..{outcome_end}] overlaps "
            f"{security_key}'s frozen exclusion window [{excl_start}..{excl_end}]. {reason}",
        )
    return EligibilityResult(True, "event span lies wholly outside all frozen exclusion windows for this symbol")


def count_excluded_bars(security_key: str, bar_dates: list[str]) -> dict:
    """Reporting helper: given the distinct date strings (YYYY-MM-DD)
    actually present in a symbol's acquired data, report which fall
    inside its frozen exclusion window. Excluded rows must be counted
    and reported, never silently dropped - this is the counting/
    reporting half of that requirement; callers that touch the raw bar
    rows should log this alongside whatever they do with them."""
    excl = FROZEN_EXCLUSIONS.get(security_key)
    if excl is None:
        return {
            "security_key": security_key,
            "frozen_exclusion_window": None,
            "reason": None,
            "excluded_dates": [],
            "excluded_bar_dates_count": 0,
        }
    excl_start, excl_end = excl
    excluded = sorted({d for d in bar_dates if excl_start.isoformat() <= d <= excl_end.isoformat()})
    return {
        "security_key": security_key,
        "frozen_exclusion_window": [excl_start.isoformat(), excl_end.isoformat()],
        "reason": FROZEN_EXCLUSION_REASONS.get(security_key, ""),
        "excluded_dates": excluded,
        "excluded_bar_dates_count": len(excluded),
    }


if __name__ == "__main__":
    # Self-check against the real acquired YESBANK file, plus boundary-
    # case tests for the crossing-window logic. Not the authoritative
    # certification pass - a focused correctness check for this module.
    import csv
    import sys
    from pathlib import Path

    DATA = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
    yesbank_path = next(DATA.glob("NSE_YESBANK_15minute_*.csv"), None)
    if yesbank_path is None:
        print("YESBANK acquired file not found - skipping the real-data check.")
    else:
        bar_dates = set()
        with yesbank_path.open(newline="", encoding="utf-8") as h:
            for row in csv.DictReader(h):
                bar_dates.add(row["timestamp"][:10])
        report = count_excluded_bars("YESBANK", sorted(bar_dates))
        print(f"Real-data check against {yesbank_path.name}:")
        print(f"  frozen_exclusion_window: {report['frozen_exclusion_window']}")
        print(f"  excluded_bar_dates_count: {report['excluded_bar_dates_count']}")
        print(f"  excluded_dates: {report['excluded_dates']}")

    print("\nBoundary-case tests for check_event_eligibility:")
    cases = [
        # (label, security_key, lookback_start, outcome_end, expected_eligible)
        ("fully before window", "YESBANK", date(2020, 2, 20), date(2020, 3, 3), True),
        ("fully after window", "YESBANK", date(2020, 3, 19), date(2020, 3, 25), True),
        ("anchor inside window", "YESBANK", date(2020, 3, 10), date(2020, 3, 10), False),
        ("lookback bleeds in from before", "YESBANK", date(2020, 2, 25), date(2020, 3, 4), False),
        ("outcome bleeds out past the window", "YESBANK", date(2020, 3, 18), date(2020, 3, 22), False),
        ("spans the entire window", "YESBANK", date(2020, 2, 1), date(2020, 4, 1), False),
        ("touches only the start boundary", "YESBANK", date(2020, 2, 25), date(2020, 3, 4), False),
        ("touches only the end boundary", "YESBANK", date(2020, 3, 18), date(2020, 3, 20), False),
        ("adjacent, does not touch (one day before)", "YESBANK", date(2020, 2, 25), date(2020, 3, 3), True),
        ("adjacent, does not touch (one day after)", "YESBANK", date(2020, 3, 19), date(2020, 3, 26), True),
        ("other symbol, no exclusion registered", "HDFCBANK", date(2020, 3, 10), date(2020, 3, 10), True),
    ]
    all_pass = True
    for label, sec, lb, oc, expected in cases:
        result = check_event_eligibility(sec, lb, oc)
        ok = result.eligible == expected
        all_pass &= ok
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {label}: eligible={result.eligible} (expected {expected})")
        if not ok:
            print(f"         reason: {result.reason}")

    try:
        check_event_eligibility("YESBANK", date(2020, 3, 10), date(2020, 3, 5))
        print("  [FAIL] malformed span (lookback_start > outcome_end) should have raised ValueError")
        all_pass = False
    except ValueError:
        print("  [PASS] malformed span correctly raises ValueError")

    print(f"\n{'All boundary-case tests passed.' if all_pass else 'SOME TESTS FAILED - do not trust this module yet.'}")
    sys.exit(0 if all_pass else 1)
