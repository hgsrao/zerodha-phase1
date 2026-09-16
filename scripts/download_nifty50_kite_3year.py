#!/usr/bin/env python3
"""Download a complete three-year NIFTY 50 5-minute OHLCV dataset from Kite.

Credentials are deliberately read from environment variables or a hidden
terminal prompt. They are never printed, saved, or accepted as command-line
arguments (where they could end up in shell history/process listings).

Run from the repository root:
  python3 -m pip install --user -r requirements_external_engine.txt
  export KITE_API_KEY='...'
  export KITE_API_SECRET='...'
  python3 scripts/download_nifty50_kite_3year.py

The script prints Kite's login URL, prompts for the short-lived request token,
and writes a validated CSV under data/. It makes read-only Kite calls only.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from kiteconnect import KiteConnect

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "NSE_NIFTY50_5minute_3years.csv"
INTERVAL = "5minute"
CHUNK_DAYS = 55  # deliberately below Kite's small-interval historical range limit
# Local convenience fallback from the supplied Kite developer-page PDF. This
# file is intentionally left untracked; never commit app credentials.
DEFAULT_KITE_API_KEY = "f5qmn3ug0i6brql3"


def secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    value = getpass.getpass(f"{name}: ").strip()
    if not value:
        raise SystemExit(f"{name} is required.")
    return value


def fetch_chunk(kite: KiteConnect, token: int, start: datetime, end: datetime) -> list[dict]:
    """Fetch one complete interval; do not silently omit an unavailable chunk."""
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return kite.historical_data(token, start, end, INTERVAL, continuous=False, oi=False)
        except Exception as exc:  # Kite exceptions have several concrete subclasses
            last_error = exc
            if attempt < 3:
                pause = 2 ** attempt
                print(f"  retry {attempt}/2 after {pause}s: {exc}", file=sys.stderr)
                time.sleep(pause)
    raise RuntimeError(f"Kite failed for {start:%F} to {end:%F}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=lambda value: datetime.strptime(value, "%Y-%m-%d"),
                        help="inclusive start date (default: exactly three years ago)")
    parser.add_argument("--end", type=lambda value: datetime.strptime(value, "%Y-%m-%d"),
                        help="inclusive end date (default: today)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    end = args.end or datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    start = args.start or (end - timedelta(days=3 * 365)).replace(hour=0, minute=0, second=0, microsecond=0)
    if start >= end:
        raise SystemExit("--start must precede --end.")

    api_key = os.environ.get("KITE_API_KEY", DEFAULT_KITE_API_KEY).strip()
    kite = KiteConnect(api_key=api_key)
    print("Open this Kite login URL in your browser:\n")
    print(kite.login_url())
    request_token = getpass.getpass("\nPaste the request_token from the redirect URL: ").strip()
    if not request_token:
        raise SystemExit("A request_token is required.")
    # The token and generated access token remain in memory for this process.
    try:
        session = kite.generate_session(request_token, api_secret=secret("KITE_API_SECRET"))
    except Exception as exc:
        raise SystemExit(
            "Kite rejected the login-session exchange. Check that KITE_API_KEY "
            "is the current app's API key (not the secret), KITE_API_SECRET "
            "belongs to that same app, and the request_token came from this "
            "script's freshly printed login URL. Underlying Kite error: " + str(exc)
        ) from exc
    kite.set_access_token(session["access_token"])

    # Do not rely on a copied numeric token: resolve the current instrument
    # using Kite's own authenticated quote response.
    quote = kite.ltp(["NSE:NIFTY 50"])
    nifty = quote.get("NSE:NIFTY 50")
    if not nifty or not nifty.get("instrument_token"):
        raise RuntimeError("Kite did not return an instrument token for NSE:NIFTY 50.")
    instrument_token = int(nifty["instrument_token"])

    print(f"Downloading NIFTY 50 {INTERVAL} candles from {start:%F} to {end:%F} in {CHUNK_DAYS}-day chunks.")
    all_candles: list[dict] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1, hours=23, minutes=59), end)
        print(f"  {chunk_start:%F} to {chunk_end:%F} ...", end="", flush=True)
        candles = fetch_chunk(kite, instrument_token, chunk_start, chunk_end)
        print(f" {len(candles):,} candles")
        if not candles:
            raise RuntimeError(f"Kite returned no candles for {chunk_start:%F} to {chunk_end:%F}; refusing partial output.")
        all_candles.extend(candles)
        chunk_start = (chunk_end + timedelta(seconds=1)).replace(hour=0, minute=0, second=0)
        time.sleep(0.4)

    frame = pd.DataFrame(all_candles)
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Unexpected Kite candle schema; missing {sorted(missing)}")
    frame["timestamp"] = pd.to_datetime(frame["date"], errors="raise")
    frame = frame[["timestamp", "open", "high", "low", "close", "volume"]].drop_duplicates("timestamp").sort_values("timestamp")
    if not frame["timestamp"].is_monotonic_increasing or frame.empty:
        raise RuntimeError("Invalid output sequence after deduplication.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(f"Saved {len(frame):,} validated candles to {args.output.resolve()}")
    print(f"Actual range: {frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]}")


if __name__ == "__main__":
    main()
