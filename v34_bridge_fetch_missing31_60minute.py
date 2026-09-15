"""Real Zerodha historical-data fetch - 60-minute bars, for the 31
symbols just added to portfolio_brain_v9.SECTORS that don't have data
yet.

Standalone, manually-run, same credential discipline as every other
real-Kite script in this project: read from the environment only, never
handled by any AI assistant, never logged.

WHY 60-MINUTE, WHY THIS DATE RANGE: external_momentum_shadow.py (the
real, live V11 signal path) reads 60-minute bars from
historical_data_60minute_extended_ready/ (or its two fallback
directories) - not the 15-minute data fetched earlier today, which
feeds a different, unrelated path. Existing symbols in that folder go
back to 2016-01-01 (confirmed from their own filenames); this fetch
matches that same depth so the new symbols aren't a shorter, weaker
history sitting next to a decade of the old ones.

OUTPUT: a NEW staging folder (historical_data_60minute_new31_raw/),
NOT written directly into historical_data_60minute_extended_ready/ -
prepare_research_ohlcv.py's correction pass (the same one the existing
20 symbols' data already went through, per correction_audit.csv) needs
to run on this raw output first. Merging uncorrected data straight into
the real folder would skip that step silently.

CHUNKING: Kite's historical-data range limit is longer for 60-minute
bars than for 15-minute (documented, though not independently
reconfirmed live this session - flagged the same way as the 15-minute
fetch's own chunking comment). Chunked conservatively at 300 days per
call; a real "range too large" error would surface per-chunk, not be
silently absorbed.

USAGE (PowerShell):
    $env:KITE_API_KEY = "..."
    $env:KITE_ACCESS_TOKEN = "..."
    python v34_bridge_fetch_missing31_60minute.py
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

START_DATE = date(2016, 1, 1)
END_DATE = date.today()
CHUNK_DAYS = 300
OUT_DIR = Path(__file__).parent / "historical_data_60minute_new31_raw"

MISSING_31 = [
    "TITAN", "M&M", "ADANIENT", "ADANIPORTS", "HCLTECH", "ULTRACEMCO",
    "BAJAJFINSV", "BAJAJ-AUTO", "JSWSTEEL", "ETERNAL", "BEL", "ONGC",
    "SHRIRAMFIN", "ASIANPAINT", "COALINDIA", "POWERGRID", "HINDALCO",
    "EICHERMOT", "GRASIM", "INDIGO", "WIPRO", "SBILIFE", "JIOFIN",
    "TECHM", "TRENT", "APOLLOHOSP", "CIPLA", "HDFCLIFE", "TATACONSUM",
    "DRREDDY", "MAXHEALTH",
]


def _chunks(start: date, end: date, days: int):
    # BUG FOUND AND FIXED 2026-08-16: Kite's historical_data() treats both
    # from_date and to_date as INCLUSIVE - confirmed live, not assumed,
    # after the first real run of this script produced a full duplicated
    # day of bars at every chunk boundary (e.g. 2016-10-27 appeared twice
    # in ADANIENT's output, once at the end of one chunk and again at the
    # start of the next). Reusing chunk_end as the next chunk's start
    # double-counts that boundary date. Advancing by one extra day here
    # avoids it; the 31 files fetched before this fix were repaired with a
    # one-off deduplication pass instead of re-fetched.
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def main() -> int:
    from v34_bridge_kite_credentials import build_kite_client_from_env

    print("=== Real Zerodha historical-data fetch (60-minute bars, 31 missing symbols) ===")
    print(f"Range: {START_DATE} to {END_DATE}  |  Output (raw, uncorrected): {OUT_DIR}\n")

    try:
        kite = build_kite_client_from_env()
    except RuntimeError as exc:
        print(f"FAIL_CLOSED: {exc}")
        return 1

    print("Looking up instrument tokens (kite.instruments('NSE'))...")
    try:
        instruments = kite.instruments("NSE")
    except Exception as exc:
        print(f"Could not fetch instrument list: {type(exc).__name__}: {exc}")
        return 1
    token_by_symbol = {
        row["tradingsymbol"]: row["instrument_token"]
        for row in instruments if isinstance(row, dict) and "tradingsymbol" in row
    }
    print(f"  {len(token_by_symbol)} NSE instruments known.\n")

    OUT_DIR.mkdir(exist_ok=True)
    results = []

    for i, symbol in enumerate(MISSING_31):
        if i > 0:
            time.sleep(1.0)
        token = token_by_symbol.get(symbol)
        if token is None:
            print(f"  {symbol:<14} SKIPPED - no matching NSE instrument token found")
            results.append((symbol, 0, "no instrument token"))
            continue

        all_rows = []
        chunk_error = None
        for chunk_start, chunk_end in _chunks(START_DATE, END_DATE, CHUNK_DAYS):
            try:
                rows = kite.historical_data(
                    token, chunk_start.isoformat(), chunk_end.isoformat(), "60minute",
                )
            except Exception as exc:
                chunk_error = f"{type(exc).__name__}: {exc}"
                break
            all_rows.extend(rows)
            time.sleep(0.34)

        if chunk_error:
            print(f"  {symbol:<14} FAILED partway ({len(all_rows)} rows before error): {chunk_error}")
            results.append((symbol, len(all_rows), chunk_error))
            continue
        if not all_rows:
            print(f"  {symbol:<14} FAILED - zero rows returned")
            results.append((symbol, 0, "zero rows"))
            continue

        # BUG FOUND AND FIXED 2026-08-16: the "&" in "M&M" must be kept
        # literal, not underscore-replaced - formation_prices() parses the
        # symbol back out of the filename via name.split("_", 2)[1], which
        # silently mis-parsed "M_M" as just "M", making that symbol
        # invisible to the real momentum ranking with no error raised
        # anywhere. Found by directly checking formation_prices()'s output
        # against EXTERNAL_UNIVERSE after the first real merge, not assumed.
        out_path = OUT_DIR / f"NSE_{symbol}_60minute_{START_DATE}_{END_DATE}.csv"
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            fh.write("timestamp,open,high,low,close,volume\n")
            for row in all_rows:
                ts = row["date"]
                ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                fh.write(f"{ts_str},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}\n")
        print(f"  {symbol:<14} OK: {len(all_rows)} rows -> {out_path.name}")
        results.append((symbol, len(all_rows), "OK"))

    ok = [r for r in results if r[1] > 0 and r[2] == "OK"]
    failed = [r for r in results if r not in ok]
    print(f"\n=== Summary: {len(ok)}/{len(results)} succeeded ===")
    if failed:
        print("Not hidden:")
        for symbol, rows, msg in failed:
            print(f"  {symbol}: {rows} rows, {msg}")
    print(f"\nRaw output (needs the correction pass next, not yet merged into production): {OUT_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
