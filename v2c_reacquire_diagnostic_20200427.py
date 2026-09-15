"""V2-C targeted reacquisition DIAGNOSTIC for 2020-04-27. Read-only.
Imports only download_historical_ohlcv.py's read-only primitives, same
credential boundary as every other historical pull in this project.

Purpose: 2020-04-27 is an EXTRA-BAR anomaly, not a missing-bar one - a
uniform out-of-session bar at exactly 15:30 across 28/69 acquired
files (see V2C_CALENDAR_DATABASE_CORRECTION_20260818.md "Round 2"/
"Round 4"). An external web-search pass already ran and found no
independent evidence of altered NSE closing/session mechanics on this
date. The diagnostic question here is different in kind from the
missing-bar diagnostics (2016-01-01, 2015-01-16, 2015-03-16,
2015-12-22): NOT "can Kite recover a missing slot?" but "does a fresh,
independent pull still return the 15:30 bar for the same symbols that
originally had it, while unaffected symbols/NIFTY do not?"

TWO SAMPLE GROUPS, chosen from the actual original certification report
(not guessed) before running anything:
  AFFECTED   (had the 15:30 bar originally): HDFCBANK, INFY, RELIANCE
  UNAFFECTED (did not, same acquisition window): SBIN, ADANIPORTS,
             ASIANPAINT
  CONTROL: NIFTY 50 (also originally unaffected)

Result-classification logic (decided AFTER running, not before):
  A. Affected symbols again show 15:30 fresh; unaffected/NIFTY do not
     -> persistent upstream Kite timestamp/session artifact for those
     specific instruments. Reproduction establishes "persistent
     upstream data behavior," NOT "legitimate NSE session behavior" -
     these are different claims and must not be conflated. Does NOT by
     itself authorize adding this to the documented-exception
     machinery the way missing-bar gaps were - see the module-level
     note below.
  B. Fresh pull no longer has 15:30 for the previously-affected symbols
     -> the anomaly looks acquisition-version/API-history dependent.
     Does NOT authorize deleting the original bar automatically - a
     candidate repair needing a separately-frozen replacement rule.
  C. Fresh 15:30 appears broadly, INCLUDING previously-unaffected
     symbols -> STOP, do not conclude anything - the historical
     endpoint may now be serving a different representation than it
     did at original-acquisition time. Requires further investigation,
     not an automatic classification either way.

Every sample is cross-checked against the ORIGINAL certification report
(not assumed from memory of what "should" be affected) before any
result is interpreted - the previous diagnostic's mistake (calling a
result "mixed" without first checking which symbols were even eligible
to show the anomaly) is not repeated here.

If the 15:30 bar exists (fresh or original), its OHLCV is inspected
too: exact duplicate of the 15:15 bar / zero-volume synthetic-looking /
genuine distinct candle. This is diagnostic evidence only - it does not
by itself justify blessing or deleting the bar.

IMPORTANT, per instruction: even if the extra bar reproduces
consistently on a fresh pull, that alone does NOT make 2020-04-27
eligible for the existing DOCUMENTED_PARTIAL/CONFIRMED_UPSTREAM_DATA_GAP
certification machinery, which was built for MISSING bars on
independently-sourced or diagnostically-confirmed session-type dates,
not for legitimizing an EXTRA bar with no NSE session basis. A separate
classification (tentatively CONFIRMED_UPSTREAM_EXTRA_BAR_ARTIFACT) and
a narrow, explicitly-frozen data-cleaning rule (remove only the 15:30
bar on this date for the specifically affected intervals, both original
and cleaned file hashes preserved) would be a SEPARATE decision, made
only after reviewing this diagnostic's output - not automated by this
script.

Fail-closed discipline, same as every other diagnostic in this project:
  - Retry data is DIAGNOSTIC ONLY until it independently passes
    timestamp-parse, window, and duplicate checks.
  - NEVER overwrites the original acquired files - writes to a
    dedicated diagnostic subfolder instead.
  - Does not patch, clean, or classify anything by itself - reports
    what a fresh pull returns; a human/subsequent step decides.

Narrow window: 2020-04-23..2020-04-29 (a few real trading days on each
side of the target date, a Monday).
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import download_historical_ohlcv as dl

TARGET_DATE = "2020-04-27"
TARGET_SLOT = "15:30:00"
COMPARISON_SLOT = "15:15:00"
WINDOW_START = date(2020, 4, 23)
WINDOW_END = date(2020, 4, 29)
AFFECTED_SYMBOLS = ["HDFCBANK", "INFY", "RELIANCE"]
UNAFFECTED_SYMBOLS = ["SBIN", "ADANIPORTS", "ASIANPAINT"]
CONTROL_SYMBOL = "NIFTY 50"
OUT_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED/DIAGNOSTIC_REACQUISITION_20260818")
ORIGINAL_DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
# 5 real trading days (Thu, Fri, Mon, Tue, Wed) x 25 regular slots - the
# 15:30 bar, if present, is EXTRA, beyond this baseline expectation.
EXPECTED_REGULAR_SLOTS = 5 * 25


def check_batch(rows: list[dict]) -> tuple[bool, str, int]:
    if not rows:
        return False, "empty batch", 0
    ts_seen: Counter[str] = Counter()
    for r in rows:
        ts_seen[r["timestamp"]] += 1
        try:
            datetime.fromisoformat(r["timestamp"])
        except ValueError:
            return False, f"unparseable timestamp {r['timestamp']!r}", 0
    dupes = sum(c - 1 for c in ts_seen.values() if c > 1)
    dates = {r["timestamp"][:10] for r in rows}
    out_of_window = {d for d in dates if not (WINDOW_START.isoformat() <= d <= WINDOW_END.isoformat())}
    if out_of_window:
        return False, f"{len(out_of_window)} dates outside the requested window: {sorted(out_of_window)}", dupes
    if dupes:
        return False, f"{dupes} duplicate timestamps", dupes
    return True, "OK", dupes


def find_bar(rows: list[dict], date_s: str, slot: str) -> dict | None:
    for r in rows:
        if r["timestamp"][:10] == date_s and r["timestamp"][11:19] == slot:
            return r
    return None


def original_bar(security_key: str, glob_pattern: str, date_s: str, slot: str) -> tuple[dict | None, str]:
    candidates = list(ORIGINAL_DATA_DIR.glob(glob_pattern))
    if not candidates:
        return None, "original file not found"
    path = candidates[0]
    with path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            if row["timestamp"][:10] == date_s and row["timestamp"][11:19] == slot:
                return row, path.name
    return None, path.name


def classify_bar_shape(bar: dict, comparison_bar: dict | None) -> str:
    if float(bar.get("volume", 0)) == 0 and bar.get("open") == bar.get("high") == bar.get("low") == bar.get("close"):
        return "ZERO_VOLUME_FLAT (same signature as the INFY-2015-04-24/YESBANK-2015-08-12 data artifacts)"
    if comparison_bar and (bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close"), bar.get("volume")) == \
            (comparison_bar.get("open"), comparison_bar.get("high"), comparison_bar.get("low"),
             comparison_bar.get("close"), comparison_bar.get("volume")):
        return f"EXACT_DUPLICATE_OF_{COMPARISON_SLOT}"
    return "DISTINCT_CANDLE (genuine-looking OHLCV, not a duplicate or flat artifact)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-token", default=os.environ.get("KITE_REQUEST_TOKEN"))
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Plan: {len(AFFECTED_SYMBOLS)} originally-affected + {len(UNAFFECTED_SYMBOLS)} "
          f"originally-unaffected + {CONTROL_SYMBOL} control, window {WINDOW_START}..{WINDOW_END}, "
          f"target date {TARGET_DATE}, target slot {TARGET_SLOT}, output -> {OUT_DIR} "
          f"(diagnostic only, originals never touched)")
    print(f"  AFFECTED (originally had {TARGET_SLOT}):   {AFFECTED_SYMBOLS}")
    print(f"  UNAFFECTED (originally did not):           {UNAFFECTED_SYMBOLS}")
    if args.dry_run:
        print("Dry run only - no authentication, no data acquired.")
        return 0

    kite = dl.authenticate(args.request_token)
    all_nse = {item["tradingsymbol"]: int(item["instrument_token"]) for item in kite.instruments("NSE")}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_rows = []

    all_symbols = [(s, "AFFECTED") for s in AFFECTED_SYMBOLS] + \
                  [(s, "UNAFFECTED") for s in UNAFFECTED_SYMBOLS] + \
                  [(CONTROL_SYMBOL, "CONTROL_UNAFFECTED")]

    for sym, group in all_symbols:
        if sym not in all_nse:
            print(f"[{sym}] SKIP - not resolvable in current NSE instrument list")
            continue
        token = all_nse[sym]
        rows = dl.download_candles(kite, token, WINDOW_START, WINDOW_END, args.interval, chunk_days=30)
        ok, reason, dupes = check_batch(rows)
        tag = group.lower()
        out_path = OUT_DIR / f"DIAGNOSTIC_{tag}_{sym.replace(' ', '_')}_{args.interval}_{WINDOW_START}_{WINDOW_END}.csv"
        if not ok and reason != f"{dupes} duplicate timestamps":
            out_path = out_path.with_name(out_path.stem + "_FAILED_SANITY_CHECK.csv")
        dl.write_csv(out_path, rows)

        fresh_extra_bar = find_bar(rows, TARGET_DATE, TARGET_SLOT)
        fresh_comparison_bar = find_bar(rows, TARGET_DATE, COMPARISON_SLOT)
        regular_slot_count = sum(1 for r in rows if not (r["timestamp"][:10] == TARGET_DATE and r["timestamp"][11:19] == TARGET_SLOT))
        first_ts = rows[0]["timestamp"] if rows else None
        last_ts = rows[-1]["timestamp"] if rows else None

        glob_pattern = f"NSE_{sym}_15minute_*.csv" if sym != CONTROL_SYMBOL else "NSE_NIFTY 50_15minute_*.csv"
        orig_bar, orig_file = original_bar(sym, glob_pattern, TARGET_DATE, TARGET_SLOT)

        shape = None
        if fresh_extra_bar:
            shape = classify_bar_shape(fresh_extra_bar, fresh_comparison_bar)

        row_report = {
            "symbol": sym, "group": group,
            "sanity_check": "OK" if ok else f"FAILED ({reason})",
            "fresh_15:30_present": fresh_extra_bar is not None,
            "original_15:30_present": orig_bar is not None,
            "original_file": orig_file,
            "fresh_regular_slot_count": regular_slot_count,
            "expected_regular_slots": EXPECTED_REGULAR_SLOTS,
            "duplicates": dupes,
            "first_timestamp": first_ts, "last_timestamp": last_ts,
            "fresh_15:30_ohlcv": fresh_extra_bar,
            "original_15:30_ohlcv": orig_bar,
            "bar_shape": shape,
            "output_file": str(out_path),
        }
        report_rows.append(row_report)

        print(f"\n[{sym}] ({group}) sanity_check={row_report['sanity_check']}")
        print(f"    fresh 15:30 present?      {row_report['fresh_15:30_present']}")
        print(f"    original 15:30 present?   {row_report['original_15:30_present']}  ({orig_file})")
        print(f"    fresh regular slots:      {EXPECTED_REGULAR_SLOTS}/{regular_slot_count}")
        print(f"    duplicates:               {dupes}")
        print(f"    first/last timestamp:     {first_ts} .. {last_ts}")
        if fresh_extra_bar:
            print(f"    fresh 15:30 OHLCV:        {fresh_extra_bar}")
            print(f"    fresh 15:15 OHLCV (cmp):  {fresh_comparison_bar}")
            print(f"    bar shape:                {shape}")
        print(f"    -> {out_path}")

    print("\n=== Summary table (cross-checked against original certification report) ===")
    print(f"{'symbol':12s} {'group':20s} {'fresh_1530':11s} {'orig_1530':10s} {'bar_shape':45s}")
    for r in report_rows:
        print(f"{r['symbol']:12s} {r['group']:20s} {str(r['fresh_15:30_present']):11s} "
              f"{str(r['original_15:30_present']):10s} {(r['bar_shape'] or ''):45s}")

    affected_fresh = [r for r in report_rows if r["group"] == "AFFECTED"]
    unaffected_fresh = [r for r in report_rows if r["group"] in ("UNAFFECTED", "CONTROL_UNAFFECTED")]
    affected_still_present = all(r["fresh_15:30_present"] for r in affected_fresh)
    affected_none_present = all(not r["fresh_15:30_present"] for r in affected_fresh)
    unaffected_stayed_clean = all(not r["fresh_15:30_present"] for r in unaffected_fresh)
    unaffected_now_dirty = any(r["fresh_15:30_present"] for r in unaffected_fresh)

    print("\nThis is DIAGNOSTIC ONLY. Nothing in the original acquired dataset was touched.")
    print("No classification or certification-rule change is applied automatically by this script.")
    if unaffected_now_dirty:
        print("\n[CASE C] Fresh 15:30 appeared on a previously-UNAFFECTED symbol or NIFTY ->")
        print("  STOP. Do not conclude anything yet - the historical endpoint may now be serving")
        print("  a different representation than at original-acquisition time. Needs further")
        print("  investigation before any classification.")
    elif affected_still_present and unaffected_stayed_clean:
        print("\n[CASE A] Originally-affected symbols reproduce 15:30 on a fresh pull; unaffected")
        print("  symbols + NIFTY stay clean -> persistent upstream Kite timestamp/session artifact")
        print("  for these specific instruments. This establishes PERSISTENT UPSTREAM DATA")
        print("  BEHAVIOR, NOT legitimate NSE session behavior - those are different claims.")
        print("  Does NOT by itself authorize a documented-exception certification pass - a")
        print("  separate classification/frozen-cleaning-rule decision is needed, made after")
        print("  reviewing this output, not automated here.")
    elif affected_none_present and unaffected_stayed_clean:
        print("\n[CASE B] Originally-affected symbols NO LONGER show 15:30 on a fresh pull ->")
        print("  looks acquisition-version/API-history dependent. Does NOT authorize deleting the")
        print("  original bar automatically - a candidate repair needing a separately-frozen")
        print("  replacement rule, not applied here.")
    else:
        print("\nMIXED WITHIN the affected group itself - inspect each row above individually")
        print("  before drawing any conclusion. Do not average this into a single verdict.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
