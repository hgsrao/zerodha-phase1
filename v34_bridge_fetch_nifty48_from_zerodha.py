"""Best-effort real historical data fetch - Zerodha/Kite, for the 48-stock
Nifty universe tested earlier this session against Yahoo data.

Standalone, manually-run script, same discipline as every other real-
credentialed script in this project (v34_bridge_pretrade_charges_probe.py,
v34_bridge_real_holdings_probe.py): credentials read from the
environment only, never pasted into or handled by any AI assistant,
never logged, never printed.

HONEST FRAMING, explicitly requested this way: this is a "let's see if
it works" attempt, not a commitment to wire anything into production.
If it succeeds, the output sits in a NEW folder
(historical_data_zerodha_nifty48/), completely separate from the real
production historical_data/ folder - nothing here touches or replaces
what the live signal engine actually reads today. Wiring a broader
universe into production is a separate, later, properly-scoped task.

WHAT THIS FETCHES: 15-minute interval bars (matching the existing
production historical_data/ folder's own convention - NSE_<SYMBOL>_
15minute_<start>_<end>.csv, load_candles_csv()-compatible columns),
2023-08-14 to today, for the same 48-symbol best-effort Nifty list used
in this session's Yahoo-sourced research (TATAMOTORS excluded - see
that earlier research for why).

REAL KITE HISTORICAL-DATA CONSTRAINTS, handled but not independently
re-verified live in this pass (flagged honestly, matching this
project's own "verified vs. assumed" discipline elsewhere):
- kite.historical_data() needs a numeric instrument_token, not a bare
  symbol - resolved here via kite.instruments("NSE") (the same real
  endpoint KiteReadOnlyClient.get_tick_size() already uses elsewhere in
  this project).
- Kite's documented 15-minute interval fetch is capped at roughly 100-
  200 days per single call (the exact number varies by source and this
  script has not confirmed it against a live response the way the Gate
  2 probe corrected its own field-name guess) - fetched here in
  conservative 90-day chunks with a short pause between calls, and any
  real "date range too large" style error is surfaced per-chunk, not
  swallowed, so a wrong guess here is visible immediately rather than
  silently truncating data.
- No retries beyond what's shown - a failed chunk is reported and moved
  on from, matching this project's "no silent retry layer" convention.

USAGE (PowerShell, same pattern as before):
    $env:KITE_API_KEY = "..."
    $env:KITE_ACCESS_TOKEN = "..."
    python v34_bridge_fetch_nifty48_from_zerodha.py
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

START_DATE = date(2023, 8, 14)
END_DATE = date.today()
CHUNK_DAYS = 90
OUT_DIR = Path(__file__).parent / "historical_data_zerodha_nifty48"

NIFTY_48 = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "BAJFINANCE",
    "LT", "HINDUNILVR", "INFY", "SUNPHARMA", "TITAN", "MARUTI", "M&M", "ADANIENT",
    "KOTAKBANK", "ADANIPORTS", "AXISBANK", "HCLTECH", "ITC", "ULTRACEMCO", "NTPC",
    "BAJAJFINSV", "BAJAJ-AUTO", "JSWSTEEL", "ETERNAL", "BEL", "ONGC", "SHRIRAMFIN",
    "ASIANPAINT", "COALINDIA", "POWERGRID", "HINDALCO", "TATASTEEL", "EICHERMOT",
    "GRASIM", "INDIGO", "WIPRO", "SBILIFE", "JIOFIN", "TECHM", "TRENT", "APOLLOHOSP",
    "CIPLA", "HDFCLIFE", "TATACONSUM", "DRREDDY", "MAXHEALTH",
]  # TATAMOTORS excluded - unresolved after its 2025 split, see earlier session notes


def _chunks(start: date, end: date, days: int):
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=days), end)
        yield cursor, chunk_end
        cursor = chunk_end


def main() -> int:
    from v34_bridge_kite_credentials import build_kite_client_from_env

    print("=== Real Zerodha historical-data fetch (15-minute bars, Nifty-48 attempt) ===")
    print(f"Range: {START_DATE} to {END_DATE}  |  Output: {OUT_DIR}\n")

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

    for i, symbol in enumerate(NIFTY_48):
        if i > 0:
            time.sleep(1.0)  # be gentle with Kite's historical-data rate limit
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
                    token, chunk_start.isoformat(), chunk_end.isoformat(), "15minute",
                )
            except Exception as exc:
                chunk_error = f"{type(exc).__name__}: {exc}"
                break
            all_rows.extend(rows)
            time.sleep(0.34)  # ~3 req/sec, Kite's general documented API rate limit

        if chunk_error:
            print(f"  {symbol:<14} FAILED partway ({len(all_rows)} rows before error): {chunk_error}")
            results.append((symbol, len(all_rows), chunk_error))
            continue
        if not all_rows:
            print(f"  {symbol:<14} FAILED - zero rows returned")
            results.append((symbol, 0, "zero rows"))
            continue

        # Keep "&" literal in the filename - see v34_bridge_fetch_missing31_
        # 60minute.py's own note on why the underscore-replaced version
        # silently broke symbol parsing downstream for "M&M".
        out_path = OUT_DIR / f"NSE_{symbol}_15minute_{START_DATE}_{END_DATE}.csv"
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
        print("Not hidden - these need a closer look if you want them included later:")
        for symbol, rows, msg in failed:
            print(f"  {symbol}: {rows} rows, {msg}")
    print(f"\nOutput folder (separate from real production data): {OUT_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
