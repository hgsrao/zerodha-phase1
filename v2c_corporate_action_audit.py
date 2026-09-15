"""V2-C corporate-action AUDIT - produces the per-security-interval
verdict required before dataset hash-freeze. LOCAL FILES ONLY for the
verdict computation - no Kite calls, no credentials, no network (the
underlying research that populated SPECIAL_DISPOSITIONS below was done
separately, via WebSearch, independent of this project's price files,
documented inline and in V2C_CORPORATE_ACTION_AUDIT_20260818.md).

Two-stage methodology, same discipline as the calendar-correction work,
and kept ORTHOGONAL to the residual calendar/session-gap disposition: a
corporate action found here is never used to excuse a missing session
unless it genuinely, independently connects to that specific date (none
of the 5 residual calendar dates coincide with any action found below -
checked explicitly).

  Stage 1 (v2c_corporate_action_screen.py): price-data-driven SCREENING
    only - flags candidate dislocations (inter-session jumps >= 12%
    log-move). Never a verdict, never a "source" by itself - the source
    for every disposition below is an independent citation, found before
    (or, for the two data artifacts, confirmed independent of any
    corporate-action claim) inspecting what the screen flagged. The
    screen's per-symbol max discontinuity is reported only as a
    CONSISTENCY CHECK alongside each sourced finding, per instruction -
    not as the basis for identifying that an action occurred.

  Stage 2 (this script): each flagged security was reviewed. Symbols
    with a genuine, independently-sourced finding get an explicit,
    documented disposition (SPECIAL_DISPOSITIONS below - INFY, YESBANK,
    HDFCBANK, GRASIM x2, ADANIENT, ADANIPORTS). Everything else was
    checked against two things: (a) no flagged jump anywhere in the
    file matches a clean split/bonus/rights ratio (0.5, 0.333, 0.25,
    0.2, 0.1, 0.667 +/-2%) - Kite's historical API is documented to
    auto-adjust splits/bonuses, so a real unadjusted one would produce
    exactly this kind of clean-ratio jump, and none was found outside
    the two flagged data-artifact days; (b) the flagged dates cluster
    overwhelmingly on 2020-03 (COVID-19 crash, already independently
    documented via the CIRCUIT_INTERRUPTED calendar dates) or isolated
    single-stock news-driven moves. This is disclosed explicitly as a
    METHODOLOGY LIMIT, not overclaimed as individually-verified: bulk
    PASS_NO_ACTION dispositions for the remaining ~63 intervals rest on
    (a) + (b), not a per-date news search for every single flag.

Output categories (as specified): PASS_NO_ACTION, PASS_ACTION_ADJUSTED,
REQUIRES_ADJUSTMENT, EXCLUDE_CORPORATE_ACTION_CONTAMINATION, UNRESOLVED.
A sixth, informal tag - DATA_ARTIFACT_NOT_CORPORATE_ACTION - is used for
the two flat/zero-volume days found (INFY 2015-04-24, YESBANK
2015-08-12): genuine defects, but explicitly NOT corporate actions, so
forcing them into the 5-category taxonomy would misdescribe them.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path

DATA = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
SUMMARY_CSV = DATA / "V2C_15MIN_ACQUISITION_SUMMARY.csv"
SCREEN_JSON = DATA / "V2C_CORPORATE_ACTION_SCREEN_CANDIDATES_20260818.json"
OUT_CSV = DATA / "V2C_CORPORATE_ACTION_AUDIT_20260818.csv"

COMMON_RATIOS = [0.5, 0.3333, 0.25, 0.2, 0.1, 0.6667]
RATIO_TOL = 0.02
FIELDNAMES = [
    "security_key", "historical_symbol", "window",
    "corporate_action_present", "action_type", "effective_or_ex_date",
    "source", "ohlc_series_raw_or_adjusted", "disposition",
    "largest_discontinuity_near_action_consistency_check", "note",
]

# Independently sourced, documented dispositions - see module docstring
# and V2C_CORPORATE_ACTION_AUDIT_20260818.md for the full citation trail.
# "consistency_check_near" is a (date, +/-days) window used ONLY to
# report the largest screened discontinuity near the sourced date, per
# instruction - a consistency check, never the basis for the finding.
SPECIAL_DISPOSITIONS = {
    "INFY": {
        "disposition": "PASS_ACTION_ADJUSTED",
        "corporate_action_present": "NO (for the disposition itself - see note)",
        "action_type": "N/A - no unadjusted split/bonus/merger identified for INFY in this window",
        "effective_or_ex_date": "",
        "source": "N/A (absence-of-action finding: no clean split/bonus ratio in 12 screened INFY "
                   "flags, consistent with Kite's documented auto-adjustment)",
        "ohlc_series_raw_or_adjusted": "ADJUSTED (inferred: no unadjusted-ratio discontinuity found "
                                        "anywhere in the window, consistent with Kite's documented "
                                        "split/bonus adjustment - not independently confirmed via an "
                                        "NSE adjustment-factor record)",
        "consistency_check_near": None,
        "note": "Separately, NOT part of the disposition above: 2015-04-24 is a "
                "DATA_ARTIFACT_NOT_CORPORATE_ACTION (25 bars, 0 volume, identical OHLC at "
                "2090.9, ~4x the surrounding price level, reverting the next real trading "
                "day) - not a corporate action, must be excluded from any V2-C labeling for "
                "that single date. Flagged here for completeness; does not change the "
                "PASS_ACTION_ADJUSTED disposition for the interval as a whole.",
    },
    "YESBANK": {
        "disposition": "REQUIRES_ADJUSTMENT",
        "corporate_action_present": "YES",
        "action_type": "RBI-imposed moratorium + YES Bank Limited Reconstruction Scheme, 2020 "
                        "(SBI-led capital infusion, AT1 bonds written to zero, equity NOT written off)",
        "effective_or_ex_date": "moratorium 2020-03-05; Reconstruction Scheme in force 2020-03-13; "
                                 "moratorium lifted 2020-03-18",
        "source": "National Herald India; Moneylife; Business Standard; Yale/EliScholar Journal of "
                   "Financial Crises case study",
        "ohlc_series_raw_or_adjusted": "RAW / UNADJUSTED for this event - this was not a share-count "
                                        "ratio action, so there is no adjustment factor to apply; the "
                                        "price swings are genuine, unadjusted market data reflecting a "
                                        "real regulatory-intervention event",
        "consistency_check_near": ("2020-03-05", 10),
        "note": "Recommend excluding the 2020-03-04..2020-03-18 sub-window from V2-C exhaustion/"
                "deterioration labeling - regulatory-intervention-driven, not organic price discovery. "
                "Rest of the window (including its genuinely volatile 2018-2019 asset-quality "
                "deterioration period) is real, organic price discovery - signal, not contamination, "
                "no adjustment needed there. Separately, NOT part of this disposition: 2015-08-12 is a "
                "DATA_ARTIFACT_NOT_CORPORATE_ACTION (25 bars, 0 volume, identical OHLC at 798.65, ~5x "
                "the surrounding price level, reverting the next real trading day).",
    },
    "HDFCBANK": {
        "disposition": "PASS_NO_ACTION",
        "corporate_action_present": "YES (for a related security, HDFC Ltd - see note); NO for "
                                     "HDFCBANK's own share price/count",
        "action_type": "HDFC Ltd - HDFC Bank merger (HDFC merged INTO HDFC Bank)",
        "effective_or_ex_date": "2023-07-01 (HDFC delisted 2023-07-13)",
        "source": "BusinessToday; Business Standard; ICICI Direct; newsonair.gov.in",
        "ohlc_series_raw_or_adjusted": "RAW / UNADJUSTED and correctly so - no adjustment applies to "
                                        "HDFCBANK's own series for this event (see note)",
        "consistency_check_near": ("2023-07-01", 7),
        "note": "A real, major action, but it changed HDFC's identity/listing (already reflected via "
                "HDFC's own exclusion_date=2023-07-13 in the point-in-time requirements file - HDFC "
                "itself is not one of this dataset's 69 acquired intervals), not HDFCBANK's own share "
                "price/count. HDFC shareholders received newly-issued HDFCBANK shares (25:42 ratio); "
                "HDFCBANK's existing shares needed no adjustment. Directly checked: HDFCBANK's 15-min "
                "bars for 2023-06-26..2023-07-05 are smooth and organic (815-833 range, no "
                "discontinuity) - confirms no artifact.",
    },
    "GRASIM__2014-12-03__2017-05-25": {
        "disposition": "PASS_NO_ACTION",
        "corporate_action_present": "NO (for this specific interval - the action falls outside it, see note)",
        "action_type": "Aditya Birla Nuvo (ABNL) amalgamation into Grasim + demerger of financial "
                        "services business into Aditya Birla Financial Services",
        "effective_or_ex_date": "ex-scheme ~2017-07-05",
        "source": "Aditya Birla Group press release; Business Standard (x3); Zee Business; "
                   "thedollarbusiness.com",
        "ohlc_series_raw_or_adjusted": "N/A - event postdates this interval's end (2017-05-25)",
        "consistency_check_near": None,
        "note": "This interval ends 2017-05-25, BEFORE the ex-scheme date - the action falls entirely "
                "outside this acquired window. No adjustment needed for this interval.",
    },
    "GRASIM__2018-02-28__2023-07-31": {
        "disposition": "PASS_NO_ACTION",
        "corporate_action_present": "NO (for this specific interval - the action falls outside it, see note)",
        "action_type": "Aditya Birla Nuvo (ABNL) amalgamation into Grasim + demerger of financial "
                        "services business into Aditya Birla Financial Services",
        "effective_or_ex_date": "ex-scheme ~2017-07-05",
        "source": "Aditya Birla Group press release; Business Standard (x3); Zee Business; "
                   "thedollarbusiness.com",
        "ohlc_series_raw_or_adjusted": "N/A - event predates this interval's start (2018-02-28)",
        "consistency_check_near": None,
        "note": "This interval starts 2018-02-28, AFTER the ex-scheme date - the action falls entirely "
                "before this acquired window starts. The genuine gap between Grasim's two acquired "
                "intervals (2017-05-26..2018-02-27) is exactly where this restructuring occurred; the "
                "two GRASIM intervals in this dataset are NOT continuous across that boundary and must "
                "never be treated as one continuous series for V2-C purposes. No adjustment needed "
                "within either interval individually.",
    },
    "ADANIENT": {
        "disposition": "PASS_NO_ACTION",
        "corporate_action_present": "NO",
        "action_type": "N/A - market reaction to Hindenburg Research short-seller report, not a "
                        "corporate action",
        "effective_or_ex_date": "report published 2023-01-24",
        "source": "Adani Group media statement; CNN Business; multiple outlets",
        "ohlc_series_raw_or_adjusted": "RAW - no adjustment applicable (not a corporate action)",
        "consistency_check_near": ("2023-01-24", 90),
        "note": "All 11 screened flags for this interval fall in the 2023-01-25..2023-05-23 window, "
                "matching the well-documented Hindenburg report fallout - a real, extreme, but organic "
                "market reaction to a fraud allegation, not a split/bonus/merger. No clean ratio match. "
                "Genuine signal for V2-C, not contamination - no exclusion needed.",
    },
    "ADANIPORTS": {
        "disposition": "PASS_NO_ACTION",
        "corporate_action_present": "NO",
        "action_type": "N/A - market reaction to Hindenburg Research short-seller report (partial - "
                        "2 of 6 flags), rest is 2020-03 COVID volatility and one 2021-04 news move",
        "effective_or_ex_date": "report published 2023-01-24",
        "source": "Adani Group media statement; CNN Business; multiple outlets",
        "ohlc_series_raw_or_adjusted": "RAW - no adjustment applicable (not a corporate action)",
        "consistency_check_near": ("2023-01-24", 90),
        "note": "Same reasoning as ADANIENT for the 2023-01-25/2023-01-31 flags. Remaining flags "
                "(2020-03, 2019-01, 2021-04) fall under the general COVID/isolated-news bulk "
                "disposition below - no clean ratio match found.",
    },
}


def matches_common_ratio(ratio: float) -> bool:
    return any(abs(ratio - cr) <= RATIO_TOL for cr in COMMON_RATIOS)


def largest_near(sec_flags: list[dict], center: str, days: int) -> str:
    """Consistency-check only: largest |log_move| among this symbol's
    screened flags within +/-days of `center`. Never used to decide
    whether an action occurred - the disposition and source above are
    independent of this."""
    c = date.fromisoformat(center)
    best = None
    for f in sec_flags:
        for d_str in (f["prev_date"], f["next_date"]):
            d = date.fromisoformat(d_str)
            if abs((d - c).days) <= days:
                if best is None or abs(f["log_move"]) > abs(best["log_move"]):
                    best = f
                break
    if best is None:
        return "no screened flag within the consistency-check window"
    return (f"{best['prev_date']}->{best['next_date']}: {best['simple_move_pct']:+.2f}% "
            f"(ratio {best['ratio']})")


def main() -> int:
    summary_rows = list(csv.DictReader(SUMMARY_CSV.open(encoding="utf-8")))
    flags = json.loads(SCREEN_JSON.read_text(encoding="utf-8"))
    flags_by_symbol: dict[str, list[dict]] = {}
    for f in flags:
        flags_by_symbol.setdefault(f["security_key"], []).append(f)

    out_rows = []
    for row in summary_rows:
        sec = row["security_key"]
        hist_symbol = row["symbol_at_interval_start"]
        key_windowed = f"{sec}__{row['from']}__{row['through']}"
        sec_flags = flags_by_symbol.get(sec, [])
        n_flags = len(sec_flags)

        special = SPECIAL_DISPOSITIONS.get(key_windowed) or SPECIAL_DISPOSITIONS.get(sec)
        if special:
            d = dict(special)
            if d.get("consistency_check_near"):
                center, days = d["consistency_check_near"]
                consistency = largest_near(sec_flags, center, days)
            else:
                consistency = "N/A - event outside this interval's window" if "GRASIM" in sec else \
                               f"largest of {n_flags} screened flag(s) not computed near a specific " \
                               f"action date (no unadjusted action identified for this interval)"
        elif n_flags == 0:
            d = {
                "disposition": "PASS_NO_ACTION",
                "corporate_action_present": "NO",
                "action_type": "",
                "effective_or_ex_date": "",
                "source": "N/A - no screened dislocation to investigate",
                "ohlc_series_raw_or_adjusted": "N/A",
                "note": "No screened price dislocation >=12% log-move found anywhere in this interval.",
            }
            consistency = "N/A - no screened flags"
        else:
            ratio_hit = [f for f in sec_flags if matches_common_ratio(f["ratio"])]
            if ratio_hit:
                d = {
                    "disposition": "UNRESOLVED",
                    "corporate_action_present": "UNKNOWN",
                    "action_type": "",
                    "effective_or_ex_date": "",
                    "source": "NONE - not yet independently investigated",
                    "ohlc_series_raw_or_adjusted": "UNKNOWN",
                    "note": f"{len(ratio_hit)} screened flag(s) match a clean split/bonus ratio "
                            f"and were NOT individually resolved in this audit pass - requires "
                            f"dedicated follow-up before this interval can be accepted: "
                            f"{ratio_hit}",
                }
                consistency = f"largest ratio-matching flag: {ratio_hit[0]}"
            else:
                d = {
                    "disposition": "PASS_NO_ACTION",
                    "corporate_action_present": "NO (bulk disposition - see METHODOLOGY LIMIT)",
                    "action_type": "",
                    "effective_or_ex_date": "",
                    "source": "N/A - no independent corporate-action source sought for this interval "
                               "(see METHODOLOGY LIMIT in note)",
                    "ohlc_series_raw_or_adjusted": "N/A",
                    "note": f"{n_flags} screened dislocation(s), none matching a clean split/bonus "
                            f"ratio. Consistent with 2020-03 COVID-19 market-wide volatility and/or "
                            f"isolated company-specific news-driven moves, not corporate actions. "
                            f"METHODOLOGY LIMIT: bulk disposition, not individually news-verified "
                            f"per date - see audit record.",
                }
                biggest = max(sec_flags, key=lambda f: abs(f["log_move"]))
                consistency = (f"largest screened flag (not tied to any sourced action date): "
                                f"{biggest['prev_date']}->{biggest['next_date']}: "
                                f"{biggest['simple_move_pct']:+.2f}%")

        out_rows.append({
            "security_key": sec,
            "historical_symbol": hist_symbol,
            "window": f"{row['from']}..{row['through']}",
            "corporate_action_present": d.get("corporate_action_present", ""),
            "action_type": d.get("action_type", ""),
            "effective_or_ex_date": d.get("effective_or_ex_date", ""),
            "source": d.get("source", ""),
            "ohlc_series_raw_or_adjusted": d.get("ohlc_series_raw_or_adjusted", ""),
            "disposition": d["disposition"],
            "largest_discontinuity_near_action_consistency_check": consistency,
            "note": d.get("note", ""),
        })

    with OUT_CSV.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(out_rows)

    from collections import Counter
    counts = Counter(r["disposition"] for r in out_rows)
    print(f"Audited {len(out_rows)} security-intervals -> {OUT_CSV}")
    print("Disposition counts:", dict(counts))
    for label in ("UNRESOLVED", "REQUIRES_ADJUSTMENT", "EXCLUDE_CORPORATE_ACTION_CONTAMINATION"):
        rows = [r for r in out_rows if r["disposition"] == label]
        if rows:
            print(f"\n{len(rows)} {label}:")
            for r in rows:
                print(f"  {r['security_key']:14s} {r['window']:24s} {r['action_type'] or r['note'][:100]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
