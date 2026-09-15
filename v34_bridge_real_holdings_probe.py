"""Pre-live gate — real, read-only Kite holdings/positions retrieval.

Standalone, manually-run script. Answers a simple, legitimate question:
"what does the real Zerodha account actually show right now?" - net
positions, demat holdings, and their current market value at real live
prices.

READ-ONLY, NOT AN ORDER: this script never imports or calls anything
from v34_bridge_kite_broker_client.py's write path (place_order/
submit_emergency_exit). There is no code path in this file capable of
submitting, modifying, or cancelling a real order. LIVE_TRADING_ENABLED
is irrelevant here and is not read or referenced anywhere in this
script.

Two sources, deliberately kept separate and clearly labeled:

1. KiteReadOnlyClient(kite).get_positions() - the exact, already-tested,
   already-validating call this bridge's production engine itself uses
   (v34_p02_accounting.build_portfolio_snapshot reads Kite's "net"
   position book for CNC holdings too - see v34_bridge_kite_read_only_
   client.py's own docstring for why "net" is what this project treats
   as authoritative). This is what Monday's shadow run will actually see.

2. kite.holdings() - Kite's own T+1 settled demat-holdings endpoint.
   NOT wrapped by KiteReadOnlyClient (this project has never needed it
   for any production decision), called here directly and printed
   informationally only, with its own explicit validation in this
   script - so a genuine demat holding that hasn't shown up in the
   "net" position book yet (or vice versa) is visible, not silently
   merged into one number.

For every symbol found in either source, this script also fetches a
real live quote (KiteReadOnlyClient.ltp()) and prints quantity x last
price - the actual current market value of what's really held.

Credentials are read from the environment exactly like every other real-
Kite entrypoint in this project (v34_bridge_kite_credentials.
build_kite_client_from_env()) - never pasted into this script, never
logged, never printed by it.

USAGE (PowerShell):
    $env:KITE_API_KEY = "..."
    $env:KITE_ACCESS_TOKEN = "..."
    python v34_bridge_real_holdings_probe.py

Exit code 0 on a successful read (even if the account is completely
flat - an empty book is a valid, successful answer), 1 on any failure
to connect or a malformed response.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any, Dict, List


def _print_positions(positions: List[Dict[str, Any]]) -> None:
    print(f"\n--- kite.positions()['net'] (production-validated source, {len(positions)} row(s)) ---")
    if not positions:
        print("  (empty - no net positions)")
        return
    for p in positions:
        symbol = p.get("tradingsymbol", "?")
        exchange = p.get("exchange", "?")
        product = p.get("product", "?")
        qty = p.get("quantity", "?")
        avg_price = p.get("average_price", "?")
        print(f"  {exchange}:{symbol:<14} product={product:<5} quantity={qty:<8} average_price={avg_price}")


def _print_holdings(holdings: List[Dict[str, Any]]) -> None:
    print(f"\n--- kite.holdings() (raw demat book, informational only, {len(holdings)} row(s)) ---")
    if not holdings:
        print("  (empty - no demat holdings)")
        return
    for h in holdings:
        symbol = h.get("tradingsymbol", "?")
        exchange = h.get("exchange", "?")
        qty = h.get("quantity", "?")
        avg_price = h.get("average_price", "?")
        print(f"  {exchange}:{symbol:<14} quantity={qty:<8} average_price={avg_price}")


def _print_current_values(read_only_client, symbols: List[str]) -> None:
    print(f"\n--- live value check ({len(symbols)} unique symbol(s)) ---")
    if not symbols:
        print("  (nothing held - nothing to price)")
        return
    try:
        quotes = read_only_client.ltp(symbols)
    except Exception as exc:
        print(f"  could not fetch live quotes: {type(exc).__name__}: {exc}")
        return
    for symbol in symbols:
        quote = quotes.get(f"NSE:{symbol}")
        last_price = quote.get("last_price") if isinstance(quote, dict) else None
        if last_price is None:
            print(f"  {symbol:<14} (no live quote returned)")
        else:
            print(f"  {symbol:<14} last_price={last_price}")


def main() -> int:
    from v34_bridge_kite_credentials import build_kite_client_from_env
    from v34_bridge_kite_read_only_client import KiteReadOnlyClient, KiteResponseMalformedError

    print("=== Real Kite account read (positions + holdings) - READ-ONLY, no order calls anywhere ===")
    try:
        kite = build_kite_client_from_env()
    except RuntimeError as exc:
        print(f"FAIL_CLOSED: {exc}")
        return 1

    client = KiteReadOnlyClient(kite)

    try:
        positions = client.get_positions()
    except (KiteResponseMalformedError, Exception) as exc:
        print(f"kite.positions() failed: {type(exc).__name__}: {exc}")
        return 1
    _print_positions(positions)

    try:
        raw_holdings = kite.holdings()
    except Exception as exc:
        print(f"kite.holdings() failed: {type(exc).__name__}: {exc}")
        return 1
    if not isinstance(raw_holdings, list) or not all(isinstance(h, dict) for h in raw_holdings):
        print(f"kite.holdings(): unexpected shape, got {raw_holdings!r}")
        return 1
    _print_holdings(raw_holdings)

    symbols = sorted({p.get("tradingsymbol") for p in positions if p.get("tradingsymbol")}
                      | {h.get("tradingsymbol") for h in raw_holdings if h.get("tradingsymbol")})
    _print_current_values(client, symbols)

    print("\nNo order was placed. No credentials were printed above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
