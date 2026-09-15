"""Download the 11 GICS-style sector baskets from Yahoo Finance.

Same discipline as download_yahoo_finance_dataset.py: free, no
credentials, writes into the same historical_data_yahoo_daily/ folder
(shared, not a separate location) - a symbol already downloaded earlier
(e.g. AAPL, JPM, GOOGL from the international basket) is skipped, not
re-fetched, since the file already exists with the same date range.

USAGE:
    python download_yahoo_sector_universes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from download_yahoo_finance_dataset import START_DATE, END_DATE, download_one

OUT_DIR = Path(__file__).parent / "historical_data_yahoo_daily"

SECTORS = {
    "Information Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "AMD", "INTC"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "BRK-B", "BLK", "AXP", "SCHW", "C"],
    "Healthcare": ["JNJ", "UNH", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BMY"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TJX", "MAR"],
    "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MDLZ", "CL", "KMB", "GIS"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T", "CHTR", "EA"],
    "Industrials": ["HON", "UNP", "RTX", "CAT", "BA", "GE", "LMT", "DE", "UPS", "MMM"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "PEG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DD", "DOW", "NUE", "CTVA"],
    "Real Estate": ["PLD", "AMT", "EQIX", "PSA", "O", "SPG", "WELL", "DLR", "AVB", "EQR"],
}


def already_downloaded(symbol: str) -> bool:
    return any(OUT_DIR.glob(f"{symbol}_daily_*.csv"))


def main() -> int:
    print(f"=== Sector universe download (Yahoo Finance, NOT Zerodha) — {START_DATE} to {END_DATE} ===\n")
    results: list[tuple[str, str, int, str]] = []

    for sector, symbols in SECTORS.items():
        print(f"--- {sector} ({len(symbols)} symbols) ---")
        for symbol in symbols:
            if already_downloaded(symbol):
                print(f"  {symbol:<8} SKIPPED (already downloaded earlier)")
                results.append((sector, symbol, -1, "SKIPPED"))
                continue
            result = download_one(symbol, yahoo_symbol=symbol)
            results.append((sector, symbol, result[1], result[2]))
            print(f"  {symbol:<8} {result[2]}")
        print()

    ok = [r for r in results if r[2] > 0 or r[2] == -1]
    failed = [r for r in results if r[2] == 0]
    print(f"=== Summary: {len(ok)}/{len(results)} available (downloaded or already present) ===")
    if failed:
        print("FAILED:")
        for sector, symbol, _, msg in failed:
            print(f"  [{sector}] {symbol}: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
