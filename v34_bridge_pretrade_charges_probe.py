"""Pre-live gate — real Kite virtual-contract-note schema probe.

Standalone, manually-run script. NOT part of `run_forever()`/`_build_
engine()`, NOT wired into the automated test suite (the 981-test offline
regression must never depend on real, authenticated Kite credentials -
that dependency would make the suite non-reproducible and would risk
this project's own standing rule against ever accepting/handling real
credentials in an automated context). This is the one thing this
project's own "verify before trusting" discipline explicitly left
outside the 981 tests: `_parse_charges_total`'s expected response shape
(`v34_bridge_daily_accounting.py`) was built from Kite's documented
`/charges/orders` schema, confirmed against Kite's own documentation
during Phase 3.5A - but never against a captured real payload. This
script closes exactly that gap, nothing more.

CALCULATION-ONLY, NOT AN ORDER: `get_virtual_contract_note` (POST /
charges/orders) computes charges for the order attributes the caller
supplies - it does not place, modify, or cancel anything. This script
runs with real credentials but never imports or calls anything from
`v34_bridge_kite_broker_client.py`'s write path (`place_order`/
`submit_emergency_exit`) - there is no code path in this file capable of
submitting a real order, `LIVE_TRADING_ENABLED` is irrelevant here and
is not read or referenced anywhere in this script.

WHAT THIS SCRIPT CHECKS - structural facts only, exactly as scoped:

    SDK call succeeds
    returned object is list
    list length == 1
    item is a mapping
    item["charges"] is a mapping
    item["charges"]["total"] exists
    total is finite/non-negative numeric

WHAT THIS SCRIPT DELIBERATELY NEVER PRINTS BY DEFAULT: the API key, the
access token, or the full raw response body (which could carry account-
identifying detail well beyond what this structural check needs). Only
the pass/fail label for each fact above, and - on an unexpected
exception - the exception's type name only, never its message (a Kite
error message COULD echo back request/account details, so this stays
off unless explicitly requested).

OPT-IN DEBUG MODE, for exactly the situation where the type name alone
isn't enough to diagnose a real failure: set `PRETRADE_PROBE_SHOW_ERROR_
DETAIL=1` to also print `str(exc)`. This is a deliberate, explicit
escape hatch, not the default - a human decision to trade a small,
usually-generic risk (Kite's own exception messages are typically
generic protocol/validation text, e.g. "Missing required key" or a plan/
permission notice, not account numbers or holdings) for the ability to
actually debug a real integration mismatch. STILL READ THE PRINTED
MESSAGE YOURSELF BEFORE PASTING IT ANYWHERE - this flag does not
guarantee the message is safe to share, it only makes it visible to you,
locally, so you can judge that yourself.

USAGE (credentials read from the environment, exactly like every other
real-Kite entrypoint in this project - v34_bridge_kite_credentials.
build_kite_client_from_env() - never pasted into this script, never
logged by it):

    KITE_API_KEY=... KITE_ACCESS_TOKEN=... python v34_bridge_pretrade_charges_probe.py
    # or, to see the real exception message locally when debugging:
    KITE_API_KEY=... KITE_ACCESS_TOKEN=... PRETRADE_PROBE_SHOW_ERROR_DETAIL=1 python v34_bridge_pretrade_charges_probe.py

Exit code 0 if every check passes, 1 otherwise - suitable for a CI/ops
pre-live gate, not just human reading.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal, InvalidOperation
from typing import Any, List, Tuple

Check = Tuple[str, bool]


def _show_error_detail() -> bool:
    return os.getenv("PRETRADE_PROBE_SHOW_ERROR_DETAIL", "") == "1"


def _run_probe() -> List[Check]:
    from v34_bridge_kite_credentials import build_kite_client_from_env

    # Deliberately calls kite.get_virtual_contract_note(...) directly,
    # NOT through KiteReadOnlyClient (Phase 1A) - that wrapper already
    # fails closed on a non-list/wrong-length response, which would
    # collapse this script's "returned object is a list"/"list length ==
    # 1" checks into one generic "call failed" outcome. This probe wants
    # each of the seven facts observed independently, exactly as scoped
    # - the raw SDK object is what's being characterized here, not the
    # already-validating client built on top of it.
    kite = build_kite_client_from_env()

    # A single, harmless, well-formed CNC BUY - the smallest order shape
    # this calculator plausibly accepts. Never submitted anywhere; only
    # ever passed to the charges calculator.
    #
    # Field is `average_price` (a numeric value) - CONFIRMED, not
    # guessed: a live GeneralException ("average_price value not found")
    # against the real endpoint proved a prior attempt to rename this to
    # `price` (reasoning from the documented response's echoed key name,
    # which turned out to differ from the request's own field name) was
    # wrong. See v34_bridge_daily_accounting.py's _order_params_for_
    # charges() docstring for the full correction history.
    #
    # `order_id` IS required (GATE 2 CORRECTION - the documentation only
    # says it's "accepted," not that it's optional; a live
    # GeneralException("order_id value not found") without it proved
    # otherwise). Production code (_order_params_for_charges) already
    # includes the real order's own order_id; this probe has no real
    # order to reference, so it supplies a harmless placeholder string -
    # never submitted anywhere, only passed to the charges calculator.
    order_params = {
        "order_id": "PROBE-1", "exchange": "NSE", "tradingsymbol": "SBIN", "transaction_type": "BUY",
        "variety": "regular", "product": "CNC", "order_type": "MARKET",
        "quantity": 1, "average_price": 560,
    }

    checks: List[Check] = []
    try:
        response = kite.get_virtual_contract_note([order_params])
    except Exception as exc:
        checks.append(("SDK call succeeded", False))
        if _show_error_detail():
            checks.append((f"(raised {type(exc).__name__}: {exc})", False))
        else:
            checks.append((f"(raised {type(exc).__name__} - message withheld; rerun with "
                            "PRETRADE_PROBE_SHOW_ERROR_DETAIL=1 to see it locally)", False))
        return checks
    checks.append(("SDK call succeeded", True))

    is_list = isinstance(response, list)
    checks.append(("returned object is a list", is_list))
    checks.append(("list length == 1", is_list and len(response) == 1))

    item: Any = response[0] if (is_list and len(response) == 1) else None
    is_mapping = isinstance(item, dict)
    checks.append(("item is a mapping", is_mapping))

    charges = item.get("charges") if is_mapping else None
    charges_is_mapping = isinstance(charges, dict)
    checks.append(("item['charges'] is a mapping", charges_is_mapping))

    has_total = charges_is_mapping and "total" in charges
    checks.append(("charges['total'] key exists", has_total))

    numeric_ok = False
    if has_total:
        try:
            total = Decimal(str(charges["total"]))
            numeric_ok = total.is_finite() and total >= 0
        except (InvalidOperation, ValueError, TypeError):
            numeric_ok = False
    checks.append(("charges['total'] is finite/non-negative numeric", numeric_ok))

    return checks


def main() -> int:
    print("=== Kite virtual-contract-note structural probe (calculation-only, no order placed) ===")
    checks = _run_probe()
    for label, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print("No credentials, access tokens, or full response bodies are ever printed by this script.")
    return 0 if checks and all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
