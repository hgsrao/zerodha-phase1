"""V2-C 15-minute dataset certification pass. LOCAL FILES ONLY — no Kite
calls, no credentials, no network.

Per P01D_V2C_DATASET_CERTIFICATION_GATE_20260818.md and the frozen rules
from this certification session:

- Timestamps are bar-OPEN. A full regular NSE session has 25 fifteen-
  minute opens: 09:15, 09:30, ..., 15:15 (last bar covers 15:15-15:30).
- Certification tolerance, frozen before computing any missingness:
    duplicates                : 0 allowed, always FAIL
    out-of-session timestamps : 0 allowed, always FAIL (unless the date
                                 is a documented special session, see below)
    unexplained interior gaps : 0 allowed -> FAIL, pending manual review
    edge truncation           : a CONTIGUOUS run of fully-missing sessions
                                 anchored at the acquisition window's start
                                 or end -> PASS_WITH_DOCUMENTED_EXCEPTION.
                                 (Corrected 2026-08-18: originally worded
                                 as "the single first/last session only" -
                                 that was too narrow. Real Kite history for
                                 most instruments starts materially later
                                 than the requested warmup date, producing
                                 one genuine multi-day leading block, not
                                 scattered interior gaps. This rule is
                                 frozen as the contiguous-block definition
                                 BEFORE the certification rerun that uses
                                 it, not redefined after seeing results —
                                 the underlying finding that motivated it
                                 was made and reported before this wording
                                 was corrected.) Scattered or partial
                                 missingness anywhere inside the block
                                 that already has continuous coverage
                                 remains an interior gap, never edge.
    documented special session : an EVENT-SCOPED exception (Muhurat window,
                                 Budget Saturday, circuit-breaker halt
                                 window, exchange outage/recovery window)
                                 verified against an authoritative source
                                 -> PASS_WITH_DOCUMENTED_EXCEPTION for
                                 gaps/bars that fall inside the sourced
                                 event window on that specific date, never
                                 inferred from the price file alone, and
                                 never a blanket pass for the whole date
                                 (Corrected 2026-08-18, second pass: the
                                 first version of this rule excused ANY
                                 missing bar or ANY out-of-session
                                 timestamp on a documented date, which
                                 would have silently passed an unrelated
                                 gap or a fabricated timestamp merely
                                 because the date had a real anomaly.
                                 Tightened to only the sourced event
                                 window - see MUHURAT_SESSION_WINDOW,
                                 CIRCUIT_WINDOWS, OUTAGE_WINDOW below.)
    anything else             : never silently interpolated or excused,
                                 stays FAIL / UNRESOLVED_CALENDAR_EXCEPTION
                                 pending verification

This script computes and reports. It does NOT decide that an interior
gap is "probably illiquidity" or invent any other excuse for a FAIL -
that requires actual documentation, a separate, later, human step.

`verdict` (per interval, above) is bar-level integrity ONLY - missing/
duplicate/out-of-session bars - and is never touched by a corporate-
action finding. A separate, combined field, `release_disposition`,
folds in v2c_frozen_data_validity_exclusions.py's frozen exclusion
registry (currently: YESBANK, 2020-03-04..2020-03-18,
REQUIRES_ADJUSTMENT - see V2C_FROZEN_DATA_VALIDITY_EXCLUSIONS_20260818.md)
to distinguish CERTIFIED_CLEAN (bar-level PASS/PASS_WITH_DOCUMENTED_EXCEPTION,
no frozen exclusion) from CERTIFIED_WITH_FROZEN_EXCLUSION (bar-level
PASS/PASS_WITH_DOCUMENTED_EXCEPTION, but a specific sub-window is walled
off from labeling) from NOT_CERTIFIED (bar-level FAIL, ineligible
regardless of any exclusion question).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

from v2c_frozen_data_validity_exclusions import FROZEN_EXCLUSIONS, count_excluded_bars

BASE = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816")
REF = BASE / "V2C_REFERENCE"
RAW_DATA_DIR = BASE / "V2C_15MIN_DATA_ACQUIRED"
# DATA is reassigned in main() if --data-dir is passed (e.g. to certify
# V2C_15MIN_DATA_CLEANED/ instead) - every price-CSV glob and the output
# report path below use this name, looked up at call time, so overriding
# it before main()'s body runs redirects the whole certification pass.
DATA = RAW_DATA_DIR
REQUIREMENTS_CSV = REF / "V2C_15MIN_ACQUISITION_REQUIREMENTS_WITH_WARMUP_WORKING_2015_2023.csv"

# Corrected, versioned calendar (built by v2c_build_corrected_calendar.py,
# project root) - NOT the raw working file. The raw file is
# price-file-derived (its own `source` column says
# nifty50_index_historical_2014_2026.csv) and still carries
# REVIEW_REQUIRED/unflagged special dates; certifying against it would
# knowingly reproduce the false FAILs this whole correction pass exists to
# fix. Hash-pinned: refuses to run against a calendar file whose SHA256
# doesn't match what was actually reviewed and frozen.
CALENDAR_CSV = REF / "NSE_TRADING_CALENDAR_CORRECTED_20260818.csv"
CALENDAR_SHA256_FILE = REF / "NSE_TRADING_CALENDAR_CORRECTED_20260818.sha256"
# Always the RAW acquisition's summary (pure metadata - which security/
# window pairs exist - not price data, so not duplicated into
# V2C_15MIN_DATA_CLEANED/; unaffected by --data-dir).
SUMMARY_CSV = RAW_DATA_DIR / "V2C_15MIN_ACQUISITION_SUMMARY.csv"

# Full audited 13-date special-session set, per
# V2C_CALENDAR_DATABASE_CORRECTION_20260818.md - every date here has a
# source independent of this project's own price files (Panchang calendar
# for Muhurat, news/regulatory sources for the rest). NOT a silent excuse:
# any nonstandard bar/gap on a date NOT in this dict stays FAIL/interior-
# missing pending explicit review. Four distinct session_type values
# because they require different expected-bar handling, not one flat
# "special" bucket:
#   MUHURAT             - regular session did NOT run (Diwali holiday);
#                          the date is EXCLUDED from the 25-bar regular
#                          grid entirely, not just exempted from
#                          out-of-session checks. Evening bars documented
#                          ONLY inside MUHURAT_SESSION_WINDOW.
#   BUDGET_SPECIAL       - a normally-non-trading Saturday that WAS a full
#                          regular session; the date is ADDED to the valid
#                          25-bar grid (calendar file doesn't include
#                          Saturdays by default) and then treated exactly
#                          like an ordinary weekday - no special exemption
#                          of any kind. The Budget-day fact only justifies
#                          the date being a session at all, not any
#                          particular missing/OOS bar on it.
#   CIRCUIT_INTERRUPTED  - a normal weekday with a mid-session halt;
#                          ONLY missing bars whose slot falls inside the
#                          sourced halt window (CIRCUIT_WINDOWS) are
#                          documented. Missing bars outside that window,
#                          or any out-of-session timestamp, are NOT
#                          exempted - a circuit breaker does not authorize
#                          arbitrary gaps or arbitrary extra bars.
#   EXTENDED_OUTAGE_RECOVERY - a normal weekday with an outage AND an
#                          extended-hours resumption; missing bars are
#                          documented only inside the outage's "missing"
#                          window, and out-of-session (post-15:15) bars
#                          are documented only inside the outage's
#                          "extra" recovery window (OUTAGE_WINDOW below).
#   TECHNICAL_GLITCH_DELAYED_OPEN - a normal weekday where NSE's own
#                          technical glitch delayed the open; ONLY
#                          missing bars inside the sourced delayed-open
#                          window (GLITCH_WINDOWS) are documented, same
#                          discipline as CIRCUIT_INTERRUPTED - no extra
#                          hours, no blanket pass.
#   CONFIRMED_UPSTREAM_DATA_GAP - a normal weekday, independently
#                          CONFIRMED via a second, separate live Kite
#                          pull (not inferred from the original price
#                          file alone) that the historical data for
#                          individual equities genuinely does not exist
#                          upstream, while other instrument classes
#                          (e.g. the NIFTY 50 index) are unaffected. The
#                          full day's missing bars are documented - not
#                          a session-type exemption, a data-availability
#                          finding, verified by actually re-querying the
#                          source rather than guessing from the shape of
#                          the gap.
#
# 2017-07-10 added same session, second-pass residual-date investigation
# (not price-file inference): BusinessToday, Business Standard (x3),
# India Infoline, Forbes India, Zee Business all independently report an
# NSE technical glitch that halted trading at/near market open, with two
# failed restart attempts (10:45, 11:15) before trading resumed ~12:30pm
# - a real, sourced event, found via web search BEFORE looking at what
# the acquired price files showed for that date, then cross-checked
# against them (which showed exactly the matching pattern: slots
# 09:15-12:15 missing, 12:30-15:15 present, uniformly across 45/69
# files) - source first, price file only as confirmation, never the
# reverse.
#
# 2016-01-01 added third pass (same day, targeted reacquisition
# diagnostic, v2c_reacquire_diagnostic_20160101.py): a narrow, separate
# live re-pull of HDFCBANK/RELIANCE/INFY/SBIN (sample) + NIFTY 50
# (control) over 2015-12-28..2016-01-06 confirmed 0/25 bars for all 4
# sample equities on 2016-01-01 in BOTH the fresh pull and the original
# file, while NIFTY 50's own series has full 25/25 data both times -
# a persistent upstream gap for individual equities specifically, not
# an acquisition-time bug and not fixable by retrying. This is the
# calendar-classification half of the finding already independently
# confirmed in Round 2 (2016 official holiday list has no Jan 1 entry -
# it was genuinely a trading day); this diagnostic is the separate
# data-availability half, confirmed via a real second query, not price-
# file inference.
#
# 2015-01-16 added fourth pass, same day
# (v2c_reacquire_diagnostic_20150116.py): SUPERSEDES an earlier "NIFTY-
# only" characterization (see V2C_CALENDAR_DATABASE_CORRECTION_20260818.md
# "Round 4" for the full trail - original text preserved, not deleted).
# The refined finding: absent from all 70 originally acquired files, but
# for all 69 stock intervals this is fully explained by pre-existing
# edge-truncation (their real Kite history starts 2015-02-02 in the
# diagnostic sample, confirmed via a fresh live pull - the same
# mechanism already handled generically, not a new/separate gap - 45/69
# stock intervals mask the date via their edge block, the other 24/69
# simply don't cover it in their acquisition window; 0/69 have it as a
# standalone in-window gap). NIFTY 50's own real history starts
# 2015-01-09 - well before the target date - so its absence IS a
# genuine, isolated interior gap, confirmed via two independent live
# pulls (original acquisition + this diagnostic). Documented-exception
# handling below is therefore effectively scoped to NIFTY 50 only - not
# because of any special-casing, simply because no stock interval ever
# reaches this date as a standalone gap in the first place.
#
# 2015-03-16 added fifth pass, same day (targeted diagnostic
# reacquisition, v2c_reacquire_diagnostic_20150316.py, chosen over
# further web search since an external-search pass had already run and
# found no documented NSE incident on this date - a fresh pull could
# distinguish upstream-gap vs. acquisition-defect in a way search
# could not): HDFCBANK/RELIANCE/INFY/SBIN + NIFTY 50, all 5, returned
# EXACTLY 124/125 slots in a fresh independent pull over
# 2015-03-12..2015-03-18 - missing precisely the 09:15 bar-open on
# 2015-03-16, matching the original acquisition exactly, 0 duplicates,
# unanimous across the whole sample including the NIFTY control. Same
# evidentiary structure as 2016-01-01/2015-01-16 (persistent upstream
# gap, confirmed via a second independent live pull, not inferred from
# the price file's shape alone) but at single-slot granularity, not
# full-day - GAP_WINDOWS below reflects that (09:15 only, not the full
# session).
#
# 2015-12-22 added sixth pass, same day (v2c_reacquire_diagnostic_20151222.py,
# same method as 2015-03-16). Result required a check before concluding,
# not a blunt "mixed" read: HDFCBANK/INFY/NIFTY 50 all showed 10:30
# PRESENT in both the fresh pull and the original file, while
# RELIANCE/SBIN showed it MISSING in both - looked like disagreement
# until cross-checked against the original certification report, which
# confirms HDFCBANK/INFY/NIFTY were never among the 20 originally-
# affected files at all (only RELIANCE/SBIN were, of the 5 sampled).
# So this is a clean, non-contradictory Type A confirmation for the
# symbols actually affected (persistent upstream gap, same as
# 2015-03-16, single-slot), with the unaffected symbols correctly
# showing no issue - not evidence against the finding.
#
# 2023-07-20 added seventh pass, same day (v2c_reacquire_diagnostic_20230720.py):
# only RELIANCE was ever affected by this date (1/69 files). Two-group
# design (AFFECTED: RELIANCE; UNAFFECTED controls: HDFCBANK/INFY/TCS;
# CONTROL: NIFTY 50), cross-checked against the original certification
# report before running. Result: RELIANCE reproduces exactly the same
# 3-slot gap (09:15, 09:30, 09:45) in a fresh independent pull; all
# three unaffected controls plus NIFTY show full data, both fresh and
# original. Clean, unambiguous Type A - persistent upstream gap,
# single-symbol, three consecutive slots.
#
# 2017-02-21 added eighth pass, same day (v2c_reacquire_diagnostic_20170221.py):
# the last of the originally-identified residual dates. Only NIFTY 50's
# own series was ever affected (missing 11:15); all 47 in-window stock
# intervals already confirmed complete (Round 4). AFFECTED: NIFTY 50.
# UNAFFECTED controls: HDFCBANK, INFY (confirmed via direct file check
# to have 11:15 present). Result: NIFTY 50 reproduces the exact missing
# 11:15 slot in a fresh independent pull; both controls show full data,
# fresh matching original exactly. Clean, unambiguous Type A -
# persistent upstream gap, index-specific, single-slot.
SPECIAL_SESSION_DATES = {
    "2015-11-11": "MUHURAT", "2016-10-30": "MUHURAT", "2017-10-19": "MUHURAT",
    "2018-11-07": "MUHURAT", "2019-10-27": "MUHURAT", "2020-11-14": "MUHURAT",
    "2021-11-04": "MUHURAT", "2022-10-24": "MUHURAT",
    "2015-02-28": "BUDGET_SPECIAL", "2020-02-01": "BUDGET_SPECIAL",
    "2020-03-13": "CIRCUIT_INTERRUPTED", "2020-03-23": "CIRCUIT_INTERRUPTED",
    "2021-02-24": "EXTENDED_OUTAGE_RECOVERY",
    "2017-07-10": "TECHNICAL_GLITCH_DELAYED_OPEN",
    "2016-01-01": "CONFIRMED_UPSTREAM_DATA_GAP",
    "2015-01-16": "CONFIRMED_UPSTREAM_DATA_GAP",
    "2015-03-16": "CONFIRMED_UPSTREAM_DATA_GAP",
    "2015-12-22": "CONFIRMED_UPSTREAM_DATA_GAP",
    "2023-07-20": "CONFIRMED_UPSTREAM_DATA_GAP",
    "2017-02-21": "CONFIRMED_UPSTREAM_DATA_GAP",
}
MUHURAT_LIKE = {"MUHURAT"}  # session_types where the regular grid doesn't apply at all
ADD_TO_REGULAR_GRID = {"BUDGET_SPECIAL"}  # normally-excluded dates that WERE full sessions
DOCUMENTED_PARTIAL = {"CIRCUIT_INTERRUPTED", "EXTENDED_OUTAGE_RECOVERY", "TECHNICAL_GLITCH_DELAYED_OPEN", "CONFIRMED_UPSTREAM_DATA_GAP"}  # stay in grid, gaps documented ONLY inside a sourced/confirmed window

# Documented Diwali Muhurat corridor, applied uniformly to all 8 Muhurat
# dates. Per-year NSE circular windows were independently sourced for 6 of
# the 8 (2015: 17:45-18:45; 2017: 18:15 pre-open/18:30-19:30 normal;
# 2018: sources conflicted between 17:15-18:30 and 18:30-19:30, both
# inside this bound; 2020: 17:45 block-deal .. 19:35 post-close;
# 2021: 18:15-19:15; 2022: 18:15-19:15 - see WebSearch trail in this
# session). 2016 and 2019 had no directly-locatable to-the-minute circular.
# Rather than mix per-year exact cutoffs (tighter for some years than
# others, on uneven evidence) against a uniform-looking exemption, this
# uses ONE conservative, sourced corridor for all 8 dates: 17:00-19:45
# IST (last bar-open slot before 20:00), which comfortably contains every
# specific window actually found above with margin on both sides. This
# still excludes ~22 of 24 hours on each date, so it is not "any
# timestamp on that date" - it is a real, cited bound, just not pinned to
# the minute for every individual year. Bounds are INCLUSIVE bar-open
# times: start <= t <= end.
MUHURAT_SESSION_WINDOW = (dtime(17, 0), dtime(19, 45))

# Documented circuit-breaker halt windows (BusinessToday, Business
# Standard - see V2C_CALENDAR_DATABASE_CORRECTION_20260818.md). Each halt
# was ~45 minutes plus a 15-minute pre-open re-start; the window below is
# rounded outward to the nearest bar-open slot on both sides as a margin,
# not narrowed to look clean. INCLUSIVE bar-open bounds: start <= t <= end.
#   2020-03-13: plunge/halt ~09:20, resumed (post pre-open) ~10:20
#   2020-03-23: plunge/halt ~09:58, resumed (post pre-open) ~10:58
CIRCUIT_WINDOWS: dict[str, tuple[dtime, dtime]] = {
    "2020-03-13": (dtime(9, 15), dtime(10, 15)),
    "2020-03-23": (dtime(9, 45), dtime(10, 45)),
}

# Confirmed-upstream-data-gap windows: the FULL regular session, since
# the finding is "this date's data doesn't exist upstream for this
# instrument class" (confirmed via a second independent live pull, see
# v2c_reacquire_diagnostic_20160101.py), not a partial-session event.
# INCLUSIVE bar-open bounds: start <= t <= end.
GAP_WINDOWS: dict[str, tuple[dtime, dtime]] = {
    "2016-01-01": (dtime(9, 15), dtime(15, 15)),
    "2015-01-16": (dtime(9, 15), dtime(15, 15)),
    # Single-slot gap, not full-day - confirmed via diagnostic that only
    # the 09:15 bar-open is missing on this date, unlike 2016-01-01/
    # 2015-01-16. A narrower window here is deliberate: it means any
    # OTHER missing slot on 2015-03-16 (which the diagnostic never
    # observed but which a future acquisition attempt could still
    # surface) stays a real, undocumented, FAIL-triggering gap - this
    # entry documents exactly what was confirmed, nothing broader.
    "2015-03-16": (dtime(9, 15), dtime(9, 15)),
    # Confirmed via v2c_reacquire_diagnostic_20151222.py: RELIANCE/SBIN
    # (the two originally-affected sampled symbols) both reproduce the
    # exact 10:30 gap in a fresh independent pull. Single-slot, same
    # discipline as 2015-03-16.
    "2015-12-22": (dtime(10, 30), dtime(10, 30)),
    # Confirmed via v2c_reacquire_diagnostic_20230720.py: RELIANCE (the
    # only originally-affected symbol) reproduces exactly the same
    # 3-slot gap in a fresh independent pull; all controls stay clean.
    "2023-07-20": (dtime(9, 15), dtime(9, 45)),
    # Confirmed via v2c_reacquire_diagnostic_20170221.py: NIFTY 50 (the
    # only originally-affected series) reproduces the exact missing
    # 11:15 slot in a fresh independent pull; controls stay clean.
    "2017-02-21": (dtime(11, 15), dtime(11, 15)),
}

# Documented NSE technical-glitch delayed-open window (BusinessToday,
# Business Standard x3, India Infoline, Forbes India, Zee Business):
# trading halted at/near market open, two failed restart attempts
# (10:45, 11:15), resumed ~12:30pm. Bar-open slots 09:15..12:15 (13
# slots) are the documented-missing window; 12:30 onward is ordinary.
# INCLUSIVE bar-open bounds: start <= t <= end.
GLITCH_WINDOWS: dict[str, tuple[dtime, dtime]] = {
    "2017-07-10": (dtime(9, 15), dtime(12, 15)),
}

# Documented NSE outage/recovery window, 2021-02-24 (SEBI press release,
# RBI/Moneylife, Zerodha Z-Connect): outage ~10:08-15:17 IST, recovery
# session extended to 17:00 IST. "missing" = regular-grid bar-open slots
# that can be documented-missing because they fall inside the outage;
# "extra" = post-15:15 bar-open slots that can be documented
# out-of-session bars because they fall inside the extended recovery
# session (15:17..17:00, first possible extra bar-open at 15:30, last at
# 16:45 covering 16:45-17:00) - nothing outside either range on this date
# is exempted. INCLUSIVE bar-open bounds: start <= t <= end.
# Missing-window upper bound is 15:15, not 15:00: the sourced outage end
# (15:17) falls after the 15:15 bar-open (which covers 15:15-15:30), so
# that bar sits inside the documented outage too. Corrected 2026-08-18
# after the first certification rerun showed exactly this single-slot
# residual on 2021-02-24 across 48/69 files - a boundary/arithmetic fix
# to how the already-sourced 10:08-15:17 window maps onto the bar-open
# grid, not a new claim inferred from the price file (the outage window
# itself was already sourced before any rerun).
OUTAGE_WINDOW: dict[str, dict[str, tuple[dtime, dtime]]] = {
    "2021-02-24": {
        "missing": (dtime(10, 0), dtime(15, 15)),
        "extra": (dtime(15, 30), dtime(16, 45)),
    },
}

FULL_SESSION_SLOTS = []
t = dtime(9, 15)
while t <= dtime(15, 15):
    FULL_SESSION_SLOTS.append(t)
    minutes = t.hour * 60 + t.minute + 15
    if minutes > 15 * 60 + 15:
        break
    t = dtime(minutes // 60, minutes % 60)
assert len(FULL_SESSION_SLOTS) == 25, len(FULL_SESSION_SLOTS)
SLOT_SET = set(FULL_SESSION_SLOTS)


def verify_calendar_hash() -> str:
    """Refuse to run against a calendar file that doesn't match the
    SHA256 recorded when it was built/reviewed. A calendar edit made
    after that review (accidental or not) must not silently feed into
    certification."""
    if not CALENDAR_SHA256_FILE.exists():
        print(f"REFUSING TO RUN: no SHA256 sidecar at {CALENDAR_SHA256_FILE} - "
              f"the corrected calendar was never hash-frozen.")
        sys.exit(1)
    expected = CALENDAR_SHA256_FILE.read_text(encoding="utf-8").split()[0].strip()
    actual = hashlib.sha256(CALENDAR_CSV.read_bytes()).hexdigest()
    if actual != expected:
        print(f"REFUSING TO RUN: {CALENDAR_CSV} SHA256 mismatch.\n"
              f"  expected (frozen): {expected}\n"
              f"  actual (on disk):  {actual}\n"
              f"The calendar file changed since it was reviewed/hashed - "
              f"re-review before certifying against it.")
        sys.exit(1)
    print(f"Calendar hash verified: {CALENDAR_CSV.name} matches frozen SHA256 {actual}.")
    return actual


def load_calendar() -> dict[str, bool]:
    """date_str -> regular_session bool, for regular_session in {TRUE,FALSE}."""
    cal = {}
    with CALENDAR_CSV.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            if row["trading_session"].strip().upper() != "TRUE":
                continue
            cal[row["session_date"]] = row["regular_session"].strip().upper() == "TRUE"
    return cal


def load_requirements() -> dict[str, dict]:
    out = {}
    with REQUIREMENTS_CSV.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            key = f"{row['security_key']}__{row['acquisition_from_with_warmup']}__{row['acquisition_through']}"
            out[key] = row
    return out


def load_summary() -> list[dict]:
    with SUMMARY_CSV.open(newline="", encoding="utf-8") as h:
        return list(csv.DictReader(h))


def parse_ts(raw: str) -> datetime:
    # kite historical_data timestamps as written by download_historical_ohlcv.py
    raw = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(raw)


def compute_release_disposition(security_key: str, verdict: str) -> str:
    """NOT_CERTIFIED (bar-level FAIL, ineligible regardless of any
    frozen-exclusion question) / CERTIFIED_WITH_FROZEN_EXCLUSION
    (bar-level PASS/PASS_WITH_DOCUMENTED_EXCEPTION, but a registered
    frozen exclusion walls off part of this symbol's data from
    labeling) / CERTIFIED_CLEAN (bar-level PASS/PASS_WITH_DOCUMENTED_EXCEPTION,
    no frozen exclusion registered). See
    V2C_FROZEN_DATA_VALIDITY_EXCLUSIONS_20260818.md."""
    if verdict == "FAIL":
        return "NOT_CERTIFIED"
    if security_key in FROZEN_EXCLUSIONS:
        return "CERTIFIED_WITH_FROZEN_EXCLUSION"
    return "CERTIFIED_CLEAN"


def certify_file(path: Path, window_start: str, window_end: str, calendar: dict[str, bool]) -> dict:
    ts_counter: Counter[str] = Counter()
    per_date_times: dict[str, list[dtime]] = defaultdict(list)
    raw_first = raw_last = None

    with path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            raw = row["timestamp"]
            dt = parse_ts(raw)
            date_s = dt.date().isoformat()
            ts_counter[raw] += 1
            per_date_times[date_s].append(dt.time())
            if raw_first is None:
                raw_first = raw
            raw_last = raw

    duplicate_rows = sum(c - 1 for c in ts_counter.values() if c > 1)

    # Adjust the regular-session grid per the audited 13-date set:
    #   - MUHURAT dates removed (regular session never ran there).
    #   - BUDGET_SPECIAL dates added (real full Saturday sessions the
    #     calendar file doesn't include by default).
    calendar_dates_in_window = {
        d for d in calendar
        if window_start <= d <= window_end and calendar[d]
    }
    muhurat_in_window = {
        d for d in SPECIAL_SESSION_DATES
        if window_start <= d <= window_end and SPECIAL_SESSION_DATES[d] in MUHURAT_LIKE
    }
    budget_in_window = {
        d for d in SPECIAL_SESSION_DATES
        if window_start <= d <= window_end and SPECIAL_SESSION_DATES[d] in ADD_TO_REGULAR_GRID
    }
    valid_dates_in_window = sorted(
        (calendar_dates_in_window - muhurat_in_window) | budget_in_window
    )
    expected_sessions = len(valid_dates_in_window)
    expected_bars = expected_sessions * 25

    # Missing-slot computation on the adjusted regular-session dates.
    # Slot-level (not just a count) so CIRCUIT_INTERRUPTED/
    # EXTENDED_OUTAGE_RECOVERY dates can have only the specific slots
    # inside their sourced halt/outage window documented - the rest of
    # a missing day on those dates is NOT automatically excused.
    missing_slots_by_date: dict[str, list[dtime]] = {}
    for d in valid_dates_in_window:
        times_today = per_date_times.get(d, [])
        present_valid_slots = {t for t in times_today if t in SLOT_SET}
        missing_slots = sorted(SLOT_SET - present_valid_slots)
        if missing_slots:
            missing_slots_by_date[d] = missing_slots

    # Split each date's missing slots into documented (inside the sourced
    # event window for that date's session_type) vs undocumented. Only
    # CIRCUIT_INTERRUPTED/EXTENDED_OUTAGE_RECOVERY dates have any
    # documented-missing window at all; BUDGET_SPECIAL and ordinary dates
    # get none - a Budget Saturday does not excuse a missing bar, only
    # the date itself being a valid session.
    documented_missing_dates: dict[str, int] = {}
    missing_by_date: dict[str, int] = {}
    for d, slots in missing_slots_by_date.items():
        session_type = SPECIAL_SESSION_DATES.get(d)
        window = None
        if session_type == "CIRCUIT_INTERRUPTED":
            window = CIRCUIT_WINDOWS.get(d)
        elif session_type == "EXTENDED_OUTAGE_RECOVERY":
            window = OUTAGE_WINDOW.get(d, {}).get("missing")
        elif session_type == "TECHNICAL_GLITCH_DELAYED_OPEN":
            window = GLITCH_WINDOWS.get(d)
        elif session_type == "CONFIRMED_UPSTREAM_DATA_GAP":
            window = GAP_WINDOWS.get(d)
        if window is None:
            undocumented = slots
            documented = []
        else:
            w_start, w_end = window
            documented = [t for t in slots if w_start <= t <= w_end]
            undocumented = [t for t in slots if not (w_start <= t <= w_end)]
        if documented:
            documented_missing_dates[d] = len(documented)
        if undocumented:
            missing_by_date[d] = len(undocumented)

    # Edge-block detection: a missing date counts as EDGE only if it is
    # part of an unbroken run of missing dates anchored at either boundary
    # of valid_dates_in_window - not merely the single first/last date.
    # This is what actually distinguishes "real history starts later than
    # the requested warmup date" (one edge block) from genuine scattered
    # interior gaps.
    #
    # IMPORTANT: the contiguity scan uses missing_slots_by_date (ANY
    # missing slot, documented or not), not missing_by_date (undocumented
    # only). Fixed 2026-08-18 after adding 2015-01-16 as a documented
    # CONFIRMED_UPSTREAM_DATA_GAP date: that date sits in the MIDDLE of
    # several stocks' edge-truncation blocks (their real history starts
    # even later, e.g. 2015-02-02). Fully "documenting away" its missing
    # slots removed it from missing_by_date, which broke the contiguous
    # run right at that date and spuriously reclassified everything AFTER
    # it in the same block as scattered interior FAILs - caught by
    # inspecting the rerun's output before accepting it, not assumed
    # correct just because the code ran without error.
    all_missing_dates = set(missing_slots_by_date.keys())
    edge_dates: set[str] = set()
    n = len(valid_dates_in_window)
    i = 0
    while i < n and valid_dates_in_window[i] in all_missing_dates:
        edge_dates.add(valid_dates_in_window[i])
        i += 1
    j = n - 1
    while j >= 0 and valid_dates_in_window[j] in all_missing_dates:
        edge_dates.add(valid_dates_in_window[j])
        j -= 1

    interior_missing_dates = {d: v for d, v in missing_by_date.items() if d not in edge_dates}
    edge_missing_dates = {d: v for d, v in missing_by_date.items() if d in edge_dates}

    # Out-of-session bars: any bar whose date+time isn't a valid regular-
    # session slot. Documented ONLY if it falls inside that date's
    # specific sourced event window - never a blanket pass for the whole
    # date, and never for BUDGET_SPECIAL or CIRCUIT_INTERRUPTED (a
    # circuit breaker documents missing bars, not extra ones; a Budget
    # Saturday is treated as an ordinary session with no exemption at
    # all). Anything else is unexplained - a hard FAIL trigger, never
    # silently excused.
    documented_special_bars: dict[str, list[str]] = {}
    unexplained_out_of_session: dict[str, list[str]] = {}
    for d, times_today in per_date_times.items():
        is_regular = d in valid_dates_in_window
        session_type = SPECIAL_SESSION_DATES.get(d)
        for t in times_today:
            slot_str = t.strftime("%H:%M:%S")
            if is_regular and t in SLOT_SET:
                continue  # normal, in-session bar on a regular day
            documented = False
            if session_type == "MUHURAT":
                w_start, w_end = MUHURAT_SESSION_WINDOW
                documented = w_start <= t <= w_end
            elif session_type == "EXTENDED_OUTAGE_RECOVERY":
                window = OUTAGE_WINDOW.get(d, {}).get("extra")
                documented = window is not None and window[0] <= t <= window[1]
            # BUDGET_SPECIAL, CIRCUIT_INTERRUPTED, and no-session_type
            # dates: documented stays False - no out-of-session exemption.
            if documented:
                documented_special_bars.setdefault(d, []).append(slot_str)
            else:
                unexplained_out_of_session.setdefault(d, []).append(slot_str)

    unexplained_count = sum(len(v) for v in unexplained_out_of_session.values())
    documented_count = sum(len(v) for v in documented_special_bars.values())

    if duplicate_rows > 0 or unexplained_count > 0 or interior_missing_dates:
        verdict = "FAIL"
    elif edge_missing_dates or documented_special_bars or documented_missing_dates:
        verdict = "PASS_WITH_DOCUMENTED_EXCEPTION"
    else:
        verdict = "PASS"

    return {
        "file": str(path.name),
        "window_start": window_start,
        "window_end": window_end,
        "expected_sessions": expected_sessions,
        "expected_bars": expected_bars,
        "actual_bars": sum(ts_counter.values()),
        "distinct_timestamps": len(ts_counter),
        "duplicate_bars": duplicate_rows,
        "unexplained_out_of_session_count": unexplained_count,
        "unexplained_out_of_session_dates": unexplained_out_of_session,
        "documented_special_session_bar_count": documented_count,
        "documented_special_session_dates": sorted(documented_special_bars.keys()),
        "documented_missing_bars_count": sum(documented_missing_dates.values()),
        "documented_missing_dates": documented_missing_dates,
        "total_missing_bars": sum(missing_by_date.values()) + sum(documented_missing_dates.values()),
        "interior_missing_dates_count": len(interior_missing_dates),
        "interior_missing_dates_full": interior_missing_dates,
        "edge_missing_block_size": len(edge_missing_dates),
        "edge_missing_first_last": (
            [min(edge_missing_dates), max(edge_missing_dates)] if edge_missing_dates else []
        ),
        "first_timestamp": raw_first,
        "last_timestamp": raw_last,
        "verdict": verdict,
    }


def main() -> int:
    global DATA
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=None,
                     help="directory of NSE_*.csv price files to certify (default: "
                          "V2C_15MIN_DATA_ACQUIRED, the raw acquisition). Pass "
                          "V2C_15MIN_DATA_CLEANED to certify the cleaned derivative instead - "
                          "the same certification logic runs unchanged against either directory, "
                          "so a clean rerun genuinely proves the cleanup rather than assuming it.")
    args = ap.parse_args()
    if args.data_dir:
        DATA = Path(args.data_dir)
        if not DATA.exists():
            raise SystemExit(f"REFUSING TO RUN: --data-dir {DATA} does not exist")
        print(f"Certifying against --data-dir override: {DATA}")

    calendar_sha256 = verify_calendar_hash()
    calendar = load_calendar()
    requirements = load_requirements()
    summary_rows = load_summary()

    results = []
    for row in summary_rows:
        sec_key = row["security_key"]
        w_start, w_end = row["from"], row["through"]
        req_key = None
        for k, r in requirements.items():
            if r["security_key"] == sec_key and r["acquisition_from_with_warmup"] == w_start and r["acquisition_through"] == w_end:
                req_key = k
                req_row = r
                break
        candidates = [p for p in DATA.glob(f"NSE_{sec_key}_15minute_{w_start}_{w_end}.csv")]
        if not candidates:
            results.append({"file": f"NSE_{sec_key}_15minute_{w_start}_{w_end}.csv (MISSING FROM DISK)",
                             "verdict": "FAIL", "security_key": sec_key})
            continue
        cert = certify_file(candidates[0], w_start, w_end, calendar)
        cert["security_key"] = sec_key
        cert["symbol_at_interval_start"] = row["symbol_at_interval_start"]
        if req_key:
            cert["required_from"] = req_row["required_from"]
            cert["required_through"] = req_row["required_through"]
            cert["membership_window_consistent"] = (
                cert["window_start"] <= req_row["required_from"]
                and cert["window_end"] == req_row["required_through"]
            )
        cert["release_disposition"] = compute_release_disposition(sec_key, cert["verdict"])
        if sec_key in FROZEN_EXCLUSIONS:
            bar_dates = set()
            with candidates[0].open(newline="", encoding="utf-8") as h:
                for row2 in csv.DictReader(h):
                    bar_dates.add(row2["timestamp"][:10])
            cert["frozen_exclusion"] = count_excluded_bars(sec_key, sorted(bar_dates))
        results.append(cert)

    nifty_files = list(DATA.glob("NSE_NIFTY 50_15minute_*.csv"))
    nifty_cert = None
    if nifty_files:
        nifty_path = nifty_files[0]
        parts = nifty_path.stem.split("_")
        w_start, w_end = parts[-2], parts[-1]
        nifty_cert = certify_file(nifty_path, w_start, w_end, calendar)
        nifty_cert["release_disposition"] = compute_release_disposition("NIFTY 50", nifty_cert["verdict"])

    skipped_identity = list(csv.DictReader((DATA / "V2C_15MIN_ACQUISITION_SKIPPED_IDENTITY_UNRESOLVED.csv").open(encoding="utf-8"))) \
        if (DATA / "V2C_15MIN_ACQUISITION_SKIPPED_IDENTITY_UNRESOLVED.csv").exists() else []
    skipped_token = list(csv.DictReader((DATA / "V2C_15MIN_ACQUISITION_SKIPPED_UNRESOLVED_TOKEN.csv").open(encoding="utf-8"))) \
        if (DATA / "V2C_15MIN_ACQUISITION_SKIPPED_UNRESOLVED_TOKEN.csv").exists() else []

    verdict_counts = Counter(r["verdict"] for r in results)
    release_disposition_counts = Counter(r["release_disposition"] for r in results)

    report = {
        "certification_rules": {
            "timestamp_convention": "bar-open, 25 slots per regular session (09:15..15:15 step 15m)",
            "duplicates_allowed": 0,
            "out_of_session_allowed": 0,
            "interior_gaps_allowed": 0,
            "edge_truncation": "allowed, PASS_WITH_DOCUMENTED_EXCEPTION only",
            "calendar_file": CALENDAR_CSV.name,
            "calendar_sha256": calendar_sha256,
            "special_session_dates_count": len(SPECIAL_SESSION_DATES),
            "muhurat_session_window": [MUHURAT_SESSION_WINDOW[0].isoformat(), MUHURAT_SESSION_WINDOW[1].isoformat()],
            "circuit_windows": {d: [w[0].isoformat(), w[1].isoformat()] for d, w in CIRCUIT_WINDOWS.items()},
            "glitch_windows": {d: [w[0].isoformat(), w[1].isoformat()] for d, w in GLITCH_WINDOWS.items()},
            "gap_windows": {d: [w[0].isoformat(), w[1].isoformat()] for d, w in GAP_WINDOWS.items()},
            "outage_window": {
                d: {k: [w[0].isoformat(), w[1].isoformat()] for k, w in win.items()}
                for d, win in OUTAGE_WINDOW.items()
            },
            "frozen_exclusions": {k: [v[0].isoformat(), v[1].isoformat()] for k, v in FROZEN_EXCLUSIONS.items()},
        },
        "per_interval": results,
        "nifty50": nifty_cert,
        "unresolved_identity": skipped_identity,
        "unresolved_token": skipped_token,
        "verdict_counts": dict(verdict_counts),
        "release_disposition_counts": dict(release_disposition_counts),
        "n_intervals_certified": len(results),
    }

    out_path = DATA / "V2C_15MIN_CERTIFICATION_REPORT_20260818.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"Certified {len(results)} security-intervals + NIFTY 50.")
    print("Verdict counts:", dict(verdict_counts))
    print("Release disposition counts:", dict(release_disposition_counts))
    print(f"NIFTY 50 verdict: {nifty_cert['verdict'] if nifty_cert else 'NOT FOUND'}"
          f" (release_disposition: {nifty_cert['release_disposition'] if nifty_cert else 'N/A'})")
    for r in results:
        if r.get("release_disposition") == "CERTIFIED_WITH_FROZEN_EXCLUSION" or "frozen_exclusion" in r:
            print(f"  {r['security_key']}: release_disposition={r['release_disposition']}, "
                  f"frozen_exclusion={r.get('frozen_exclusion')}")
    print(f"Full report -> {out_path}")
    print()

    fails = [r for r in results if r["verdict"] == "FAIL"]
    not_certified = [r for r in results if r.get("release_disposition") == "NOT_CERTIFIED"]
    nifty_fail = bool(nifty_cert) and nifty_cert["verdict"] == "FAIL"
    if fails or not_certified or nifty_fail:
        print("SHA256 manifest NOT generated - per the frozen rule, only after")
        print(f"the accepted-files/exception record is final ({len(fails)} intervals FAILED"
              f"{', NIFTY 50 FAILED' if nifty_fail else ''}).")
    else:
        # Genuinely 0 FAIL, 0 NOT_CERTIFIED, NIFTY not FAIL - the gate is
        # real, checked here, not assumed from an earlier run. Generate
        # the final dataset SHA256 manifest now, over exactly the files
        # DATA currently points at (raw or cleaned, whichever was
        # certified this run) - never re-hash a different directory than
        # the one just proven clean.
        manifest_rows = []
        for r in results:
            fpath = DATA / r["file"]
            manifest_rows.append({
                "security_key": r["security_key"],
                "file": r["file"],
                "sha256": hashlib.sha256(fpath.read_bytes()).hexdigest(),
                "verdict": r["verdict"],
                "release_disposition": r["release_disposition"],
            })
        if nifty_cert:
            npath = DATA / nifty_cert["file"]
            manifest_rows.append({
                "security_key": "NIFTY 50",
                "file": nifty_cert["file"],
                "sha256": hashlib.sha256(npath.read_bytes()).hexdigest(),
                "verdict": nifty_cert["verdict"],
                "release_disposition": nifty_cert["release_disposition"],
            })
        hash_manifest_path = DATA / "V2C_15MIN_DATASET_HASH_MANIFEST_20260818.json"
        hash_manifest_path.write_text(json.dumps({
            "data_dir": str(DATA),
            "calendar_file": CALENDAR_CSV.name,
            "calendar_sha256": calendar_sha256,
            "n_files": len(manifest_rows),
            "files": manifest_rows,
        }, indent=2), encoding="utf-8")
        print(f"ALL {len(manifest_rows)} files CERTIFIED_CLEAN or CERTIFIED_WITH_FROZEN_EXCLUSION - "
              f"0 FAIL, 0 NOT_CERTIFIED, genuinely verified this run.")
        print(f"SHA256 dataset hash manifest generated -> {hash_manifest_path}")

    print(f"\n{len(fails)} intervals require disposition (FAIL):")
    for r in fails[:30]:
        reason_bits = []
        if r.get("duplicate_bars", 0) > 0:
            reason_bits.append(f"{r['duplicate_bars']} duplicate bars")
        if r.get("unexplained_out_of_session_count", 0) > 0:
            reason_bits.append(f"{r['unexplained_out_of_session_count']} unexplained out-of-session bars "
                                f"on {list(r.get('unexplained_out_of_session_dates', {}).keys())[:3]}")
        if r.get("interior_missing_dates_count", 0) > 0:
            reason_bits.append(f"{r['interior_missing_dates_count']} interior dates with missing bars")
        if "MISSING FROM DISK" in r["file"]:
            reason_bits.append("file missing from disk")
        print(f"  {r.get('security_key', '?'):20s} {r['file']:55s} {', '.join(reason_bits)}")

    date_impact: Counter = Counter()
    for r in results:
        for d in r.get("unexplained_out_of_session_dates", {}):
            date_impact[d] += 1
        for d in r.get("interior_missing_dates_full", {}):
            date_impact[d] += 1

    print(f"\n=== CALENDAR-LEVEL VIEW: {len(date_impact)} unique dates account for all FAILs across {len(results)} files ===")
    for d, n in sorted(date_impact.items(), key=lambda x: -x[1]):
        print(f"  {d}: affects {n}/{len(results)} files")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
