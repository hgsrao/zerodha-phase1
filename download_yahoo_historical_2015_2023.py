"""Download 2015-01-01 to 2023-01-01 for the same 109 sector stocks -
a genuinely different, non-overlapping historical period from the
2023-08-14 to 2026-08-16 data already downloaded (that window starts
after this one ends, so there is zero overlap: this is real out-of-
sample history, not a re-slice of what's already been looked at).

Same source (Yahoo Finance, no credentials), same symbols, saved into
the same historical_data_yahoo_daily/ folder with a distinct filename
(the 2015-2023 date range in the name keeps it unambiguous from the
2023-2026 files already there).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

START_DATE = "2015-01-01"
END_DATE = "2023-01-01"
OUT_DIR = Path(__file__).parent / "historical_data_yahoo_daily"

SECTORS = {
    "Information Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "AMD", "INTC"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "BRK-B", "BLK", "AXP", "SCHW", "C"],
    "Healthcare": ["JNJ", "UNH", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BMY"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TJX", "MAR"],
    "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MDLZ", "CL", "KMB", "GIS"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T", "CHTR"],
    "Industrials": ["HON", "UNP", "RTX", "CAT", "BA", "GE", "LMT", "DE", "UPS", "MMM"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "PEG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DD", "DOW", "NUE", "CTVA"],
    "Real Estate": ["PLD", "AMT", "EQIX", "PSA", "O", "SPG", "WELL", "DLR", "AVB", "EQR"],
}
ALL_SYMBOLS = sorted({s for symbols in SECTORS.values() for s in symbols})


def download_one(symbol: str) -> tuple[str, int, str]:
    import yfinance as yf

    try:
        df = yf.download(symbol, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    except Exception as exc:
        return symbol, 0, f"FAILED: {type(exc).__name__}: {exc}"
    if df is None or df.empty:
        return symbol, 0, "FAILED: no data returned (no history back to 2015, or delisted/wrong symbol)"
    if getattr(df.columns, "nlevels", 1) > 1:
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
    print(f"=== Downloading {START_DATE} to {END_DATE} for {len(ALL_SYMBOLS)} sector symbols ===")
    print("(A period that does not overlap the 2023-2026 data already downloaded - genuinely unseen.)\n")
    results = []
    for i, symbol in enumerate(ALL_SYMBOLS):
        if i > 0:
            time.sleep(1.5)  # avoid Yahoo's rate limit - confirmed the first batch attempt got throttled
        result = download_one(symbol)
        results.append(result)
        print(f"  {symbol:<8} {result[2]}")

    ok = [r for r in results if r[1] > 0]
    failed = [r for r in results if r[1] == 0]
    print(f"\n=== Summary: {len(ok)}/{len(results)} succeeded ===")
    if failed:
        print("FAILED (nothing hidden - these symbols likely didn't exist under this name in 2015):")
        for symbol, _, msg in failed:
            print(f"  {symbol}: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
