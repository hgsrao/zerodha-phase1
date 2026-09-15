"""V2-C corrected trading calendar builder. LOCAL FILES ONLY — no Kite
calls, no credentials, no network.

Produces a versioned, corrected replacement for
NSE_TRADING_CALENDAR_WORKING_2014_2023.csv per
V2C_CALENDAR_DATABASE_CORRECTION_20260818.md. Does NOT edit the original
file in place (it stays as the untouched acquisition-time record); this
script reads it and writes a new file alongside it.

Every value change applied here traces to a specific row in the audit
table of V2C_CALENDAR_DATABASE_CORRECTION_20260818.md, which in turn
traces to a source independent of this project's own acquired price
data (DrikPanchang for Muhurat dates, news/regulatory sources for
circuit-breaker/outage/budget-day/glitch events). Nothing here is
inferred from the price files - independent source found first, price
files cross-checked for confirmation only, never the reverse. The
remaining unresolved dates get a session_type flag only — their
regular_session value is left exactly as the original file had it,
since no independent source was found either direction and guessing is
not permitted.

Second pass (2026-08-18, post-certification-rerun, residual-date
investigation): 2017-07-10 resolved (NSE technical glitch, sourced) and
added as a 14th audited date; 2016-01-01 reclassified from
UNRESOLVED_CALENDAR_EXCEPTION to CONFIRMED_TRADING_DAY_DATA_GAP (the
2016 official holiday list independently confirms Jan 1 was NOT a
holiday, so the calendar's regular_session=TRUE was already correct —
the observed missing data is a genuine acquisition gap, not a calendar
error, and still does NOT get a documented-exception pass). Two new
NIFTY-50-index-only anomalies surfaced by the rerun (2015-01-16,
2017-02-21) are flagged as NEWLY_FLAGGED_UNINVESTIGATED — not resolved,
not exempted, a separate future investigation.

Two new columns are added:
  session_type    - REGULAR (default) | MUHURAT | BUDGET_SPECIAL |
                     CIRCUIT_INTERRUPTED | EXTENDED_OUTAGE_RECOVERY |
                     TECHNICAL_GLITCH_DELAYED_OPEN |
                     CONFIRMED_TRADING_DAY_DATA_GAP |
                     UNRESOLVED_CALENDAR_EXCEPTION |
                     NEWLY_FLAGGED_UNINVESTIGATED
  correction_note - free text pointing at the audit-table source, blank
                     for untouched REGULAR rows.

regular_session is changed only for the 8 MUHURAT dates (-> FALSE, the
regular day session did not run) and the 2 BUDGET_SPECIAL Saturdays
(-> TRUE, they were full regular sessions the base file had marked
REVIEW_REQUIRED). CIRCUIT_INTERRUPTED, EXTENDED_OUTAGE_RECOVERY, and
TECHNICAL_GLITCH_DELAYED_OPEN dates keep regular_session=TRUE (the
regular session did run, just with a mid-day interruption) and only
gain the session_type/correction_note tags.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

REF = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816") / "V2C_REFERENCE"
SOURCE_CSV = REF / "NSE_TRADING_CALENDAR_WORKING_2014_2023.csv"
OUT_CSV = REF / "NSE_TRADING_CALENDAR_CORRECTED_20260818.csv"
OUT_HASH = REF / "NSE_TRADING_CALENDAR_CORRECTED_20260818.sha256"

# session_type, new regular_session (None = leave unchanged), correction_note
# — every row here is one line of the audit table in
# V2C_CALENDAR_DATABASE_CORRECTION_20260818.md.
CORRECTIONS: dict[str, tuple[str, str | None, str]] = {
    "2015-11-11": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Wed; Muhurat session only, not a regular session"),
    "2016-10-30": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Sun; was REVIEW_REQUIRED, resolved via independent Panchang source (not price-file inference)"),
    "2017-10-19": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Thu; Muhurat session only, not a regular session"),
    "2018-11-07": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Wed; Muhurat session only, not a regular session"),
    "2019-10-27": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Sun; was REVIEW_REQUIRED, resolved via independent Panchang source (not price-file inference)"),
    "2020-11-14": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Sat; was REVIEW_REQUIRED, resolved via independent Panchang source (not price-file inference)"),
    "2021-11-04": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Thu; Muhurat session only, not a regular session"),
    "2022-10-24": ("MUHURAT", "FALSE", "Diwali/Lakshmi Puja, DrikPanchang-confirmed Mon; Muhurat session only, not a regular session"),
    "2015-02-28": ("BUDGET_SPECIAL", "TRUE", "Special Saturday Union Budget session (ClearTax); was REVIEW_REQUIRED, resolved as a full regular session"),
    "2020-02-01": ("BUDGET_SPECIAL", "TRUE", "Special Saturday Union Budget session (ClearTax); was REVIEW_REQUIRED, resolved as a full regular session"),
    "2020-03-13": ("CIRCUIT_INTERRUPTED", None, "Market-wide circuit breaker, ~45-min halt from ~09:20 IST (BusinessToday, Business Standard); regular session ran, gap is documented"),
    "2020-03-23": ("CIRCUIT_INTERRUPTED", None, "Market-wide circuit breaker (BusinessToday); regular session ran, gap is documented"),
    "2021-02-24": ("EXTENDED_OUTAGE_RECOVERY", None, "NSE telecom-link outage, halted ~10:08-15:17 IST, extended to 17:00 IST on recovery (SEBI press release, RBI/Moneylife, Zerodha Z-Connect); regular session ran with interruption + extension, gap/extra bars documented"),
    # Residual-date investigation, second pass (2026-08-18, post-
    # certification-rerun): sourced independently, web search done
    # BEFORE inspecting what the acquired price files showed, then
    # cross-checked against them for confirmation only.
    "2017-07-10": ("TECHNICAL_GLITCH_DELAYED_OPEN", None, "NSE technical glitch halted trading at/near market open; two failed restart attempts (10:45, 11:15); resumed ~12:30pm (BusinessToday, Business Standard x3, India Infoline, Forbes India, Zee Business); regular session ran with a delayed start, gap during the glitch window is documented"),
    "2016-01-01": ("CONFIRMED_UPSTREAM_DATA_GAP", None, "2016 NSE/BSE official holiday list (earliest January entry: Republic Day, Jan 26) does not include Jan 1; independent sources confirm NSE's standing practice of trading on New Year's Day - so the calendar's regular_session=TRUE was already correct, this was never a calendar misclassification. Separately, targeted diagnostic reacquisition (v2c_reacquire_diagnostic_20160101.py, 2026-08-18) independently re-pulled HDFCBANK/RELIANCE/INFY/SBIN + NIFTY 50 control over a narrow window: all 4 sample equities returned 0/25 bars for 2016-01-01 in a FRESH, separate live pull, matching the original acquisition exactly, while NIFTY 50's own series had full 25/25 data both times. Confirmed persistent upstream gap for individual equities, not an acquisition-time bug, not fixable by retrying - now gets a documented-exception pass in certification (full-day GAP_WINDOWS entry), per the same two-source-independent-confirmation discipline as every other special date, adapted for a data-availability question rather than a session-type question"),
    # Still genuinely unresolved - session_type flag only, regular_session left as-is.
    "2020-04-27": ("UNRESOLVED_CALENDAR_EXCEPTION", None, "No independent source found for a market-wide special session; kept unresolved despite a new observation (uniform extra bar at 15:30 across 28/69 files) - that pattern alone is not a source and must not be used to infer or exempt anything, per the no-price-file-inference rule; flagged for further investigation, not resolved"),
    "2015-01-16": ("CONFIRMED_UPSTREAM_DATA_GAP", None, "SUPERSEDES an earlier 'NIFTY-only' characterization (original text preserved in the NEWLY_FLAGGED_UNINVESTIGATED comment below, not deleted). Targeted diagnostic reacquisition (v2c_reacquire_diagnostic_20150116.py, 2026-08-18) independently re-pulled HDFCBANK/RELIANCE/INFY/SBIN + NIFTY 50 over a narrow window: the 4 equities returned an EMPTY BATCH for the whole window - confirmed to be because their real Kite history starts 2015-02-02 (checked directly), i.e. entirely after the target window, the same pre-existing edge-truncation mechanism already handled generically elsewhere - NOT a new/separate finding for stocks (0/69 stock intervals have this date as a standalone in-window gap: 45/69 edge-masked, 24/69 window-excluded). NIFTY 50's real history starts 2015-01-09 (well before the target date), so its absence IS a genuine, isolated interior gap, confirmed via two independent live pulls (original acquisition + this diagnostic). Documented-exception handling is therefore effectively scoped to NIFTY 50 only - not special-casing, simply because no stock interval reaches this date as a standalone gap"),
    "2015-03-16": ("CONFIRMED_UPSTREAM_DATA_GAP", None, "Chosen for targeted diagnostic reacquisition over further web search, since an external-search pass had already run and found no documented NSE incident on this date. v2c_reacquire_diagnostic_20150316.py independently re-pulled HDFCBANK/RELIANCE/INFY/SBIN + NIFTY 50 over 2015-03-12..2015-03-18: ALL 5 instruments returned exactly 124/125 slots, missing precisely the 09:15 bar-open, matching the original acquisition exactly, 0 duplicates, unanimous across the whole sample including the NIFTY control. Same evidentiary structure as 2016-01-01/2015-01-16 (persistent upstream gap, confirmed via a second independent live pull) but SINGLE-SLOT, not full-day - the certification GAP_WINDOWS entry for this date documents only the 09:15 slot, not the whole session, so any other missing slot on this date (not observed, but not ruled out either) stays a real, undocumented gap"),
    "2015-12-22": ("CONFIRMED_UPSTREAM_DATA_GAP", None, "Same method as 2015-03-16. v2c_reacquire_diagnostic_20151222.py independently re-pulled HDFCBANK/RELIANCE/INFY/SBIN + NIFTY 50 over 2015-12-18..2015-12-24: HDFCBANK/INFY/NIFTY 50 all showed the 10:30 slot present in both the fresh pull and the original file, while RELIANCE/SBIN showed it missing in both. Cross-checked against the original certification report before concluding: HDFCBANK/INFY/NIFTY were never among the 20 originally-affected files at all (only RELIANCE/SBIN were, of the 5 sampled) - so this is a clean, non-contradictory Type A confirmation for the symbols actually affected, not a mixed/ambiguous result. Single-slot GAP_WINDOWS entry (10:30 only), same discipline as 2015-03-16"),
    "2023-07-20": ("CONFIRMED_UPSTREAM_DATA_GAP", None, "Only RELIANCE ever affected (1/69 files). v2c_reacquire_diagnostic_20230720.py used a two-group design (AFFECTED: RELIANCE; UNAFFECTED controls: HDFCBANK/INFY/TCS; CONTROL: NIFTY 50), cross-checked against the original certification report before running. Result: RELIANCE reproduces exactly the same 3-slot gap (09:15/09:30/09:45) in a fresh independent pull; all controls show full data both fresh and original. Clean, unambiguous Type A. GAP_WINDOWS entry covers exactly the 3 confirmed slots (09:15-09:45 inclusive), not broader"),
    "2017-02-21": ("CONFIRMED_UPSTREAM_DATA_GAP", None, "The last of the originally-identified residual dates. Moved here from NEWLY_FLAGGED_UNINVESTIGATED, 2026-08-18, after v2c_reacquire_diagnostic_20170221.py ran: only NIFTY 50's own series was ever affected (missing 11:15); all 47 in-window stock intervals already confirmed complete. AFFECTED: NIFTY 50. UNAFFECTED controls: HDFCBANK, INFY (confirmed via direct file check). Result: NIFTY 50 reproduces the exact missing 11:15 slot in a fresh independent pull; both controls show full data, fresh matching original exactly. Clean, unambiguous Type A. Single-slot GAP_WINDOWS entry (11:15 only)"),
}

# New, previously-unflagged anomalies surfaced by the certification
# rerun (NIFTY 50 index only, not part of the 78-row per-symbol
# requirements set, so never went through the original residual-6-date
# review) - recorded here as a flag, NOT resolved, NOT added to
# SPECIAL_SESSION_DATES, no session_type assigned. Investigation is a
# separate future step.
# 2015-01-16 REMOVED from this dict 2026-08-18 (Round 4, diagnostic
# result) - it is now RESOLVED and moved into CORRECTIONS above as
# CONFIRMED_UPSTREAM_DATA_GAP. Full history preserved there, not here -
# this dict is for still-uninvestigated dates only. Original text this
# entry once held, for the correction trail:
#   Superseded original (2026-08-18, first pass): "NIFTY 50 index only:
#   25/25 bars missing (full-day absence), found in the certification
#   rerun. Not in the original 6-date residual list. Not investigated
#   yet."
#   Superseding refinement (2026-08-18, second pass, before the
#   diagnostic ran): "MARKET-WIDE (supersedes an earlier wrong
#   'NIFTY-only' characterization): absent from all 70 acquired files.
#   45/69 stock intervals mask it via edge-truncation, 24/69 don't
#   cover this date in their acquisition window. Diagnostic
#   reacquisition script ready, not yet run."
#   Now RESOLVED (see CORRECTIONS above) after the diagnostic ran.
# 2017-02-21 REMOVED from this dict 2026-08-18 (same-day diagnostic
# result) - it is now RESOLVED and moved into CORRECTIONS above as
# CONFIRMED_UPSTREAM_DATA_GAP. This was the last of the originally-
# identified residual dates - this dict is now empty.
NEWLY_FLAGGED_UNINVESTIGATED: dict[str, str] = {}


def main() -> int:
    with SOURCE_CSV.open(newline="", encoding="utf-8") as h:
        reader = csv.DictReader(h)
        fieldnames = list(reader.fieldnames) + ["session_type", "correction_note"]
        rows = list(reader)

    touched_regular_session_changes = []
    seen_correction_dates = set()

    for row in rows:
        d = row["session_date"]
        if d in CORRECTIONS:
            session_type, new_regular, note = CORRECTIONS[d]
            seen_correction_dates.add(d)
            if new_regular is not None and row["regular_session"].strip().upper() != new_regular:
                touched_regular_session_changes.append(
                    (d, row["regular_session"], new_regular, session_type)
                )
                row["regular_session"] = new_regular
            row["session_type"] = session_type
            row["correction_note"] = note
        elif d in NEWLY_FLAGGED_UNINVESTIGATED:
            row["session_type"] = "NEWLY_FLAGGED_UNINVESTIGATED"
            row["correction_note"] = NEWLY_FLAGGED_UNINVESTIGATED[d]
        else:
            row["session_type"] = "REGULAR" if row["regular_session"].strip().upper() == "TRUE" else row["regular_session"]
            row["correction_note"] = ""

    missing_dates = set(CORRECTIONS) - seen_correction_dates
    if missing_dates:
        raise SystemExit(f"REFUSING TO WRITE: {len(missing_dates)} corrected dates not found as rows "
                          f"in the source calendar file: {sorted(missing_dates)}")
    seen_flagged = {d for d in NEWLY_FLAGGED_UNINVESTIGATED if any(r["session_date"] == d for r in rows)}
    missing_flagged = set(NEWLY_FLAGGED_UNINVESTIGATED) - seen_flagged
    if missing_flagged:
        raise SystemExit(f"REFUSING TO WRITE: {len(missing_flagged)} newly-flagged dates not found as rows "
                          f"in the source calendar file: {sorted(missing_flagged)}")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as h:
        writer = csv.DictWriter(h, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    digest = hashlib.sha256(OUT_CSV.read_bytes()).hexdigest()
    OUT_HASH.write_text(f"{digest}  {OUT_CSV.name}\n", encoding="utf-8")

    print(f"Wrote {OUT_CSV} ({len(rows)} rows, {len(fieldnames)} columns).")
    print(f"SHA256: {digest}")
    print(f"Hash sidecar: {OUT_HASH}")
    print()
    n_unresolved = sum(1 for st, _, _ in CORRECTIONS.values() if st == "UNRESOLVED_CALENDAR_EXCEPTION")
    print(f"{len(CORRECTIONS)} dates carry a correction_note / session_type tag "
          f"({len(CORRECTIONS) - n_unresolved} audited/resolved + {n_unresolved} still-unresolved).")
    print(f"regular_session value actually changed on {len(touched_regular_session_changes)} dates:")
    for d, old, new, st in touched_regular_session_changes:
        print(f"  {d}: {old} -> {new}  ({st})")
    print(f"\n{len(NEWLY_FLAGGED_UNINVESTIGATED)} newly-flagged, uninvestigated anomalies tagged "
          f"(NIFTY-50-index-only, found by the certification rerun, not resolved):")
    for d, note in NEWLY_FLAGGED_UNINVESTIGATED.items():
        print(f"  {d}: {note}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
