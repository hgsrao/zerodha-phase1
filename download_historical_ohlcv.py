"""Read-only Zerodha historical candle downloader. No execution functionality."""

from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from kiteconnect import KiteConnect


DEFAULT_SYMBOLS = ("TATASTEEL", "INFY", "ZYDUSLIFE", "LAURUSLABS", "POLYCAB")
CSV_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def exchange_request_token(request_token: str) -> KiteConnect:
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    api_secret = os.environ.get("KITE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("KITE_API_KEY and KITE_API_SECRET must be present")
    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token.strip(), api_secret=api_secret)
    kite.set_access_token(session["access_token"])
    return kite


def authenticate(request_token: str | None) -> KiteConnect:
    """Prefer an already-established session over spending a one-time-use
    request token. A Zerodha request token can only be exchanged once, so a
    caller that needs several downloader runs in the same terminal session
    (e.g. equities, then the market index) should log in once, export
    KITE_ACCESS_TOKEN, and skip the exchange on every run after the first.
    """
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    access_token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()
    if api_key and access_token:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        return kite
    if not request_token:
        raise RuntimeError(
            "Provide --request-token (or KITE_REQUEST_TOKEN) for a first-time "
            "login, or set KITE_ACCESS_TOKEN to reuse an existing session."
        )
    return exchange_request_token(request_token)


def resolve_tokens(kite: KiteConnect, symbols: tuple[str, ...]) -> dict[str, int]:
    wanted = set(symbols)
    found = {
        item["tradingsymbol"]: int(item["instrument_token"])
        for item in kite.instruments("NSE")
        if item.get("tradingsymbol") in wanted
    }
    missing = wanted - set(found)
    if missing:
        raise RuntimeError(f"NSE instruments not found: {', '.join(sorted(missing))}")
    return found


def download_candles(
    kite: KiteConnect,
    instrument_token: int,
    start: date,
    end: date,
    interval: str,
    chunk_days: int = 60,
) -> list[dict]:
    rows: list[dict] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        candles = kite.historical_data(
            instrument_token,
            datetime.combine(cursor, datetime.min.time()),
            datetime.combine(chunk_end, datetime.max.time().replace(microsecond=0)),
            interval,
        )
        rows.extend({
            "timestamp": candle["date"].isoformat(),
            "open": candle["open"], "high": candle["high"],
            "low": candle["low"], "close": candle["close"],
            "volume": candle["volume"],
        } for candle in candles)
        cursor = chunk_end + timedelta(days=1)
        time.sleep(0.4)

    by_time = {row["timestamp"]: row for row in rows}
    ordered = [by_time[key] for key in sorted(by_time)]
    if len(ordered) != len(rows):
        print(f"warning: removed {len(rows) - len(ordered)} duplicate candles")
    return ordered


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download read-only NSE historical OHLCV")
    parser.add_argument("--request-token", default=os.environ.get("KITE_REQUEST_TOKEN"))
    parser.add_argument("--start", default=(date.today() - timedelta(days=3 * 365)).isoformat())
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--interval", default="15minute")
    parser.add_argument("--output-dir", default="historical_data")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args()

    symbols = tuple(symbol.upper() for symbol in args.symbols)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start >= end:
        raise ValueError("start date must be earlier than end date")
    kite = authenticate(args.request_token)
    tokens = resolve_tokens(kite, symbols)
    output_dir = Path(args.output_dir)
    for symbol in symbols:
        rows = download_candles(kite, tokens[symbol], start, end, args.interval)
        if not rows:
            raise RuntimeError(f"no candles returned for {symbol}")
        path = output_dir / f"NSE_{symbol}_{args.interval}_{start}_{end}.csv"
        write_csv(path, rows)
        print(f"{symbol}: {len(rows)} candles -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
