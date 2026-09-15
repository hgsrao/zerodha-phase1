"""V2-C targeted reacquisition DIAGNOSTIC for 2023-07-20. Read-only.
Imports only download_historical_ohlcv.py's read-only primitives, same
credential boundary as every other historical pull in this project.

Purpose: 2023-07-20 affects exactly ONE of 69 acquired intervals -
RELIANCE, missing exactly the first three bar-opens of the day (09:15,
09:30, 09:45), present from 10:00 onward (see
V2C_CALENDAR_DATABASE_CORRECTION_20260818.md "Round 4"). Already
confirmed in the corporate-action audit: no action found for RELIANCE
on this date. Lowest priority of the residual dates given its narrow
blast radius, but worth the same rigor - is this a genuine single-
symbol upstream gap (fresh pull reproduces it) or an acquisition-time
artifact (fresh pull recovers it)?

TWO GROUPS, drawn from the actual certification report (not guessed):
  AFFECTED   (originally missing 09:15/09:30/09:45): RELIANCE (the only one)
  UNAFFECTED (same acquisition window, not affected): HDFCBANK, INFY, TCS
  CONTROL: NIFTY 50 (confirmed unaffected - this date affects 1/69
           stock intervals only, not the index)

Narrow window: 2023-07-17..2023-07-21 (5 real trading days, the target
date roughly centered).

Same fail-closed discipline and reporting shape as the other slot-level
diagnostics (2015-03-16, 2015-12-22): fresh/original presence per
target slot, expected/returned regular-slot counts, duplicates,
first/last timestamp. Diagnostic only - never overwrites originals,
never patches or classifies anything by itself.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import download_historical_ohlcv as dl

TARGET_DATE = "2023-07-20"
TARGET_SLOTS = ["09:15:00", "09:30:00", "09:45:00"]
WINDOW_START = date(2023, 7, 17)
WINDOW_END = date(2023, 7, 21)
AFFECTED_SYMBOLS = ["RELIANCE"]
UNAFFECTED_SYMBOLS = ["HDFCBANK", "INFY", "TCS"]
CONTROL_SYMBOL = "NIFTY 50"
OUT_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED/DIAGNOSTIC_REACQUISITION_20260818")
ORIGINAL_DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
# 5 real trading days x 25 regular slots.
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


def slots_present(rows: list[dict], date_s: str, slots: list[str]) -> dict[str, bool]:
    present_times = {r["timestamp"][11:19] for r in rows if r["timestamp"][:10] == date_s}
    return {s: s in present_times for s in slots}


def original_slots_present(security_key: str, glob_pattern: str, date_s: str, slots: list[str]) -> tuple[dict[str, bool] | None, str]:
    candidates = list(ORIGINAL_DATA_DIR.glob(glob_pattern))
    if not candidates:
        return None, "original file not found"
    path = candidates[0]
    present_times = set()
    with path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            if row["timestamp"][:10] == date_s:
                present_times.add(row["timestamp"][11:19])
    return {s: s in present_times for s in slots}, path.name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-token", default=os.environ.get("KITE_REQUEST_TOKEN"))
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Plan: {len(AFFECTED_SYMBOLS)} originally-affected + {len(UNAFFECTED_SYMBOLS)} "
          f"originally-unaffected + {CONTROL_SYMBOL} control, window {WINDOW_START}..{WINDOW_END}, "
          f"target date {TARGET_DATE}, target slots {TARGET_SLOTS}, output -> {OUT_DIR} "
          f"(diagnostic only, originals never touched)")
    print(f"  AFFECTED (originally missing these slots): {AFFECTED_SYMBOLS}")
    print(f"  UNAFFECTED (originally had full data):     {UNAFFECTED_SYMBOLS}")
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

        fresh_slots = slots_present(rows, TARGET_DATE, TARGET_SLOTS)
        first_ts = rows[0]["timestamp"] if rows else None
        last_ts = rows[-1]["timestamp"] if rows else None

        glob_pattern = f"NSE_{sym}_15minute_*.csv" if sym != CONTROL_SYMBOL else "NSE_NIFTY 50_15minute_*.csv"
        orig_slots, orig_file = original_slots_present(sym, glob_pattern, TARGET_DATE, TARGET_SLOTS)

        row_report = {
            "symbol": sym, "group": group,
            "sanity_check": "OK" if ok else f"FAILED ({reason})",
            "fresh_slots_present": fresh_slots,
            "original_slots_present": orig_slots,
            "original_file": orig_file,
            "fresh_returned_count": len(rows),
            "expected_regular_slots": EXPECTED_REGULAR_SLOTS,
            "duplicates": dupes,
            "first_timestamp": first_ts, "last_timestamp": last_ts,
            "output_file": str(out_path),
        }
        report_rows.append(row_report)

        print(f"\n[{sym}] ({group}) sanity_check={row_report['sanity_check']}")
        for slot in TARGET_SLOTS:
            print(f"    {slot}: fresh={fresh_slots[slot]}  original={orig_slots[slot] if orig_slots else 'N/A'}")
        print(f"    fresh expected/returned: {EXPECTED_REGULAR_SLOTS}/{len(rows)}")
        print(f"    duplicates:              {dupes}")
        print(f"    first/last timestamp:    {first_ts} .. {last_ts}  ({orig_file})")
        print(f"    -> {out_path}")

    print("\n=== Summary table (cross-checked against original certification report) ===")
    header = f"{'symbol':10s} {'group':20s}" + "".join(f" {s[:5]}(f/o)" for s in TARGET_SLOTS)
    print(header)
    for r in report_rows:
        cells = "".join(
            f" {str(r['fresh_slots_present'][s])[0]}/{str(r['original_slots_present'][s] if r['original_slots_present'] else '?')[0]:6s}"
            for s in TARGET_SLOTS
        )
        print(f"{r['symbol']:10s} {r['group']:20s}{cells}")

    affected_rows = [r for r in report_rows if r["group"] == "AFFECTED"]
    unaffected_rows = [r for r in report_rows if r["group"] in ("UNAFFECTED", "CONTROL_UNAFFECTED")]
    affected_still_missing = all(
        not any(r["fresh_slots_present"].values()) for r in affected_rows
    )
    unaffected_all_clean = all(
        all(r["fresh_slots_present"].values()) for r in unaffected_rows
    )
    print("\nThis is DIAGNOSTIC ONLY. Nothing in the original acquired dataset was touched.")
    if affected_still_missing and unaffected_all_clean:
        print("RELIANCE still missing all 3 target slots in the fresh pull; unaffected symbols +")
        print("  NIFTY have full data -> persistent upstream Kite gap, single-symbol, same")
        print("  evidentiary structure as the other CONFIRMED_UPSTREAM_DATA_GAP dates.")
    elif not affected_still_missing:
        print("RELIANCE RECOVERED some/all target slots in the fresh pull -> candidate")
        print("  acquisition-time/transient defect, NOT auto-patched. Do not touch the certified")
        print("  dataset until a frozen replacement/merge rule is defined.")
    if not unaffected_all_clean:
        print("WARNING: an originally-unaffected symbol or NIFTY shows a gap in the fresh pull -")
        print("  STOP, do not conclude anything, investigate further before any classification.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
