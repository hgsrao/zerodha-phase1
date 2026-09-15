"""Read-only V2-C 15-minute historical dataset acquisition.

Drives the existing, already-tested download_historical_ohlcv.py primitives
(authenticate/resolve_tokens/download_candles/write_csv - imported, not
duplicated) against the pre-built, point-in-time acquisition requirements
file:

  P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_REFERENCE/
    V2C_15MIN_ACQUISITION_REQUIREMENTS_WITH_WARMUP_WORKING_2015_2023.csv

78 security-interval rows, 2014-12-03 through 2023-07-31, each with its
own warmup-adjusted acquisition window (20 trading sessions of pre-
membership warmup already baked into `acquisition_from_with_warmup` by
the prior session that built this file - not re-derived here).

Per P01D_V2C_DATASET_CERTIFICATION_GATE_20260818.md: this script performs
ACQUISITION only. It does not certify the result (missing/duplicate-bar
audit, corporate-action check, hash-freeze) and does not touch epoch
boundaries, labels, or TRAIN - those are separate, later steps.

Explicit no-fabrication rule, carried over from the requirements file
itself: any row whose `identity_note` is non-empty (currently: the
SSLT/VEDL exchange-symbol transition, explicitly marked "unresolved; do
not fabricate alias date") is SKIPPED, never guessed at, and written to
its own flagged-for-review file instead of being silently acquired with
an invented date boundary.

Uses `symbol_at_interval_start` for instrument-token resolution (the real
NSE tradingsymbol for that window), not `security_key` (a conceptual
identity label spanning renames - not itself a valid lookup symbol).

No KiteConnect write/order capability - imports only download_historical_
ohlcv.py's read-only primitives. Requires the user's own KITE_API_KEY/
KITE_ACCESS_TOKEN (or --request-token), same credential boundary as every
other historical pull in this project - not run automatically, not run
by the assistant.
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import date
from pathlib import Path

import download_historical_ohlcv as dl

REQUIREMENTS_CSV = (
    "P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_REFERENCE/"
    "V2C_15MIN_ACQUISITION_REQUIREMENTS_WITH_WARMUP_WORKING_2015_2023.csv"
)
NIFTY_SYMBOL = "NIFTY 50"


def load_requirements(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def split_acquirable(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    acquirable, flagged = [], []
    for r in rows:
        if (r.get("identity_note") or "").strip():
            flagged.append(r)
        else:
            acquirable.append(r)
    return acquirable, flagged


def write_flagged(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) + ["skip_reason"])
        writer.writeheader()
        for r in rows:
            row = dict(r)
            row["skip_reason"] = "IDENTITY_UNRESOLVED_NOT_FABRICATED: " + r["identity_note"]
            writer.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-token", default=os.environ.get("KITE_REQUEST_TOKEN"))
    ap.add_argument("--output-dir", default="P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
    ap.add_argument("--requirements-csv", default=REQUIREMENTS_CSV)
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--chunk-days", type=int, default=180,
                     help="conservative, under Kite's documented ~200-day cap for 15minute; "
                          "the API will raise a clear error if this assumption is wrong, "
                          "rather than silently truncating")
    ap.add_argument("--include-nifty", action="store_true", default=True)
    ap.add_argument("--skip-nifty", dest="include_nifty", action="store_false")
    ap.add_argument("--dry-run", action="store_true",
                     help="resolve requirements and print the acquisition plan; "
                          "no authentication, no Kite calls")
    args = ap.parse_args()

    rows = load_requirements(args.requirements_csv)
    acquirable, flagged = split_acquirable(rows)

    output_dir = Path(args.output_dir)
    flagged_path = output_dir / "V2C_15MIN_ACQUISITION_SKIPPED_IDENTITY_UNRESOLVED.csv"
    write_flagged(flagged_path, flagged)

    print(f"Requirements: {len(rows)} rows -> {len(acquirable)} acquirable, "
          f"{len(flagged)} skipped (identity unresolved, written to {flagged_path})")

    if args.dry_run:
        earliest = min(r["acquisition_from_with_warmup"] for r in acquirable)
        latest = max(r["acquisition_through"] for r in acquirable)
        print(f"Would acquire {len(acquirable)} security-intervals, "
              f"{earliest} .. {latest}, interval={args.interval}, "
              f"chunk_days={args.chunk_days}, output_dir={output_dir}")
        if args.include_nifty:
            print(f"Would also acquire {NIFTY_SYMBOL} over the same full span.")
        print("Dry run only - no authentication, no data acquired.")
        return 0

    kite = dl.authenticate(args.request_token)

    unique_symbols = sorted({r["symbol_at_interval_start"] for r in acquirable})
    print(f"Resolving instrument tokens for {len(unique_symbols)} unique tradingsymbols...")
    tokens: dict[str, int] = {}
    unresolved: list[str] = []
    all_nse = {item["tradingsymbol"]: int(item["instrument_token"]) for item in kite.instruments("NSE")}
    for sym in unique_symbols:
        if sym in all_nse:
            tokens[sym] = all_nse[sym]
        else:
            unresolved.append(sym)

    if unresolved:
        unresolved_path = output_dir / "V2C_15MIN_ACQUISITION_SKIPPED_UNRESOLVED_TOKEN.csv"
        output_dir.mkdir(parents=True, exist_ok=True)
        with unresolved_path.open("w", newline="", encoding="utf-8") as handle:
            w = csv.writer(handle)
            w.writerow(["symbol_at_interval_start", "reason"])
            for sym in unresolved:
                w.writerow([sym, "NOT_IN_CURRENT_NSE_INSTRUMENT_LIST"])
        print(f"WARNING: {len(unresolved)} symbols could not be resolved against the "
              f"current NSE instrument list (likely delisted/renamed since 2023-07-31) "
              f"-> {unresolved_path}. These rows are skipped, not fabricated.")

    print(f"Resolved {len(tokens)} of {len(unique_symbols)} symbols. Beginning acquisition...")

    results = []
    for i, r in enumerate(acquirable, 1):
        sym = r["symbol_at_interval_start"]
        if sym not in tokens:
            continue
        start = date.fromisoformat(r["acquisition_from_with_warmup"])
        end = date.fromisoformat(r["acquisition_through"])
        print(f"[{i}/{len(acquirable)}] {r['security_key']} ({sym}) {start}..{end}")
        rows_out = dl.download_candles(kite, tokens[sym], start, end, args.interval, chunk_days=args.chunk_days)
        if not rows_out:
            print(f"  WARNING: zero candles returned for {r['security_key']} ({sym}) {start}..{end}")
            results.append((r["security_key"], sym, start, end, 0))
            continue
        path = output_dir / f"NSE_{r['security_key']}_{args.interval}_{start}_{end}.csv"
        dl.write_csv(path, rows_out)
        print(f"  {len(rows_out)} candles -> {path}")
        results.append((r["security_key"], sym, start, end, len(rows_out)))

    if args.include_nifty:
        earliest = min(date.fromisoformat(r["acquisition_from_with_warmup"]) for r in acquirable)
        latest = max(date.fromisoformat(r["acquisition_through"]) for r in acquirable)
        print(f"Acquiring {NIFTY_SYMBOL} {earliest}..{latest} ...")
        nifty_tokens = {
            item["tradingsymbol"]: int(item["instrument_token"])
            for item in kite.instruments("NSE")
            if item.get("tradingsymbol") == NIFTY_SYMBOL
        }
        if NIFTY_SYMBOL not in nifty_tokens:
            print(f"  WARNING: {NIFTY_SYMBOL} instrument token not found - skipped.")
        else:
            nifty_rows = dl.download_candles(
                kite, nifty_tokens[NIFTY_SYMBOL], earliest, latest, args.interval, chunk_days=args.chunk_days
            )
            nifty_path = output_dir / f"NSE_{NIFTY_SYMBOL}_{args.interval}_{earliest}_{latest}.csv"
            dl.write_csv(nifty_path, nifty_rows)
            print(f"  {len(nifty_rows)} candles -> {nifty_path}")

    summary_path = output_dir / "V2C_15MIN_ACQUISITION_SUMMARY.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(["security_key", "symbol_at_interval_start", "from", "through", "candles"])
        for row in results:
            w.writerow(row)

    print(f"\nDone. {len(results)} security-intervals attempted, summary -> {summary_path}")
    print("This is ACQUISITION ONLY. Certification (missing/duplicate-bar audit, "
          "corporate-action check, hash-freeze) is the separate next step per "
          "P01D_V2C_DATASET_CERTIFICATION_GATE_20260818.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
