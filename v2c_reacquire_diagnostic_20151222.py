"""V2-C targeted reacquisition DIAGNOSTIC for 2015-12-22. Read-only.
Imports only download_historical_ohlcv.py's read-only primitives, same
credential boundary as every other historical pull in this project.

Purpose: 2015-12-22 shows the same kind of precise pattern as
2015-03-16 - exactly the 10:30 bar-open slot missing, uniformly, across
all 20 affected files (see V2C_CALENDAR_DATABASE_CORRECTION_20260818.md
"Round 4"/"Round 6"). An external web-search pass already ran and found
no documented NSE incident on this date. Same method as 2015-03-16,
chosen over further search for the same reason: a fresh pull can
distinguish two explanations that search cannot:
  A. Persistent upstream Kite gap: fresh pull ALSO misses 10:30 while
     later bars are present - same evidentiary structure as
     2015-03-16/2016-01-01/2015-01-16, slot-level like 2015-03-16.
  B. Original acquisition defect / transient API issue: fresh pull
     RECOVERS 10:30 - a candidate repair operation, NOT auto-applied;
     stops here pending a separately-defined replacement/merge rule.

Fail-closed discipline, same as every other diagnostic in this project:
  - Retry data is DIAGNOSTIC ONLY until it independently passes
    timestamp-parse, window, and duplicate checks.
  - NEVER overwrites the original acquired files - writes to a
    dedicated diagnostic subfolder instead.
  - Does not patch anything by itself - reports what a fresh pull
    returns; a human/subsequent step decides the disposition.

Sample: HDFCBANK, RELIANCE, INFY, SBIN (all four have real Kite history
starting 2015-02-02, well before this target date) plus NIFTY 50.

Narrow window: 2015-12-18..2015-12-24 (a few real trading days on each
side of the target date, a Tuesday).

Report, per instrument, exactly the fields used for 2015-03-16:
  fresh 10:30 present? / original 10:30 present? /
  fresh total expected slots / fresh returned slots /
  duplicates / first timestamp / last timestamp
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import download_historical_ohlcv as dl

TARGET_DATE = "2015-12-22"
TARGET_SLOT = "10:30:00"
WINDOW_START = date(2015, 12, 18)
WINDOW_END = date(2015, 12, 24)
SAMPLE_SYMBOLS = ["HDFCBANK", "RELIANCE", "INFY", "SBIN"]
CONTROL_SYMBOL = "NIFTY 50"
OUT_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED/DIAGNOSTIC_REACQUISITION_20260818")
ORIGINAL_DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
# 5 real trading days (Fri, Mon, Tue, Wed, Thu) x 25 slots/session.
EXPECTED_SLOTS = 5 * 25


def check_batch(rows: list[dict]) -> tuple[bool, str, int]:
    """Fail-closed sanity checks. Returns (ok, reason, duplicate_count)."""
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


def original_target_slot_present(security_key: str, glob_pattern: str) -> tuple[bool | None, str]:
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

    print(f"Plan: {len(SAMPLE_SYMBOLS)} sample symbols + {CONTROL_SYMBOL}, "
          f"window {WINDOW_START}..{WINDOW_END}, target date {TARGET_DATE}, "
          f"target slot {TARGET_SLOT}, output -> {OUT_DIR} (diagnostic only, "
          f"originals never touched)")
    if args.dry_run:
        print("Dry run only - no authentication, no data acquired.")
        return 0

    kite = dl.authenticate(args.request_token)
    all_nse = {item["tradingsymbol"]: int(item["instrument_token"]) for item in kite.instruments("NSE")}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_rows = []

    for sym in SAMPLE_SYMBOLS + [CONTROL_SYMBOL]:
        if sym not in all_nse:
            print(f"[{sym}] SKIP - not resolvable in current NSE instrument list")
            continue
        token = all_nse[sym]
        rows = dl.download_candles(kite, token, WINDOW_START, WINDOW_END, args.interval, chunk_days=30)
        ok, reason, dupes = check_batch(rows)
        tag = "sample" if sym in SAMPLE_SYMBOLS else "control"
        out_path = OUT_DIR / f"DIAGNOSTIC_{tag}_{sym.replace(' ', '_')}_{args.interval}_{WINDOW_START}_{WINDOW_END}.csv"
        if not ok and reason != f"{dupes} duplicate timestamps":
            out_path = out_path.with_name(out_path.stem + "_FAILED_SANITY_CHECK.csv")
        dl.write_csv(out_path, rows)

        fresh_present = any(r["timestamp"][:10] == TARGET_DATE and r["timestamp"][11:19] == TARGET_SLOT for r in rows)
        first_ts = rows[0]["timestamp"] if rows else None
        last_ts = rows[-1]["timestamp"] if rows else None

        glob_pattern = f"NSE_{sym}_15minute_*.csv" if sym != CONTROL_SYMBOL else "NSE_NIFTY 50_15minute_*.csv"
        orig_present, orig_file = original_target_slot_present(sym, glob_pattern)

        row_report = {
            "symbol": sym,
            "sanity_check": "OK" if ok else f"FAILED ({reason})",
            "fresh_10:30_present": fresh_present,
            "original_10:30_present": orig_present,
            "original_file": orig_file,
            "fresh_expected_slots": EXPECTED_SLOTS,
            "fresh_returned_slots": len(rows),
            "duplicates": dupes,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "output_file": str(out_path),
        }
        report_rows.append(row_report)

        print(f"\n[{sym}] sanity_check={row_report['sanity_check']}")
        print(f"    fresh 10:30 present?      {fresh_present}")
        print(f"    original 10:30 present?   {orig_present}  ({orig_file})")
        print(f"    fresh expected/returned:  {EXPECTED_SLOTS}/{len(rows)}")
        print(f"    duplicates:               {dupes}")
        print(f"    first/last timestamp:     {first_ts} .. {last_ts}")
        print(f"    -> {out_path}")

    print("\n=== Summary table ===")
    print(f"{'symbol':12s} {'fresh_1030':11s} {'orig_1030':10s} {'exp/ret':9s} {'dupes':6s}")
    for r in report_rows:
        print(f"{r['symbol']:12s} {str(r['fresh_10:30_present']):11s} {str(r['original_10:30_present']):10s} "
              f"{EXPECTED_SLOTS}/{r['fresh_returned_slots']:<6d} {r['duplicates']}")

    equities_missing_fresh = [r for r in report_rows if r["symbol"] != CONTROL_SYMBOL and r["fresh_10:30_present"] is False]
    equities_recovered_fresh = [r for r in report_rows if r["symbol"] != CONTROL_SYMBOL and r["fresh_10:30_present"] is True]
    print("\nThis is DIAGNOSTIC ONLY. Nothing in the original acquired dataset was touched.")
    if len(equities_missing_fresh) == len(SAMPLE_SYMBOLS):
        print("ALL sampled equities STILL missing 10:30 in the fresh pull ->")
        print("  strong evidence of a persistent upstream Kite slot-level defect (same")
        print("  evidentiary structure as 2015-03-16). Check the NIFTY 50 result above too")
        print("  before concluding.")
    elif len(equities_recovered_fresh) == len(SAMPLE_SYMBOLS):
        print("ALL sampled equities RECOVERED 10:30 in the fresh pull ->")
        print("  candidate acquisition-time/transient defect, NOT auto-patched. Do not touch")
        print("  the certified dataset until a frozen replacement/merge rule is defined.")
    else:
        print("MIXED result across the sample - inspect per-symbol rows above individually")
        print("  before drawing any conclusion.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
