"""Local, interactive helper — exchange a fresh Zerodha request token for
today's access token, entirely on this machine.

Standalone, manually-run, NOT part of the automated suite and NOT
imported by anything in the runner/engine. Mirrors the exact
`generate_session()` exchange `read_only_broker_inspection.py` already
uses elsewhere in this project - factored out here as its own single-
purpose tool rather than duplicated inline, since minting today's access
token is a distinct daily chore, not a broker-inspection or a charges-
probe concern.

WHAT THIS SCRIPT DOES: reads KITE_API_KEY/KITE_API_SECRET from the
environment, prompts you to paste a FRESH request token or the full
Kite login redirect URL, exchanges it for an access token via
kite.generate_session(), and prints ONLY that access token to YOUR OWN
terminal - nothing is sent anywhere else, nothing is written to any file
by this script, and nothing here is capable of placing an order (it
never imports the write-capable KiteBrokerClient or any P02 engine
code at all).

WHY A REQUEST TOKEN CANNOT BE USED DIRECTLY AS AN ACCESS TOKEN: Kite's
OAuth-style flow issues a short-lived, single-use request token from the
login redirect; `generate_session()` is the one call that exchanges it
(together with your api_secret) for the actual access token your other
scripts need in KITE_ACCESS_TOKEN. Using the raw request token as if it
were the access token always fails with a TokenException - that failure
does not mean your credentials are wrong, it means this exchange step
was skipped.

USAGE:

    $env:KITE_API_KEY = "..."
    $env:KITE_API_SECRET = "..."
    python v34_bridge_generate_access_token.py
    # paste the fresh request token (or full redirect URL) when prompted
    # copy the printed access token, then:
    $env:KITE_ACCESS_TOKEN = "<paste what was printed>"

A request token is normally good for one exchange only and expires
quickly - if this script fails, the fastest fix is almost always: go
back to the Kite login URL, get a brand new request token, and retry
with that one immediately.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse


def normalize_request_token(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("request token is empty")
    if "request_token=" in text:
        parsed = parse_qs(urlparse(text).query)
        tokens = parsed.get("request_token", [])
        if not tokens or not tokens[0].strip():
            raise ValueError("request_token parameter is missing from the pasted URL")
        return tokens[0].strip()
    return text


def main() -> int:
    api_key = os.getenv("KITE_API_KEY", "")
    api_secret = os.getenv("KITE_API_SECRET", "")
    if not api_key or not api_secret:
        print("[BLOCK] KITE_API_KEY or KITE_API_SECRET is not set in this terminal's environment.")
        return 2

    from kiteconnect import KiteConnect

    raw_token = input("Paste a FRESH Zerodha request token or the full login redirect URL: ")
    try:
        request_token = normalize_request_token(raw_token)
    except ValueError as exc:
        print(f"[BLOCK] {exc}")
        return 2

    kite = KiteConnect(api_key=api_key)
    try:
        session = kite.generate_session(request_token, api_secret=api_secret)
    except Exception as exc:
        # Type name only - never the message, which can echo back
        # request/account details (same discipline as the charges probe).
        print(f"[BLOCK] generate_session() failed: {type(exc).__name__}. "
              "The request token is likely already used or expired - get a fresh one and retry immediately.")
        return 1

    access_token = session.get("access_token") if isinstance(session, dict) else None
    if not isinstance(access_token, str) or not access_token:
        print("[BLOCK] Session exchange did not return a usable access token.")
        return 2

    print("\nAccess token generated. Copy the line below and export it in THIS terminal:\n")
    print(access_token)
    print("\nThis is valid only for today - repeat this whole process tomorrow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
