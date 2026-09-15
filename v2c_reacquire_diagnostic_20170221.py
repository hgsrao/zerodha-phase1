"""V2-C targeted reacquisition DIAGNOSTIC for 2017-02-21. Read-only.
Imports only download_historical_ohlcv.py's read-only primitives, same
credential boundary as every other historical pull in this project.

Purpose: 2017-02-21 affects only NIFTY 50's own series - exactly the
11:15 bar-open slot missing (1/25 bars), confirmed genuinely
NIFTY-specific in Round 4/5: all 47 stock intervals whose acquisition
window actually covers this date have complete data; the other 22
simply don't cover it at all. No independent source found for this
date. Same two-group discipline as the other diagnostics, adapted for
a single-instrument (index-only) question - the "affected" group here
is just NIFTY 50 itself; the "unaffected" group is ordinary stocks
already confirmed to have full data for this date, used as controls to
confirm nothing broader is happening.

AFFECTED: NIFTY 50 (the only originally-affected series, missing 11:15)
UNAFFECTED controls: HDFCBANK, INFY (confirmed via direct file check to
  have the 11:15 slot present for this date)

Narrow window: 2017-02-17..2017-02-23 (5 real trading days, the target
date - a Tuesday - roughly centered).

Same fail-closed discipline and reporting shape as the other slot-level
diagnostics: fresh/original presence per target slot, expected/returned
regular-slot counts, duplicates, first/last timestamp. Diagnostic only
- never overwrites originals, never patches or classifies anything by
itself.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import download_historical_ohlcv as dl

TARGET_DATE = "2017-02-21"
TARGET_SLOT = "11:15:00"
WINDOW_START = date(2017, 2, 17)
WINDOW_END = date(2017, 2, 23)
AFFECTED_SYMBOL = "NIFTY 50"
UNAFFECTED_SYMBOLS = ["HDFCBANK", "INFY"]
OUT_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED/DIAGNOSTIC_REACQUISITION_20260818")
ORIGINAL_DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
# 5 real trading days x 25 slots/session.
EXPECTED_SLOTS = 5 * 25


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


def original_target_slot_present(glob_pattern: str) -> tuple[bool | None, str]:
    candidates = list(ORIGINAL_DATA_DIR.glob(glob_pattern))
    if not candidates:
        return None, "original file not found"
    path = candidates[0]
    present = False
    with path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            if row["timestamp"][:10] == TARGET_DATE and row["timestamp"][11:19] == TARGET_SLOT:
                present = True
                break
    return present, path.name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-token", default=os.environ.get("KITE_REQUEST_TOKEN"))
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Plan: 1 originally-affected (NIFTY 50) + {len(UNAFFECTED_SYMBOLS)} "
          f"originally-unaffected controls, window {WINDOW_START}..{WINDOW_END}, "
          f"target date {TARGET_DATE}, target slot {TARGET_SLOT}, output -> {OUT_DIR} "
          f"(diagnostic only, originals never touched)")
    if args.dry_run:
        print("Dry run only - no authentication, no data acquired.")
        return 0

    kite = dl.authenticate(args.request_token)
    all_nse = {item["tradingsymbol"]: int(item["instrument_token"]) for item in kite.instruments("NSE")}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_rows = []

    all_symbols = [(AFFECTED_SYMBOL, "AFFECTED")] + [(s, "UNAFFECTED") for s in UNAFFECTED_SYMBOLS]

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

        fresh_present = any(r["timestamp"][:10] == TARGET_DATE and r["timestamp"][11:19] == TARGET_SLOT for r in rows)
        first_ts = rows[0]["timestamp"] if rows else None
        last_ts = rows[-1]["timestamp"] if rows else None

        glob_pattern = "NSE_NIFTY 50_15minute_*.csv" if sym == AFFECTED_SYMBOL else f"NSE_{sym}_15minute_*.csv"
        orig_present, orig_file = original_target_slot_present(glob_pattern)

        row_report = {
            "symbol": sym, "group": group,
            "sanity_check": "OK" if ok else f"FAILED ({reason})",
            "fresh_11:15_present": fresh_present,
            "original_11:15_present": orig_present,
            "original_file": orig_file,
            "fresh_expected_slots": EXPECTED_SLOTS,
            "fresh_returned_slots": len(rows),
            "duplicates": dupes,
            "first_timestamp": first_ts, "last_timestamp": last_ts,
            "output_file": str(out_path),
        }
        report_rows.append(row_report)

        print(f"\n[{sym}] ({group}) sanity_check={row_report['sanity_check']}")
        print(f"    fresh 11:15 present?      {fresh_present}")
        print(f"    original 11:15 present?   {orig_present}  ({orig_file})")
        print(f"    fresh expected/returned:  {EXPECTED_SLOTS}/{len(rows)}")
        print(f"    duplicates:               {dupes}")
        print(f"    first/last timestamp:     {first_ts} .. {last_ts}")
        print(f"    -> {out_path}")

    print("\n=== Summary table ===")
    print(f"{'symbol':12s} {'group':12s} {'fresh_1115':11s} {'orig_1115':10s} {'exp/ret':9s} {'dupes':6s}")
    for r in report_rows:
        print(f"{r['symbol']:12s} {r['group']:12s} {str(r['fresh_11:15_present']):11s} "
              f"{str(r['original_11:15_present']):10s} {EXPECTED_SLOTS}/{r['fresh_returned_slots']:<6d} {r['duplicates']}")

    nifty_row = next((r for r in report_rows if r["symbol"] == AFFECTED_SYMBOL), None)
    controls_clean = all(r["fresh_11:15_present"] for r in report_rows if r["group"] == "UNAFFECTED")
    print("\nThis is DIAGNOSTIC ONLY. Nothing in the original acquired dataset was touched.")
    if nifty_row and not nifty_row["fresh_11:15_present"] and controls_clean:
        print("NIFTY 50 still missing 11:15 in the fresh pull; unaffected stock controls stay")
        print("  clean -> persistent upstream Kite gap, index-specific, single-slot, same")
        print("  evidentiary structure as the other CONFIRMED_UPSTREAM_DATA_GAP dates.")
    elif nifty_row and nifty_row["fresh_11:15_present"]:
        print("NIFTY 50 RECOVERED 11:15 in the fresh pull -> candidate acquisition-time/")
        print("  transient defect, NOT auto-patched. Do not touch the certified dataset until")
        print("  a frozen replacement/merge rule is defined.")
    if not controls_clean:
        print("WARNING: an unaffected control lost its 11:15 slot in the fresh pull - STOP,")
        print("  investigate further before any classification.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
