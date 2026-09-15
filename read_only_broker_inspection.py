"""One-shot Zerodha account inspection with no trading operations.

Allowed broker calls: generate_session, profile, positions, orders.
This program never imports or starts the production engine or runner.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

from broker_exposure_scope import classify_positions


ACTIVE_ORDER_STATUSES = {
    "OPEN",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
    "PUT ORDER REQ RECEIVED",
    "MODIFY PENDING",
    "CANCEL PENDING",
    "AMO REQ RECEIVED",
}


def normalize_request_token(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("request token is empty")
    if "request_token=" in text:
        parsed = parse_qs(urlparse(text).query)
        tokens = parsed.get("request_token", [])
        if not tokens or not tokens[0].strip():
            raise ValueError("request_token parameter is missing")
        return tokens[0].strip()
    return text


def summarize(positions: Any, orders: Any) -> dict[str, Any]:
    if not isinstance(positions, dict) or not isinstance(positions.get("net"), list):
        raise RuntimeError("FAIL_CLOSED: malformed positions response")
    if not isinstance(orders, list):
        raise RuntimeError("FAIL_CLOSED: malformed orders response")

    blocking_positions, ignored_cnc = classify_positions(positions["net"])

    def sanitize(rows):
        return [
            {
                "symbol": position.get("tradingsymbol"),
                "exchange": position.get("exchange"),
                "product": position.get("product"),
                "quantity": int(position.get("quantity", 0) or 0),
                "overnight_quantity": position.get("overnight_quantity"),
                "average_price": position.get("average_price"),
                "pnl": position.get("pnl"),
            }
            for position in rows
        ]

    active_orders = []
    for order in orders:
        if not isinstance(order, dict):
            raise RuntimeError("FAIL_CLOSED: malformed order row")
        status = str(order.get("status", "")).upper()
        if status in ACTIVE_ORDER_STATUSES:
            active_orders.append({
                "order_id": order.get("order_id"),
                "symbol": order.get("tradingsymbol"),
                "transaction_type": order.get("transaction_type"),
                "product": order.get("product"),
                "quantity": order.get("quantity"),
                "filled_quantity": order.get("filled_quantity"),
                "pending_quantity": order.get("pending_quantity"),
                "status": status,
            })

    return {
        "nonzero_net_positions": sanitize(blocking_positions),
        "ignored_cnc_holdings": sanitize(ignored_cnc),
        "active_orders": active_orders,
        "broker_clean": not blocking_positions and not active_orders,
    }


def main() -> int:
    api_key = os.getenv("KITE_API_KEY") or os.getenv("KITE_API_KEY", "")
    api_secret = os.getenv("KITE_API_SECRET") or os.getenv("KITE_API_SECRET", "")
    if not api_key or not api_secret:
        print("[BLOCK] KITE_API_KEY or KITE_API_SECRET is unavailable in this terminal.")
        return 2

    from kiteconnect import KiteConnect

    raw_token = input("Paste fresh Zerodha request token or redirect URL: ")
    request_token = normalize_request_token(raw_token)
    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session.get("access_token") if isinstance(session, dict) else None
    if not isinstance(access_token, str) or not access_token:
        print("[BLOCK] Session exchange did not return a usable access token.")
        return 2
    kite.set_access_token(access_token)

    profile = kite.profile()
    result = summarize(kite.positions(), kite.orders())
    print("=== READ-ONLY BROKER INSPECTION ===")
    print(f"user_id={profile.get('user_id') if isinstance(profile, dict) else 'UNKNOWN'}")
    print(json.dumps(result, indent=2))
    print("No order-placement, modification, cancellation, or state-write operation was invoked.")
    return 0 if result["broker_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
