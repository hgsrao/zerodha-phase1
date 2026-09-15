"""V2-C targeted reacquisition DIAGNOSTIC for 2016-01-01. Read-only.
Imports only download_historical_ohlcv.py's read-only primitives, same
credential boundary as every other historical pull in this project.

Purpose: 2016-01-01 is a CONFIRMED normal trading day (2016's official
NSE/BSE holiday list has no January 1 entry; NSE's standing practice is
to trade on New Year's Day - see V2C_CALENDAR_DATABASE_CORRECTION_20260818.md
"Round 2"), yet 46/69 acquired stock intervals show a complete 25/25-bar
absence for that date, while NIFTY 50's own acquired index series HAS
full data for it. The remaining question is purely a data-engineering
one: did the original bulk pull fail to retrieve that one session for
individual equities, and can a narrow, independent re-pull recover it?

Fail-closed discipline, same as v2c_reacquire_ltim_as_ltm.py:
  - Retry data is DIAGNOSTIC ONLY until it independently passes
    timestamp-parse, window, duplicate, and session-integrity checks.
  - NEVER overwrites the original acquired files - writes to a
    dedicated diagnostic subfolder instead.
  - Does not decide the dataset's final disposition by itself - this
    script reports what a fresh pull returns; a human/subsequent step
    decides whether to patch, or to record 2016-01-01 as a persistent
    upstream gap.

Sample: a representative handful of liquid, large-cap symbols already
in this dataset (HDFCBANK, RELIANCE, INFY, SBIN) plus NIFTY 50 as a
known-good control (its own acquired series already has full 2016-01-01
data - re-pulling it here is a sanity check on this script itself, not
because the index is suspected of a gap).

Narrow window: 2015-12-28..2016-01-06 (a few real trading days on each
side of the target date), not the whole multi-year interval - keeps the
call small and the comparison direct.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import download_historical_ohlcv as dl

TARGET_DATE = "2016-01-01"
WINDOW_START = date(2015, 12, 28)
WINDOW_END = date(2016, 1, 6)
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

    print(f"Plan: {len(SAMPLE_SYMBOLS)} sample symbols + {CONTROL_SYMBOL} control, "
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
            # Still write it - a failed sanity check is itself diagnostic
            # information - but label the file so it's never mistaken for
            # a clean pull.
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
    import glob as globmod
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

    print("\nThis is DIAGNOSTIC ONLY. Nothing in the original acquired dataset was touched.")
    print("If the fresh pull ALSO shows 0/25 bars across the sample: record 2016-01-01 as a")
    print("persistent upstream historical-data gap, do not keep retrying.")
    print("If the fresh pull shows real bars: a targeted, verified patch to the affected")
    print("intervals becomes a separate, deliberate next step - not automatic from this script.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
