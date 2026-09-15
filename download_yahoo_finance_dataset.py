"""One-off dataset download — Yahoo Finance, NOT Zerodha.

Standalone, manually-run script. Pulls daily OHLCV history for the
Nifty 50 (best-effort ticker list, compiled from public web sources on
2026-08-16 - see the honesty note below) plus a small basket of
international megacaps, via the free `yfinance` library. No API key, no
account, no credentials of any kind - this is a completely separate data
source from every Zerodha-touching script in this project.

Output matches the CSV shape `brain_research_lab.load_candles_csv()`
already expects (`timestamp,open,high,low,close,volume`), written to a
NEW folder (`historical_data_yahoo_daily/`) kept separate from the
existing Zerodha-sourced `historical_data*/` folders - never mixed
together, so it's always obvious which data came from which source.

HONESTY NOTE ON THE TICKER LIST: compiled from two live web pages on
2026-08-16, not from a single authoritative feed. 49 of 50 Nifty 50
names were confirmed; the 50th could not be identified from the sources
checked, so it is simply omitted rather than guessed. One ticker
(TATAMOTORS) is uncertain because Tata Motors underwent a corporate
split into separate commercial/passenger-vehicle listings during 2025 -
this script does not silently assume it worked. Every symbol's outcome
(rows fetched, or a clear failure) is reported explicitly at the end -
nothing is hidden if a symbol didn't resolve.

USAGE:
    python download_yahoo_finance_dataset.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

START_DATE = "2023-08-14"
END_DATE = "2026-08-16"  # yfinance end date is exclusive of "today" in practice; harmless if slightly short
OUT_DIR = Path(__file__).parent / "historical_data_yahoo_daily"

# Best-effort Nifty 50 list (49 of 50 - see module docstring).
NIFTY_50 = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "BAJFINANCE",
    "LT", "HINDUNILVR", "INFY", "SUNPHARMA", "TITAN", "MARUTI", "M&M", "ADANIENT",
    "KOTAKBANK", "ADANIPORTS", "AXISBANK", "HCLTECH", "ITC", "ULTRACEMCO", "NTPC",
    "BAJAJFINSV", "BAJAJ-AUTO", "JSWSTEEL", "ETERNAL", "BEL", "ONGC", "SHRIRAMFIN",
    "ASIANPAINT", "COALINDIA", "POWERGRID", "HINDALCO", "TATASTEEL", "EICHERMOT",
    "GRASIM", "INDIGO", "WIPRO", "SBILIFE", "JIOFIN", "TECHM", "TRENT", "APOLLOHOSP",
    "TATAMOTORS", "CIPLA", "HDFCLIFE", "TATACONSUM", "DRREDDY", "MAXHEALTH",
]

INTERNATIONAL = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "JNJ", "V", "WMT", "BRK-B",
]


def _yahoo_symbol(nse_symbol: str) -> str:
    # Yahoo's NSE tickers mirror NSE's own symbol literally, "&" included
    # (confirmed live: "M-M.NS" 404s, "M&M.NS" works) - only the ".NS"
    # suffix is added.
    return nse_symbol + ".NS"


def download_one(symbol: str, *, yahoo_symbol: str) -> tuple[str, int, str]:
    """Returns (symbol, row_count, status_message). row_count == 0 means failure."""
    import yfinance as yf

    try:
        df = yf.download(yahoo_symbol, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    except Exception as exc:
        return symbol, 0, f"FAILED: {type(exc).__name__}: {exc}"

    if df is None or df.empty:
        return symbol, 0, "FAILED: no data returned (ticker likely wrong/delisted on Yahoo)"

    if isinstance(df.columns, type(df.columns)) and getattr(df.columns, "nlevels", 1) > 1:
        df.columns = df.columns.get_level_values(0)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{symbol}_daily_{START_DATE}_{END_DATE}.csv"
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for ts, row in df.iterrows():
            fh.write(f"{ts.date().isoformat()},{row['Open']:.4f},{row['High']:.4f},"
                      f"{row['Low']:.4f},{row['Close']:.4f},{int(row['Volume'])}\n")

    return symbol, len(df), f"OK: {len(df)} rows -> {out_path.name}"


def main() -> int:
    print(f"=== Yahoo Finance dataset download (NOT Zerodha) — {START_DATE} to {END_DATE} ===")
    print(f"Output directory: {OUT_DIR}\n")

    results: list[tuple[str, int, str]] = []

    print(f"--- Nifty 50 ({len(NIFTY_50)} symbols) ---")
    for symbol in NIFTY_50:
        result = download_one(symbol, yahoo_symbol=_yahoo_symbol(symbol))
        results.append(result)
        print(f"  {symbol:<14} {result[2]}")

    print(f"\n--- International ({len(INTERNATIONAL)} symbols) ---")
    for symbol in INTERNATIONAL:
        result = download_one(symbol, yahoo_symbol=symbol)
        results.append(result)
        print(f"  {symbol:<14} {result[2]}")

    ok = [r for r in results if r[1] > 0]
    failed = [r for r in results if r[1] == 0]

    print(f"\n=== Summary: {len(ok)}/{len(results)} succeeded ===")
    if failed:
        print("FAILED symbols (nothing hidden):")
        for symbol, _, msg in failed:
            print(f"  {symbol}: {msg}")

    total_rows = sum(r[1] for r in ok)
    print(f"\nTotal rows downloaded: {total_rows}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
