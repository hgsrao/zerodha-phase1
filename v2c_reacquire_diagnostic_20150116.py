"""V2-C targeted reacquisition DIAGNOSTIC for 2015-01-16. Read-only.
Imports only download_historical_ohlcv.py's read-only primitives, same
credential boundary as every other historical pull in this project.

Purpose: 2015-01-16 is a CONFIRMED regular_session=TRUE calendar date
(valid trading day per the corrected calendar), yet it is absent from
ALL 70 originally acquired files (69 stock intervals + NIFTY 50) - see
V2C_CALENDAR_DATABASE_CORRECTION_20260818.md "Round 4" for the full
correction trail.

**Supersedes an earlier, wrong characterization of this date** (recorded
in P01D_RRME_INTERIM_STATUS_20260818.md and the calendar-builder's
NEWLY_FLAGGED_UNINVESTIGATED comment as "NIFTY-50-index-only"). That
was based on the top-line certification report, which only showed the
gap for NIFTY because 45 of 69 stock intervals have an edge-truncation
block that silently absorbs 2015-01-16 (their real Kite history starts
later than the requested warmup date anyway) instead of reporting it as
a distinct interior-missing date, and the other 24 stocks' acquisition
windows simply don't reach back to this date. A direct file-by-file
check (not the summarized certification report) is what caught this -
the original "NIFTY-only" statement is superseded, not silently
overwritten; both versions remain in the correction record's revision
history.

Same fail-closed discipline as v2c_reacquire_diagnostic_20160101.py and
v2c_reacquire_ltim_as_ltm.py:
  - Retry data is DIAGNOSTIC ONLY until it independently passes
    timestamp-parse, window, duplicate, and session-integrity checks.
  - NEVER overwrites the original acquired files - writes to a
    dedicated diagnostic subfolder instead.
  - Does not decide the dataset's final disposition by itself - this
    script reports what a fresh pull returns; a human/subsequent step
    decides whether to patch, or to record 2015-01-16 as a persistent
    upstream gap (same as 2016-01-01's outcome).

Sample: the same representative handful of liquid, large-cap symbols
used for the 2016-01-01 diagnostic (HDFCBANK, RELIANCE, INFY, SBIN) -
all four have acquisition windows starting 2014-12-03, well before this
target date - plus NIFTY 50. Unlike the 2016-01-01 diagnostic, NIFTY is
NOT a pre-confirmed control here: its own acquired series is ALSO
missing 2015-01-16 (see module purpose above), so this run treats it as
a genuine second data point on the same question, not a sanity check on
the script.

Narrow window: 2015-01-12..2015-01-20 (a few real trading days on each
side of the target date, a Friday), not the whole multi-year interval.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import download_historical_ohlcv as dl

TARGET_DATE = "2015-01-16"
WINDOW_START = date(2015, 1, 12)
WINDOW_END = date(2015, 1, 20)
SAMPLE_SYMBOLS = ["HDFCBANK", "RELIANCE", "INFY", "SBIN"]
CONTROL_SYMBOL = "NIFTY 50"
OUT_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED/DIAGNOSTIC_REACQUISITION_20260818")
ORIGINAL_DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")


def dates_present(rows: list[dict]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for r in rows:
        counter[r["timestamp"][:10]] += 1
    return dict(counter)


def check_batch(rows: list[dict]) -> tuple[bool, str]:
    """Fail-closed sanity checks on the diagnostic pull itself - not a
    certification pass, just enough to trust what we're comparing."""
    if not rows:
        return False, "empty batch"
    ts_seen: Counter[str] = Counter()
    for r in rows:
        ts_seen[r["timestamp"]] += 1
        try:
            datetime.fromisoformat(r["timestamp"])
        except ValueError:
            return False, f"unparseable timestamp {r['timestamp']!r}"
    dupes = sum(c - 1 for c in ts_seen.values() if c > 1)
    if dupes:
        return False, f"{dupes} duplicate timestamps"
    dates = {r["timestamp"][:10] for r in rows}
    out_of_window = {d for d in dates if not (WINDOW_START.isoformat() <= d <= WINDOW_END.isoformat())}
    if out_of_window:
        return False, f"{len(out_of_window)} dates outside the requested window: {sorted(out_of_window)}"
    return True, "OK"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-token", default=os.environ.get("KITE_REQUEST_TOKEN"))
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Plan: {len(SAMPLE_SYMBOLS)} sample symbols + {CONTROL_SYMBOL}, "
          f"window {WINDOW_START}..{WINDOW_END}, target date {TARGET_DATE}, "
          f"output -> {OUT_DIR} (diagnostic only, originals never touched)")
    if args.dry_run:
        print("Dry run only - no authentication, no data acquired.")
        return 0

    kite = dl.authenticate(args.request_token)
    all_nse = {item["tradingsymbol"]: int(item["instrument_token"]) for item in kite.instruments("NSE")}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for sym in SAMPLE_SYMBOLS + [CONTROL_SYMBOL]:
        if sym not in all_nse:
            print(f"[{sym}] SKIP - not resolvable in current NSE instrument list")
            results.append((sym, "SKIP_UNRESOLVED", {}))
            continue
        token = all_nse[sym]
        rows = dl.download_candles(kite, token, WINDOW_START, WINDOW_END, args.interval, chunk_days=30)
        ok, reason = check_batch(rows)
        tag = "sample" if sym in SAMPLE_SYMBOLS else "control"
        out_path = OUT_DIR / f"DIAGNOSTIC_{tag}_{sym.replace(' ', '_')}_{args.interval}_{WINDOW_START}_{WINDOW_END}.csv"
        if ok:
            dl.write_csv(out_path, rows)
            print(f"[{sym}] OK - {len(rows)} candles, sanity checks passed -> {out_path}")
        else:
            failed_path = out_path.with_name(out_path.stem + "_FAILED_SANITY_CHECK.csv")
            dl.write_csv(failed_path, rows)
            print(f"[{sym}] SANITY CHECK FAILED ({reason}) - wrote anyway for inspection -> {failed_path}")
        d_present = dates_present(rows)
        target_count = d_present.get(TARGET_DATE, 0)
        results.append((sym, "OK" if ok else f"FAILED:{reason}", d_present))
        print(f"    {TARGET_DATE}: {target_count}/25 bars in this fresh pull "
              f"({'PRESENT' if target_count > 0 else 'STILL MISSING'})")

    # Compare against the ORIGINAL acquired files (read-only, never modified).
    print("\n=== Comparison against originally acquired files (read-only) ===")
    for sym in SAMPLE_SYMBOLS:
        candidates = list(ORIGINAL_DATA_DIR.glob(f"NSE_{sym}_15minute_*.csv"))
        if not candidates:
            print(f"[{sym}] original file not found")
            continue
        orig_path = candidates[0]
        orig_target_count = 0
        with orig_path.open(newline="", encoding="utf-8") as h:
            for row in csv.DictReader(h):
                if row["timestamp"][:10] == TARGET_DATE:
                    orig_target_count += 1
        print(f"[{sym}] original file ({orig_path.name}): {orig_target_count}/25 bars on {TARGET_DATE}")
    nifty_candidates = list(ORIGINAL_DATA_DIR.glob("NSE_NIFTY 50_15minute_*.csv"))
    if nifty_candidates:
        orig_target_count = 0
        with nifty_candidates[0].open(newline="", encoding="utf-8") as h:
            for row in csv.DictReader(h):
                if row["timestamp"][:10] == TARGET_DATE:
                    orig_target_count += 1
        print(f"[NIFTY 50] original file ({nifty_candidates[0].name}): {orig_target_count}/25 bars on {TARGET_DATE}")

    print("\nThis is DIAGNOSTIC ONLY. Nothing in the original acquired dataset was touched.")
    print("If the fresh pull ALSO shows 0/25 bars across the whole sample (equities AND NIFTY):")
    print("  record 2015-01-16 as another persistent upstream Kite historical-data gap, do not")
    print("  keep retrying.")
    print("If the fresh pull recovers real bars for the equities: this was an acquisition-time")
    print("  gap, not an upstream one, and a broader targeted repair pull becomes a separate,")
    print("  deliberate next step - not automatic from this script.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
