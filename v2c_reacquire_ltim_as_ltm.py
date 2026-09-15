"""V2-C single-symbol re-acquisition: LTIM, looked up under its current
tradingsymbol LTM. Read-only. No write/order capability - imports only
download_historical_ohlcv.py's read-only primitives, same as
v2c_acquire_15min_dataset.py.

Per V2C_IDENTITY_RESOLUTION_LTIM_LTM_20260818.md: LTIMindtree's trading
symbol changed from LTIM to LTM effective 2026-02-27. The original bulk
acquisition run failed to resolve LTIM against a *current* NSE
instrument-list lookup (kite.instruments("NSE")) for exactly that reason
- not because the historical data is unobtainable. This script re-runs
acquisition for that one security-interval only, looking up the current
symbol LTM but keeping the historical identity label LTIM (security_key)
for output-file/dataset consistency with the rest of V2C_15MIN_DATA_ACQUIRED.

Same historical window as the original requirements-file row, unchanged:
  acquisition_from_with_warmup = 2023-06-14
  acquisition_through          = 2023-07-31
  interval                     = 15minute

Success requires ALL of, in order - a current LTM token resolving is
necessary but NOT sufficient, since that alone does not prove Kite
exposes the historical LTIM period under that token:
  1. LTM token resolves against the current NSE instrument list.
  2. download_candles() returns a non-empty batch.
  3. Returned timestamps parse and are duplicate-free.
  4. The returned dates actually, credibly overlap the required
     2023-06-14..2023-07-31 window (>=95% of distinct returned dates
     in-window, and the batch's min/max dates touch the window) - this
     is the identity-bridge proof itself, not the token lookup.
Only if all four hold: write the output file, append the shared
acquisition summary, and remove LTIM from the unresolved-token file.
Any failure leaves the summary/unresolved-token files untouched.

SCOPE NOTE: the >=95% in-window threshold in step 4 is an
identity-reacquisition ACCEPTANCE rule for this one script only - it
answers "did the LTM token actually hand back LTIM's history", not "is
this data clean enough for V2-C". It must not be read as, or reused as,
a general data-quality tolerance. The main certification pass
(v2c_certify_15min_dataset.py) is and stays strictly separate and
stricter: 0 duplicates, 0 unexplained interior gaps, no threshold-based
partial acceptance anywhere in it. A file that clears this script's
95% bridge check still goes through that full, stricter certification
like every other interval - it gets no special pass here.

Requires the user's own KITE_API_KEY/KITE_ACCESS_TOKEN (or
--request-token) already set in the environment - same credential
boundary as every other historical pull in this project. Not run
automatically, not run by the assistant without an explicit go-ahead
each time; this script only prepares the call, it does not self-invoke.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import download_historical_ohlcv as dl

SECURITY_KEY = "LTIM"
LOOKUP_SYMBOL = "LTM"
FROM_WITH_WARMUP = "2023-06-14"
THROUGH = "2023-07-31"
OUTPUT_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-token", default=os.environ.get("KITE_REQUEST_TOKEN"))
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--chunk-days", type=int, default=180)
    ap.add_argument("--dry-run", action="store_true",
                     help="print the plan only; no authentication, no Kite calls")
    args = ap.parse_args()

    start = date.fromisoformat(FROM_WITH_WARMUP)
    end = date.fromisoformat(THROUGH)
    out_path = OUTPUT_DIR / f"NSE_{SECURITY_KEY}_{args.interval}_{start}_{end}.csv"

    print(f"Plan: security_key={SECURITY_KEY}, lookup_symbol={LOOKUP_SYMBOL}, "
          f"window={start}..{end}, interval={args.interval}, output={out_path}")

    if args.dry_run:
        print("Dry run only - no authentication, no data acquired.")
        return 0

    if out_path.exists():
        print(f"REFUSING TO OVERWRITE: {out_path} already exists. "
              f"Delete/move it first if a genuine re-run is intended.")
        return 1

    kite = dl.authenticate(args.request_token)

    all_nse = {item["tradingsymbol"]: int(item["instrument_token"]) for item in kite.instruments("NSE")}
    if LOOKUP_SYMBOL not in all_nse:
        print(f"FAILED: {LOOKUP_SYMBOL} still not found in the current NSE instrument list. "
              f"Identity resolution assumption (V2C_IDENTITY_RESOLUTION_LTIM_LTM_20260818.md) "
              f"may be stale or wrong - do not fabricate a token, stop here.")
        return 1

    token = all_nse[LOOKUP_SYMBOL]
    print(f"Resolved {LOOKUP_SYMBOL} -> instrument_token {token}. Downloading {start}..{end}...")

    rows_out = dl.download_candles(kite, token, start, end, args.interval, chunk_days=args.chunk_days)
    if not rows_out:
        print(f"FAILED: zero candles returned for {SECURITY_KEY} ({LOOKUP_SYMBOL}) {start}..{end}. "
              f"Not writing an empty file.")
        return 1

    # Fail-closed identity-bridge check. Resolving LTM's *current* token is
    # necessary but not sufficient - it does not by itself prove Kite
    # exposes the *historical* LTIM period under that token. The returned
    # dates themselves have to prove the bridge works, or this is silently
    # accepting whatever Kite happens to hand back (e.g. only-recent data,
    # or a different security's history) as if it were the requested
    # 2023-06-14..2023-07-31 LTIM window.
    parsed_dates = set()
    ts_seen: Counter[str] = Counter()
    for row in rows_out:
        raw = row["timestamp"]
        ts_seen[raw] += 1
        try:
            parsed_dates.add(datetime.fromisoformat(raw).date())
        except ValueError:
            print(f"FAILED: unparseable timestamp {raw!r} in returned candles - refusing to trust this batch.")
            return 1

    duplicate_count = sum(c - 1 for c in ts_seen.values() if c > 1)
    if duplicate_count > 0:
        print(f"FAILED: {duplicate_count} duplicate timestamps in the returned candles - "
              f"refusing to write, not a clean pull.")
        return 1

    in_window = {d for d in parsed_dates if start <= d <= end}
    out_of_window = parsed_dates - in_window
    coverage_ratio = len(in_window) / len(parsed_dates) if parsed_dates else 0.0

    print(f"Returned {len(rows_out)} candles spanning {len(parsed_dates)} distinct dates "
          f"({min(parsed_dates)}..{max(parsed_dates)}). "
          f"{len(in_window)} dates fall inside the required window {start}..{end}, "
          f"{len(out_of_window)} do not.")

    # The bar with the earliest/latest date must actually sit inside (or at
    # the very edge of) the required window, and the overwhelming majority
    # of returned dates must be in-window. A handful of stray out-of-window
    # dates (e.g. a chunk-boundary artifact) is tolerated; a batch that is
    # mostly or entirely outside the required window means the token
    # resolved to something else's history, not proof the identity bridge
    # holds, and must not be accepted.
    if coverage_ratio < 0.95 or min(parsed_dates) > end or max(parsed_dates) < start:
        print(f"FAILED: returned candle dates do not credibly overlap the required "
              f"LTIM historical window {start}..{end} (in-window coverage "
              f"{coverage_ratio:.1%}). This does NOT prove the identity bridge works - "
              f"refusing to write, refusing to touch the summary/unresolved-token files.")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dl.write_csv(out_path, rows_out)
    print(f"{len(rows_out)} candles -> {out_path}")
    print(f"Identity-bridge check PASSED: {coverage_ratio:.1%} of returned dates fall inside "
          f"the required {start}..{end} window, 0 duplicate timestamps.")

    # Append to the shared acquisition summary so certification picks it up,
    # rather than requiring a separate re-run of the whole bulk script.
    summary_path = OUTPUT_DIR / "V2C_15MIN_ACQUISITION_SUMMARY.csv"
    row = [SECURITY_KEY, LOOKUP_SYMBOL, str(start), str(end), len(rows_out)]
    write_header = not summary_path.exists()
    with summary_path.open("a", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        if write_header:
            w.writerow(["security_key", "symbol_at_interval_start", "from", "through", "candles"])
        w.writerow(row)
    print(f"Appended to {summary_path}: {row}")

    # Remove LTIM from the unresolved-token skip file, if present - it is
    # no longer unresolved. Rewrite rather than edit in place so the file
    # stays a clean CSV.
    skip_path = OUTPUT_DIR / "V2C_15MIN_ACQUISITION_SKIPPED_UNRESOLVED_TOKEN.csv"
    if skip_path.exists():
        with skip_path.open(newline="", encoding="utf-8") as h:
            remaining = [r for r in csv.DictReader(h) if r["symbol_at_interval_start"] != SECURITY_KEY]
        with skip_path.open("w", newline="", encoding="utf-8") as h:
            w = csv.writer(h)
            w.writerow(["symbol_at_interval_start", "reason"])
            for r in remaining:
                w.writerow([r["symbol_at_interval_start"], r["reason"]])
        print(f"Removed {SECURITY_KEY} from {skip_path} ({len(remaining)} symbols still unresolved).")

    print("\nThis is ACQUISITION ONLY for the one LTIM/LTM interval. Certification "
          "(missing/duplicate-bar audit, corporate-action check, hash-freeze) is the "
          "separate next step, and must still cover the whole dataset, not just this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
